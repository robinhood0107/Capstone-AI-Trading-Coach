from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.verification import provider_claim


class _Cursor:
    def __init__(self, claim_result: bool) -> None:
        self.claim_result = claim_result
        self.query_count = 0
        self.arguments: tuple[object, ...] | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, arguments: tuple[object, ...] | None = None) -> None:
        self.query_count += 1
        if "consume_p1_provider_approval" in query:
            self.arguments = arguments

    def fetchone(self) -> tuple[object, ...]:
        if self.query_count == 1:
            return ("decision_replay", "decision_replay", "decision")
        return (self.claim_result,)


class _Connection:
    def __init__(self, claim_result: bool) -> None:
        self.cursor_value = _Cursor(claim_result)
        self.committed = False

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True


def _approval() -> SimpleNamespace:
    return SimpleNamespace(
        approval_id="P1.TEST-CLAIM-01",
        nonce="a" * 32,
        allowed_operations=("ONE", "TWO"),
        physical_call_cap=2,
        expires_at=datetime(2030, 1, 2, 3, tzinfo=UTC),
        to_dict=lambda: {"contractId": "p1-approval-packet.v2"},
    )


def test_postgres_claim_is_exact_and_committed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection(True)
    monkeypatch.setattr(provider_claim, "_read_owner_private_file", lambda _: b"fixture")
    monkeypatch.setattr(
        provider_claim,
        "conninfo_to_dict",
        lambda _: {"user": "decision_replay", "dbname": "decision"},
    )
    monkeypatch.setattr(provider_claim.psycopg, "connect", lambda *args, **kwargs: connection)

    provider_claim.claim_signed_provider_approval(
        _approval(),
        dsn_file=Path("/run/secrets/p1-provider-claim-dsn"),
    )

    assert connection.committed is True
    assert connection.cursor_value.arguments is not None
    assert connection.cursor_value.arguments[4] == 2


def test_postgres_claim_rejects_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection(False)
    monkeypatch.setattr(provider_claim, "_read_owner_private_file", lambda _: b"fixture")
    monkeypatch.setattr(
        provider_claim,
        "conninfo_to_dict",
        lambda _: {"user": "decision_replay", "dbname": "decision"},
    )
    monkeypatch.setattr(provider_claim.psycopg, "connect", lambda *args, **kwargs: connection)

    with pytest.raises(provider_claim.ProviderApprovalClaimError, match="ALREADY_CONSUMED"):
        provider_claim.claim_signed_provider_approval(
            _approval(),
            dsn_file=Path("/run/secrets/p1-provider-claim-dsn"),
        )
