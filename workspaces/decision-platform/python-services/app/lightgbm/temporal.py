"""S5.6 production temporal evidence와 row-specific PIT clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
import hashlib
from typing import Callable, Iterable, TypeVar
from zoneinfo import ZoneInfo

import pandas as pd

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.pit_calendar import corrected_calendar


KST = ZoneInfo("Asia/Seoul")
TEMPORAL_POLICY_VERSION = "s5-temporal-policy-v2"


class AvailabilityBasis(StrEnum):
    PROVIDER_FIELD = "PROVIDER_FIELD"
    PROVIDER_AS_OF_SCHEDULE = "PROVIDER_AS_OF_SCHEDULE"
    PROJECT_FIXED_LAG = "PROJECT_FIXED_LAG"
    RETRIEVAL_ONLY = "RETRIEVAL_ONLY"


class RevisionBasis(StrEnum):
    PROVIDER_REVISION = "PROVIDER_REVISION"
    CONTENT_SNAPSHOT = "CONTENT_SNAPSHOT"
    NONE = "NONE"


class TemporalQuality(StrEnum):
    PROVIDER_VINTAGE = "PROVIDER_VINTAGE"
    PROVIDER_AS_OF_NO_VINTAGE = "PROVIDER_AS_OF_NO_VINTAGE"
    RECONSTRUCTED_FIXED_LAG = "RECONSTRUCTED_FIXED_LAG"
    COLLECTION_ONLY = "COLLECTION_ONLY"


@dataclass(frozen=True, slots=True)
class TemporalReceipt:
    """Provider 주장과 프로젝트 재구성 시각을 분리한 closed production receipt."""

    source_id: str
    operation_id: str
    observation_date: date
    retrieved_at: datetime
    availability_basis: AvailabilityBasis
    revision_basis: RevisionBasis
    request_sha256: str
    snapshot_sha256: str
    temporal_quality: TemporalQuality
    provider_available_at: datetime | None = None
    policy_effective_at: datetime | None = None
    provider_revision: str | None = None
    temporal_policy_version: str = TEMPORAL_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.source_id not in {"KIS", "KRX", "ECOS"} or not self.operation_id:
            raise LightGbmContractError("temporal receipt source or operation is invalid")
        _require_utc(self.retrieved_at, "retrievedAt")
        for value, name in (
            (self.provider_available_at, "providerAvailableAt"),
            (self.policy_effective_at, "policyEffectiveAt"),
        ):
            if value is not None:
                _require_aware(value, name)
        _require_sha256(self.request_sha256, "requestSha256")
        _require_sha256(self.snapshot_sha256, "snapshotSha256")
        if self.temporal_policy_version != TEMPORAL_POLICY_VERSION:
            raise LightGbmContractError("temporal policy version is not approved")
        if self.availability_basis is AvailabilityBasis.PROVIDER_FIELD:
            if self.provider_available_at is None or self.policy_effective_at is not None:
                raise LightGbmContractError("provider availability receipt is inconsistent")
        elif self.availability_basis in {
            AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE,
            AvailabilityBasis.PROJECT_FIXED_LAG,
        }:
            if self.policy_effective_at is None or self.provider_available_at is not None:
                raise LightGbmContractError("policy-effective receipt is inconsistent")
        elif self.provider_available_at is not None or self.policy_effective_at is not None:
            raise LightGbmContractError("retrieval-only receipt cannot backdate availability")
        if self.revision_basis is RevisionBasis.PROVIDER_REVISION:
            if not self.provider_revision:
                raise LightGbmContractError("provider revision evidence is missing")
        elif self.provider_revision is not None:
            raise LightGbmContractError("snapshot digest must not be called provider revision")
        if self.temporal_quality is TemporalQuality.RECONSTRUCTED_FIXED_LAG and (
            self.availability_basis is not AvailabilityBasis.PROJECT_FIXED_LAG
        ):
            raise LightGbmContractError("reconstructed quality requires fixed-lag evidence")
        if self.temporal_quality is TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE and (
            self.availability_basis is not AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE
        ):
            raise LightGbmContractError("KRX as-of quality requires provider schedule")
        if self.temporal_quality is TemporalQuality.PROVIDER_VINTAGE and (
            self.availability_basis is not AvailabilityBasis.PROVIDER_FIELD
        ):
            raise LightGbmContractError("provider-vintage quality requires provider availability")
        if self.temporal_quality is TemporalQuality.COLLECTION_ONLY and (
            self.availability_basis is not AvailabilityBasis.RETRIEVAL_ONLY
        ):
            raise LightGbmContractError("collection-only quality requires retrieval-only evidence")

    @property
    def effective_at(self) -> datetime | None:
        """Eligibility clock이며 retrieval time을 과거 availableAt으로 재해석하지 않는다."""

        return self.provider_available_at or self.policy_effective_at

    def as_dict(self) -> dict[str, object]:
        """Contract schema와 동일한 camelCase projection을 반환한다."""

        payload: dict[str, object] = {
            "sourceId": self.source_id,
            "operationId": self.operation_id,
            "observationDate": self.observation_date.isoformat(),
            "retrievedAt": _canonical_utc(self.retrieved_at),
            "availabilityBasis": self.availability_basis.value,
            "revisionBasis": self.revision_basis.value,
            "requestSha256": self.request_sha256,
            "snapshotSha256": self.snapshot_sha256,
            "temporalPolicyVersion": self.temporal_policy_version,
            "temporalQuality": self.temporal_quality.value,
        }
        if self.provider_available_at is not None:
            payload["providerAvailableAt"] = _canonical_utc(self.provider_available_at)
        if self.policy_effective_at is not None:
            payload["policyEffectiveAt"] = _canonical_utc(self.policy_effective_at)
        if self.provider_revision is not None:
            payload["providerRevision"] = self.provider_revision
        return payload


def next_session_evidence_clock(observation_date: date, *, extra_sessions: int = 0) -> datetime:
    """관측 session 다음 XKRX session 08:10 KST를 반환한다."""

    if type(observation_date) is not date or isinstance(extra_sessions, bool) or extra_sessions < 0:
        raise LightGbmContractError("evidence clock input is invalid")
    calendar = corrected_calendar()
    try:
        session = calendar.date_to_session(pd.Timestamp(observation_date), direction="none")
    except Exception:
        raise LightGbmContractError("observationDate must be an XKRX session") from None
    target = session
    for _ in range(extra_sessions + 1):
        target = calendar.next_session(target)
    return datetime.combine(target.date(), time(8, 10), tzinfo=KST)


def next_xkrx_evidence_clock(observation_date: date) -> datetime:
    """ECOS 같은 비거래일 관측에도 적용할 수 있는 다음 XKRX session 08:10 clock."""

    if type(observation_date) is not date:
        raise LightGbmContractError("evidence clock input is invalid")
    calendar = corrected_calendar()
    candidate = calendar.date_to_session(pd.Timestamp(observation_date), direction="next")
    try:
        if candidate.date() == observation_date:
            candidate = calendar.next_session(candidate)
    except Exception:
        raise LightGbmContractError("evidence observation date is invalid") from None
    return datetime.combine(candidate.date(), time(8, 10), tzinfo=KST)


def feature_as_of(session_date: date) -> datetime:
    """Feature row t의 source eligibility clock."""

    return next_session_evidence_clock(session_date)


def label_as_of(label_end_session: date) -> datetime:
    """t+6 open label이 성숙하는 최초 clock."""

    return next_session_evidence_clock(label_end_session)


def require_receipt_eligible(
    receipt: TemporalReceipt,
    *,
    row_clock: datetime,
    dataset_cutoff: datetime,
) -> None:
    """Final cutoff만 보지 않고 row clock과 dataset cutoff를 함께 강제한다."""

    _require_aware(row_clock, "row clock")
    _require_aware(dataset_cutoff, "dataset cutoff")
    effective = receipt.effective_at
    if effective is None or receipt.temporal_quality is TemporalQuality.COLLECTION_ONLY:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: source is collection-only")
    if effective > row_clock or row_clock > dataset_cutoff:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: source was unavailable at row clock")


_T = TypeVar("_T")


def collapse_or_reject_snapshots(
    values: Iterable[_T],
    *,
    logical_key: Callable[[_T], object],
    receipt_of: Callable[[_T], TemporalReceipt],
) -> tuple[_T, ...]:
    """동일 snapshot만 collapse하고 비교 불가한 conflicting revision은 거부한다."""

    selected: dict[object, _T] = {}
    for value in values:
        receipt = receipt_of(value)
        key = logical_key(value)
        previous = selected.get(key)
        if previous is None:
            selected[key] = value
            continue
        previous_receipt = receipt_of(previous)
        if receipt.snapshot_sha256 == previous_receipt.snapshot_sha256:
            continue
        if (
            receipt.revision_basis is RevisionBasis.PROVIDER_REVISION
            and previous_receipt.revision_basis is RevisionBasis.PROVIDER_REVISION
        ):
            if receipt.provider_revision == previous_receipt.provider_revision:
                raise LightGbmContractError("SOURCE_SNAPSHOT_CONFLICT")
            raise LightGbmContractError(
                "provider revision ordering requires a source-specific comparator"
            )
        raise LightGbmContractError("SOURCE_SNAPSHOT_CONFLICT")
    return tuple(selected[key] for key in sorted(selected, key=str))


def receipt_set_sha256(receipts: Iterable[TemporalReceipt]) -> str:
    """완전한 TemporalReceipt set을 canonical content hash로 묶는다."""

    values = sorted(
        (receipt.as_dict() for receipt in receipts),
        key=lambda value: (
            str(value["sourceId"]),
            str(value["operationId"]),
            str(value["observationDate"]),
            str(value["snapshotSha256"]),
        ),
    )
    if not values:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: temporal receipts are absent")
    digest = hashlib.sha256(b"s5-temporal-receipt-set-v2\x00")
    digest.update(canonical_json_bytes(values))
    return digest.hexdigest()


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_utc(value: datetime, field: str) -> None:
    _require_aware(value, field)
    if value.utcoffset() != UTC.utcoffset(value):
        raise LightGbmContractError(f"{field} must be UTC")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LightGbmContractError(f"{field} must be timezone aware")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LightGbmContractError(f"{field} must be lowercase SHA-256")
