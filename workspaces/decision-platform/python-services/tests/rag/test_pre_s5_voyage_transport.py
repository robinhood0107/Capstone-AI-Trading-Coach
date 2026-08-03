from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.rag.pre_s5_provider_control import PreS5VoyageActivation
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageHttpResponse,
    PreS5VoyageTransportError,
    PreS5VoyageContext4Transport,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)


NOW = datetime(2026, 8, 3, 1, tzinfo=UTC)


def test_voyage_context4_transport_uses_fixed_prechunked_one_shot_request_and_discards_raw_response() -> None:
    sender = _FixtureSender(response=_response_for(_groups()))
    transport = PreS5VoyageContext4Transport(
        activation=_activation(),
        api_key="test-key",
        sender=sender,
        clock=lambda: NOW,
    )

    vectors = transport.embed_document_groups(groups=_groups())

    assert vectors.shape == (2, 1024)
    assert vectors.dtype == np.float32
    assert sender.calls == 1
    assert transport.external_physical_calls == 1
    assert len(sender.requests) == 1
    request = sender.requests[0]
    assert request.url == "https://api.voyageai.com/v1/contextualizedembeddings"
    assert request.timeout_seconds == 20
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.body)
    assert body == {
        "enable_auto_chunking": False,
        "input_type": "document",
        "inputs": [["first canonical chunk"], ["second canonical chunk"]],
        "model": "voyage-context-4",
        "output_dimension": 1024,
        "output_dtype": "float",
    }
    receipt = json.dumps(transport.content_free_summary(), ensure_ascii=False, sort_keys=True)
    assert "test-key" not in receipt
    assert "canonical chunk" not in receipt
    assert "response" not in receipt


def test_voyage_context4_transport_marks_first_attempt_consumed_and_never_retries() -> None:
    sender = _FixtureSender(error=OSError("fixture transport down"))
    transport = PreS5VoyageContext4Transport(
        activation=_activation(),
        api_key="test-key",
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE"):
        transport.embed_document_groups(groups=_groups())
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        transport.embed_document_groups(groups=_groups())
    assert sender.calls == 1
    assert transport.external_physical_calls == 1


def test_voyage_context4_transport_rejects_invalid_or_expired_input_before_any_physical_call() -> None:
    sender = _FixtureSender(response=_response_for(_groups()))
    expired = _activation(expires_at=NOW - timedelta(seconds=1))
    transport = PreS5VoyageContext4Transport(
        activation=expired,
        api_key="test-key",
        sender=sender,
        clock=lambda: NOW,
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_ACTIVATION_EXPIRED"):
        transport.embed_document_groups(groups=_groups())
    assert sender.calls == 0
    assert transport.external_physical_calls == 0

    duplicate = (_groups()[0], _groups()[0])
    fresh = PreS5VoyageContext4Transport(
        activation=_activation(),
        api_key="test-key",
        sender=sender,
        clock=lambda: NOW,
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_REQUEST_INVALID"):
        fresh.embed_document_groups(groups=duplicate)
    assert sender.calls == 0
    assert fresh.external_physical_calls == 0


def test_voyage_context4_transport_rejects_cross_group_or_nonunit_response_without_followup_call() -> None:
    malformed = _response_for(_groups())
    body = json.loads(malformed.body)
    body["data"][1]["data"][0]["text"] = "wrong group text"
    malformed = PreS5VoyageHttpResponse(status=200, headers={}, body=json.dumps(body).encode("utf-8"))
    sender = _FixtureSender(response=malformed)
    transport = PreS5VoyageContext4Transport(
        activation=_activation(),
        api_key="test-key",
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        transport.embed_document_groups(groups=_groups())
    assert sender.calls == 1
    assert transport.external_physical_calls == 1


def test_voyage_context4_transport_keeps_default_sender_disabled_without_consuming_packet() -> None:
    transport = PreS5VoyageContext4Transport(
        activation=_activation(),
        api_key="test-key",
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_OUTBOUND_DISABLED"):
        transport.embed_document_groups(groups=_groups())

    assert transport.external_physical_calls == 0
    assert transport.content_free_summary()["logicalCallsConsumed"] == 0


def test_voyage_context4_transport_rejects_nonunit_vector_after_exactly_one_attempt() -> None:
    malformed = _response_for(_groups())
    body = json.loads(malformed.body)
    body["data"][0]["data"][0]["embedding"] = [0.0] * 1024
    sender = _FixtureSender(
        response=PreS5VoyageHttpResponse(
            status=200,
            headers={},
            body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        )
    )
    transport = PreS5VoyageContext4Transport(
        activation=_activation(),
        api_key="test-key",
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        transport.embed_document_groups(groups=_groups())
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        transport.embed_document_groups(groups=_groups())
    assert sender.calls == 1
    assert transport.external_physical_calls == 1


class _FixtureSender:
    """network 없이 fixed response/error 한 번만 내는 transport seam이다."""

    def __init__(self, *, response: PreS5VoyageHttpResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls = 0
        self.requests = []

    def post(self, request: object) -> PreS5VoyageHttpResponse:
        self.calls += 1
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _activation(*, expires_at: datetime | None = None) -> PreS5VoyageActivation:
    return PreS5VoyageActivation(
        packet_sha256="a" * 64,
        bundle_manifest_sha256="b" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=expires_at or NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=120_000,
        byte_cap=4_194_304,
        cost_cap_microusd=100_000,
        retry_count=0,
        raw_artifact_count=0,
    )


def _groups() -> tuple[VoyagePreChunkedDocumentGroup, ...]:
    return (
        VoyagePreChunkedDocumentGroup(
            source_id="src_transport_first_001",
            source_revision_id="srv_transport_first_001",
            context_set_hash="c" * 64,
            chunks=(
                VoyagePreChunkedChunk(
                    chunk_id="rag_v2_chk_" + "1" * 32,
                    canonical_text="first canonical chunk",
                    canonical_text_sha256=_sha256("first canonical chunk"),
                    embedding_input_hash="e" * 64,
                ),
            ),
        ),
        VoyagePreChunkedDocumentGroup(
            source_id="src_transport_second_001",
            source_revision_id="srv_transport_second_001",
            context_set_hash="f" * 64,
            chunks=(
                VoyagePreChunkedChunk(
                    chunk_id="rag_v2_chk_" + "2" * 32,
                    canonical_text="second canonical chunk",
                    canonical_text_sha256=_sha256("second canonical chunk"),
                    embedding_input_hash="a" * 64,
                ),
            ),
        ),
    )


def _response_for(groups: tuple[VoyagePreChunkedDocumentGroup, ...]) -> PreS5VoyageHttpResponse:
    data: list[dict[str, object]] = []
    for group_index, group in enumerate(groups):
        chunks: list[dict[str, object]] = []
        for chunk_index, chunk in enumerate(group.chunks):
            vector = [0.0] * 1024
            vector[group_index + chunk_index] = 1.0
            chunks.append({"embedding": vector, "index": chunk_index, "text": chunk.canonical_text})
        data.append({"data": chunks, "index": group_index})
    return PreS5VoyageHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(
            {
                "chunker_version": "fixture",
                "data": data,
                "model": "voyage-context-4",
                "usage": {"total_tokens": 4},
            },
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
