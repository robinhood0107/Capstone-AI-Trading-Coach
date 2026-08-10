"""final HEAD와 exact 승인 latch에 결속된 KIS_MOCK brokerage 단일 probe."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.brokerage.kis_mock_online_client import (
    KISBrokerageCallBudget,
    KISBrokerageCallBudgetExceeded,
    KISMockBrokerageError,
    KISMockBrokerageHttpClient,
    KISMockFailureReason,
)
from app.brokerage.kis_mock_online_runtime import (
    KISMockExecutionReader,
    KISMockOnlineBalanceReader,
    KISMockProjectionError,
)
from app.brokerage.kis_mock_approval_environment import (
    KISMockApprovalEnvironmentRejected,
    load_kis_mock_approval_environment,
)
from app.brokerage.kis_mock_order_gateway import (
    KISMockOrderGateway,
    MockOrderIntent,
    MockOrderRecoveryError,
)
from app.brokerage.mock_order_reference_store import (
    EncryptedRedisOrderReferenceStore,
    EncryptedRedisApprovalOutcomeStore,
    KISMockApprovalOutcome,
    KISMockApprovalOutcomeUnavailable,
)
from app.data.kis._credential_transport import KISCredentialError, _build_redis_client
from app.data.kis.settings import KISSettings

_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
_MAX_PACKET_BYTES = 64 * 1024
_SHA256 = r"^[0-9a-f]{64}$"
_GIT_SHA = r"^[0-9a-f]{40}$"
_NONCE = r"^[0-9a-f]{64}$"
_APPROVAL_ID = r"^approval-s3-online-[a-z0-9][a-z0-9-]{3,95}$"
_BRANCH = r"^(?:feature|fix|docs|infra|experiment)/[A-Za-z0-9._/-]{1,120}$"
_ORDER_ID = r"^ord_mock_[0-9a-f]{32}$"
_ACCOUNT_ID = r"^acct_[0-9a-f]{32}$"
_SYMBOL = r"^[0-9]{6}$"
_CANONICAL_STEPS = (
    "balance",
    "buyable",
    "submitLimitBuy",
    "cancelFull",
    "executionRead",
)
_BALANCE_DIAGNOSTIC_STEPS = ("balance",)
_APPROVAL_CONSUMED_KEY_PREFIX = "kis:mock:approval-consumed:v1:"
_PROVIDER_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
_REQUIRED_CI_CHECKS = frozenset(
    {
        "Contract schema validation",
        "Spring OpenAPI drift",
        "Kotlin ktlint and build",
        "Python quality gates",
        "Repo hygiene",
    }
)


class KISMockApprovalRejected(RuntimeError):
    """packet, repository evidence, TTL 또는 current-user latch가 다르면 outbound 전에 거부한다."""


class KISMockProbeFailed(RuntimeError):
    """첫 실패 지점, bounded 원인과 이미 예약된 호출 수만 보존한다."""

    def __init__(
        self,
        failed_step: str,
        physical_reservations: dict[str, int],
        *,
        reason_code: str = KISMockFailureReason.UNCLASSIFIED_FAILURE.value,
        provider_code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__("KIS_MOCK brokerage probe failed")
        self.failed_step = failed_step
        self.physical_reservations = dict(physical_reservations)
        allowed_reasons = {reason.value for reason in KISMockFailureReason}
        self.reason_code = (
            reason_code
            if reason_code in allowed_reasons
            else KISMockFailureReason.UNCLASSIFIED_FAILURE.value
        )
        self.provider_code = (
            provider_code
            if provider_code is not None
            and _PROVIDER_CODE.fullmatch(provider_code) is not None
            else None
        )
        self.http_status = (
            http_status
            if type(http_status) is int and 100 <= http_status <= 599
            else None
        )


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RepositoryEvidence(_StrictModel):
    """v1 history packet은 PR #55만 검증할 수 있게 영구 고정한다."""

    root: str
    branch_ref: str = Field(alias="branchRef", pattern=_BRANCH)
    head_sha: str = Field(alias="headSha", pattern=_GIT_SHA)
    remote_head_sha: str = Field(alias="remoteHeadSha", pattern=_GIT_SHA)
    pull_request: StrictInt = Field(alias="pullRequest", ge=55, le=55)


class RepositoryEvidenceV2(_StrictModel):
    """v2 packet은 open PR 또는 merged main의 exact final HEAD에 결속한다."""

    root: str
    branch_ref: str = Field(alias="branchRef", min_length=1, max_length=128)
    base_ref: Literal["main"] = Field(alias="baseRef")
    head_sha: str = Field(alias="headSha", pattern=_GIT_SHA)
    remote_head_sha: str = Field(alias="remoteHeadSha", pattern=_GIT_SHA)
    pull_request: StrictInt = Field(alias="pullRequest", ge=1)
    evidence_mode: Literal["OPEN_PR", "MERGED_MAIN"] = Field(
        default="OPEN_PR",
        alias="evidenceMode",
    )

    @model_validator(mode="after")
    def _branch_matches_evidence_mode(self) -> "RepositoryEvidenceV2":
        if self.evidence_mode == "MERGED_MAIN" and self.branch_ref != "main":
            raise ValueError("merged main evidence must bind origin/main")
        if self.evidence_mode == "OPEN_PR" and re.fullmatch(_BRANCH, self.branch_ref) is None:
            raise ValueError("open PR evidence must bind a feature branch")
        return self


