from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
from io import BytesIO
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.market_data import (
    MarketDataArchiveError,
    ParquetMarketDataOperationalReader,
    ParquetResearchMarketHistoryReader,
    PostgresMarketDataOperationalReader,
    PostgresResearchMarketHistoryReader,
    read_market_data_archive,
)
from app.data.market_data.archive import MarketDataArtifact, archive_digest
from app.data.market_data.repository import stage_seed_archive


def test_operational_and_research_readers_enforce_distinct_bounds(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)

    operational = ParquetMarketDataOperationalReader(root)
    research = ParquetResearchMarketHistoryReader(root)

    assert len(operational.current_symbols()) == 31
    assert operational.current_symbols()[-1] == "132030"
    assert len(operational.read_closes("000001")) == 253
    assert len(research.read_symbol_closes("000001")) == 300
    assert len(research.read_index_closes("KOSPI")) == 300
    with pytest.raises(MarketDataArchiveError, match="outside the current exact-31"):
        operational.read_closes("999999")
    with pytest.raises(MarketDataArchiveError, match="1..253"):
        operational.read_closes("000001", limit=254)
    with pytest.raises(MarketDataArchiveError, match="1..1260"):
        research.read_index_closes("KOSPI", limit=1261)


def test_operational_package_has_no_lightgbm_dependency() -> None:
    package = Path(__file__).parents[3] / "app" / "data" / "market_data"
    for source in package.glob("*.py"):
        assert "app.lightgbm" not in source.read_text(encoding="utf-8"), source


def test_manifest_last_digest_tampering_is_rejected(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)
    target = root / "bars" / "bars-v1.parquet"
    target.chmod(0o600)
    target.write_bytes(target.read_bytes() + b"tamper")
    target.chmod(0o600)

    with pytest.raises(MarketDataArchiveError, match="digest mismatch"):
        ParquetMarketDataOperationalReader(root)


