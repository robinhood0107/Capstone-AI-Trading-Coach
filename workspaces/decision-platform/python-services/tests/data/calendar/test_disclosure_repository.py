from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg

from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.disclosure_repository import PostgresStoredDisclosureRepository
from tests.data.calendar.conftest import PostgresTestCluster


def test_app_role_reads_only_sanitized_stored_disclosure_projection(
    postgres_cluster: PostgresTestCluster,
) -> None:
    observed_at = datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC)
    window_from = date(2026, 6, 24)
    window_to = date(2026, 7, 24)
    mapping = load_default_risk_mapping()
    operations = sorted(
        {
            entry.official_endpoint
            for entry in mapping.active_by_code.values()
            if entry.official_endpoint is not None
        }.union({"bnkMngtPcsp"})
    )
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO calendar_observations (
              observation_id, source_id, origin_group, capability,
              effective_from, effective_to, observed_at, ingested_at,
              sanitized_payload, sanitized_payload_hash, adapter_version,
              mapping_version, registry_version
            ) VALUES (
              'obs-s23-disclosure', 'opendart-structured-events', 'opendart',
              'DISCLOSURE_EVENT', %s, %s, %s, %s,
              '{"stored":true}'::jsonb, %s, 's1.6-opendart-v1',
              's1.6-disclosure-state-v1', 's1.6-registry-v1'
            )
            """,
            (
                window_from,
                window_to,
                observed_at,
                observed_at,
                "1" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO calendar_events (
              event_id, event_series_key, revision_no, source_id,
              source_event_key, event_type, symbol, exchange_mic, event_date,
              detail, status, confidence_bps, has_conflict, canonical_hash
            ) VALUES (
              'evt-s23-disclosure', 'series-s23-disclosure', 1,
              'opendart-structured-events', '20260724000001', 'DISCLOSURE',
              '005930', 'XKRX', %s,
              '{"corp_code":"00126380","endpoint_id":"dfOcr","report_nm":"ignored"}'::jsonb,
              'ACTUAL', 9000, false, %s
            )
            """,
            (window_to, "2" * 64),
        )
        connection.execute(
            """
            INSERT INTO calendar_event_sources (
              event_source_id, event_id, observation_id, source_choice,
              resolution_reason, opaque_source_ref
            ) VALUES (
              'source-s23-disclosure', 'evt-s23-disclosure',
              'obs-s23-disclosure', 'CHOSEN', 'STRUCTURED_ENDPOINT_IDENTITY', %s
            )
            """,
            ("3" * 64,),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_collection_cursors (
                  source_id, operation, subject, window_from, window_to,
                  mapping_version, next_page, completed, updated_at
                ) VALUES (
                  'opendart-structured-events', %s, '00126380', %s, %s,
                  's1.6-disclosure-state-v1', 1, true, %s
                )
                """,
                [
                    (operation, window_from, window_to, observed_at)
                    for operation in operations
                ],
            )

    batch = PostgresStoredDisclosureRepository(postgres_cluster["app_dsn"]).load(
        symbol="005930",
        corp_code="00126380",
        window_from=window_from,
        window_to=window_to,
    )

    assert batch.complete is True
    assert batch.mapping_version == mapping.version
    assert batch.observed_at == observed_at
    assert len(batch.events) == 1
    event = batch.events[0]
    assert event.event_code == "OPENDART:dfOcr"
    assert event.receipt_no == "20260724000001"
    assert event.source_refs == ("3" * 64,)
    assert event.attributes == {}


def test_empty_stored_observation_is_incomplete_not_a_fake_zero(
    postgres_cluster: PostgresTestCluster,
) -> None:
    batch = PostgresStoredDisclosureRepository(postgres_cluster["app_dsn"]).load(
        symbol="000660",
        corp_code="00164779",
        window_from=date(2026, 6, 24),
        window_to=date(2026, 7, 24),
    )

    assert batch.complete is False
    assert batch.events == ()
    assert batch.observed_at == datetime.fromtimestamp(0, tz=UTC)
