from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.rag.pre_s5_provider_control import (
    PreS5VoyageDocumentBatchActivation,
    PreS5VoyageEvaluationBatchActivation,
    PreS5VoyageQueryActivation,
)
from app.rag.pre_s5_voyage_evaluation_batch_transport import (
    PreS5VoyageEvaluationBatchTransport,
    PreS5VoyageEvaluationBatchTransportError,
)
from app.rag.pre_s5_voyage_evaluation_batch_transport import (
    _parse_response as parse_evaluation_response,
)
from app.rag.pre_s5_voyage_query_transport import (
    PreS5VoyageContext4QueryEmbedder,
)
from app.rag.pre_s5_voyage_query_transport import (
    _parse_response as parse_query_response,
)
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageContext4Transport,
    PreS5VoyageHttpRequest,
    PreS5VoyageHttpResponse,
    PreS5VoyageTransportError,
)
from app.rag.pre_s5_voyage_transport import (
    _parse_response as parse_document_response,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_public_voyage_cli import _document_batch_failure_summary
from app.rag.rag_v2_voyage_batching import VoyageContextSegment, VoyageDocumentBatch

_NOW = datetime(2026, 8, 12, 10, tzinfo=UTC)
_QUESTION = "Which assumptions support the reported result?"
_RAW_MARKER = "provider-private-response-marker"
_SCOPE_CLAIM = f"rvs_{'a' * 32}"
_PARSER_NAMES = ("document", "evaluation", "query")
_LEAF_CASES = (
    ("STATUS", "STATUS"),
    ("BODY_SIZE_OR_TYPE", "BODY_SIZE_OR_TYPE"),
    ("BODY_UTF8_OR_JSON", "BODY_UTF8_OR_JSON"),
    ("ENVELOPE_REQUIRED_FIELDS", "ENVELOPE_REQUIRED_FIELDS"),
    ("MODEL", "MODEL"),
    ("USAGE", "USAGE"),
    ("GROUP_COUNT", "GROUP_COUNT"),
    ("GROUP_FIELDS_OR_INDEX", "GROUP_FIELDS_OR_INDEX"),
    ("CHUNK_COUNT", "CHUNK_COUNT"),
    ("CHUNK_FIELDS_OR_INDEX", "CHUNK_FIELDS_OR_INDEX"),
    ("CHUNK_TEXT", "CHUNK_TEXT"),
    ("VECTOR_DIMENSION", "VECTOR_DIMENSION"),
    ("VECTOR_NUMBER", "VECTOR_NUMBER"),
    ("VECTOR_FINITE", "BODY_UTF8_OR_JSON"),
    ("VECTOR_NORM", "VECTOR_NORM"),
    ("COST_CAP", "COST_CAP"),
)


def test_contextualized_response_accepts_official_optional_fields_for_all_transports() -> None:
    response = _response(include_chunker_version=False, include_text=False)

    document = _parse("document", response)
    evaluation_vectors, evaluation_tokens = _parse("evaluation", response)
    query = _parse("query", response)

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

    document = _parse("document", response)
    evaluation_vectors, _ = _parse("evaluation", response)
    query = _parse("query", response)

    assert document.vectors.shape == (1, 1024)
    assert len(evaluation_vectors) == 1
    assert len(query.vector) == 1024


def test_contextualized_response_ignores_bounded_harmless_additive_fields() -> None:
    payload = _payload(include_chunker_version=True, include_text=True)
    payload["provider_request_metadata"] = {"opaque": True}
    outer = payload["data"][0]
    outer["provider_group_metadata"] = "ignored"
    item = outer["data"][0]
    item["provider_item_metadata"] = 7
    payload["usage"]["provider_usage_metadata"] = {"cached": False}
    response = _response_from_payload(payload)

    for parser_name in _PARSER_NAMES:
        result = _parse(parser_name, response)
        assert result is not None


@pytest.mark.parametrize(("case", "expected_leaf"), _LEAF_CASES)
@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_contextualized_response_reports_the_same_exact_leaf_for_all_transports(
    case: str,
    expected_leaf: str,
    parser_name: str,
) -> None:
    response = _invalid_response(case)

    with pytest.raises(Exception) as captured:
        _parse(parser_name, response, force_cost_cap_failure=case == "COST_CAP")

    assert str(captured.value) == "PRE_S5_VOYAGE_RESPONSE_INVALID"
    assert getattr(captured.value, "response_validation_leaf", None) == expected_leaf
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_contextualized_response_rejects_duplicate_json_without_retaining_raw_content(
    parser_name: str,
) -> None:
    response = PreS5VoyageHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=(
            b'{"data":[],"model":"voyage-context-4","model":"'
            + _RAW_MARKER.encode()
            + b'","usage":{"total_tokens":1}}'
        ),
    )

    with pytest.raises(Exception) as captured:
        _parse(parser_name, response)

    assert getattr(captured.value, "response_validation_leaf", None) == "BODY_UTF8_OR_JSON"
    assert _RAW_MARKER not in str(captured.value)
    assert _RAW_MARKER not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_contextualized_response_validates_known_optional_fields(parser_name: str) -> None:
    invalid_chunker = _payload(include_chunker_version=True, include_text=False)
    invalid_chunker["chunker_version"] = ""
    with pytest.raises(Exception) as chunker_error:
        _parse(parser_name, _response_from_payload(invalid_chunker))
    assert (
        getattr(chunker_error.value, "response_validation_leaf", None) == "ENVELOPE_REQUIRED_FIELDS"
    )

    invalid_text = _payload(include_chunker_version=False, include_text=True)
    invalid_text["data"][0]["data"][0]["text"] = 7
    with pytest.raises(Exception) as text_error:
        _parse(parser_name, _response_from_payload(invalid_text))
    assert getattr(text_error.value, "response_validation_leaf", None) == "CHUNK_TEXT"


