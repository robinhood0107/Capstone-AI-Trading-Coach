"""S4.8 Core 6/Optional 3 typed runtime을 provider 없이 materialize하는 local operator CLI다."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

import psycopg

from app.cross_market.s48_runtime import S48RuntimeBatch, S48RuntimeMaterializer
from app.cross_market.s48_runtime_repository import (
    PostgresS48RuntimeRepository,
    S48RuntimeWriterAuthorityError,
)

_WRITER_DSN_ENV: Final[str] = "DECISION_MARKET_WRITER_DATABASE_DSN"
_OFFLINE_TARGET_ENV: Final[str] = "DECISION_SOURCE_WRITER_OFFLINE_TARGET"
_ALLOWED_OFFLINE_TARGETS: Final[frozenset[str]] = frozenset(
    {"local", "offline", "test", "testcontainers"}
)


def main(argv: Sequence[str] | None = None) -> int:
    """No-argv-secret CLI로 exact nine S4.8 state를 materialize 또는 local DB stage한다.

    `materialize`와 `stage` 모두 provider transport, raw response, retry를 만들지 않는다. stage는
    explicitly offline target인 function-only market-writer DSN이 있어야 하며 static typed blocker를
    future entitlement/packet 승인으로 해석하지 않는다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("materialize",):
        _emit(_receipt(_batch(), code="S48_RUNTIME_MATERIALIZED", state="MATERIALIZED"))
        return 0
    if arguments == ("stage",):
        return _stage()
    _emit({"code": "S48_RUNTIME_COMMAND_INVALID", "state": "FAILED"})
    return 2


def _stage() -> int:
    """V50 append function으로 only offline typed state를 replay-safe하게 stage한다."""

    if os.environ.get(_OFFLINE_TARGET_ENV, "").strip().lower() not in _ALLOWED_OFFLINE_TARGETS:
        _emit({"code": "S48_RUNTIME_OFFLINE_TARGET_REQUIRED", "state": "FAILED"})
        return 2
    database_dsn = os.environ.get(_WRITER_DSN_ENV, "").strip()
    if not database_dsn:
        _emit({"code": "S48_RUNTIME_WRITER_DATABASE_DSN", "state": "FAILED"})
        return 2

    batch = _batch()
    try:
        summary = PostgresS48RuntimeRepository(database_dsn=database_dsn).append_batch(batch)
    except (psycopg.Error, S48RuntimeWriterAuthorityError, ValueError):
        _emit({"code": "S48_RUNTIME_STAGE_UNAVAILABLE", "state": "FAILED"})
        return 2
    receipt = _receipt(batch, code="S48_RUNTIME_STAGED", state="STAGED")
    receipt.update({"inserted": summary.inserted, "replayed": summary.replayed})
    _emit(receipt)
    return 0


def _batch() -> S48RuntimeBatch:
    """Current UTC instant에서 fixed state를 만들어 no-provider runtime invariant를 보존한다."""

    return S48RuntimeMaterializer().materialize(evaluated_at=_now())


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _receipt(
    batch: S48RuntimeBatch,
    *,
    code: str,
    state: str,
) -> dict[str, object]:
    """상태 count만 내보내 raw provider/credential data가 operator output에 섞이지 않게 한다."""

    statuses = tuple(lane.status for lane in batch.lanes)
    return {
        "abstainLaneCount": statuses.count("ABSTAIN"),
        "availableLaneCount": statuses.count("AVAILABLE"),
        "blockedLaneCount": statuses.count("BLOCKED"),
        "code": code,
        "evaluatedAt": batch.evaluated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "laneCount": len(batch.lanes),
        "providerPhysicalCalls": batch.provider_physical_calls,
        "retryCount": batch.retry_count,
        "state": state,
    }


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
