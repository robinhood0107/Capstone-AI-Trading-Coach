"""S4.8 Optional 3의 local one-shot provider probe boundary다.

이 모듈은 local 0700 control root의 canonical packet과 current execution evidence가 일치할 때만
단 한 번의 fixed HTTPS request를 transport에 넘긴다. API key, raw body/header/query는 receipt나
DB에 보관하지 않으며 이 probe는 Decision/Signal/Risk/order authority를 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import quote

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
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

_CONTRACT_ID: Final[str] = "s4-8-optional3-probe-approval-v2"
_RECEIPT_CONTRACT_ID: Final[str] = "s4-8-optional3-probe-receipt-v2"
_MAX_PACKET_BYTES: Final[int] = 16 * 1024
_MAX_RESPONSE_BYTES: Final[int] = 256 * 1024
_REQUEST_TIMEOUT_SECONDS: Final[float] = 10.0
_RESPONSE_JSON_LIMITS: Final = BoundedJsonLimits(
    max_bytes=_MAX_RESPONSE_BYTES,
    max_depth=12,
    max_list_items=4_096,
    max_object_keys=128,
    max_text_codepoints=65_536,
    max_text_bytes=_MAX_RESPONSE_BYTES,
    max_number_characters=64,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_APPROVAL_ID = re.compile(r"^o3p_[a-z0-9]{32,64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_OPERATOR = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.:-]{0,19}$")
_DATE = re.compile(r"^(?:NONE|[0-9]{4}-[0-9]{2}-[0-9]{2})$")
_LEAF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Optional3ProbeError(ValueError):
    """Optional 3 packet, local control, or single-call invariants가 깨졌음을 나타낸다."""

    def __init__(self, code: str, *, physical_call_count: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.physical_call_count = physical_call_count


@dataclass(frozen=True, slots=True)
class _FixedRequestPlan:
    """Provider endpoint는 code-owned mapping만 사용해 packet/CLI가 origin을 바꾸지 못하게 한다."""

    provider_family: str
    operation: str
    hostname: str
    credential_environment_variable: str
    credential_query_parameter: str
    target_template: str
    response_list_key: str

    def target(self, *, symbol: str, date: str) -> str:
        """Non-secret symbol/date만 fixed template에 넣고 key는 transport 순간에만 전달한다."""

        if date != "NONE" and self.operation != "TWELVE_DATA_TIME_SERIES":
            raise Optional3ProbeError("OPTIONAL3_PROBE_DATE_OPERATION_INVALID")
        if self.operation == "TWELVE_DATA_TIME_SERIES":
            target = self.target_template.format(symbol=symbol)
            return target if date == "NONE" else f"{target}&end_date={date}"
        return self.target_template.format(symbol=symbol)

    def validate_success_body(self, *, body: bytes, symbol: str) -> None:
        """Raw JSON은 bounded transient parse만 하고 retained projection에는 복사하지 않는다."""

        if not body or len(body) > _MAX_RESPONSE_BYTES:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_BOUND")
        try:
            payload = parse_bounded_json_bytes(body, limits=_RESPONSE_JSON_LIMITS)
        except BoundedJsonError:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_JSON_INVALID") from None
        if self.operation.startswith("FINNHUB_"):
            # Finnhub Recommendation/Earnings는 document가 아니라 top-level array를 반환한다.
            if not isinstance(payload, list):
                raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_SHAPE_INVALID")
            for entry in payload:
                if not isinstance(entry, Mapping):
                    raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_SHAPE_INVALID")
                response_symbol = entry.get("symbol")
                if response_symbol is not None and response_symbol != symbol:
                    raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_SYMBOL_INVALID")
            return
        if not isinstance(payload, Mapping):
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_SHAPE_INVALID")
        values = payload.get(self.response_list_key)
        if not isinstance(values, list):
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_SHAPE_INVALID")


_PLANS: Final[dict[str, _FixedRequestPlan]] = {
    "FINNHUB_RECOMMENDATION": _FixedRequestPlan(
        provider_family="FINNHUB_OPTIONAL3",
        operation="FINNHUB_RECOMMENDATION",
        hostname="finnhub.io",
        credential_environment_variable="FINNHUB_API_KEY",
        credential_query_parameter="token",
        target_template="/api/v1/stock/recommendation?symbol={symbol}",
        response_list_key="data",
    ),
    "FINNHUB_EARNINGS": _FixedRequestPlan(
        provider_family="FINNHUB_OPTIONAL3",
        operation="FINNHUB_EARNINGS",
        hostname="finnhub.io",
        credential_environment_variable="FINNHUB_API_KEY",
        credential_query_parameter="token",
        target_template="/api/v1/stock/earnings?limit=1&symbol={symbol}",
        response_list_key="data",
    ),
    "TWELVE_DATA_TIME_SERIES": _FixedRequestPlan(
        provider_family="TWELVE_DATA",
        operation="TWELVE_DATA_TIME_SERIES",
        hostname="api.twelvedata.com",
        credential_environment_variable="TWELVE_DATA_API_KEY",
        credential_query_parameter="apikey",
        target_template="/time_series?interval=1day&outputsize=1&symbol={symbol}",
        response_list_key="values",
    ),
    "MASSIVE_PREVIOUS_DAY_AGGREGATE": _FixedRequestPlan(
        provider_family="MASSIVE",
        operation="MASSIVE_PREVIOUS_DAY_AGGREGATE",
        hostname="api.massive.com",
        credential_environment_variable="MASSIVE_API_KEY",
        credential_query_parameter="apiKey",
        target_template="/v2/aggs/ticker/{symbol}/prev",
        response_list_key="results",
    ),
}


class Optional3ProbeTransport(Protocol):
    """Production transport는 fixed hostname/target만 받고 response를 memory에서만 반환한다."""

    def get(
        self,
        *,
        hostname: str,
        target: str,
        api_key: str,
        expires_at: datetime,
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> Optional3ProbeHttpResponse: ...


@dataclass(frozen=True, slots=True)
class Optional3ProbeHttpResponse:
    """Transport의 bounded response다. header와 body는 executor를 벗어나 저장되지 않는다."""

    status_code: int
    body: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_STATUS_INVALID")
        if not isinstance(self.body, bytes) or len(self.body) > _MAX_RESPONSE_BYTES:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_BOUND")


class StdlibOptional3ProbeTransport:
    """OA112 downloader의 DNS pinning/TLS transport를 reuse하는 single-request HTTPS adapter다.

    URL origin은 caller나 packet에서 받지 않고 fixed plan hostname으로만 결정된다. Two independent DNS
    reads, global-address allowlist, pinned peer verification, no redirect/decompression, bounded body를 같은
    hardened primitives로 적용해 Optional 3가 약한 ad-hoc HTTP client를 새로 만들지 않게 한다.
    """

    def __init__(self) -> None:
        self._resolver = _SocketOa112DnsResolver()
        self._transport = _StdlibOa112HttpsTransport()

    def get(
        self,
        *,
        hostname: str,
        target: str,
        api_key: str,
        expires_at: datetime,
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> Optional3ProbeHttpResponse:
        """Transport handoff는 one socket/one GET이며 secret query는 memory 밖으로 복사하지 않는다."""

        plan = _plan_for_hostname(hostname)
        if (
            not target.startswith("/")
            or "\r" in target
            or "\n" in target
            or not isinstance(maximum_response_bytes, int)
            or maximum_response_bytes < 1
            or maximum_response_bytes > _MAX_RESPONSE_BYTES
            or not isinstance(timeout_seconds, float)
            or timeout_seconds <= 0
        ):
            raise Optional3ProbeError("OPTIONAL3_PROBE_TRANSPORT_ARGUMENT_INVALID")
        secret_target = _target_with_credential(
            target=target,
            parameter=plan.credential_query_parameter,
            api_key=api_key,
        )
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
                    raise Optional3ProbeError("OPTIONAL3_PROBE_DNS_REBINDING")
                with self._transport.connect(
                    hostname=hostname,
                    pinned_ip=first_addresses[0],
                    connect_timeout_seconds=min(timeout_seconds, deadline.remaining_seconds()),
                    read_timeout_seconds=min(timeout_seconds, deadline.remaining_seconds()),
                    deadline=deadline,
                ) as connection:
                    _validate_peer(connection.peer_ip, first_addresses)
                    response = connection.get(
                        target=secret_target,
                        headers={
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                            "Host": hostname,
                            "User-Agent": "capstone-s48-optional3-probe/1",
                        },
                        read_timeout_seconds=min(timeout_seconds, deadline.remaining_seconds()),
                    )
                    _validate_http_response_boundary(
                        response.headers,
                        maximum_response_bytes=maximum_response_bytes,
                    )
                    body = _read_bounded_response_body(
                        response=response,
                        deadline=deadline,
                        timeout_seconds=timeout_seconds,
                        maximum_response_bytes=maximum_response_bytes,
                    )
                    return Optional3ProbeHttpResponse(status_code=response.status_code, body=body)
        except Optional3ProbeError:
            raise
        except Oa112DownloadError as error:
            raise Optional3ProbeError("OPTIONAL3_PROBE_TRANSPORT_UNAVAILABLE") from error
        except (OSError, TimeoutError) as error:
            raise Optional3ProbeError("OPTIONAL3_PROBE_TRANSPORT_UNAVAILABLE") from error


@dataclass(frozen=True, slots=True)
class Optional3ProbeExecutionBinding:
    """Final CI/security digest와 clean Git identity를 local packet에 결속하는 proof다."""

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
            raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_BINDING_INVALID")


@dataclass(frozen=True, slots=True)
class Optional3ProbePacket:
    """One provider request만 허용하는 local approval packet의 parsed shape다."""

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
            raise Optional3ProbeError("OPTIONAL3_PROBE_OPERATION_PROVIDER_INVALID")
        if (
            _APPROVAL_ID.fullmatch(self.approval_id) is None
            or _NONCE.fullmatch(self.nonce) is None
            or _OPERATOR.fullmatch(self.operator) is None
            or _SYMBOL.fullmatch(self.symbol) is None
            or _DATE.fullmatch(self.date) is None
        ):
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_FIELD_INVALID")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_EXPIRY_INVALID")
        if self.logical_call_cap != 1 or self.physical_call_cap != 1 or self.retry_count != 0:
            raise Optional3ProbeError("OPTIONAL3_PROBE_CAP_INVALID")
        if self.tracked_raw_artifact_count != 0 or not 0 <= self.cost_cap_microusd <= 1_000_000:
            raise Optional3ProbeError("OPTIONAL3_PROBE_ARTIFACT_OR_COST_INVALID")
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
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_HASH_INVALID")
        if self.endpoint_set_digest != optional3_endpoint_set_digest():
            raise Optional3ProbeError("OPTIONAL3_PROBE_ENDPOINT_SET_DRIFT")
        if self.request_plan_digest != optional3_request_plan_digest(
            operation=self.operation,
            symbol=self.symbol,
            date=self.date,
        ):
            raise Optional3ProbeError("OPTIONAL3_PROBE_REQUEST_PLAN_DIGEST_INVALID")
        # 날짜가 endpoint에 등장하지 않는 operation은 only NONE을 쓴다.
        plan.target(symbol=self.symbol, date=self.date)

    def require_current(self, *, now: datetime) -> None:
        """Packet은 issuance 시각에 무관하게 실행 순간 한 시간 이내 one-shot window여야 한다."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise Optional3ProbeError("OPTIONAL3_PROBE_NOW_INVALID")
        now_utc = now.astimezone(UTC)
        expires_at = self.expires_at.astimezone(UTC)
        if not now_utc < expires_at <= now_utc + timedelta(hours=1):
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_EXPIRED")

    def require_execution_binding(self, *, binding: Optional3ProbeExecutionBinding) -> None:
        """Stale CI/security evidence가 다른 code tree에서 socket을 열지 못하게 한다."""

        if (
            self.ci_digest != binding.ci_digest
            or self.head_sha != binding.head_sha
            or self.security_digest != binding.security_digest
            or self.tree_sha256 != binding.tree_sha256
        ):
            raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_BINDING_DRIFT")

    def packet_sha256(self) -> str:
        """Claim과 receipt는 packet 본문 대신 its canonical digest만 retain한다."""

        return _sha256(_canonical_bytes(self.to_local_document()))

    def to_local_document(self) -> dict[str, object]:
        """Control root에만 둘 canonical packet document를 만든다. credential은 포함하지 않는다."""

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
            "schemaVersion": 2,
            "securityDigest": self.security_digest,
            "symbol": self.symbol,
            "trackedRawArtifactCount": self.tracked_raw_artifact_count,
            "treeSha256": self.tree_sha256,
        }

    @classmethod
    def from_local_document(cls, document: Mapping[str, object]) -> Optional3ProbePacket:
        """Unknown field를 허용하지 않아 local JSON이 execution semantics를 추가할 수 없게 한다."""

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
        if set(document) != expected or document.get("schemaVersion") != 2:
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_SHAPE_INVALID")
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
        except Optional3ProbeError:
            raise
        except (TypeError, ValueError) as error:
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_SHAPE_INVALID") from error

    @classmethod
    def load_from_control_root(
        cls,
        *,
        control_root: Path,
        relative_path: str,
        now: datetime,
    ) -> Optional3ProbePacket:
        """0700 root/0600 regular local file의 exact canonical packet만 읽는다."""

        if _LEAF.fullmatch(relative_path) is None:
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_UNSAFE")
        try:
            content = _read_private_control_file(
                root=control_root,
                name=relative_path,
                maximum=_MAX_PACKET_BYTES,
                error_code="OPTIONAL3_PROBE_PACKET_UNSAFE",
            )
        except Oa112DownloadError as error:
            raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_UNSAFE") from error
        document = _parse_canonical_document(
            content, code="OPTIONAL3_PROBE_PACKET_CANONICAL_INVALID"
        )
        packet = cls.from_local_document(document)
        packet.require_current(now=now)
        return packet


@dataclass(frozen=True, slots=True)
class Optional3ProbeReceipt:
    """Raw provider material 없이 one-shot outcome만 남기는 local receipt다."""

    approval_id_hash: str
    approval_packet_sha256: str
    completed_at: datetime
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
        if self.outcome not in {"SUCCESS", "FAILED"}:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_OUTCOME_INVALID")
        plan = _PLANS.get(self.operation)
        if plan is None or plan.provider_family != self.provider_family:
            raise Optional3ProbeError("OPTIONAL3_PROBE_OPERATION_PROVIDER_INVALID")
        if self.logical_call_count != 1 or self.physical_call_count != 1:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_CAP_INVALID")
        if self.provider_status_class not in {
            "HTTP_2XX",
            "HTTP_4XX",
            "HTTP_5XX",
            "TRANSPORT",
            "PROTOCOL",
        }:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_STATUS_INVALID")
        if self.outcome == "SUCCESS":
            if self.provider_status_class != "HTTP_2XX" or self.projection_hash is None:
                raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_SUCCESS_INVALID")
        elif self.projection_hash is not None:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_FAILURE_INVALID")
        if not all(
            _SHA256.fullmatch(value) is not None
            for value in (
                self.approval_id_hash,
                self.approval_packet_sha256,
                self.request_plan_digest,
            )
        ) or (self.projection_hash is not None and _SHA256.fullmatch(self.projection_hash) is None):
            raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_HASH_INVALID")
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_TIME_INVALID")

    def to_local_document(self) -> dict[str, object]:
        """Receipt에는 secret, body, query, raw header가 들어갈 위치 자체를 두지 않는다."""

        return {
            "approvalIdHash": self.approval_id_hash,
            "approvalPacketSha256": self.approval_packet_sha256,
            "completedAt": _instant(self.completed_at),
            "contractId": _RECEIPT_CONTRACT_ID,
            "decisionAuthority": "NONE",
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
            "startedAt": _instant(self.started_at),
            "state": "EXECUTED",
        }


