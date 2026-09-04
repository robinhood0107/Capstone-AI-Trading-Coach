"""Publish balance and risk observations immediately before risk evaluation."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.brokerage.kis_mock_portfolio_writer import append_kis_mock_portfolio_fixture
from app.data.kis.market_quote_observation_writer import append_market_quote_fixture
from app.decision_source_cli import attest_source_writer_dsn
from app.data.decision.deterministic_observation_writer import (
    append_deterministic_metric_fixture,
)
from app.data.decision.observation_payloads import (
    GOLD_ETF_SYMBOLS,
    deterministic_metrics_payload,
    market_quote_payload,
    owner_scope_hash,
    portfolio_balance_payload,
)

_SOURCE_VERSION: Final = "p1-runtime-observation-v1"
# The automation projection reads only this balance source version.
_BALANCE_SOURCE_VERSION: Final = "kis-mock-online-complete-v2"
_PORTFOLIO_SOURCE: Final = "KIS_MOCK"
_PORTFOLIO_DSN_KEY: Final = "DECISION_PORTFOLIO_WRITER_DATABASE_DSN"
_RISK_DSN_KEY: Final = "DECISION_RISK_WRITER_DATABASE_DSN"
_MARKET_DSN_KEY: Final = "DECISION_MARKET_WRITER_DATABASE_DSN"
# Match the daily collector's quote lineage.
_QUOTE_SOURCE_VERSION: Final = "p1-daily-quote-observation-v1"


def _write(
    payload: dict[str, Any],
    writer: Any,
    dsn: str,
    *,
    expected_role: str,
    allowed_insert_tables: tuple[str, ...],
) -> int:
    """Write only through an attested least-privilege database role."""

    attest_source_writer_dsn(
        dsn, expected_role=expected_role, allowed_insert_tables=allowed_insert_tables
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        path = Path(handle.name)
    try:
        return int(writer(path, database_dsn=dsn))
    finally:
        path.unlink(missing_ok=True)


def publish_runtime_observations(
    *,
    owner_user_id: str,
    account_id: str,
    balance: Mapping[str, Any],
    baseline_equity_krw: int,
    trading_date: str,
    quotes: Mapping[str, int] | None = None,
) -> str:
    """Publish pre-order observations and return a bounded outcome marker."""

    portfolio_dsn = os.environ.get(_PORTFOLIO_DSN_KEY, "").strip()
    risk_dsn = os.environ.get(_RISK_DSN_KEY, "").strip()
    if not portfolio_dsn or not risk_dsn:
        return "SKIPPED_DSN_MISSING"

    raw_positions = balance.get("positions")
    positions = (
        [item for item in raw_positions if isinstance(item, dict)]
        if isinstance(raw_positions, list)
        else []
    )
    now = datetime.now(UTC)
    try:
        scope_hash = owner_scope_hash(account_id)
        cash_krw = int(balance["cashKrw"])
        portfolio = portfolio_balance_payload(
            owner_user_id=owner_user_id,
            scope_hash=scope_hash,
            cash_krw=cash_krw,
            positions=positions,
            now=now,
            source_version=_BALANCE_SOURCE_VERSION,
            gold_etf_symbols=GOLD_ETF_SYMBOLS,
        )
        metrics = deterministic_metrics_payload(
            owner_user_id=owner_user_id,
            scope_hash=scope_hash,
            portfolio_source=_PORTFOLIO_SOURCE,
            equity_krw=int(portfolio["portfolioEquityKrw"]),
            baseline_equity_krw=baseline_equity_krw,
            daily_order_count=0,
            trading_date=trading_date,
            now=now,
            source_version=_SOURCE_VERSION,
        )
        # Publish the current quote in the same tick as the risk check.
        market_dsn = os.environ.get(_MARKET_DSN_KEY, "").strip()
        if quotes and market_dsn:
            _write(
                market_quote_payload(dict(quotes), now=now, source_version=_QUOTE_SOURCE_VERSION),
                append_market_quote_fixture,
                market_dsn,
                expected_role="decision_market_writer",
                allowed_insert_tables=(
                    "market_quote_observations",
                    "instrument_catalog_observations",
                ),
            )
        _write(
            portfolio,
            append_kis_mock_portfolio_fixture,
            portfolio_dsn,
            expected_role="decision_portfolio_writer",
            allowed_insert_tables=(
                "portfolio_balance_observations",
                "portfolio_position_observations",
            ),
        )
        _write(
            metrics,
            append_deterministic_metric_fixture,
            risk_dsn,
            expected_role="decision_risk_writer",
            allowed_insert_tables=(
                "deterministic_risk_observations",
                "daily_order_count_observations",
            ),
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        return f"FAILED_{type(error).__name__}"
    except Exception as error:  # noqa: BLE001 - psycopg 오류를 여기서 삼킨다
        return f"FAILED_{type(error).__name__}"
    return "PUBLISHED"
