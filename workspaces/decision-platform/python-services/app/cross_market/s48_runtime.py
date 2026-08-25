"""Pre-S5 S4.8 Core 6 + Optional 3의 fixture-first sanitized runtime boundary다.

이 모듈은 provider transport, credential, query, raw response를 갖지 않는다. Core 6과
Optional 3의 exact nine lanes를 deterministic typed blocker 또는 existing authorized
projection hash로만 materialize하고, Risk/Signal/Decision/order 권한을 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.cross_market.core6_probe import Core6ProbeReceipt

CORE6_LANES: Final[tuple[tuple[str, str], ...]] = (
    ("KIS", "S48_CORE6_KIS"),
    ("OPENDART", "S48_CORE6_OPENDART"),
    ("SEC_EDGAR", "S48_CORE6_SEC_EDGAR"),
    ("KRX", "S48_CORE6_KRX"),
    ("KOFIA", "S48_CORE6_KOFIA"),
    ("ECOS", "S48_CORE6_ECOS"),
)
OPTIONAL3_LANES: Final[tuple[tuple[str, str], ...]] = (
    ("FINNHUB_OPTIONAL3", "S48_OPTIONAL3_FINNHUB"),
    ("TWELVE_DATA", "S48_OPTIONAL3_TWELVE_DATA"),
    ("MASSIVE", "S48_OPTIONAL3_MASSIVE"),
)
S48_RUNTIME_LANES: Final[tuple[tuple[str, str], ...]] = CORE6_LANES + OPTIONAL3_LANES
_SOURCE_ID_BY_FAMILY: Final[dict[str, str]] = dict(S48_RUNTIME_LANES)
_DIRECT_READ_FAMILIES: Final[frozenset[str]] = frozenset(
    {"KIS", "SEC_EDGAR", "KRX", "KOFIA", "FINNHUB_OPTIONAL3", "TWELVE_DATA", "MASSIVE"}
)
_PROJECTION_ONLY_FAMILIES: Final[frozenset[str]] = frozenset({"OPENDART", "ECOS"})
_CORE6_RECEIPT_OPERATIONS_BY_FAMILY: Final[dict[str, frozenset[str]]] = {
    "KIS": frozenset({"KIS_CURRENT_PRICE"}),
    "SEC_EDGAR": frozenset({"SEC_EDGAR_SUBMISSIONS", "SEC_EDGAR_COMPANYFACTS"}),
    "KRX": frozenset({"KRX_KOSPI_DAILY", "KRX_KOSDAQ_DAILY"}),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class S48RuntimeError(ValueError):
    """S4.8 runtime lane이 fixture-first/no-authority boundary를 벗어났음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class S48AuthorizedProjection:
    """OpenDART/ECOS의 기존 authorized projection을 content 없이 참조하는 hash proof다."""

    source_family: str
    projection_hash: str

    def __post_init__(self) -> None:
        if self.source_family not in _PROJECTION_ONLY_FAMILIES:
            raise S48RuntimeError("S48_DIRECT_PROJECTION_REUSE_FORBIDDEN")
        if _SHA256.fullmatch(self.projection_hash) is None:
            raise S48RuntimeError("S48_RUNTIME_PROJECTION_HASH_INVALID")


