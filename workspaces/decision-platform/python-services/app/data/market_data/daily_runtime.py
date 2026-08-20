"""S5.7C provider-free, one-session market-data replay runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
import pandas as pd


_KST = ZoneInfo("Asia/Seoul")
_PINNED_CALENDAR_VERSION = "4.13.2"
_CALENDAR_NAME = "XKRX"
_CALENDAR_REVISION = "XKRX-4.13.2+KIS_CTCA0903R"
_FIXED_SYMBOL = "132030"
_MACRO_SERIES = ("722Y001/0101000/D", "731Y001/0000001/D")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_PATTERN = re.compile(r"^[0-9]{6}$")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class DailyMarketDataError(RuntimeError):
    """Offline packet, replay evidence, or durable state is invalid."""


class ReplayEvidenceUnavailable(DailyMarketDataError):
    """A required sealed replay operation is unavailable."""


class ReplayBindingMismatch(DailyMarketDataError):
    """The complete replay receipt set differs from the approved packet."""


@dataclass(frozen=True, slots=True)
class DailyReplayPacket:
    """Closed internal packet; it grants zero physical provider calls."""

    session_date: date
    as_of: datetime
    checked_at: datetime
    previous_session_date: date
    previous_accepted_manifest_sha256: str
    membership_month: str
    membership: tuple[str, ...]
    previous_membership_sha256: str
    month_boundary: bool
    generation: int
    calendar_revision: str
    calendar_attestation_sha256: str
    expected_receipt_set_sha256: str
    supersedes_sha256: str | None = None
    provider_authority: str = "OFFLINE_REPLAY_ONLY"
    provider_physical_call_cap: int = 0
    account_call_cap: int = 0
    balance_call_cap: int = 0
    order_call_cap: int = 0

    @property
    def packet_sha256(self) -> str:
        """Return the deterministic packet identity."""

        return canonical_json_sha256(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        """Return the canonical packet document."""

        payload: dict[str, object] = {
            "accountCallCap": self.account_call_cap,
            "asOf": _iso(self.as_of),
            "balanceCallCap": self.balance_call_cap,
            "calendar": {
                "attestationSha256": self.calendar_attestation_sha256,
                "name": _CALENDAR_NAME,
                "revision": self.calendar_revision,
                "version": _PINNED_CALENDAR_VERSION,
            },
            "checkedAt": _iso(self.checked_at),
            "contractId": "market-data-offline-replay-packet.v1",
            "generation": self.generation,
            "expectedReceiptSetSha256": self.expected_receipt_set_sha256,
            "membership": list(self.membership),
            "membershipMonth": self.membership_month,
            "monthBoundary": self.month_boundary,
            "orderCallCap": self.order_call_cap,
            "previousAcceptedManifestSha256": self.previous_accepted_manifest_sha256,
            "previousMembershipSha256": self.previous_membership_sha256,
            "previousSessionDate": self.previous_session_date.isoformat(),
            "providerAuthority": self.provider_authority,
            "providerPhysicalCallCap": self.provider_physical_call_cap,
            "sessionDate": self.session_date.isoformat(),
        }
        if self.supersedes_sha256 is not None:
            payload["supersedesSha256"] = self.supersedes_sha256
        return payload


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """One content-addressed, provider-free replay result."""

    source_id: str
    operation_id: str
    query_sha256: str
    content_sha256: str
    retrieved_at: datetime
    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return the complete sealed record."""

        return {
            "contentSha256": self.content_sha256,
            "operationId": self.operation_id,
            "payload": dict(self.payload),
            "querySha256": self.query_sha256,
            "retrievedAt": _iso(self.retrieved_at),
            "sourceId": self.source_id,
        }

    def receipt(self) -> dict[str, object]:
        """Return the bounded content-free source receipt."""

        return {
            "contentSha256": self.content_sha256,
            "operationId": self.operation_id,
            "querySha256": self.query_sha256,
            "retrievedAt": _iso(self.retrieved_at),
            "sourceId": self.source_id,
        }


class OfflineReplayPort(Protocol):
    """A sealed local evidence source; implementations may not access providers."""

    def read(self, operation_id: str) -> ReplayRecord: ...


class DailyShardSink(Protocol):
    """Transactional destination for an accepted daily shard."""

    def preflight(self) -> None: ...

    def adopt(self, accepted: "AcceptedDailyShard") -> str: ...


