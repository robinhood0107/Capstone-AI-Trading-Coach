"""S4.8 Core 6의 packet-gated single-provider probe 공통 경계다.

이 모듈은 local 0700 control root의 canonical packet과 현재 실행 evidence가 모두 일치할 때만
backend에 한 번의 provider handoff를 허용한다. packet·claim·receipt에는 credential, raw response,
header, query 또는 account material을 보관하지 않으며 Decision/Signal/Risk/order authority를 만들지
않는다. 실제 KIS·SEC EDGAR·KRX backend는 이 공통 one-shot 경계를 통해서만 연결한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

from app.rag.oa112_downloader import (
    Oa112DownloadError,
    _open_private_root,
    _read_private_control_file,
    _write_new_private_file,
)


_CONTRACT_ID: Final[str] = "s4-8-core6-probe-approval-v2"
_RECEIPT_CONTRACT_ID: Final[str] = "s4-8-core6-probe-receipt-v2"
_MAX_PACKET_BYTES: Final[int] = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_APPROVAL_ID = re.compile(r"^c6p_[a-z0-9]{32,64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_OPERATOR = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_LEAF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATE = re.compile(r"^(?:NONE|[0-9]{4}-[0-9]{2}-[0-9]{2})$")


class Core6ProbeError(ValueError):
    """Core 6 packet, local control, 또는 one-shot execution invariant 위반이다."""

    def __init__(self, code: str, *, physical_call_count: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.physical_call_count = physical_call_count


@dataclass(frozen=True, slots=True)
class _FixedRequestPlan:
    """Endpoint identity는 code-owned map으로만 고정해 packet이 origin/path를 바꾸지 못하게 한다."""

    provider_family: str
    source_id: str
    operation: str
    resource_pattern: re.Pattern[str]
    requires_date: bool
    target_identity: str

    def validate(self, *, resource_id: str, date: str) -> None:
        """secret-free resource/date가 해당 one-shot operation에 맞는지만 검증한다."""

        if self.resource_pattern.fullmatch(resource_id) is None:
            raise Core6ProbeError("CORE6_PROBE_RESOURCE_INVALID")
        if _DATE.fullmatch(date) is None:
            raise Core6ProbeError("CORE6_PROBE_DATE_INVALID")
        if self.requires_date:
            if date == "NONE":
                raise Core6ProbeError("CORE6_PROBE_DATE_REQUIRED")
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError as error:
                raise Core6ProbeError("CORE6_PROBE_DATE_INVALID") from error
        elif date != "NONE":
            raise Core6ProbeError("CORE6_PROBE_DATE_OPERATION_INVALID")


_PLANS: Final[dict[str, _FixedRequestPlan]] = {
    "KIS_CURRENT_PRICE": _FixedRequestPlan(
        provider_family="KIS",
        source_id="S48_CORE6_KIS",
        operation="KIS_CURRENT_PRICE",
        resource_pattern=re.compile(r"^[0-9]{6}$"),
        requires_date=False,
        target_identity="kis:domestic-stock:current-price:v1",
    ),
    "SEC_EDGAR_SUBMISSIONS": _FixedRequestPlan(
        provider_family="SEC_EDGAR",
        source_id="S48_CORE6_SEC_EDGAR",
        operation="SEC_EDGAR_SUBMISSIONS",
        resource_pattern=re.compile(r"^CIK[0-9]{10}$"),
        requires_date=False,
        target_identity="sec-edgar:submissions:v1",
    ),
    "SEC_EDGAR_COMPANYFACTS": _FixedRequestPlan(
        provider_family="SEC_EDGAR",
        source_id="S48_CORE6_SEC_EDGAR",
        operation="SEC_EDGAR_COMPANYFACTS",
        resource_pattern=re.compile(r"^CIK[0-9]{10}$"),
        requires_date=False,
        target_identity="sec-edgar:companyfacts:v1",
    ),
    "KRX_KOSPI_DAILY": _FixedRequestPlan(
        provider_family="KRX",
        source_id="S48_CORE6_KRX",
        operation="KRX_KOSPI_DAILY",
        resource_pattern=re.compile(r"^NONE$"),
        requires_date=True,
        target_identity="krx:stk_bydd_trd:v1",
    ),
    "KRX_KOSDAQ_DAILY": _FixedRequestPlan(
        provider_family="KRX",
        source_id="S48_CORE6_KRX",
        operation="KRX_KOSDAQ_DAILY",
        resource_pattern=re.compile(r"^NONE$"),
        requires_date=True,
        target_identity="krx:ksq_bydd_trd:v1",
    ),
}


@dataclass(frozen=True, slots=True)
class Core6ProbeExecutionBinding:
    """Current clean Git object와 CI/security result를 bind한 local execution proof다."""

    ci_digest: str
    head_sha: str
    security_digest: str
    tree_sha256: str

    def __post_init__(self) -> None:
        if not all(
            _SHA256.fullmatch(value) is not None
            for value in (self.ci_digest, self.security_digest, self.tree_sha256)
        ) or _HEAD_SHA.fullmatch(self.head_sha) is None:
            raise Core6ProbeError("CORE6_PROBE_EXECUTION_BINDING_INVALID")


@dataclass(frozen=True, slots=True)
class Core6ProbePacket:
    """정확히 한 Core 6 provider handoff만 허용하는 local-only approval packet이다."""

    approval_id: str
    ci_digest: str
    cost_cap_microusd: int
    date: str
    endpoint_set_identity_hash: str
    expires_at: datetime
    head_sha: str
    logical_call_cap: int
    nonce: str
    operation: str
    operator: str
    physical_call_cap: int
    provider_family: str
    request_plan_digest: str
    resource_id: str
    retry_count: int
    security_digest: str
    tracked_raw_artifact_count: int
    tree_sha256: str

    def __post_init__(self) -> None:
        plan = _PLANS.get(self.operation)
        if plan is None or plan.provider_family != self.provider_family:
            raise Core6ProbeError("CORE6_PROBE_OPERATION_PROVIDER_INVALID")
        if (
            _APPROVAL_ID.fullmatch(self.approval_id) is None
            or _NONCE.fullmatch(self.nonce) is None
            or _OPERATOR.fullmatch(self.operator) is None
        ):
            raise Core6ProbeError("CORE6_PROBE_PACKET_FIELD_INVALID")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRY_INVALID")
        if self.logical_call_cap != 1 or self.physical_call_cap != 1 or self.retry_count != 0:
            raise Core6ProbeError("CORE6_PROBE_CAP_INVALID")
        if self.tracked_raw_artifact_count != 0 or not 0 <= self.cost_cap_microusd <= 1_000_000:
            raise Core6ProbeError("CORE6_PROBE_ARTIFACT_OR_COST_INVALID")
        if not all(
            _SHA256.fullmatch(value) is not None
            for value in (
                self.ci_digest,
                self.endpoint_set_identity_hash,
                self.request_plan_digest,
                self.security_digest,
                self.tree_sha256,
            )
        ) or _HEAD_SHA.fullmatch(self.head_sha) is None:
            raise Core6ProbeError("CORE6_PROBE_PACKET_HASH_INVALID")
        if self.endpoint_set_identity_hash != core6_endpoint_set_identity_hash(self.provider_family):
            raise Core6ProbeError("CORE6_PROBE_ENDPOINT_SET_DRIFT")
        plan.validate(resource_id=self.resource_id, date=self.date)
        if self.request_plan_digest != core6_request_plan_digest(
            operation=self.operation,
            resource_id=self.resource_id,
            date=self.date,
        ):
            raise Core6ProbeError("CORE6_PROBE_REQUEST_PLAN_DIGEST_INVALID")

    @property
    def source_id(self) -> str:
        """Public Core 6 source identity는 operation plan에서만 도출한다."""

        return _PLANS[self.operation].source_id

    def require_current(self, *, now: datetime) -> None:
        """Packet은 실행 순간부터 최대 한 시간의 one-shot window만 가진다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise Core6ProbeError("CORE6_PROBE_NOW_INVALID")
        now_utc = now.astimezone(UTC)
        expires_at = self.expires_at.astimezone(UTC)
        if not now_utc < expires_at <= now_utc + timedelta(hours=1):
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRED")

    def require_execution_binding(self, *, binding: Core6ProbeExecutionBinding) -> None:
        """Packet과 final CI/security proof가 같은 clean tree를 가리켜야 한다."""

        if (
            self.ci_digest != binding.ci_digest
            or self.head_sha != binding.head_sha
            or self.security_digest != binding.security_digest
            or self.tree_sha256 != binding.tree_sha256
        ):
            raise Core6ProbeError("CORE6_PROBE_EXECUTION_BINDING_DRIFT")

    def packet_sha256(self) -> str:
        """Claim/receipt에는 packet body 대신 canonical digest만 남긴다."""

        return _sha256(_canonical_bytes(self.to_local_document()))

    def to_local_document(self) -> dict[str, object]:
        """Credential이나 raw response를 둘 수 없는 canonical local packet representation이다."""

        return {
            "approvalId": self.approval_id,
            "ciDigest": self.ci_digest,
            "costCapMicrousd": self.cost_cap_microusd,
            "date": self.date,
            "endpointSetIdentityHash": self.endpoint_set_identity_hash,
            "expiresAt": _instant(self.expires_at),
            "headSha": self.head_sha,
            "logicalCallCap": self.logical_call_cap,
            "nonce": self.nonce,
            "operation": self.operation,
            "operator": self.operator,
            "physicalCallCap": self.physical_call_cap,
            "providerFamily": self.provider_family,
            "requestPlanDigest": self.request_plan_digest,
            "resourceId": self.resource_id,
            "retryCount": self.retry_count,
            "schemaVersion": 1,
            "securityDigest": self.security_digest,
            "trackedRawArtifactCount": self.tracked_raw_artifact_count,
            "treeSha256": self.tree_sha256,
        }

    @classmethod
    def from_local_document(cls, document: Mapping[str, object]) -> "Core6ProbePacket":
        """Unknown packet field를 거부해 local control JSON이 새 execution semantics를 넣지 못하게 한다."""

        expected = {
            "approvalId",
            "ciDigest",
            "costCapMicrousd",
            "date",
            "endpointSetIdentityHash",
            "expiresAt",
            "headSha",
            "logicalCallCap",
            "nonce",
            "operation",
            "operator",
            "physicalCallCap",
            "providerFamily",
            "requestPlanDigest",
            "resourceId",
            "retryCount",
            "schemaVersion",
            "securityDigest",
            "trackedRawArtifactCount",
            "treeSha256",
        }
        if set(document) != expected or document.get("schemaVersion") != 1:
            raise Core6ProbeError("CORE6_PROBE_PACKET_SHAPE_INVALID")
        try:
            return cls(
                approval_id=_required_text(document, "approvalId"),
                ci_digest=_required_text(document, "ciDigest"),
                cost_cap_microusd=_required_integer(document, "costCapMicrousd"),
                date=_required_text(document, "date"),
                endpoint_set_identity_hash=_required_text(document, "endpointSetIdentityHash"),
                expires_at=_parse_instant(_required_text(document, "expiresAt")),
                head_sha=_required_text(document, "headSha"),
                logical_call_cap=_required_integer(document, "logicalCallCap"),
                nonce=_required_text(document, "nonce"),
                operation=_required_text(document, "operation"),
                operator=_required_text(document, "operator"),
                physical_call_cap=_required_integer(document, "physicalCallCap"),
                provider_family=_required_text(document, "providerFamily"),
                request_plan_digest=_required_text(document, "requestPlanDigest"),
                resource_id=_required_text(document, "resourceId"),
                retry_count=_required_integer(document, "retryCount"),
                security_digest=_required_text(document, "securityDigest"),
                tracked_raw_artifact_count=_required_integer(document, "trackedRawArtifactCount"),
                tree_sha256=_required_text(document, "treeSha256"),
            )
        except Core6ProbeError:
            raise
        except (TypeError, ValueError) as error:
            raise Core6ProbeError("CORE6_PROBE_PACKET_SHAPE_INVALID") from error

    @classmethod
    def load_from_control_root(
        cls,
        *,
        control_root: Path,
        relative_path: str,
        now: datetime,
    ) -> "Core6ProbePacket":
        """0700 root/0600 regular local file의 exact canonical packet만 읽는다."""

        if _LEAF.fullmatch(relative_path) is None:
            raise Core6ProbeError("CORE6_PROBE_PACKET_UNSAFE")
        try:
            content = _read_private_control_file(
                root=control_root,
                name=relative_path,
                maximum=_MAX_PACKET_BYTES,
                error_code="CORE6_PROBE_PACKET_UNSAFE",
            )
        except Oa112DownloadError as error:
            raise Core6ProbeError("CORE6_PROBE_PACKET_UNSAFE") from error
        document = _parse_canonical_document(content, code="CORE6_PROBE_PACKET_CANONICAL_INVALID")
        packet = cls.from_local_document(document)
        packet.require_current(now=now)
        return packet


