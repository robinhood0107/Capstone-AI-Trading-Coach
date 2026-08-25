from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg
import pytest

from app import disclosure_repository
from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.disclosure_repository import (
    PostgresStoredDisclosureRepository,
    StoredDisclosureIncompleteError,
    StoredDisclosureOversizedError,
)
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
                [(operation, window_from, window_to, observed_at) for operation in operations],
            )

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        batch = repository.load(
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


def test_disclosure_window_includes_exact_day_365_and_excludes_day_366(
    postgres_cluster: PostgresTestCluster,
) -> None:
    observed_at = datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC)
    window_from = date(2025, 7, 24)
    outside_window = date(2025, 7, 23)
    window_to = date(2026, 7, 24)
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO calendar_observations (
              observation_id, source_id, origin_group, capability,
              effective_from, effective_to, observed_at, ingested_at,
              sanitized_payload, sanitized_payload_hash, adapter_version,
              mapping_version, registry_version
            ) VALUES (
              'obs-s23-365-boundary', 'opendart-structured-events', 'opendart',
              'DISCLOSURE_EVENT', %s, %s, %s, %s,
              '{"stored":true}'::jsonb, %s, 's1.6-opendart-v1',
              's1.6-disclosure-state-v1', 's1.6-registry-v1'
            )
            """,
            (outside_window, window_to, observed_at, observed_at, "4" * 64),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_events (
                  event_id, event_series_key, revision_no, source_id,
                  source_event_key, event_type, symbol, exchange_mic, event_date,
                  detail, status, confidence_bps, has_conflict, canonical_hash
                ) VALUES (
                  %s, %s, 1, 'opendart-structured-events',
                  %s, 'DISCLOSURE', '035720', 'XKRX', %s,
                  '{"corp_code":"00258801","endpoint_id":"dfOcr"}'::jsonb,
                  'ACTUAL', 9000, false, %s
                )
                """,
                [
                    (
                        "evt-s23-exact-365",
                        "series-s23-exact-365",
                        "20250724000001",
                        window_from,
                        "5" * 64,
                    ),
                    (
                        "evt-s23-outside-366",
                        "series-s23-outside-366",
                        "20250723000001",
                        outside_window,
                        "6" * 64,
                    ),
                ],
            )
            cursor.executemany(
                """
                INSERT INTO calendar_event_sources (
                  event_source_id, event_id, observation_id, source_choice,
                  resolution_reason, opaque_source_ref
                ) VALUES (
                  %s, %s, 'obs-s23-365-boundary',
                  'CHOSEN', 'STRUCTURED_ENDPOINT_IDENTITY', %s
                )
                """,
                [
                    ("source-s23-exact-365", "evt-s23-exact-365", "7" * 64),
                    ("source-s23-outside-366", "evt-s23-outside-366", "8" * 64),
                ],
            )

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        batch = repository.load(
            symbol="035720",
            corp_code="00258801",
            window_from=window_from,
            window_to=window_to,
        )

    assert [event.occurred_on for event in batch.events] == [window_from]
    assert [event.receipt_no for event in batch.events] == ["20250724000001"]


def test_repository_statement_timeout_cancels_a_locked_projection_query(
    postgres_cluster: PostgresTestCluster,
) -> None:
    blocker = psycopg.connect(postgres_cluster["admin_dsn"])
    try:
        blocker.execute("LOCK TABLE corporation_registry_observations IN ACCESS EXCLUSIVE MODE")
        with (
            PostgresStoredDisclosureRepository(
                postgres_cluster["disclosure_reader_dsn"]
            ) as repository,
            pytest.raises(psycopg.errors.QueryCanceled) as raised,
        ):
            repository.load(
                symbol="005930",
                corp_code=None,
                window_from=date(2025, 7, 24),
                window_to=date(2026, 7, 24),
            )
        assert raised.value.sqlstate == "57014"
    finally:
        blocker.rollback()
        blocker.close()