class Optional3ProbeExecutor:
    """Packet을 consume한 뒤 fixed endpoint에 exactly one request만 넘기는 execution boundary다."""

    def __init__(self, *, control_root: Path, transport: Optional3ProbeTransport) -> None:
        self._control_root = control_root
        self._transport = transport

    def execute(
        self,
        *,
        packet: Optional3ProbePacket,
        binding: Optional3ProbeExecutionBinding,
        api_key: str,
        now: datetime,
    ) -> Optional3ProbeReceipt:
        """All preconditions pass한 뒤 one transport handoff를 만들고 outcome을 content-free로 seal한다."""

        packet.require_current(now=now)
        packet.require_execution_binding(binding=binding)
        if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
            raise Optional3ProbeError("OPTIONAL3_PROBE_CREDENTIAL_UNAVAILABLE")
        if "\r" in api_key or "\n" in api_key:
            raise Optional3ProbeError("OPTIONAL3_PROBE_CREDENTIAL_INVALID")
        plan = _PLANS[packet.operation]
        claim_key = _claim_key(packet)
        self._consume(packet=packet, claim_key=claim_key)
        started_at = now.astimezone(UTC)
        try:
            response = self._transport.get(
                hostname=plan.hostname,
                target=plan.target(symbol=packet.symbol, date=packet.date),
                api_key=api_key,
                expires_at=packet.expires_at,
                timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                maximum_response_bytes=_MAX_RESPONSE_BYTES,
            )
        except Exception:
            receipt = _receipt(
                packet=packet,
                started_at=started_at,
                completed_at=_completed_at(now),
                outcome="FAILED",
                provider_status_class="TRANSPORT",
                projection_hash=None,
            )
            self._write_receipt(claim_key=claim_key, receipt=receipt)
            return receipt

        if 200 <= response.status_code <= 299:
            try:
                plan.validate_success_body(body=response.body, symbol=packet.symbol)
            except Optional3ProbeError:
                receipt = _receipt(
                    packet=packet,
                    started_at=started_at,
                    completed_at=_completed_at(now),
                    outcome="FAILED",
                    provider_status_class="PROTOCOL",
                    projection_hash=None,
                )
                self._write_receipt(claim_key=claim_key, receipt=receipt)
                return receipt
            receipt = _receipt(
                packet=packet,
                started_at=started_at,
                completed_at=_completed_at(now),
                outcome="SUCCESS",
                provider_status_class="HTTP_2XX",
                projection_hash=_successful_parse_projection_hash(packet=packet),
            )
            self._write_receipt(claim_key=claim_key, receipt=receipt)
            return receipt

        receipt = _receipt(
            packet=packet,
            started_at=started_at,
            completed_at=_completed_at(now),
            outcome="FAILED",
            provider_status_class=_status_class(response.status_code),
            projection_hash=None,
        )
        self._write_receipt(claim_key=claim_key, receipt=receipt)
        return receipt

    def _consume(self, *, packet: Optional3ProbePacket, claim_key: str) -> None:
        """O_EXCL claim을 request보다 먼저 write해 concurrent caller가 same packet을 재사용하지 못하게 한다."""

        document = {
            "approvalIdHash": _sha256(packet.approval_id.encode("utf-8")),
            "approvalPacketSha256": packet.packet_sha256(),
            "contractId": _CONTRACT_ID,
            "nonceHash": _sha256(packet.nonce.encode("utf-8")),
            "schemaVersion": 2,
        }
        try:
            root_fd = _open_private_root(
                self._control_root, error_code="OPTIONAL3_PROBE_CONTROL_UNSAFE"
            )
        except Oa112DownloadError as error:
            raise Optional3ProbeError("OPTIONAL3_PROBE_CONTROL_UNSAFE") from error
        try:
            try:
                _write_new_private_file(
                    root_fd,
                    f"consumed-{claim_key}.json",
                    _canonical_bytes(document),
                )
            except FileExistsError as error:
                raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_ALREADY_CONSUMED") from error
            except (OSError, Oa112DownloadError) as error:
                raise Optional3ProbeError("OPTIONAL3_PROBE_CLAIM_UNAVAILABLE") from error
        finally:
            os.close(root_fd)

    def _write_receipt(self, *, claim_key: str, receipt: Optional3ProbeReceipt) -> None:
        """Provider call 후 receipt write가 실패하면 silently lose하지 않고 fail-closed한다."""

        try:
            root_fd = _open_private_root(
                self._control_root, error_code="OPTIONAL3_PROBE_CONTROL_UNSAFE"
            )
        except Oa112DownloadError as error:
            raise Optional3ProbeError("OPTIONAL3_PROBE_CONTROL_UNSAFE") from error
        try:
            try:
                _write_new_private_file(
                    root_fd,
                    f"receipt-{claim_key}.json",
                    _canonical_bytes(receipt.to_local_document()),
                )
            except (FileExistsError, OSError, Oa112DownloadError) as error:
                raise Optional3ProbeError(
                    "OPTIONAL3_PROBE_RECEIPT_UNAVAILABLE",
                    physical_call_count=1,
                ) from error
        finally:
            os.close(root_fd)


def optional3_endpoint_set_digest() -> str:
    """Public packet에는 endpoint strings 대신 fixed ordered map digest만 bind한다."""

    projection = [
        {
            "credentialEnvironmentVariable": plan.credential_environment_variable,
            "credentialQueryParameter": plan.credential_query_parameter,
            "hostname": plan.hostname,
            "operation": plan.operation,
            "providerFamily": plan.provider_family,
            "targetTemplate": plan.target_template,
        }
        for plan in _PLANS.values()
    ]
    return _sha256(_canonical_bytes(projection))


def optional3_request_plan_digest(*, operation: str, symbol: str, date: str) -> str:
    """Secret credential 없이 exact operation/symbol/date request plan identity를 계산한다."""

    plan = _PLANS.get(operation)
    if plan is None or _SYMBOL.fullmatch(symbol) is None or _DATE.fullmatch(date) is None:
        raise Optional3ProbeError("OPTIONAL3_PROBE_REQUEST_PLAN_INVALID")
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


def optional3_credential_environment_variable(*, operation: str) -> str:
    """Only the fixed provider-standard environment variable name is exposed to the CLI."""

    plan = _PLANS.get(operation)
    if plan is None:
        raise Optional3ProbeError("OPTIONAL3_PROBE_OPERATION_PROVIDER_INVALID")
    return plan.credential_environment_variable