@dataclass(frozen=True, slots=True)
class S48DirectProbeProjection:
    """Successful Core 6 receipt의 content-free projection proof를 runtime에 전달하는 typed bridge다."""

    source_family: str
    source_id: str
    operation: str
    projection_hash: str

    def __post_init__(self) -> None:
        required_operations = _CORE6_RECEIPT_OPERATIONS_BY_FAMILY.get(self.source_family)
        if required_operations is None or self.operation not in required_operations:
            raise S48RuntimeError("S48_RUNTIME_DIRECT_RECEIPT_OPERATION_INVALID")
        if _SOURCE_ID_BY_FAMILY.get(self.source_family) != self.source_id:
            raise S48RuntimeError("S48_RUNTIME_DIRECT_RECEIPT_SOURCE_INVALID")
        if _SHA256.fullmatch(self.projection_hash) is None:
            raise S48RuntimeError("S48_RUNTIME_PROJECTION_HASH_INVALID")

    @classmethod
    def from_core6_receipt(cls, receipt: Core6ProbeReceipt) -> S48DirectProbeProjection:
        """Only a completed one-call Core 6 success receipt can make a direct lane available."""

        if (
            receipt.outcome != "SUCCESS"
            or receipt.logical_call_count != 1
            or receipt.physical_call_count != 1
            or receipt.provider_status_class != "HTTP_2XX"
            or receipt.projection_hash is None
        ):
            raise S48RuntimeError("S48_RUNTIME_DIRECT_RECEIPT_NOT_SUCCESSFUL")
        return cls(
            source_family=receipt.provider_family,
            source_id=receipt.source_id,
            operation=receipt.operation,
            projection_hash=receipt.projection_hash,
        )


