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
    window_from = date(2025, 7, 24)
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
            INSERT INTO corporation_registry_observations (
              observation_id, symbol, corp_code, registry_status, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) VALUES (
              'corp-s23-disclosure', '005930', '00126380', 'ACTIVE', 'COMPLETE',
              %s, %s, 'corporation-registry-observation.v1',
              's1.6-sanitized-corp-registry-v1',
              '{"symbol":"005930","corpCode":"00126380"}'::jsonb, %s, %s
            )
            """,
            (observed_at, observed_at, "0" * 64, "f" * 64),
        )
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
        corp_code=None,
        window_from=window_from,
        window_to=window_to,
    )

    assert batch.complete is True
    assert batch.mapping_version == mapping.version
    assert batch.corp_code == "00126380"
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


def test_empty_or_ambiguous_corporation_registry_is_incomplete(
    postgres_cluster: PostgresTestCluster,
) -> None:
    repository = PostgresStoredDisclosureRepository(postgres_cluster["app_dsn"])
    missing = repository.load(
        symbol="000660",
        corp_code=None,
        window_from=date(2025, 7, 24),
        window_to=date(2026, 7, 24),
    )
    assert missing.complete is False
    assert missing.corp_code == ""
    assert missing.events == ()

    observed_at = datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC)
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO corporation_registry_observations (
                  observation_id, symbol, corp_code, registry_status, completeness,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) VALUES (
                  %s, '000660', %s, 'ACTIVE', 'COMPLETE', %s, %s,
                  'corporation-registry-observation.v1', 'ambiguous-fixture-v1',
                  %s::jsonb, %s, %s
                )
                """,
                [
                    (
                        "corp-ambiguous-a",
                        "00164779",
                        observed_at,
                        observed_at,
                        '{"symbol":"000660","corpCode":"00164779"}',
                        "a" * 64,
                        "b" * 64,
                    ),
                    (
                        "corp-ambiguous-b",
                        "00999999",
                        observed_at,
                        observed_at,
                        '{"symbol":"000660","corpCode":"00999999"}',
                        "c" * 64,
                        "d" * 64,
                    ),
                ],
            )

    ambiguous = repository.load(
        symbol="000660",
        corp_code=None,
        window_from=date(2025, 7, 24),
        window_to=date(2026, 7, 24),
    )
    assert ambiguous.complete is False
    assert ambiguous.corp_code == ""
    assert ambiguous.events == ()

    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO corporation_registry_observations (
                  observation_id, symbol, corp_code, registry_status, completeness,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) VALUES (
                  %s, '035420', '00266961', %s, 'COMPLETE', %s, %s,
                  'corporation-registry-observation.v1', 'inactive-fixture-v1',
                  '{"symbol":"035420","corpCode":"00266961"}'::jsonb, %s, %s
                )
                """,
                [
                    (
                        "corp-active-old",
                        "ACTIVE",
                        datetime(2026, 7, 23, 1, 2, 3, tzinfo=UTC),
                        datetime(2026, 7, 23, 1, 2, 3, tzinfo=UTC),
                        "e" * 64,
                        "f" * 64,
                    ),
                    (
                        "corp-inactive-new",
                        "INACTIVE",
                        observed_at,
                        observed_at,
                        "1" * 64,
                        "2" * 64,
                    ),
                ],
            )

    inactive = repository.load(
        symbol="035420",
        corp_code=None,
        window_from=date(2025, 7, 24),
        window_to=date(2026, 7, 24),
    )
    assert inactive.complete is False
    assert inactive.corp_code == ""
    assert inactive.events == ()
