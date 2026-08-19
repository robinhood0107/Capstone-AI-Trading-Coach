"""S5 자율 운영의 결과 분류와 진단 원장 경계."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.data.kis.accounting import KISCallBudgetExceeded
from app.data.kis.http_client import (
    KISDistributionRetryableStatus,
    KISHttpError,
    KISProviderRateLimitError,
    KISRetryableStatus,
    KISTransportError,
)
from app.data.kis.rate_limiter import KISRateLimitUnavailable, KISRateLimitWaitExceeded
from app.lightgbm.diagnostics import (
    DIAGNOSTIC_LEDGER_FILENAME,
    MAX_DIAGNOSTIC_BYTES,
    read_diagnostics,
    record_diagnostic,
)
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.outcomes import (
    BootstrapEvidenceGap,
    CollectionUnit,
    OutcomeClass,
    classify,
    halts_run,
    is_retryable,
)


def _unit() -> CollectionUnit:
    return CollectionUnit(
        provider="KIS",
        operation_id="FHKST03010100",
        query_sha256="a" * 64,
        label="010620",
    )


def test_unclassified_failure_fails_closed() -> None:
    """모르는 실패를 재시도나 제외로 넘기면 승인 호출을 태우거나 데이터를 조용히 축소한다.

    이번 세션에서 분류되지 않은 ValueError가 ECOS 경계를 넘어 승인 호출 2건을 태웠다.
    """

    for error in (
        ValueError("page range"),
        RuntimeError("unexpected"),
        LightGbmContractError("contract"),
        DatasetUnavailable("dataset"),
        KISHttpError(500, "server"),
        KISRateLimitUnavailable("slot"),
    ):
        assert classify(error) is OutcomeClass.CONTRACT_VIOLATION
        assert halts_run(error)
        assert not is_retryable(error)


def test_provider_transient_failures_declare_themselves() -> None:
    """분류 지식은 예외를 정의한 곳이 선언한다. lightgbm이 HTTP client를 알 필요가 없다."""

    for error in (
        KISRetryableStatus(503, "unavailable"),
        KISDistributionRetryableStatus(502, "gateway"),
        KISProviderRateLimitError(429, "rate"),
        KISTransportError("transport"),
        KISRateLimitWaitExceeded("deadline"),
    ):
        assert classify(error) is OutcomeClass.RETRYABLE_TRANSIENT
        assert is_retryable(error)
        assert not halts_run(error)


def test_budget_exhaustion_halts_and_is_not_retried() -> None:
    error = KISCallBudgetExceeded("cap")
    assert classify(error) is OutcomeClass.BUDGET_EXHAUSTED
    assert halts_run(error)
    assert not is_retryable(error)


def test_evidence_gap_carries_the_unit_and_never_halts_the_run() -> None:
    """상장폐지·신규상장·무거래처럼 실제 시장에서 나오는 성질은 그 단위만 제외한다."""

    error = BootstrapEvidenceGap(
        "no provider evidence",
        unit=_unit(),
        measured={"expectedSessions": 1072, "observedSessions": 910},
    )
    assert classify(error) is OutcomeClass.EVIDENCE_GAP
    assert not halts_run(error)
    assert not is_retryable(error)
    assert error.unit.label == "010620"
    assert error.measured["observedSessions"] == 910
    # 기존 fail-closed 경로가 여전히 잡을 수 있어야 한다.
    assert isinstance(error, DatasetUnavailable)


def test_declared_outcome_that_is_not_approved_fails_closed() -> None:
    """오타나 폐기된 분류 문자열이 조용히 재시도로 해석되면 안 된다."""

    class _Bogus(RuntimeError):
        outcome_class = "RETRY_FOREVER"

    assert classify(_Bogus("x")) is OutcomeClass.CONTRACT_VIOLATION


def test_diagnostic_ledger_is_append_only_and_content_free(tmp_path: Path) -> None:
    """실패가 어떤 단위에서 어떤 수치로 걸렸는지 실행 하나로 드러나야 한다."""

    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    record_diagnostic(
        source_root=root,
        phase="COLLECTING_KIS",
        outcome=OutcomeClass.EVIDENCE_GAP,
        unit=_unit(),
        measured={"expectedSessions": 1072, "observedSessions": 910},
    )
    record_diagnostic(
        source_root=root,
        phase="COLLECTING_KIS",
        outcome=OutcomeClass.RETRYABLE_TRANSIENT,
        unit=_unit(),
    )
    events = read_diagnostics(source_root=root)
    assert [event["outcome"] for event in events] == [
        "EVIDENCE_GAP",
        "RETRYABLE_TRANSIENT",
    ]
    assert events[0]["unit"]["label"] == "010620"  # type: ignore[index]
    assert events[0]["measured"]["observedSessions"] == 910  # type: ignore[index]
    # append-only: 첫 줄은 그대로 남는다.
    assert os.stat(root / DIAGNOSTIC_LEDGER_FILENAME).st_mode & 0o777 == 0o600

    # provider payload가 흘러들 수 있는 구조는 걸러진다.
    record_diagnostic(
        source_root=root,
        phase="COLLECTING_KIS",
        outcome=OutcomeClass.EVIDENCE_GAP,
        measured={"rows": [{"secret": "payload"}], "note": "x" * 200},
    )
    third = read_diagnostics(source_root=root)[2]
    assert "rows" not in third["measured"]  # type: ignore[operator]
    assert len(third["measured"]["note"]) == 64  # type: ignore[index]


def test_diagnostic_ledger_cannot_grow_without_bound(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    target = root / DIAGNOSTIC_LEDGER_FILENAME
    target.write_bytes(b"x" * MAX_DIAGNOSTIC_BYTES)
    record_diagnostic(
        source_root=root,
        phase="COLLECTING_KIS",
        outcome=OutcomeClass.EVIDENCE_GAP,
        unit=_unit(),
    )
    assert target.stat().st_size == MAX_DIAGNOSTIC_BYTES


def test_recording_failure_does_not_change_collection_outcome(tmp_path: Path) -> None:
    """진단을 남기지 못하는 것과 데이터가 틀린 것은 다르다."""

    missing = tmp_path / "absent"
    record_diagnostic(
        source_root=missing,
        phase="COLLECTING_KIS",
        outcome=OutcomeClass.EVIDENCE_GAP,
        unit=_unit(),
    )
    assert not missing.exists()


def test_diagnostic_ledger_rejects_a_corrupted_line(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    (root / DIAGNOSTIC_LEDGER_FILENAME).write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_diagnostics(source_root=root)
