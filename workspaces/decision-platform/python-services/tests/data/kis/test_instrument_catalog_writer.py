from __future__ import annotations

import inspect
import json
from pathlib import Path

import psycopg
import pytest

from app.data.kis.instrument_catalog_writer import (
    append_instrument_catalog_fixture,
    load_instrument_catalog_fixture,
)
from tests.data.calendar.conftest import PostgresTestCluster

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "decision"
    / "instrument_catalog.v1.json"
)


def test_sanitized_fixture_appends_once_and_latest_projection_keeps_exact_fields(
    postgres_cluster: PostgresTestCluster,
) -> None:
    first = append_instrument_catalog_fixture(
        FIXTURE,
        database_dsn=postgres_cluster["market_writer_dsn"],
    )
    replay = append_instrument_catalog_fixture(
        FIXTURE,
        database_dsn=postgres_cluster["market_writer_dsn"],
    )

    assert first == 2
    assert replay == 0
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        rows = connection.execute(
            """
            SELECT symbol, is_etf_etn, is_gold_etf_etn, product_risk_score,
                   catalog_version, observed_at, received_at, source_ref, artifact_hash
            FROM latest_instrument_catalog_observations
            ORDER BY symbol
            """
        ).fetchall()
        stored_payloads = connection.execute(
            """
            SELECT payload_json::text
            FROM instrument_catalog_observations
            ORDER BY symbol
            """
        ).fetchall()

    assert len(rows) == 2
    assert rows[0][0:5] == (
        "005930",
        False,
        False,
        None,
        "s1.1-sanitized-catalog-20260724",
    )
    assert rows[1][0:4] == ("132030", True, True, rows[1][3])
    assert str(rows[1][3]) == "0.3500"
    assert all(len(row[7]) == 64 and len(row[8]) == 64 for row in rows)
    forbidden = ("account", "token", "authorization", "providerheader", "appkey")
    assert all(
        not any(marker in payload.lower() for marker in forbidden)
        for (payload,) in stored_payloads
    )


def test_fixture_rejects_unknown_raw_fields_and_gold_misclassification(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["instruments"][0]["rawProviderPayload"] = {"secret": "forbidden"}
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_instrument_catalog_fixture(unsafe)

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["instruments"][1]["isEtfEtn"] = False
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_instrument_catalog_fixture(invalid)


def test_writer_has_no_provider_live_order_or_fallback_dependency() -> None:
    from app.data.kis import instrument_catalog_writer

    source = inspect.getsource(instrument_catalog_writer)
    assert "httpx" not in source
    assert "requests" not in source
    assert "KisMarketClient" not in source
    assert "/uapi/domestic-stock/v1/trading" not in source
    assert "DEFAULT_KOSPI_LARGECAP30" not in source