@dataclass(frozen=True, slots=True)
class AcceptedDailyShard:
    """Complete daily contract plus optional month-boundary universe details."""

    payload: Mapping[str, object]
    universe_rows: tuple[Mapping[str, object], ...]

    @property
    def manifest_sha256(self) -> str:
        return cast(str, self.payload["manifestSha256"])


@dataclass(frozen=True, slots=True)
class DailyRunResult:
    """Terminal result of one offline manual invocation."""

    status: str
    health: Mapping[str, object]
    accepted: AcceptedDailyShard | None
    replay_reads: int
    provider_physical_calls: int = 0


class SealedDirectoryReplay:
    """Read canonical replay records from an owner-private directory."""

    def __init__(self, root: Path) -> None:
        self._root = _absolute_directory(root)
        self.read_count = 0

    def read(self, operation_id: str) -> ReplayRecord:
        if not re.fullmatch(r"[A-Z0-9_./-]{3,80}", operation_id):
            raise ReplayEvidenceUnavailable("invalid replay operation id")
        filename = hashlib.sha256(operation_id.encode("utf-8")).hexdigest() + ".json"
        content = _read_regular_file(self._root, filename)
        self.read_count += 1
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReplayEvidenceUnavailable("sealed replay record is not canonical JSON") from error
        if not isinstance(value, dict) or canonical_json_bytes(value) != content:
            raise ReplayEvidenceUnavailable("sealed replay record is not canonical JSON")
        record = _record(value)
        if record.operation_id != operation_id:
            raise ReplayEvidenceUnavailable("sealed replay operation identity mismatch")
        return record


def normal_operation_ids(membership: Sequence[str]) -> tuple[str, ...]:
    """Derive the exact 38-operation normal-session plan."""

    _validate_membership(tuple(membership))
    krx = tuple(f"KRX_DAILY_{index:02d}" for index in range(1, 6))
    kis = tuple(f"KIS_DAILY_{symbol}" for symbol in membership)
    ecos = tuple(f"ECOS_DAILY_{series}" for series in _MACRO_SERIES)
    operations = krx + kis + ecos
    if len(operations) != 38:
        raise DailyMarketDataError("normal operation derivation drifted")
    return operations


def operation_ids(packet: DailyReplayPacket) -> tuple[str, ...]:
    """Derive exact 38 normal or 41 month-boundary replay operations."""

    normal = normal_operation_ids(packet.membership)
    if not packet.month_boundary:
        return normal
    operations = normal[:5] + tuple(f"KRX_MONTHLY_{index:02d}" for index in range(1, 4)) + normal[5:]
    if len(operations) != 41:
        raise DailyMarketDataError("month-boundary operation derivation drifted")
    return operations


def evidence_clock_for_session(session_date: date) -> datetime:
    """Return the next pinned XKRX session 08:10 KST evidence clock."""

    calendar = _calendar()
    label = calendar.date_to_session(pd.Timestamp(session_date), direction="none")
    next_label = calendar.next_session(label)
    return datetime.combine(next_label.date(), time(8, 10), tzinfo=_KST)


