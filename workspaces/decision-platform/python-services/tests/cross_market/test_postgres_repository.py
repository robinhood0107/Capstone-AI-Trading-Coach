from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date

import pytest

from app.cross_market.fixture_producer import CrossMarketFixtureBatch, SyntheticEodFixtureFactory
from app.cross_market.postgres_repository import (
    CrossMarketWriterAuthorityError,
    PostgresAppendOnlyCrossMarketRepository,
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
        functions_allowed: bool = True,
        table_privilege_present: bool = False,
        snapshot_writer_allowed: bool = False,
        disposition: str = "INSERTED",
    ) -> None:
        self.role = role
        self.functions_allowed = functions_allowed
        self.table_privilege_present = table_privilege_present
        self.snapshot_writer_allowed = snapshot_writer_allowed
        self.disposition = disposition
        self.append_calls: list[str] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> _Result:
        normalized = " ".join(query.split())
        if normalized == "select current_user":
            return _Result(self.role)
        if "bool_and(has_function_privilege" in normalized:
            return _Result(self.functions_allowed)
        if "bool_or(has_table_privilege" in normalized:
            return _Result(self.table_privilege_present)
        if "append_cross_market_risk_snapshot(jsonb)" in normalized:
            return _Result(self.snapshot_writer_allowed)
        if normalized.startswith("select append_"):
            self.append_calls.append(normalized.split("(", maxsplit=1)[0].removeprefix("select "))
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


def _minimal_batch() -> CrossMarketFixtureBatch:
    full = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    return CrossMarketFixtureBatch(
        entitlements=full.entitlements[:1],
        exposures=full.exposures[:1],
        observations=full.observations[:1],
        analyst_evidence=full.analyst_evidence[:1],
        cause_evidence=full.cause_evidence[:1],
    )


def test_repository_attests_exact_role_and_appends_all_groups_in_one_connection() -> None:
    connection = _FakeConnection()
    factory = _ConnectionFactory(connection)
    repository = PostgresAppendOnlyCrossMarketRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=factory,
    )

    result = repository.append_batch(_minimal_batch())

    assert result.inserted == 5
    assert result.replayed == 0
    assert connection.append_calls == [
        "append_market_source_entitlement",
        "append_cross_market_exposure_catalog_entry",
        "append_cross_market_observation",
        "append_analyst_revision_evidence",
        "append_market_cause_evidence",
    ]
    assert factory.arguments == (
        ("postgresql://fixture.invalid/decision",),
        {"autocommit": False, "connect_timeout": 2},
    )


def test_repository_counts_hash_stable_replay() -> None:
    connection = _FakeConnection(disposition="REPLAY")
    repository = PostgresAppendOnlyCrossMarketRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    result = repository.append_batch(_minimal_batch())

    assert result.inserted == 0
    assert result.replayed == 5


@pytest.mark.parametrize(
    ("connection", "message"),
    [
        (_FakeConnection(role="decision_app"), "decision_market_writer"),
        (_FakeConnection(functions_allowed=False), "append functions"),
        (_FakeConnection(table_privilege_present=True), "direct table privilege"),
        (_FakeConnection(snapshot_writer_allowed=True), "snapshot writer"),
    ],
)
def test_repository_rejects_wrong_or_broad_database_authority_before_any_append(
    connection: _FakeConnection,
    message: str,
) -> None:
    repository = PostgresAppendOnlyCrossMarketRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    with pytest.raises(CrossMarketWriterAuthorityError, match=message):
        repository.append_batch(_minimal_batch())

    assert connection.append_calls == []


def test_repository_rejects_unknown_database_disposition() -> None:
    connection = _FakeConnection(disposition="UPDATED")
    repository = PostgresAppendOnlyCrossMarketRepository(
        "postgresql://fixture.invalid/decision",
        connection_factory=_ConnectionFactory(connection),
    )

    with pytest.raises(RuntimeError, match="unexpected append disposition"):
        repository.append_batch(_minimal_batch())
