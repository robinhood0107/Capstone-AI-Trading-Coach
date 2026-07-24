"""S2.3 공시 RPC가 사용하는 PostgreSQL sanitized projection reader."""

from __future__ import annotations

import os
import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.disclosure_rpc import StoredDisclosureBatch, StoredDisclosureEvent

_SOURCE_ID = "opendart-structured-events"
_QUERY_TIMEOUT_MS = 450
_STATE_COMPLETENESS_OPERATIONS = ("bnkMngtPcsp",)


class PostgresStoredDisclosureRepository:
    """비밀 DSN을 로그에 남기지 않고 read-only repeatable snapshot으로 공시 projection을 읽는다."""

    def __init__(self, database_dsn: str) -> None:
        if not database_dsn.strip():
            raise ValueError("DECISION_GRPC_DATABASE_DSN is required")
        self._database_dsn = database_dsn

    @classmethod
    def from_env(cls) -> "PostgresStoredDisclosureRepository":
        """runtime secret store가 주입한 DSN만 받고 테스트/production 값을 코드에 만들지 않는다."""
        return cls(os.environ.get("DECISION_GRPC_DATABASE_DSN", ""))

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
    ) -> StoredDisclosureBatch:
        """event·sourceRefs·cursor completeness를 한 repeatable-read DB snapshot에서 조립한다."""
        mapping = load_default_risk_mapping()
        required_operations = tuple(
            sorted(
                {
                    entry.official_endpoint
                    for entry in mapping.active_by_code.values()
                    if entry.official_endpoint is not None
                }.union(_STATE_COMPLETENESS_OPERATIONS)
            )
        )
        with psycopg.connect(
            self._database_dsn,
            autocommit=False,
            connect_timeout=2,
            row_factory=dict_row,
        ) as connection:
            with connection.transaction():
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{_QUERY_TIMEOUT_MS}ms",),
                )
                event_rows = connection.execute(
                    """
                    SELECT
                      event_id,
                      symbol,
                      corp_code,
                      event_code,
                      receipt_no,
                      occurred_on,
                      observed_at,
                      source_mapping_version,
                      source_ref,
                      attributes_json
                    FROM disclosure_event_observation_projection
                    WHERE symbol = %s
                      AND (%s::text IS NULL OR corp_code = %s::text)
                      AND occurred_on BETWEEN %s AND %s
                    ORDER BY occurred_on, event_code, receipt_no, source_ref
                    """,
                    (
                        symbol,
                        corp_code,
                        corp_code,
                        window_from,
                        window_to,
                    ),
                ).fetchall()
                cursor_rows = connection.execute(
                    """
                    SELECT DISTINCT ON (operation)
                      operation,
                      completed,
                      updated_at
                    FROM disclosure_collection_status_projection
                    WHERE source_id = %s
                      AND corp_code = %s
                      AND window_from <= %s
                      AND window_to >= %s
                      AND operation = ANY(%s)
                    ORDER BY operation, updated_at DESC
                    """,
                    (
                        _SOURCE_ID,
                        corp_code or "",
                        window_from,
                        window_to,
                        list(required_operations),
                    ),
                ).fetchall()

        events = _events_from_rows(event_rows, mapping.version)
        completed_operations = {
            str(row["operation"])
            for row in cursor_rows
            if bool(row["completed"])
        }
        complete = bool(corp_code) and completed_operations == set(required_operations)
        observed_at = max(
            (
                *[event.observed_at for event in events],
                *[_datetime(row["updated_at"]) for row in cursor_rows],
            ),
            default=datetime.fromtimestamp(0, tz=UTC),
        )
        cursor_ref = (
            _cursor_source_ref(cursor_rows)
            if complete
            else ()
        )
        return StoredDisclosureBatch(
            symbol=symbol,
            corp_code=corp_code or "",
            observed_at=observed_at,
            mapping_version=mapping.version,
            complete=complete,
            events=events,
            source_refs=cursor_ref,
        )


def _events_from_rows(
    rows: list[dict[str, Any]],
    mapping_version: str,
) -> tuple[StoredDisclosureEvent, ...]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["event_id"])].append(row)

    events: list[StoredDisclosureEvent] = []
    for event_id in sorted(grouped):
        group = grouped[event_id]
        first = group[0]
        attributes_value = first["attributes_json"]
        attributes = (
            {
                str(key): str(value)
                for key, value in attributes_value.items()
                if value is not None
            }
            if isinstance(attributes_value, dict)
            else {}
        )
        events.append(
            StoredDisclosureEvent(
                symbol=str(first["symbol"]),
                corp_code=str(first["corp_code"]),
                event_code=str(first["event_code"]),
                receipt_no=str(first["receipt_no"]),
                occurred_on=_date(first["occurred_on"]),
                observed_at=_datetime(first["observed_at"]),
                mapping_version=mapping_version,
                source_refs=tuple(sorted(str(row["source_ref"]) for row in group)),
                attributes=attributes,
            )
        )
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.occurred_on,
                event.event_code,
                event.receipt_no,
            ),
        )
    )


def _date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _cursor_source_ref(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    canonical = [
        {
            "completed": bool(row["completed"]),
            "operation": str(row["operation"]),
            "updatedAt": _datetime(row["updated_at"]).astimezone(UTC).isoformat(),
        }
        for row in sorted(rows, key=lambda item: str(item["operation"]))
    ]
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (hashlib.sha256(payload).hexdigest(),)