@dataclass(frozen=True, slots=True)
class S48RuntimeLane:
    """one source family의 minimal runtime state다. provider content는 hash로도 이 object에 넣지 않는다."""

    source_family: str
    source_id: str
    evaluated_at: datetime
    ingestion_mode: str
    status: str
    reason: str
    projection_hash: str | None

    def __post_init__(self) -> None:
        if _SOURCE_ID_BY_FAMILY.get(self.source_family) != self.source_id:
            raise S48RuntimeError("S48_RUNTIME_SOURCE_ID_INVALID")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise S48RuntimeError("S48_RUNTIME_EVALUATED_AT_INVALID")
        if self.ingestion_mode not in {"DIRECT_READ_PROBE", "REUSE_AUTHORIZED_PROJECTION"}:
            raise S48RuntimeError("S48_RUNTIME_INGESTION_MODE_INVALID")
        if self.status not in {"AVAILABLE", "ABSTAIN", "BLOCKED"}:
            raise S48RuntimeError("S48_RUNTIME_STATUS_INVALID")
        if self.projection_hash is not None and _SHA256.fullmatch(self.projection_hash) is None:
            raise S48RuntimeError("S48_RUNTIME_PROJECTION_HASH_INVALID")
        self._validate_state()

    def _validate_state(self) -> None:
        if self.source_family in _PROJECTION_ONLY_FAMILIES:
            expected_mode = "REUSE_AUTHORIZED_PROJECTION"
            if self.ingestion_mode != expected_mode:
                raise S48RuntimeError("S48_RUNTIME_PROJECTION_MODE_INVALID")
            if self.status == "AVAILABLE":
                if self.reason != "AUTHORIZED_PROJECTION_AVAILABLE" or self.projection_hash is None:
                    raise S48RuntimeError("S48_RUNTIME_PROJECTION_STATE_INVALID")
                return
            if (
                self.status != "ABSTAIN"
                or self.reason != "REUSE_AUTHORIZED_PROJECTION_NOT_AVAILABLE"
                or self.projection_hash is not None
            ):
                raise S48RuntimeError("S48_RUNTIME_PROJECTION_STATE_INVALID")
            return
        if self.source_family in _CORE6_RECEIPT_OPERATIONS_BY_FAMILY:
            if self.ingestion_mode != "DIRECT_READ_PROBE":
                raise S48RuntimeError("S48_RUNTIME_DIRECT_READ_BLOCKER_INVALID")
            if self.status == "AVAILABLE":
                if (
                    self.reason != "COMPLETE_DIRECT_PROBE_SET_AVAILABLE"
                    or self.projection_hash is None
                ):
                    raise S48RuntimeError("S48_RUNTIME_DIRECT_RECEIPT_STATE_INVALID")
                return
            if (
                self.status != "ABSTAIN"
                or self.reason
                not in {"APPROVAL_PACKET_REQUIRED", "DIRECT_PROBE_RECEIPT_SET_INCOMPLETE"}
                or self.projection_hash is not None
            ):
                raise S48RuntimeError("S48_RUNTIME_DIRECT_RECEIPT_STATE_INVALID")
            return
        if self.source_family == "KOFIA":
            if (
                self.ingestion_mode != "DIRECT_READ_PROBE"
                or self.status != "BLOCKED"
                or self.reason != "BLOCKED_NO_CREDENTIAL_OR_APPROVAL"
                or self.projection_hash is not None
            ):
                raise S48RuntimeError("S48_RUNTIME_KOFIA_BLOCKER_INVALID")
            return
        if self.source_family in {"FINNHUB_OPTIONAL3", "TWELVE_DATA", "MASSIVE"}:
            if (
                self.ingestion_mode != "DIRECT_READ_PROBE"
                or self.status != "BLOCKED"
                or self.reason != "BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT"
                or self.projection_hash is not None
            ):
                raise S48RuntimeError("S48_RUNTIME_OPTIONAL3_BLOCKER_INVALID")
            return
        if (
            self.source_family not in _DIRECT_READ_FAMILIES
            or self.ingestion_mode != "DIRECT_READ_PROBE"
            or self.status != "ABSTAIN"
            or self.reason != "APPROVAL_PACKET_REQUIRED"
            or self.projection_hash is not None
        ):
            raise S48RuntimeError("S48_RUNTIME_DIRECT_READ_BLOCKER_INVALID")

    def to_writer_record(self) -> dict[str, object]:
        """V50 append function이 수용하는 content-free, deterministic record를 만든다."""

        payload = {
            "contractId": "s4-8-runtime-lane.v1",
            "decisionAuthority": "NONE",
            "evaluatedAt": _instant(self.evaluated_at),
            "ingestionMode": self.ingestion_mode,
            "orderAuthority": "NONE",
            "projectionHash": self.projection_hash,
            "providerPhysicalCalls": 0,
            "rawProviderDataStored": False,
            "reason": self.reason,
            "retryCount": 0,
            "riskSignalOrderAuthority": "NONE",
            "schemaVersion": 1,
            "sourceFamily": self.source_family,
            "sourceId": self.source_id,
            "status": self.status,
        }
        logical_identity_hash = _sha256(
            f"s4-8-runtime-lane/v1|{self.source_id}|{payload['evaluatedAt']}".encode()
        )
        payload_hash = _sha256(_canonical(payload))
        artifact_hash = _sha256(
            _canonical(
                {
                    "logicalIdentityHash": logical_identity_hash,
                    "payload": payload,
                    "payloadHash": payload_hash,
                }
            )
        )
        return {
            "artifactHash": artifact_hash,
            "logicalIdentityHash": logical_identity_hash,
            "payloadHash": payload_hash,
            **payload,
        }


@dataclass(frozen=True, slots=True)
class S48RuntimeAppendSummary:
    """append-only runtime store가 반환하는 inserted/replay count다."""

    inserted: int
    replayed: int


@dataclass(frozen=True, slots=True)
class S48RuntimeBatch:
    """exact nine lanes를 한 atomic append unit으로 묶은 fixture-first result다."""

    evaluated_at: datetime
    lanes: tuple[S48RuntimeLane, ...]
    provider_physical_calls: int = 0
    retry_count: int = 0

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise S48RuntimeError("S48_RUNTIME_EVALUATED_AT_INVALID")
        if tuple((item.source_family, item.source_id) for item in self.lanes) != S48_RUNTIME_LANES:
            raise S48RuntimeError("S48_RUNTIME_LANE_ORDER_INVALID")
        if self.provider_physical_calls != 0:
            raise S48RuntimeError("S48_RUNTIME_PROVIDER_CALLS_FORBIDDEN")
        if self.retry_count != 0:
            raise S48RuntimeError("S48_RUNTIME_RETRY_FORBIDDEN")

    def writer_records(self) -> tuple[dict[str, object], ...]:
        """No direct table path: each record is for the fixed append function only."""

        return tuple(item.to_writer_record() for item in self.lanes)

    def canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "lanes": list(self.writer_records()),
                "providerPhysicalCalls": self.provider_physical_calls,
                "retryCount": self.retry_count,
            }
        )