def run_offline_daily(
    *,
    packet: DailyReplayPacket,
    run_root: Path,
    replay_factory: Callable[[], OfflineReplayPort],
    sink: DailyShardSink,
) -> DailyRunResult:
    """Replay one complete session with provider calls fixed at zero."""

    _validate_packet(packet)
    sink.preflight()
    clock_status = _clock_status(packet)
    if clock_status is not None:
        health = _health(packet=packet, status=clock_status, details=(clock_status,))
        _publish_health(run_root=run_root, packet=packet, health=health)
        return DailyRunResult(clock_status, health, None, 0)

    run = _prepare_run_root(run_root, packet.packet_sha256)
    replay = replay_factory()
    records: list[ReplayRecord] = []
    reads = 0
    for operation_id in operation_ids(packet):
        staged = _read_staged(run, operation_id)
        if staged is not None:
            records.append(staged)
            record = staged
        else:
            try:
                record = replay.read(operation_id)
                reads += 1
                _validate_record_for_operation(record, operation_id)
                _stage_record(run, record)
                records.append(record)
            except ReplayEvidenceUnavailable:
                status = "EVIDENCE_GAP" if operation_id.startswith("KIS_DAILY_") else "PARTIAL"
                health = _health(packet=packet, status=status, details=("REPLAY_EVIDENCE_MISSING",))
                _write_replace(run, "health.json", canonical_json_bytes(health))
                return DailyRunResult(status, health, None, reads)

        if operation_id == "KRX_DAILY_01" and not _trading_evidence_present(record, packet):
            health = _health(
                packet=packet,
                status="CALENDAR_DIVERGENCE_SUSPECTED",
                details=("EMPTY_KRX_DAILY_PROJECTION",),
            )
            _write_replace(run, "health.json", canonical_json_bytes(health))
            return DailyRunResult("CALENDAR_DIVERGENCE_SUSPECTED", health, None, reads)

    try:
        accepted = _build_accepted(packet, records)
    except ReplayBindingMismatch:
        health = _health(packet=packet, status="NEEDS_HUMAN", details=("PACKET_BINDING_MISMATCH",))
        _write_replace(run, "health.json", canonical_json_bytes(health))
        return DailyRunResult("NEEDS_HUMAN", health, None, reads)
    except DailyMarketDataError:
        health = _health(packet=packet, status="EVIDENCE_GAP", details=("EVIDENCE_SET_INVALID",))
        _write_replace(run, "health.json", canonical_json_bytes(health))
        return DailyRunResult("EVIDENCE_GAP", health, None, reads)
    outcome = sink.adopt(accepted)
    _publish_manifest_last(run, accepted)
    health = _health(
        packet=packet,
        status="ACCEPTED",
        details=("OFFLINE_REPLAY_ONLY", outcome),
        accepted=accepted,
    )
    _write_replace(run, "health.json", canonical_json_bytes(health))
    return DailyRunResult("ACCEPTED", health, accepted, reads)