def test_document_attempt_exposes_only_the_leaf_and_stops_after_one_failed_batch() -> None:
    lease = _Lease()
    sender = _Sender(
        _response(
            include_chunker_version=True,
            include_text=True,
            returned_text=_RAW_MARKER,
        )
    )
    activation = _document_activation()
    batch = _document_batch()
    transport = PreS5VoyageContext4Transport(
        activation=activation,
        api_key="test-key",
        lease=lease,
        token_counter=_TokenCounter(),
        sender=sender,
        clock=lambda: _NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        transport.embed_document_batch(
            batch_plan_sha256=activation.batch_plan_sha256,
            batch=batch,
        )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        transport.embed_document_batch(
            batch_plan_sha256=activation.batch_plan_sha256,
            batch=batch,
        )

    assert sender.calls == 1
    assert lease.claims == 1
    assert lease.unknown_billing == 1
    receipt = transport.content_free_summary()
    assert receipt["responseValidationLeaf"] == "CHUNK_TEXT"
    assert _RAW_MARKER not in json.dumps(receipt, sort_keys=True)
    assert _QUESTION not in json.dumps(receipt, sort_keys=True)

    cli_receipt = _document_batch_failure_summary(
        transport=transport,
        preparation=SimpleNamespace(plan=SimpleNamespace(batches=(batch,))),  # type: ignore[arg-type]
        accumulator=SimpleNamespace(completed_batch_ids=()),  # type: ignore[arg-type]
        batch=batch,
    )
    assert cli_receipt["responseValidationLeaf"] == "CHUNK_TEXT"
    assert cli_receipt["externalPhysicalCalls"] == 1
    assert cli_receipt["completedBatchCount"] == 0


def test_evaluation_attempt_exposes_the_same_content_free_leaf() -> None:
    questions = tuple((f"q{index:02d}", f"question {index}") for index in range(1, 11))
    response = _evaluation_response(
        tuple(question for _, question in questions),
        first_returned_text=_RAW_MARKER,
    )
    lease = _Lease()
    transport = PreS5VoyageEvaluationBatchTransport(
        activation=replace(
            _evaluation_activation(),
            expected_query_count=10,
            expected_token_count=10,
            token_cap=100,
            cost_cap_microusd=100,
        ),
        api_key="test-key",
        lease=lease,
        token_counter=_EvaluationTokenCounter(),
        sender=_Sender(response),
        clock=lambda: _NOW,
    )

    with pytest.raises(PreS5VoyageEvaluationBatchTransportError) as captured:
        transport.embed(query_id_questions=questions)

    assert captured.value.response_validation_leaf == "CHUNK_TEXT"
    assert captured.value.voyage_physical_calls == 1
    assert lease.claims == 1
    assert lease.unknown_billing == 1


def test_query_attempt_exposes_the_same_content_free_leaf() -> None:
    activation = replace(
        _query_activation(),
        scope_claim_sha256=hashlib.sha256(_SCOPE_CLAIM.encode()).hexdigest(),
    )
    lease = _Lease()
    embedder = PreS5VoyageContext4QueryEmbedder(
        activation=activation,
        api_key="test-key",
        lease=lease,
        token_counter=_QueryTokenCounter(),
        sender=_Sender(
            _response(
                include_chunker_version=False,
                include_text=True,
                returned_text=_RAW_MARKER,
            )
        ),
        clock=lambda: _NOW,
    )

    with pytest.raises(Exception) as captured:
        embedder.embed_query_with_receipt(
            question=_QUESTION,
            scope_claim_id=_SCOPE_CLAIM,
            external_query_consent_granted=True,
        )

    assert getattr(captured.value, "response_validation_leaf", None) == "CHUNK_TEXT"
    assert getattr(captured.value, "voyage_physical_calls", None) == 1
    assert lease.claims == 1
    assert lease.unknown_billing == 1


def test_validation_leaf_set_is_closed_and_content_free() -> None:
    assert {leaf for _, leaf in _LEAF_CASES} | {"VECTOR_FINITE"} == {
        "STATUS",
        "BODY_SIZE_OR_TYPE",
        "BODY_UTF8_OR_JSON",
        "ENVELOPE_REQUIRED_FIELDS",
        "MODEL",
        "USAGE",
        "GROUP_COUNT",
        "GROUP_FIELDS_OR_INDEX",
        "CHUNK_COUNT",
        "CHUNK_FIELDS_OR_INDEX",
        "CHUNK_TEXT",
        "VECTOR_DIMENSION",
        "VECTOR_NUMBER",
        "VECTOR_FINITE",
        "VECTOR_NORM",
        "COST_CAP",
    }


def _parse(
    parser_name: str,
    response: PreS5VoyageHttpResponse,
    *,
    force_cost_cap_failure: bool = False,
) -> object:
    if parser_name == "document":
        activation = _document_activation()
        if force_cost_cap_failure:
            activation = replace(activation, cost_cap_microusd=0)
        return parse_document_response(
            response=response,
            groups=(_group(),),
            activation=activation,
        )
    if parser_name == "evaluation":
        activation = _evaluation_activation()
        if force_cost_cap_failure:
            activation = replace(activation, cost_cap_microusd=0)
        return parse_evaluation_response(
            response=response,
            activation=activation,
            questions=(_QUESTION,),
        )
    if parser_name == "query":
        activation = _query_activation()
        if force_cost_cap_failure:
            activation = replace(activation, cost_cap_microusd=0)
        return parse_query_response(
            response=response,
            activation=activation,
            question=_QUESTION,
        )
    raise AssertionError(f"unknown parser: {parser_name}")


def _invalid_response(case: str) -> PreS5VoyageHttpResponse:
    if case == "STATUS":
        return replace(_response(include_chunker_version=False, include_text=False), status=503)
    if case == "BODY_SIZE_OR_TYPE":
        return PreS5VoyageHttpResponse(status=200, headers={}, body=b"")
    if case == "BODY_UTF8_OR_JSON":
        return PreS5VoyageHttpResponse(status=200, headers={}, body=b"\xff")

    payload = _payload(include_chunker_version=False, include_text=False)
    outer = payload["data"][0]
    item = outer["data"][0]
    if case == "ENVELOPE_REQUIRED_FIELDS":
        payload.pop("usage")
    elif case == "MODEL":
        payload["model"] = "different-model"
    elif case == "USAGE":
        payload["usage"]["total_tokens"] = True
    elif case == "GROUP_COUNT":
        payload["data"] = []
    elif case == "GROUP_FIELDS_OR_INDEX":
        outer["index"] = 1
    elif case == "CHUNK_COUNT":
        outer["data"] = []
    elif case == "CHUNK_FIELDS_OR_INDEX":
        item.pop("embedding")
    elif case == "CHUNK_TEXT":
        item["text"] = _RAW_MARKER
    elif case == "VECTOR_DIMENSION":
        item["embedding"] = [1.0] + [0.0] * 1022
    elif case == "VECTOR_NUMBER":
        item["embedding"][1] = "0"
    elif case == "VECTOR_FINITE":
        item["embedding"][1] = float("nan")
    elif case == "VECTOR_NORM":
        item["embedding"] = [0.0] * 1024
    elif case == "COST_CAP":
        pass
    else:
        raise AssertionError(f"unknown leaf case: {case}")
    return _response_from_payload(payload)


def _response(
    *,
    include_chunker_version: bool,
    include_text: bool,
    returned_text: str = _QUESTION,
) -> PreS5VoyageHttpResponse:
    return _response_from_payload(
        _payload(
            include_chunker_version=include_chunker_version,
            include_text=include_text,
            returned_text=returned_text,
        )
    )


def _payload(
    *,
    include_chunker_version: bool,
    include_text: bool,
    returned_text: str = _QUESTION,
) -> dict[str, object]:
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
    return payload


def _response_from_payload(payload: dict[str, object]) -> PreS5VoyageHttpResponse:
    return PreS5VoyageHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload, separators=(",", ":"), allow_nan=True).encode(),
    )


