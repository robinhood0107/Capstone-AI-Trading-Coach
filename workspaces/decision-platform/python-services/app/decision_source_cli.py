"""S2.3 sanitized fixture writer의 최소권한 offline CLI 표면."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from app.brokerage.kis_mock_portfolio_writer import append_kis_mock_portfolio_fixture
from app.data.decision.deterministic_observation_writer import (
    append_deterministic_metric_fixture,
)
from app.data.kis.instrument_catalog_writer import append_instrument_catalog_fixture
from app.data.kis.market_quote_observation_writer import append_market_quote_fixture
from app.data.opendart.corporation_registry_writer import (
    append_corporation_registry_fixture,
)

FixtureWriter = Callable[..., int]

_OFFLINE_TARGET_ENV = "DECISION_SOURCE_WRITER_OFFLINE_TARGET"
_ALLOWED_OFFLINE_TARGETS = {"local", "offline", "test", "testcontainers"}
_SOURCE_WRITER_TABLES = (
    "market_quote_observations",
    "instrument_catalog_observations",
    "portfolio_balance_observations",
    "portfolio_position_observations",
    "deterministic_risk_observations",
    "daily_order_count_observations",
    "corporation_registry_observations",
)
_FORBIDDEN_MUTATION_TABLES = (
    "users",
    "principles",
    "principle_versions",
    "decisions",
    "decision_violations",
    "decision_artifacts",
    "decision_traces",
    "audit_logs",
    "event_outbox",
    "decision_idempotency_results",
    "flyway_schema_history",
)


def market_quote_main() -> None:
    """decision_market_writer로 sanitized current quote fixture만 append한다."""
    _run(
        "market_quote",
        "DECISION_MARKET_WRITER_DATABASE_DSN",
        append_market_quote_fixture,
        expected_role="decision_market_writer",
        allowed_insert_tables=("market_quote_observations", "instrument_catalog_observations"),
    )


def instrument_catalog_main() -> None:
    """decision_market_writer로 approved S1.1 instrument fixture만 append한다."""
    _run(
        "instrument_catalog",
        "DECISION_MARKET_WRITER_DATABASE_DSN",
        append_instrument_catalog_fixture,
        expected_role="decision_market_writer",
        allowed_insert_tables=("market_quote_observations", "instrument_catalog_observations"),
    )


def kis_mock_portfolio_main() -> None:
    """decision_portfolio_writer로 sanitized KIS_MOCK fixture만 append한다."""
    _run(
        "kis_mock_portfolio",
        "DECISION_PORTFOLIO_WRITER_DATABASE_DSN",
        append_kis_mock_portfolio_fixture,
        expected_role="decision_portfolio_writer",
        allowed_insert_tables=("portfolio_balance_observations", "portfolio_position_observations"),
    )


def deterministic_metrics_main() -> None:
    """decision_risk_writer로 deterministic risk/order-count fixture만 append한다."""
    _run(
        "deterministic_metrics",
        "DECISION_RISK_WRITER_DATABASE_DSN",
        append_deterministic_metric_fixture,
        expected_role="decision_risk_writer",
        allowed_insert_tables=("deterministic_risk_observations", "daily_order_count_observations"),
    )


def corporation_registry_main() -> None:
    """decision_collector로 sanitized corporation registry fixture만 append한다."""
    _run(
        "corporation_registry",
        "DECISION_COLLECTOR_DATABASE_DSN",
        append_corporation_registry_fixture,
        expected_role="decision_collector",
        allowed_insert_tables=("corporation_registry_observations",),
    )


def _run(
    label: str,
    dsn_environment: str,
    writer: FixtureWriter,
    *,
    expected_role: str,
    allowed_insert_tables: tuple[str, ...],
) -> None:
    parser = argparse.ArgumentParser(description=f"Append offline {label} fixture")
    parser.add_argument("fixture", type=Path)
    arguments = parser.parse_args()
    database_dsn = os.environ.get(dsn_environment, "").strip()
    if not database_dsn:
        raise ValueError(f"{dsn_environment} is required")
    attest_source_writer_dsn(
        database_dsn,
        expected_role=expected_role,
        allowed_insert_tables=allowed_insert_tables,
    )
    inserted = writer(arguments.fixture, database_dsn=database_dsn)
    print(
        json.dumps(
            {"inserted": inserted, "source": label},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def attest_source_writer_dsn(
    database_dsn: str,
    *,
    expected_role: str,
    allowed_insert_tables: tuple[str, ...],
) -> None:
    """offline fixture writer가 production/app DSN이나 broad role로 실행되지 않게 사전 검증한다."""
    target = os.environ.get(_OFFLINE_TARGET_ENV, "").strip().lower()
    if target not in _ALLOWED_OFFLINE_TARGETS:
        raise ValueError(f"{_OFFLINE_TARGET_ENV} must be one of local/offline/test/testcontainers")
    with psycopg.connect(database_dsn, autocommit=True, connect_timeout=1) as connection:
        current_user = str(_required_scalar(connection.execute("select current_user").fetchone()))
        if current_user != expected_role:
            raise ValueError(f"offline source writer DSN must use {expected_role}")
        allowed = set(allowed_insert_tables)
        for table in _SOURCE_WRITER_TABLES:
            has_insert = _has_table_privilege(connection, table, "INSERT")
            if has_insert != (table in allowed):
                raise ValueError("offline source writer has unexpected source INSERT privilege")
            for privilege in ("SELECT", "UPDATE", "DELETE", "TRUNCATE"):
                if _has_table_privilege(connection, table, privilege):
                    raise ValueError(
                        "offline source writer has unexpected source read/write privilege"
                    )
        for table in _FORBIDDEN_MUTATION_TABLES:
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                if _has_table_privilege(connection, table, privilege):
                    raise ValueError("offline source writer can mutate an unrelated table")
        if bool(
            _required_scalar(
                connection.execute(
                    "select has_schema_privilege(current_user, 'public', 'CREATE')"
                ).fetchone()
            )
        ):
            raise ValueError("offline source writer must not create schema objects")


def _has_table_privilege(
    connection: psycopg.Connection[Any],
    table: str,
    privilege: str,
) -> bool:
    return bool(
        _required_scalar(
            connection.execute(
                "select has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
        )
    )


def _required_scalar(row: tuple[Any, ...] | None) -> Any:
    if row is None:
        raise ValueError("offline source writer attestation returned no row")
    return row[0]