@dataclass(frozen=True, slots=True)
class Core6ProbeBackendResult:
    """Backend가 raw response 없이 공통 executor에 반환하는 one-handoff outcome이다."""

    outcome: str
    provider_status_class: str
    projection_hash: str | None
    physical_call_count: int

    def __post_init__(self) -> None:
        if self.outcome not in {"NOT_EXECUTED", "SUCCESS", "FAILED"}:
            raise Core6ProbeError("CORE6_PROBE_BACKEND_OUTCOME_INVALID")
        if self.provider_status_class not in {
            "NOT_ATTEMPTED",
            "HTTP_2XX",
            "HTTP_4XX",
            "HTTP_5XX",
            "TRANSPORT",
            "PROTOCOL",
        }:
            raise Core6ProbeError("CORE6_PROBE_BACKEND_STATUS_INVALID")
        if self.physical_call_count not in {0, 1}:
            raise Core6ProbeError("CORE6_PROBE_BACKEND_CAP_INVALID")
        if self.outcome == "NOT_EXECUTED":
            if (
                self.provider_status_class != "NOT_ATTEMPTED"
                or self.projection_hash is not None
                or self.physical_call_count != 0
            ):
                raise Core6ProbeError("CORE6_PROBE_BACKEND_NOT_EXECUTED_INVALID")
        elif self.outcome == "SUCCESS":
            if (
                self.provider_status_class != "HTTP_2XX"
                or self.projection_hash is None
                or self.physical_call_count != 1
            ):
                raise Core6ProbeError("CORE6_PROBE_BACKEND_SUCCESS_INVALID")
        elif self.projection_hash is not None or self.physical_call_count != 1:
            raise Core6ProbeError("CORE6_PROBE_BACKEND_FAILURE_INVALID")
        if self.projection_hash is not None and _SHA256.fullmatch(self.projection_hash) is None:
            raise Core6ProbeError("CORE6_PROBE_BACKEND_HASH_INVALID")