class S48RuntimeInMemoryRepository:
    """V50 replay/conflict behavior를 provider-free unit test에서 재현하는 append-only port다."""

    def __init__(self) -> None:
        self._records: dict[str, bytes] = {}

    def append_batch(self, batch: S48RuntimeBatch) -> S48RuntimeAppendSummary:
        candidate = dict(self._records)
        inserted = 0
        replayed = 0
        for record in batch.writer_records():
            identity = _required_hash(record, "logicalIdentityHash")
            canonical = _canonical(record)
            existing = candidate.get(identity)
            if existing is None:
                candidate[identity] = canonical
                inserted += 1
            elif existing == canonical:
                replayed += 1
            else:
                raise S48RuntimeError("S48_RUNTIME_IDENTITY_CONFLICT")
        self._records = candidate
        return S48RuntimeAppendSummary(inserted=inserted, replayed=replayed)


class S48RuntimeMaterializer:
    """provider socket 없이 Core 6/Optional 3 current typed state를 materialize한다."""

    def materialize(
        self,
        *,
        evaluated_at: datetime,
        authorized_projections: Sequence[S48AuthorizedProjection] = (),
        direct_probe_projections: Sequence[S48DirectProbeProjection] = (),
        provider_physical_calls: int = 0,
        retry_count: int = 0,
    ) -> S48RuntimeBatch:
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise S48RuntimeError("S48_RUNTIME_EVALUATED_AT_INVALID")
        if provider_physical_calls != 0:
            raise S48RuntimeError("S48_RUNTIME_PROVIDER_CALLS_FORBIDDEN")
        if retry_count != 0:
            raise S48RuntimeError("S48_RUNTIME_RETRY_FORBIDDEN")
        projection_by_family = _projection_map(authorized_projections)
        direct_projection_by_family = _direct_projection_map(direct_probe_projections)
        lanes = tuple(
            _materialize_lane(
                source_family=source_family,
                source_id=source_id,
                evaluated_at=evaluated_at.astimezone(UTC),
                authorized_projection=projection_by_family.get(source_family),
                direct_probe_projections=direct_projection_by_family.get(source_family, {}),
            )
            for source_family, source_id in S48_RUNTIME_LANES
        )
        return S48RuntimeBatch(
            evaluated_at=evaluated_at.astimezone(UTC),
            lanes=lanes,
            provider_physical_calls=provider_physical_calls,
            retry_count=retry_count,
        )


def _projection_map(
    authorized_projections: Sequence[S48AuthorizedProjection],
) -> Mapping[str, S48AuthorizedProjection]:
    result: dict[str, S48AuthorizedProjection] = {}
    for projection in authorized_projections:
        if projection.source_family in result:
            raise S48RuntimeError("S48_RUNTIME_PROJECTION_DUPLICATE")
        result[projection.source_family] = projection
    return result


def _direct_projection_map(
    direct_probe_projections: Sequence[S48DirectProbeProjection],
) -> Mapping[str, Mapping[str, S48DirectProbeProjection]]:
    """Different receipt는 operation별로 한 번만 받아 stale/ambiguous proof를 fail-closed한다."""

    result: dict[str, dict[str, S48DirectProbeProjection]] = {}
    for projection in direct_probe_projections:
        operations = result.setdefault(projection.source_family, {})
        if projection.operation in operations:
            raise S48RuntimeError("S48_RUNTIME_DIRECT_RECEIPT_DUPLICATE")
        operations[projection.operation] = projection
    return result


