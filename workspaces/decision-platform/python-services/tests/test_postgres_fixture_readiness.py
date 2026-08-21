from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests import conftest


def test_admin_connection_retries_until_the_host_port_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_connection = object()
    attempts = 0
    delays: list[float] = []

    def connect(_dsn: str, *, autocommit: bool) -> Any:
        nonlocal attempts
        assert autocommit is True
        attempts += 1
        if attempts < 3:
            raise psycopg.OperationalError("host port not ready")
        return expected_connection

    monkeypatch.setattr(conftest.psycopg, "connect", connect)
    monkeypatch.setattr(conftest.time, "sleep", delays.append)

    connection = conftest._connect_postgres_admin_with_host_readiness_retry(
        "postgresql://test",
        attempts=3,
        delay_seconds=0.25,
    )

    assert connection is expected_connection
    assert attempts == 3
    assert delays == [0.25, 0.25]


def test_admin_connection_retry_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    def connect(_dsn: str, *, autocommit: bool) -> Any:
        nonlocal attempts
        assert autocommit is True
        attempts += 1
        raise psycopg.OperationalError("host port unavailable")

    monkeypatch.setattr(conftest.psycopg, "connect", connect)
    monkeypatch.setattr(conftest.time, "sleep", delays.append)

    with pytest.raises(psycopg.OperationalError, match="host port unavailable"):
        conftest._connect_postgres_admin_with_host_readiness_retry(
            "postgresql://test",
            attempts=3,
            delay_seconds=0.25,
        )

    assert attempts == 3
    assert delays == [0.25, 0.25]
