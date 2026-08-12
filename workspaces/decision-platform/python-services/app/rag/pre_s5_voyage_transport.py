"""Pre-S5 Voyage contextual embedding의 full-bundle one-shot boundary다.

이 module은 local packet을 읽거나 DB writer credential을 받지 않는다. caller는 packet verifier가
만든 activation, full-bundle membership proof, 그리고 DB-backed one-shot lease를 함께 주입해야 한다.
기본 sender는 socket을 열지 않으며 request/response 원문과 credential을 object lifetime 밖에 저장하거나
receipt/log로 투영하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, NoReturn, Protocol

import numpy as np
from numpy.typing import NDArray

from app.rag.pre_s5_provider_control import (
    PreS5VoyageActivation,
    PreS5VoyageDocumentBatchActivation,
)
from app.rag.pre_s5_voyage_tokenizer import PreS5VoyageTokenCounter
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_voyage_batching import VoyageDocumentBatch

_VOYAGE_ORIGIN: Final = "https://api.voyageai.com"
_VOYAGE_ENDPOINT: Final = "/v1/contextualizedembeddings"
_VOYAGE_OPERATION: Final = "CONTEXTUALIZED_DOCUMENT_EMBEDDING"
_VOYAGE_MODEL: Final = "voyage-context-4"
_VOYAGE_PROFILE: Final = "voyage_context_4_1024_v1"
_OUTPUT_DIMENSION: Final = 1024
_TIMEOUT_SECONDS: Final = 20
_MAX_GROUPS: Final = 1_000
# Voyage contextual embeddings accepts at most 16,000 pre-chunked chunks per request.
# Keep this provider ceiling below the packet's 120K-token ceiling so a syntactically valid
# local bundle can never become an undocumented oversized outbound request.
_MAX_CHUNKS: Final = 16_000
_MAX_CHUNK_UTF8_BYTES: Final = 64 * 1024
_DOCUMENT_BATCH_MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")
_COMPONENT_SCOPE = Literal["EXACT30", "OA112", "OWNER_PRIVATE"]
_FULL_BUNDLE_SCOPES: Final[tuple[_COMPONENT_SCOPE, ...]] = (
    "EXACT30",
    "OA112",
    "OWNER_PRIVATE",
)
_PUBLIC_SCOPE_GROUP_COUNTS: Final[dict[str, int]] = {"EXACT30": 30, "OA112": 112}
_DocumentActivation = PreS5VoyageActivation | PreS5VoyageDocumentBatchActivation


class PreS5VoyageTransportError(RuntimeError):
    """Voyage packet, full-bundle, lease, request 또는 response가 fail-closed 했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class PreS5VoyageBundleComponent:
    """full profile rebuild에 포함되는 one component의 ordered document groups다.

    owner scope에는 owner ID 원문 대신 SHA-256 projection만 넣는다. public base activation은
    `OWNER_PRIVATE` empty sentinel(sha256=None, groups=())만 허용하며, 실제 owner component에는
    non-empty group과 owner projection이 함께 있어야 한다. component는 local process에서만 canonical
    text를 보유하며 manifest와 ledger에는 identity/hash만 전달한다.
    """

    component_scope: _COMPONENT_SCOPE
    owner_scope_sha256: str | None
    groups: tuple[VoyagePreChunkedDocumentGroup, ...]


@dataclass(frozen=True, slots=True)
class PreS5VoyageFullBundle:
    """EXACT30·OA112와 실제 owner component 또는 public-only empty sentinel을 bind한 input이다."""

    components: tuple[PreS5VoyageBundleComponent, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class PreS5VoyageDocumentBatchResult:
    """raw response를 폐기한 뒤 atomic usage-commit/vector-stage에 넘기는 process-local 결과다."""

    vectors: NDArray[np.float32]
    expected_input_tokens: int
    provider_total_tokens: int
    actual_cost_microusd: int


class PreS5VoyageAttemptLease(Protocol):
    """DB가 packet/nonce 기준으로 one physical attempt를 atomically 선점하는 port다."""

    def claim_attempt(self, *, now: datetime) -> None: ...

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None: ...

    def mark_unknown_billing(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PreS5VoyageHttpRequest:
    """fixed-origin one-shot HTTP request다.

    sender는 `max_response_bytes`를 넘는 body를 읽지 않아야 한다. Authorization header와 body는
    provider call 중에만 존재하며 receipt, exception message, object field 외부로 복사하지 않는다.
    """

    url: str
    headers: Mapping[str, str]
    body: bytes
    timeout_seconds: int
    max_response_bytes: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PreS5VoyageHttpResponse:
    """sender가 process-local로 반환하는 bounded response envelope다."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class PreS5VoyageHttpSender(Protocol):
    """transport가 one POST만 위임하는 narrow outbound seam이다."""

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse: ...


class OutboundDisabledPreS5VoyageHttpSender:
    """명시적으로 enable하지 않은 process가 provider socket을 열지 못하게 막는다."""

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        del request
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OUTBOUND_DISABLED")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """fixed origin boundary에서 redirect target을 authorization 대상이 되게 하지 않는다."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class UrllibPreS5VoyageHttpSender:
    """fixed HTTPS POST를 한 번만 수행하는 real sender다.

    redirect와 ambient proxy를 끄고 bounded response만 읽는다. retry, request logging, raw artifact
    persistence는 제공하지 않으며 caller가 DB lease를 claim한 뒤에만 이 sender에 도달할 수 있다.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        """one fixed request를 실행하고 error body 없이 status 또는 bounded body만 반환한다."""

        _validate_http_request(request)
        # lease 직후 caller 재검증과 별개로, socket open 바로 앞에서 packet deadline을 다시 닫는다.
        now = _require_utc_now(self._clock)
        expires_at = request.expires_at.astimezone(UTC)
        if now >= expires_at:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")
        remaining_seconds = (expires_at - now).total_seconds()
        outbound = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method="POST",
        )
        transport_failed = False
        try:
            with self._opener.open(
                outbound,
                timeout=min(float(request.timeout_seconds), remaining_seconds),
            ) as stream:
                body = stream.read(request.max_response_bytes + 1)
                status = stream.getcode()
                headers = {str(key).lower(): str(value) for key, value in stream.headers.items()}
        except urllib.error.HTTPError as error:
            # HTTP error payload는 raw provider data이므로 읽거나 error message에 붙이지 않는다.
            return PreS5VoyageHttpResponse(status=error.code, headers={}, body=b"")
        except (OSError, ValueError, urllib.error.URLError):
            transport_failed = True
        if transport_failed:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE")
        if not isinstance(status, int) or len(body) > request.max_response_bytes:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE")
        return PreS5VoyageHttpResponse(status=status, headers=headers, body=body)


class PreS5VoyageContext4Transport:
    """verified full bundle 하나를 ordered contextual embedding call 한 번으로 소비한다.

    production caller는 local verifier와 DB reservation factory에서 나온 lease를 주입한다. 임의
    group entrypoint는 packet의 full-bundle authority를 우회할 수 있으므로 항상 거부한다.
    """

    def __init__(
        self,
        *,
        activation: _DocumentActivation,
        api_key: str,
        lease: PreS5VoyageAttemptLease,
        token_counter: PreS5VoyageTokenCounter,
        sender: PreS5VoyageHttpSender | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _validate_activation_shape(activation)
        if (
            not isinstance(api_key, str)
            or not 1 <= len(api_key) <= 4_096
            or api_key != api_key.strip()
            or any(character in api_key for character in ("\x00", "\r", "\n"))
            or lease is None
            or token_counter is None
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_API_KEY_REQUIRED")
        _validate_token_counter(token_counter=token_counter, activation=activation)
        self._activation = activation
        self._api_key = api_key
        self._lease = lease
        self._token_counter = token_counter
        self._sender = sender or OutboundDisabledPreS5VoyageHttpSender()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._consumed = False
        self._external_physical_calls = 0
        self._provider_total_tokens: int | None = None
        self._expected_input_tokens: int | None = None
        self._provider_status_class: str | None = None
        self._usage_state = "READY"

    @property
    def external_physical_calls(self) -> int:
        """sender seam에 실제 위임한 provider attempt 수만 반환한다."""

        with self._lock:
            return self._external_physical_calls

    def content_free_summary(self) -> dict[str, object]:
        """credential, source text, raw provider body를 제외한 operator receipt projection을 반환한다."""

        with self._lock:
            consumed = self._consumed
            physical_calls = self._external_physical_calls
            total_tokens = self._provider_total_tokens
            expected_input_tokens = self._expected_input_tokens
            provider_status_class = self._provider_status_class
            usage_state = self._usage_state
        summary: dict[str, object] = {
            "code": "PRE_S5_VOYAGE_ATTEMPT_CONSUMED" if consumed else "PRE_S5_VOYAGE_READY",
            "externalPhysicalCalls": physical_calls,
            "logicalCallsConsumed": 1 if consumed else 0,
            "operation": self._activation.operation,
            "provider": self._activation.provider,
            "rawArtifactCount": 0,
            "state": usage_state,
            "tokenizerSha256": self._activation.tokenizer_sha256,
        }
        if expected_input_tokens is not None:
            summary["expectedInputTokens"] = expected_input_tokens
        if total_tokens is not None:
            summary["providerTotalTokens"] = total_tokens
        if provider_status_class is not None:
            summary["providerStatusClass"] = provider_status_class
        return summary

    def embed_document_groups(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> NDArray[np.float32]:
        """subset entrypoint를 막아 full-bundle packet을 임의 group call에 재사용하지 못하게 한다."""

        del groups
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_REQUIRED")

    def embed_full_bundle(self, *, bundle: PreS5VoyageFullBundle) -> NDArray[np.float32]:
        """all three component scopes가 packet manifest와 같을 때만 one call을 수행한다."""

        if not isinstance(self._activation, PreS5VoyageActivation):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_REQUIRED")
        initial_now = _require_utc_now(self._clock)
        if initial_now >= self._activation.expires_at:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")
        groups = _validate_full_bundle(bundle=bundle, activation=self._activation)
        result = self._embed_validated_groups(groups)
        if not isinstance(result, np.ndarray):  # pragma: no cover - activation type closes this branch.
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
        return result

    def embed_document_batch(
        self,
        *,
        batch_plan_sha256: str,
        batch: VoyageDocumentBatch,
    ) -> PreS5VoyageDocumentBatchResult:
        """exact plan/member/count에 결속된 batch packet 하나만 physical call 한 번으로 소비한다."""

        if not isinstance(self._activation, PreS5VoyageDocumentBatchActivation):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_DOCUMENT_BATCH_REQUIRED")
        initial_now = _require_utc_now(self._clock)
        if initial_now >= self._activation.expires_at:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")
        groups = _validate_document_batch(
            batch_plan_sha256=batch_plan_sha256,
            batch=batch,
            activation=self._activation,
        )
        result = self._embed_validated_groups(groups)
        if not isinstance(result, PreS5VoyageDocumentBatchResult):  # pragma: no cover
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_DOCUMENT_BATCH_INVALID")
        return result

    def _embed_validated_groups(
        self,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> NDArray[np.float32] | PreS5VoyageDocumentBatchResult:
        """manifest validation 뒤 공통 one-shot lease/request/usage 경계를 실행한다."""

        expected_input_tokens = _expected_input_token_count(
            groups=groups,
            token_counter=self._token_counter,
            activation=self._activation,
        )
        if (
            isinstance(self._activation, PreS5VoyageDocumentBatchActivation)
            and expected_input_tokens != self._activation.expected_token_count
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_DOCUMENT_BATCH_INVALID")
        request = _build_request(groups=groups, activation=self._activation, api_key=self._api_key)
        if isinstance(self._sender, OutboundDisabledPreS5VoyageHttpSender):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OUTBOUND_DISABLED")
        self._claim_before_outbound()
        self._require_current_activation_after_claim()
        self._record_sender_handoff()
        sender_failed = False
        response: PreS5VoyageHttpResponse | None = None
        try:
            response = self._sender.post(request)
        except PreS5VoyageTransportError as error:
            if str(error) == "PRE_S5_VOYAGE_ACTIVATION_EXPIRED":
                self._raise_after_attempt("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")
            sender_failed = True
        except Exception:
            sender_failed = True
        if sender_failed:
            self._set_provider_status_class("TRANSPORT")
            self._raise_after_attempt("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE")
        if response is None:  # pragma: no cover - Protocol implementation must return a response.
            self._set_provider_status_class("TRANSPORT")
            self._raise_after_attempt("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE")
        self._set_provider_status_class(_safe_provider_status_class(response.status))
        parsed: _ParsedVoyageResponse | None = None
        parse_failed = False
        try:
            parsed = _parse_response(response=response, groups=groups, activation=self._activation)
        except PreS5VoyageTransportError:
            parse_failed = True
        if parse_failed or parsed is None:
            # raw response parser exception을 active exception context로 보존하지 않고 ledger만 남긴다.
            if response.status == 200:
                self._set_provider_status_class("HTTP_2XX_INVALID")
            self._raise_after_attempt("PRE_S5_VOYAGE_RESPONSE_INVALID")
        actual_cost_microusd = parsed.total_tokens * self._activation.input_microusd_per_token
        if actual_cost_microusd > self._activation.cost_cap_microusd:
            self._raise_after_attempt("PRE_S5_VOYAGE_RESPONSE_INVALID")
        if isinstance(self._activation, PreS5VoyageDocumentBatchActivation):
            with self._lock:
                self._provider_total_tokens = parsed.total_tokens
                self._expected_input_tokens = expected_input_tokens
                self._usage_state = "PARSED_PENDING_ATOMIC_STAGE"
            return PreS5VoyageDocumentBatchResult(
                vectors=parsed.vectors,
                expected_input_tokens=expected_input_tokens,
                provider_total_tokens=parsed.total_tokens,
                actual_cost_microusd=actual_cost_microusd,
            )
        if not self._commit_usage(
            expected_input_tokens=expected_input_tokens,
            total_tokens=parsed.total_tokens,
            actual_cost_microusd=actual_cost_microusd,
        ):
            self._raise_after_attempt("PRE_S5_VOYAGE_LEDGER_UNAVAILABLE")
        with self._lock:
            self._provider_total_tokens = parsed.total_tokens
            self._expected_input_tokens = expected_input_tokens
            self._usage_state = "COMMITTED"
        return parsed.vectors

    def _set_provider_status_class(self, value: str) -> None:
        """상태 숫자·header·body 없이 운영에 필요한 bounded provider 결과 분류만 보존한다."""

        with self._lock:
            self._provider_status_class = value

    def _claim_before_outbound(self) -> None:
        """expiry recheck와 DB one-shot claim을 sender 직전에 같은 local critical section에서 수행한다."""

        with self._lock:
            if self._consumed:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_SINGLE_USE")
            now = _require_utc_now(self._clock)
            if now >= self._activation.expires_at:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")
            claim_failed = False
            try:
                self._lease.claim_attempt(now=now)
            except PreS5VoyageTransportError:
                raise
            except Exception:
                claim_failed = True
            if claim_failed:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_LEDGER_UNAVAILABLE")
            self._consumed = True
            self._usage_state = "CLAIMED"

    def _require_current_activation_after_claim(self) -> None:
        """lease wait가 packet expiry를 넘긴 경우 socket 없이 terminal ledger outcome으로 끝낸다."""

        if _require_utc_now(self._clock) >= self._activation.expires_at:
            self._raise_after_attempt("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")

    def _record_sender_handoff(self) -> None:
        """packet이 아직 유효할 때만 sender seam 진입을 physical attempt로 센다."""

        with self._lock:
            if not self._consumed:  # pragma: no cover - callers always claim before handoff.
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_SINGLE_USE")
            self._external_physical_calls += 1
            self._usage_state = "ATTEMPTED"

    def _commit_usage(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> bool:
        """sanitized usage outcome이 append-only ledger에 기록될 때만 vector를 caller에 넘긴다."""

        committed = True
        try:
            self._lease.commit(
                expected_input_tokens=expected_input_tokens,
                total_tokens=total_tokens,
                actual_cost_microusd=actual_cost_microusd,
            )
        except Exception:
            committed = False
        return committed

    def _raise_after_attempt(self, code: str) -> NoReturn:
        """provider attempt 뒤에는 verified output이 없어도 unknown-billing outcome을 남긴다."""

        recorded = True
        try:
            self._lease.mark_unknown_billing()
        except Exception:
            recorded = False
        with self._lock:
            self._usage_state = "UNKNOWN_BILLING" if recorded else "LEDGER_UNAVAILABLE"
        if not recorded:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_LEDGER_UNAVAILABLE")
        raise PreS5VoyageTransportError(code)


@dataclass(frozen=True, slots=True)
class _GroupMetrics:
    """canonical input text를 보관하지 않는 local preflight aggregate다."""

    total_chunks: int
    total_tokens: int
    total_utf8_bytes: int


@dataclass(frozen=True, slots=True)
class _ParsedVoyageResponse:
    """raw JSON을 retention 없이 좁힌 vector/usage projection이다."""

    vectors: NDArray[np.float32]
    total_tokens: int


def build_pre_s5_voyage_full_bundle(
    *,
    components: tuple[PreS5VoyageBundleComponent, ...],
) -> PreS5VoyageFullBundle:
    """full profile rebuild의 immutable component membership manifest를 canonical hash로 만든다."""

    validated = _validate_full_bundle_components(components)
    return PreS5VoyageFullBundle(
        components=components,
        manifest_sha256=_full_bundle_manifest_sha256(validated),
    )


def _validate_full_bundle(
    *,
    bundle: object,
    activation: PreS5VoyageActivation,
) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
    """packet bound manifest와 every full-bundle member hash를 socket 생성 전에 다시 비교한다."""

    if not isinstance(bundle, PreS5VoyageFullBundle) or not _is_sha256(bundle.manifest_sha256):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
    components = _validate_full_bundle_components(bundle.components)
    calculated = _full_bundle_manifest_sha256(components)
    if (
        bundle.manifest_sha256 != calculated
        or activation.bundle_manifest_sha256 != calculated
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
    groups = tuple(group for component in components for group in component.groups)
    _validate_groups(groups)
    return groups


def _validate_document_batch(
    *,
    batch_plan_sha256: object,
    batch: object,
    activation: PreS5VoyageDocumentBatchActivation,
) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
    """packet의 exact plan/member/count와 batch의 content-free identity를 socket 전에 다시 비교한다."""

    if (
        not _is_sha256(batch_plan_sha256)
        or batch_plan_sha256 != activation.batch_plan_sha256
        or not isinstance(batch, VoyageDocumentBatch)
        or batch.batch_id != activation.batch_id
        or batch.batch_manifest_sha256 != activation.batch_manifest_sha256
        or batch.batch_ordinal != activation.batch_ordinal
        or batch.batch_count != activation.batch_count
        or batch.token_count != activation.expected_token_count
        or batch.chunk_count != activation.expected_chunk_count
        or batch.group_count != activation.expected_group_count
        or activation.byte_cap < batch.estimated_response_bytes
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_DOCUMENT_BATCH_INVALID")
    groups = batch.groups
    metrics = _validate_groups(groups)
    if (
        metrics.total_chunks != batch.chunk_count
        or len(groups) != batch.group_count
        or batch.token_count > 110_000
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_DOCUMENT_BATCH_INVALID")
    return groups


def _validate_full_bundle_components(
    components: object,
) -> tuple[PreS5VoyageBundleComponent, ...]:
    """scope order/count/owner boundary가 바뀐 profile mix를 full bundle로 승격하지 않는다."""

    if not isinstance(components, tuple) or len(components) != len(_FULL_BUNDLE_SCOPES):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
    selected: list[PreS5VoyageBundleComponent] = []
    source_ids: set[str] = set()
    revision_ids: set[str] = set()
    chunk_ids: set[str] = set()
    for expected_scope, component in zip(_FULL_BUNDLE_SCOPES, components, strict=True):
        if not isinstance(component, PreS5VoyageBundleComponent) or component.component_scope != expected_scope:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
        if expected_scope in _PUBLIC_SCOPE_GROUP_COUNTS:
            if component.owner_scope_sha256 is not None or len(component.groups) != _PUBLIC_SCOPE_GROUP_COUNTS[expected_scope]:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
        elif component.owner_scope_sha256 is None:
            # 전역 public base는 어떤 owner 원문도 provider input에 넣지 않는다.
            if component.groups:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
        elif not _is_sha256(component.owner_scope_sha256) or not component.groups:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
        if component.groups:
            try:
                metrics = _validate_groups(component.groups)
            except PreS5VoyageTransportError:
                # A malformed group is part of the signed full-profile input. Do not leak a
                # lower-level request marker before a packet/lease exists.
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID") from None
            del metrics
        component_source_ids = tuple(group.source_id for group in component.groups)
        if component_source_ids != tuple(sorted(component_source_ids, key=lambda value: value.encode("utf-8"))):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
        for group in component.groups:
            if group.source_id in source_ids or group.source_revision_id in revision_ids:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
            source_ids.add(group.source_id)
            revision_ids.add(group.source_revision_id)
            for chunk in group.chunks:
                if chunk.chunk_id in chunk_ids:
                    raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
                chunk_ids.add(chunk.chunk_id)
        selected.append(component)
    all_groups = tuple(group for component in selected for group in component.groups)
    try:
        metrics = _validate_groups(all_groups)
    except PreS5VoyageTransportError:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID") from None
    if not 1 <= metrics.total_chunks <= _MAX_CHUNKS or len(all_groups) > _MAX_GROUPS:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_FULL_BUNDLE_INVALID")
    return tuple(selected)


def _full_bundle_manifest_sha256(
    components: tuple[PreS5VoyageBundleComponent, ...],
) -> str:
    """canonical source/chunk identity만 hash해 provider로 보낼 raw text를 approval record에 넣지 않는다."""

    return _canonical_hash(
        {
            "components": [
                {
                    "componentScope": component.component_scope,
                    "members": [
                        {
                            "chunks": [
                                {
                                    "canonicalTextSha256": chunk.canonical_text_sha256,
                                    "chunkId": chunk.chunk_id,
                                    "contextSetHash": group.context_set_hash,
                                    "embeddingInputHash": chunk.embedding_input_hash,
                                    "tokenCount": chunk.token_count,
                                }
                                for chunk in group.chunks
                            ],
                            "sourceId": group.source_id,
                            "sourceRevisionId": group.source_revision_id,
                        }
                        for group in component.groups
                    ],
                    "ownerScopeSha256": component.owner_scope_sha256,
                }
                for component in components
            ],
            "embeddingProfileId": _VOYAGE_PROFILE,
            "schemaVersion": 1,
        }
    )


def _build_request(
    *,
    groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    activation: _DocumentActivation,
    api_key: str,
) -> PreS5VoyageHttpRequest:
    """canonical full-bundle order를 보존하면서 byte/token caps를 socket 전 검증한다."""

    _validate_groups(groups)
    inputs = [[chunk.canonical_text for chunk in group.chunks] for group in groups]
    request_failed = False
    try:
        body = json.dumps(
            {
                "enable_auto_chunking": False,
                "input_type": "document",
                "inputs": inputs,
                "model": _VOYAGE_MODEL,
                "output_dimension": _OUTPUT_DIMENSION,
                "output_dtype": "float",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError):
        request_failed = True
        body = b""
    if request_failed or not 1 <= len(body) <= activation.byte_cap:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
    return PreS5VoyageHttpRequest(
        url=f"{_VOYAGE_ORIGIN}{_VOYAGE_ENDPOINT}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        body=body,
        timeout_seconds=_TIMEOUT_SECONDS,
        max_response_bytes=activation.byte_cap,
        expires_at=activation.expires_at,
    )


def _validate_activation_shape(activation: object) -> None:
    """packet loader 외 direct construction도 fixed provider/cost contract로 다시 close한다."""

    if not isinstance(activation, (PreS5VoyageActivation, PreS5VoyageDocumentBatchActivation)):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID")
    manifest_is_valid = (
        _is_sha256(activation.bundle_manifest_sha256)
        if isinstance(activation, PreS5VoyageActivation)
        else (
            _is_sha256(activation.batch_plan_sha256)
            and _is_sha256(activation.batch_manifest_sha256)
            and 1 <= activation.expected_token_count <= 110_000
            and 1 <= activation.expected_chunk_count <= _MAX_CHUNKS
            and 1 <= activation.expected_group_count <= _MAX_GROUPS
        )
    )
    if (
        not _is_sha256(activation.packet_sha256)
        or not _is_sha256(activation.nonce_sha256)
        or not manifest_is_valid
        or not _is_sha256(activation.rate_evidence_sha256)
        or not _is_sha256(activation.tokenizer_sha256)
        or activation.provider != "VOYAGE"
        or activation.operation != _VOYAGE_OPERATION
        or activation.origin != _VOYAGE_ORIGIN
        or activation.endpoint != _VOYAGE_ENDPOINT
        or not isinstance(activation.expires_at, datetime)
        or activation.expires_at.tzinfo is None
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        or type(activation.token_cap) is not int
        or not 1 <= activation.token_cap <= 120_000
        or type(activation.byte_cap) is not int
        or not 1 <= activation.byte_cap <= (
            _DOCUMENT_BATCH_MAX_RESPONSE_BYTES
            if isinstance(activation, PreS5VoyageDocumentBatchActivation)
            else 4_194_304
        )
        or type(activation.cost_cap_microusd) is not int
        or not 1 <= activation.cost_cap_microusd <= 1_000_000_000
        or type(activation.input_microusd_per_token) is not int
        or not 1 <= activation.input_microusd_per_token <= 1_000_000
        or activation.token_cap * activation.input_microusd_per_token > activation.cost_cap_microusd
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID")


def _validate_groups(groups: object) -> _GroupMetrics:
    """group/chunk identity와 canonical text hash를 provider call 전에 strict하게 검증한다."""

    if not isinstance(groups, tuple) or not 1 <= len(groups) <= _MAX_GROUPS:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
    source_ids: set[str] = set()
    revision_ids: set[str] = set()
    chunk_ids: set[str] = set()
    total_chunks = 0
    total_tokens = 0
    total_utf8_bytes = 0
    for group in groups:
        if (
            not isinstance(group, VoyagePreChunkedDocumentGroup)
            or _SOURCE_ID.fullmatch(group.source_id) is None
            or _SOURCE_REVISION_ID.fullmatch(group.source_revision_id) is None
            or not _is_sha256(group.context_set_hash)
            or not isinstance(group.chunks, tuple)
            or not group.chunks
            or group.source_id in source_ids
            or group.source_revision_id in revision_ids
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
        source_ids.add(group.source_id)
        revision_ids.add(group.source_revision_id)
        for chunk in group.chunks:
            if (
                not isinstance(chunk, VoyagePreChunkedChunk)
                or _CHUNK_ID.fullmatch(chunk.chunk_id) is None
                or not isinstance(chunk.canonical_text, str)
                or not chunk.canonical_text.strip()
                or "\x00" in chunk.canonical_text
                or not _is_sha256(chunk.canonical_text_sha256)
                or not _is_sha256(chunk.embedding_input_hash)
                or type(chunk.token_count) is not int
                or not 1 <= chunk.token_count <= 600
                or hashlib.sha256(chunk.canonical_text.encode("utf-8")).hexdigest()
                != chunk.canonical_text_sha256
            ):
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
            encoded_length = len(chunk.canonical_text.encode("utf-8"))
            if not 1 <= encoded_length <= _MAX_CHUNK_UTF8_BYTES or chunk.chunk_id in chunk_ids:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
            chunk_ids.add(chunk.chunk_id)
            total_chunks += 1
            total_tokens += chunk.token_count
            total_utf8_bytes += encoded_length
    if not 1 <= total_chunks <= _MAX_CHUNKS:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
    return _GroupMetrics(
        total_chunks=total_chunks,
        total_tokens=total_tokens,
        total_utf8_bytes=total_utf8_bytes,
    )


def _validate_token_counter(
    *,
    token_counter: object,
    activation: _DocumentActivation,
) -> None:
    """Only the packet-pinned official model tokenizer can authorize an outbound input count."""

    if (
        not isinstance(token_counter, PreS5VoyageTokenCounter)
        or token_counter.model != _VOYAGE_MODEL
        or token_counter.tokenizer_sha256 != activation.tokenizer_sha256
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_REQUIRED")


def _expected_input_token_count(
    *,
    groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    token_counter: PreS5VoyageTokenCounter,
    activation: _DocumentActivation,
) -> int:
    """BGE chunk metadata가 아닌 official Voyage tokenizer count로 packet cap을 close한다."""

    texts = tuple(chunk.canonical_text for group in groups for chunk in group.chunks)
    try:
        expected = token_counter.count_texts(texts=texts, token_cap=activation.token_cap)
    except Exception:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_REQUIRED") from None
    if type(expected) is not int or not 1 <= expected <= activation.token_cap:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_REQUIRED")
    return expected


def _parse_response(
    *,
    response: object,
    groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    activation: _DocumentActivation,
) -> _ParsedVoyageResponse:
    """raw provider response를 retaining하지 않고 exact ordered float32 vectors/usage로 좁힌다."""

    if (
        not isinstance(response, PreS5VoyageHttpResponse)
        or response.status != 200
        or not isinstance(response.body, bytes)
        or not 1 <= len(response.body) <= activation.byte_cap
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    decoded: object | None = None
    parse_failed = False
    try:
        decoded = json.loads(
            response.body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        parse_failed = True
    if parse_failed:
        # Parsing exception이 raw body를 __cause__/__context__로 보존하지 않게 except 밖에서 raise한다.
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    if not _contextualized_response_envelope_is_valid(decoded, model=_VOYAGE_MODEL):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    assert isinstance(decoded, dict)
    total_tokens = _validated_total_tokens(decoded.get("usage"), token_cap=activation.token_cap)
    group_data = decoded.get("data")
    if not isinstance(group_data, list) or len(group_data) != len(groups):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    vectors: list[NDArray[np.float32]] = []
    for group_index, (expected_group, received_group) in enumerate(zip(groups, group_data, strict=True)):
        if (
            not isinstance(received_group, dict)
            or set(received_group) != {"data", "index"}
            or received_group.get("index") != group_index
            or type(received_group.get("index")) is not int
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        chunk_data = received_group.get("data")
        if not isinstance(chunk_data, list) or len(chunk_data) != len(expected_group.chunks):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        for chunk_index, (expected_chunk, received_chunk) in enumerate(
            zip(expected_group.chunks, chunk_data, strict=True)
        ):
            if not _contextualized_response_item_is_valid(
                received_chunk,
                expected_index=chunk_index,
                expected_text=expected_chunk.canonical_text,
            ):
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
            assert isinstance(received_chunk, dict)
            vectors.append(_vector_from_response(received_chunk.get("embedding")))
    if not vectors:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    return _ParsedVoyageResponse(
        vectors=np.vstack(vectors).astype(np.float32, copy=False),
        total_tokens=total_tokens,
    )


def _validated_total_tokens(value: object, *, token_cap: int) -> int:
    """provider usage가 packet token/cost reservation을 넘지 않게 strict JSON projection으로 닫는다."""

    total_tokens = value.get("total_tokens") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"total_tokens"}
        or type(total_tokens) is not int
        or not 0 <= total_tokens <= token_cap
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    return total_tokens


def _contextualized_response_envelope_is_valid(value: object, *, model: str) -> bool:
    """공식 SDK처럼 pre-chunked 응답의 chunker_version 생략 또는 null을 허용한다.

    data/model/usage 외 임의 field는 계속 거부하며, chunker_version이 존재하면 빈 문자열이나
    다른 타입은 받지 않아 응답 경계를 넓히지 않는다.
    """

    if not isinstance(value, dict):
        return False
    keys = set(value)
    if keys not in ({"data", "model", "usage"}, {"chunker_version", "data", "model", "usage"}):
        return False
    chunker_version = value.get("chunker_version")
    return value.get("model") == model and (
        "chunker_version" not in value
        or chunker_version is None
        or (isinstance(chunker_version, str) and bool(chunker_version))
    )


def _contextualized_response_item_is_valid(
    value: object,
    *,
    expected_index: int,
    expected_text: str,
) -> bool:
    """pre-chunked chunk text는 optional이지만, 반환되면 요청 text와 정확히 결속한다."""

    if not isinstance(value, dict):
        return False
    keys = set(value)
    if keys not in ({"embedding", "index"}, {"embedding", "index", "text"}):
        return False
    return (
        type(value.get("index")) is int
        and value.get("index") == expected_index
        and ("text" not in value or value.get("text") == expected_text)
    )


def _vector_from_response(value: object) -> NDArray[np.float32]:
    """JSON numeric array 하나를 finite unit-norm 1024-dimension float32 vector로 좁힌다."""

    if not isinstance(value, list) or len(value) != _OUTPUT_DIMENSION:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        try:
            number = float(item)
        except (OverflowError, ValueError):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        if not math.isfinite(number):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        numbers.append(number)
    with np.errstate(over="ignore", invalid="ignore"):
        vector = np.asarray(numbers, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
    if not bool(np.isfinite(vector).all()) or not math.isfinite(norm) or not math.isclose(
        norm, 1.0, abs_tol=1e-5, rel_tol=0.0
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    return vector


def _require_utc_now(clock: Callable[[], datetime]) -> datetime:
    """naive/custom clock을 provider authorization clock으로 쓰지 않는다."""

    now: datetime | None = None
    clock_failed = False
    try:
        now = clock()
    except Exception:
        clock_failed = True
    if clock_failed or not isinstance(now, datetime) or now.tzinfo is None:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID")
    return now.astimezone(UTC)


def _validate_http_request(request: object) -> None:
    """real sender가 caller-supplied URL/header mutation을 outbound 직전에 다시 거부한다."""

    if (
        not isinstance(request, PreS5VoyageHttpRequest)
        or request.url != f"{_VOYAGE_ORIGIN}{_VOYAGE_ENDPOINT}"
        or request.timeout_seconds != _TIMEOUT_SECONDS
        or type(request.max_response_bytes) is not int
        or not 1 <= request.max_response_bytes <= _DOCUMENT_BATCH_MAX_RESPONSE_BYTES
        or not isinstance(request.expires_at, datetime)
        or request.expires_at.tzinfo is None
        or not isinstance(request.body, bytes)
        or not request.body
        or set(request.headers) != {"Accept", "Authorization", "Content-Type"}
        or request.headers.get("Accept") != "application/json"
        or request.headers.get("Content-Type") != "application/json"
        or not isinstance(request.headers.get("Authorization"), str)
        or not request.headers["Authorization"].startswith("Bearer ")
        or len(request.headers["Authorization"]) > 4_103
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")


def _safe_provider_status_class(status: object) -> str:
    """Provider status 숫자를 receipt에 남기지 않고 안정적인 class로만 축약한다."""

    if type(status) is not int:
        return "HTTP_OTHER"
    if 200 <= status <= 299:
        return "HTTP_2XX"
    if 300 <= status <= 399:
        return "HTTP_3XX"
    if 400 <= status <= 499:
        return "HTTP_4XX"
    if 500 <= status <= 599:
        return "HTTP_5XX"
    return "HTTP_OTHER"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """duplicate response key를 last-write-wins parsing으로 해석하지 않는다."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
