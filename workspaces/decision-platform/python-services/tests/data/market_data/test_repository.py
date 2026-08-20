from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.data.market_data.repository import MarketDataRepositoryError, adopt_seed_archive


@dataclass
class _Cursor:
    identity: tuple[object, ...]
    rows: list[tuple[object, ...] | None] = field(default_factory=list)
    rowcount: int = 0

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        del params
        if "session_user" in query:
            self.rows.append(self.identity)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0)

    def copy(self, statement: str) -> Any:
        raise AssertionError(f"copy must not be reached: {statement}")


@dataclass
class _Connection:
    cursor_value: _Cursor
    committed: int = 0
    rolled_back: int = 0

    def cursor(self) -> _Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def test_repository_rejects_non_writer_before_any_archive_copy() -> None:
    connection = _Connection(_Cursor(("decision_app", "decision_app")))

    with pytest.raises(MarketDataRepositoryError, match="writer role"):
        adopt_seed_archive(connection=connection, archive=object())  # type: ignore[arg-type]

    assert connection.committed == 0
    assert connection.rolled_back == 1
