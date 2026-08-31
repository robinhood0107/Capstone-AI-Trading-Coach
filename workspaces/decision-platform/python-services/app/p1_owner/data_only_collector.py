"""P1 fixture-first 16:10 KST data-only collection coordinator.

The coordinator stages deterministic, provider-free source records at the
16:10 collection boundary.  Promotion remains delegated to the preserved
S5.7C runtime at its pinned next-session 08:10 evidence clock.  No live
provider adapter, credential, account, balance, or order surface exists here.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, time
from importlib.metadata import version
from pathlib import Path
from typing import Mapping, Protocol, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
from app.data.market_data.daily_runtime import (
    DailyMarketDataError,
    DailyReplayPacket,
    DailyRunResult,
    DailyShardSink,
    ReplayEvidenceUnavailable,
    ReplayRecord,
    SealedDirectoryReplay,
    evidence_clock_for_session,
    operation_ids,
    run_offline_daily,
    write_replay_record,
)

_KST = ZoneInfo("Asia/Seoul")
_COLLECTION_TIME = time(16, 10)
_MAX_FILE_BYTES = 2 * 1024 * 1024
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class P1DailyCollectorError(RuntimeError):
    """The schedule, sealed fixture, or collection state is unsafe."""


class FixtureCollectionFailure(P1DailyCollectorError):
    """One deterministic fixture operation failed without retry."""


@dataclass(frozen=True, slots=True)
class DailyCollectorSettings:
    """Default-off schedule and immutable operation caps."""

    enabled: bool = False
    schedule_kst: time = _COLLECTION_TIME
    kis_token_physical_max: int = 1
    kis_daily_physical_max: int = 31
    krx_daily_physical_max: int = 5
    ecos_physical_max: int = 2
    gdelt_physical_max: int = 0
    retry_max: int = 0

    def validate(self) -> None:
        if (
            self.schedule_kst != _COLLECTION_TIME
            or self.kis_token_physical_max != 1
            or self.kis_daily_physical_max != 31
            or self.krx_daily_physical_max != 5
            or self.ecos_physical_max != 2
            or self.gdelt_physical_max != 0
            or self.retry_max != 0
        ):
            raise P1DailyCollectorError("P1 data-only collector settings drifted")


class FixtureCollectionTransportPort(Protocol):
    """Fixture-only record source; production provider transports are out of scope."""

    logical_calls: int
    physical_calls: int

    def collect(self, operation_id: str) -> ReplayRecord: ...


@dataclass(slots=True)
class FixtureDailyCollectionTransport:
    """Deterministic mapping transport with exactly zero physical calls."""

    records: Mapping[str, ReplayRecord]
    failing_operation: str | None = None
    logical_calls: int = 0
    physical_calls: int = 0

    def collect(self, operation_id: str) -> ReplayRecord:
        self.logical_calls += 1
        if operation_id == self.failing_operation:
            raise FixtureCollectionFailure("fixture collection operation failed")
        record = self.records.get(operation_id)
        if record is None:
            raise FixtureCollectionFailure("fixture collection evidence is missing")
        return record


@dataclass(frozen=True, slots=True)
class DailyCollectionResult:
    """One terminal fixture collection result."""

    status: str
    plan_sha256: str
    collection_manifest_sha256: str | None
    logical_calls: int
    physical_calls: int = 0
    buy_candidate_allowed: bool = False


def stage_fixture_daily_collection(
    *,
    packet: DailyReplayPacket,
    observed_at: datetime,
    collection_root: Path,
    transport: FixtureCollectionTransportPort,
    settings: DailyCollectorSettings = DailyCollectorSettings(),
) -> DailyCollectionResult:
    """Stage the exact required fixture set at or after 16:10 on one XKRX session."""

    return _stage_daily_collection(
        packet=packet,
        observed_at=observed_at,
        collection_root=collection_root,
        transport=transport,
        settings=settings,
        provider_authority="FIXTURE_ONLY",
        require_zero_physical_calls=True,
    )


def stage_live_daily_collection(
    *,
    packet: DailyReplayPacket,
    observed_at: datetime,
    collection_root: Path,
    transport: FixtureCollectionTransportPort,
    settings: DailyCollectorSettings,
) -> DailyCollectionResult:
    """Stage one explicitly enabled live read-only set; promotion remains provider-free."""

    return _stage_daily_collection(
        packet=packet,
        observed_at=observed_at,
        collection_root=collection_root,
        transport=transport,
        settings=settings,
        provider_authority="LIVE_READ_ONLY",
        require_zero_physical_calls=False,
    )


def _stage_daily_collection(
    *,
    packet: DailyReplayPacket,
    observed_at: datetime,
    collection_root: Path,
    transport: FixtureCollectionTransportPort,
    settings: DailyCollectorSettings,
    provider_authority: str,
    require_zero_physical_calls: bool,
) -> DailyCollectionResult:
    """Shared manifest-last coordinator with an explicit fixture/live authority bit."""

    settings.validate()
    if provider_authority not in {"FIXTURE_ONLY", "LIVE_READ_ONLY"}:
        raise P1DailyCollectorError("data-only collector provider authority is invalid")
    operations = operation_ids(packet)
    if not settings.enabled:
        inactive_sha = canonical_json_sha256(
            {"packetSha256": packet.packet_sha256, "status": "DISABLED"}
        )
        return DailyCollectionResult("DISABLED", inactive_sha, None, 0)
    schedule_status = _schedule_status(packet, observed_at)
    if schedule_status is not None:
        inactive_sha = canonical_json_sha256(
            {"packetSha256": packet.packet_sha256, "status": schedule_status}
        )
        return DailyCollectionResult(schedule_status, inactive_sha, None, 0)
    plan = _plan(packet, settings, operations, provider_authority=provider_authority)
    plan_sha = canonical_json_sha256(plan)
    root = _private_directory(collection_root)
    session_root = _private_directory(root / packet.session_date.isoformat(), create=True)
    plan_bytes = canonical_json_bytes(plan)
    existing_plan = _read_optional(session_root, "collection-plan.json")
    if existing_plan is not None and existing_plan != plan_bytes:
        return DailyCollectionResult("HALTED", plan_sha, None, 0)
    _write_new_or_same(session_root, "collection-plan.json", plan_bytes)
    existing_manifest = _read_optional(session_root, "complete-manifest.json")
    if existing_manifest is not None:
        manifest = _object(existing_manifest, "collection manifest")
        if not _complete_manifest_valid(
            manifest,
            plan_sha,
            len(operations),
            provider_authority=provider_authority,
        ):
            return DailyCollectionResult("HALTED", plan_sha, None, 0)
        return DailyCollectionResult(
            "NO_OP",
            plan_sha,
            cast(str, manifest["manifestSha256"]),
            0,
        )

    records_root = _private_directory(session_root / "records", create=True)
    replay = SealedDirectoryReplay(records_root)
    calls_before = transport.logical_calls
    receipts: list[dict[str, object]] = []
    for operation_id in operations:
        try:
            record = replay.read(operation_id)
        except ReplayEvidenceUnavailable:
            try:
                record = transport.collect(operation_id)
                _validate_fixture_record(packet, operation_id, record)
                write_replay_record(records_root, record)
                _append_event(
                    session_root,
                    {
                        "contentSha256": record.content_sha256,
                        "operationId": operation_id,
                        "outcome": "SUCCEEDED",
                    },
                )
            except (
                DailyMarketDataError,
                FixtureCollectionFailure,
                P1DailyCollectorError,
                ReplayEvidenceUnavailable,
                ValueError,
            ):
                _append_event(
                    session_root,
                    {"operationId": operation_id, "outcome": "EVIDENCE_GAP"},
                )
                if require_zero_physical_calls and transport.physical_calls != 0:
                    raise P1DailyCollectorError("fixture transport made a physical call")
                return DailyCollectionResult(
                    "EVIDENCE_GAP",
                    plan_sha,
                    None,
                    transport.logical_calls - calls_before,
                    transport.physical_calls,
                )
        try:
            _validate_fixture_record(packet, operation_id, record)
        except ReplayEvidenceUnavailable:
            return DailyCollectionResult(
                "HALTED",
                plan_sha,
                None,
                transport.logical_calls - calls_before,
                transport.physical_calls,
            )
        receipts.append(record.receipt())
    if require_zero_physical_calls and transport.physical_calls != 0:
        raise P1DailyCollectorError("fixture transport made a physical call")
    if canonical_json_sha256(receipts) != packet.expected_receipt_set_sha256:
        _append_event(
            session_root,
            {"operationId": "RECEIPT_SET", "outcome": "NEEDS_HUMAN"},
        )
        return DailyCollectionResult(
            "NEEDS_HUMAN",
            plan_sha,
            None,
            transport.logical_calls - calls_before,
            transport.physical_calls,
        )
    manifest_without_sha: dict[str, object] = {
        "complete": True,
        "contractId": "p1-data-only-collection-manifest.v1",
        "evidenceClock": evidence_clock_for_session(packet.session_date).isoformat(),
        "operationCount": len(operations),
        "planSha256": plan_sha,
        "receipts": receipts,
        "sessionDate": packet.session_date.isoformat(),
    }
    if provider_authority == "FIXTURE_ONLY":
        manifest_without_sha["fixturePhysicalCalls"] = 0
    else:
        manifest_without_sha["providerAuthority"] = provider_authority
        manifest_without_sha["providerPhysicalCalls"] = transport.physical_calls
    manifest_sha = canonical_json_sha256(manifest_without_sha)
    manifest = {**manifest_without_sha, "manifestSha256": manifest_sha}
    manifest_bytes = canonical_json_bytes(manifest)
    _write_new_or_same(session_root, "complete-manifest.json", manifest_bytes)
    return DailyCollectionResult(
        "STAGED_COMPLETE",
        plan_sha,
        manifest_sha,
        transport.logical_calls - calls_before,
        transport.physical_calls,
    )


def promote_staged_daily_collection(
    *,
    packet: DailyReplayPacket,
    collection_root: Path,
    run_root: Path,
    sink: DailyShardSink,
) -> DailyRunResult:
    """Promote only a complete staged set through preserved S5.7C validation/storage."""

    session_root = _private_directory(collection_root / packet.session_date.isoformat())
    manifest = _object(
        _read_required(session_root, "complete-manifest.json"), "collection manifest"
    )
    provider_authority = cast(str, manifest.get("providerAuthority", "FIXTURE_ONLY"))
    plan = _plan(
        packet,
        DailyCollectorSettings(enabled=True),
        operation_ids(packet),
        provider_authority=provider_authority,
    )
    plan_sha = canonical_json_sha256(plan)
    operation_count = len(operation_ids(packet))
    if not _complete_manifest_valid(
        manifest,
        plan_sha,
        operation_count,
        provider_authority=provider_authority,
    ):
        raise P1DailyCollectorError("complete collection manifest binding drifted")
    records_root = _private_directory(session_root / "records")
    result = run_offline_daily(
        packet=packet,
        run_root=run_root,
        replay_factory=lambda: SealedDirectoryReplay(records_root),
        sink=sink,
    )
    if result.provider_physical_calls != 0:
        raise P1DailyCollectorError("staged promotion made a physical provider call")
    return result


def _plan(
    packet: DailyReplayPacket,
    settings: DailyCollectorSettings,
    operations: tuple[str, ...],
    *,
    provider_authority: str = "FIXTURE_ONLY",
) -> dict[str, object]:
    counts = {
        "ECOS": sum(item.startswith("ECOS_DAILY_") for item in operations),
        "KIS_DAILY": sum(item.startswith("KIS_DAILY_") for item in operations),
        "KRX_DAILY": sum(item.startswith("KRX_DAILY_") for item in operations),
    }
    if counts != {"ECOS": 2, "KIS_DAILY": 31, "KRX_DAILY": 5}:
        raise P1DailyCollectorError("normal collection operation derivation drifted")
    plan: dict[str, object] = {
        "contractId": "p1-data-only-collection-plan.v1",
        "defaultEnabled": False,
        "evidenceClock": evidence_clock_for_session(packet.session_date).isoformat(),
        "membership": list(packet.membership),
        "operationCaps": {
            "ECOS": settings.ecos_physical_max,
            "GDELT": settings.gdelt_physical_max,
            "KIS_DAILY": settings.kis_daily_physical_max,
            "KIS_TOKEN": settings.kis_token_physical_max,
            "KRX_DAILY": settings.krx_daily_physical_max,
            "RETRY": settings.retry_max,
        },
        "operations": list(operations),
        "packetSha256": packet.packet_sha256,
        "providerAuthority": provider_authority,
        "scheduleKst": "16:10",
        "sessionDate": packet.session_date.isoformat(),
    }
    if provider_authority == "FIXTURE_ONLY":
        plan["fixturePhysicalCalls"] = 0
    else:
        plan["providerPhysicalCallMax"] = len(operations)
    return plan


def _schedule_status(packet: DailyReplayPacket, observed_at: datetime) -> str | None:
    if observed_at.tzinfo is None:
        raise P1DailyCollectorError("collector observation clock must be timezone aware")
    if version("exchange-calendars") != "4.13.2":
        raise P1DailyCollectorError("collector XKRX calendar version drifted")
    calendar = xcals.get_calendar("XKRX")
    if not bool(calendar.is_session(pd.Timestamp(packet.session_date))):
        return "NO_NEW_SESSION"
    local = observed_at.astimezone(_KST)
    if local.date() != packet.session_date:
        return "NOT_DUE" if local.date() < packet.session_date else "SKIPPED_LATE_START"
    if local.time().replace(tzinfo=None) < _COLLECTION_TIME:
        return "NOT_DUE"
    return None


def _validate_fixture_record(
    packet: DailyReplayPacket,
    operation_id: str,
    record: ReplayRecord,
) -> None:
    if record.operation_id != operation_id or record.source_id != operation_id.split("_", 1)[0]:
        raise ReplayEvidenceUnavailable("fixture record identity mismatch")
    if record.content_sha256 != canonical_json_sha256(record.payload):
        raise ReplayEvidenceUnavailable("fixture record content hash mismatch")
    payload = record.payload
    if operation_id.startswith("KIS_DAILY_"):
        symbol = operation_id.removeprefix("KIS_DAILY_")
        if (
            payload.get("kind") != "BAR"
            or payload.get("symbol") != symbol
            or payload.get("sessionDate") != packet.session_date.isoformat()
        ):
            raise ReplayEvidenceUnavailable("required exact-31 symbol evidence is missing")
    elif operation_id == "KRX_DAILY_01":
        if (
            payload.get("kind") != "TRADING_EVIDENCE"
            or payload.get("sessionDate") != packet.session_date.isoformat()
            or payload.get("nonEmpty") is not True
        ):
            raise ReplayEvidenceUnavailable("KRX trading-session evidence is missing")
    elif operation_id.startswith("ECOS_DAILY_"):
        if payload.get("kind") != "MACRO":
            raise ReplayEvidenceUnavailable("ECOS series evidence is missing")


def _private_directory(path: Path, *, create: bool = False) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise P1DailyCollectorError("collector root must be an absolute clean path")
    _reject_symlink_components(path)
    if create and not path.exists():
        path.mkdir(mode=_DIRECTORY_MODE)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise P1DailyCollectorError("collector directory is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise P1DailyCollectorError("collector directory metadata is unsafe")
    return path.resolve(strict=True)


def _write_new_or_same(directory: Path, filename: str, content: bytes) -> None:
    if not content or len(content) > _MAX_FILE_BYTES:
        raise P1DailyCollectorError("collector file exceeds its byte bound")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            file_fd = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _FILE_MODE,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            if _read_required(directory, filename) != content:
                raise P1DailyCollectorError("immutable collector file conflicts") from None
            return
        with os.fdopen(file_fd, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _append_event(directory: Path, event: dict[str, object]) -> None:
    content = canonical_json_bytes(event)
    path = directory / "collection-journal.jsonl"
    file_fd = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        _FILE_MODE,
    )
    metadata = os.fstat(file_fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        os.close(file_fd)
        raise P1DailyCollectorError("collector journal metadata is unsafe")
    with os.fdopen(file_fd, "ab", closefd=True) as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_optional(directory: Path, filename: str) -> bytes | None:
    try:
        return _read_required(directory, filename)
    except FileNotFoundError:
        return None


def _read_required(directory: Path, filename: str) -> bytes:
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        file_fd = os.open(
            filename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or metadata.st_size <= 0
                or metadata.st_size > _MAX_FILE_BYTES
            ):
                raise P1DailyCollectorError("collector file metadata is unsafe")
            content = os.read(file_fd, metadata.st_size + 1)
            after = os.fstat(file_fd)
            if len(content) != metadata.st_size or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns):
                raise P1DailyCollectorError("collector file changed during read")
            return content
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


def _object(content: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P1DailyCollectorError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise P1DailyCollectorError(f"{label} must be canonical JSON")
    return cast(dict[str, object], value)


def _complete_manifest_valid(
    manifest: dict[str, object],
    plan_sha: str,
    operation_count: int,
    *,
    provider_authority: str,
) -> bool:
    without_sha = {key: value for key, value in manifest.items() if key != "manifestSha256"}
    physical_valid = (
        manifest.get("fixturePhysicalCalls") == 0
        if provider_authority == "FIXTURE_ONLY"
        else isinstance(manifest.get("providerPhysicalCalls"), int)
        and 0 <= cast(int, manifest["providerPhysicalCalls"]) <= operation_count
    )
    return (
        manifest.get("complete") is True
        and manifest.get("planSha256") == plan_sha
        and manifest.get("providerAuthority", "FIXTURE_ONLY") == provider_authority
        and physical_valid
        and manifest.get("operationCount") == operation_count
        and manifest.get("manifestSha256") == canonical_json_sha256(without_sha)
    )


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if os.path.lexists(current) and os.path.islink(current):
            raise P1DailyCollectorError("collector path cannot contain symlinks")
