"""Pre-S5 Voyage contextual query embedding의 single-query hard-gated transport다.

The active immutable bundle profile is chosen by the database scope, never by the HTTP request.
This module receives only the already-normalized question, opaque scope claim, explicit consent flag,
and a query-specific one-shot packet.  It writes no raw question, response body, vector, or credential
to a ledger, log, receipt, or object that outlives the active request.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NoReturn, Protocol

from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5ProviderBinding,
    PreS5VoyageEvaluationBatchActivation,
    PreS5VoyageQueryActivation,
    load_pre_s5_voyage_query_activation,
)
from app.rag.pre_s5_voyage_transport import (
    OutboundDisabledPreS5VoyageHttpSender,
    PreS5VoyageAttemptLease,
    PreS5VoyageHttpRequest,
    PreS5VoyageHttpResponse,
    PreS5VoyageHttpSender,
    PreS5VoyageResponseValidationError,
    _parse_contextualized_response,
)
from app.rag.pre_s5_voyage_tokenizer import (
    LocalPreS5VoyageContext4Tokenizer,
    PreS5VoyageTokenCounter,
    PreS5VoyageTokenizerError,
)
from app.rag.rag_v2_authorized_retrieval import (
    RagV2QueryEmbeddingError,
    RagV2QueryEmbeddingReceipt,
    RagV2RetrievalFailureCode,
)

_VOYAGE_ORIGIN = "https://api.voyageai.com"
_VOYAGE_ENDPOINT = "/v1/contextualizedembeddings"
_VOYAGE_MODEL = "voyage-context-4"
_VOYAGE_PROFILE = "voyage_context_4_1024_v1"
_OUTPUT_DIMENSION = 1024
_TIMEOUT_SECONDS = 20
_MAX_QUERY_BYTES = 8_192
_MAX_RESPONSE_BYTES = 4_194_304
_SCOPE_CLAIM = re.compile(r"^rvs_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PreS5VoyageQueryTransportError(RuntimeError):
    """Internal query transport failure that must be mapped to a non-content public typed result."""


@dataclass(frozen=True, slots=True)
class _ParsedQueryResponse:
    """The only response projection retained after validation: unit vector plus billed token count."""

    vector: tuple[float, ...]
    total_tokens: int


class PreS5VoyageQueryUsageReservationPort(Protocol):
    """The process-wide factory may create only a packet-bound one-shot lease per incoming question."""

    def reserve(
        self,
        *,
        activation: PreS5VoyageQueryActivation | PreS5VoyageEvaluationBatchActivation,
        evaluation_component_scope: Literal["EXACT30", "OA112"] | None = None,
    ) -> PreS5VoyageAttemptLease:
        """Return one lease; the optional public-evaluation label contains no owner/question text."""


class PacketGatedPreS5VoyageContext4QueryEmbedder:
    """Create a fresh one-shot Voyage query embedder only after the current request's local packet validates.

    A process never caches a successful packet or lease.  Therefore an old packet cannot authorize a
    later question, and a scope/profile change is detected before a provider socket is reachable.
    """

    def __init__(
        self,
        *,
        local_root: Path,
        binding: PreS5ProviderBinding,
        api_key: str,
        usage_repository: PreS5VoyageQueryUsageReservationPort,
        sender: PreS5VoyageHttpSender | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(local_root, Path)
            or not local_root.is_absolute()
            or not isinstance(binding, PreS5ProviderBinding)
            or not isinstance(api_key, str)
            or not 1 <= len(api_key) <= 4_096
            or usage_repository is None
        ):
            raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_RUNTIME_CONFIGURATION")
        self._local_root = local_root
        self._binding = binding
        self._api_key = api_key
        self._usage_repository = usage_repository
        self._sender = sender or OutboundDisabledPreS5VoyageHttpSender()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def embedding_profile_id(self) -> str:
        """Expose only the immutable Voyage profile identity to the profile-selected retrieval engine."""

        return _VOYAGE_PROFILE

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        """Load/consume a fresh exact packet and perform the one allowed query embedding attempt."""

        if not external_query_consent_granted:
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
            )
        try:
            now = _utc_now(self._clock)
            activation = load_pre_s5_voyage_query_activation(
                local_root=self._local_root,
                binding=self._binding,
                question=question,
                scope_claim_id=scope_claim_id,
                now=now,
            )
            token_counter = LocalPreS5VoyageContext4Tokenizer.from_local_root(
                local_root=self._local_root,
                expected_sha256=activation.tokenizer_sha256,
            )
            # local official tokenizer preflight가 실패한 packet은 DB reservation도 남기지 않는다.
            # reservation은 one-shot nonce를 소비하므로, byte approximation 없이 input cap을 먼저 닫는다.
            _expected_query_token_count(
                question=question,
                token_counter=token_counter,
                activation=activation,
            )
            lease = self._usage_repository.reserve(activation=activation)
            request_embedder = PreS5VoyageContext4QueryEmbedder(
                activation=activation,
                api_key=self._api_key,
                lease=lease,
                token_counter=token_counter,
                sender=self._sender,
                clock=self._clock,
            )
        except (
            PreS5ProviderActivationError,
            PreS5VoyageTokenizerError,
            PreS5VoyageQueryTransportError,
            ValueError,
        ):
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
            ) from None
        return request_embedder.embed_query_with_receipt(
            question=question,
            scope_claim_id=scope_claim_id,
            external_query_consent_granted=True,
        )


class PreS5VoyageContext4QueryEmbedder:
    """Embed exactly one consented query with a packet bound to that query and current opaque scope.

    The default sender is disabled.  Production startup must deliberately inject a fixed-origin sender,
    verified activation packet, writer-backed one-shot lease, and standard API key; no environment value
    alone can turn this class into a provider call.  There is no retry and no BGE or alternate-provider fallback.
    """

    def __init__(
        self,
        *,
        activation: PreS5VoyageQueryActivation,
        api_key: str,
        lease: PreS5VoyageAttemptLease,
        token_counter: PreS5VoyageTokenCounter,
        sender: PreS5VoyageHttpSender | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _validate_activation(activation)
        if (
            not isinstance(api_key, str)
            or not 1 <= len(api_key) <= 4_096
            or api_key != api_key.strip()
            or any(character in api_key for character in ("\x00", "\r", "\n"))
            or lease is None
            or token_counter is None
        ):
            raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_API_KEY_REQUIRED")
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
        self._response_validation_leaf: str | None = None

    @property
    def embedding_profile_id(self) -> str:
        """The query adapter identity must match only the Voyage immutable bundle profile."""

        return _VOYAGE_PROFILE

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        """Return one 1024d query vector or a typed zero/one-attempt failure without provider data leakage."""

        try:
            _validate_query_inputs(
                question=question,
                scope_claim_id=scope_claim_id,
                external_query_consent_granted=external_query_consent_granted,
            )
        except PreS5VoyageQueryTransportError:
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID
            ) from None
        if not external_query_consent_granted:
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
            )
        if not _activation_matches_request(
            activation=self._activation,
            question=question,
            scope_claim_id=scope_claim_id,
            now=_utc_now(self._clock),
        ) or isinstance(self._sender, OutboundDisabledPreS5VoyageHttpSender):
            # A missing/expired packet, disabled sender, or a different question never makes a physical attempt.
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
            )
        try:
            expected_input_tokens = _expected_query_token_count(
                question=question,
                token_counter=self._token_counter,
                activation=self._activation,
            )
            request = _build_request(
                activation=self._activation,
                question=question,
                api_key=self._api_key,
            )
            self._claim_before_outbound()
        except PreS5VoyageQueryTransportError:
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE,
                voyage_physical_calls=self._external_physical_calls,
            ) from None
        try:
            self._require_current_activation_after_claim()
        except PreS5VoyageQueryTransportError:
            # lease는 이미 one-shot으로 소비됐으므로 provider socket 없이도 terminal outcome을 남긴다.
            self._mark_unknown_billing()
        self._record_sender_handoff()
        response: PreS5VoyageHttpResponse | None = None
        try:
            response = self._sender.post(request)
            parsed = _parse_response(
                response=response,
                activation=self._activation,
                question=question,
            )
            actual_cost = parsed.total_tokens * self._activation.input_microusd_per_token
            self._lease.commit(
                expected_input_tokens=expected_input_tokens,
                total_tokens=parsed.total_tokens,
                actual_cost_microusd=actual_cost,
            )
        except PreS5VoyageResponseValidationError as error:
            self._response_validation_leaf = error.response_validation_leaf
            self._mark_unknown_billing()
        except Exception:
            self._mark_unknown_billing()
        if response is None:  # pragma: no cover - Protocol boundary is exercised by the generic exception path.
            self._raise_after_attempt()
        try:
            return RagV2QueryEmbeddingReceipt(
                vector=parsed.vector,
                voyage_physical_calls=1,
            )
        except (AttributeError, TypeError, ValueError):  # pragma: no cover - _parse_response closes this shape.
            self._raise_after_attempt()

    def _claim_before_outbound(self) -> None:
        """Claim the DB-backed lease under a local lock immediately before the one allowed HTTP send."""

        with self._lock:
            if self._consumed:
                raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_SINGLE_USE")
            now = _utc_now(self._clock)
            if now >= self._activation.expires_at:
                raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_EXPIRED")
            try:
                self._lease.claim_attempt(now=now)
            except Exception:
                raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_LEASE_UNAVAILABLE") from None
            self._consumed = True

    def _record_sender_handoff(self) -> None:
        """유효 packet에서 sender seam으로 넘어간 경우에만 physical attempt를 기록한다."""

        with self._lock:
            if not self._consumed:  # pragma: no cover - callers always claim before handoff.
                raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_SINGLE_USE")
            self._external_physical_calls = 1

    def _require_current_activation_after_claim(self) -> None:
        """DB lock 대기 뒤 packet이 만료됐으면 sender seam에 도달하지 못하게 한다."""

        if _utc_now(self._clock) >= self._activation.expires_at:
            raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_EXPIRED")

    def _mark_unknown_billing(self) -> None:
        """소비된 one-shot lease가 정상 commit되지 않으면 immutable terminal outcome을 남긴다."""

        try:
            self._lease.mark_unknown_billing()
        except Exception:
            pass
        self._raise_after_attempt()

    def _raise_after_attempt(self) -> NoReturn:
        error = RagV2QueryEmbeddingError(
            RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID,
            voyage_physical_calls=self._external_physical_calls,
        )
        if self._response_validation_leaf is not None:
            setattr(error, "response_validation_leaf", self._response_validation_leaf)
        raise error from None


def _validate_activation(activation: object) -> None:
    """Reject direct construction that widens the fixed origin, one-call, cost, or query-only purpose."""

    if not isinstance(activation, PreS5VoyageQueryActivation):
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_ACTIVATION_INVALID")
    if (
        not _is_sha256(activation.packet_sha256)
        or not _is_sha256(activation.nonce_sha256)
        or not _is_sha256(activation.query_sha256)
        or not _is_sha256(activation.scope_claim_sha256)
        or not _is_sha256(activation.rate_evidence_sha256)
        or not _is_sha256(activation.tokenizer_sha256)
        or activation.provider != "VOYAGE"
        or activation.operation != "CONTEXTUALIZED_QUERY_EMBEDDING"
        or activation.origin != _VOYAGE_ORIGIN
        or activation.endpoint != _VOYAGE_ENDPOINT
        or not isinstance(activation.expires_at, datetime)
        or activation.expires_at.tzinfo is None
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        or type(activation.token_cap) is not int
        or not 1 <= activation.token_cap <= 8_192
        or type(activation.byte_cap) is not int
        or not 1 <= activation.byte_cap <= _MAX_RESPONSE_BYTES
        or type(activation.cost_cap_microusd) is not int
        or not 1 <= activation.cost_cap_microusd <= 1_000_000_000
        or type(activation.input_microusd_per_token) is not int
        or not 1 <= activation.input_microusd_per_token <= 1_000_000
        or activation.token_cap * activation.input_microusd_per_token
        > activation.cost_cap_microusd
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
    ):
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_ACTIVATION_INVALID")


def _validate_query_inputs(
    *,
    question: str,
    scope_claim_id: str,
    external_query_consent_granted: bool,
) -> None:
    """Question validation is repeated adjacent to the provider seam so malformed wire data cannot consume a lease."""

    if (
        not isinstance(question, str)
        or not 1 <= len(question) <= 1_000
        or not 1 <= len(question.encode("utf-8", errors="strict")) <= _MAX_QUERY_BYTES
        or question != unicodedata.normalize("NFC", question)
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in question)
        or _SCOPE_CLAIM.fullmatch(scope_claim_id) is None
        or type(external_query_consent_granted) is not bool
    ):
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_ARGUMENT")


def _activation_matches_request(
    *,
    activation: PreS5VoyageQueryActivation,
    question: str,
    scope_claim_id: str,
    now: datetime,
) -> bool:
    """Compare only local SHA-256 projections so packet validation never persists request content or owner identity."""

    return (
        now < activation.expires_at.astimezone(UTC)
        and activation.query_sha256 == hashlib.sha256(question.encode("utf-8")).hexdigest()
        and activation.scope_claim_sha256
        == hashlib.sha256(scope_claim_id.encode("utf-8")).hexdigest()
    )


def _build_request(
    *,
    activation: PreS5VoyageQueryActivation,
    question: str,
    api_key: str,
) -> PreS5VoyageHttpRequest:
    """Build the only allowed contextual-query request shape: one nested query, no auto chunking, no tools."""

    try:
        body = json.dumps(
            {
                "enable_auto_chunking": False,
                "input_type": "query",
                "inputs": [[question]],
                "model": _VOYAGE_MODEL,
                "output_dimension": _OUTPUT_DIMENSION,
                "output_dtype": "float",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError):
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_REQUEST_INVALID") from None
    if not 1 <= len(body) <= activation.byte_cap:
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_REQUEST_INVALID")
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


def _validate_token_counter(
    *,
    token_counter: object,
    activation: PreS5VoyageQueryActivation,
) -> None:
    """Query packet의 exact local official tokenizer가 없으면 lease도 consume하지 않는다."""

    if (
        not isinstance(token_counter, PreS5VoyageTokenCounter)
        or token_counter.model != _VOYAGE_MODEL
        or token_counter.tokenizer_sha256 != activation.tokenizer_sha256
    ):
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_OFFICIAL_TOKENIZER_REQUIRED")


def _expected_query_token_count(
    *,
    question: str,
    token_counter: PreS5VoyageTokenCounter,
    activation: PreS5VoyageQueryActivation,
) -> int:
    """Question byte length가 아니라 provider model tokenizer count로 one-shot packet cap을 검사한다."""

    try:
        expected = token_counter.count_texts(texts=(question,), token_cap=activation.token_cap)
    except Exception:
        raise PreS5VoyageQueryTransportError(
            "PRE_S5_VOYAGE_QUERY_OFFICIAL_TOKENIZER_REQUIRED"
        ) from None
    if type(expected) is not int or not 1 <= expected <= activation.token_cap:
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_OFFICIAL_TOKENIZER_REQUIRED")
    return expected


def _parse_response(
    *,
    response: object,
    activation: PreS5VoyageQueryActivation,
    question: str,
) -> _ParsedQueryResponse:
    """Parse and immediately discard raw response JSON; exact nested query response cardinality is one-by-one."""

    projection = _parse_contextualized_response(
        response=response,
        expected_text_groups=((question,),),
        model=_VOYAGE_MODEL,
        token_cap=activation.token_cap,
        byte_cap=activation.byte_cap,
        cost_cap_microusd=activation.cost_cap_microusd,
        input_microusd_per_token=activation.input_microusd_per_token,
    )
    vector = tuple(float(value) for value in projection.vectors[0])
    return _ParsedQueryResponse(vector=vector, total_tokens=projection.total_tokens)


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PreS5VoyageQueryTransportError("PRE_S5_VOYAGE_QUERY_CLOCK")
    return value.astimezone(UTC)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