def _plan_for_hostname(hostname: str) -> _FixedRequestPlan:
    matches = tuple(plan for plan in _PLANS.values() if plan.hostname == hostname)
    if not matches:
        raise Optional3ProbeError("OPTIONAL3_PROBE_HOSTNAME_FORBIDDEN")
    first = matches[0]
    if any(
        plan.credential_query_parameter != first.credential_query_parameter
        or plan.credential_environment_variable != first.credential_environment_variable
        for plan in matches[1:]
    ):
        raise Optional3ProbeError("OPTIONAL3_PROBE_HOSTNAME_PLAN_INVALID")
    return first


def _target_with_credential(*, target: str, parameter: str, api_key: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", parameter):
        raise Optional3ProbeError("OPTIONAL3_PROBE_CREDENTIAL_PARAMETER_INVALID")
    separator = "&" if "?" in target else "?"
    return f"{target}{separator}{parameter}={quote(api_key, safe='')}"


def _validate_http_response_boundary(
    headers: Mapping[str, str],
    *,
    maximum_response_bytes: int,
) -> None:
    """Redirect, compression, malformed length/header를 body parse 전에 fail-closed한다."""

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
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_HEADER_INVALID")
        normalized[lowered] = value.strip()
    if "location" in normalized:
        raise Optional3ProbeError("OPTIONAL3_PROBE_REDIRECT_FORBIDDEN")
    if normalized.get("content-encoding", "identity").lower() != "identity":
        raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_ENCODING_FORBIDDEN")
    if normalized.get("transfer-encoding", "").lower() not in {"", "chunked"}:
        raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_TRANSFER_INVALID")
    content_type = normalized.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type != "application/json":
        raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_MIME_INVALID")
    length = normalized.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > maximum_response_bytes):
        raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_BOUND")


