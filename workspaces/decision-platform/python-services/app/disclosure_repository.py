"""S2.3 공시 RPC가 사용하는 PostgreSQL sanitized projection reader."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.disclosure_rpc import (
    QueryCancellation,
    StoredDisclosureBatch,
    StoredDisclosureEvent,
)

_SOURCE_ID = "opendart-structured-events"
_QUERY_TIMEOUT_MS = 450
_POOL_ACQUIRE_TIMEOUT_SECONDS = 0.45
_POOL_MAX_SIZE = 8
_STATE_COMPLETENESS_OPERATIONS = ("bnkMngtPcsp",)
_EXPECTED_READER_ROLE = "decision_disclosure_reader"
_REQUIRED_SELECT_TABLES = (
    "current_corporation_registry_projection",
    "disclosure_event_observation_projection",
    "disclosure_collection_status_projection",
)
_FORBIDDEN_TABLES = (
    "decisions",
    "audit_logs",
    "market_quote_observations",
    "corporation_registry_observations",
    "flyway_schema_history",
)
_FORBIDDEN_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")


class PostgresStoredDisclosureRepository:
    """비밀 DSN을 로그에 남기지 않고 read-only repeatable snapshot으로 공시 projection을 읽는다."""

    def __init__(self, database_dsn: str) -> None:
        if not database_dsn.strip():
            raise ValueError("DECISION_DISCLOSURE_READER_DATABASE_DSN is required")
        _attest_reader_dsn(database_dsn)
        self._pool: ConnectionPool[Connection[dict[str, Any]]] = ConnectionPool(
            conninfo=database_dsn,
            kwargs={
                "autocommit": False,
                "connect_timeout": 1,
                "row_factory": dict_row,
            },
            min_size=0,
            max_size=_POOL_MAX_SIZE,
            timeout=_POOL_ACQUIRE_TIMEOUT_SECONDS,
            max_waiting=_POOL_MAX_SIZE,
            open=True,
        )

    @classmethod
    def from_env(cls) -> "PostgresStoredDisclosureRepository":
        """runtime secret store가 주입한 DSN만 받고 테스트/production 값을 코드에 만들지 않는다."""
        return cls(os.environ.get("DECISION_DISCLOSURE_READER_DATABASE_DSN", ""))

    def close(self) -> None:
        """bounded pool worker와 physical connection을 gRPC server lifecycle에 맞춰 닫는다."""
        self._pool.close()

    def __enter__(self) -> "PostgresStoredDisclosureRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
        cancellation: QueryCancellation | None = None,
    ) -> StoredDisclosureBatch:
        """event·sourceRefs·cursor completeness를 한 repeatable-read DB snapshot에서 조립한다."""
        cancellation = cancellation or QueryCancellation()
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
        cancellation.raise_if_cancelled()
        with self._pool.connection(timeout=_POOL_ACQUIRE_TIMEOUT_SECONDS) as connection:
            cancellation.raise_if_cancelled()
            cancellation.attach(connection)
            try:
                with connection.transaction():
                    cancellation.raise_if_cancelled()
                    connection.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    connection.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{_QUERY_TIMEOUT_MS}ms",),
                    )
                    resolved_corp_code = corp_code
                    if resolved_corp_code is None:
                        cancellation.raise_if_cancelled()
                        registry_rows = connection.execute(
                            """
                            SELECT corp_code
                            FROM current_corporation_registry_projection
                            WHERE symbol = %s
                            ORDER BY corp_code
                            LIMIT 2
                            """,
                            (symbol,),
                        ).fetchall()
                        if len(registry_rows) == 1:
                            resolved_corp_code = str(registry_rows[0]["corp_code"])
                    if resolved_corp_code is None:
                        event_rows: list[dict[str, Any]] = []
                        cursor_rows: list[dict[str, Any]] = []
                    else:
                        cancellation.raise_if_cancelled()
                        event_id_rows = connection.execute(
                            """
                            SELECT
                              event_id,
                              min(occurred_on) AS occurred_on,
                              min(event_code) AS event_code,
                              min(receipt_no) AS receipt_no
                            FROM disclosure_event_observation_projection
                            WHERE symbol = %s
                              AND corp_code = %s
                              AND occurred_on BETWEEN %s AND %s
                            GROUP BY event_id
                            ORDER BY occurred_on, event_code, receipt_no, event_id
                            LIMIT 101
                            """,
                            (
                                symbol,
                                resolved_corp_code,
                                window_from,
                                window_to,
                            ),
                        ).fetchall()
                        if len(event_id_rows) > 100:
                            raise ValueError("stored disclosure event bound exceeded")
                        event_ids = [str(row["event_id"]) for row in event_id_rows]
                        cancellation.raise_if_cancelled()
                        event_rows = (
                            connection.execute(
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
                                  AND corp_code = %s
                                  AND occurred_on BETWEEN %s AND %s
                                  AND event_id = ANY(%s)
                                ORDER BY occurred_on, event_code, receipt_no, source_ref
                                LIMIT 101
                                """,
                                (
                                    symbol,
                                    resolved_corp_code,
                                    window_from,
                                    window_to,
                                    event_ids,
                                ),
                            ).fetchall()
                            if event_ids
                            else []
                        )
                        cancellation.raise_if_cancelled()
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
                                resolved_corp_code,
                                window_from,
                                window_to,
                                list(required_operations),
                            ),
                        ).fetchall()
            finally:
                cancellation.detach(connection)

        events = _events_from_rows(event_rows, mapping.version)
        completed_operations = {
            str(row["operation"])
            for row in cursor_rows
            if bool(row["completed"])
        }
        complete = bool(resolved_corp_code) and completed_operations == set(required_operations)
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
            corp_code=resolved_corp_code or "",
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


def _attest_reader_dsn(database_dsn: str) -> None:
    """공시 sidecar DSN이 전용 projection reader role인지 pool 생성 전에 검증한다."""
    with psycopg.connect(database_dsn, autocommit=True, connect_timeout=1) as connection:
        current_user = str(_required_scalar(connection.execute("select current_user").fetchone()))
        if current_user != _EXPECTED_READER_ROLE:
            raise ValueError("stored disclosure repository requires decision_disclosure_reader role")
        for table in _REQUIRED_SELECT_TABLES:
            if not _has_table_privilege(connection, table, "SELECT"):
                raise ValueError("stored disclosure reader lacks required projection SELECT")
            for privilege in _FORBIDDEN_PRIVILEGES:
                if _has_table_privilege(connection, table, privilege):
                    raise ValueError("stored disclosure reader has unexpected write privilege")
        for table in _FORBIDDEN_TABLES:
            for privilege in ("SELECT", *_FORBIDDEN_PRIVILEGES):
                if _has_table_privilege(connection, table, privilege):
                    raise ValueError("stored disclosure reader has unrelated table privilege")
        if bool(
            _required_scalar(
                connection.execute(
                    "select has_schema_privilege(current_user, 'public', 'CREATE')"
                ).fetchone()
            )
        ):
            raise ValueError("stored disclosure reader must not create schema objects")


def _has_table_privilege(
    connection: Connection[Any],
    table: str,
    privilege: str,
) -> bool:
    return bool(
        _required_scalar(
            connection.execute(
                "select has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
        )
    )


def _required_scalar(row: tuple[Any, ...] | None) -> Any:
    if row is None:
        raise ValueError("stored disclosure reader attestation returned no row")
    return row[0]
