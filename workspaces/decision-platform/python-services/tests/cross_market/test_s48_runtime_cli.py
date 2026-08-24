from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.cross_market import s48_runtime_cli
from app.cross_market.core6_probe import Core6ProbeReceipt, core6_endpoint_set_identity_hash
from app.cross_market.s48_runtime import S48RuntimeAppendSummary


def test_materialize_emits_only_fixed_nine_lane_content_free_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        s48_runtime_cli,
        "_now",
        lambda: datetime(2026, 8, 9, 3, 4, 5, tzinfo=UTC),
    )

    assert s48_runtime_cli.main(("materialize",)) == 0

    assert json.loads(capsys.readouterr().out) == {
        "abstainLaneCount": 5,
        "availableLaneCount": 0,
        "blockedLaneCount": 4,
        "code": "S48_RUNTIME_MATERIALIZED",
        "evaluatedAt": "2026-08-09T03:04:05Z",
        "laneCount": 9,
        "providerPhysicalCalls": 0,
        "retryCount": 0,
        "state": "MATERIALIZED",
    }


def test_stage_requires_explicit_offline_target_and_writer_dsn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DECISION_SOURCE_WRITER_OFFLINE_TARGET", raising=False)
    monkeypatch.delenv("DECISION_MARKET_WRITER_DATABASE_DSN", raising=False)

    assert s48_runtime_cli.main(("stage",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "S48_RUNTIME_OFFLINE_TARGET_REQUIRED",
        "state": "FAILED",
    }

    monkeypatch.setenv("DECISION_SOURCE_WRITER_OFFLINE_TARGET", "local")
    assert s48_runtime_cli.main(("stage",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "S48_RUNTIME_WRITER_DATABASE_DSN",
        "state": "FAILED",
    }


def test_stage_uses_function_only_repository_and_emits_no_source_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    class _Repository:
        def __init__(self, *, database_dsn: str) -> None:
            captured["database_dsn"] = database_dsn

        def append_batch(self, batch: object) -> S48RuntimeAppendSummary:
            captured["batch"] = batch
            return S48RuntimeAppendSummary(inserted=9, replayed=0)

    monkeypatch.setenv("DECISION_SOURCE_WRITER_OFFLINE_TARGET", "testcontainers")
    monkeypatch.setenv("DECISION_MARKET_WRITER_DATABASE_DSN", "postgresql://sanitized-role")
    monkeypatch.setattr(s48_runtime_cli, "PostgresS48RuntimeRepository", _Repository)
    monkeypatch.setattr(
        s48_runtime_cli,
        "_now",
        lambda: datetime(2026, 8, 9, 3, 4, 5, tzinfo=UTC),
    )

    assert s48_runtime_cli.main(("stage",)) == 0

    assert captured["database_dsn"] == "postgresql://sanitized-role"
    assert json.loads(capsys.readouterr().out) == {
        "abstainLaneCount": 5,
        "availableLaneCount": 0,
        "blockedLaneCount": 4,
        "code": "S48_RUNTIME_STAGED",
        "evaluatedAt": "2026-08-09T03:04:05Z",
        "inserted": 9,
        "laneCount": 9,
        "providerPhysicalCalls": 0,
        "replayed": 0,
        "retryCount": 0,
        "state": "STAGED",
    }


def test_materialize_reuses_selected_content_free_core6_receipt_without_provider_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_load(cls, *, control_root, relative_path):
        captured["control_root"] = control_root
        captured["relative_path"] = relative_path
        return _kis_receipt()

    monkeypatch.setattr(s48_runtime_cli, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(Core6ProbeReceipt, "load_from_control_root", classmethod(fake_load))
    monkeypatch.setattr(
        s48_runtime_cli,
        "_now",
        lambda: datetime(2026, 8, 9, 3, 4, 5, tzinfo=UTC),
    )

    assert (
        s48_runtime_cli.main(("materialize", "--core6-receipt", "receipt-" + "a" * 64 + ".json"))
        == 0
    )

    assert captured == {
        "control_root": tmp_path / "capstone-rag/secrets/core6-probes",
        "relative_path": "receipt-" + "a" * 64 + ".json",
    }
    assert json.loads(capsys.readouterr().out) == {
        "abstainLaneCount": 4,
        "availableLaneCount": 1,
        "blockedLaneCount": 4,
        "code": "S48_RUNTIME_MATERIALIZED",
        "evaluatedAt": "2026-08-09T03:04:05Z",
        "laneCount": 9,
        "providerPhysicalCalls": 0,
        "retryCount": 0,
        "state": "MATERIALIZED",
    }


def test_materialize_rejects_arbitrary_core6_receipt_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert s48_runtime_cli.main(("materialize", "--core6-receipt", "../receipt.json")) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "S48_RUNTIME_COMMAND_INVALID",
        "state": "FAILED",
    }


def _kis_receipt() -> Core6ProbeReceipt:
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    return Core6ProbeReceipt(
        approval_id_hash="a" * 64,
        approval_packet_sha256="b" * 64,
        completed_at=now,
        endpoint_set_identity_hash=core6_endpoint_set_identity_hash("KIS"),
        logical_call_count=1,
        operation="KIS_CURRENT_PRICE",
        outcome="SUCCESS",
        physical_call_count=1,
        projection_hash="d" * 64,
        provider_family="KIS",
        provider_status_class="HTTP_2XX",
        request_plan_digest="e" * 64,
        source_id="S48_CORE6_KIS",
        started_at=now,
    )
