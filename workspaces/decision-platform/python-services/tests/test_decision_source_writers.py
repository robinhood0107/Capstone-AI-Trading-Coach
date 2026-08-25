from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from app.brokerage.kis_mock_portfolio_writer import (
    append_kis_mock_portfolio_fixture,
    load_kis_mock_portfolio_fixture,
)
from app.data.decision.deterministic_observation_writer import (
    append_deterministic_metric_fixture,
    load_deterministic_metric_fixture,
)
from app.data.kis.market_quote_observation_writer import (
    append_market_quote_fixture,
    load_market_quote_fixture,
)
from app.decision_source_cli import attest_source_writer_dsn
from app.offline_fixture_io import read_bounded_fixture
from tests.conftest import PostgresTestCluster
from tests.support.actor_rls_scope import open_actor_rls_scope

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "decision"
QUOTE_FIXTURE = FIXTURE_DIR / "market_quote.v1.json"
PORTFOLIO_FIXTURE = FIXTURE_DIR / "kis_mock_portfolio.v1.json"
DETERMINISTIC_FIXTURE = FIXTURE_DIR / "deterministic_metrics.v1.json"


def test_quote_fixture_appends_once_and_latest_projection_is_exact(
    postgres_cluster: PostgresTestCluster,
) -> None:
    _reset_source_rows(postgres_cluster)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: append_market_quote_fixture(
                    QUOTE_FIXTURE,
                    database_dsn=postgres_cluster["market_writer_dsn"],
                ),
                range(2),
            )
        )
    replay = append_market_quote_fixture(
        QUOTE_FIXTURE,
        database_dsn=postgres_cluster["market_writer_dsn"],
    )

    assert sorted(results) == [0, 2]
    assert replay == 0
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        rows = connection.execute(
            """
            SELECT symbol, price_krw, previous_close_krw, bid_krw, ask_krw, completeness,
                   observed_at, received_at, source_ref, artifact_hash
            FROM latest_market_quote_observations
            ORDER BY symbol
            """
        ).fetchall()
        payloads = connection.execute(
            "SELECT payload_json::text FROM market_quote_observations ORDER BY symbol"
        ).fetchall()

    assert [row[:6] for row in rows] == [
        ("005930", 70000, 69800, 69900, 70000, "COMPLETE"),
        ("132030", 24500, 24400, 24450, 24500, "COMPLETE"),
    ]
    assert all(len(row[8]) == 64 and len(row[9]) == 64 for row in rows)
    _assert_sanitized(payload for (payload,) in payloads)


def test_quote_fixture_accepts_previous_close_without_current_price(tmp_path: Path) -> None:
    payload = json.loads(QUOTE_FIXTURE.read_text(encoding="utf-8"))
    payload["quotes"][0]["priceKrw"] = None
    fallback = tmp_path / "market-quote-previous-close.json"
    fallback.write_text(json.dumps(payload), encoding="utf-8")

    observations = load_market_quote_fixture(fallback)

    assert observations[0].price_krw is None
    assert observations[0].previous_close_krw == 69800


def test_kis_mock_fixture_is_concurrently_idempotent_and_owner_scoped(
    postgres_cluster: PostgresTestCluster,
) -> None:
    _reset_source_rows(postgres_cluster)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: append_kis_mock_portfolio_fixture(
                    PORTFOLIO_FIXTURE,
                    database_dsn=postgres_cluster["portfolio_writer_dsn"],
                ),
                range(2),
            )
        )

    assert sorted(results) == [0, 1]
    with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
        open_actor_rls_scope(
            identity_dsn=postgres_cluster["identity_dsn"],
            connection=connection,
            actor_user_id="usr_demo_user",
            actor_role="USER",
            operation="READ_PORTFOLIO_SOURCE",
            target_kind="OWNER",
            target_id="usr_demo_user",
        )
        row = connection.execute(
            """
            SELECT cash_krw, portfolio_equity_krw, margin_requirement_krw,
                   completeness, position_count, positions_json::text
            FROM latest_portfolio_balance_observations
            WHERE account_scope_hash = %s
            """,
            ("c" * 64,),
        ).fetchone()
    assert row is not None
    assert row[:5] == (500000, 1200000, 140000, "COMPLETE", 2)
    positions = json.loads(row[5])
    assert positions == [
        {
            "symbol": "005930",
            "quantity": 10,
            "marketValueKrw": 700000,
            "isGoldEtfEtn": False,
        },
        {
            "symbol": "132030",
            "quantity": 0,
            "marketValueKrw": 0,
            "isGoldEtfEtn": True,
        },
    ]