class Core6ProbeBackend(Protocol):
    """Provider-specific backend는 packet consume 전 local preflight, 이후 exactly one handoff만 수행한다."""

    def preflight(self, *, packet: Core6ProbePacket) -> None:
        """credential/cache/date 같은 no-network condition을 먼저 fail-closed한다."""

    def execute(self, *, packet: Core6ProbePacket) -> Core6ProbeBackendResult:
        """Packet claim 뒤 raw material을 retained state 없이 한 번만 parse한다."""


@dataclass(frozen=True, slots=True)
class Core6ProbeReceipt:
    """Provider body/header/query 없이 durable one-shot outcome만 남기는 local receipt다."""

    approval_id_hash: str
    approval_packet_sha256: str
    completed_at: datetime
    endpoint_set_identity_hash: str
    logical_call_count: int
    operation: str
    outcome: str
    physical_call_count: int
    projection_hash: str | None
    provider_family: str
    provider_status_class: str
    request_plan_digest: str
    source_id: str
    started_at: datetime

    def __post_init__(self) -> None:
        plan = _PLANS.get(self.operation)
        if plan is None or plan.provider_family != self.provider_family or plan.source_id != self.source_id:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_OPERATION_INVALID")
        if self.outcome not in {"NOT_EXECUTED", "SUCCESS", "FAILED"}:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_OUTCOME_INVALID")
        if self.logical_call_count not in {0, 1} or self.physical_call_count not in {0, 1}:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_CAP_INVALID")
        if self.provider_status_class not in {"NOT_ATTEMPTED", "HTTP_2XX", "HTTP_4XX", "HTTP_5XX", "TRANSPORT", "PROTOCOL"}:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_STATUS_INVALID")
        if self.outcome == "NOT_EXECUTED":
            if (
                self.logical_call_count != 0
                or self.physical_call_count != 0
                or self.provider_status_class != "NOT_ATTEMPTED"
                or self.projection_hash is not None
            ):
                raise Core6ProbeError("CORE6_PROBE_RECEIPT_NOT_EXECUTED_INVALID")
        elif self.outcome == "SUCCESS":
            if (
                self.logical_call_count != 1
                or self.physical_call_count != 1
                or self.provider_status_class != "HTTP_2XX"
                or self.projection_hash is None
            ):
                raise Core6ProbeError("CORE6_PROBE_RECEIPT_SUCCESS_INVALID")
        elif self.logical_call_count != 1 or self.physical_call_count != 1 or self.projection_hash is not None:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_FAILURE_INVALID")
        if not all(
            _SHA256.fullmatch(value) is not None
            for value in (
                self.approval_id_hash,
                self.approval_packet_sha256,
                self.endpoint_set_identity_hash,
                self.request_plan_digest,
            )
        ) or (self.projection_hash is not None and _SHA256.fullmatch(self.projection_hash) is None):
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_HASH_INVALID")
        if self.endpoint_set_identity_hash != core6_endpoint_set_identity_hash(self.provider_family):
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_ENDPOINT_SET_DRIFT")
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_TIME_INVALID")
        if self.completed_at.astimezone(UTC) < self.started_at.astimezone(UTC):
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_TIME_INVALID")

    def to_local_document(self) -> dict[str, object]:
        """Receipt shape에서 raw provider material이 들어갈 field 자체를 제거한다."""

        return {
            "approvalIdHash": self.approval_id_hash,
            "approvalPacketSha256": self.approval_packet_sha256,
            "completedAt": _instant(self.completed_at),
            "contractId": _RECEIPT_CONTRACT_ID,
            "decisionAuthority": "NONE",
            "endpointSetIdentityHash": self.endpoint_set_identity_hash,
            "firstFailureStopsRemainingCalls": True,
            "logicalCallCount": self.logical_call_count,
            "operation": self.operation,
            "outcome": self.outcome,
            "physicalCallCount": self.physical_call_count,
            "projectionHash": self.projection_hash,
            "providerFamily": self.provider_family,
            "providerStatusClass": self.provider_status_class,
            "rawHeaderStored": False,
            "rawProviderDataStored": False,
            "rawQueryStored": False,
            "requestPlanDigest": self.request_plan_digest,
            "retryCount": 0,
            "riskSignalOrderAuthority": "NONE",
            "schemaVersion": 2,
            "sourceId": self.source_id,
            "startedAt": _instant(self.started_at),
            "state": "EXECUTED",
        }

    @classmethod
    def from_local_document(cls, document: Mapping[str, object]) -> "Core6ProbeReceipt":
        """Closed local receipt shape만 parse해 runtime이 raw/provider field를 묵인하지 않게 한다."""

        expected = {
            "approvalIdHash",
            "approvalPacketSha256",
            "completedAt",
            "contractId",
            "decisionAuthority",
            "endpointSetIdentityHash",
            "firstFailureStopsRemainingCalls",
            "logicalCallCount",
            "operation",
            "outcome",
            "physicalCallCount",
            "projectionHash",
            "providerFamily",
            "providerStatusClass",
            "rawHeaderStored",
            "rawProviderDataStored",
            "rawQueryStored",
            "requestPlanDigest",
            "retryCount",
            "riskSignalOrderAuthority",
            "schemaVersion",
            "sourceId",
            "startedAt",
            "state",
        }
        if (
            set(document) != expected
            or document.get("contractId") != _RECEIPT_CONTRACT_ID
            or document.get("schemaVersion") != 2
            or document.get("decisionAuthority") != "NONE"
            or document.get("riskSignalOrderAuthority") != "NONE"
            or document.get("firstFailureStopsRemainingCalls") is not True
            or document.get("rawHeaderStored") is not False
            or document.get("rawProviderDataStored") is not False
            or document.get("rawQueryStored") is not False
            or document.get("retryCount") != 0
            or document.get("state") != "EXECUTED"
        ):
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_SHAPE_INVALID")
        try:
            return cls(
                approval_id_hash=_required_text(document, "approvalIdHash"),
                approval_packet_sha256=_required_text(document, "approvalPacketSha256"),
                completed_at=_parse_instant(_required_text(document, "completedAt")),
                endpoint_set_identity_hash=_required_text(document, "endpointSetIdentityHash"),
                logical_call_count=_required_integer(document, "logicalCallCount"),
                operation=_required_text(document, "operation"),
                outcome=_required_text(document, "outcome"),
                physical_call_count=_required_integer(document, "physicalCallCount"),
                projection_hash=_optional_hash(document, "projectionHash"),
                provider_family=_required_text(document, "providerFamily"),
                provider_status_class=_required_text(document, "providerStatusClass"),
                request_plan_digest=_required_text(document, "requestPlanDigest"),
                source_id=_required_text(document, "sourceId"),
                started_at=_parse_instant(_required_text(document, "startedAt")),
            )
        except Core6ProbeError:
            raise
        except (TypeError, ValueError) as error:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_SHAPE_INVALID") from error

    @classmethod
    def load_from_control_root(
        cls,
        *,
        control_root: Path,
        relative_path: str,
    ) -> "Core6ProbeReceipt":
        """Runtime은 local 0700 root 안의 canonical regular receipt만 read-only로 재사용한다."""

        if _LEAF.fullmatch(relative_path) is None:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_UNSAFE")
        try:
            content = _read_private_control_file(
                root=control_root,
                name=relative_path,
                maximum=_MAX_PACKET_BYTES,
                error_code="CORE6_PROBE_RECEIPT_UNSAFE",
            )
        except Oa112DownloadError as error:
            raise Core6ProbeError("CORE6_PROBE_RECEIPT_UNSAFE") from error
        return cls.from_local_document(
            _parse_canonical_document(content, code="CORE6_PROBE_RECEIPT_CANONICAL_INVALID")
        )


