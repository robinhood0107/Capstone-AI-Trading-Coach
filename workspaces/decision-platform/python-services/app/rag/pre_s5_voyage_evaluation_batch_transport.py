"""EXACT30 10개와 OA112 112개 평가 질문을 component별 한 요청으로 전송한다.

각 질문은 singleton contextual group으로 유지된다. packet은 ordered query manifest, 공통 public
scope claim, official tokenizer count, current execution evidence에 결속되고 retry와 raw persistence는 0이다.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn, Protocol

from app.rag.pre_s5_provider_control import PreS5VoyageEvaluationBatchActivation
from app.rag.pre_s5_voyage_tokenizer import PreS5VoyageTokenCounter
from app.rag.pre_s5_voyage_transport import (
    OutboundDisabledPreS5VoyageHttpSender,
    PreS5VoyageAttemptLease,
    PreS5VoyageHttpRequest,
    PreS5VoyageHttpResponse,
    PreS5VoyageHttpSender,
)

_ORIGIN = "https://api.voyageai.com"
_ENDPOINT = "/v1/contextualizedembeddings"
_MODEL = "voyage-context-4"
_DIMENSION = 1024
_TIMEOUT_SECONDS = 20


class PreS5VoyageEvaluationBatchTransportError(RuntimeError):
    """평가 batch가 provider seam 전 또는 최대 한 번의 시도 뒤 fail-closed 했다."""

    def __init__(self, code: str, *, voyage_physical_calls: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.voyage_physical_calls = voyage_physical_calls


class PreS5VoyageEvaluationBatchUsagePort(Protocol):
    """component별 aggregate query usage 한 건만 reserve하는 DB capability다."""

    def reserve(
        self,
        *,
        activation: PreS5VoyageEvaluationBatchActivation,
        evaluation_component_scope: str,
    ) -> PreS5VoyageAttemptLease:
        """질문 plaintext 없이 component-bound one-shot lease를 반환한다."""


@dataclass(frozen=True, slots=True)
class PreS5VoyageEvaluationBatchResult:
    """응답 원문을 폐기한 뒤 process 안에서만 사용하는 query vector 집합이다."""

    component_scope: str
    query_manifest_sha256: str
    vectors_by_query_sha256: Mapping[str, tuple[float, ...]]
    expected_input_tokens: int
    total_tokens: int
    voyage_physical_calls: int = 1


class PreS5VoyageEvaluationBatchTransport:
    """한 activation/lease로 closed component 평가 batch를 정확히 한 번 실행한다."""

    def __init__(
        self,
        *,
        activation: PreS5VoyageEvaluationBatchActivation,
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
            or not isinstance(token_counter, PreS5VoyageTokenCounter)
            or token_counter.model != _MODEL
            or token_counter.tokenizer_sha256 != activation.tokenizer_sha256
        ):
            raise PreS5VoyageEvaluationBatchTransportError(
                "PRE_S5_VOYAGE_EVALUATION_BATCH_CONFIGURATION"
            )
        self._activation = activation
        self._api_key = api_key
        self._lease = lease
        self._token_counter = token_counter
        self._sender = sender or OutboundDisabledPreS5VoyageHttpSender()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._consumed = False
        self._physical_calls = 0

    def embed(
        self,
        *,
        query_id_questions: Sequence[tuple[str, str]],
    ) -> PreS5VoyageEvaluationBatchResult:
        """ordered singleton groups를 전송하고 vector만 반환하며 첫 실패 뒤 호출을 만들지 않는다."""

        queries = _validate_queries(
            component_scope=self._activation.component_scope,
            query_id_questions=query_id_questions,
        )
        if isinstance(self._sender, OutboundDisabledPreS5VoyageHttpSender):
            raise PreS5VoyageEvaluationBatchTransportError(
                "PRE_S5_VOYAGE_EVALUATION_BATCH_DISABLED"
            )
        try:
            expected_tokens = self._token_counter.count_texts(
                texts=tuple(question for _, question in queries),
                token_cap=self._activation.token_cap,
            )
        except Exception:
            raise PreS5VoyageEvaluationBatchTransportError(
                "PRE_S5_VOYAGE_EVALUATION_BATCH_TOKENIZER"
            ) from None
        if expected_tokens != self._activation.expected_token_count:
            raise PreS5VoyageEvaluationBatchTransportError(
                "PRE_S5_VOYAGE_EVALUATION_BATCH_TOKENIZER"
            )
        request = _build_request(
            activation=self._activation,
            questions=tuple(question for _, question in queries),
            api_key=self._api_key,
        )
        self._claim()
        if _utc_now(self._clock) >= self._activation.expires_at:
            self._unknown("PRE_S5_VOYAGE_EVALUATION_BATCH_EXPIRED")
        self._physical_calls = 1
        try:
            response = self._sender.post(request)
            vectors, total_tokens = _parse_response(
                response=response,
                activation=self._activation,
                questions=tuple(question for _, question in queries),
            )
            actual_cost = total_tokens * self._activation.input_microusd_per_token
            if actual_cost > self._activation.cost_cap_microusd:
                raise PreS5VoyageEvaluationBatchTransportError(
                    "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
                )
            self._lease.commit(
                expected_input_tokens=expected_tokens,
                total_tokens=total_tokens,
                actual_cost_microusd=actual_cost,
            )
        except Exception:
            self._unknown("PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE")
        return PreS5VoyageEvaluationBatchResult(
            component_scope=self._activation.component_scope,
            query_manifest_sha256=self._activation.query_manifest_sha256,
            vectors_by_query_sha256={
                hashlib.sha256(question.encode("utf-8")).hexdigest(): vector
                for (_, question), vector in zip(queries, vectors, strict=True)
            },
            expected_input_tokens=expected_tokens,
            total_tokens=total_tokens,
        )

    def _claim(self) -> None:
        with self._lock:
            now = _utc_now(self._clock)
            if self._consumed or now >= self._activation.expires_at:
                raise PreS5VoyageEvaluationBatchTransportError(
                    "PRE_S5_VOYAGE_EVALUATION_BATCH_SINGLE_USE"
                )
            try:
                self._lease.claim_attempt(now=now)
            except Exception:
                raise PreS5VoyageEvaluationBatchTransportError(
                    "PRE_S5_VOYAGE_EVALUATION_BATCH_LEASE"
                ) from None
            self._consumed = True

    def _unknown(self, code: str) -> NoReturn:
        try:
            self._lease.mark_unknown_billing()
        except Exception:
            pass
        raise PreS5VoyageEvaluationBatchTransportError(
            code,
            voyage_physical_calls=self._physical_calls,
        )


def _validate_activation(activation: object) -> None:
    if (
        not isinstance(activation, PreS5VoyageEvaluationBatchActivation)
        or activation.component_scope not in {"EXACT30", "OA112"}
        or activation.expected_query_count != (10 if activation.component_scope == "EXACT30" else 112)
        or activation.provider != "VOYAGE"
        or activation.operation != "CONTEXTUALIZED_QUERY_EMBEDDING"
        or activation.origin != _ORIGIN
        or activation.endpoint != _ENDPOINT
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
        or not 1 <= activation.expected_token_count <= activation.token_cap <= 8_192
    ):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_ACTIVATION"
        )


def _validate_queries(
    *, component_scope: str, query_id_questions: Sequence[tuple[str, str]]
) -> tuple[tuple[str, str], ...]:
    queries = tuple(query_id_questions)
    expected_ids = (
        tuple(f"q{index:02d}" for index in range(1, 11))
        if component_scope == "EXACT30"
        else tuple(f"oa112-q{index:03d}" for index in range(1, 113))
    )
    if (
        len(queries) != len(expected_ids)
        or any(not isinstance(item, tuple) or len(item) != 2 for item in queries)
        or tuple(item[0] for item in queries) != expected_ids
        or len({item[1] for item in queries}) != len(queries)
        or any(not 1 <= len(item[1].encode("utf-8")) <= 8_192 for item in queries)
    ):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_ARGUMENT"
        )
    return queries


def _build_request(
    *,
    activation: PreS5VoyageEvaluationBatchActivation,
    questions: tuple[str, ...],
    api_key: str,
) -> PreS5VoyageHttpRequest:
    body = json.dumps(
        {
            "enable_auto_chunking": False,
            "input_type": "query",
            "inputs": [[question] for question in questions],
            "model": _MODEL,
            "output_dimension": _DIMENSION,
            "output_dtype": "float",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if not 1 <= len(body) <= activation.byte_cap:
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_REQUEST"
        )
    return PreS5VoyageHttpRequest(
        url=f"{_ORIGIN}{_ENDPOINT}",
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


def _parse_response(
    *,
    response: object,
    activation: PreS5VoyageEvaluationBatchActivation,
    questions: tuple[str, ...],
) -> tuple[tuple[tuple[float, ...], ...], int]:
    if (
        not isinstance(response, PreS5VoyageHttpResponse)
        or response.status != 200
        or not 1 <= len(response.body) <= activation.byte_cap
    ):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        )
    try:
        decoded = json.loads(
            response.body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        ) from None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"chunker_version", "data", "model", "usage"}
        or decoded.get("model") != _MODEL
        or not isinstance(decoded.get("data"), list)
        or len(decoded["data"]) != len(questions)
    ):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        )
    vectors: list[tuple[float, ...]] = []
    for outer_index, (outer, question) in enumerate(zip(decoded["data"], questions, strict=True)):
        if (
            not isinstance(outer, dict)
            or set(outer) != {"data", "index"}
            or outer.get("index") != outer_index
            or not isinstance(outer.get("data"), list)
            or len(outer["data"]) != 1
        ):
            raise PreS5VoyageEvaluationBatchTransportError(
                "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
            )
        item = outer["data"][0]
        if (
            not isinstance(item, dict)
            or set(item) != {"embedding", "index", "text"}
            or item.get("index") != 0
            or item.get("text") != question
        ):
            raise PreS5VoyageEvaluationBatchTransportError(
                "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
            )
        vectors.append(_unit_vector(item.get("embedding")))
    usage = decoded.get("usage")
    total_tokens = (
        usage.get("total_tokens")
        if isinstance(usage, dict) and set(usage) == {"total_tokens"}
        else None
    )
    if type(total_tokens) is not int or not 0 <= total_tokens <= activation.token_cap:
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        )
    return tuple(vectors), total_tokens


def _unit_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != _DIMENSION:
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        )
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        ) from None
    if (
        any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
        or not all(math.isfinite(item) for item in vector)
        or abs(math.sqrt(math.fsum(item * item for item in vector)) - 1.0) > 0.00001
    ):
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_RESPONSE"
        )
    return vector


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PreS5VoyageEvaluationBatchTransportError(
            "PRE_S5_VOYAGE_EVALUATION_BATCH_CLOCK"
        )
    return value.astimezone(UTC)
