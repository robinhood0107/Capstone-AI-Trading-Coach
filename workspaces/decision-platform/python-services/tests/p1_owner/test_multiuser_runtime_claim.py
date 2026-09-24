"""Focused contracts for concurrent owner scheduling and database adapters."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock
from typing import Any, cast

import pytest

from app.p1_owner import automation_runtime as runtime
from app.p1_owner.automation_runtime import (
    AutomationRuntimeError,
    AutomationRuntimeService,
    PostgresAutomationRuntimeRepository,
    RuntimeClaim,
)


class _Cursor:
    def __init__(self, rows: list[Any], row: Any | None = None) -> None:
        self.rows = rows
        self.row = row
        self.query = ""
        self.params: tuple[Any, ...] = ()

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[Any]:
        return self.rows

    def fetchone(self) -> Any | None:
        return self.row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self, **_: object) -> _Cursor:
        return self._cursor


def _repository(
    monkeypatch: pytest.MonkeyPatch, cursor: _Cursor
) -> PostgresAutomationRuntimeRepository:
    repository = PostgresAutomationRuntimeRepository(
        "postgresql://decision_automation_runtime:runtime-test@postgres:5432/capstone_p1"
    )
    monkeypatch.setattr(repository, "_connect", lambda **_: _Connection(cursor))
    return repository


def test_owner_list_is_bounded_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    owners = [(f"usr_owner_{index:04d}",) for index in range(1, 3)]
    cursor = _Cursor(owners)
    assert _repository(monkeypatch, cursor).armed_owner_user_ids() == tuple(
        row[0] for row in owners
    )
    cursor.rows = [(f"usr_owner_{index:04d}",) for index in range(101)]
    with pytest.raises(AutomationRuntimeError, match="AUTOMATION_OWNER_ADMISSION_INVALID"):
        _repository(monkeypatch, cursor).armed_owner_user_ids()


def test_claim_hash_is_user_scoped() -> None:
    shared = b"x" * 32
    target = date(2026, 9, 24)
    first = runtime._owner_claim_hash(shared, "usr_owner_0001", target)
    second = runtime._owner_claim_hash(shared, "usr_owner_0002", target)
    assert first != second
    assert first == runtime._owner_claim_hash(shared, "usr_owner_0001", target)


def test_full_owner_session_claims_and_drives_only_the_requested_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "usr_owner_0001"
    target = date(2026, 9, 24)
    claim_hash = runtime._owner_claim_hash(b"x" * 32, user_id, target)
    claim = RuntimeClaim(
        user_id=user_id,
        run_id="auto_run_owner_0001",
        control_version=3,
        account_id="acct_" + "a" * 32,
        principle_id="prn_owner_0001",
        strategy_id="stg_owner_0001",
        baseline_account_digest="b" * 64,
        replayed=False,
        session_date=target,
        claim_token_hash=claim_hash,
    )
    observed: list[tuple[str, str]] = []

    class Repository:
        def last_completed_session(self, owner: str) -> None:
            observed.append(("last_completed", owner))
            return None

        def settle_missed_schedules(self, owner: str, _session: date) -> int:
            observed.append(("settle", owner))
            return 0

        def claim_for_owner(self, owner: str, session: date, token: str) -> RuntimeClaim:
            assert owner == user_id and session == target
            assert token == claim_hash
            observed.append(("claim", owner))
            return claim

        def read_state(self, actual: RuntimeClaim) -> dict[str, object]:
            assert actual.user_id == user_id
            observed.append(("read_state", actual.user_id))
            return {"state": "COMPLETED"}

    service = AutomationRuntimeService(Repository(), cast(Any, None), "x" * 32)
    monkeypatch.setattr(
        service, "_drive_claim", lambda actual: observed.append(("drive", actual.user_id))
    )
    outcome, retry_at = service._process_full_owner_session(user_id, target)
    assert outcome == "DONE"
    assert retry_at is None
    assert all(owner == user_id for _, owner in observed)


def test_two_full_owner_sessions_run_concurrently_without_crossing_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owners = ("usr_owner_0001", "usr_owner_0002")
    target = date(2026, 9, 24)
    claim_barrier = Barrier(2)
    observed: list[tuple[str, str]] = []
    lock = Lock()

    class Repository:
        def last_completed_session(self, owner: str) -> None:
            assert owner in owners
            with lock:
                observed.append(("recovery", owner))
            return None

        def settle_missed_schedules(self, owner: str, _session: date) -> int:
            assert owner in owners
            with lock:
                observed.append(("settle", owner))
            return 0

        def claim_for_owner(self, owner: str, session: date, token: str) -> RuntimeClaim:
            assert owner in owners and session == target
            assert token == runtime._owner_claim_hash(b"x" * 32, owner, target)
            claim_barrier.wait(timeout=3)
            return RuntimeClaim(
                user_id=owner,
                run_id=f"auto_run_{owner[-4:]}",
                control_version=3,
                account_id=f"acct_{owner[-4:]}",
                principle_id=f"prn_{owner[-4:]}",
                strategy_id=f"stg_{owner[-4:]}",
                baseline_account_digest=owner[-1] * 64,
                replayed=False,
                session_date=session,
                claim_token_hash=token,
            )

        def read_state(self, claim: RuntimeClaim) -> dict[str, object]:
            assert claim.user_id in owners
            assert claim.account_id == f"acct_{claim.user_id[-4:]}"
            with lock:
                observed.append(("read", claim.user_id))
            return {"state": "COMPLETED"}

    service = AutomationRuntimeService(Repository(), cast(Any, None), "x" * 32)

    def drive(claim: RuntimeClaim) -> None:
        assert claim.user_id in owners
        assert claim.run_id == f"auto_run_{claim.user_id[-4:]}"
        assert claim.account_id == f"acct_{claim.user_id[-4:]}"
        with lock:
            observed.append(("drive", claim.user_id))

    monkeypatch.setattr(service, "_drive_claim", drive)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(lambda owner: service._process_full_owner_session(owner, target), owners)
        )

    assert results == (("DONE", None), ("DONE", None))
    assert {owner for event, owner in observed if event == "drive"} == set(owners)
    assert {owner for event, owner in observed if event == "read"} == set(owners)
