"""Pre-S5 foreign-news provider의 packet-gated transient fetch 경계다.

Finnhub personal-local, SEC official, Federal Reserve official만 fixed endpoint에서 one-shot으로
읽는다. 본문·headline·summary·URL·header·credential·query는 메모리의 bounded parser/analyzer를
떠나지 않으며, local receipt에는 content-free outcome만 남긴다. GDELT HTTP transport는 이 module에
없고 existing offline reference aggregate를 계속 재사용한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import quote

from app.cross_market.foreign_news import ForeignNewsTransientLaneAggregate
from app.rag.oa112_downloader import (
    Oa112DownloadError,
    _Oa112SourceDeadline,
    _open_private_root,
    _read_private_control_file,
    _resolve_public_addresses,
    _SocketOa112DnsResolver,
    _StdlibOa112HttpsTransport,
    _validate_peer,
    _write_new_private_file,
)

_CONTRACT_ID: Final[str] = "foreign-news-provider-probe-approval-v1"
_RECEIPT_CONTRACT_ID: Final[str] = "foreign-news-provider-probe-receipt-v1"
_MAX_PACKET_BYTES: Final[int] = 16 * 1024
_MAX_RESPONSE_BYTES: Final[int] = 256 * 1024
_REQUEST_TIMEOUT_SECONDS: Final[float] = 10.0
_MAX_ARTICLES: Final[int] = 32
_MAX_TEXT_BYTES: Final[int] = 16 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_APPROVAL_ID = re.compile(r"^fnp_[a-z0-9]{32,64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_OPERATOR = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
# 공개 foreign-news response와 같은 symbol alphabet을 써야 005930/005930.KS 같은 국내 종목도
# fixed Finnhub company-news request 또는 official-release lane의 packet 단계에서 막히지 않는다.
_SYMBOL = re.compile(r"^[0-9A-Z._:-]{1,20}$")
_DATE = re.compile(r"^(?:NONE|[0-9]{4}-[0-9]{2}-[0-9]{2})$")
_LEAF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_USER_AGENT = re.compile(r"^[ -~]{3,256}$")


class ForeignNewsProviderProbeError(ValueError):
    """packet/evidence/one-shot provider boundary가 깨졌음을 typed code로 전달한다."""

    def __init__(self, code: str, *, physical_call_count: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.physical_call_count = physical_call_count


class ForeignNewsTransientSentimentAnalyzer(Protocol):
    """selected local model만 bounded transient text를 소비하는 one-way analyzer port다."""

    def analyze(self, *, lane_id: str, texts: tuple[str, ...]) -> None:
        """예측은 in-memory에서 폐기하며 raw text나 label을 storage/receipt로 내보내지 않는다."""


class ForeignNewsProviderProbeTransport(Protocol):
    """fixed-origin one-request transport다. caller는 provider hostname을 packet으로 정하지 못한다."""

    def get(
        self,
        *,
        operation: str,
        hostname: str,
        target: str,
        api_key: str | None,
        user_agent: str,
        expires_at: datetime,
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> ForeignNewsProviderProbeHttpResponse: ...


@dataclass(frozen=True, slots=True)
class ForeignNewsProviderProbeHttpResponse:
    """bounded raw response가 executor의 transient parser 경계를 넘는 최소 carrier다."""

    status_code: int
    body: bytes
    content_type: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
            or not isinstance(self.body, bytes)
            or len(self.body) > _MAX_RESPONSE_BYTES
            or self.content_type not in {"application/json", "text/html"}
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_INVALID")


@dataclass(frozen=True, slots=True)
class _FixedRequestPlan:
    """plan은 origin/endpoint/mime/credential mode를 code-owned immutable map으로 고정한다."""

    provider_family: str
    lane_id: str
    operation: str
    hostname: str
    expected_content_type: str
    requires_date: bool
    api_key_environment_variable: str | None
    target_template: str

    def target(self, *, symbol: str, date: str) -> str:
        """symbol/date only canonical request plan에 넣고 credential은 transport 순간에만 append한다."""

        _validate_symbol_date(symbol=symbol, date=date)
        if self.requires_date:
            if date == "NONE":
                raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_DATE_REQUIRED")
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError as error:
                raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_DATE_INVALID") from error
        elif date != "NONE":
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_DATE_OPERATION_INVALID")
        return self.target_template.format(symbol=symbol, date=date)

    def parse_transient_texts(
        self, response: ForeignNewsProviderProbeHttpResponse
    ) -> tuple[str, ...]:
        """provider body를 bounded local analyzer input으로만 축소하고 object metadata를 discard한다."""

        if response.content_type != self.expected_content_type:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
        if self.expected_content_type == "application/json":
            return _finnhub_transient_texts(response.body)
        return _official_html_transient_texts(response.body)


_PLANS: Final[dict[str, _FixedRequestPlan]] = {
    "FINNHUB_MARKET_NEWS": _FixedRequestPlan(
        provider_family="FINNHUB_PERSONAL_LOCAL",
        lane_id="FINNHUB_PERSONAL_LOCAL",
        operation="FINNHUB_MARKET_NEWS",
        hostname="finnhub.io",
        expected_content_type="application/json",
        requires_date=False,
        api_key_environment_variable="FINNHUB_API_KEY",
        target_template="/api/v1/news?category=general",
    ),
    "FINNHUB_COMPANY_NEWS": _FixedRequestPlan(
        provider_family="FINNHUB_PERSONAL_LOCAL",
        lane_id="FINNHUB_PERSONAL_LOCAL",
        operation="FINNHUB_COMPANY_NEWS",
        hostname="finnhub.io",
        expected_content_type="application/json",
        requires_date=True,
        api_key_environment_variable="FINNHUB_API_KEY",
        target_template="/api/v1/company-news?from={date}&symbol={symbol}&to={date}",
    ),
    "SEC_OFFICIAL_RELEASES": _FixedRequestPlan(
        provider_family="SEC_OFFICIAL",
        lane_id="SEC_OFFICIAL",
        operation="SEC_OFFICIAL_RELEASES",
        hostname="www.sec.gov",
        expected_content_type="text/html",
        requires_date=False,
        api_key_environment_variable=None,
        target_template="/newsroom/press-releases",
    ),
    "FED_OFFICIAL_RELEASES": _FixedRequestPlan(
        provider_family="FED_OFFICIAL",
        lane_id="FED_OFFICIAL",
        operation="FED_OFFICIAL_RELEASES",
        hostname="www.federalreserve.gov",
        expected_content_type="text/html",
        requires_date=False,
        api_key_environment_variable=None,
        target_template="/newsevents/pressreleases.htm",
    ),
}


@dataclass(frozen=True, slots=True)
class ForeignNewsProviderProbeExecutionBinding:
    """final clean Git tree와 CI/security digest를 packet에 재결속하는 local proof다."""

    ci_digest: str
    head_sha: str
    security_digest: str
    tree_sha256: str

    def __post_init__(self) -> None:
        if (
            not all(
                _SHA256.fullmatch(value) is not None
                for value in (self.ci_digest, self.security_digest, self.tree_sha256)
            )
            or _HEAD_SHA.fullmatch(self.head_sha) is None
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_BINDING_INVALID")


@dataclass(frozen=True, slots=True)
class ForeignNewsProviderProbePacket:
    """one fixed external operation만 허용하는 local canonical approval packet이다."""

    approval_id: str
    ci_digest: str
    cost_cap_microusd: int
    date: str
    endpoint_set_digest: str
    expires_at: datetime
    head_sha: str
    logical_call_cap: int
    nonce: str
    operation: str
    operator: str
    physical_call_cap: int
    provider_family: str
    request_plan_digest: str
    retry_count: int
    security_digest: str
    symbol: str
    tracked_raw_artifact_count: int
    tree_sha256: str

    def __post_init__(self) -> None:
        plan = _PLANS.get(self.operation)
        if plan is None or plan.provider_family != self.provider_family:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OPERATION_PROVIDER_INVALID")
        if (
            _APPROVAL_ID.fullmatch(self.approval_id) is None
            or _NONCE.fullmatch(self.nonce) is None
            or _OPERATOR.fullmatch(self.operator) is None
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_FIELD_INVALID")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_EXPIRY_INVALID")
        if self.logical_call_cap != 1 or self.physical_call_cap != 1 or self.retry_count != 0:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_CAP_INVALID")
        if self.tracked_raw_artifact_count != 0 or not 0 <= self.cost_cap_microusd <= 1_000_000:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_ARTIFACT_OR_COST_INVALID")
        if (
            not all(
                _SHA256.fullmatch(value) is not None
                for value in (
                    self.ci_digest,
                    self.endpoint_set_digest,
                    self.request_plan_digest,
                    self.security_digest,
                    self.tree_sha256,
                )
            )
            or _HEAD_SHA.fullmatch(self.head_sha) is None
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_HASH_INVALID")
        if self.endpoint_set_digest != foreign_news_provider_endpoint_set_digest():
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_ENDPOINT_SET_DRIFT")
        if self.request_plan_digest != foreign_news_provider_request_plan_digest(
            operation=self.operation,
            symbol=self.symbol,
            date=self.date,
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_REQUEST_PLAN_DIGEST_INVALID")
        plan.target(symbol=self.symbol, date=self.date)

    def require_current(self, *, now: datetime) -> None:
        """packet은 execution 순간 기준 한 시간 이하 one-shot window여야 한다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_NOW_INVALID")
        now_utc = now.astimezone(UTC)
        expires_at = self.expires_at.astimezone(UTC)
        if not now_utc < expires_at <= now_utc + timedelta(hours=1):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_EXPIRED")

    def require_execution_binding(
        self, *, binding: ForeignNewsProviderProbeExecutionBinding
    ) -> None:
        """same current HEAD/tree proof가 아니면 socket보다 먼저 stop한다."""

        if (
            self.ci_digest != binding.ci_digest
            or self.head_sha != binding.head_sha
            or self.security_digest != binding.security_digest
            or self.tree_sha256 != binding.tree_sha256
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_BINDING_DRIFT")

    def packet_sha256(self) -> str:
        """claim/receipt에는 packet body가 아닌 canonical digest만 보존한다."""

        return _sha256(_canonical_bytes(self.to_local_document()))

    def to_local_document(self) -> dict[str, object]:
        """local-only packet은 credential/body/header를 넣을 slot이 없는 canonical form이다."""

        return {
            "approvalId": self.approval_id,
            "ciDigest": self.ci_digest,
            "costCapMicrousd": self.cost_cap_microusd,
            "date": self.date,
            "endpointSetDigest": self.endpoint_set_digest,
            "expiresAt": _instant(self.expires_at),
            "headSha": self.head_sha,
            "logicalCallCap": self.logical_call_cap,
            "nonce": self.nonce,
            "operation": self.operation,
            "operator": self.operator,
            "physicalCallCap": self.physical_call_cap,
            "providerFamily": self.provider_family,
            "requestPlanDigest": self.request_plan_digest,
            "retryCount": self.retry_count,
            "schemaVersion": 1,
            "securityDigest": self.security_digest,
            "symbol": self.symbol,
            "trackedRawArtifactCount": self.tracked_raw_artifact_count,
            "treeSha256": self.tree_sha256,
        }

    @classmethod
    def from_local_document(cls, document: Mapping[str, object]) -> ForeignNewsProviderProbePacket:
        """unknown field를 거부해 local JSON으로 execution semantics를 넓히지 못하게 한다."""

        expected = {
            "approvalId",
            "ciDigest",
            "costCapMicrousd",
            "date",
            "endpointSetDigest",
            "expiresAt",
            "headSha",
            "logicalCallCap",
            "nonce",
            "operation",
            "operator",
            "physicalCallCap",
            "providerFamily",
            "requestPlanDigest",
            "retryCount",
            "schemaVersion",
            "securityDigest",
            "symbol",
            "trackedRawArtifactCount",
            "treeSha256",
        }
        if set(document) != expected or document.get("schemaVersion") != 1:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_SHAPE_INVALID")
        try:
            return cls(
                approval_id=_required_text(document, "approvalId"),
                ci_digest=_required_text(document, "ciDigest"),
                cost_cap_microusd=_required_integer(document, "costCapMicrousd"),
                date=_required_text(document, "date"),
                endpoint_set_digest=_required_text(document, "endpointSetDigest"),
                expires_at=_parse_instant(_required_text(document, "expiresAt")),
                head_sha=_required_text(document, "headSha"),
                logical_call_cap=_required_integer(document, "logicalCallCap"),
                nonce=_required_text(document, "nonce"),
                operation=_required_text(document, "operation"),
                operator=_required_text(document, "operator"),
                physical_call_cap=_required_integer(document, "physicalCallCap"),
                provider_family=_required_text(document, "providerFamily"),
                request_plan_digest=_required_text(document, "requestPlanDigest"),
                retry_count=_required_integer(document, "retryCount"),
                security_digest=_required_text(document, "securityDigest"),
                symbol=_required_text(document, "symbol"),
                tracked_raw_artifact_count=_required_integer(document, "trackedRawArtifactCount"),
                tree_sha256=_required_text(document, "treeSha256"),
            )
        except ForeignNewsProviderProbeError:
            raise
        except (TypeError, ValueError) as error:
            raise ForeignNewsProviderProbeError(
                "FOREIGN_NEWS_PROBE_PACKET_SHAPE_INVALID"
            ) from error

    @classmethod
    def load_from_control_root(
        cls,
        *,
        control_root: Path,
        relative_path: str,
        now: datetime,
    ) -> ForeignNewsProviderProbePacket:
        """0700 root 안의 canonical 0600 regular leaf만 packet으로 consume한다."""

        if _LEAF.fullmatch(relative_path) is None:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_UNSAFE")
        try:
            content = _read_private_control_file(
                root=control_root,
                name=relative_path,
                maximum=_MAX_PACKET_BYTES,
                error_code="FOREIGN_NEWS_PROBE_PACKET_UNSAFE",
            )
        except Oa112DownloadError as error:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_UNSAFE") from error
        packet = cls.from_local_document(
            _parse_canonical_document(content, code="FOREIGN_NEWS_PROBE_PACKET_CANONICAL_INVALID")
        )
        packet.require_current(now=now)
        return packet


@dataclass(frozen=True, slots=True)
class ForeignNewsProviderProbeReceipt:
    """raw provider material을 영속시키지 않는 one-shot content-free receipt다."""

    approval_id_hash: str
    approval_packet_sha256: str
    completed_at: datetime
    lane_state: str
    logical_call_count: int
    operation: str
    outcome: str
    physical_call_count: int
    projection_hash: str | None
    provider_family: str
    provider_status_class: str
    request_plan_digest: str
    started_at: datetime

    def __post_init__(self) -> None:
        plan = _PLANS.get(self.operation)
        if plan is None or plan.provider_family != self.provider_family:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OPERATION_PROVIDER_INVALID")
        if self.outcome not in {"NOT_EXECUTED", "SUCCESS", "FAILED"} or self.lane_state not in {
            "AVAILABLE",
            "ABSTAIN",
        }:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_OUTCOME_INVALID")
        if self.logical_call_count not in {0, 1} or self.physical_call_count not in {0, 1}:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_CAP_INVALID")
        if self.provider_status_class not in {
            "NOT_ATTEMPTED",
            "HTTP_2XX",
            "HTTP_4XX",
            "HTTP_5XX",
            "TRANSPORT",
            "PROTOCOL",
        }:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_STATUS_INVALID")
        if self.outcome == "NOT_EXECUTED":
            if (
                self.lane_state != "ABSTAIN"
                or self.logical_call_count != 0
                or self.physical_call_count != 0
                or self.provider_status_class != "NOT_ATTEMPTED"
                or self.projection_hash is not None
            ):
                raise ForeignNewsProviderProbeError(
                    "FOREIGN_NEWS_PROBE_RECEIPT_NOT_EXECUTED_INVALID"
                )
        elif self.outcome == "SUCCESS":
            if (
                self.lane_state != "AVAILABLE"
                or self.provider_status_class != "HTTP_2XX"
                or self.projection_hash is None
            ):
                raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_SUCCESS_INVALID")
            if self.logical_call_count != 1 or self.physical_call_count != 1:
                raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_SUCCESS_INVALID")
        elif (
            self.lane_state != "ABSTAIN"
            or self.logical_call_count != 1
            or self.physical_call_count != 1
            or self.projection_hash is not None
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_FAILURE_INVALID")
        if not all(
            _SHA256.fullmatch(value) is not None
            for value in (
                self.approval_id_hash,
                self.approval_packet_sha256,
                self.request_plan_digest,
            )
        ) or (self.projection_hash is not None and _SHA256.fullmatch(self.projection_hash) is None):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_HASH_INVALID")
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_TIME_INVALID")
        if self.completed_at.astimezone(UTC) < self.started_at.astimezone(UTC):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RECEIPT_TIME_INVALID")

    def to_local_document(self) -> dict[str, object]:
        """receipt shape에는 article/query/header/body/credential을 기록할 field가 없다."""

        return {
            "approvalIdHash": self.approval_id_hash,
            "approvalPacketSha256": self.approval_packet_sha256,
            "articleMetadataStored": False,
            "completedAt": _instant(self.completed_at),
            "contractId": _RECEIPT_CONTRACT_ID,
            "decisionAuthority": "NONE",
            "firstFailureStopsRemainingCalls": True,
            "laneState": self.lane_state,
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
            "schemaVersion": 1,
            "startedAt": _instant(self.started_at),
            "state": "EXECUTED",
        }


@dataclass(frozen=True, slots=True)
class ForeignNewsProviderProbeResult:
    """caller가 existing sanitized materializer로 넘길 수 있는 transient aggregate와 receipt pair다."""

    aggregate: ForeignNewsTransientLaneAggregate
    receipt: ForeignNewsProviderProbeReceipt


class ForeignNewsProviderProbeExecutor:
    """packet consume 뒤 exactly one fixed provider handoff만 만드는 boundary다."""

    def __init__(
        self,
        *,
        control_root: Path,
        transport: ForeignNewsProviderProbeTransport,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._control_root = control_root
        self._transport = transport
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        packet: ForeignNewsProviderProbePacket,
        binding: ForeignNewsProviderProbeExecutionBinding,
        api_key: str | None,
        analyzer: ForeignNewsTransientSentimentAnalyzer | None,
        now: datetime,
        user_agent: str,
    ) -> ForeignNewsProviderProbeResult:
        """model gate, packet, evidence를 모두 확인한 뒤 한 request만 만들고 typed receipt를 seal한다."""

        packet.require_current(now=now)
        packet.require_execution_binding(binding=binding)
        if analyzer is None or not callable(getattr(analyzer, "analyze", None)):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_ANALYZER_UNAVAILABLE")
        if (
            _SAFE_USER_AGENT.fullmatch(user_agent) is None
            or "\r" in user_agent
            or "\n" in user_agent
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_USER_AGENT_INVALID")
        plan = _PLANS[packet.operation]
        _validate_api_key(plan=plan, api_key=api_key)
        claim_key = _claim_key(packet)
        self._consume(packet=packet, claim_key=claim_key)
        started_at = now.astimezone(UTC)
        try:
            response = self._transport.get(
                operation=packet.operation,
                hostname=plan.hostname,
                target=plan.target(symbol=packet.symbol, date=packet.date),
                api_key=api_key,
                user_agent=user_agent,
                expires_at=packet.expires_at,
                timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                maximum_response_bytes=_MAX_RESPONSE_BYTES,
            )
        except ForeignNewsProviderProbeError as error:
            return self._seal_failure(
                packet=packet,
                claim_key=claim_key,
                started_at=started_at,
                physical_call_count=error.physical_call_count,
                provider_status_class="TRANSPORT"
                if error.physical_call_count == 1
                else "NOT_ATTEMPTED",
            )
        except Exception:
            # 계약을 따르지 않는 custom transport는 전송 여부를 증명할 수 없으므로 one-shot capacity를 보수적으로 소비한다.
            # production transport는 pre/post-send 경계를 직접 추적해 정확한 0/1 count를 반환한다.
            return self._seal_failure(
                packet=packet,
                claim_key=claim_key,
                started_at=started_at,
                physical_call_count=1,
                provider_status_class="TRANSPORT",
            )

        if 200 <= response.status_code <= 299:
            try:
                texts = plan.parse_transient_texts(response)
                analyzer.analyze(lane_id=plan.lane_id, texts=texts)
            except Exception:
                return self._seal_failure(
                    packet=packet,
                    claim_key=claim_key,
                    started_at=started_at,
                    physical_call_count=1,
                    provider_status_class="PROTOCOL",
                )
            aggregate = ForeignNewsTransientLaneAggregate(
                lane_id=plan.lane_id,
                state="AVAILABLE",
                content_hash=(
                    _sha256(response.body)
                    if plan.lane_id in {"SEC_OFFICIAL", "FED_OFFICIAL"}
                    else None
                ),
                official_release_locator=(
                    plan.operation if plan.lane_id in {"SEC_OFFICIAL", "FED_OFFICIAL"} else None
                ),
            )
            receipt = _receipt(
                packet=packet,
                started_at=started_at,
                completed_at=_completed_at(started_at, self._now_provider()),
                outcome="SUCCESS",
                physical_call_count=1,
                provider_status_class="HTTP_2XX",
                projection_hash=_successful_parse_projection_hash(packet),
            )
            self._write_receipt(claim_key=claim_key, receipt=receipt)
            return ForeignNewsProviderProbeResult(aggregate=aggregate, receipt=receipt)
        return self._seal_failure(
            packet=packet,
            claim_key=claim_key,
            started_at=started_at,
            physical_call_count=1,
            provider_status_class=_status_class(response.status_code),
        )

    def _seal_failure(
        self,
        *,
        packet: ForeignNewsProviderProbePacket,
        claim_key: str,
        started_at: datetime,
        physical_call_count: int,
        provider_status_class: str,
    ) -> ForeignNewsProviderProbeResult:
        plan = _PLANS[packet.operation]
        receipt = _receipt(
            packet=packet,
            started_at=started_at,
            completed_at=_completed_at(started_at, self._now_provider()),
            outcome="NOT_EXECUTED" if physical_call_count == 0 else "FAILED",
            physical_call_count=physical_call_count,
            provider_status_class="NOT_ATTEMPTED"
            if physical_call_count == 0
            else provider_status_class,
            projection_hash=None,
        )
        self._write_receipt(claim_key=claim_key, receipt=receipt)
        return ForeignNewsProviderProbeResult(
            aggregate=ForeignNewsTransientLaneAggregate(
                lane_id=plan.lane_id,
                state="ABSTAIN",
                content_hash=None,
                official_release_locator=None,
            ),
            receipt=receipt,
        )

    def _consume(self, *, packet: ForeignNewsProviderProbePacket, claim_key: str) -> None:
        """O_EXCL claim을 socket보다 먼저 남겨 concurrent packet reuse를 막는다."""

        document = {
            "approvalIdHash": _sha256(packet.approval_id.encode("utf-8")),
            "approvalPacketSha256": packet.packet_sha256(),
            "contractId": _CONTRACT_ID,
            "nonceHash": _sha256(packet.nonce.encode("utf-8")),
            "schemaVersion": 1,
        }
        try:
            root_fd = _open_private_root(
                self._control_root, error_code="FOREIGN_NEWS_PROBE_CONTROL_UNSAFE"
            )
        except Oa112DownloadError as error:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_CONTROL_UNSAFE") from error
        try:
            try:
                _write_new_private_file(
                    root_fd,
                    f"consumed-{claim_key}.json",
                    _canonical_bytes(document),
                )
            except FileExistsError as error:
                raise ForeignNewsProviderProbeError(
                    "FOREIGN_NEWS_PROBE_PACKET_ALREADY_CONSUMED"
                ) from error
            except (OSError, Oa112DownloadError) as error:
                raise ForeignNewsProviderProbeError(
                    "FOREIGN_NEWS_PROBE_CLAIM_UNAVAILABLE"
                ) from error
        finally:
            os.close(root_fd)

    def _write_receipt(self, *, claim_key: str, receipt: ForeignNewsProviderProbeReceipt) -> None:
        """request 후 receipt persist 실패도 exactly-one attempt marker와 함께 fail-closed한다."""

        try:
            root_fd = _open_private_root(
                self._control_root, error_code="FOREIGN_NEWS_PROBE_CONTROL_UNSAFE"
            )
        except Oa112DownloadError as error:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_CONTROL_UNSAFE") from error
        try:
            try:
                _write_new_private_file(
                    root_fd,
                    f"receipt-{claim_key}.json",
                    _canonical_bytes(receipt.to_local_document()),
                )
            except (FileExistsError, OSError, Oa112DownloadError) as error:
                raise ForeignNewsProviderProbeError(
                    "FOREIGN_NEWS_PROBE_RECEIPT_UNAVAILABLE",
                    physical_call_count=receipt.physical_call_count,
                ) from error
        finally:
            os.close(root_fd)


class StdlibForeignNewsProviderProbeTransport:
    """DNS pinning, peer check, redirect/compression rejection을 재사용하는 one request HTTPS adapter다."""

    def __init__(self) -> None:
        self._resolver = _SocketOa112DnsResolver()
        self._transport = _StdlibOa112HttpsTransport()

    def get(
        self,
        *,
        operation: str,
        hostname: str,
        target: str,
        api_key: str | None,
        user_agent: str,
        expires_at: datetime,
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> ForeignNewsProviderProbeHttpResponse:
        """credential query는 process memory에서 only once append하고 body는 bounded transient buffer다."""

        plan = _PLANS.get(operation)
        if (
            plan is None
            or plan.hostname != hostname
            or not _target_matches_plan(plan=plan, target=target)
            or not target.startswith("/")
            or "\r" in target
            or "\n" in target
            or not isinstance(maximum_response_bytes, int)
            or not 1 <= maximum_response_bytes <= _MAX_RESPONSE_BYTES
            or not isinstance(timeout_seconds, float)
            or timeout_seconds <= 0
            or _SAFE_USER_AGENT.fullmatch(user_agent) is None
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_TRANSPORT_ARGUMENT_INVALID")
        _validate_api_key(plan=plan, api_key=api_key)
        secret_target = (
            _target_with_finnhub_credential(target=target, api_key=api_key)
            if plan.api_key_environment_variable is not None
            else target
        )
        sent = False
        try:
            with _Oa112SourceDeadline(expires_at=expires_at) as deadline:
                first_addresses = _resolve_public_addresses(
                    hostname,
                    resolver=self._resolver,
                    deadline=deadline,
                )
                second_addresses = _resolve_public_addresses(
                    hostname,
                    resolver=self._resolver,
                    deadline=deadline,
                )
                if set(first_addresses) != set(second_addresses):
                    raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_DNS_REBINDING")
                with self._transport.connect(
                    hostname=hostname,
                    pinned_ip=first_addresses[0],
                    connect_timeout_seconds=min(timeout_seconds, deadline.remaining_seconds()),
                    read_timeout_seconds=min(timeout_seconds, deadline.remaining_seconds()),
                    deadline=deadline,
                ) as connection:
                    _validate_peer(connection.peer_ip, first_addresses)
                    # `get` can fail after some request bytes leave the socket. Record the physical cap at
                    # this boundary so pre-DNS/pre-connect failures remain exact zero-call receipts.
                    sent = True
                    response = connection.get(
                        target=secret_target,
                        headers={
                            "Accept": plan.expected_content_type,
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                            "Host": hostname,
                            "User-Agent": user_agent,
                        },
                        read_timeout_seconds=min(timeout_seconds, deadline.remaining_seconds()),
                    )
                    content_type = _validate_http_response_boundary(
                        response.headers,
                        expected_content_type=plan.expected_content_type,
                        maximum_response_bytes=maximum_response_bytes,
                    )
                    body = _read_bounded_response_body(
                        response=response,
                        deadline=deadline,
                        timeout_seconds=timeout_seconds,
                        maximum_response_bytes=maximum_response_bytes,
                    )
                    return ForeignNewsProviderProbeHttpResponse(
                        status_code=response.status_code,
                        body=body,
                        content_type=content_type,
                    )
        except ForeignNewsProviderProbeError as error:
            if sent and error.physical_call_count == 0:
                raise ForeignNewsProviderProbeError(error.code, physical_call_count=1) from error
            raise
        except Oa112DownloadError as error:
            raise ForeignNewsProviderProbeError(
                "FOREIGN_NEWS_PROBE_TRANSPORT_UNAVAILABLE",
                physical_call_count=1 if sent else 0,
            ) from error
        except (OSError, TimeoutError) as error:
            raise ForeignNewsProviderProbeError(
                "FOREIGN_NEWS_PROBE_TRANSPORT_UNAVAILABLE",
                physical_call_count=1 if sent else 0,
            ) from error


def foreign_news_provider_endpoint_set_digest() -> str:
    """public packet에는 endpoint strings가 아닌 fixed allowlist digest만 bind한다."""

    projection = [
        {
            "apiKeyEnvironmentVariable": plan.api_key_environment_variable,
            "expectedContentType": plan.expected_content_type,
            "hostname": plan.hostname,
            "laneId": plan.lane_id,
            "operation": plan.operation,
            "providerFamily": plan.provider_family,
            "targetTemplate": plan.target_template,
        }
        for plan in _PLANS.values()
    ]
    return _sha256(_canonical_bytes(projection))


def foreign_news_provider_request_plan_digest(*, operation: str, symbol: str, date: str) -> str:
    """credential 없이 exact provider operation/symbol/date plan identity를 derive한다."""

    plan = _PLANS.get(operation)
    if plan is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_REQUEST_PLAN_INVALID")
    return _sha256(
        _canonical_bytes(
            {
                "date": date,
                "hostname": plan.hostname,
                "operation": plan.operation,
                "providerFamily": plan.provider_family,
                "symbol": symbol,
                "target": plan.target(symbol=symbol, date=date),
            }
        )
    )


def foreign_news_provider_credential_environment_variable(*, operation: str) -> str | None:
    """CLI가 provider-standard secret name만 discover하도록 fixed plan value를 반환한다."""

    plan = _PLANS.get(operation)
    if plan is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OPERATION_PROVIDER_INVALID")
    return plan.api_key_environment_variable


def _validate_api_key(*, plan: _FixedRequestPlan, api_key: str | None) -> None:
    if plan.api_key_environment_variable is None:
        if api_key is not None:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_CREDENTIAL_FORBIDDEN")
        return
    if (
        not isinstance(api_key, str)
        or not api_key.strip()
        or len(api_key) > 4_096
        or "\r" in api_key
        or "\n" in api_key
    ):
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_CREDENTIAL_UNAVAILABLE")


def _target_with_finnhub_credential(*, target: str, api_key: str | None) -> str:
    if api_key is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_CREDENTIAL_UNAVAILABLE")
    return f"{target}{'&' if '?' in target else '?'}token={quote(api_key, safe='')}"


def _target_matches_plan(*, plan: _FixedRequestPlan, target: str) -> bool:
    if plan.operation == "FINNHUB_COMPANY_NEWS":
        match = re.fullmatch(
            r"/api/v1/company-news\?from=(?P<from>[0-9]{4}-[0-9]{2}-[0-9]{2})&symbol=(?P<symbol>[0-9A-Z._:-]{1,20})&to=(?P<to>[0-9]{4}-[0-9]{2}-[0-9]{2})",
            target,
        )
        if match is None or match.group("from") != match.group("to"):
            return False
        try:
            datetime.strptime(match.group("from"), "%Y-%m-%d")
        except ValueError:
            return False
        return _SYMBOL.fullmatch(match.group("symbol")) is not None
    return target == plan.target(symbol="AAPL", date="NONE")


def _finnhub_transient_texts(body: bytes) -> tuple[str, ...]:
    try:
        decoded = body.decode("utf-8", errors="strict")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL") from error
    if not isinstance(payload, list) or not 1 <= len(payload) <= _MAX_ARTICLES:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
    texts: list[str] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
        segments = tuple(
            _bounded_article_text(item.get(field))
            for field in ("headline", "summary")
            if item.get(field) is not None
        )
        segments = tuple(segment for segment in segments if segment)
        if not segments:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
        text = "\n".join(segments)
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
        texts.append(text)
    return tuple(texts)


def _official_html_transient_texts(body: bytes) -> tuple[str, ...]:
    try:
        html = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL") from error
    lowered = html.casefold()
    if "<!doctype" in lowered or "<!entity" in lowered or "<?xml" in lowered:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
    parser = _BoundedOfficialHtmlTextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL") from error
    text = parser.text()
    if not text:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
    return (text,)


class _BoundedOfficialHtmlTextParser(HTMLParser):
    """HTML body text만 bounded memory로 모으고 script/style/URL/attribute는 처리하지 않는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if (
            tag.casefold() in {"script", "style", "noscript", "template"}
            and self._ignored_depth > 0
        ):
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth != 0:
            return
        normalized = " ".join(data.split())
        if normalized:
            self._parts.append(normalized)
            if len(" ".join(self._parts).encode("utf-8")) > _MAX_TEXT_BYTES:
                raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")

    def text(self) -> str:
        return " ".join(self._parts)


def _bounded_article_text(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
    normalized = " ".join(value.split())
    if not normalized or len(normalized.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_PROTOCOL")
    return normalized


def _validate_symbol_date(*, symbol: str, date: str) -> None:
    if _SYMBOL.fullmatch(symbol) is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_SYMBOL_INVALID")
    if _DATE.fullmatch(date) is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_DATE_INVALID")


def _validate_http_response_boundary(
    headers: Mapping[str, str],
    *,
    expected_content_type: str,
    maximum_response_bytes: int,
) -> str:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower().strip()
        if (
            not lowered
            or lowered in normalized
            or not isinstance(value, str)
            or "\r" in value
            or "\n" in value
        ):
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_HEADER_INVALID")
        normalized[lowered] = value.strip()
    if "location" in normalized:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_REDIRECT_FORBIDDEN")
    if normalized.get("content-encoding", "identity").lower() != "identity":
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_ENCODING_FORBIDDEN")
    if normalized.get("transfer-encoding", "").lower() not in {"", "chunked"}:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_TRANSFER_INVALID")
    content_type = normalized.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type != expected_content_type:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_MIME_INVALID")
    length = normalized.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > maximum_response_bytes):
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_BOUND")
    return content_type


def _read_bounded_response_body(
    *,
    response: Any,
    deadline: Any,
    timeout_seconds: float,
    maximum_response_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    iterator = response.iter_raw(chunk_size=16 * 1024)
    while True:
        response.set_read_timeout_seconds(
            timeout_seconds=min(timeout_seconds, deadline.remaining_seconds())
        )
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        deadline.remaining_seconds()
        if not isinstance(chunk, bytes) or not chunk:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_BODY_INVALID")
        total += len(chunk)
        if total > maximum_response_bytes:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_RESPONSE_BOUND")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_canonical_document(content: bytes, *, code: str) -> Mapping[str, object]:
    if not content or len(content) > _MAX_PACKET_BYTES:
        raise ForeignNewsProviderProbeError(code)
    try:
        document = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsProviderProbeError(code) from error
    if not isinstance(document, Mapping) or _canonical_bytes(document) != content:
        raise ForeignNewsProviderProbeError(code)
    return document


def _required_text(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_SHAPE_INVALID")
    return value


def _required_integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_SHAPE_INVALID")
    return value


def _parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_EXPIRY_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_PACKET_EXPIRY_INVALID")
    return parsed.astimezone(UTC)


def _receipt(
    *,
    packet: ForeignNewsProviderProbePacket,
    started_at: datetime,
    completed_at: datetime,
    outcome: str,
    physical_call_count: int,
    provider_status_class: str,
    projection_hash: str | None,
) -> ForeignNewsProviderProbeReceipt:
    return ForeignNewsProviderProbeReceipt(
        approval_id_hash=_sha256(packet.approval_id.encode("utf-8")),
        approval_packet_sha256=packet.packet_sha256(),
        completed_at=completed_at,
        lane_state="AVAILABLE" if outcome == "SUCCESS" else "ABSTAIN",
        logical_call_count=0 if outcome == "NOT_EXECUTED" else 1,
        operation=packet.operation,
        outcome=outcome,
        physical_call_count=physical_call_count,
        projection_hash=projection_hash,
        provider_family=packet.provider_family,
        provider_status_class=provider_status_class,
        request_plan_digest=packet.request_plan_digest,
        started_at=started_at,
    )


def _claim_key(packet: ForeignNewsProviderProbePacket) -> str:
    return _sha256(f"{packet.approval_id}\0{packet.packet_sha256()}\0{packet.nonce}".encode())


def _successful_parse_projection_hash(packet: ForeignNewsProviderProbePacket) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "contractId": _RECEIPT_CONTRACT_ID,
                "operation": packet.operation,
                "parserOutcome": "ANALYZED_TRANSIENT_ONLY",
                "providerFamily": packet.provider_family,
            }
        )
    )


def _status_class(status_code: int) -> str:
    if 400 <= status_code <= 499:
        return "HTTP_4XX"
    if 500 <= status_code <= 599:
        return "HTTP_5XX"
    return "PROTOCOL"


def _completed_at(started_at: datetime, candidate: datetime) -> datetime:
    """Wall-clock regression이 receipt chronology를 뒤집지 않도록 completion time을 fail-closed normalize한다."""

    completed_at = candidate.astimezone(UTC)
    return completed_at if completed_at >= started_at else started_at


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