def _evaluation_response(
    questions: tuple[str, ...],
    *,
    first_returned_text: str,
) -> PreS5VoyageHttpResponse:
    vector = [1.0] + [0.0] * 1023
    payload = {
        "data": [
            {
                "data": [
                    {
                        "embedding": vector,
                        "index": 0,
                        "text": first_returned_text if index == 0 else question,
                    }
                ],
                "index": index,
            }
            for index, question in enumerate(questions)
        ],
        "model": "voyage-context-4",
        "usage": {"total_tokens": len(questions)},
    }
    return _response_from_payload(payload)


def _group() -> VoyagePreChunkedDocumentGroup:
    text_hash = hashlib.sha256(_QUESTION.encode()).hexdigest()
    return VoyagePreChunkedDocumentGroup(
        source_id="src_exact_01",
        source_revision_id="srv_exact_01",
        context_set_hash="a" * 64,
        chunks=(
            VoyagePreChunkedChunk(
                chunk_id=f"rag_v2_chk_{'1' * 32}",
                canonical_text=_QUESTION,
                canonical_text_sha256=text_hash,
                embedding_input_hash="b" * 64,
                token_count=1,
            ),
        ),
    )


def _document_batch() -> VoyageDocumentBatch:
    group = _group()
    segment = VoyageContextSegment(
        component_scope="EXACT30",
        source_id=group.source_id,
        source_revision_id=group.source_revision_id,
        segment_ordinal=1,
        segment_count=1,
        token_count=1,
        group=group,
        segment_manifest_sha256="7" * 64,
    )
    return VoyageDocumentBatch(
        batch_id="ps5_voyage_doc_0001_1234567890abcdef",
        batch_ordinal=1,
        batch_count=1,
        token_count=1,
        chunk_count=1,
        group_count=1,
        estimated_response_bytes=286_720,
        segments=(segment,),
        batch_manifest_sha256="4" * 64,
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


class _TokenCounter:
    model = "voyage-context-4"
    tokenizer_sha256 = "6" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        assert texts == (_QUESTION,)
        assert token_cap == 120_000
        return 1


class _EvaluationTokenCounter:
    model = "voyage-context-4"
    tokenizer_sha256 = "6" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        assert len(texts) == 10
        assert token_cap == 100
        return 10


class _QueryTokenCounter:
    model = "voyage-context-4"
    tokenizer_sha256 = "5" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        assert texts == (_QUESTION,)
        assert token_cap == 1
        return 1


class _Lease:
    def __init__(self) -> None:
        self.claims = 0
        self.unknown_billing = 0

    def claim_attempt(self, *, now: datetime) -> None:
        assert now == _NOW
        self.claims += 1

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        raise AssertionError(
            (expected_input_tokens, total_tokens, actual_cost_microusd),
        )

    def mark_unknown_billing(self) -> None:
        self.unknown_billing += 1


class _Sender:
    def __init__(self, response: PreS5VoyageHttpResponse) -> None:
        self._response = response
        self.calls = 0

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        assert request.url == "https://api.voyageai.com/v1/contextualizedembeddings"
        self.calls += 1
        return self._response
