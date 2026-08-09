from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime

import pytest

from app.cross_market.s48_runtime import S48RuntimeMaterializer
from app.cross_market.s48_runtime_repository import (
    S48RuntimeWriterAuthorityError,
    PostgresS48RuntimeRepository,
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
        self.append_calls = 0

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
        if normalized.startswith("select append_s48_runtime_sanitized_projection"):
            self.append_calls += 1
            assert params is not None
            return _Result(self.disposition)
        raise AssertionError(f"unexpected SQL: {normalized}")


class _ConnectionFactory:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.arguments: tuple[tuple[object, ...], dict[str, object]] | None = None

    def __call__(self, *args: object, **kwargs: object) -> _FakeConnection:
        self.arguments = (args, kwargs)
        return self.connection


def test_repository_attests_exact_writer_and_appends_all_nine_lanes() -> None:
    connection = _FakeConnection()
    factory = _ConnectionFactory(connection)
    repository = PostgresS48RuntimeRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=factory,
    )

    result = repository.append_batch(_batch())

    assert result.inserted == 9
    assert result.replayed == 0
    assert connection.append_calls == 9
    assert factory.arguments == (
        ("postgresql://fixture.invalid/decision",),
        {"autocommit": False, "connect_timeout": 2},
    )


@pytest.mark.parametrize(
    ("connection", "message"),
    [
        (_FakeConnection(role="decision_app"), "decision_market_writer"),
        (_FakeConnection(function_allowed=False), "append function"),
        (_FakeConnection(table_privilege_present=True), "direct table privilege"),
    ],
)
def test_repository_rejects_broad_or_wrong_authority_before_append(
    connection: _FakeConnection,
    message: str,
) -> None:
    repository = PostgresS48RuntimeRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    with pytest.raises(S48RuntimeWriterAuthorityError, match=message):
        repository.append_batch(_batch())

    assert connection.append_calls == 0


def test_repository_rejects_unknown_append_disposition() -> None:
    connection = _FakeConnection(disposition="UPDATED")
    repository = PostgresS48RuntimeRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    with pytest.raises(RuntimeError, match="unexpected append disposition"):
        repository.append_batch(_batch())


def _batch():
    return S48RuntimeMaterializer().materialize(
        evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
    )
