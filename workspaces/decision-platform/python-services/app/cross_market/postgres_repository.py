from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final, TypeAlias, cast

import psycopg
from psycopg.types.json import Jsonb

from app.cross_market.fixture_producer import (
    AppendSummary,
    CrossMarketFixtureBatch,
    PayloadConflictError,
)


class CrossMarketWriterAuthorityError(ValueError):
    """offline writer DSN이 exact append-only authority가 아닐 때 fail-closed한다."""


ConnectionFactory: TypeAlias = Callable[
    ...,
    AbstractContextManager[psycopg.Connection[Any]],
]

_FUNCTION_BY_GROUP: Final[dict[str, str]] = {
    "ENTITLEMENT": "append_market_source_entitlement",
    "EXPOSURE": "append_cross_market_exposure_catalog_entry",
    "OBSERVATION": "append_cross_market_observation",
    "ANALYST": "append_analyst_revision_evidence",
    "CAUSE": "append_market_cause_evidence",
}
_FUNCTION_SIGNATURES: Final[tuple[str, ...]] = tuple(
    f"{function}(jsonb)" for function in _FUNCTION_BY_GROUP.values()
)
_STORAGE_TABLES: Final[tuple[str, ...]] = (
    "market_source_entitlements",
    "cross_market_exposure_catalog_entries",
    "cross_market_observations",
    "analyst_revision_evidence",
    "market_cause_evidence",
    "cross_market_risk_snapshots",
    "cross_market_snapshot_evidence_links",
)


class PostgresAppendOnlyCrossMarketRepository:
    """V23 SECURITY DEFINER 함수만 사용해 fixture batch를 한 transaction으로 append한다.

    연결 role, direct table privilege, snapshot writer authority를 쓰기 전에 확인한다. 외부
    provider나 파일 입력을 열지 않으며 DB conflict는 domain-level conflict로 보존한다.
    """

    def __init__(
        self,
        database_dsn: str,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if not database_dsn.strip() or len(database_dsn) > 4096:
            raise ValueError("cross-market database DSN is invalid")
        self._database_dsn = database_dsn
        self._connection_factory = connection_factory or cast(ConnectionFactory, psycopg.connect)

    def append_batch(self, batch: CrossMarketFixtureBatch) -> AppendSummary:
        inserted = 0
        replayed = 0
        try:
            with self._connection_factory(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_exact_writer_authority(connection)
                for group, records in batch.record_groups():
                    function = _FUNCTION_BY_GROUP[group]
                    for record in records:
                        # 함수 이름은 compile-time allowlist에서만 선택하고 record는 Jsonb bind로 보낸다.
                        row = connection.execute(
                            f"select {function}(%s::jsonb)",
                            (Jsonb(record),),
                        ).fetchone()
                        disposition = str(_required_scalar(row))
                        if disposition == "INSERTED":
                            inserted += 1
                        elif disposition == "REPLAY":
                            replayed += 1
                        else:
                            raise RuntimeError("unexpected append disposition")
        except psycopg.Error as error:
            if error.sqlstate == "23505":
                raise PayloadConflictError("cross-market logical identity conflict") from error
            raise
        return AppendSummary(inserted=inserted, replayed=replayed)


def _attest_exact_writer_authority(connection: psycopg.Connection[Any]) -> None:
    role = str(_required_scalar(connection.execute("select current_user").fetchone()))
    if role != "decision_market_writer":
        raise CrossMarketWriterAuthorityError(
            "cross-market DSN must use decision_market_writer",
        )

    append_functions_allowed = bool(
        _required_scalar(
            connection.execute(
                """
                select bool_and(has_function_privilege(current_user, function_signature, 'EXECUTE'))
                from unnest(%s::text[]) as function_signature
                """,
                (list(_FUNCTION_SIGNATURES),),
            ).fetchone(),
        ),
    )
    if not append_functions_allowed:
        raise CrossMarketWriterAuthorityError(
            "cross-market append functions are not all allowed",
        )

    direct_table_privilege = bool(
        _required_scalar(
            connection.execute(
                """
                select coalesce(bool_or(has_table_privilege(current_user, table_name, privilege)), false)
                from unnest(%s::text[]) as table_name
                cross join unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) as privilege
                """,
                (list(_STORAGE_TABLES),),
            ).fetchone(),
        ),
    )
    if direct_table_privilege:
        raise CrossMarketWriterAuthorityError(
            "cross-market writer has a forbidden direct table privilege",
        )

    snapshot_writer_allowed = bool(
        _required_scalar(
            connection.execute(
                """
                select case
                  when to_regprocedure('append_cross_market_risk_snapshot(jsonb)') is null then false
                  else has_function_privilege(
                    current_user,
                    'append_cross_market_risk_snapshot(jsonb)',
                    'EXECUTE'
                  )
                end
                """,
            ).fetchone(),
        ),
    )
    if snapshot_writer_allowed:
        raise CrossMarketWriterAuthorityError(
            "cross-market snapshot writer authority is forbidden in S4.8B",
        )


def _required_scalar(row: Sequence[object] | None) -> object:
    if row is None or len(row) != 1 or row[0] is None:
        raise CrossMarketWriterAuthorityError("cross-market writer authority attestation failed")
    return row[0]
