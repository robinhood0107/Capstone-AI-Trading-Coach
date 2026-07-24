"""S2.3 sanitized fixture writer의 최소권한 offline CLI 표면."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path

from app.brokerage.kis_mock_portfolio_writer import append_kis_mock_portfolio_fixture
from app.data.kis.instrument_catalog_writer import append_instrument_catalog_fixture
from app.data.kis.market_quote_observation_writer import append_market_quote_fixture
from app.data.opendart.corporation_registry_writer import (
    append_corporation_registry_fixture,
)
from app.financial_engineering.deterministic_observation_writer import (
    append_deterministic_metric_fixture,
)

FixtureWriter = Callable[..., int]


def market_quote_main() -> None:
    """decision_market_writer로 sanitized current quote fixture만 append한다."""
    _run("market_quote", "DECISION_MARKET_WRITER_DATABASE_DSN", append_market_quote_fixture)


def instrument_catalog_main() -> None:
    """decision_market_writer로 approved S1.1 instrument fixture만 append한다."""
    _run(
        "instrument_catalog",
        "DECISION_MARKET_WRITER_DATABASE_DSN",
        append_instrument_catalog_fixture,
    )


def kis_mock_portfolio_main() -> None:
    """decision_portfolio_writer로 sanitized KIS_MOCK fixture만 append한다."""
    _run(
        "kis_mock_portfolio",
        "DECISION_PORTFOLIO_WRITER_DATABASE_DSN",
        append_kis_mock_portfolio_fixture,
    )


def deterministic_metrics_main() -> None:
    """decision_risk_writer로 deterministic risk/order-count fixture만 append한다."""
    _run(
        "deterministic_metrics",
        "DECISION_RISK_WRITER_DATABASE_DSN",
        append_deterministic_metric_fixture,
    )


def corporation_registry_main() -> None:
    """decision_collector로 sanitized corporation registry fixture만 append한다."""
    _run(
        "corporation_registry",
        "DECISION_COLLECTOR_DATABASE_DSN",
        append_corporation_registry_fixture,
    )


def _run(label: str, dsn_environment: str, writer: FixtureWriter) -> None:
    parser = argparse.ArgumentParser(description=f"Append offline {label} fixture")
    parser.add_argument("fixture", type=Path)
    arguments = parser.parse_args()
    database_dsn = os.environ.get(dsn_environment, "").strip()
    if not database_dsn:
        raise ValueError(f"{dsn_environment} is required")
    inserted = writer(arguments.fixture, database_dsn=database_dsn)
    print(
        json.dumps(
            {"inserted": inserted, "source": label},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
