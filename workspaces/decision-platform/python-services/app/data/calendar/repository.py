from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.data.calendar.disclosure_state import DisclosureStateTransition
from app.data.calendar.errors import QuotaReservationDenied
from app.data.calendar.models import (
    CalendarConflictRecord,
    CalendarEventSource,
    CalendarEventWrite,
    CalendarObservation,
    CalendarPageCommit,
    CanonicalTradingSession,
    CollectionCursor,
    CursorKey,
    PersistenceMode,
    QuotaUsage,
    RetentionRule,
    SourceHealthSnapshot,
)
from app.data.calendar.normalizer import EventRevision
from app.data.calendar.privacy import assert_sanitized_payload
from app.data.calendar.settings import OpenDARTQuotaConfig

_COLLECTOR_ADVISORY_LOCK_ID = 7_316_202_607_220_001


class OpenDARTQuotaRepository:
    """PostgreSQL 한 row에서 OpenDART KST 일자별 physical attempt를 원자 예약한다."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def reserve(
        self,
        usage_date: date,
        config: OpenDARTQuotaConfig,
        grant_token: str,
    ) -> QuotaUsage:
        """same-day config를 LEAST로 낮춘 뒤 남은 slot 하나를 단일 SQL로 non-refundable 예약한다."""
        if not grant_token or len(grant_token) > 128:
            raise ValueError("grant token length is invalid")
        with self._connection.transaction():
            row = self._connection.execute(
                """
                INSERT INTO opendart_quota_usage (
                  usage_date, effective_limit, daily_budget, physical_attempts,
                  last_grant_token, created_at, updated_at
                ) VALUES (%s, %s, %s, 1, %s, now(), now())
                ON CONFLICT (usage_date) DO UPDATE SET
                  effective_limit = LEAST(
                    opendart_quota_usage.effective_limit,
                    EXCLUDED.effective_limit
                  ),
                  daily_budget = LEAST(
                    opendart_quota_usage.daily_budget,
                    EXCLUDED.daily_budget
                  ),
                  physical_attempts = CASE
                    WHEN opendart_quota_usage.exhausted_at IS NULL
                     AND opendart_quota_usage.physical_attempts < LEAST(
                       opendart_quota_usage.daily_budget,
                       EXCLUDED.daily_budget
                     )
                    THEN opendart_quota_usage.physical_attempts + 1
                    ELSE opendart_quota_usage.physical_attempts
                  END,
                  last_grant_token = CASE
                    WHEN opendart_quota_usage.exhausted_at IS NULL
                     AND opendart_quota_usage.physical_attempts < LEAST(
                       opendart_quota_usage.daily_budget,
                       EXCLUDED.daily_budget
                     )
                    THEN EXCLUDED.last_grant_token
                    ELSE opendart_quota_usage.last_grant_token
                  END,
                  updated_at = now()
                RETURNING
                  opendart_quota_usage.usage_date,
                  opendart_quota_usage.effective_limit,
                  opendart_quota_usage.daily_budget,
                  opendart_quota_usage.physical_attempts,
                  opendart_quota_usage.exhausted_at,
                  opendart_quota_usage.exhausted_reason,
                  opendart_quota_usage.last_grant_token,
                  opendart_quota_usage.last_grant_token = %s AS granted
                """,
                (
                    usage_date,
                    config.daily_call_limit,
                    config.daily_call_budget,
                    grant_token,
                    grant_token,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("OpenDART quota UPSERT returned no row")
            usage = _quota_usage(row)
            granted = bool(row[7])
        if not granted:
            if usage.exhausted_at is not None:
                raise QuotaReservationDenied("OpenDART quota exhausted for the KST date")
            raise QuotaReservationDenied("OpenDART daily budget reservation denied")
        return usage

    def mark_exhausted(self, usage_date: date, reason: str) -> None:
        """status=020을 durable하게 기록해 같은 KST date의 이후 reservation을 모두 막는다."""
        if not reason or len(reason) > 64:
            raise ValueError("exhausted reason length is invalid")
        with self._connection.transaction():
            updated = self._connection.execute(
                """
                UPDATE opendart_quota_usage
                SET exhausted_at = COALESCE(exhausted_at, now()),
                    exhausted_reason = COALESCE(exhausted_reason, %s),
                    updated_at = now()
                WHERE usage_date = %s
                """,
                (reason, usage_date),
            ).rowcount
            if updated != 1:
                raise QuotaReservationDenied("cannot mark quota exhausted without a charged reservation")

    def get_usage(self, usage_date: date) -> QuotaUsage:
        """운영 수치만 반환하고 credential, request URL 또는 provider body는 읽지 않는다."""
        row = self._get_usage_row(usage_date)
        if row is None:
            raise KeyError("quota usage date does not exist")
        return _quota_usage(row)

    def _get_usage_row(self, usage_date: date) -> tuple[Any, ...] | None:
        return self._connection.execute(
            """
            SELECT usage_date, effective_limit, daily_budget, physical_attempts,
                   exhausted_at, exhausted_reason, last_grant_token
            FROM opendart_quota_usage
            WHERE usage_date = %s
            """,
            (usage_date,),
        ).fetchone()


class CalendarRepository:
    """sanitized observation/canonical/cursor를 collector role의 exact DML로 저장한다."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def acquire_collector_lock(self) -> bool:
        """session advisory lock으로 전체 OpenDART collector를 한 instance로 제한한다."""
        row = self._connection.execute(
            "SELECT pg_try_advisory_lock(%s)",
            (_COLLECTOR_ADVISORY_LOCK_ID,),
        ).fetchone()
        return bool(row and row[0])

    def release_collector_lock(self) -> None:
        """현재 connection이 보유한 collector advisory lock을 명시적으로 해제한다."""
        self._connection.execute(
            "SELECT pg_advisory_unlock(%s)",
            (_COLLECTOR_ADVISORY_LOCK_ID,),
        )

    def publish_observation_and_cursor(
        self,
        observation: CalendarObservation,
        cursor: CollectionCursor,
        *,
        persistence_mode: PersistenceMode,
        retention: RetentionRule | None,
        fail_before_commit: bool = False,
    ) -> None:
        """observation과 deterministic cursor를 같은 transaction에 묶어 crash 시 둘 다 rollback한다."""
        _validate_persistence(persistence_mode, retention)
        with self._connection.transaction():
            self._insert_observation(observation)
            self._upsert_cursor(cursor)
            if fail_before_commit:
                raise RuntimeError("injected crash before canonical transaction commit")

    def publish_page(
        self,
        commit: CalendarPageCommit,
        *,
        fail_before_commit: bool = False,
    ) -> None:
        """한 page의 sanitized observation, canonical, audit relation, cursor를 원자 게시한다."""
        _validate_page_commit(commit)
        with self._connection.transaction():
            self._insert_observation(commit.observation)
            self._upsert_source_health(commit.source_health)
            if commit.trading_session is not None:
                self._upsert_trading_session_and_revision(commit.trading_session)
            for event_write in commit.event_writes:
                self._insert_event(event_write)
            for transition in commit.disclosure_transitions:
                self._insert_disclosure_transition(transition)
            for source_link in commit.source_links:
                self._insert_source_link(source_link)
            for conflict in commit.conflicts:
                self._insert_conflict(conflict)
            self._upsert_cursor(commit.cursor)
            if fail_before_commit:
                raise RuntimeError("injected crash before canonical transaction commit")

    def upsert_trading_session(self, session: CanonicalTradingSession) -> None:
        """canonical session current row만 갱신하며 observation/conflict 과거 행은 재작성하지 않는다."""
        with self._connection.transaction():
            self._upsert_trading_session_and_revision(session)

    def append_event(self, revision: EventRevision, *, confidence_bps: int, status: str) -> bool:
        """event correction을 UPDATE하지 않고 새 revision으로 append하며 동일 hash rerun은 no-op 처리한다."""
        with self._connection.transaction():
            return self._insert_event(
                CalendarEventWrite(
                    revision=revision,
                    confidence_bps=confidence_bps,
                    status=status,
                )
            )

    def append_disclosure_transition(self, transition: DisclosureStateTransition) -> None:
        """공시 상태 correction/open/close를 기존 row UPDATE 없이 append한다."""
        with self._connection.transaction():
            self._insert_disclosure_transition(transition)

    def load_active_states(self, corp_code: str) -> list[object]:
        """scorer가 provider HTTP 없이 읽을 수 있는 sanitized active-state view만 조회한다."""
        return list(
            self._connection.execute(
                """
                SELECT transition_id, state_type, state_key, revision_no,
                       effective_at, observed_at, canonical_event_id, mapping_version
                FROM active_disclosure_risk_states
                WHERE corp_code = %s
                ORDER BY state_key
                """,
                (corp_code,),
            ).fetchall()
        )

    def observation_exists(self, observation_id: str) -> bool:
        row = self._connection.execute(
            "SELECT EXISTS (SELECT 1 FROM calendar_observations WHERE observation_id = %s)",
            (observation_id,),
        ).fetchone()
        return bool(row and row[0])

    def load_cursor(self, key: CursorKey) -> CollectionCursor | None:
        row = self._connection.execute(
            """
            SELECT source_id, operation, subject, window_from, window_to,
                   mapping_version, next_page, continuation, completed
            FROM calendar_collection_cursors
            WHERE source_id = %s AND operation = %s AND subject = %s
              AND window_from = %s AND window_to = %s AND mapping_version = %s
            """,
            key,
        ).fetchone()
        if row is None:
            return None
        return CollectionCursor(
            source_id=str(row[0]),
            operation=str(row[1]),
            subject=str(row[2]),
            window_from=row[3],
            window_to=row[4],
            mapping_version=str(row[5]),
            next_page=int(row[6]),
            continuation=None if row[7] is None else str(row[7]),
            completed=bool(row[8]),
        )

    def _insert_observation(self, observation: CalendarObservation) -> None:
        assert_sanitized_payload(observation.sanitized_payload)
        self._connection.execute(
            """
            INSERT INTO calendar_observations (
              observation_id, source_id, origin_group, capability,
              effective_from, effective_to, observed_at, ingested_at,
              sanitized_payload, sanitized_payload_hash, adapter_version,
              mapping_version, registry_version
            ) VALUES (
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s
            )
            ON CONFLICT (
              source_id, capability, effective_from, effective_to,
              sanitized_payload_hash, mapping_version
            ) DO NOTHING
            """,
            (
                observation.observation_id,
                observation.source_id,
                observation.origin_group,
                observation.capability,
                observation.effective_from,
                observation.effective_to,
                observation.observed_at,
                observation.ingested_at,
                Jsonb(observation.sanitized_payload),
                observation.sanitized_payload_hash,
                observation.adapter_version,
                observation.mapping_version,
                observation.registry_version,
            ),
        )

    def _upsert_source_health(self, health: SourceHealthSnapshot) -> None:
        self._connection.execute(
            """
            INSERT INTO calendar_source_health (
              source_id, last_success_at, last_failure_at, failure_count,
              stale_after, network_ready, status_code, error_code, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (source_id) DO UPDATE SET
              last_success_at = EXCLUDED.last_success_at,
              last_failure_at = EXCLUDED.last_failure_at,
              failure_count = EXCLUDED.failure_count,
              stale_after = EXCLUDED.stale_after,
              network_ready = EXCLUDED.network_ready,
              status_code = EXCLUDED.status_code,
              error_code = EXCLUDED.error_code,
              updated_at = now()
            """,
            (
                health.source_id,
                health.last_success_at,
                health.last_failure_at,
                health.failure_count,
                health.stale_after,
                health.network_ready,
                health.status_code,
                health.error_code,
            ),
        )

    def _upsert_trading_session_and_revision(self, session: CanonicalTradingSession) -> None:
        _validate_session(session)
        values = _session_values(session)
        self._connection.execute(
            """
            INSERT INTO trading_sessions (
              exchange_mic, session_date, is_open, open_at, close_at, timezone,
              reason, chosen_source_id, degraded, fallback_reason, as_of,
              confidence_bps, has_conflict, canonical_hash,
              canonical_rule_version, confidence_rule_version
            ) VALUES (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            ON CONFLICT (exchange_mic, session_date) DO UPDATE SET
              is_open = EXCLUDED.is_open,
              open_at = EXCLUDED.open_at,
              close_at = EXCLUDED.close_at,
              timezone = EXCLUDED.timezone,
              reason = EXCLUDED.reason,
              chosen_source_id = EXCLUDED.chosen_source_id,
              degraded = EXCLUDED.degraded,
              fallback_reason = EXCLUDED.fallback_reason,
              as_of = EXCLUDED.as_of,
              confidence_bps = EXCLUDED.confidence_bps,
              has_conflict = EXCLUDED.has_conflict,
              canonical_hash = EXCLUDED.canonical_hash,
              canonical_rule_version = EXCLUDED.canonical_rule_version,
              confidence_rule_version = EXCLUDED.confidence_rule_version,
              updated_at = now()
            """,
            values,
        )
        self._connection.execute(
            """
            INSERT INTO trading_session_revisions (
              revision_id, exchange_mic, session_date, revision_no,
              is_open, open_at, close_at, timezone, reason, chosen_source_id,
              degraded, fallback_reason, as_of, confidence_bps, has_conflict,
              canonical_hash, canonical_rule_version, confidence_rule_version
            )
            SELECT
              %s, %s, %s,
              COALESCE((
                SELECT max(revision_no) + 1
                FROM trading_session_revisions
                WHERE exchange_mic = %s AND session_date = %s
              ), 1),
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s
            WHERE NOT EXISTS (
              SELECT 1
              FROM trading_session_revisions
              WHERE exchange_mic = %s AND session_date = %s AND canonical_hash = %s
            )
            ON CONFLICT DO NOTHING
            """,
            (
                session.canonical_hash,
                session.exchange_mic,
                session.session_date,
                session.exchange_mic,
                session.session_date,
                *values[2:],
                session.exchange_mic,
                session.session_date,
                session.canonical_hash,
            ),
        )

    def _insert_event(self, event_write: CalendarEventWrite) -> bool:
        if event_write.status not in {"SCHEDULED", "TENTATIVE", "CONFIRMED", "ACTUAL", "CANCELLED"}:
            raise ValueError("calendar event status is outside the frozen lifecycle enum")
        if not 0 <= event_write.confidence_bps <= 9_900:
            raise ValueError("calendar event confidence is invalid")
        revision = event_write.revision
        candidate = revision.candidate
        assert_sanitized_payload(candidate.detail)
        row = self._connection.execute(
            """
            INSERT INTO calendar_events (
              event_id, event_series_key, revision_no, revised_from_event_id,
              source_id, source_event_key, source_revision, event_type, symbol,
              exchange_mic, event_date, detail, status, confidence_bps,
              has_conflict, canonical_hash
            ) VALUES (
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              false, %s
            )
            ON CONFLICT (event_series_key, canonical_hash) DO NOTHING
            RETURNING event_id
            """,
            (
                revision.event_id,
                revision.event_series_key,
                revision.revision_no,
                revision.revised_from_event_id,
                candidate.source_id,
                candidate.source_event_key,
                candidate.source_revision,
                candidate.event_type,
                candidate.symbol,
                candidate.exchange_mic,
                candidate.event_date,
                Jsonb(candidate.detail),
                event_write.status,
                event_write.confidence_bps,
                revision.canonical_hash,
            ),
        ).fetchone()
        return row is not None

    def _insert_disclosure_transition(self, transition: DisclosureStateTransition) -> None:
        self._connection.execute(
            """
            INSERT INTO disclosure_risk_state_transitions (
              transition_id, corp_code, state_type, state_key, transition_type,
              revision_no, revised_from_transition_id, source_id, source_event_key,
              source_revision, effective_at, observed_at, canonical_event_id, mapping_version
            ) VALUES (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            ON CONFLICT (source_id, source_event_key, source_revision) DO NOTHING
            """,
            (
                transition.transition_id,
                transition.corp_code,
                transition.state_type,
                transition.state_key,
                transition.transition,
                transition.revision_no,
                transition.revised_from_transition_id,
                transition.source_id,
                transition.source_event_key,
                transition.source_revision,
                transition.effective_on,
                transition.observed_at,
                transition.canonical_event_id,
                transition.mapping_version,
            ),
        )

    def _insert_source_link(self, source_link: CalendarEventSource) -> None:
        if len(source_link.opaque_source_ref) != 64:
            raise ValueError("calendar source link must use an opaque SHA-256 reference")
        assert_sanitized_payload(
            {
                "event_id": source_link.event_id,
                "exchange_mic": source_link.exchange_mic,
                "source_choice": source_link.source_choice,
                "resolution_reason": source_link.resolution_reason,
                "opaque_source_ref": source_link.opaque_source_ref,
            }
        )
        self._connection.execute(
            """
            INSERT INTO calendar_event_sources (
              event_source_id, event_id, exchange_mic, session_date,
              observation_id, source_choice, resolution_reason, opaque_source_ref
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, exchange_mic, session_date, observation_id) DO NOTHING
            """,
            (
                source_link.event_source_id,
                source_link.event_id,
                source_link.exchange_mic,
                source_link.session_date,
                source_link.observation_id,
                source_link.source_choice,
                source_link.resolution_reason,
                source_link.opaque_source_ref,
            ),
        )

    def _insert_conflict(self, conflict: CalendarConflictRecord) -> None:
        competing = list(conflict.competing_values)
        assert_sanitized_payload(competing)
        assert_sanitized_payload(conflict.chosen_value)
        assert_sanitized_payload(
            {
                "canonical_key": conflict.canonical_key,
                "field_name": conflict.field_name,
                "chosen_source_id": conflict.chosen_source_id,
                "resolution_rule": conflict.resolution_rule,
                "resolution_reason": conflict.resolution_reason,
            }
        )
        for item in competing:
            if set(item) != {"source_id", "tier", "origin_group", "value"}:
                raise ValueError("calendar conflict source projection is incomplete")
        self._connection.execute(
            """
            INSERT INTO calendar_conflicts (
              conflict_id, canonical_key, field_name, competing_values, chosen_value,
              chosen_source_id, resolution_rule, resolution_reason, unresolved, conflict_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (conflict_hash) DO NOTHING
            """,
            (
                conflict.conflict_id,
                conflict.canonical_key,
                conflict.field_name,
                Jsonb(competing),
                Jsonb(conflict.chosen_value),
                conflict.chosen_source_id,
                conflict.resolution_rule,
                conflict.resolution_reason,
                conflict.unresolved,
                conflict.conflict_hash,
            ),
        )

    def _upsert_cursor(self, cursor: CollectionCursor) -> None:
        self._connection.execute(
            """
            INSERT INTO calendar_collection_cursors (
              source_id, operation, subject, window_from, window_to,
              mapping_version, next_page, continuation, completed
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, operation, subject, window_from, window_to, mapping_version)
            DO UPDATE SET
              next_page = EXCLUDED.next_page,
              continuation = EXCLUDED.continuation,
              completed = EXCLUDED.completed,
              updated_at = now()
            """,
            (
                cursor.source_id,
                cursor.operation,
                cursor.subject,
                cursor.window_from,
                cursor.window_to,
                cursor.mapping_version,
                cursor.next_page,
                cursor.continuation,
                cursor.completed,
            ),
        )


