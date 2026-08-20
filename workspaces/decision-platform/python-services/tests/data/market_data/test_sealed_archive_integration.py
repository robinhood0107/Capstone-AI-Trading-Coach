from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from app.data.market_data.repository import stage_seed_archive


_ARCHIVE_ROOT = os.environ.get("S5_7B_ARCHIVE_ROOT")
_ARCHIVE_MANIFEST_SHA256 = "e3f26485c93d5e8bd9cdbd7f9ea7cc46cf3f446cf42e9d65b28f1f5b89bd9a5c"


@pytest.mark.skipif(not _ARCHIVE_ROOT, reason="sealed owner-private S5.7B archive is local-only")
def test_sealed_archive_stages_exact_normalized_counts_provider_free(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    result = stage_seed_archive(
        database_dsn=isolated_postgres_cluster["market_writer_dsn"],
        archive_root=Path(_ARCHIVE_ROOT or ""),
        expected_manifest_sha256=_ARCHIVE_MANIFEST_SHA256,
    )
    replay = stage_seed_archive(
        database_dsn=isolated_postgres_cluster["market_writer_dsn"],
        archive_root=Path(_ARCHIVE_ROOT or ""),
        expected_manifest_sha256=_ARCHIVE_MANIFEST_SHA256,
    )

    assert result.outcome == "INSERTED"
    assert result.provider_calls == 0
    assert replay.outcome == "NO_OP"
    assert replay.provider_calls == 0
    assert (result.bars, result.indices, result.macro, result.universes) == (
        267_788,
        2_144,
        3_042,
        1_581,
    )
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        counts = connection.execute(
            """
            select
              (select count(*) from market_data_manifests),
              (select count(*) from market_data_operational_universe),
              (select max(n) from (
                select count(*) n from market_data_operational_bars group by symbol
              ) bounded),
              (select count(*) from market_data_research_indices where index_id = 'KOSPI')
            """
        ).fetchone()
    assert counts == (1, 31, 253, 1_072)