def load_packet(path: Path) -> DailyReplayPacket:
    """Load and validate a canonical offline packet."""

    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DailyMarketDataError("offline replay packet must be a regular non-symlink file")
    content = path.read_bytes()
    if len(content) > 1_000_000:
        raise DailyMarketDataError("offline replay packet exceeds size limit")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DailyMarketDataError("offline replay packet is not valid JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise DailyMarketDataError("offline replay packet must be canonical JSON")
    calendar = value.get("calendar")
    if not isinstance(calendar, dict):
        raise DailyMarketDataError("offline replay packet calendar is missing")
    return DailyReplayPacket(
        session_date=date.fromisoformat(cast(str, value["sessionDate"])),
        as_of=_datetime(cast(str, value["asOf"])),
        checked_at=_datetime(cast(str, value["checkedAt"])),
        previous_session_date=date.fromisoformat(cast(str, value["previousSessionDate"])),
        previous_accepted_manifest_sha256=cast(str, value["previousAcceptedManifestSha256"]),
        membership_month=cast(str, value["membershipMonth"]),
        membership=tuple(cast(list[str], value["membership"])),
        previous_membership_sha256=cast(str, value["previousMembershipSha256"]),
        month_boundary=cast(bool, value["monthBoundary"]),
        generation=cast(int, value["generation"]),
        calendar_revision=cast(str, calendar["revision"]),
        calendar_attestation_sha256=cast(str, calendar["attestationSha256"]),
        expected_receipt_set_sha256=cast(str, value["expectedReceiptSetSha256"]),
        supersedes_sha256=cast(str | None, value.get("supersedesSha256")),
        provider_authority=cast(str, value["providerAuthority"]),
        provider_physical_call_cap=cast(int, value["providerPhysicalCallCap"]),
        account_call_cap=cast(int, value["accountCallCap"]),
        balance_call_cap=cast(int, value["balanceCallCap"]),
        order_call_cap=cast(int, value["orderCallCap"]),
    )


def write_replay_record(root: Path, record: ReplayRecord) -> None:
    """Seal one offline replay record for tests and manual fixture preparation."""

    _validate_record_for_operation(record, record.operation_id)
    directory = _ensure_private_directory(root)
    filename = hashlib.sha256(record.operation_id.encode("utf-8")).hexdigest() + ".json"
    _write_no_replace(directory, filename, canonical_json_bytes(record.to_dict()))


def _validate_packet(packet: DailyReplayPacket) -> None:
    _validate_membership(packet.membership)
    if packet.provider_authority != "OFFLINE_REPLAY_ONLY":
        raise DailyMarketDataError("live provider authority is not implemented")
    if any(
        value != 0
        for value in (
            packet.provider_physical_call_cap,
            packet.account_call_cap,
            packet.balance_call_cap,
            packet.order_call_cap,
        )
    ):
        raise DailyMarketDataError("offline packet call caps must all be zero")
    if packet.calendar_revision != _CALENDAR_REVISION:
        raise DailyMarketDataError("calendar correction revision drifted")
    expected_attestation = hashlib.sha256(packet.calendar_revision.encode("ascii")).hexdigest()
    if packet.calendar_attestation_sha256 != expected_attestation:
        raise DailyMarketDataError("calendar attestation hash mismatch")
    if packet.membership_month != packet.session_date.strftime("%Y-%m"):
        raise DailyMarketDataError("membership month does not match session")
    expected_boundary = packet.previous_session_date.strftime("%Y-%m") != packet.membership_month
    if packet.month_boundary != expected_boundary:
        raise DailyMarketDataError("month-boundary flag does not match adjacent sessions")
    if packet.as_of.tzinfo is None or packet.checked_at.tzinfo is None:
        raise DailyMarketDataError("packet timestamps must be timezone aware")
    _sha(packet.previous_accepted_manifest_sha256, "previous accepted manifest")
    _sha(packet.expected_receipt_set_sha256, "expected receipt set")
    _sha(packet.previous_membership_sha256, "previous membership")
    current_membership_sha256 = canonical_json_sha256(list(packet.membership))
    if not packet.month_boundary and packet.previous_membership_sha256 != current_membership_sha256:
        raise DailyMarketDataError("mid-month membership must remain frozen")
    if packet.generation < 1:
        raise DailyMarketDataError("generation must be positive")
    if packet.generation == 1 and packet.supersedes_sha256 is not None:
        raise DailyMarketDataError("generation one cannot supersede a manifest")
    if packet.generation > 1:
        _sha(packet.supersedes_sha256, "supersedes manifest")
    _calendar()


def _clock_status(packet: DailyReplayPacket) -> str | None:
    calendar = _calendar()
    if not bool(calendar.is_session(pd.Timestamp(packet.session_date))):
        return "NO_NEW_SESSION"
    if packet.as_of != evidence_clock_for_session(packet.session_date):
        raise DailyMarketDataError("packet asOf must equal the next-session evidence clock")
    previous = calendar.date_to_session(pd.Timestamp(packet.previous_session_date), direction="none")
    expected = calendar.next_session(previous).date()
    if packet.session_date != expected:
        return "NO_NEW_SESSION"
    if packet.checked_at.astimezone(_KST) < evidence_clock_for_session(packet.session_date):
        return "WAITING_FOR_EVIDENCE_CLOCK"
    return None


def _calendar() -> Any:
    if version("exchange-calendars") != _PINNED_CALENDAR_VERSION:
        raise DailyMarketDataError("exchange-calendars version drifted from approved pin")
    return xcals.get_calendar(_CALENDAR_NAME)


def _validate_membership(membership: tuple[str, ...]) -> None:
    if len(membership) != 31 or len(set(membership)) != 31:
        raise DailyMarketDataError("membership must contain exact 31 unique symbols")
    if membership[-1] != _FIXED_SYMBOL or membership.count(_FIXED_SYMBOL) != 1:
        raise DailyMarketDataError("membership must end with fixed member 132030")
    if any(_SYMBOL_PATTERN.fullmatch(symbol) is None for symbol in membership):
        raise DailyMarketDataError("membership symbol is invalid")


def _validate_record_for_operation(record: ReplayRecord, operation_id: str) -> None:
    expected_source = operation_id.split("_", maxsplit=1)[0]
    if record.source_id != expected_source or record.operation_id != operation_id:
        raise ReplayEvidenceUnavailable("replay record identity mismatch")
    _sha(record.query_sha256, "query")
    expected_content = canonical_json_sha256(record.payload)
    if record.content_sha256 != expected_content:
        raise ReplayEvidenceUnavailable("replay content hash mismatch")
    if record.retrieved_at.tzinfo is None:
        raise ReplayEvidenceUnavailable("replay timestamp must be timezone aware")


def _trading_evidence_present(record: ReplayRecord, packet: DailyReplayPacket) -> bool:
    return (
        record.payload.get("kind") == "TRADING_EVIDENCE"
        and record.payload.get("sessionDate") == packet.session_date.isoformat()
        and record.payload.get("nonEmpty") is True
    )


def _build_accepted(
    packet: DailyReplayPacket, records: Sequence[ReplayRecord]
) -> AcceptedDailyShard:
    by_operation = {record.operation_id: record for record in records}
    bars = [_bar(by_operation[f"KIS_DAILY_{symbol}"], symbol, packet) for symbol in packet.membership]
    indices = [_index(records, index_id, packet) for index_id in ("KOSPI", "KOSDAQ")]
    macro = [_macro(by_operation[f"ECOS_DAILY_{series}"], series) for series in _MACRO_SERIES]
    universe_rows = _universe_rows(records, packet)
    receipts = [record.receipt() for record in records]
    if canonical_json_sha256(receipts) != packet.expected_receipt_set_sha256:
        raise ReplayBindingMismatch("sealed replay receipt set does not match packet binding")
    membership_sha = canonical_json_sha256(list(packet.membership))
    without_manifest: dict[str, object] = {
        "accepted": True,
        "asOf": _iso(packet.as_of),
        "bars": bars,
        "calendar": {
            "attestationSha256": packet.calendar_attestation_sha256,
            "name": _CALENDAR_NAME,
            "revision": packet.calendar_revision,
            "version": _PINNED_CALENDAR_VERSION,
        },
        "contractId": "market-data-daily-shard.v1",
        "decisionAuthority": "NONE",
        "forwardFillUsed": False,
        "generation": packet.generation,
        "indices": indices,
        "macro": macro,
        "membership": list(packet.membership),
        "membershipMonth": packet.membership_month,
        "membershipSha256": membership_sha,
        "previousAcceptedManifestSha256": packet.previous_accepted_manifest_sha256,
        "providerCallsOnRead": 0,
        "providerPhysicalCalls": 0,
        "sessionDate": packet.session_date.isoformat(),
        "sourceReceipts": receipts,
    }
    if packet.supersedes_sha256 is not None:
        without_manifest["supersedesSha256"] = packet.supersedes_sha256
    manifest_sha = canonical_json_sha256(without_manifest)
    payload = {**without_manifest, "manifestSha256": manifest_sha}
    return AcceptedDailyShard(payload=payload, universe_rows=universe_rows)


def _bar(record: ReplayRecord, symbol: str, packet: DailyReplayPacket) -> dict[str, object]:
    value = record.payload
    if value.get("kind") != "BAR" or value.get("symbol") != symbol:
        raise DailyMarketDataError(f"KIS replay bar is missing for {symbol}")
    if value.get("sessionDate") != packet.session_date.isoformat():
        raise DailyMarketDataError("KIS replay bar session mismatch")
    row: dict[str, object] = {
        "close": value.get("close"),
        "currency": "KRW",
        "high": value.get("high"),
        "low": value.get("low"),
        "open": value.get("open"),
        "sessionDate": packet.session_date.isoformat(),
        "sourceReceiptSha256": record.content_sha256,
        "symbol": symbol,
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "volume": value.get("volume"),
    }
    _validate_ohlcv(row)
    return row


def _index(
    records: Sequence[ReplayRecord], index_id: str, packet: DailyReplayPacket
) -> dict[str, object]:
    matches = [
        record
        for record in records
        if record.payload.get("kind") == "INDEX"
        and record.payload.get("indexId") == index_id
    ]
    if len(matches) != 1:
        raise DailyMarketDataError(f"exactly one {index_id} index is required")
    record = matches[0]
    if record.payload.get("sessionDate") != packet.session_date.isoformat():
        raise DailyMarketDataError("index session mismatch")
    close = record.payload.get("close")
    if not isinstance(close, int | float) or isinstance(close, bool) or close <= 0:
        raise DailyMarketDataError("index close must be positive")
    return {
        "close": close,
        "indexId": index_id,
        "sessionDate": packet.session_date.isoformat(),
        "sourceReceiptSha256": record.content_sha256,
        "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
    }


def _macro(record: ReplayRecord, series: str) -> dict[str, object]:
    value = record.payload
    if value.get("kind") != "MACRO" or value.get("seriesId") != series:
        raise DailyMarketDataError("ECOS replay series identity mismatch")
    observation_date = cast(str, value.get("observationDate"))
    available_at = cast(str, value.get("availableAt"))
    date.fromisoformat(observation_date)
    _datetime(available_at)
    numeric = value.get("value")
    if not isinstance(numeric, int | float) or isinstance(numeric, bool):
        raise DailyMarketDataError("ECOS replay value must be numeric")
    return {
        "availableAt": available_at,
        "observationDate": observation_date,
        "seriesId": series,
        "sourceReceiptSha256": record.content_sha256,
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "value": numeric,
    }


def _universe_rows(
    records: Sequence[ReplayRecord], packet: DailyReplayPacket
) -> tuple[Mapping[str, object], ...]:
    if not packet.month_boundary:
        if any(record.payload.get("kind") == "UNIVERSE" for record in records):
            raise DailyMarketDataError("mid-month replay cannot change membership")
        return ()
    matches = [record for record in records if record.payload.get("kind") == "UNIVERSE"]
    if len(matches) != 1:
        raise DailyMarketDataError("month boundary requires one sealed universe projection")
    record = matches[0]
    members = record.payload.get("members")
    if not isinstance(members, list) or len(members) != 31:
        raise DailyMarketDataError("month boundary universe must contain exact 31 rows")
    rows: list[Mapping[str, object]] = []
    for rank, member in enumerate(members, start=1):
        if not isinstance(member, dict) or member.get("symbol") != packet.membership[rank - 1]:
            raise DailyMarketDataError("month boundary universe order mismatches packet membership")
        market = member.get("market")
        if market not in ("KOSPI", "KOSDAQ"):
            raise DailyMarketDataError("universe market is invalid")
        fixed = rank == 31
        if fixed != (member.get("symbol") == _FIXED_SYMBOL):
            raise DailyMarketDataError("universe fixed member invariant failed")
        instrument_id = "XKRX:ETF:132030" if fixed else member.get("instrumentId")
        if not isinstance(instrument_id, str):
            raise DailyMarketDataError("universe instrument identity is missing")
        rows.append(
            {
                "effectiveFromSession": packet.session_date.isoformat(),
                "instrumentId": instrument_id,
                "isFixedMember": fixed,
                "market": market,
                "membershipMonth": packet.membership_month,
                "rank": rank,
                "selectionSession": packet.previous_session_date.isoformat(),
                "sourceReceiptSha256": record.content_sha256,
                "symbol": member["symbol"],
                "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
            }
        )
    return tuple(rows)


def _validate_ohlcv(row: Mapping[str, object]) -> None:
    prices = [row.get(field) for field in ("open", "high", "low", "close")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in prices):
        raise DailyMarketDataError("OHLC values must be positive integers")
    opened, high, low, close = cast(tuple[int, int, int, int], tuple(prices))
    if high < max(opened, close) or low > min(opened, close):
        raise DailyMarketDataError("OHLC range is invalid")
    volume = row.get("volume")
    if not isinstance(volume, int) or isinstance(volume, bool) or volume < 0:
        raise DailyMarketDataError("volume must be a non-negative integer")


def _health(
    *,
    packet: DailyReplayPacket,
    status: str,
    details: tuple[str, ...],
    accepted: AcceptedDailyShard | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "checkedAt": _iso(packet.checked_at),
        "collectorInvocationAllowed": False,
        "contractId": "market-data-health.v1",
        "details": list(details),
        "expectedSessionDate": packet.session_date.isoformat(),
        "providerPhysicalCalls": 0,
        "retryAllowed": False,
        "status": status,
    }
    if accepted is not None:
        payload["lastAcceptedAsOf"] = cast(str, accepted.payload["asOf"])
        payload["lastAcceptedManifestSha256"] = accepted.manifest_sha256
    return payload


def _record(value: Mapping[str, object]) -> ReplayRecord:
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ReplayEvidenceUnavailable("sealed replay payload is invalid")
    return ReplayRecord(
        source_id=cast(str, value.get("sourceId")),
        operation_id=cast(str, value.get("operationId")),
        query_sha256=cast(str, value.get("querySha256")),
        content_sha256=cast(str, value.get("contentSha256")),
        retrieved_at=_datetime(cast(str, value.get("retrievedAt"))),
        payload=payload,
    )


def _prepare_run_root(root: Path, packet_sha256: str) -> Path:
    base = _ensure_private_directory(root)
    run = base / packet_sha256
    if run.exists():
        return _absolute_directory(run)
    run.mkdir(mode=0o700)
    staging = run / "staging"
    staging.mkdir(mode=0o700)
    _fsync_directory(run)
    _fsync_directory(base)
    return run.resolve(strict=True)


def _stage_record(run: Path, record: ReplayRecord) -> None:
    filename = hashlib.sha256(record.operation_id.encode("utf-8")).hexdigest() + ".json"
    _write_no_replace(run / "staging", filename, canonical_json_bytes(record.to_dict()))
    event = canonical_json_bytes(
        {"contentSha256": record.content_sha256, "operationId": record.operation_id, "outcome": "SUCCEEDED"}
    )
    journal = run / "progress.jsonl"
    fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "ab", closefd=True) as output:
            output.write(event)
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        pass
    _fsync_directory(run)


