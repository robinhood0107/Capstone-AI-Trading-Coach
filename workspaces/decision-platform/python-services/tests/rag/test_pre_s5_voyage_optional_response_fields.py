from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from app.rag.pre_s5_provider_control import (
    PreS5VoyageDocumentBatchActivation,
    PreS5VoyageEvaluationBatchActivation,
    PreS5VoyageQueryActivation,
)
from app.rag.pre_s5_voyage_evaluation_batch_transport import (
    _parse_response as parse_evaluation_response,
)
from app.rag.pre_s5_voyage_query_transport import _parse_response as parse_query_response
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageHttpResponse,
    _contextualized_response_envelope_is_valid,
    _contextualized_response_item_is_valid,
    _parse_response as parse_document_response,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)


_NOW = datetime(2026, 8, 12, 10, tzinfo=UTC)
_QUESTION = "Which assumptions support the reported result?"


def test_contextualized_response_accepts_official_optional_fields_for_all_transports() -> None:
    response = _response(include_chunker_version=False, include_text=False)
    group = _group()

    document = parse_document_response(
        response=response,
        groups=(group,),
        activation=_document_activation(),
    )
    evaluation_vectors, evaluation_tokens = parse_evaluation_response(
        response=response,
        activation=_evaluation_activation(),
        questions=(_QUESTION,),
    )
    query = parse_query_response(
        response=response,
        activation=_query_activation(),
        question=_QUESTION,
    )

    assert document.vectors.shape == (1, 1024)
    assert document.total_tokens == 1
    assert len(evaluation_vectors) == 1
    assert evaluation_tokens == 1
    assert len(query.vector) == 1024
    assert query.total_tokens == 1


def test_contextualized_response_keeps_returned_text_bound_when_present() -> None:
    response = _response(
        include_chunker_version=True,
        include_text=True,
        returned_text=_QUESTION,
    )

    document = parse_document_response(
        response=response,
        groups=(_group(),),
        activation=_document_activation(),
    )
    evaluation_vectors, _ = parse_evaluation_response(
        response=response,
        activation=_evaluation_activation(),
        questions=(_QUESTION,),
    )
    query = parse_query_response(
        response=response,
        activation=_query_activation(),
        question=_QUESTION,
    )

    assert document.vectors.shape == (1, 1024)
    assert len(evaluation_vectors) == 1
    assert len(query.vector) == 1024


def test_contextualized_response_still_rejects_mismatched_or_extra_optional_fields() -> None:
    assert not _contextualized_response_item_is_valid(
        {"embedding": [1.0], "index": 0, "text": "different"},
        expected_index=0,
        expected_text=_QUESTION,
    )
    assert not _contextualized_response_item_is_valid(
        {"embedding": [1.0], "index": 0, "provider_debug": "unexpected"},
        expected_index=0,
        expected_text=_QUESTION,
    )
    assert not _contextualized_response_envelope_is_valid(
        {
            "chunker_version": "",
            "data": [],
            "model": "voyage-context-4",
            "usage": {"total_tokens": 0},
        },
        model="voyage-context-4",
    )


def _response(
    *,
    include_chunker_version: bool,
    include_text: bool,
    returned_text: str = _QUESTION,
) -> PreS5VoyageHttpResponse:
    item: dict[str, object] = {
        "embedding": [1.0] + [0.0] * 1023,
        "index": 0,
    }
    if include_text:
        item["text"] = returned_text
    payload: dict[str, object] = {
        "data": [{"data": [item], "index": 0}],
        "model": "voyage-context-4",
        "usage": {"total_tokens": 1},
    }
    if include_chunker_version:
        payload["chunker_version"] = "1.0.0"
    return PreS5VoyageHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload, separators=(",", ":")).encode(),
    )


def _group() -> VoyagePreChunkedDocumentGroup:
    text_hash = hashlib.sha256(_QUESTION.encode()).hexdigest()
    return VoyagePreChunkedDocumentGroup(
        source_id="src_exact_01",
        source_revision_id="srv_exact_01",
        context_set_hash="a" * 64,
        chunks=(
            VoyagePreChunkedChunk(
                chunk_id="chk_exact_01_0001",
                canonical_text=_QUESTION,
                canonical_text_sha256=text_hash,
                embedding_input_hash="b" * 64,
                token_count=1,
            ),
        ),
    )


def _document_activation() -> PreS5VoyageDocumentBatchActivation:
    return PreS5VoyageDocumentBatchActivation(
        packet_sha256="1" * 64,
        nonce_sha256="2" * 64,
        batch_plan_sha256="3" * 64,
        batch_id="ps5_voyage_doc_0001_1234567890abcdef",
        batch_manifest_sha256="4" * 64,
        batch_ordinal=1,
        batch_count=1,
        expected_token_count=1,
        expected_chunk_count=1,
        expected_group_count=1,
        rate_evidence_sha256="5" * 64,
        tokenizer_sha256="6" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=_NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=120_000,
        byte_cap=16_777_216,
        cost_cap_microusd=120_000,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )


def _evaluation_activation() -> PreS5VoyageEvaluationBatchActivation:
    return PreS5VoyageEvaluationBatchActivation(
        packet_sha256="1" * 64,
        nonce_sha256="2" * 64,
        component_scope="EXACT30",
        query_manifest_sha256="3" * 64,
        scope_claim_sha256="4" * 64,
        expected_query_count=1,
        expected_token_count=1,
        rate_evidence_sha256="5" * 64,
        tokenizer_sha256="6" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_QUERY_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=_NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=1,
        byte_cap=4_194_304,
        cost_cap_microusd=1,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )


def _query_activation() -> PreS5VoyageQueryActivation:
    return PreS5VoyageQueryActivation(
        packet_sha256="1" * 64,
        nonce_sha256="2" * 64,
        query_sha256=hashlib.sha256(_QUESTION.encode()).hexdigest(),
        scope_claim_sha256="3" * 64,
        rate_evidence_sha256="4" * 64,
        tokenizer_sha256="5" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_QUERY_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=_NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=1,
        byte_cap=4_194_304,
        cost_cap_microusd=1,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )
