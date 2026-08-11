from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.pre_s5_provider_control import PreS5ProviderBinding
from app.rag.rag_v2_public_bge_evaluator import PublicBgeEvaluationQuery
from app.rag.rag_v2_public_voyage_evaluator import (
    PacketGatedPublicVoyageEvaluationBatchEmbedder,
)


class _ResumeOnlyRepository:
    def __init__(self) -> None:
        self.resume_calls = 0
        self.reserve_calls = 0

    def resume_evaluation_batch(
        self,
        *,
        scope_claim_sha256: str,
        component_scope: str,
        query_manifest_sha256: str,
        expected_query_sha256s: Sequence[str],
    ) -> dict[str, tuple[float, ...]] | None:
        assert len(scope_claim_sha256) == 64
        assert len(query_manifest_sha256) == 64
        self.resume_calls += 1
        if component_scope != "EXACT30":
            return None
        vector = (1.0,) + (0.0,) * 1023
        return {query_sha256: vector for query_sha256 in expected_query_sha256s}

    def reserve(self, **kwargs: object) -> object:
        del kwargs
        self.reserve_calls += 1
        raise AssertionError("completed evaluation batch must not reserve another packet")

    def stage_evaluation_batch(self, **kwargs: object) -> None:
        del kwargs
        raise AssertionError("completed evaluation batch must not be staged twice")


class _NoOutboundSender:
    def post(self, request: object) -> object:
        del request
        raise AssertionError("completed evaluation batch must not call Voyage")


def test_completed_exact30_evaluation_batch_resumes_without_packet_or_provider(tmp_path: Path) -> None:
    repository = _ResumeOnlyRepository()
    exact30 = tuple(
        PublicBgeEvaluationQuery(
            query_id=f"q{index:02d}",
            question=f"exact question {index}",
            expected_source_id="src_exact_fixture",
            topics=("topic",),
            track_id=None,
        )
        for index in range(1, 11)
    )
    oa112 = tuple(
        PublicBgeEvaluationQuery(
            query_id=f"oa112-q{index:03d}",
            question=f"oa question {index}",
            expected_source_id=f"src_oa_fixture_{index:03d}",
            topics=("topic",),
            track_id=track_id,
        )
        for index, track_id in enumerate(
            (track for track in OA_TRACK_IDS for _ in range(8)),
            start=1,
        )
    )
    embedder = PacketGatedPublicVoyageEvaluationBatchEmbedder(
        local_root=tmp_path,
        binding=PreS5ProviderBinding(
            head_commit="a" * 40,
            tree_object="b" * 40,
            ci_digest="c" * 64,
            security_digest="d" * 64,
        ),
        api_key="unused-on-resume",
        usage_repository=repository,  # type: ignore[arg-type]
        exact30_queries=exact30,
        oa112_queries=oa112,
        tokenizer_sha256="e" * 64,
        sender=_NoOutboundSender(),  # type: ignore[arg-type]
    )

    first = embedder.embed_query_with_receipt(
        question=exact30[0].question,
        scope_claim_id="rvs_" + "f" * 32,
        external_query_consent_granted=True,
    )
    second = embedder.embed_query_with_receipt(
        question=exact30[1].question,
        scope_claim_id="rvs_" + "f" * 32,
        external_query_consent_granted=True,
    )

    assert first.voyage_physical_calls == 1
    assert second.voyage_physical_calls == 0
    assert repository.resume_calls == 1
    assert repository.reserve_calls == 0
    assert embedder.content_free_summary()["exact30QueryPhysicalCallCount"] == 1