class RequiredCheck(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    conclusion: Literal["SUCCESS"]


class ApprovalEvidence(_StrictModel):
    ci_head_sha: str = Field(alias="ciHeadSha", pattern=_GIT_SHA)
    required_checks: tuple[RequiredCheck, ...] = Field(
        alias="requiredChecks",
        min_length=4,
        max_length=16,
    )
    security_head_sha: str = Field(alias="securityHeadSha", pattern=_GIT_SHA)
    security_status: Literal["SECURITY_SCAN_COMPLETE"] = Field(alias="securityStatus")
    security_findings: StrictInt = Field(alias="securityFindings", ge=0, le=0)
    security_report_path: str = Field(alias="securityReportPath", min_length=1)
    security_report_sha256: str = Field(alias="securityReportSha256", pattern=_SHA256)

    @model_validator(mode="after")
    def _checks_are_unique(self) -> "ApprovalEvidence":
        names = tuple(check.name for check in self.required_checks)
        if len(set(names)) != len(names):
            raise ValueError("required CI checks must be unique")
        if not _REQUIRED_CI_CHECKS.issubset(names):
            raise ValueError("required CI evidence is incomplete")
        return self


class ApprovalEvidenceV2(ApprovalEvidence):
    """v2는 sealed scan의 manifest/coverage/findings까지 exact packet에 함께 결속한다."""

    security_manifest_path: str = Field(alias="securityManifestPath", min_length=1)
    security_manifest_sha256: str = Field(alias="securityManifestSha256", pattern=_SHA256)
    security_coverage_path: str = Field(alias="securityCoveragePath", min_length=1)
    security_coverage_sha256: str = Field(alias="securityCoverageSha256", pattern=_SHA256)
    security_findings_path: str = Field(alias="securityFindingsPath", min_length=1)
    security_findings_sha256: str = Field(alias="securityFindingsSha256", pattern=_SHA256)


class PhysicalCaps(_StrictModel):
    token_p: StrictInt = Field(alias="tokenP", ge=1, le=1)
    brokerage: StrictInt = Field(ge=1, le=5)


class RedisBaseline(_StrictModel):
    rest_pttl_millis: StrictInt = Field(alias="restPttlMillis", ge=-2, le=10_000)
    token_p_pttl_millis: StrictInt = Field(alias="tokenPttlMillis", ge=-2, le=10_000)
    observed_at: datetime = Field(alias="observedAt")


class ApprovalOrder(_StrictModel):
    order_id: str = Field(alias="orderId", pattern=_ORDER_ID)
    account_id: str = Field(alias="accountId", pattern=_ACCOUNT_ID)
    symbol: str = Field(pattern=_SYMBOL)
    side: Literal["BUY"]
    order_type: Literal["LIMIT"] = Field(alias="orderType")
    quantity: StrictInt = Field(ge=1, le=1)
    limit_price_krw: StrictInt = Field(alias="limitPriceKrw", ge=1, le=1_000_000_000)
    order_division: Literal["00", "05", "06", "07"] = Field(
        default="00",
        alias="orderDivision",
    )
    exchange_division: Literal["KRX", "NXT"] = Field(
        default="KRX",
        alias="exchangeDivision",
    )

    @model_validator(mode="after")
    def _exchange_contract(self) -> "ApprovalOrder":
        if self.exchange_division != "KRX":
            raise ValueError("KIS_MOCK cash-order probe supports KRX only")
        return self


class ExecutionWindow(_StrictModel):
    start: date = Field(alias="from")
    end: date = Field(alias="to")
    recent: StrictBool

    @model_validator(mode="after")
    def _bounded_window(self) -> "ExecutionWindow":
        if self.recent is not True:
            raise ValueError("execution read must use the recent mock TR")
        if self.start > self.end or (self.end - self.start).days > 31:
            raise ValueError("execution window must be an inclusive maximum of 31 days")
        return self


class RecoveryOf(_StrictModel):
    """새 주문을 표현하지 않고 원 FULL packet의 encrypted reference만 재사용한다."""

    source_approval_id: str = Field(alias="sourceApprovalId", pattern=_APPROVAL_ID)
    source_packet_sha256: str = Field(alias="sourcePacketSha256", pattern=_SHA256)
    source_nonce: str = Field(alias="sourceNonce", pattern=_NONCE)
    failed_step: Literal["cancelFull", "executionRead"] = Field(alias="failedStep")


class KISMockApprovalPacket(_StrictModel):
    """PR #55 historical verification 전용 v1 exact 승인 문서다."""

    schema_version: StrictInt = Field(alias="schemaVersion", ge=1, le=1)
    approval_id: str = Field(alias="approvalId", pattern=_APPROVAL_ID)
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    mode: Literal["KIS_MOCK"]
    kis_live_order_enabled: StrictBool = Field(alias="kisLiveOrderEnabled")
    retry_count: StrictInt = Field(alias="retryCount", ge=0, le=0)
    artifact_writes: StrictInt = Field(alias="artifactWrites", ge=0, le=0)
    provider_calls_before_approval: StrictInt = Field(
        alias="providerCallsBeforeApproval",
        ge=0,
        le=0,
    )
    probe_type: Literal["FULL", "BALANCE_DIAGNOSTIC"] = Field(
        default="FULL",
        alias="probeType",
    )
    repository: RepositoryEvidence
    evidence: ApprovalEvidence
    physical_caps: PhysicalCaps = Field(alias="physicalCaps")
    redis_baseline: RedisBaseline = Field(alias="redisBaseline")
    reference_ttl_seconds: StrictInt = Field(
        alias="referenceTtlSeconds",
        ge=60,
        le=7 * 24 * 60 * 60,
    )
    order: ApprovalOrder
    execution: ExecutionWindow
    steps: tuple[str, ...]
    stop_rule: Literal["FIRST_FAILURE_STOPS_REMAINING_CALLS"] = Field(alias="stopRule")
    execution_command: str = Field(alias="executionCommand", min_length=1, max_length=4096)
    packet_sha256: str = Field(alias="packetSha256", pattern=_SHA256)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("approval timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def _cross_field_contract(self) -> "KISMockApprovalPacket":
        if self.kis_live_order_enabled is not False:
            raise ValueError("KIS_LIVE order must remain disabled")
        expected_steps = (
            _CANONICAL_STEPS
            if self.probe_type == "FULL"
            else _BALANCE_DIAGNOSTIC_STEPS
        )
        expected_brokerage_cap = 5 if self.probe_type == "FULL" else 1
        if (
            self.steps != expected_steps
            or self.physical_caps.brokerage != expected_brokerage_cap
        ):
            raise ValueError("approval steps and caps must match the probe type")
        if not self.issued_at < self.expires_at:
            raise ValueError("approval TTL must be positive")
        if (self.expires_at - self.issued_at).total_seconds() > 3_600:
            raise ValueError("approval TTL must not exceed 60 minutes")
        head = self.repository.head_sha
        if (
            self.repository.remote_head_sha != head
            or self.evidence.ci_head_sha != head
            or self.evidence.security_head_sha != head
        ):
            raise ValueError("approval evidence must bind one final HEAD")
        if not self.issued_at <= self.redis_baseline.observed_at <= self.expires_at:
            raise ValueError("Redis baseline must be observed inside approval TTL")
        return self


class KISMockApprovalPacketV2(_StrictModel):
    """현재 PR의 exact evidence와 one-time nonce를 갖는 KIS_MOCK operator packet이다."""

    schema_version: Literal[2] = Field(alias="schemaVersion")
    approval_id: str = Field(alias="approvalId", pattern=_APPROVAL_ID)
    nonce: str = Field(pattern=_NONCE)
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    mode: Literal["KIS_MOCK"]
    kis_live_order_enabled: StrictBool = Field(alias="kisLiveOrderEnabled")
    retry_count: StrictInt = Field(alias="retryCount", ge=0, le=0)
    artifact_writes: StrictInt = Field(alias="artifactWrites", ge=0, le=0)
    provider_calls_before_approval: StrictInt = Field(
        alias="providerCallsBeforeApproval",
        ge=0,
        le=0,
    )
    probe_type: Literal["FULL", "BALANCE_DIAGNOSTIC", "CANCEL_RECOVERY"] = Field(
        default="FULL",
        alias="probeType",
    )
    repository: RepositoryEvidenceV2
    evidence: ApprovalEvidenceV2
    physical_caps: PhysicalCaps = Field(alias="physicalCaps")
    redis_baseline: RedisBaseline = Field(alias="redisBaseline")
    reference_ttl_seconds: StrictInt = Field(
        alias="referenceTtlSeconds",
        ge=60,
        le=7 * 24 * 60 * 60,
    )
    order: ApprovalOrder
    execution: ExecutionWindow
    steps: tuple[str, ...]
    stop_rule: Literal["FIRST_FAILURE_STOPS_REMAINING_CALLS"] = Field(alias="stopRule")
    execution_command: str = Field(alias="executionCommand", min_length=1, max_length=4096)
    recovery_of: RecoveryOf | None = Field(default=None, alias="recoveryOf")
    packet_sha256: str = Field(alias="packetSha256", pattern=_SHA256)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("approval timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def _cross_field_contract(self) -> "KISMockApprovalPacketV2":
        if self.kis_live_order_enabled is not False:
            raise ValueError("KIS_LIVE order must remain disabled")
        expected_steps: tuple[str, ...]
        expected_brokerage_cap: int
        if self.probe_type == "FULL":
            expected_steps = _CANONICAL_STEPS
            expected_brokerage_cap = 5
            if self.recovery_of is not None:
                raise ValueError("FULL packet must not contain recovery evidence")
        elif self.probe_type == "BALANCE_DIAGNOSTIC":
            expected_steps = _BALANCE_DIAGNOSTIC_STEPS
            expected_brokerage_cap = 1
            if self.recovery_of is not None:
                raise ValueError("balance diagnostic must not contain recovery evidence")
        else:
            if self.recovery_of is None:
                raise ValueError("cancel recovery requires source packet evidence")
            if self.recovery_of.failed_step == "cancelFull":
                expected_steps = ("cancelFull", "executionRead")
                expected_brokerage_cap = 2
            else:
                # 취소 성공 뒤 execution read만 실패한 경우 취소를 재전송하지 않는다.
                expected_steps = ("executionRead",)
                expected_brokerage_cap = 1
        if (
            self.steps != expected_steps
            or self.physical_caps.brokerage != expected_brokerage_cap
            or self.physical_caps.token_p != 1
        ):
            raise ValueError("approval steps and caps must match the probe type")
        if not self.issued_at < self.expires_at:
            raise ValueError("approval TTL must be positive")
        if (self.expires_at - self.issued_at).total_seconds() > 3_600:
            raise ValueError("approval TTL must not exceed 60 minutes")
        head = self.repository.head_sha
        if (
            self.repository.remote_head_sha != head
            or self.evidence.ci_head_sha != head
            or self.evidence.security_head_sha != head
        ):
            raise ValueError("approval evidence must bind one final HEAD")
        if not self.issued_at <= self.redis_baseline.observed_at <= self.expires_at:
            raise ValueError("Redis baseline must be observed inside approval TTL")
        return self


ApprovalPacket = KISMockApprovalPacket | KISMockApprovalPacketV2


class ProbeOperations(Protocol):
    """승인 executor가 호출할 수 있는 유일한 5단계 runtime 표면이다."""

    def run(self, operation: str, packet: ApprovalPacket) -> None: ...

    def counts(self) -> dict[str, int]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProbeSummary:
    approval_id: str
    completed_steps: tuple[str, ...]
    physical_reservations: dict[str, int]


def execute_approved_probe(
    packet_path: Path,
    *,
    now: datetime,
    expected_approval_id: str | None,
    expected_packet_sha256: str | None,
    repository_root: Path,
    operations_factory: Callable[[ApprovalPacket], ProbeOperations],
    approval_consumer: Callable[[ApprovalPacket, datetime], None],
    clock: Callable[[], datetime] | None = None,
) -> ProbeSummary:
    """v2 live evidence와 source outcome을 재검증한 뒤 bounded runtime만 한 번 실행한다."""
    packet = _load_packet(
        packet_path,
        now=now,
        expected_approval_id=expected_approval_id,
        expected_packet_sha256=expected_packet_sha256,
        repository_root=repository_root,
    )
    resolved_root = repository_root.resolve(strict=True)
    if isinstance(packet, KISMockApprovalPacketV2):
        # author 이후 rerun/close된 PR을 claim 이전에 다시 확인해 stale CI를 실행권한으로 쓰지 않는다.
        _require_current_v2_pr_evidence(packet, resolved_root)
        _require_recovery_source_outcome(packet)
    approval_consumer(packet, now)
    try:
        operations = operations_factory(packet)
    except KISMockApprovalRejected:
        raise
    except Exception as exception:
        raise _probe_failure(
            "runtimeInit",
            {"tokenP": 0, "brokerage": 0},
            exception,
        ) from None
    completed: list[str] = []
    failure: KISMockProbeFailed | None = None
    deadline_rejection: KISMockApprovalRejected | None = None
    failed_step_for_outcome: str | None = None
    record_outcome = (
        isinstance(packet, KISMockApprovalPacketV2)
        and packet.probe_type != "BALANCE_DIAGNOSTIC"
    )
    outcome_recording_active = False
    try:
        activation = getattr(operations, "activate", None)
        if callable(activation):
            activation(packet)
        outcome_recording_active = True
        for operation in packet.steps:
            try:
                _require_packet_inside_ttl(packet, _clock_now(clock, now))
                operations.run(operation, packet)
            except KISMockApprovalRejected as exception:
                deadline_rejection = exception
                failed_step_for_outcome = operation
                break
            except Exception as exception:
                failure = _probe_failure(
                    operation,
                    operations.counts(),
                    exception,
                )
                failed_step_for_outcome = operation
                break
            completed.append(operation)
        counts = operations.counts()
    finally:
        if record_outcome and outcome_recording_active:
            outcome_recorder = getattr(operations, "record_outcome", None)
            if not callable(outcome_recorder):
                if failure is None and deadline_rejection is None:
                    failure = KISMockProbeFailed(
                        "outcomeRecord",
                        operations.counts(),
                        reason_code=KISMockFailureReason.RUNTIME_INIT_FAILED.value,
                    )
            else:
                try:
                    outcome_recorder(packet, failed_step_for_outcome)
                except Exception as exception:
                    if failure is None and deadline_rejection is None:
                        failure = _probe_failure(
                            "outcomeRecord",
                            operations.counts(),
                            exception,
                        )
        try:
            operations.close()
        except Exception:
            if failure is None:
                failure = KISMockProbeFailed(
                    "runtimeClose",
                    operations.counts(),
                    reason_code=KISMockFailureReason.RUNTIME_CLOSE_FAILED.value,
                )
    if failure is not None:
        raise failure from None
    if deadline_rejection is not None:
        raise deadline_rejection
    return ProbeSummary(
        approval_id=packet.approval_id,
        completed_steps=tuple(completed),
        physical_reservations=counts,
    )


def _clock_now(clock: Callable[[], datetime] | None, initial: datetime) -> datetime:
    """CLI는 real UTC clock을 주입하고 deterministic tests는 승인 시각을 명시적으로 고정한다."""

    current = clock() if clock is not None else initial
    if current.tzinfo is None:
        raise KISMockApprovalRejected("approval clock must be timezone-aware")
    return current.astimezone(UTC)


def _require_packet_inside_ttl(packet: ApprovalPacket, current: datetime) -> None:
    """각 operation과 transport handoff는 같은 expiry를 넘기면 physical reservation 전에 거부한다."""

    if current < packet.issued_at or current >= packet.expires_at:
        raise KISMockApprovalRejected("approval packet is not inside its TTL")


def _load_packet(
    packet_path: Path,
    *,
    now: datetime,
    expected_approval_id: str | None,
    expected_packet_sha256: str | None,
    repository_root: Path,
) -> ApprovalPacket:
    if expected_approval_id is None or expected_packet_sha256 is None:
        raise KISMockApprovalRejected("exact current-user approval latch is missing")
    if now.tzinfo is None:
        raise KISMockApprovalRejected("approval clock must be timezone-aware")
    resolved_packet, packet_bytes = _read_secure_packet(packet_path)
    try:
        raw: object = json.loads(
            packet_bytes,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise KISMockApprovalRejected("approval packet is invalid") from None
    if not isinstance(raw, dict):
        raise KISMockApprovalRejected("approval packet is invalid")
    document = cast(dict[str, Any], raw)
    supplied_digest = document.get("packetSha256")
    unsigned = dict(document)
    unsigned.pop("packetSha256", None)
    computed_digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if supplied_digest != computed_digest or expected_packet_sha256 != computed_digest:
        raise KISMockApprovalRejected("approval packet digest does not match")
    packet = parse_approval_packet(document)
    if isinstance(packet, KISMockApprovalPacketV2):
        secured_packet, secured_bytes = _read_secure_packet_v2(packet_path)
        if not hmac.compare_digest(secured_bytes, packet_bytes):
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        resolved_packet = secured_packet
    if packet.approval_id != expected_approval_id:
        raise KISMockApprovalRejected("exact current-user approval latch does not match")
    current = now.astimezone(UTC)
    if current < packet.issued_at or current >= packet.expires_at:
        raise KISMockApprovalRejected("approval packet is not inside its TTL")

    resolved_root = repository_root.resolve(strict=True)
    if Path(packet.repository.root).resolve(strict=True) != resolved_root:
        raise KISMockApprovalRejected("approval repository root does not match")
    expected_command = (
        "uv run --directory "
        f"{resolved_root}/workspaces/decision-platform/python-services "
        "--frozen kis-mock-brokerage-probe "
        f"--approval-packet {resolved_packet}"
    )
    if packet.execution_command != expected_command:
        raise KISMockApprovalRejected("approval execution command does not match")
    if _git_revision(resolved_root, "HEAD") != packet.repository.head_sha:
        raise KISMockApprovalRejected("approval HEAD does not match")
    remote_ref = f"refs/remotes/origin/{packet.repository.branch_ref}"
    if _git_revision(resolved_root, remote_ref) != packet.repository.remote_head_sha:
        raise KISMockApprovalRejected("approval remote HEAD does not match")
    if isinstance(packet, KISMockApprovalPacketV2):
        _validate_v2_security_evidence(packet.evidence, packet.repository.head_sha)
    else:
        _validate_security_report(packet.evidence)
    _require_clean_repository(resolved_root)
    return packet


def _require_current_v2_pr_evidence(
    packet: KISMockApprovalPacketV2,
    repository_root: Path,
) -> None:
    """provider 실행 직전 packet mode에 맞는 GitHub SHA와 green checks를 다시 확인한다."""

    if packet.repository.evidence_mode == "MERGED_MAIN":
        _require_current_v2_merged_main_evidence(packet, repository_root)
        return

    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(packet.repository.pull_request),
                "--json",
                "number,state,isDraft,headRefName,baseRefName,headRefOid,statusCheckRollup",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        raw: object = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise KISMockApprovalRejected("PR evidence is unavailable") from None
    if not isinstance(raw, dict):
        raise KISMockApprovalRejected("PR evidence is invalid")
    if (
        raw.get("number") != packet.repository.pull_request
        or raw.get("state") != "OPEN"
        or raw.get("isDraft") is not False
        or raw.get("headRefName") != packet.repository.branch_ref
        or raw.get("baseRefName") != packet.repository.base_ref
        or raw.get("headRefOid") != packet.repository.head_sha
    ):
        raise KISMockApprovalRejected("PR evidence is no longer active")
    rollup = raw.get("statusCheckRollup")
    if not isinstance(rollup, list):
        raise KISMockApprovalRejected("PR checks are unavailable")
    check_by_name: dict[str, str] = {}
    for item in rollup:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        conclusion = item.get("conclusion")
        if isinstance(name, str) and isinstance(conclusion, str):
            check_by_name[name] = conclusion
    if any(check_by_name.get(name) != "SUCCESS" for name in _REQUIRED_CI_CHECKS):
        raise KISMockApprovalRejected("PR required checks are no longer successful")


def _require_current_v2_merged_main_evidence(
    packet: KISMockApprovalPacketV2,
    repository_root: Path,
) -> None:
    """merged implementation PR와 그 merge SHA의 post-merge check-runs를 claim 직전에 재검증한다."""

    head = packet.repository.head_sha
    try:
        pull_request_result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(packet.repository.pull_request),
                "--json",
                "number,state,isDraft,baseRefName,mergeCommit",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        main_ref_result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                "repos/{owner}/{repo}/git/ref/heads/main",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        checks_result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs",
                "-f",
                "filter=latest",
                "-f",
                "per_page=100",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pull_request: object = json.loads(pull_request_result.stdout)
        main_ref: object = json.loads(main_ref_result.stdout)
        checks: object = json.loads(checks_result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise KISMockApprovalRejected("merged main evidence is unavailable") from None
    if not isinstance(pull_request, dict):
        raise KISMockApprovalRejected("merged main evidence is invalid")
    if not isinstance(main_ref, dict):
        raise KISMockApprovalRejected("remote main evidence is invalid")
    main_ref_object = main_ref.get("object")
    if not isinstance(main_ref_object, dict) or main_ref_object.get("sha") != head:
        raise KISMockApprovalRejected("remote main no longer matches execution HEAD")
    merge_commit = pull_request.get("mergeCommit")
    if (
        pull_request.get("number") != packet.repository.pull_request
        or pull_request.get("state") != "MERGED"
        or pull_request.get("isDraft") is not False
        or pull_request.get("baseRefName") != "main"
        or not isinstance(merge_commit, dict)
        or merge_commit.get("oid") != head
    ):
        raise KISMockApprovalRejected("merged main evidence no longer matches")
    successful_names = _successful_post_merge_check_names(checks, head=head)
    if not _REQUIRED_CI_CHECKS.issubset(successful_names):
        raise KISMockApprovalRejected("post-merge checks are incomplete")


def _successful_post_merge_check_names(document: object, *, head: str) -> frozenset[str]:
    """Only successful check-runs whose head_sha is the exact execution SHA count as CI evidence."""

    if not isinstance(document, dict):
        return frozenset()
    check_runs = document.get("check_runs")
    if not isinstance(check_runs, list):
        return frozenset()
    return frozenset(
        name
        for item in check_runs
        if isinstance(item, dict)
        and item.get("head_sha") == head
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        and isinstance((app := item.get("app")), dict)
        and app.get("slug") == "github-actions"
        and isinstance((name := item.get("name")), str)
    )


def _require_recovery_source_outcome(packet: KISMockApprovalPacketV2) -> None:
    """recovery packet은 CLI failedStep이 아니라 encrypted source executor receipt와 먼저 대조한다."""

    if packet.probe_type != "CANCEL_RECOVERY":
        return
    assert packet.recovery_of is not None
    try:
        encryption_key = _operator_approval_value("KIS_MOCK_ORDER_REFERENCE_KEY")
    except KISMockApprovalRejected:
        raise KISMockApprovalRejected("recovery source outcome is unavailable") from None
    redis_client: Any | None = None
    try:
        redis_client = _build_redis_client()
        store = EncryptedRedisApprovalOutcomeStore(
            redis_client,
            encryption_key=SecretStr(encryption_key),
            ttl_seconds=packet.reference_ttl_seconds,
        )
        store.require_recovery(
            source_approval_id=packet.recovery_of.source_approval_id,
            source_packet_sha256=packet.recovery_of.source_packet_sha256,
            source_nonce=packet.recovery_of.source_nonce,
            expected_failed_step=packet.recovery_of.failed_step,
            order_id=packet.order.order_id,
            account_id=packet.order.account_id,
        )
    except (KISMockApprovalOutcomeUnavailable, ValueError):
        raise KISMockApprovalRejected("recovery source outcome does not match") from None
    except Exception:
        raise KISMockApprovalRejected("recovery source outcome is unavailable") from None
    finally:
        encryption_key = ""
        if redis_client is not None:
            redis_client.close()


def parse_approval_packet(document: dict[str, Any]) -> ApprovalPacket:
    """schemaVersion discriminator로 v1 history packet과 dynamic v2 packet을 분리 검증한다."""

    schema_version = document.get("schemaVersion")
    try:
        if schema_version == 1:
            return KISMockApprovalPacket.model_validate(document)
        if schema_version == 2:
            return KISMockApprovalPacketV2.model_validate(document)
    except Exception:
        pass
    raise KISMockApprovalRejected("approval packet contract is invalid")


def approval_anchor_for_source(source_packet_sha256: str, source_nonce: str) -> str:
    """recovery packet이 특정 FULL packet의 encrypted reference만 찾도록 비밀 없는 anchor를 만든다."""

    return hashlib.sha256(f"{source_packet_sha256}\0{source_nonce}".encode()).hexdigest()


def _consume_exact_approval_once(packet: ApprovalPacket, now: datetime) -> None:
    """Redis 원자 claim으로 exact packet 재실행을 성공/실패와 무관하게 차단한다."""
    current = now.astimezone(UTC)
    remaining_ms = int((packet.expires_at - current).total_seconds() * 1000)
    if remaining_ms <= 0:
        raise KISMockApprovalRejected("approval packet is not inside its TTL")
    key_material = f"{packet.approval_id}\0{packet.packet_sha256}".encode()
    version = "v2" if isinstance(packet, KISMockApprovalPacketV2) else "v1"
    redis_key = (
        f"kis:mock:approval-consumed:{version}:" + hashlib.sha256(key_material).hexdigest()
    )
    redis_client: Any | None = None
    try:
        redis_client = _build_redis_client()
        claimed = redis_client.set(redis_key, "1", nx=True, px=remaining_ms)
    except Exception:
        raise KISMockApprovalRejected("approval consumption state is unavailable") from None
    finally:
        if redis_client is not None:
            redis_client.close()
    if claimed is not True:
        raise KISMockApprovalRejected("approval packet was already consumed")


def _read_secure_packet(packet_path: Path) -> tuple[Path, bytes]:
    """v1 historical packet reader를 유지한다; v2는 뒤의 parent-dirfd verification을 추가로 거친다."""

    if not packet_path.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        raise KISMockApprovalRejected("approval packet file boundary is invalid")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(packet_path, flags)
    except OSError:
        raise KISMockApprovalRejected("approval packet file boundary is invalid") from None
    try:
        packet_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(packet_stat.st_mode)
            or stat.S_IMODE(packet_stat.st_mode) != 0o600
            or packet_stat.st_uid != os.getuid()
            or packet_stat.st_size > _MAX_PACKET_BYTES
        ):
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        chunks: list[bytes] = []
        remaining = _MAX_PACKET_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        packet_bytes = b"".join(chunks)
        if len(packet_bytes) > _MAX_PACKET_BYTES:
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        descriptor_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if not descriptor_path.is_absolute() or descriptor_path.name.endswith(" (deleted)"):
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        return descriptor_path, packet_bytes
    except OSError:
        raise KISMockApprovalRejected("approval packet file boundary is invalid") from None
    finally:
        os.close(descriptor)


def _read_secure_packet_v2(packet_path: Path) -> tuple[Path, bytes]:
    """v2 current operator packet은 parent/leaf 모두 dirfd no-follow boundary로 다시 읽는다."""

    if not packet_path.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        raise KISMockApprovalRejected("approval packet file boundary is invalid")
    parent_descriptor = _open_secure_packet_parent(packet_path.parent)
    descriptor: int | None = None
    try:
        name = packet_path.name
        if name in {"", ".", ".."} or "/" in name:
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        packet_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(packet_stat.st_mode)
            or stat.S_IMODE(packet_stat.st_mode) != 0o600
            or packet_stat.st_uid != os.getuid()
            or packet_stat.st_nlink != 1
            or packet_stat.st_size > _MAX_PACKET_BYTES
        ):
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        chunks: list[bytes] = []
        remaining = _MAX_PACKET_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        packet_bytes = b"".join(chunks)
        if len(packet_bytes) > _MAX_PACKET_BYTES:
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        descriptor_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if not descriptor_path.is_absolute() or descriptor_path.name.endswith(" (deleted)"):
            raise KISMockApprovalRejected("approval packet file boundary is invalid")
        return descriptor_path, packet_bytes
    except OSError:
        raise KISMockApprovalRejected("approval packet file boundary is invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _open_secure_packet_parent(directory: Path) -> int:
    """packet parent 전체를 no-follow dirfd로 열어 symlink replacement race를 outbound 전에 닫는다."""

    if not directory.is_absolute() or any(part in {"", ".", ".."} for part in directory.parts):
        raise KISMockApprovalRejected("approval packet file boundary is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open("/", flags)
        for component in directory.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        directory_stat = os.fstat(descriptor)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise KISMockApprovalRejected("approval packet file boundary is invalid") from None
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.getuid()
        or stat.S_IMODE(directory_stat.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise KISMockApprovalRejected("approval packet file boundary is invalid")
    return descriptor


def _validate_security_report(evidence: ApprovalEvidence) -> None:
    try:
        report_path = Path(evidence.security_report_path).resolve(strict=True)
        report_stat = report_path.stat()
        if not stat.S_ISREG(report_stat.st_mode) or report_stat.st_size > 4 * 1024 * 1024:
            raise OSError
        digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    except OSError:
        raise KISMockApprovalRejected("security report evidence is unavailable") from None
    if digest != evidence.security_report_sha256:
        raise KISMockApprovalRejected("security report evidence digest does not match")


def _validate_v2_security_evidence(evidence: ApprovalEvidenceV2, head_sha: str) -> None:
    """sealed scan receipt가 current HEAD·complete coverage·zero findings를 함께 증명할 때만 packet을 연다."""

    _validate_security_report(evidence)
    manifest_bytes = _read_security_evidence_file(
        evidence.security_manifest_path,
        evidence.security_manifest_sha256,
    )
    coverage_bytes = _read_security_evidence_file(
        evidence.security_coverage_path,
        evidence.security_coverage_sha256,
    )
    findings_bytes = _read_security_evidence_file(
        evidence.security_findings_path,
        evidence.security_findings_sha256,
    )
    try:
        manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_object)
        coverage = json.loads(coverage_bytes, object_pairs_hook=_unique_object)
        findings = json.loads(findings_bytes, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise KISMockApprovalRejected("security scan evidence is invalid") from None
    if not isinstance(manifest, dict) or not isinstance(coverage, dict) or not isinstance(
        findings, dict
    ):
        raise KISMockApprovalRejected("security scan evidence is invalid")
    scan = manifest.get("scan")
    if not isinstance(scan, dict):
        raise KISMockApprovalRejected("security scan evidence is invalid")
    target = scan.get("target")
    if (
        scan.get("status") != "completed"
        or not isinstance(target, dict)
        or target.get("kind") != "git_revision"
        or target.get("revision") != head_sha
        or scan.get("coverageRef") != Path(evidence.security_coverage_path).name
        or scan.get("findingsRef") != Path(evidence.security_findings_path).name
    ):
        raise KISMockApprovalRejected("security scan evidence does not bind final HEAD")
    scan_id = scan.get("id")
    if (
        not isinstance(scan_id, str)
        or coverage.get("completeness") != "complete"
        or coverage.get("scanId") != scan_id
        or findings.get("findings") != []
    ):
        raise KISMockApprovalRejected("security scan coverage or findings are incomplete")
    artifacts = scan.get("artifacts")
    if not isinstance(artifacts, list) or not _manifest_artifact_digest_matches(
        artifacts,
        Path(evidence.security_coverage_path).name,
        evidence.security_coverage_sha256,
    ) or not _manifest_artifact_digest_matches(
        artifacts,
        Path(evidence.security_findings_path).name,
        evidence.security_findings_sha256,
    ):
        raise KISMockApprovalRejected("security scan evidence digest does not match")


def _read_security_evidence_file(path_text: str, expected_digest: str) -> bytes:
    try:
        path = Path(path_text).resolve(strict=True)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4 * 1024 * 1024:
            raise OSError
        content = path.read_bytes()
    except OSError:
        raise KISMockApprovalRejected("security scan evidence is unavailable") from None
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise KISMockApprovalRejected("security scan evidence digest does not match")
    return content


def _manifest_artifact_digest_matches(
    artifacts: list[object],
    expected_path: str,
    expected_digest: str,
) -> bool:
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("path") == expected_path and artifact.get("sha256") == expected_digest:
            return True
    return False


def _git_revision(repository_root: Path, ref: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        raise KISMockApprovalRejected("repository revision evidence is unavailable") from None
    revision = completed.stdout.strip()
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise KISMockApprovalRejected("repository revision evidence is invalid")
    return revision


def _require_clean_repository(repository_root: Path) -> None:
    """ignored local secret는 제외하고 staged·unstaged·untracked 변경을 exact HEAD에서 거부한다."""
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        raise KISMockApprovalRejected("repository clean-tree evidence is unavailable") from None
    if completed.stdout:
        raise KISMockApprovalRejected("repository worktree is not clean")


def _require_bound_account_id(account_id: str) -> str:
    """승인 packet의 opaque account와 KIS_MOCK credential binding을 runtime 생성 전에 맞춘다."""
    bound_account_id = _operator_approval_value("KIS_MOCK_BOUND_ACCOUNT_ID")
    if re.fullmatch(_ACCOUNT_ID, bound_account_id) is None or not hmac.compare_digest(
        account_id, bound_account_id
    ):
        raise KISMockApprovalRejected("KIS_MOCK bound account does not match")
    return bound_account_id


def _operator_approval_value(name: str) -> str:
    """exact probe의 operator-only latch는 ignored root `.env` boundary 밖에서 받지 않는다."""

    try:
        return load_kis_mock_approval_environment(name)[name]
    except KISMockApprovalEnvironmentRejected:
        raise KISMockApprovalRejected("operator approval environment is unavailable") from None


class _KISMockProbeOperations:
    """production mock transport, encrypted reference store와 parser를 그대로 실행한다."""

    def __init__(self, packet: ApprovalPacket) -> None:
        _require_bound_account_id(packet.order.account_id)
        encryption_key = ""
        if packet.probe_type != "BALANCE_DIAGNOSTIC":
            try:
                encryption_key = _operator_approval_value("KIS_MOCK_ORDER_REFERENCE_KEY")
            except KISMockApprovalRejected:
                raise KISMockApprovalRejected(
                    "mock reference encryption key is unavailable"
                ) from None
        self._budget = KISBrokerageCallBudget(
            token_p_cap=packet.physical_caps.token_p,
            brokerage_cap=packet.physical_caps.brokerage,
        )
        self._reference_redis: Any | None = None
        self._reference_store: EncryptedRedisOrderReferenceStore | None = None
        self._outcome_store: EncryptedRedisApprovalOutcomeStore | None = None
        self._gateway: KISMockOrderGateway | None = None
        self._execution_reader: KISMockExecutionReader | None = None
        self._submission_anchor: str | None = None
        self._recovery_anchor: str | None = None
        if isinstance(packet, KISMockApprovalPacketV2):
            if packet.probe_type == "FULL":
                self._submission_anchor = approval_anchor_for_source(
                    packet.packet_sha256,
                    packet.nonce,
                )
        self._client = KISMockBrokerageHttpClient(
            settings=KISSettings(kis_mode="mock", kis_offline=False),
            budget=self._budget,
            approval_deadline_guard=lambda: _require_packet_inside_ttl(
                packet,
                datetime.now(tz=UTC),
            ),
        )
        self._balance_reader = KISMockOnlineBalanceReader(self._client)
        if packet.probe_type == "BALANCE_DIAGNOSTIC":
            return
        try:
            self._reference_redis = _build_redis_client()
            self._reference_store = EncryptedRedisOrderReferenceStore(
                self._reference_redis,
                encryption_key=SecretStr(encryption_key),
                ttl_seconds=packet.reference_ttl_seconds,
            )
            if isinstance(packet, KISMockApprovalPacketV2):
                self._outcome_store = EncryptedRedisApprovalOutcomeStore(
                    self._reference_redis,
                    encryption_key=SecretStr(encryption_key),
                    ttl_seconds=packet.reference_ttl_seconds,
                )
                if packet.probe_type == "CANCEL_RECOVERY":
                    assert packet.recovery_of is not None
                    source_outcome = self._outcome_store.require_recovery(
                        source_approval_id=packet.recovery_of.source_approval_id,
                        source_packet_sha256=packet.recovery_of.source_packet_sha256,
                        source_nonce=packet.recovery_of.source_nonce,
                        expected_failed_step=packet.recovery_of.failed_step,
                        order_id=packet.order.order_id,
                        account_id=packet.order.account_id,
                    )
                    # nested recovery도 original FULL reference anchor를 유지해 다른 order reference를 열지 않는다.
                    self._recovery_anchor = source_outcome.reference_anchor
            self._gateway = KISMockOrderGateway(
                self._client,
                mode="mock",
                reference_store=self._reference_store,
            )
            self._execution_reader = KISMockExecutionReader(self._client)
        except Exception:
            self.close()
            raise
        finally:
            encryption_key = ""

    def activate(self, packet: ApprovalPacket) -> None:
        """target packet을 claim한 뒤 recovery source를 한 번만 claim하고 provider dispatch를 연다."""

        if not isinstance(packet, KISMockApprovalPacketV2) or packet.probe_type != "CANCEL_RECOVERY":
            return
        if self._outcome_store is None or packet.recovery_of is None:
            raise KISMockApprovalRejected("recovery source outcome is unavailable")
        try:
            source_outcome = self._outcome_store.require_recovery(
                source_approval_id=packet.recovery_of.source_approval_id,
                source_packet_sha256=packet.recovery_of.source_packet_sha256,
                source_nonce=packet.recovery_of.source_nonce,
                expected_failed_step=packet.recovery_of.failed_step,
                order_id=packet.order.order_id,
                account_id=packet.order.account_id,
            )
            self._outcome_store.claim_recovery(
                source_approval_id=packet.recovery_of.source_approval_id,
                source_packet_sha256=packet.recovery_of.source_packet_sha256,
                source_nonce=packet.recovery_of.source_nonce,
                recovery_packet_sha256=packet.packet_sha256,
            )
        except KISMockApprovalOutcomeUnavailable:
            raise KISMockApprovalRejected("recovery source outcome does not match") from None
        self._recovery_anchor = source_outcome.reference_anchor

    def record_outcome(self, packet: ApprovalPacket, failed_step: str | None) -> None:
        """FULL/recovery 종료를 close 전에 봉인해 다음 recovery가 실제 failure만 참조하게 한다."""

        if not isinstance(packet, KISMockApprovalPacketV2) or packet.probe_type == "BALANCE_DIAGNOSTIC":
            return
        if self._outcome_store is None:
            raise KISMockApprovalRejected("approval source outcome is unavailable")
        reference_anchor = self._submission_anchor or self._recovery_anchor
        if reference_anchor is None:
            raise KISMockApprovalRejected("approval source outcome is unavailable")
        self._outcome_store.record(
            KISMockApprovalOutcome(
                approval_id=packet.approval_id,
                packet_sha256=packet.packet_sha256,
                nonce=packet.nonce,
                probe_type=packet.probe_type,
                order_id=packet.order.order_id,
                account_id=packet.order.account_id,
                reference_anchor=reference_anchor,
                failed_step=failed_step,
            )
        )

    def run(self, operation: str, packet: ApprovalPacket) -> None:
        """canonical operation 이름을 exact packet parameter에만 매핑한다."""
        if operation == "balance":
            balance_response = self._balance_reader.probe_balance_source(
                packet.order.account_id
            )
            if balance_response is None or balance_response.account_id != packet.order.account_id:
                raise KISMockProjectionError(
                    KISMockFailureReason.BALANCE_PROBE_RESPONSE_INVALID,
                    "KIS mock balance probe response is invalid",
                )
            return
        if operation == "buyable":
            buyable_response = self._balance_reader.buyable(
                packet.order.account_id,
                packet.order.symbol,
                packet.order.limit_price_krw,
                packet.order.order_division,
            )
            if (
                buyable_response is None
                or buyable_response.account_id != packet.order.account_id
                or buyable_response.buyable_quantity < packet.order.quantity
                or buyable_response.buyable_amount_krw < packet.order.limit_price_krw
            ):
                raise KISMockProjectionError(
                    KISMockFailureReason.BUYABLE_PROBE_UNFUNDED,
                    "KIS mock buyable probe cannot fund the exact order",
                )
            return
        if operation == "submitLimitBuy":
            if self._gateway is None:
                raise ValueError("KIS mock approval operation is not allowed")
            submit_kwargs: dict[str, str] = {
                "order_id": packet.order.order_id,
                "account_id": packet.order.account_id,
            }
            submission_anchor = getattr(self, "_submission_anchor", None)
            if submission_anchor is not None:
                submit_kwargs["approval_anchor"] = submission_anchor
            order_receipt = self._gateway.submit_cash_order(
                MockOrderIntent(
                    symbol=packet.order.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    quantity=packet.order.quantity,
                    estimated_price=packet.order.limit_price_krw,
                    order_division=packet.order.order_division,
                    exchange_division=packet.order.exchange_division,
                ),
                **submit_kwargs,
            )
            if not order_receipt.accepted:
                raise KISMockProjectionError(
                    KISMockFailureReason.ORDER_PROBE_REJECTED,
                    "KIS mock order probe was not accepted",
                )
            return
        if operation == "cancelFull":
            if self._gateway is None:
                raise ValueError("KIS mock approval operation is not allowed")
            cancel_kwargs: dict[str, str] = {
                "order_id": packet.order.order_id,
                "account_id": packet.order.account_id,
            }
            recovery_anchor = getattr(self, "_recovery_anchor", None)
            if recovery_anchor is not None:
                cancel_kwargs["approval_anchor"] = recovery_anchor
            cancel_receipt = self._gateway.cancel_cash_order(**cancel_kwargs)
            if cancel_receipt.status != "CANCELLED":
                raise KISMockProjectionError(
                    KISMockFailureReason.CANCEL_PROBE_UNCONFIRMED,
                    "KIS mock cancel probe was not confirmed",
                )
            return
        if operation == "executionRead":
            if self._reference_store is None or self._execution_reader is None:
                raise ValueError("KIS mock approval operation is not allowed")
            recovery_anchor = getattr(self, "_recovery_anchor", None)
            if recovery_anchor is None:
                reference = self._reference_store.get(
                    packet.order.order_id,
                    packet.order.account_id,
                )
            else:
                reference = self._reference_store.get_for_recovery(
                    packet.order.order_id,
                    packet.order.account_id,
                    recovery_anchor,
                )
            if reference is None:
                raise KISMockProjectionError(
                    KISMockFailureReason.EXECUTION_REFERENCE_UNAVAILABLE,
                    "KIS mock execution probe reference is unavailable",
                )
            self._execution_reader.probe_execution_source(
                reference=reference,
                start=packet.execution.start,
                end=packet.execution.end,
                recent=packet.execution.recent,
            )
            return
        raise ValueError("KIS mock approval operation is not allowed")

    def counts(self) -> dict[str, int]:
        return self._budget.counts

    def close(self) -> None:
        try:
            self._client.close()
        finally:
            if self._reference_redis is not None:
                self._reference_redis.close()
                self._reference_redis = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute one exact-approved KIS_MOCK brokerage probe",
    )
    parser.add_argument("--approval-packet", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        expected_approval_id = _operator_approval_value("S3_KIS_MOCK_EXACT_APPROVAL_ID")
        expected_packet_sha256 = _operator_approval_value(
            "S3_KIS_MOCK_EXACT_APPROVAL_SHA256"
        )
        summary = execute_approved_probe(
            args.approval_packet,
            now=datetime.now(tz=UTC),
            expected_approval_id=expected_approval_id,
            expected_packet_sha256=expected_packet_sha256,
            repository_root=_REPOSITORY_ROOT,
            operations_factory=_KISMockProbeOperations,
            approval_consumer=_consume_exact_approval_once,
            clock=lambda: datetime.now(tz=UTC),
        )
    except KISMockApprovalRejected:
        print("S3_KIS_MOCK_APPROVAL_REJECTED", file=sys.stderr)
        return 2
    except KISMockProbeFailed as exception:
        failure_payload: dict[str, object] = {
            "status": "FAILED",
            "failedStep": exception.failed_step,
            "reasonCode": exception.reason_code,
            "physicalReservations": exception.physical_reservations,
            "artifactWrites": 0,
            "retryCount": 0,
        }
        if exception.provider_code is not None:
            failure_payload["providerCode"] = exception.provider_code
        if exception.http_status is not None:
            failure_payload["httpStatus"] = exception.http_status
        print(
            json.dumps(
                failure_payload,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "SUCCESS",
                "approvalId": summary.approval_id,
                "completedSteps": summary.completed_steps,
                "physicalReservations": summary.physical_reservations,
                "artifactWrites": 0,
                "retryCount": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _probe_failure(
    failed_step: str,
    physical_reservations: dict[str, int],
    exception: Exception,
) -> KISMockProbeFailed:
    """untrusted 예외 문자열을 버리고 typed allowlist만 operator 출력으로 승격한다."""
    if isinstance(
        exception,
        (KISMockBrokerageError, KISMockProjectionError, MockOrderRecoveryError),
    ):
        return KISMockProbeFailed(
            failed_step,
            physical_reservations,
            reason_code=exception.reason_code,
            provider_code=exception.provider_code,
            http_status=exception.http_status,
        )
    if isinstance(exception, KISBrokerageCallBudgetExceeded):
        reason = KISMockFailureReason.CALL_BUDGET_EXCEEDED
    elif isinstance(exception, KISCredentialError):
        reason = KISMockFailureReason.CREDENTIAL_UNAVAILABLE
    else:
        reason = (
            KISMockFailureReason.RUNTIME_INIT_FAILED
            if failed_step == "runtimeInit"
            else KISMockFailureReason.UNCLASSIFIED_FAILURE
        )
    return KISMockProbeFailed(
        failed_step,
        physical_reservations,
        reason_code=reason.value,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
