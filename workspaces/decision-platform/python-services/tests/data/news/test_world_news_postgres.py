from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.data.news.repository import (
    PostgresWorldNewsRepository,
    PostgresWorldNewsRetention,
    WorldNewsCollection,
)
from app.data.news.world_news import normalize_world_news
from tests.conftest import PostgresTestCluster


NOW = datetime(2026, 9, 8, 3, 1, tzinfo=UTC)


def item(
    *,
    url: str = "https://example.com/news/a",
    provider_id: str | None = "provider-a",
    quote: str = "공급망 병목이 완화되고 있다고 발표했다.",
    observed_at: datetime = NOW,
    received_at: datetime = NOW + timedelta(minutes=1),
    available_at: datetime = NOW + timedelta(minutes=2),
    lineage: str | None = None,
):
    return normalize_world_news(
        provider="GDELT_GQG",
        canonical_url=url,
        provider_document_id=provider_id,
        source_id="src_gdelt_world_news",
        title="세계 공급망 동향",
        bounded_quote=quote,
        bounded_passage=None,
        language="ko",
        published_at=None,
        publication_status="MISSING",
        provider_observed_at=observed_at,
        received_at=received_at,
        available_at=available_at,
        republication_of_document_id=lineage,
    )


def test_version_observation_identity_and_available_at(
    postgres_cluster: PostgresTestCluster,
) -> None:
    cluster = postgres_cluster
    writer = PostgresWorldNewsRepository(cluster["market_writer_dsn"])
    reader = PostgresWorldNewsRepository(cluster["app_dsn"])
    first = item()
    assert writer.append(first) == "INSERTED"
    assert writer.append(first) == "NO_OP"

    repeated = item(
        provider_id="provider-b",
        observed_at=NOW + timedelta(hours=1),
        received_at=NOW + timedelta(hours=1, minutes=1),
        available_at=NOW + timedelta(hours=1, minutes=2),
    )
    assert repeated.document_version_id == first.document_version_id
    assert writer.append(repeated) == "OBSERVED"
    found = reader.lookup(query="공급망", as_of=NOW + timedelta(hours=2))
    assert len(found) == 1
    assert found[0].published_at is None and found[0].publication_status == "MISSING"
    assert found[0].first_seen_at == first.first_seen_at
    assert found[0].provider_observed_at == repeated.provider_observed_at
    assert found[0].provider_document_id == "provider-b"

    changed = item(
        quote="공급망 병목이 다시 확대될 수 있다고 발표했다.",
        observed_at=NOW + timedelta(hours=3),
        received_at=NOW + timedelta(hours=3, minutes=1),
        available_at=NOW + timedelta(hours=3, minutes=2),
    )
    assert writer.append(changed) == "INSERTED"
    assert reader.lookup(query="다시 확대", as_of=NOW + timedelta(hours=3, minutes=1)) == ()
    current = reader.lookup(query="다시 확대", as_of=NOW + timedelta(hours=4))
    assert len(current) == 1 and current[0].document_version_id == changed.document_version_id
    with psycopg.connect(cluster["admin_dsn"]) as db:
        assert db.execute(
            "SELECT count(*) FROM world_news_document_versions_v2 WHERE document_id=%s",
            (first.document_id,),
        ).fetchone() == (2,)


def test_same_domain_documents_republication_and_provider_id_conflict_are_preserved(
    postgres_cluster: PostgresTestCluster,
) -> None:
    cluster = postgres_cluster
    writer = PostgresWorldNewsRepository(cluster["market_writer_dsn"])
    reader = PostgresWorldNewsRepository(cluster["app_dsn"])
    lineage_quote = "계보 확인용 공급망 문서가 공개됐다."
    first = item(
        url="https://example.com/lineage/a",
        provider_id="lineage-provider-a",
        quote=lineage_quote,
    )
    second = item(
        url="https://example.com/lineage/b",
        provider_id="lineage-provider-b",
        observed_at=NOW + timedelta(minutes=3),
        received_at=NOW + timedelta(minutes=4),
        available_at=NOW + timedelta(minutes=5),
        lineage=first.document_id,
        quote=lineage_quote,
    )
    assert writer.append(first) == "INSERTED"
    assert writer.append(second) == "INSERTED"
    collision = item(
        url="https://example.com/lineage/c",
        provider_id="lineage-provider-a",
        observed_at=NOW + timedelta(minutes=6),
        received_at=NOW + timedelta(minutes=7),
        available_at=NOW + timedelta(minutes=8),
        quote=lineage_quote,
    )
    assert writer.append(collision) == "IDENTITY_CONFLICT"
    rows = reader.lookup(query="계보 확인용", as_of=NOW + timedelta(minutes=10))
    assert len(rows) == 3
    assert (
        next(
            row for row in rows if row.document_id == second.document_id
        ).republication_of_document_id
        == first.document_id
    )
    assert (
        next(row for row in rows if row.document_id == collision.document_id).identity_status
        == "PROVIDER_ID_CONFLICT"
    )