def _quota_usage(row: tuple[Any, ...]) -> QuotaUsage:
    return QuotaUsage(
        usage_date=row[0],
        effective_limit=int(row[1]),
        daily_budget=int(row[2]),
        physical_attempts=int(row[3]),
        exhausted_at=row[4],
        exhausted_reason=None if row[5] is None else str(row[5]),
        last_grant_token=None if row[6] is None else str(row[6]),
    )


def _validate_page_commit(commit: CalendarPageCommit) -> None:
    _validate_persistence(commit.persistence_mode, commit.retention)
    if commit.cursor.source_id != commit.observation.source_id:
        raise ValueError("calendar cursor source must match its observation")
    if commit.source_health.source_id != commit.observation.source_id:
        raise ValueError("calendar source health must match its observation")
    if any(link.observation_id != commit.observation.observation_id for link in commit.source_links):
        raise ValueError("calendar source links must reference the page observation")
    if commit.source_health.failure_count < 0 or commit.source_health.stale_after.total_seconds() <= 0:
        raise ValueError("calendar source health bounds are invalid")


def _validate_persistence(mode: PersistenceMode, retention: RetentionRule | None) -> None:
    if mode == "ONLINE_PERSISTENT":
        if retention is None:
            raise ValueError("online persistent calendar write requires retention days and owner")
        if retention.days <= 0 or not retention.owner.strip():
            raise ValueError("online persistent calendar retention is invalid")
    elif mode == "OFFLINE_EPHEMERAL":
        if retention is not None:
            raise ValueError("offline ephemeral calendar write cannot declare persistent retention")
    else:
        raise ValueError("calendar persistence mode is invalid")