def test_repository_rejects_broad_application_role_dsn(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with pytest.raises(ValueError, match="decision_disclosure_reader"):
        PostgresStoredDisclosureRepository(postgres_cluster["app_dsn"])


def test_empty_stored_observation_is_incomplete_not_a_fake_zero(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        batch = repository.load(
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
    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
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

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
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

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        inactive = repository.load(
            symbol="035420",
            corp_code=None,
            window_from=date(2025, 7, 24),
            window_to=date(2026, 7, 24),
        )
    assert inactive.complete is False
    assert inactive.corp_code == ""
    assert inactive.events == ()


def test_more_than_one_hundred_distinct_events_fails_before_source_row_truncation(
    postgres_cluster: PostgresTestCluster,
) -> None:
    observed_at = datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC)
    window_from = date(2025, 7, 24)
    window_to = date(2026, 7, 24)
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO corporation_registry_observations (
              observation_id, symbol, corp_code, registry_status, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) VALUES (
              'corp-s23-event-bound', '068270', '00164742', 'ACTIVE', 'COMPLETE',
              %s, %s, 'corporation-registry-observation.v1', 'event-bound-fixture-v1',
              '{"symbol":"068270","corpCode":"00164742"}'::jsonb, %s, %s
            )
            """,
            (observed_at, observed_at, "4" * 64, "5" * 64),
        )
        connection.execute(
            """
            INSERT INTO calendar_observations (
              observation_id, source_id, origin_group, capability,
              effective_from, effective_to, observed_at, ingested_at,
              sanitized_payload, sanitized_payload_hash, adapter_version,
              mapping_version, registry_version
            ) VALUES (
              'obs-s23-event-bound', 'opendart-structured-events', 'opendart',
              'DISCLOSURE_EVENT', %s, %s, %s, %s,
              '{"stored":true}'::jsonb, %s, 's1.6-opendart-v1',
              's1.6-disclosure-state-v1', 's1.6-registry-v1'
            )
            """,
            (window_from, window_to, observed_at, observed_at, "6" * 64),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_events (
                  event_id, event_series_key, revision_no, source_id,
                  source_event_key, event_type, symbol, exchange_mic, event_date,
                  detail, status, confidence_bps, has_conflict, canonical_hash
                ) VALUES (
                  %s, %s, 1, 'opendart-structured-events',
                  %s, 'DISCLOSURE', '068270', 'XKRX', %s,
                  '{"corp_code":"00164742","endpoint_id":"dfOcr"}'::jsonb,
                  'ACTUAL', 9000, false, %s
                )
                """,
                [
                    (
                        f"evt-s23-bound-{index:03d}",
                        f"series-s23-bound-{index:03d}",
                        f"2099{index:010d}",
                        window_to,
                        f"{index + 1000:064x}",
                    )
                    for index in range(101)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO calendar_event_sources (
                  event_source_id, event_id, observation_id, source_choice,
                  resolution_reason, opaque_source_ref
                ) VALUES (
                  %s, %s, 'obs-s23-event-bound',
                  'CHOSEN', 'STRUCTURED_ENDPOINT_IDENTITY', %s
                )
                """,
                [
                    (
                        f"source-s23-bound-{index:03d}",
                        f"evt-s23-bound-{index:03d}",
                        f"{index + 2000:064x}",
                    )
                    for index in range(101)
                ],
            )

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        with pytest.raises(StoredDisclosureOversizedError, match="event bound"):
            repository.load(
                symbol="068270",
                corp_code=None,
                window_from=window_from,
                window_to=window_to,
            )


def test_one_hundred_events_with_two_source_rows_each_are_returned_without_truncation(
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
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_observations (
                  observation_id, source_id, origin_group, capability,
                  effective_from, effective_to, observed_at, ingested_at,
                  sanitized_payload, sanitized_payload_hash, adapter_version,
                  mapping_version, registry_version
                ) VALUES (
                  %s, 'opendart-structured-events', 'opendart',
                  'DISCLOSURE_EVENT', %s, %s, %s, %s,
                  '{"stored":true}'::jsonb, %s, 's1.6-opendart-v1',
                  's1.6-disclosure-state-v1', 's1.6-registry-v1'
                )
                """,
                [
                    (
                        f"obs-s23-two-refs-{ref_index}",
                        window_from,
                        window_to,
                        observed_at,
                        observed_at,
                        f"{7000 + ref_index:064x}",
                    )
                    for ref_index in range(2)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO calendar_events (
                  event_id, event_series_key, revision_no, source_id,
                  source_event_key, event_type, symbol, exchange_mic, event_date,
                  detail, status, confidence_bps, has_conflict, canonical_hash
                ) VALUES (
                  %s, %s, 1, 'opendart-structured-events',
                  %s, 'DISCLOSURE', '051910', 'XKRX', %s,
                  '{"corp_code":"00164788","endpoint_id":"dfOcr"}'::jsonb,
                  'ACTUAL', 9000, false, %s
                )
                """,
                [
                    (
                        f"evt-s23-two-refs-{index:03d}",
                        f"series-s23-two-refs-{index:03d}",
                        f"2098{index:010d}",
                        window_to,
                        f"{index + 3000:064x}",
                    )
                    for index in range(100)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO calendar_event_sources (
                  event_source_id, event_id, observation_id, source_choice,
                  resolution_reason, opaque_source_ref
                ) VALUES (
                  %s, %s, %s,
                  'CHOSEN', 'STRUCTURED_ENDPOINT_IDENTITY', %s
                )
                """,
                [
                    (
                        f"source-s23-two-refs-{event_index:03d}-{ref_index}",
                        f"evt-s23-two-refs-{event_index:03d}",
                        f"obs-s23-two-refs-{ref_index}",
                        f"{4000 + event_index * 2 + ref_index:064x}",
                    )
                    for event_index in range(100)
                    for ref_index in range(2)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO calendar_collection_cursors (
                  source_id, operation, subject, window_from, window_to,
                  mapping_version, next_page, completed, updated_at
                ) VALUES (
                  'opendart-structured-events', %s, '00164788', %s, %s,
                  's1.6-disclosure-state-v1', 1, true, %s
                )
                """,
                [(operation, window_from, window_to, observed_at) for operation in operations],
            )

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        batch = repository.load(
            symbol="051910",
            corp_code="00164788",
            window_from=window_from,
            window_to=window_to,
        )

    assert batch.complete is True
    assert len(batch.events) == 100
    assert sum(len(event.source_refs) for event in batch.events) == 200
    assert all(len(event.source_refs) == 2 for event in batch.events)
    assert batch.events == tuple(
        sorted(
            batch.events,
            key=lambda event: (event.occurred_on, event.event_code, event.receipt_no),
        )
    )


def test_duplicate_source_rows_are_deduplicated_in_stable_order(
    postgres_cluster: PostgresTestCluster,
) -> None:
    observed_at = datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC)
    window_from = date(2025, 7, 24)
    window_to = date(2026, 7, 24)
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_observations (
                  observation_id, source_id, origin_group, capability,
                  effective_from, effective_to, observed_at, ingested_at,
                  sanitized_payload, sanitized_payload_hash, adapter_version,
                  mapping_version, registry_version
                ) VALUES (
                  %s, 'opendart-structured-events', 'opendart',
                  'DISCLOSURE_EVENT', %s, %s, %s, %s,
                  '{"stored":true}'::jsonb, %s, 's1.6-opendart-v1',
                  's1.6-disclosure-state-v1', 's1.6-registry-v1'
                )
                """,
                [
                    (
                        f"obs-s23-duplicate-ref-{index}",
                        window_from,
                        window_to,
                        observed_at,
                        observed_at,
                        f"{8000 + index:064x}",
                    )
                    for index in range(3)
                ],
            )
        connection.execute(
            """
            INSERT INTO calendar_events (
              event_id, event_series_key, revision_no, source_id,
              source_event_key, event_type, symbol, exchange_mic, event_date,
              detail, status, confidence_bps, has_conflict, canonical_hash
            ) VALUES (
              'evt-s23-duplicate-ref', 'series-s23-duplicate-ref', 1,
              'opendart-structured-events', '20970000000001', 'DISCLOSURE',
              '006400', 'XKRX', %s,
              '{"corp_code":"00164797","endpoint_id":"dfOcr"}'::jsonb,
              'ACTUAL', 9000, false, %s
            )
            """,
            (window_to, "9" * 64),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_event_sources (
                  event_source_id, event_id, observation_id, source_choice,
                  resolution_reason, opaque_source_ref
                ) VALUES (
                  %s, 'evt-s23-duplicate-ref', %s,
                  'CHOSEN', 'STRUCTURED_ENDPOINT_IDENTITY', %s
                )
                """,
                [
                    ("source-s23-duplicate-ref-b", "obs-s23-duplicate-ref-0", "b" * 64),
                    ("source-s23-duplicate-ref-a1", "obs-s23-duplicate-ref-1", "a" * 64),
                    ("source-s23-duplicate-ref-a2", "obs-s23-duplicate-ref-2", "a" * 64),
                ],
            )

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        batch = repository.load(
            symbol="006400",
            corp_code="00164797",
            window_from=window_from,
            window_to=window_to,
        )

    assert len(batch.events) == 1
    assert batch.events[0].source_refs == ("a" * 64, "b" * 64)


def test_source_row_overflow_fails_instead_of_returning_a_partial_event(
    postgres_cluster: PostgresTestCluster,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC)
    window_from = date(2025, 7, 24)
    window_to = date(2026, 7, 24)
    monkeypatch.setattr(disclosure_repository, "_MAX_EVENT_SOURCE_ROWS", 2)
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_observations (
                  observation_id, source_id, origin_group, capability,
                  effective_from, effective_to, observed_at, ingested_at,
                  sanitized_payload, sanitized_payload_hash, adapter_version,
                  mapping_version, registry_version
                ) VALUES (
                  %s, 'opendart-structured-events', 'opendart',
                  'DISCLOSURE_EVENT', %s, %s, %s, %s,
                  '{"stored":true}'::jsonb, %s, 's1.6-opendart-v1',
                  's1.6-disclosure-state-v1', 's1.6-registry-v1'
                )
                """,
                [
                    (
                        f"obs-s23-ref-overflow-{index}",
                        window_from,
                        window_to,
                        observed_at,
                        observed_at,
                        f"{9000 + index:064x}",
                    )
                    for index in range(3)
                ],
            )
        connection.execute(
            """
            INSERT INTO calendar_events (
              event_id, event_series_key, revision_no, source_id,
              source_event_key, event_type, symbol, exchange_mic, event_date,
              detail, status, confidence_bps, has_conflict, canonical_hash
            ) VALUES (
              'evt-s23-ref-overflow', 'series-s23-ref-overflow', 1,
              'opendart-structured-events', '20960000000001', 'DISCLOSURE',
              '000270', 'XKRX', %s,
              '{"corp_code":"00164796","endpoint_id":"dfOcr"}'::jsonb,
              'ACTUAL', 9000, false, %s
            )
            """,
            (window_to, "d" * 64),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO calendar_event_sources (
                  event_source_id, event_id, observation_id, source_choice,
                  resolution_reason, opaque_source_ref
                ) VALUES (
                  %s, 'evt-s23-ref-overflow', %s,
                  'CHOSEN', 'STRUCTURED_ENDPOINT_IDENTITY', %s
                )
                """,
                [
                    (
                        f"source-s23-ref-overflow-{index}",
                        f"obs-s23-ref-overflow-{index}",
                        f"{5000 + index:064x}",
                    )
                    for index in range(3)
                ],
            )

    with PostgresStoredDisclosureRepository(
        postgres_cluster["disclosure_reader_dsn"]
    ) as repository:
        with pytest.raises(StoredDisclosureIncompleteError, match="source reference row bound"):
            repository.load(
                symbol="000270",
                corp_code="00164796",
                window_from=window_from,
                window_to=window_to,
            )