def _read_staged(run: Path, operation_id: str) -> ReplayRecord | None:
    filename = hashlib.sha256(operation_id.encode("utf-8")).hexdigest() + ".json"
    path = run / "staging" / filename
    if not path.exists():
        return None
    value = json.loads(_read_regular_file(run / "staging", filename))
    if not isinstance(value, dict):
        raise DailyMarketDataError("staged replay record is invalid")
    record = _record(value)
    _validate_record_for_operation(record, operation_id)
    return record


def _publish_manifest_last(run: Path, accepted: AcceptedDailyShard) -> None:
    content = canonical_json_bytes(accepted.payload)
    path = run / "daily-shard.json"
    if path.exists():
        if _read_regular_file(run, "daily-shard.json") != content:
            raise DailyMarketDataError("accepted daily manifest conflicts with existing bytes")
        return
    _write_no_replace(run, "daily-shard.json", content)


def _publish_health(
    *, run_root: Path, packet: DailyReplayPacket, health: Mapping[str, object]
) -> None:
    run = _prepare_run_root(run_root, packet.packet_sha256)
    _write_replace(run, "health.json", canonical_json_bytes(health))


def _write_no_replace(directory: Path, filename: str, content: bytes) -> None:
    directory = _absolute_directory(directory)
    directory_fd = os.open(directory, _DIRECTORY_FLAGS)
    try:
        file_fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(file_fd, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(directory_fd)
    except FileExistsError:
        existing = _read_regular_file(directory, filename)
        if existing != content:
            raise DailyMarketDataError("immutable file already exists with different bytes") from None
    finally:
        os.close(directory_fd)


def _write_replace(directory: Path, filename: str, content: bytes) -> None:
    directory = _absolute_directory(directory)
    temporary = f".{filename}.{hashlib.sha256(content).hexdigest()}.tmp"
    path = directory / temporary
    _write_no_replace(directory, temporary, content)
    os.replace(path, directory / filename)
    _fsync_directory(directory)


def _read_regular_file(directory: Path, filename: str) -> bytes:
    directory = _absolute_directory(directory)
    directory_fd = os.open(directory, _DIRECTORY_FLAGS)
    try:
        file_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        with os.fdopen(file_fd, "rb", closefd=True) as source:
            content = source.read(2_000_001)
    except FileNotFoundError as error:
        raise ReplayEvidenceUnavailable(f"sealed replay operation is missing: {filename}") from error
    finally:
        os.close(directory_fd)
    if len(content) > 2_000_000:
        raise ReplayEvidenceUnavailable("sealed replay record exceeds size limit")
    return content


def _ensure_private_directory(path: Path) -> Path:
    absolute = path.absolute()
    if not absolute.exists():
        parent = _absolute_directory(absolute.parent)
        absolute = parent / absolute.name
        absolute.mkdir(mode=0o700)
    return _absolute_directory(absolute)


def _absolute_directory(path: Path) -> Path:
    absolute = path.absolute()
    _reject_symlink_components(absolute)
    stat = os.lstat(absolute)
    if not absolute.is_dir() or os.path.islink(absolute):
        raise DailyMarketDataError("market-data path must be a non-symlink directory")
    if stat.st_mode & 0o077:
        raise DailyMarketDataError("market-data directory must be owner-private")
    return absolute.resolve(strict=True)


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if not os.path.lexists(current):
            continue
        if os.path.islink(current):
            raise DailyMarketDataError("market-data path cannot contain symlinks")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, _DIRECTORY_FLAGS)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DailyMarketDataError("timestamp must be timezone aware")
    return parsed


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_PATTERN.fullmatch(value) is None:
        raise DailyMarketDataError(f"{field} sha256 is invalid")
    return value
