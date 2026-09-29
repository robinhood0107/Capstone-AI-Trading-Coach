"""관측 페이로드가 적재 스키마를 만족하는지 고정한다."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.brokerage.kis_mock_portfolio_writer import load_kis_mock_portfolio_fixture
from app.data.decision.observation_payloads import (
    GOLD_ETF_SYMBOLS,
    owner_scope_hash,
    portfolio_balance_payload,
)

_NOW = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)
_ACCOUNT = "acct_" + "a" * 32
_OWNER = "usr_demo_user"


def _payload(positions: list[dict[str, object]]) -> dict[str, object]:
    return portfolio_balance_payload(
        owner_user_id=_OWNER,
        scope_hash=owner_scope_hash(_ACCOUNT),
        cash_krw=1_000_000,
        positions=positions,
        now=_NOW,
        source_version="p1-runtime-observation-v1",
        gold_etf_symbols=GOLD_ETF_SYMBOLS,
    )


def test_every_position_carries_the_gold_flag_the_writer_requires(tmp_path) -> None:
    """브로커 응답에는 없는 `isGoldEtfEtn` 을 카탈로그로 채운다.

    적재기(`kis_mock_portfolio_writer._position`)가 이 필드를 요구한다. 그대로 흘려보내면
    보유가 하나라도 생기는 순간 ValueError 로 닫히고, 계좌가 비어 있는 동안에는 드러나지
    않는다. 보유가 생긴 fixture에서 이 경계를 검증한다.
    """

    payload = _payload(
        [
            {"marketValueKrw": 200_000, "quantity": 2, "symbol": "000660"},
            {"marketValueKrw": 24_800, "quantity": 1, "symbol": "132030"},
        ]
    )
    flags = {item["symbol"]: item["isGoldEtfEtn"] for item in payload["positions"]}
    assert flags == {"000660": False, "132030": True}

    # 적재기가 실제로 받아들이는지까지 본다. 필드 이름만 맞추고 끝내지 않는다.
    import json

    fixture = tmp_path / "balance.json"
    fixture.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    observation = load_kis_mock_portfolio_fixture(fixture)
    assert {row.symbol for row in observation.positions} == {"000660", "132030"}


def test_equity_is_cash_plus_broker_market_value() -> None:
    """평가액을 다시 곱하지 않고 브로커가 준 값을 그대로 더한다."""

    payload = _payload([{"marketValueKrw": 200_000, "quantity": 2, "symbol": "000660"}])
    assert payload["portfolioEquityKrw"] == 1_000_000 + 200_000


def test_negative_cash_is_rejected() -> None:
    with pytest.raises(ValueError):
        portfolio_balance_payload(
            owner_user_id=_OWNER,
            scope_hash=owner_scope_hash(_ACCOUNT),
            cash_krw=-1,
            positions=[],
            now=_NOW,
            source_version="p1-runtime-observation-v1",
        )


def test_manual_gold_holding_stays_in_account_snapshot_but_is_excluded_from_risk_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.p1_owner import runtime_observation_publisher as publisher

    captured: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setenv("DECISION_PORTFOLIO_WRITER_DATABASE_DSN", "postgresql://portfolio-writer")
    monkeypatch.setenv("DECISION_RISK_WRITER_DATABASE_DSN", "postgresql://risk-writer")
    monkeypatch.delenv("DECISION_MARKET_WRITER_DATABASE_DSN", raising=False)
    monkeypatch.setattr(
        publisher,
        "_write",
        lambda payload, _writer, _dsn, *, expected_role, allowed_insert_tables: (
            captured.append((expected_role, payload)) or 1
        ),
    )

    outcome = publisher.publish_runtime_observations(
        owner_user_id=_OWNER,
        account_id=_ACCOUNT,
        balance={
            "cashKrw": 200_000,
            "positions": [
                {"marketValueKrw": 100_000, "quantity": 1, "symbol": "005930"},
                {"marketValueKrw": 800_000, "quantity": 1, "symbol": "132030"},
            ],
        },
        baseline_equity_krw=500_000,
        trading_date="2026-09-29",
        risk_excluded_symbols=("132030",),
    )

    assert outcome == "PUBLISHED"
    portfolio = next(payload for role, payload in captured if role == "decision_portfolio_writer")
    metric_batch = next(payload for role, payload in captured if role == "decision_risk_writer")
    metric_fixtures = metric_batch["fixtures"]
    assert isinstance(metric_fixtures, list)
    full_risk = next(
        fixture for fixture in metric_fixtures if fixture["sourceVersion"] == "p1-runtime-observation-v1"
    )
    automation_risk = next(
        fixture for fixture in metric_fixtures if fixture["sourceVersion"] == "p1-automation-risk-v1"
    )
    assert len([role for role, _payload in captured if role == "decision_risk_writer"]) == 1
    assert portfolio["portfolioEquityKrw"] == 1_100_000
    assert {item["symbol"] for item in portfolio["positions"]} == {"005930", "132030"}  # type: ignore[index]
    assert full_risk["risk"]["dailyLossRate"] == "0.0000"  # type: ignore[index]
    assert automation_risk["risk"]["dailyLossRate"] == "-0.4000"  # type: ignore[index]
