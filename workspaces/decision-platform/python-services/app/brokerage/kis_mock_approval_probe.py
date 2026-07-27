"""final HEAD와 exact 승인 latch에 결속된 KIS_MOCK brokerage 단일 probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    KISMockBrokerageHttpClient,
)
from app.brokerage.kis_mock_online_runtime import (
    KISMockExecutionReader,
    KISMockOnlineBalanceReader,
)
from app.brokerage.kis_mock_order_gateway import KISMockOrderGateway, MockOrderIntent
from app.brokerage.mock_order_reference_store import (
    EncryptedRedisOrderReferenceStore,
)
from app.data.kis._credential_transport import _build_redis_client
from app.data.kis.settings import KISSettings

_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
_MAX_PACKET_BYTES = 64 * 1024
_SHA256 = r"^[0-9a-f]{64}$"
_GIT_SHA = r"^[0-9a-f]{40}$"
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
    """첫 실패 지점과 이미 예약된 호출 수만 보존하는 stable probe 오류다."""

    def __init__(
        self,
        failed_step: str,
        physical_reservations: dict[str, int],
    ) -> None:
        super().__init__("KIS_MOCK brokerage probe failed")
        self.failed_step = failed_step
        self.physical_reservations = dict(physical_reservations)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RepositoryEvidence(_StrictModel):
    root: str
    branch_ref: str = Field(alias="branchRef", pattern=_BRANCH)
    head_sha: str = Field(alias="headSha", pattern=_GIT_SHA)
    remote_head_sha: str = Field(alias="remoteHeadSha", pattern=_GIT_SHA)
    pull_request: StrictInt = Field(alias="pullRequest", ge=55, le=55)


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


class PhysicalCaps(_StrictModel):
    token_p: StrictInt = Field(alias="tokenP", ge=1, le=1)
    brokerage: StrictInt = Field(ge=5, le=5)


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


class KISMockApprovalPacket(_StrictModel):
    """KIS_MOCK 5단계 probe 외에는 표현할 수 없는 exact 승인 문서다."""

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
        if self.steps != _CANONICAL_STEPS:
            raise ValueError("approval steps must match the canonical five-step probe")
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


class ProbeOperations(Protocol):
    """승인 executor가 호출할 수 있는 유일한 5단계 runtime 표면이다."""

    def run(self, operation: str, packet: KISMockApprovalPacket) -> None: ...

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
    operations_factory: Callable[[KISMockApprovalPacket], ProbeOperations],
) -> ProbeSummary:
    """모든 local evidence와 current-user latch를 검증한 뒤에만 runtime factory를 만든다."""
    packet = _load_packet(
        packet_path,
        now=now,
        expected_approval_id=expected_approval_id,
        expected_packet_sha256=expected_packet_sha256,
        repository_root=repository_root,
    )
    operations = operations_factory(packet)
    completed: list[str] = []
    failure: KISMockProbeFailed | None = None
    try:
        for operation in packet.steps:
            try:
                operations.run(operation, packet)
            except Exception:
                failure = KISMockProbeFailed(operation, operations.counts())
                break
            completed.append(operation)
        counts = operations.counts()
    finally:
        try:
            operations.close()
        except Exception:
            if failure is None:
                failure = KISMockProbeFailed("runtimeClose", operations.counts())
    if failure is not None:
        raise failure from None
    return ProbeSummary(
        approval_id=packet.approval_id,
        completed_steps=tuple(completed),
        physical_reservations=counts,
    )


def _load_packet(
    packet_path: Path,
    *,
    now: datetime,
    expected_approval_id: str | None,
    expected_packet_sha256: str | None,
    repository_root: Path,
) -> KISMockApprovalPacket:
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
    if (
        supplied_digest != computed_digest
        or expected_packet_sha256 != computed_digest
    ):
        raise KISMockApprovalRejected("approval packet digest does not match")
    try:
        packet = KISMockApprovalPacket.model_validate(document)
    except Exception:
        raise KISMockApprovalRejected("approval packet contract is invalid") from None
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
    _validate_security_report(packet.evidence)
    return packet


def _read_secure_packet(packet_path: Path) -> tuple[Path, bytes]:
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


class _KISMockProbeOperations:
    """production mock transport, encrypted reference store와 parser를 그대로 실행한다."""

    def __init__(self, packet: KISMockApprovalPacket) -> None:
        encryption_key = os.environ.get("KIS_MOCK_ORDER_REFERENCE_KEY", "").strip()
        if not encryption_key:
            raise KISMockApprovalRejected("mock reference encryption key is unavailable")
        self._budget = KISBrokerageCallBudget(
            token_p_cap=packet.physical_caps.token_p,
            brokerage_cap=packet.physical_caps.brokerage,
        )
        self._client = KISMockBrokerageHttpClient(
            settings=KISSettings(kis_mode="mock", kis_offline=False),
            budget=self._budget,
        )
        self._reference_redis: Any | None = None
        try:
            self._reference_redis = _build_redis_client()
            self._reference_store = EncryptedRedisOrderReferenceStore(
                self._reference_redis,
                encryption_key=SecretStr(encryption_key),
                ttl_seconds=packet.reference_ttl_seconds,
            )
            self._gateway = KISMockOrderGateway(
                self._client,
                mode="mock",
                reference_store=self._reference_store,
            )
            self._balance_reader = KISMockOnlineBalanceReader(self._client)
            self._execution_reader = KISMockExecutionReader(self._client)
        except Exception:
            self.close()
            raise
        finally:
            encryption_key = ""

    def run(self, operation: str, packet: KISMockApprovalPacket) -> None:
        """canonical operation 이름을 exact packet parameter에만 매핑한다."""
        if operation == "balance":
            balance_response = self._balance_reader.balance(packet.order.account_id)
            if (
                balance_response is None
                or balance_response.account_id != packet.order.account_id
            ):
                raise ValueError("KIS mock balance probe response is invalid")
            return
        if operation == "buyable":
            buyable_response = self._balance_reader.buyable(
                packet.order.account_id,
                packet.order.symbol,
                packet.order.limit_price_krw,
            )
            if (
                buyable_response is None
                or buyable_response.account_id != packet.order.account_id
                or buyable_response.buyable_quantity < packet.order.quantity
                or buyable_response.buyable_amount_krw < packet.order.limit_price_krw
            ):
                raise ValueError("KIS mock buyable probe cannot fund the exact order")
            return
        if operation == "submitLimitBuy":
            order_receipt = self._gateway.submit_cash_order(
                MockOrderIntent(
                    symbol=packet.order.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    quantity=packet.order.quantity,
                    estimated_price=packet.order.limit_price_krw,
                ),
                order_id=packet.order.order_id,
                account_id=packet.order.account_id,
            )
            if not order_receipt.accepted:
                raise ValueError("KIS mock order probe was not accepted")
            return
        if operation == "cancelFull":
            cancel_receipt = self._gateway.cancel_cash_order(
                order_id=packet.order.order_id,
                account_id=packet.order.account_id,
            )
            if cancel_receipt.status != "CANCELLED":
                raise ValueError("KIS mock cancel probe was not confirmed")
            return
        if operation == "executionRead":
            reference = self._reference_store.get(
                packet.order.order_id,
                packet.order.account_id,
            )
            if reference is None:
                raise ValueError("KIS mock execution probe reference is unavailable")
            self._execution_reader.read(
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
        summary = execute_approved_probe(
            args.approval_packet,
            now=datetime.now(tz=UTC),
            expected_approval_id=os.environ.get("S3_KIS_MOCK_EXACT_APPROVAL_ID"),
            expected_packet_sha256=os.environ.get(
                "S3_KIS_MOCK_EXACT_APPROVAL_SHA256"
            ),
            repository_root=_REPOSITORY_ROOT,
            operations_factory=_KISMockProbeOperations,
        )
    except KISMockApprovalRejected:
        print("S3_KIS_MOCK_APPROVAL_REJECTED", file=sys.stderr)
        return 2
    except KISMockProbeFailed as exception:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "failedStep": exception.failed_step,
                    "physicalReservations": exception.physical_reservations,
                    "artifactWrites": 0,
                    "retryCount": 0,
                },
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
