from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.brokerage import kis_mock_certification_cli as certification
from app.data._shared.canonical_json import canonical_json_bytes


def _request(path: Path) -> dict[str, object]:
    value: dict[str, object] = {
        "branch": "feature/p1-full-app-v2",
        "commitSha": "a" * 40,
        "pullRequest": 123,
        "quantity": 1,
        "requiredChecks": sorted(certification._REQUIRED_CHECKS),
        "securityEvidenceDigest": "b" * 64,
        "symbol": "005930",
    }
    path.write_bytes(canonical_json_bytes(value))
    path.chmod(0o600)
    return value


def test_request_requires_all_general_security_and_ci_checks(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    expected = _request(request_path)

    assert certification._read_request(request_path) == expected

    expected["requiredChecks"] = ["Repo hygiene"]
    request_path.write_bytes(canonical_json_bytes(expected))
    with pytest.raises(certification.KISMockCertificationError):
        certification._read_request(request_path)


def test_request_accepts_codex_workflow_branch(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    expected = _request(request_path)
    expected["branch"] = "codex/p1-v3-preopen-e2e-hardening-20260901"
    request_path.write_bytes(canonical_json_bytes(expected))

    assert certification._read_request(request_path) == expected


def test_certification_claims_signed_authority_before_quote_or_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = tmp_path / "request.json"
    approval_path = tmp_path / "approval.json"
    receipt_path = tmp_path / "receipt.json"
    _request(request_path)
    approval_path.write_text("{}", encoding="utf-8")
    approval_path.chmod(0o600)
    order: list[str] = []
    approval = SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=4))
    monkeypatch.setenv(
        "KIS_MOCK_BOUND_ACCOUNT_ID",
        "acct_00000000000000000000000000000000",
    )
    monkeypatch.setattr(
        certification,
        "require_certification_window",
        lambda now: "2026-08-26",
    )
    monkeypatch.setattr(
        certification,
        "load_and_verify_execution_approval",
        lambda *args, **kwargs: order.append("verify") or approval,
    )
    monkeypatch.setattr(
        certification,
        "claim_signed_provider_approval",
        lambda value: order.append("claim"),
    )
    monkeypatch.setattr(
        certification,
        "_read_lower_limit",
        lambda symbol, expiry: order.append("quote") or (50_000, {"marketData": 1, "tokenP": 1}),
    )
    monkeypatch.setattr(
        certification,
        "_runtime_packet",
        lambda *args: object(),
    )
    monkeypatch.setattr(
        certification,
        "_run_brokerage",
        lambda packet, expiry: order.append("brokerage") or {"brokerage": 7, "tokenP": 0},
    )

    receipt = certification.certify(request_path, approval_path, receipt_path)

    assert order == ["verify", "claim", "quote", "brokerage"]
    assert receipt["status"] == "PASS"
    assert receipt["physicalCalls"] == {"brokerage": 7, "quote": 1, "token": 1}
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] == "PASS"


def test_closed_market_rejects_before_request_approval_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        certification,
        "require_certification_window",
        lambda now: (_ for _ in ()).throw(
            certification.CertificationWindowClosed("KIS_MOCK_CERTIFICATION_MARKET_CLOSED"),
        ),
    )
    monkeypatch.setattr(
        certification,
        "_read_request",
        lambda path: touched.append("request"),
    )

    with pytest.raises(
        certification.KISMockCertificationError,
        match="KIS_MOCK_CERTIFICATION_MARKET_CLOSED",
    ):
        certification.certify(
            tmp_path / "request.json",
            tmp_path / "approval.json",
            tmp_path / "receipt.json",
        )

    assert touched == []


def test_two_token_issuances_are_rejected() -> None:
    with pytest.raises(certification.KISMockCertificationError):
        certification._combined_counts(
            {"marketData": 1, "tokenP": 1},
            {"brokerage": 7, "tokenP": 1},
        )


@pytest.mark.parametrize(
    ("price", "tick"),
    [
        (1_999, 1),
        (2_000, 5),
        (5_000, 10),
        (20_000, 50),
        (50_000, 100),
        (200_000, 500),
        (500_000, 1_000),
    ],
)
def test_krx_tick_boundaries(price: int, tick: int) -> None:
    assert certification._krx_tick(price) == tick
