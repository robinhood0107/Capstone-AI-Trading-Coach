from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.pre_s5_provider_control import PreS5ProviderBinding
from app.rag.rag_v2_public_bge_evaluator import PublicBgeEvaluationQuery
from app.rag import rag_v2_public_voyage_evaluator as voyage_evaluator
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


class _FreshEvaluationRepository:
    def __init__(self) -> None:
        self.reserve_calls = 0
        self.stage_calls = 0

    def resume_evaluation_batch(self, **kwargs: object) -> None:
        del kwargs
        return None

    def reserve(self, **kwargs: object) -> object:
        del kwargs
        self.reserve_calls += 1
        return object()

    def stage_evaluation_batch(self, **kwargs: object) -> None:
        del kwargs
        self.stage_calls += 1


class _TokenCounter:
    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        assert token_cap == 8_192
        return len(texts)


class _SuccessfulEvaluationTransport:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    def embed(self, *, query_id_questions: tuple[tuple[str, str], ...]) -> object:
        vector = (1.0,) + (0.0,) * 1023
        return SimpleNamespace(
            actual_cost_microusd=len(query_id_questions),
            expected_input_tokens=len(query_id_questions),
            total_tokens=len(query_id_questions),
            vectors_by_query_sha256={
                hashlib.sha256(question.encode("utf-8")).hexdigest(): vector
                for _, question in query_id_questions
            },
            voyage_physical_calls=1,
        )


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


def test_fresh_evaluation_loads_tokenizer_from_read_only_source_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    source_root.mkdir()
    output_root.mkdir()
    loaded_roots: list[Path] = []
    exact30, oa112 = _queries()
    repository = _FreshEvaluationRepository()

    def _load_tokenizer(*, local_root: Path, expected_sha256: str) -> _TokenCounter:
        assert expected_sha256 == "e" * 64
        loaded_roots.append(local_root)
        return _TokenCounter()

    monkeypatch.setattr(
        voyage_evaluator.LocalPreS5VoyageContext4Tokenizer,
        "from_local_root",
        _load_tokenizer,
    )
    monkeypatch.setattr(
        voyage_evaluator,
        "load_pre_s5_voyage_evaluation_batch_activation",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        voyage_evaluator,
        "PreS5VoyageEvaluationBatchTransport",
        _SuccessfulEvaluationTransport,
    )
    embedder = PacketGatedPublicVoyageEvaluationBatchEmbedder(
        local_root=output_root,
        tokenizer_local_root=source_root,
        binding=PreS5ProviderBinding(
            head_commit="a" * 40,
            tree_object="b" * 40,
            ci_digest="c" * 64,
            security_digest="d" * 64,
        ),
        api_key="test-key",
        usage_repository=repository,  # type: ignore[arg-type]
        exact30_queries=exact30,
        oa112_queries=oa112,
        tokenizer_sha256="e" * 64,
        sender=_NoOutboundSender(),  # type: ignore[arg-type]
    )

    receipt = embedder.embed_query_with_receipt(
        question=exact30[0].question,
        scope_claim_id="rvs_" + "f" * 32,
        external_query_consent_granted=True,
    )

    assert receipt.voyage_physical_calls == 1
    assert loaded_roots == [source_root]
    assert repository.reserve_calls == 1
    assert repository.stage_calls == 1


def _queries() -> tuple[tuple[PublicBgeEvaluationQuery, ...], tuple[PublicBgeEvaluationQuery, ...]]:
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
    return exact30, oa112
