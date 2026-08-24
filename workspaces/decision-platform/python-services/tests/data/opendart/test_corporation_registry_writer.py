from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from app.data.opendart.corporation_registry_writer import (
    append_corporation_registry_fixture,
    load_corporation_registry_fixture,
)
from tests.conftest import PostgresTestCluster

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "decision" / "corporation_registry.v1.json"
)


def test_registry_fixture_is_concurrently_idempotent_and_projection_is_exact(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
        connection.execute("DELETE FROM corporation_registry_observations")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: append_corporation_registry_fixture(
                    FIXTURE,
                    database_dsn=postgres_cluster["collector_dsn"],
                ),
                range(2),
            )
        )
    replay = append_corporation_registry_fixture(
        FIXTURE,
        database_dsn=postgres_cluster["collector_dsn"],
    )

    assert sorted(results) == [0, 2]
    assert replay == 0
    with psycopg.connect(postgres_cluster["disclosure_reader_dsn"]) as connection:
        rows = connection.execute(
            """
            SELECT symbol, corp_code, observed_at, received_at,
                   schema_version, source_version, source_ref, artifact_hash
            FROM current_corporation_registry_projection
            ORDER BY symbol
            """
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("005930", "00126380"),
        ("132030", "00999999"),
    ]
    assert all(len(row[6]) == 64 and len(row[7]) == 64 for row in rows)


def test_registry_fixture_rejects_raw_or_duplicate_mapping(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["providerHeader"] = "forbidden"
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_corporation_registry_fixture(unsafe)

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["mappings"].append(dict(payload["mappings"][0]))
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_corporation_registry_fixture(duplicate)


def test_registry_writer_has_no_provider_live_or_fallback_dependency() -> None:
    from app.data.opendart import corporation_registry_writer

    source = inspect.getsource(corporation_registry_writer)
    forbidden = (
        "httpx",
        "requests",
        "OpenDartClient",
        "/api/corpCode.xml",
        "OPENDART_API_KEY",
        "DEFAULT_CORPORATION",
    )
    assert all(marker not in source for marker in forbidden)
