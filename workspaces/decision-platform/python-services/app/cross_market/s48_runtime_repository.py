"""V50 S4.8 runtime projection의 function-only writer adapter다."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final, TypeAlias, cast

import psycopg
from psycopg.types.json import Jsonb

from app.cross_market.s48_runtime import S48RuntimeAppendSummary, S48RuntimeBatch


class S48RuntimeWriterAuthorityError(ValueError):
    """S4.8 writer DSN이 exact append-only role/function boundary를 벗어났음을 나타낸다."""


ConnectionFactory: TypeAlias = Callable[
    ...,
    AbstractContextManager[psycopg.Connection[Any]],
]

_APPEND_FUNCTION: Final[str] = "append_s48_runtime_sanitized_projection"
_APPEND_SIGNATURE: Final[str] = f"{_APPEND_FUNCTION}(jsonb)"
_STORAGE_TABLE: Final[str] = "s48_runtime_sanitized_projections"


class PostgresS48RuntimeRepository:
    """Core 6/Optional 3 lane은 V50 definer function으로만 append하고 table DML은 갖지 않는다."""

    def __init__(
        self,
        database_dsn: str,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if not database_dsn.strip() or len(database_dsn) > 4096:
            raise ValueError("S4.8 runtime database DSN is invalid")
        self._database_dsn = database_dsn
        self._connection_factory = connection_factory or cast(ConnectionFactory, psycopg.connect)

    def append_batch(self, batch: S48RuntimeBatch) -> S48RuntimeAppendSummary:
        """Nine sanitized records를 one connection에서 replay-safe하게 append한다."""

        inserted = 0
        replayed = 0
        try:
            with self._connection_factory(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_exact_writer_authority(connection)
                for record in batch.writer_records():
                    row = connection.execute(
                        f"select {_APPEND_FUNCTION}(%s::jsonb)",
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
                raise S48RuntimeWriterAuthorityError("S4.8 runtime logical identity conflict") from error
            raise
        return S48RuntimeAppendSummary(inserted=inserted, replayed=replayed)


def _attest_exact_writer_authority(connection: psycopg.Connection[Any]) -> None:
    role = str(_required_scalar(connection.execute("select current_user").fetchone()))
    if role != "decision_market_writer":
        raise S48RuntimeWriterAuthorityError("S4.8 runtime DSN must use decision_market_writer")

    function_allowed = bool(
        _required_scalar(
            connection.execute(
                "select has_function_privilege(current_user, %s, 'EXECUTE')",
                (_APPEND_SIGNATURE,),
            ).fetchone(),
        )
    )
    if not function_allowed:
        raise S48RuntimeWriterAuthorityError("S4.8 runtime append function is not allowed")

    direct_table_privilege = bool(
        _required_scalar(
            connection.execute(
                """
                select coalesce(bool_or(has_table_privilege(current_user, %s, privilege)), false)
                from unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) as privilege
                """,
                (_STORAGE_TABLE,),
            ).fetchone(),
        )
    )
    if direct_table_privilege:
        raise S48RuntimeWriterAuthorityError(
            "S4.8 runtime writer has a forbidden direct table privilege",
        )


def _required_scalar(row: Sequence[object] | None) -> object:
    if row is None or len(row) != 1 or row[0] is None:
        raise S48RuntimeWriterAuthorityError("S4.8 runtime writer authority attestation failed")
    return row[0]