def test_collection_empty_failure_and_resume_are_distinct(
    postgres_cluster: PostgresTestCluster,
) -> None:
    cluster = postgres_cluster
    repository = PostgresWorldNewsRepository(cluster["market_writer_dsn"])
    empty = WorldNewsCollection(
        collection_id="news_col_" + "1" * 32,
        provider="GDELT_GQG",
        collection_status="COMPLETE",
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        observed_through=NOW,
        item_count=0,
        cursor_sha256="a" * 64,
        error_code=None,
    )
    failed = WorldNewsCollection(
        collection_id="news_col_" + "2" * 32,
        provider="GDELT_GQG",
        collection_status="COLLECTION_FAILED",
        started_at=NOW + timedelta(minutes=1),
        completed_at=NOW + timedelta(minutes=1, seconds=1),
        observed_through=NOW,
        item_count=0,
        cursor_sha256="c" * 64,
        error_code="PROVIDER_UNAVAILABLE",
        previous_collection_id=empty.collection_id,
    )
    resumed = WorldNewsCollection(
        collection_id="news_col_" + "3" * 32,
        provider="GDELT_GQG",
        collection_status="PARTIAL",
        started_at=NOW + timedelta(minutes=2),
        completed_at=None,
        observed_through=NOW + timedelta(minutes=1),
        item_count=2,
        cursor_sha256="b" * 64,
        error_code=None,
        previous_collection_id=failed.collection_id,
    )
    assert repository.was_completed(provider="GDELT_GQG", cursor_sha256="a" * 64) is False
    assert repository.append_collection(empty) == "INSERTED"
    assert repository.was_completed(provider="GDELT_GQG", cursor_sha256="a" * 64) is True
    assert repository.append_collection(empty) == "NO_OP"
    assert repository.append_collection(failed) == "INSERTED"
    assert repository.append_collection(resumed) == "INSERTED"
    assert repository.was_completed(provider="GDELT_GQG", cursor_sha256="c" * 64) is False
    assert repository.was_completed(provider="GDELT_GQG", cursor_sha256="b" * 64) is True
    with psycopg.connect(cluster["admin_dsn"]) as db:
        assert db.execute(
            "SELECT collection_status,item_count,error_code FROM world_news_collection_runs_v2 ORDER BY started_at"
        ).fetchall() == [
            ("COMPLETE", 0, None),
            ("COLLECTION_FAILED", 0, "PROVIDER_UNAVAILABLE"),
            ("PARTIAL", 2, None),
        ]


def test_writer_has_no_direct_table_access_and_rejects_raw_fields(
    postgres_cluster: PostgresTestCluster,
) -> None:
    cluster = postgres_cluster
    with psycopg.connect(cluster["market_writer_dsn"]) as db:
        assert db.execute(
            "SELECT has_table_privilege(current_user,'world_news_document_versions_v2','SELECT')"
        ).fetchone() == (False,)
        payload = item().projection() | {"rawProviderBody": "forbidden"}
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            db.execute(
                "SELECT append_world_news_document_v2(%s::jsonb)",
                (psycopg.types.json.Jsonb(payload),),
            )


def test_retention_is_dry_run_by_default_and_tombstone_prevents_clock_reset(
    postgres_cluster: PostgresTestCluster,
) -> None:
    cluster = postgres_cluster
    writer = PostgresWorldNewsRepository(cluster["market_writer_dsn"])
    retention = PostgresWorldNewsRetention(cluster["worker_dsn"])
    old = item(
        url="https://example.com/news/retention-only",
        provider_id="provider-retention",
        received_at=NOW,
        available_at=NOW + timedelta(minutes=1),
    )
    assert writer.append(old) == "INSERTED"
    clock = NOW + timedelta(days=31)
    dry_run = retention.run(now=clock)
    assert dry_run["scanned"] >= 1
    assert dry_run["eligible"] >= 1
    assert dry_run["pinned"] == 0 and dry_run["deleted"] == 0
    assert retention.run(apply=True, now=clock)["deleted"] >= 1
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        writer.append(
            item(
                url="https://example.com/news/retention-only",
                provider_id="provider-retention",
                observed_at=clock,
                received_at=clock,
                available_at=clock + timedelta(minutes=1),
            )
        )
    with psycopg.connect(cluster["admin_dsn"]) as db:
        assert db.execute(
            "SELECT first_seen_at,reason_code FROM world_news_retention_tombstones_v1 "
            "WHERE document_version_id=%s",
            (old.document_version_id,),
        ).fetchone() == (NOW, "ORDINARY_RETENTION_30D")
