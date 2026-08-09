from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime

import pytest

from app.cross_market.foreign_news import (
    ForeignNewsSentimentMaterializer,
    ForeignNewsTransientLaneAggregate,
)
from app.cross_market.foreign_news_repository import (
    ForeignNewsWriterAuthorityError,
    PostgresForeignNewsSentimentRepository,
)


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def fetchone(self) -> tuple[object]:
        return (self._value,)


class _FakeConnection(AbstractContextManager["_FakeConnection"]):
    def __init__(
        self,
        *,
        role: str = "decision_market_writer",
        function_allowed: bool = True,
        table_privilege_present: bool = False,
        disposition: str = "INSERTED",
    ) -> None:
        self.role = role
        self.function_allowed = function_allowed
        self.table_privilege_present = table_privilege_present
        self.disposition = disposition
        self.appended_owner: str | None = None

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> _Result:
        normalized = " ".join(query.split())
        if normalized == "select current_user":
            return _Result(self.role)
        if "has_function_privilege" in normalized:
            return _Result(self.function_allowed)
        if "has_table_privilege" in normalized:
            return _Result(self.table_privilege_present)
        if normalized.startswith("select append_owned_foreign_news_sentiment"):
            assert isinstance(params, tuple)
            self.appended_owner = str(params[0])
            return _Result(self.disposition)
        raise AssertionError(f"unexpected SQL: {normalized}")


class _ConnectionFactory:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __call__(self, *_args: object, **_kwargs: object) -> _FakeConnection:
        return self.connection


def test_repository_appends_only_the_sanitized_writer_record() -> None:
    connection = _FakeConnection()
    repository = PostgresForeignNewsSentimentRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    disposition = repository.append(_record())

    assert disposition == "INSERTED"
    assert connection.appended_owner == "usr_demo_user"


@pytest.mark.parametrize(
    ("connection", "message"),
    (
        (_FakeConnection(role="decision_app"), "decision_market_writer"),
        (_FakeConnection(function_allowed=False), "append function"),
        (_FakeConnection(table_privilege_present=True), "direct table privilege"),
    ),
)
def test_repository_rejects_broad_or_wrong_writer_authority_before_append(
    connection: _FakeConnection,
    message: str,
) -> None:
    repository = PostgresForeignNewsSentimentRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    with pytest.raises(ForeignNewsWriterAuthorityError, match=message):
        repository.append(_record())

    assert connection.appended_owner is None


def test_repository_rejects_unknown_append_disposition() -> None:
    repository = PostgresForeignNewsSentimentRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(_FakeConnection(disposition="UPDATED")),
    )

    with pytest.raises(ForeignNewsWriterAuthorityError, match="append disposition"):
        repository.append(_record())


def _record():
    return ForeignNewsSentimentMaterializer().materialize(
        owner_user_id="usr_demo_user",
        symbol="005930",
        as_of=datetime(2026, 8, 9, 1, tzinfo=UTC),
        aggregates=(
            ForeignNewsTransientLaneAggregate(
                lane_id="GDELT_OFFLINE_REFERENCE",
                state="AVAILABLE",
                content_hash="a" * 64,
                official_release_locator=None,
            ),
        ),
    )
