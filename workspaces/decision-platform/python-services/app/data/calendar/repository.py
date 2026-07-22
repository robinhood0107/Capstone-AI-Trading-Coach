from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.data.calendar.errors import QuotaReservationDenied
from app.data.calendar.models import (
    CalendarObservation,
    CanonicalTradingSession,
    CollectionCursor,
    CursorKey,
    QuotaUsage,
)
from app.data.calendar.normalizer import EventRevision
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
        fail_before_commit: bool = False,
    ) -> None:
        """observation과 deterministic cursor를 같은 transaction에 묶어 crash 시 둘 다 rollback한다."""
        with self._connection.transaction():
            self._insert_observation(observation)
            self._upsert_cursor(cursor)
            if fail_before_commit:
                raise RuntimeError("injected crash before canonical transaction commit")

    def upsert_trading_session(self, session: CanonicalTradingSession) -> None:
        """canonical session current row만 갱신하며 observation/conflict 과거 행은 재작성하지 않는다."""
        with self._connection.transaction():
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
                (
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
                ),
            )

    def append_event(self, revision: EventRevision, *, confidence_bps: int, status: str) -> bool:
        """event correction을 UPDATE하지 않고 새 revision으로 append하며 동일 hash rerun은 no-op 처리한다."""
        candidate = revision.candidate
        with self._connection.transaction():
            row = self._connection.execute(
                """
                INSERT INTO calendar_events (
                  event_id, event_series_key, revision_no, revised_from_event_id,
                  source_id, source_event_key, source_revision, event_type, symbol,
                  event_date, detail, status, confidence_bps, has_conflict, canonical_hash
                ) VALUES (
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, false, %s
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
                    candidate.event_date,
                    Jsonb(candidate.detail),
                    status,
                    confidence_bps,
                    revision.canonical_hash,
                ),
            ).fetchone()
            return row is not None

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
