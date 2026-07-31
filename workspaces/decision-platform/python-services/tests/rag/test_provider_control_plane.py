from __future__ import annotations

import hashlib
import json

import pytest

from app.rag.external_processing_corpus import load_external_processing_corpus
from app.rag.fixture_answering import EvidenceChunk
from app.rag.provider_control_plane import (
    GEMINI_INTERACTIONS_PATH,
    GEMINI_MODEL,
    ApprovalPurpose,
    GeminiFixtureFailure,
    NetworkDisabledGeminiInteractionsTransport,
    NetworkDisabledVoyageTransport,
    OutboundDisabledGeminiExecutor,
    OutboundDisabledVoyageExecutor,
    ProviderControlPlaneError,
    ProviderUsageLedger,
    UsageState,
    build_gemini_interaction_request,
    build_voyage_generation_plan,
    execute_gemini_fixture,
    execute_voyage_fixture,
    validate_gemini_approval_packet,
    validate_voyage_approval_packet,
    validate_voyage_generation,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
GENERATION_ID = "rag_gen_" + "d" * 32


def _voyage_plan():
    return build_voyage_generation_plan(
        corpus=load_external_processing_corpus(),
        project_fingerprint_sha256=HASH_A,
        balance_snapshot_sha256=HASH_B,
    )


def _evidence() -> tuple[EvidenceChunk, ...]:
    corpus = load_external_processing_corpus()
    card = corpus.cards[0]
    return (
        EvidenceChunk(
            citation_id="cit_1",
            source_id=card.source_id,
            source_revision_id="src_rev_fixture",
            chunk_revision_id="rag_chk_" + "1" * 32,
            generation_id=GENERATION_ID,
            title=str(card.front_matter["title"]),
            section_title="핵심 claim",
            canonical_url=str(card.front_matter["canonicalUrl"]),
            content=card.canonical_body,
            access_level="PUBLIC",
            source_status="VERIFIED",
            external_processing_allowed=True,
        ),
    )


def test_voyage_plan_is_single_shot_stable_bounded_and_offline() -> None:
    first = _voyage_plan()
    second = _voyage_plan()

    assert first == second
    assert first.model == "voyage-context-4"
    assert first.profile_id == "voyage_context_4_1024_v1"
    assert first.output_dimension == 1024
    assert first.chunk_overlap == 0
    assert first.max_documents_per_request == 32
    assert first.max_tokens_per_request == 80_000
    assert first.max_chunks_per_request == 256
    assert first.max_request_bytes == 4_194_304
    assert first.max_response_bytes == 16_777_216
    assert first.concurrency == 1
    assert first.retry_count == 0
    assert first.paid_hard_cap_usd == 0
    assert first.official_files_api_calls == 0
    assert first.official_batch_api_calls == 0
    assert first.tokenizer_mode == "OFFICIAL_TOKENIZER_REVIEW_REQUIRED"
    assert len(first.batches) == 1
    assert len(first.batches[0].documents) == 30
    assert first.batches[0].estimated_token_upper_bound <= 80_000
    assert [item.source_id for item in first.batches[0].documents] == sorted(
        item.source_id for item in first.batches[0].documents
    )
    assert len(first.context_set_hash) == 64
    assert len(first.plan_sha256) == 64
    assert len(first.generation_sha256) == 64


def test_voyage_packet_and_live_executor_remain_fail_closed() -> None:
    plan = _voyage_plan()
    packet = {
        "schemaVersion": "s4-2c-voyage-approval/v1",
        "provider": "VOYAGE",
        "state": "APPROVED",
        "purpose": "EVALUATION_ONLY",
        "corpusManifestSha256": plan.corpus_manifest_sha256,
        "contextSetHash": plan.context_set_hash,
        "generationPlanSha256": plan.plan_sha256,
        "projectFingerprintSha256": HASH_A,
        "balanceSnapshotSha256": HASH_B,
        "zdrOptOutEvidenceSha256": HASH_C,
        "physicalBatchCap": 1,
        "retryCount": 0,
        "paidHardCapUsd": 0,
    }

    validated = validate_voyage_approval_packet(packet, plan=plan)
    assert validated.purpose is ApprovalPurpose.EVALUATION_ONLY

    invalid = dict(packet)
    invalid["paidHardCapUsd"] = 1
    with pytest.raises(ProviderControlPlaneError):
        validate_voyage_approval_packet(invalid, plan=plan)

    executor = OutboundDisabledVoyageExecutor()
    with pytest.raises(ProviderControlPlaneError, match="voyage_outbound_disabled"):
        executor.execute(plan=plan, approval=None)
    assert executor.external_physical_calls == 0


def test_voyage_mock_stops_on_first_failure_and_requires_complete_generation() -> None:
    plan = _voyage_plan()
    failed_transport = NetworkDisabledVoyageTransport(fail_batch_indexes={0})

    with pytest.raises(ProviderControlPlaneError, match="voyage_fixture_transport_failed"):
        execute_voyage_fixture(plan=plan, transport=failed_transport)
    assert failed_transport.fixture_attempts == 1
    assert failed_transport.external_physical_calls == 0

    vectors = {
        document.source_id: [[1.0] + [0.0] * 1023 for _ in document.chunks]
        for document in plan.batches[0].documents
    }
    complete_transport = NetworkDisabledVoyageTransport(responses={0: vectors})
    response = execute_voyage_fixture(plan=plan, transport=complete_transport)
    validated = validate_voyage_generation(plan=plan, responses=response)

    assert validated.complete is True
    assert validated.document_count == 30
    assert validated.chunk_count == 30
    assert validated.generation_sha256 == plan.generation_sha256
    assert complete_transport.external_physical_calls == 0

    missing = {**vectors}
    missing.pop(next(iter(missing)))
    with pytest.raises(ProviderControlPlaneError, match="voyage_generation_incomplete"):
        validate_voyage_generation(plan=plan, responses=(missing,))


def test_usage_ledger_is_reservation_first_and_unknown_billing_is_terminal() -> None:
    ledger = ProviderUsageLedger()
    reserved = ledger.reserve(
        request_id="s4-5-eval-001",
        provider="VOYAGE",
        purpose=ApprovalPurpose.EVALUATION_ONLY,
        plan_sha256=HASH_A,
    )
    assert reserved.state is UsageState.RESERVED
    assert ledger.reserve(
        request_id="s4-5-eval-001",
        provider="VOYAGE",
        purpose=ApprovalPurpose.EVALUATION_ONLY,
        plan_sha256=HASH_A,
    ) == reserved

    unknown = ledger.mark_unknown_billing(request_id="s4-5-eval-001")
    assert unknown.state is UsageState.UNKNOWN_BILLING
    with pytest.raises(ProviderControlPlaneError):
        ledger.commit(request_id="s4-5-eval-001", input_tokens=1, output_tokens=0)
    with pytest.raises(ProviderControlPlaneError):
        ledger.reserve(
            request_id="s4-5-eval-001",
            provider="VOYAGE",
            purpose=ApprovalPurpose.EVALUATION_ONLY,
            plan_sha256=HASH_B,
        )


def test_gemini_interactions_request_has_current_stateless_no_tool_shape() -> None:
    request = build_gemini_interaction_request(
        prompt="typed fixture prompt",
        evaluation_manifest_sha256=HASH_A,
    )

    assert request["model"] == GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert request["store"] is False
    assert request["background"] is False
    assert request["stream"] is False
    assert request["generation_config"] == {
        "max_output_tokens": 800,
        "thinking_level": "minimal",
        "thinking_summaries": "none",
    }
    assert request["tool_choice"] == "none"
    assert request["response_format"]["type"] == "json_schema"
    for forbidden in (
        "agent",
        "agent_config",
        "cache",
        "file",
        "grounding",
        "previous_interaction_id",
        "tools",
        "url",
    ):
        assert forbidden not in request
    assert GEMINI_INTERACTIONS_PATH == "/v1beta/interactions"


def test_gemini_packet_purposes_are_separate_and_live_executor_is_disabled() -> None:
    packet = {
        "schemaVersion": "s4-4g-gemini-approval/v1",
        "provider": "GEMINI",
        "state": "APPROVED",
        "purpose": "EVALUATION",
        "model": GEMINI_MODEL,
        "evaluationManifestSha256": HASH_A,
        "promptSha256": HASH_B,
        "responseSchemaSha256": HASH_C,
        "projectFingerprintSha256": "d" * 64,
        "zdrEvidenceSha256": "e" * 64,
        "loggingPolicyEvidenceSha256": "f" * 64,
        "logicalCallCap": 60,
        "physicalCallCap": 60,
        "retryCount": 0,
        "store": False,
        "paidProject": True,
    }
    validated = validate_gemini_approval_packet(
        packet,
        expected_purpose=ApprovalPurpose.EVALUATION,
        evaluation_manifest_sha256=HASH_A,
    )
    assert validated.purpose is ApprovalPurpose.EVALUATION
    with pytest.raises(ProviderControlPlaneError, match="gemini_packet_purpose_mismatch"):
        validate_gemini_approval_packet(
            packet,
            expected_purpose=ApprovalPurpose.PRODUCTION_ACTIVATION,
            evaluation_manifest_sha256=HASH_A,
        )

    executor = OutboundDisabledGeminiExecutor()
    with pytest.raises(ProviderControlPlaneError, match="gemini_outbound_disabled"):
        executor.execute(request={}, approval=None)
    assert executor.external_physical_calls == 0


def test_gemini_mock_rechecks_response_and_withholds_failures() -> None:
    answer_payload = json.dumps(
        {"answer": "공개 근거로 확인된 경계입니다. [cit_1]", "citations": ["cit_1"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    interaction = json.dumps(
        {
            "object": "interaction",
            "status": "completed",
            "model": GEMINI_MODEL,
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": answer_payload}],
                }
            ],
            "usage": {
                "total_cached_tokens": 0,
                "total_tool_use_tokens": 0,
                "total_input_tokens": 10,
                "total_output_tokens": 10,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    transport = NetworkDisabledGeminiInteractionsTransport(response=interaction)
    result = execute_gemini_fixture(
        request=build_gemini_interaction_request(
            prompt="typed fixture prompt",
            evaluation_manifest_sha256=HASH_A,
        ),
        transport=transport,
        evidence=_evidence(),
        active_generation_id=GENERATION_ID,
    )

    assert result.failure is None
    assert result.answer is not None
    assert result.answer.citations == ("cit_1",)
    assert transport.fixture_attempts == 1
    assert transport.external_physical_calls == 0

    failed = execute_gemini_fixture(
        request=build_gemini_interaction_request(
            prompt="typed fixture prompt",
            evaluation_manifest_sha256=HASH_A,
        ),
        transport=NetworkDisabledGeminiInteractionsTransport(
            response=b'{"object":"interaction","status":"failed"}'
        ),
        evidence=_evidence(),
        active_generation_id=GENERATION_ID,
    )
    assert failed.answer is None
    assert failed.failure is GeminiFixtureFailure.PROVIDER_FAILED

    malformed = execute_gemini_fixture(
        request=build_gemini_interaction_request(
            prompt="typed fixture prompt",
            evaluation_manifest_sha256=HASH_A,
        ),
        transport=NetworkDisabledGeminiInteractionsTransport(response=b"not-json"),
        evidence=_evidence(),
        active_generation_id=GENERATION_ID,
    )
    assert malformed.answer is None
    assert malformed.failure is GeminiFixtureFailure.RESPONSE_INVALID


def test_hash_helpers_never_include_secret_shaped_material() -> None:
    plan = _voyage_plan()
    digest = hashlib.sha256(plan.plan_sha256.encode()).hexdigest()
    assert len(digest) == 64
    assert "secret" not in repr(plan).casefold()