def _validate_session(session: CanonicalTradingSession) -> None:
    assert_sanitized_payload(
        {
            "exchange_mic": session.exchange_mic,
            "timezone": session.timezone,
            "reason": session.reason,
            "chosen_source_id": session.chosen_source_id,
            "fallback_reason": session.fallback_reason,
            "source_refs": session.source_refs,
            "canonical_rule_version": session.canonical_rule_version,
            "confidence_rule_version": session.confidence_rule_version,
        }
    )
    if session.is_open:
        if session.open_at is None or session.close_at is None or session.close_at <= session.open_at:
            raise ValueError("open trading session requires ordered open and close timestamps")
    elif session.open_at is not None or session.close_at is not None:
        raise ValueError("closed trading session cannot retain open or close timestamps")
    if len(session.canonical_hash) != 64:
        raise ValueError("trading session canonical hash is invalid")


def _session_values(session: CanonicalTradingSession) -> tuple[object, ...]:
    return (
        session.exchange_mic,
        session.session_date,
        session.is_open,
        session.open_at,
        session.close_at,
        session.timezone,
        session.reason,
        session.chosen_source_id,
        session.degraded,
        session.fallback_reason,
        session.as_of,
        session.confidence_bps,
        session.has_conflict,
        session.canonical_hash,
        session.canonical_rule_version,
        session.confidence_rule_version,
    )