def _read_bounded_response_body(
    *,
    response: Any,
    deadline: Any,
    timeout_seconds: float,
    maximum_response_bytes: int,
) -> bytes:
    """Body는 one bounded in-memory buffer에서만 transient parse 쪽으로 넘긴다."""

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
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_BODY_INVALID")
        total += len(chunk)
        if total > maximum_response_bytes:
            raise Optional3ProbeError("OPTIONAL3_PROBE_RESPONSE_BOUND")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_canonical_document(content: bytes, *, code: str) -> Mapping[str, object]:
    if not content or len(content) > _MAX_PACKET_BYTES:
        raise Optional3ProbeError(code)
    try:
        document = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Optional3ProbeError(code) from error
    if not isinstance(document, Mapping) or _canonical_bytes(document) != content:
        raise Optional3ProbeError(code)
    return document


def _required_text(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_SHAPE_INVALID")
    return value


def _required_integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_SHAPE_INVALID")
    return value


def _parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_EXPIRY_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Optional3ProbeError("OPTIONAL3_PROBE_PACKET_EXPIRY_INVALID")
    return parsed.astimezone(UTC)


def _receipt(
    *,
    packet: Optional3ProbePacket,
    started_at: datetime,
    completed_at: datetime,
    outcome: str,
    provider_status_class: str,
    projection_hash: str | None,
) -> Optional3ProbeReceipt:
    return Optional3ProbeReceipt(
        approval_id_hash=_sha256(packet.approval_id.encode("utf-8")),
        approval_packet_sha256=packet.packet_sha256(),
        completed_at=completed_at,
        logical_call_count=1,
        operation=packet.operation,
        outcome=outcome,
        physical_call_count=1,
        projection_hash=projection_hash,
        provider_family=packet.provider_family,
        provider_status_class=provider_status_class,
        request_plan_digest=packet.request_plan_digest,
        started_at=started_at,
    )


def _claim_key(packet: Optional3ProbePacket) -> str:
    return _sha256(f"{packet.approval_id}\0{packet.packet_sha256()}\0{packet.nonce}".encode())


def _successful_parse_projection_hash(*, packet: Optional3ProbePacket) -> str:
    """Provider content가 아닌 fixed parser success proof만 hash해 raw-derived retention을 피한다."""

    return _sha256(
        _canonical_bytes(
            {
                "contractId": _RECEIPT_CONTRACT_ID,
                "operation": packet.operation,
                "parserOutcome": "VALIDATED_TRANSIENT_ONLY",
                "providerFamily": packet.provider_family,
                "symbol": packet.symbol,
            }
        )
    )


def _status_class(status_code: int) -> str:
    if 400 <= status_code <= 499:
        return "HTTP_4XX"
    if 500 <= status_code <= 599:
        return "HTTP_5XX"
    return "PROTOCOL"


def _completed_at(now: datetime) -> datetime:
    # Injectable clock tests와 receipt ordering을 위해 request 완료 시각은 monotonic source가 아닌 UTC로 round한다.
    return now.astimezone(UTC)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