def test_deterministic_fixture_appends_complete_zero_and_previous_session_metrics(
    postgres_cluster: PostgresTestCluster,
) -> None:
    _reset_source_rows(postgres_cluster)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: append_deterministic_metric_fixture(
                    DETERMINISTIC_FIXTURE,
                    database_dsn=postgres_cluster["risk_writer_dsn"],
                ),
                range(2),
            )
        )
    replay = append_deterministic_metric_fixture(
        DETERMINISTIC_FIXTURE,
        database_dsn=postgres_cluster["risk_writer_dsn"],
    )

    assert sorted(results) == [0, 2]
    assert replay == 0
    with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
        open_actor_rls_scope(
            identity_dsn=postgres_cluster["identity_dsn"],
            connection=connection,
            actor_user_id="usr_demo_user",
            actor_role="USER",
            operation="READ_RISK_SOURCE",
            target_kind="OWNER",
            target_id="usr_demo_user",
        )
        risk = connection.execute(
            """
            SELECT daily_loss_rate, max_drawdown, annualized_volatility, completeness
            FROM latest_deterministic_risk_observations
            WHERE owner_scope_hash = %s AND portfolio_source = 'KIS_MOCK'
            """,
            ("c" * 64,),
        ).fetchone()
        orders = connection.execute(
            """
            SELECT trading_date::text, order_count, covered_through, completeness
            FROM latest_daily_order_count_observations
            WHERE owner_scope_hash = %s AND portfolio_source = 'KIS_MOCK'
            """,
            ("c" * 64,),
        ).fetchone()

    assert risk == (Decimal("-0.0125"), Decimal("-0.0800"), Decimal("0.2200"), "COMPLETE")
    assert orders is not None
    assert orders[0] == "2026-06-24"
    assert orders[1] == 0
    assert orders[3] == "COMPLETE"


@pytest.mark.parametrize(
    ("fixture", "loader", "unsafe_field"),
    [
        (QUOTE_FIXTURE, load_market_quote_fixture, "providerHeader"),
        (PORTFOLIO_FIXTURE, load_kis_mock_portfolio_fixture, "accountId"),
        (DETERMINISTIC_FIXTURE, load_deterministic_metric_fixture, "rawAccount"),
    ],
)
def test_source_fixtures_reject_unknown_raw_fields(
    tmp_path: Path,
    fixture: Path,
    loader: object,
    unsafe_field: str,
) -> None:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload[unsafe_field] = "forbidden"
    unsafe = tmp_path / fixture.name
    unsafe.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        loader(unsafe)  # type: ignore[operator]


def test_offline_fixture_byte_bound_is_checked_before_reading_payload(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as stream:
        stream.seek(64)
        stream.write(b"}")

    with pytest.raises(ValueError, match="byte bound"):
        read_bounded_fixture(oversized, max_bytes=64, label="test")


def test_offline_fixture_reader_uses_bounded_stream_read() -> None:
    from app import offline_fixture_io

    source = inspect.getsource(offline_fixture_io.read_bounded_fixture)
    assert ".read_bytes(" not in source
    assert "read(max_bytes + 1)" in source


def test_source_fixture_rejects_duplicate_json_member(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate-deterministic.json"
    duplicate.write_text(
        DETERMINISTIC_FIXTURE.read_text(encoding="utf-8").replace(
            '"orderCount": 0,',
            '"orderCount": 0, "orderCount": 1,',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_deterministic_metric_fixture(duplicate)


def test_source_writer_dsn_attestation_requires_exact_role_and_target(
    postgres_cluster: PostgresTestCluster,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_SOURCE_WRITER_OFFLINE_TARGET", "testcontainers")

    attest_source_writer_dsn(
        postgres_cluster["market_writer_dsn"],
        expected_role="decision_market_writer",
        allowed_insert_tables=("market_quote_observations", "instrument_catalog_observations"),
    )
    with pytest.raises(ValueError, match="decision_market_writer"):
        attest_source_writer_dsn(
            postgres_cluster["app_dsn"],
            expected_role="decision_market_writer",
            allowed_insert_tables=("market_quote_observations", "instrument_catalog_observations"),
        )


def test_source_writers_have_no_provider_live_order_or_fallback_dependency() -> None:
    from app.brokerage import kis_mock_portfolio_writer
    from app.data.decision import deterministic_observation_writer
    from app.data.kis import instrument_catalog_writer, market_quote_observation_writer
    from app.data.opendart import corporation_registry_writer

    source = "\n".join(
        inspect.getsource(module)
        for module in (
            market_quote_observation_writer,
            instrument_catalog_writer,
            kis_mock_portfolio_writer,
            deterministic_observation_writer,
            corporation_registry_writer,
        )
    )
    forbidden = (
        "httpx",
        "requests",
        "KisMarketClient",
        "/uapi/domestic-stock/v1/trading",
        "DEFAULT_KOSPI_LARGECAP30",
        "account_number",
        "access_token",
    )
    assert all(marker not in source for marker in forbidden)
    assert "ON CONFLICT (" not in source


def _reset_source_rows(postgres_cluster: PostgresTestCluster) -> None:
    with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
        connection.execute("DELETE FROM portfolio_position_observations")
        connection.execute("DELETE FROM portfolio_balance_observations")
        connection.execute("DELETE FROM market_quote_observations")
        connection.execute("DELETE FROM deterministic_risk_observations")
        connection.execute("DELETE FROM daily_order_count_observations")


def _assert_sanitized(payloads: object) -> None:
    forbidden = ("account", "token", "authorization", "providerheader", "appkey")
    assert all(
        not any(marker in str(payload).lower() for marker in forbidden)
        for payload in payloads  # type: ignore[union-attr]
    )