class Core6ProbeExecutor:
    """Preflight→O_EXCL claim→one backend handoff→content-free receipt 순서를 강제한다."""

    def __init__(
        self,
        *,
        control_root: Path,
        backend: Core6ProbeBackend,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._control_root = control_root
        self._backend = backend
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        packet: Core6ProbePacket,
        binding: Core6ProbeExecutionBinding,
        now: datetime,
    ) -> Core6ProbeReceipt:
        """모든 immutable proof가 통과해야만 backend의 한 번의 provider handoff를 허용한다."""

        packet.require_current(now=now)
        packet.require_execution_binding(binding=binding)
        self._backend.preflight(packet=packet)
        claim_key = _claim_key(packet)
        self._consume(packet=packet, claim_key=claim_key)
        handoff_now = self._now_provider()
        try:
            # preflight/claim 사이에 window가 끝나면 provider handoff 전에 packet을 seal한다.
            packet.require_current(now=handoff_now)
            started_at = handoff_now.astimezone(UTC)
            result = self._backend.execute(packet=packet)
        except Core6ProbeError as error:
            started_at = handoff_now.astimezone(UTC)
            result = Core6ProbeBackendResult(
                outcome="NOT_EXECUTED" if error.physical_call_count == 0 else "FAILED",
                provider_status_class="NOT_ATTEMPTED" if error.physical_call_count == 0 else "TRANSPORT",
                projection_hash=None,
                physical_call_count=error.physical_call_count,
            )
        except Exception:
            started_at = handoff_now.astimezone(UTC)
            # Claim 이후 backend boundary가 예외로 끝나면 first handoff는 실패한 것으로 seal한다.
            result = Core6ProbeBackendResult(
                outcome="FAILED",
                provider_status_class="TRANSPORT",
                projection_hash=None,
                physical_call_count=1,
            )
        receipt = _receipt(
            packet=packet,
            started_at=started_at,
            completed_at=_completed_at(self._now_provider()),
            result=result,
        )
        self._write_receipt(packet=packet, receipt=receipt)
        return receipt

    def _consume(self, *, packet: Core6ProbePacket, claim_key: str) -> None:
        """Concurrent process가 동일 packet을 재사용하지 못하게 request 전 O_EXCL claim을 만든다."""

        document = {
            "approvalIdHash": _sha256(packet.approval_id.encode("utf-8")),
            "approvalPacketSha256": packet.packet_sha256(),
            "contractId": _CONTRACT_ID,
            "nonceHash": _sha256(packet.nonce.encode("utf-8")),
            "schemaVersion": 2,
        }
        try:
            root_fd = _open_private_root(self._control_root, error_code="CORE6_PROBE_CONTROL_UNSAFE")
        except Oa112DownloadError as error:
            raise Core6ProbeError("CORE6_PROBE_CONTROL_UNSAFE") from error
        try:
            try:
                _write_new_private_file(
                    root_fd,
                    f"consumed-{claim_key}.json",
                    _canonical_bytes(document),
                )
            except FileExistsError as error:
                raise Core6ProbeError("CORE6_PROBE_PACKET_ALREADY_CONSUMED") from error
            except (OSError, Oa112DownloadError) as error:
                raise Core6ProbeError("CORE6_PROBE_CLAIM_UNAVAILABLE") from error
        finally:
            os.close(root_fd)

    def _write_receipt(self, *, packet: Core6ProbePacket, receipt: Core6ProbeReceipt) -> None:
        """Handoff 뒤 receipt 저장이 실패하면 outcome을 조용히 잃지 않고 fail-closed한다."""

        try:
            root_fd = _open_private_root(self._control_root, error_code="CORE6_PROBE_CONTROL_UNSAFE")
        except Oa112DownloadError as error:
            raise Core6ProbeError("CORE6_PROBE_CONTROL_UNSAFE") from error
        try:
            try:
                _write_new_private_file(
                    root_fd,
                    core6_receipt_file_name(packet),
                    _canonical_bytes(receipt.to_local_document()),
                )
            except (FileExistsError, OSError, Oa112DownloadError) as error:
                raise Core6ProbeError(
                    "CORE6_PROBE_RECEIPT_UNAVAILABLE",
                    physical_call_count=receipt.physical_call_count,
                ) from error
        finally:
            os.close(root_fd)