def _materialize_lane(
    *,
    source_family: str,
    source_id: str,
    evaluated_at: datetime,
    authorized_projection: S48AuthorizedProjection | None,
    direct_probe_projections: Mapping[str, S48DirectProbeProjection],
) -> S48RuntimeLane:
    if source_family in _PROJECTION_ONLY_FAMILIES:
        if authorized_projection is not None:
            return S48RuntimeLane(
                source_family=source_family,
                source_id=source_id,
                evaluated_at=evaluated_at,
                ingestion_mode="REUSE_AUTHORIZED_PROJECTION",
                status="AVAILABLE",
                reason="AUTHORIZED_PROJECTION_AVAILABLE",
                projection_hash=authorized_projection.projection_hash,
            )
        return S48RuntimeLane(
            source_family=source_family,
            source_id=source_id,
            evaluated_at=evaluated_at,
            ingestion_mode="REUSE_AUTHORIZED_PROJECTION",
            status="ABSTAIN",
            reason="REUSE_AUTHORIZED_PROJECTION_NOT_AVAILABLE",
            projection_hash=None,
        )
    if source_family in _CORE6_RECEIPT_OPERATIONS_BY_FAMILY:
        required_operations = _CORE6_RECEIPT_OPERATIONS_BY_FAMILY[source_family]
        if set(direct_probe_projections) == required_operations:
            return S48RuntimeLane(
                source_family=source_family,
                source_id=source_id,
                evaluated_at=evaluated_at,
                ingestion_mode="DIRECT_READ_PROBE",
                status="AVAILABLE",
                reason="COMPLETE_DIRECT_PROBE_SET_AVAILABLE",
                projection_hash=_direct_probe_set_hash(
                    source_family=source_family,
                    projections=direct_probe_projections,
                ),
            )
        return S48RuntimeLane(
            source_family=source_family,
            source_id=source_id,
            evaluated_at=evaluated_at,
            ingestion_mode="DIRECT_READ_PROBE",
            status="ABSTAIN",
            reason=(
                "DIRECT_PROBE_RECEIPT_SET_INCOMPLETE"
                if direct_probe_projections
                else "APPROVAL_PACKET_REQUIRED"
            ),
            projection_hash=None,
        )
    if source_family == "KOFIA":
        return S48RuntimeLane(
            source_family=source_family,
            source_id=source_id,
            evaluated_at=evaluated_at,
            ingestion_mode="DIRECT_READ_PROBE",
            status="BLOCKED",
            reason="BLOCKED_NO_CREDENTIAL_OR_APPROVAL",
            projection_hash=None,
        )
    if source_family in {"FINNHUB_OPTIONAL3", "TWELVE_DATA", "MASSIVE"}:
        return S48RuntimeLane(
            source_family=source_family,
            source_id=source_id,
            evaluated_at=evaluated_at,
            ingestion_mode="DIRECT_READ_PROBE",
            status="BLOCKED",
            reason="BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT",
            projection_hash=None,
        )
    return S48RuntimeLane(
        source_family=source_family,
        source_id=source_id,
        evaluated_at=evaluated_at,
        ingestion_mode="DIRECT_READ_PROBE",
        status="ABSTAIN",
        reason="APPROVAL_PACKET_REQUIRED",
        projection_hash=None,
    )


def _direct_probe_set_hash(
    *,
    source_family: str,
    projections: Mapping[str, S48DirectProbeProjection],
) -> str:
    """All required receipt hashes를 combine해 individual provider payload 대신 complete-set proof만 남긴다."""

    return _sha256(
        _canonical(
            {
                "operations": [
                    {
                        "operation": operation,
                        "projectionHash": projections[operation].projection_hash,
                    }
                    for operation in sorted(projections)
                ],
                "sourceFamily": source_family,
            }
        )
    )


def _required_hash(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise S48RuntimeError("S48_RUNTIME_HASH_INVALID")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
