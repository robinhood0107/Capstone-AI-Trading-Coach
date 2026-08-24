"""foreign-news sanitized aggregate의 append-only PostgreSQL writer port다."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final, TypeAlias, cast

import psycopg
from psycopg.types.json import Jsonb

from app.cross_market.foreign_news import ForeignNewsSentimentRecord


class ForeignNewsWriterAuthorityError(ValueError):
    """writer DSN이 V49의 single append capability보다 넓거나 다른 role일 때 발생한다."""


ConnectionFactory: TypeAlias = Callable[
    ...,
    AbstractContextManager[psycopg.Connection[Any]],
]

_APPEND_SIGNATURE: Final[str] = "append_owned_foreign_news_sentiment(text,jsonb)"
_TABLE: Final[str] = "foreign_news_sentiment_aggregates"


class PostgresForeignNewsSentimentRepository:
    """V49 definer function으로만 owner-local sanitized aggregate를 append한다.

    DSN·provider key·article text를 log/argv에 투영하지 않고, direct table DML 또는 read privilege가
    하나라도 보이면 append 전 fail-closed한다.
    """

    def __init__(
        self,
        database_dsn: str,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if not database_dsn.strip() or len(database_dsn) > 4096:
            raise ValueError("foreign-news database DSN is invalid")
        self._database_dsn = database_dsn
        self._connection_factory = connection_factory or cast(ConnectionFactory, psycopg.connect)

    def append(self, record: ForeignNewsSentimentRecord) -> str:
        """hash-only writer record 하나를 transaction 안에서 insert/replay로 확정한다."""

        try:
            with self._connection_factory(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_exact_writer_authority(connection)
                row = connection.execute(
                    "select append_owned_foreign_news_sentiment(%s, %s::jsonb)",
                    (record.owner_user_id, Jsonb(record.to_writer_record())),
                ).fetchone()
        except psycopg.Error as error:
            raise ForeignNewsWriterAuthorityError("foreign-news append rejected") from error
        disposition = str(_required_scalar(row))
        if disposition not in {"INSERTED", "REPLAY"}:
            raise ForeignNewsWriterAuthorityError("foreign-news append disposition is invalid")
        return disposition

    def preflight(self) -> None:
        """socket 이전에 writer role의 단일 append capability만 확인한다.

        외부 provider call 뒤 DB 권한 누락이 드러나 packet이 소비되는 일을 줄이기 위한 read-only
        preflight다. 이 method는 row를 만들거나 provider data를 읽지 않는다.
        """

        try:
            with self._connection_factory(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_exact_writer_authority(connection)
        except psycopg.Error as error:
            raise ForeignNewsWriterAuthorityError(
                "foreign-news writer preflight rejected"
            ) from error


def _attest_exact_writer_authority(connection: psycopg.Connection[Any]) -> None:
    role = str(_required_scalar(connection.execute("select current_user").fetchone()))
    if role != "decision_market_writer":
        raise ForeignNewsWriterAuthorityError(
            "foreign-news DSN must use decision_market_writer",
        )
    function_allowed = bool(
        _required_scalar(
            connection.execute(
                "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
                (_APPEND_SIGNATURE,),
            ).fetchone(),
        ),
    )
    if not function_allowed:
        raise ForeignNewsWriterAuthorityError("foreign-news append function is not allowed")
    table_privilege_present = bool(
        _required_scalar(
            connection.execute(
                """
                SELECT coalesce(bool_or(has_table_privilege(current_user, %s, privilege)), false)
                FROM unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS privilege
                """,
                (_TABLE,),
            ).fetchone(),
        ),
    )
    if table_privilege_present:
        raise ForeignNewsWriterAuthorityError("foreign-news writer has a direct table privilege")


def _required_scalar(row: Sequence[object] | None) -> object:
    if row is None or len(row) != 1 or row[0] is None:
        raise ForeignNewsWriterAuthorityError("foreign-news writer authority attestation failed")
    return row[0]