def core6_endpoint_set_identity_hash(provider_family: str) -> str:
    """Public Core 6 contract와 같은 opaque endpoint-set identity를 계산한다."""

    if provider_family not in {plan.provider_family for plan in _PLANS.values()}:
        raise Core6ProbeError("CORE6_PROBE_PROVIDER_FAMILY_INVALID")
    return _sha256(f"s4-8-core6:{provider_family}:opaque-endpoint-set-v1".encode("utf-8"))


def core6_request_plan_digest(*, operation: str, resource_id: str, date: str) -> str:
    """Fixed operation/resource/date와 code-owned target identity를 secret 없이 bind한다."""

    plan = _PLANS.get(operation)
    if plan is None:
        raise Core6ProbeError("CORE6_PROBE_OPERATION_PROVIDER_INVALID")
    plan.validate(resource_id=resource_id, date=date)
    return _sha256(
        _canonical_bytes(
            {
                "date": date,
                "operation": plan.operation,
                "providerFamily": plan.provider_family,
                "resourceId": resource_id,
                "sourceId": plan.source_id,
                "targetIdentity": plan.target_identity,
            }
        )
    )


def core6_request_plan(*, operation: str) -> _FixedRequestPlan:
    """Actual backend selection은 fixed operation map에서만 request plan을 읽는다."""

    plan = _PLANS.get(operation)
    if plan is None:
        raise Core6ProbeError("CORE6_PROBE_OPERATION_PROVIDER_INVALID")
    return plan


