from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.rag.pre_s5_provider_control import PreS5VoyageEvaluationBatchActivation
from app.rag.pre_s5_voyage_evaluation_batch_transport import (
    PreS5VoyageEvaluationBatchTransport,
    PreS5VoyageEvaluationBatchTransportError,
)
from app.rag.pre_s5_voyage_transport import PreS5VoyageHttpRequest, PreS5VoyageHttpResponse

_NOW = datetime(2026, 8, 11, 4, tzinfo=UTC)


class _Counter:
    model = "voyage-context-4"
    tokenizer_sha256 = "a" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        assert token_cap == 100
        return len(texts)


class _Lease:
    def __init__(self) -> None:
        self.claims = 0
        self.commits: list[tuple[int, int, int]] = []
        self.unknown = 0

    def claim_attempt(self, *, now: datetime) -> None:
        assert now == _NOW
        self.claims += 1

    def commit(
        self, *, expected_input_tokens: int, total_tokens: int, actual_cost_microusd: int
    ) -> None:
        self.commits.append((expected_input_tokens, total_tokens, actual_cost_microusd))

    def mark_unknown_billing(self) -> None:
        self.unknown += 1


class _Sender:
    def __init__(self, response: PreS5VoyageHttpResponse) -> None:
        self.response = response
        self.requests: list[PreS5VoyageHttpRequest] = []

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        self.requests.append(request)
        return self.response


def test_exact30_evaluation_batch_uses_ten_singleton_groups_in_one_physical_call() -> None:
    queries = tuple((f"q{index:02d}", f"question {index}") for index in range(1, 11))
    lease = _Lease()
    sender = _Sender(_response(tuple(question for _, question in queries)))
    transport = PreS5VoyageEvaluationBatchTransport(
        activation=_activation(query_count=10, token_count=10),
        api_key="test-key",
        lease=lease,
        token_counter=_Counter(),
        sender=sender,
        clock=lambda: _NOW,
    )

    result = transport.embed(query_id_questions=queries)

    assert result.voyage_physical_calls == 1
    assert len(result.vectors_by_query_sha256) == 10
    assert lease.claims == 1
    assert lease.commits == [(10, 10, 10)]
    assert lease.unknown == 0
    assert len(sender.requests) == 1
    body = json.loads(sender.requests[0].body)
    assert body["inputs"] == [[question] for _, question in queries]
    assert body["input_type"] == "query"


def test_evaluation_batch_stops_after_one_invalid_response_and_cannot_retry() -> None:
    queries = tuple((f"q{index:02d}", f"question {index}") for index in range(1, 11))
    lease = _Lease()
    sender = _Sender(PreS5VoyageHttpResponse(status=500, headers={}, body=b"failed"))
    transport = PreS5VoyageEvaluationBatchTransport(
        activation=_activation(query_count=10, token_count=10),
        api_key="test-key",
        lease=lease,
        token_counter=_Counter(),
        sender=sender,
        clock=lambda: _NOW,
    )

    with pytest.raises(PreS5VoyageEvaluationBatchTransportError) as first:
        transport.embed(query_id_questions=queries)
    with pytest.raises(PreS5VoyageEvaluationBatchTransportError):
        transport.embed(query_id_questions=queries)

    assert first.value.voyage_physical_calls == 1
    assert len(sender.requests) == 1
    assert lease.claims == 1
    assert lease.unknown == 1


def _activation(*, query_count: int, token_count: int) -> PreS5VoyageEvaluationBatchActivation:
    return PreS5VoyageEvaluationBatchActivation(
        packet_sha256="b" * 64,
        nonce_sha256="c" * 64,
        component_scope="EXACT30",
        query_manifest_sha256="d" * 64,
        scope_claim_sha256="e" * 64,
        expected_query_count=query_count,
        expected_token_count=token_count,
        rate_evidence_sha256="f" * 64,
        tokenizer_sha256="a" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_QUERY_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=_NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=100,
        byte_cap=4_194_304,
        cost_cap_microusd=100,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )


def _response(questions: tuple[str, ...]) -> PreS5VoyageHttpResponse:
    vector = [1.0] + [0.0] * 1023
    return PreS5VoyageHttpResponse(
        status=200,
        headers={},
        body=json.dumps(
            {
                "chunker_version": "fixture",
                "data": [
                    {
                        "data": [{"embedding": vector, "index": 0, "text": question}],
                        "index": index,
                    }
                    for index, question in enumerate(questions)
                ],
                "model": "voyage-context-4",
                "usage": {"total_tokens": len(questions)},
            },
            separators=(",", ":"),
        ).encode(),
    )
