"""S4.8 Core 6/Optional 3 typed runtime을 provider 없이 materialize하는 local operator CLI다."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import psycopg

from app.cross_market.core6_probe import Core6ProbeError, Core6ProbeReceipt
from app.cross_market.s48_runtime import (
    S48DirectProbeProjection,
    S48RuntimeBatch,
    S48RuntimeError,
    S48RuntimeMaterializer,
)
from app.cross_market.s48_runtime_repository import (
    PostgresS48RuntimeRepository,
    S48RuntimeWriterAuthorityError,
)

_WRITER_DSN_ENV: Final[str] = "DECISION_MARKET_WRITER_DATABASE_DSN"
_OFFLINE_TARGET_ENV: Final[str] = "DECISION_SOURCE_WRITER_OFFLINE_TARGET"
_ALLOWED_OFFLINE_TARGETS: Final[frozenset[str]] = frozenset(
    {"local", "offline", "test", "testcontainers"}
)
_CORE6_CONTROL_ROOT_RELATIVE: Final[Path] = Path("capstone-rag/secrets/core6-probes")
_CORE6_RECEIPT_FILE = re.compile(r"^receipt-[0-9a-f]{64}\.json$")


def main(argv: Sequence[str] | None = None) -> int:
    """No-argv-secret CLI로 exact nine S4.8 state를 materialize 또는 local DB stage한다.

    `materialize`와 `stage` 모두 provider transport, raw response, retry를 만들지 않는다. stage는
    explicitly offline target인 function-only market-writer DSN이 있어야 하며 static typed blocker를
    future entitlement/packet 승인으로 해석하지 않는다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    parsed = _parse_command(arguments)
    if parsed is None:
        _emit({"code": "S48_RUNTIME_COMMAND_INVALID", "state": "FAILED"})
        return 2
    command, core6_receipt_names = parsed
    if command == "materialize":
        try:
            batch = _batch(core6_receipt_names=core6_receipt_names)
        except (Core6ProbeError, S48RuntimeError):
            _emit({"code": "S48_RUNTIME_CORE6_RECEIPT_UNAVAILABLE", "state": "FAILED"})
            return 2
        _emit(_receipt(batch, code="S48_RUNTIME_MATERIALIZED", state="MATERIALIZED"))
        return 0
    return _stage(core6_receipt_names=core6_receipt_names)


def _stage(*, core6_receipt_names: tuple[str, ...]) -> int:
    """V50 append function으로 only offline typed state를 replay-safe하게 stage한다."""

    if os.environ.get(_OFFLINE_TARGET_ENV, "").strip().lower() not in _ALLOWED_OFFLINE_TARGETS:
        _emit({"code": "S48_RUNTIME_OFFLINE_TARGET_REQUIRED", "state": "FAILED"})
        return 2
    database_dsn = os.environ.get(_WRITER_DSN_ENV, "").strip()
    if not database_dsn:
        _emit({"code": "S48_RUNTIME_WRITER_DATABASE_DSN", "state": "FAILED"})
        return 2

    try:
        batch = _batch(core6_receipt_names=core6_receipt_names)
    except (Core6ProbeError, S48RuntimeError):
        _emit({"code": "S48_RUNTIME_CORE6_RECEIPT_UNAVAILABLE", "state": "FAILED"})
        return 2
    try:
        summary = PostgresS48RuntimeRepository(database_dsn=database_dsn).append_batch(batch)
    except (psycopg.Error, S48RuntimeWriterAuthorityError, ValueError):
        _emit({"code": "S48_RUNTIME_STAGE_UNAVAILABLE", "state": "FAILED"})
        return 2
    receipt = _receipt(batch, code="S48_RUNTIME_STAGED", state="STAGED")
    receipt.update({"inserted": summary.inserted, "replayed": summary.replayed})
    _emit(receipt)
    return 0


def _batch(*, core6_receipt_names: tuple[str, ...] = ()) -> S48RuntimeBatch:
    """Selected local Core 6 receipts만 read-only로 재사용하고 runtime 자체는 provider handoff를 만들지 않는다."""

    control_root = _repository_root() / _CORE6_CONTROL_ROOT_RELATIVE
    direct_projections = tuple(
        S48DirectProbeProjection.from_core6_receipt(
            Core6ProbeReceipt.load_from_control_root(
                control_root=control_root,
                relative_path=receipt_name,
            )
        )
        for receipt_name in core6_receipt_names
    )
    return S48RuntimeMaterializer().materialize(
        evaluated_at=_now(),
        direct_probe_projections=direct_projections,
    )


def _parse_command(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]] | None:
    """Receipt selector는 exact local receipt filename만 받아 argv로 arbitrary path를 열지 않는다."""

    if not arguments or arguments[0] not in {"materialize", "stage"}:
        return None
    trailing = arguments[1:]
    if len(trailing) % 2 != 0:
        return None
    names: list[str] = []
    for flag, name in zip(trailing[0::2], trailing[1::2], strict=True):
        if flag != "--core6-receipt" or _CORE6_RECEIPT_FILE.fullmatch(name) is None:
            return None
        names.append(name)
    if len(names) > 5 or len(set(names)) != len(names):
        return None
    return arguments[0], tuple(names)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[5]


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