def _parse_canonical_document(content: bytes, *, code: str) -> Mapping[str, object]:
    if not content or len(content) > _MAX_PACKET_BYTES:
        raise Core6ProbeError(code)
    try:
        document = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Core6ProbeError(code) from error
    if not isinstance(document, Mapping) or _canonical_bytes(document) != content:
        raise Core6ProbeError(code)
    return document


def _required_text(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise Core6ProbeError("CORE6_PROBE_PACKET_SHAPE_INVALID")
    return value


def _required_integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise Core6ProbeError("CORE6_PROBE_PACKET_SHAPE_INVALID")
    return value


def _optional_hash(document: Mapping[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise Core6ProbeError("CORE6_PROBE_RECEIPT_SHAPE_INVALID")
    return value


def _parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRY_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRY_INVALID")
    return parsed.astimezone(UTC)


def _receipt(
    *,
    packet: Core6ProbePacket,
    started_at: datetime,
    completed_at: datetime,
    result: Core6ProbeBackendResult,
) -> Core6ProbeReceipt:
    return Core6ProbeReceipt(
        approval_id_hash=_sha256(packet.approval_id.encode("utf-8")),
        approval_packet_sha256=packet.packet_sha256(),
        completed_at=completed_at,
        endpoint_set_identity_hash=packet.endpoint_set_identity_hash,
        logical_call_count=0 if result.outcome == "NOT_EXECUTED" else 1,
        operation=packet.operation,
        outcome=result.outcome,
        physical_call_count=result.physical_call_count,
        projection_hash=result.projection_hash,
        provider_family=packet.provider_family,
        provider_status_class=result.provider_status_class,
        request_plan_digest=packet.request_plan_digest,
        source_id=packet.source_id,
        started_at=started_at,
    )


def _claim_key(packet: Core6ProbePacket) -> str:
    return _sha256(f"{packet.approval_id}\0{packet.packet_sha256()}\0{packet.nonce}".encode("utf-8"))


def core6_receipt_file_name(packet: Core6ProbePacket) -> str:
    """Runtime selector에 쓸 local receipt leaf만 도출한다. packet/nonce 본문은 출력하지 않는다."""

    return f"receipt-{_claim_key(packet)}.json"


def _completed_at(now: datetime) -> datetime:
    return now.astimezone(UTC)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