def test_seed_archive_db_adoption_is_atomic_and_replays_as_no_op(
    tmp_path: Path, postgres_cluster: dict[str, str]
) -> None:
    root = _seed_archive(tmp_path)
    expected = read_market_data_archive(root).manifest_sha256

    inserted = stage_seed_archive(
        database_dsn=postgres_cluster["market_writer_dsn"],
        archive_root=root,
        expected_manifest_sha256=expected,
    )
    replayed = stage_seed_archive(
        database_dsn=postgres_cluster["market_writer_dsn"],
        archive_root=root,
        expected_manifest_sha256=expected,
    )

    assert inserted.outcome == "INSERTED"
    assert (inserted.bars, inserted.indices, inserted.macro, inserted.universes) == (
        300,
        600,
        1,
        31,
    )
    assert replayed.outcome == "NO_OP"
    assert replayed.provider_calls == 0
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        counts = connection.execute(
            """
            select
              (select count(*) from market_data_manifests),
              (select count(*) from market_data_bars),
              (select count(*) from market_data_indices),
              (select count(*) from market_data_macro),
              (select count(*) from market_data_universes)
            """
        ).fetchone()
    assert counts == (1, 300, 600, 1, 31)

    corrected_manifest = "a" * 64
    corrected_session = date(2025, 1, 1)
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            insert into market_data_manifests (
                manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                generation, source_manifest_sha256, supersedes_sha256, archive_sha256,
                calendar_revision, calendar_sha256, temporal_quality
            ) values (
                %s, 'SEED', 'market-data-seed.v1', %s, timestamptz '2026-08-20 00:00:00+00',
                2, %s, %s, %s, 'XKRX-4.13.2+KIS_CTCA0903R', %s,
                'RECONSTRUCTED_FIXED_LAG'
            )
            """,
            (
                corrected_manifest,
                date(2026, 8, 3),
                "1" * 64,
                expected,
                "b" * 64,
                "c" * 64,
            ),
        )
        connection.execute(
            """
            insert into market_data_bars (
                manifest_sha256, generation, symbol, session_date, open_price,
                high_price, low_price, close_price, volume, currency,
                temporal_quality, source_receipt_sha256
            ) values (%s, 2, '000001', %s, 100, 110, 90, 102, 1000, 'KRW',
                      'RECONSTRUCTED_FIXED_LAG', %s)
            """,
            (corrected_manifest, corrected_session, "d" * 64),
        )
        corrected = connection.execute(
            """
            select count(*), max(close_price)
            from market_data_research_bars
            where symbol = '000001' and session_date = %s
            """,
            (corrected_session,),
        ).fetchone()
    assert corrected == (1, 102)

    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute("set role decision_market_operational_reader")
        operational = PostgresMarketDataOperationalReader(connection)  # type: ignore[arg-type]
        assert len(operational.current_symbols()) == 31
        assert len(operational.read_closes("000001")) == 253
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute("set role decision_market_research_reader")
        research = PostgresResearchMarketHistoryReader(connection)  # type: ignore[arg-type]
        assert len(research.read_symbol_closes("000001")) == 300
        assert len(research.read_index_closes("KOSPI")) == 300
    with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
        with pytest.raises(MarketDataArchiveError, match="research_reader"):
            PostgresResearchMarketHistoryReader(connection)  # type: ignore[arg-type]


def test_seed_stage_requires_exact_operator_manifest_binding(tmp_path: Path) -> None:
    root = _seed_archive(tmp_path)

    with pytest.raises(MarketDataArchiveError, match="operator binding"):
        stage_seed_archive(
            database_dsn="postgresql://must-not-connect.invalid/db",
            archive_root=root,
            expected_manifest_sha256="f" * 64,
        )


def _seed_archive(tmp_path: Path) -> Path:
    root = tmp_path / "seed"
    root.mkdir(mode=0o700)
    artifacts: list[MarketDataArtifact] = []
    payloads = {
        "BARS": _bars(),
        "INDICES": _indices(),
        "MACRO": _macro(),
        "UNIVERSES": _universes(),
    }
    paths = {
        "BARS": "bars/bars-v1.parquet",
        "INDICES": "indices/indices-v1.parquet",
        "MACRO": "macro/macro-v1.parquet",
        "UNIVERSES": "universes/universes-v1.parquet",
    }
    date_columns = {
        "BARS": "sessionDate",
        "INDICES": "sessionDate",
        "MACRO": "observationDate",
        "UNIVERSES": "effectiveFromSession",
    }
    for kind, table in payloads.items():
        directory = root / kind.lower()
        directory.mkdir(mode=0o700)
        encoded = _parquet(table)
        target = root / paths[kind]
        target.write_bytes(encoded)
        target.chmod(0o600)
        dates = table[date_columns[kind]].to_pylist()
        artifacts.append(
            MarketDataArtifact(
                kind=kind,
                relative_path=paths[kind],
                sha256=hashlib.sha256(encoded).hexdigest(),
                row_count=table.num_rows,
                first_session_date=min(dates),
                last_session_date=max(dates),
                temporal_quality=(
                    "RECONSTRUCTED_FIXED_LAG"
                    if kind in {"BARS", "MACRO"}
                    else "PROVIDER_AS_OF_NO_VINTAGE"
                ),
            )
        )
    artifact_tuple = tuple(sorted(artifacts, key=lambda artifact: artifact.kind))
    manifest = canonical_json_bytes(
        {
            "archiveSha256": archive_digest(artifact_tuple),
            "artifacts": [
                {
                    "firstSessionDate": artifact.first_session_date.isoformat(),
                    "kind": artifact.kind,
                    "lastSessionDate": artifact.last_session_date.isoformat(),
                    "relativePath": artifact.relative_path,
                    "rowCount": artifact.row_count,
                    "sha256": artifact.sha256,
                    "temporalQuality": artifact.temporal_quality,
                }
                for artifact in artifact_tuple
            ],
            "contractId": "market-data-seed.v1",
            "createdAt": "2026-08-19T13:08:04Z",
            "hardlinkUsed": False,
            "historicalProviderIntentCount": 7230,
            "historicalUniverseUnionCount": 270,
            "operationalHistoryMaxSessions": 253,
            "providerCallsDuringAdoption": 0,
            "rawChunkCopied": False,
            "researchHistoryMaxSessions": 1260,
            "sourceChunkCount": 7218,
            "sourceManifestSha256": "1" * 64,
            "sourcePathPersisted": False,
            "sourceSessionCount": 1072,
            "strictPitPerformanceClaimAllowed": False,
            "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        }
    )
    (root / "manifest.json").write_bytes(manifest)
    (root / "manifest.json").chmod(0o600)
    return root


def _bars() -> pa.Table:
    first = date(2025, 1, 1)
    rows = [
        {
            "symbol": "000001",
            "sessionDate": first + timedelta(days=offset),
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 101,
            "volume": 1000,
            "currency": "KRW",
            "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
            "sourceReceiptSha256": "2" * 64,
        }
        for offset in range(300)
    ]
    return pa.Table.from_pylist(rows)


def _indices() -> pa.Table:
    first = date(2025, 1, 1)
    return pa.Table.from_pylist(
        [
            {
                "indexId": index,
                "sessionDate": first + timedelta(days=offset),
                "close": 2500.0,
                "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
                "sourceReceiptSha256": "3" * 64,
            }
            for offset in range(300)
            for index in ("KOSPI", "KOSDAQ")
        ]
    )


def _macro() -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "seriesId": "722Y001/0101000/D",
                "observationDate": date(2026, 1, 1),
                "availableAt": datetime(2026, 1, 2, tzinfo=UTC),
                "value": "2.5",
                "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
                "sourceReceiptSha256": "4" * 64,
            }
        ]
    )


def _universes() -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "membershipMonth": "2026-08",
                "selectionSession": date(2026, 7, 31),
                "effectiveFromSession": date(2026, 8, 3),
                "instrumentId": ("XKRX:ETF:132030" if rank == 31 else f"KR70000{rank:05d}"),
                "symbol": "132030" if rank == 31 else f"{rank:06d}",
                "market": "KOSPI",
                "rank": rank,
                "isFixedMember": rank == 31,
                "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
                "sourceReceiptSha256": "5" * 64,
            }
            for rank in range(1, 32)
        ]
    )


def _parquet(table: pa.Table) -> bytes:
    sink = BytesIO()
    pq.write_table(table, sink)  # type: ignore[no-untyped-call]
    return sink.getvalue()
