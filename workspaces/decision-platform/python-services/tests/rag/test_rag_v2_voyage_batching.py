from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np
import pytest

from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_voyage_batching import (
    RagV2VoyageBatchingError,
    VoyageBatchVectorAccumulator,
    VoyagePreparedComponent,
    build_public_voyage_batch_plan,
)


class _TokenCounter:
    model = "voyage-context-4"
    tokenizer_sha256 = "e" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        total = sum(int(text.removeprefix("tokens:")) for text in texts)
        if total > token_cap:
            raise ValueError("fixture cap")
        return total


class _RotatedTokenizerCounter(_TokenCounter):
    tokenizer_sha256 = "f" * 64


def test_public_plan_splits_large_source_contiguously_and_packs_under_headroom() -> None:
    exact30 = tuple(_group("exact", index, (1_000,)) for index in range(30))
    oa112 = tuple(
        _group("oa", index, (16_000, 16_000, 16_000, 16_000))
        if index == 0
        else _group("oa", index, (100,))
        for index in range(112)
    )

    plan = build_public_voyage_batch_plan(
        components=_components(exact30=exact30, oa112=oa112),
        token_counter=_TokenCounter(),
    )

    assert plan.source_count == 142
    assert plan.chunk_count == 145
    assert plan.owner_private_ordered_group_count == 0
    assert plan.owner_scope_sha256 is None
    assert len(plan.batches) == 3
    # UNKNOWN_BILLING forward recovery rotates the immutable plan and keeps every new request
    # materially below the provider ceiling instead of replaying the consumed 110K plan.
    assert all(batch.token_count <= 60_000 for batch in plan.batches)
    assert all(batch.group_count <= 1_000 for batch in plan.batches)
    assert all(batch.chunk_count <= 16_000 for batch in plan.batches)
    split = [
        segment
        for batch in plan.batches
        for segment in batch.segments
        if segment.source_id == "src_oa_000"
    ]
    assert [(segment.segment_ordinal, segment.segment_count) for segment in split] == [
        (1, 2),
        (2, 2),
    ]
    assert [chunk.chunk_id for segment in split for chunk in segment.group.chunks] == [
        chunk.chunk_id for chunk in oa112[0].chunks
    ]
    assert {segment.group.context_set_hash for segment in split} != {oa112[0].context_set_hash}
    assert len({segment.group.context_set_hash for segment in split}) == 1
    assert all(
        effective.embedding_input_hash != original.embedding_input_hash
        for effective, original in zip(
            (chunk for segment in split for chunk in segment.group.chunks),
            oa112[0].chunks,
            strict=True,
        )
    )
    identities = plan.effective_chunk_identities()
    assert identities[split[0].group.chunks[0].chunk_id] == (
        split[0].group.chunks[0].embedding_input_hash,
        split[0].group.context_set_hash,
    )


def test_public_plan_never_emits_a_context_segment_over_voyage_context4_window() -> None:
    """120K request 여유와 별개로 각 contextual document group은 32K를 넘지 않는다."""

    exact30 = tuple(
        _group("exact", index, (20_000, 20_000)) if index == 0 else _group("exact", index, (1,))
        for index in range(30)
    )
    oa112 = tuple(_group("oa", index, (1,)) for index in range(112))

    plan = build_public_voyage_batch_plan(
        components=_components(exact30=exact30, oa112=oa112),
        token_counter=_TokenCounter(),
    )
    segments = tuple(segment for batch in plan.batches for segment in batch.segments)
    split = tuple(segment for segment in segments if segment.source_id == "src_exact_000")

    assert tuple(segment.token_count for segment in split) == (20_000, 20_000)
    assert tuple(segment.segment_ordinal for segment in split) == (1, 2)
    assert all(segment.token_count <= 32_000 for segment in segments)


def test_public_plan_limits_chunks_by_conservative_response_body_budget() -> None:
    exact30 = tuple(
        _group("exact", index, tuple(1 for _ in range(700)) if index == 0 else (1,))
        for index in range(30)
    )
    oa112 = tuple(_group("oa", index, (1,)) for index in range(112))

    plan = build_public_voyage_batch_plan(
        components=_components(exact30=exact30, oa112=oa112),
        token_counter=_TokenCounter(),
    )

    assert len(plan.batches) >= 2
    assert all(batch.estimated_response_bytes <= 16 * 1024 * 1024 for batch in plan.batches)
    assert all(batch.chunk_count < 700 for batch in plan.batches)


def test_public_plan_preserves_unsplit_context_and_embedding_identity() -> None:
    exact30 = tuple(_group("exact", index, (1,)) for index in range(30))
    oa112 = tuple(_group("oa", index, (1,)) for index in range(112))

    plan = build_public_voyage_batch_plan(
        components=_components(exact30=exact30, oa112=oa112),
        token_counter=_TokenCounter(),
    )
    segment = next(
        item
        for batch in plan.batches
        for item in batch.segments
        if item.source_id == exact30[0].source_id
    )

    assert segment.group.context_set_hash == exact30[0].context_set_hash
    assert segment.group.chunks[0].embedding_input_hash == exact30[0].chunks[0].embedding_input_hash


def test_public_plan_is_deterministic_and_content_free() -> None:
    components = _components(
        exact30=tuple(_group("exact", index, (2_000,)) for index in range(30)),
        oa112=tuple(_group("oa", index, (2_000, 3_000)) for index in range(112)),
    )

    first = build_public_voyage_batch_plan(components=components, token_counter=_TokenCounter())
    second = build_public_voyage_batch_plan(components=components, token_counter=_TokenCounter())

    assert first == second
    assert first.plan_sha256 == second.plan_sha256
    receipt = first.content_free_receipt()
    assert receipt["batchCount"] == len(first.batches)
    assert receipt["sourceCount"] == 142
    assert "tokens:" not in str(receipt)
    assert len({batch.batch_manifest_sha256 for batch in first.batches}) == len(first.batches)


def test_public_plan_rejects_nonempty_owner_or_wrong_public_membership() -> None:
    exact30 = tuple(_group("exact", index, (1,)) for index in range(30))
    oa112 = tuple(_group("oa", index, (1,)) for index in range(112))
    components = list(_components(exact30=exact30, oa112=oa112))
    components[2] = VoyagePreparedComponent(
        component_scope="OWNER_PRIVATE",
        owner_scope_sha256="f" * 64,
        groups=(_group("owner", 0, (1,)),),
    )

    with pytest.raises(RagV2VoyageBatchingError, match="VOYAGE_BATCH_PUBLIC_MEMBERSHIP"):
        build_public_voyage_batch_plan(components=tuple(components), token_counter=_TokenCounter())

    wrong = list(_components(exact30=exact30[:-1], oa112=oa112))
    with pytest.raises(RagV2VoyageBatchingError, match="VOYAGE_BATCH_PUBLIC_MEMBERSHIP"):
        build_public_voyage_batch_plan(components=tuple(wrong), token_counter=_TokenCounter())


def test_public_plan_rejects_one_chunk_larger_than_context_window_without_calling_provider() -> (
    None
):
    exact30 = tuple(
        _group("exact", index, (32_001,) if index == 0 else (1,)) for index in range(30)
    )
    oa112 = tuple(_group("oa", index, (1,)) for index in range(112))

    with pytest.raises(RagV2VoyageBatchingError, match="VOYAGE_BATCH_CHUNK_TOKEN_CAP"):
        build_public_voyage_batch_plan(
            components=_components(exact30=exact30, oa112=oa112),
            token_counter=_TokenCounter(),
        )


def test_public_plan_rejects_checkpoint_token_count_over_profile_neutral_cap_before_recount() -> (
    None
):
    exact30 = tuple(_group("exact", index, (1,)) for index in range(30))
    oa112 = [_group("oa", index, (1,)) for index in range(112)]
    chunks = list(oa112[0].chunks)
    chunks[0] = replace(chunks[0], token_count=601)
    oa112[0] = replace(oa112[0], chunks=tuple(chunks))

    with pytest.raises(
        RagV2VoyageBatchingError,
        match="VOYAGE_BATCH_PROFILE_NEUTRAL_TOKEN_CAP",
    ):
        build_public_voyage_batch_plan(
            components=_components(exact30=exact30, oa112=tuple(oa112)),
            token_counter=_TokenCounter(),
        )


def test_public_plan_hash_changes_when_member_identity_changes() -> None:
    components = _components(
        exact30=tuple(_group("exact", index, (1,)) for index in range(30)),
        oa112=tuple(_group("oa", index, (1,)) for index in range(112)),
    )
    baseline = build_public_voyage_batch_plan(components=components, token_counter=_TokenCounter())
    changed_groups = list(components[1].groups)
    changed_chunks = list(changed_groups[0].chunks)
    changed_chunks[0] = replace(changed_chunks[0], embedding_input_hash="f" * 64)
    changed_groups[0] = replace(changed_groups[0], chunks=tuple(changed_chunks))
    changed = list(components)
    changed[1] = replace(changed[1], groups=tuple(changed_groups))

    drifted = build_public_voyage_batch_plan(
        components=tuple(changed), token_counter=_TokenCounter()
    )

    assert drifted.plan_sha256 != baseline.plan_sha256


def test_public_plan_rotates_every_global_batch_id_with_the_exact_tokenizer_plan() -> None:
    components = _components(
        exact30=tuple(
            _group("exact", index, (30_000, 30_000)) if index < 2 else _group("exact", index, (1,))
            for index in range(30)
        ),
        oa112=tuple(_group("oa", index, (1,)) for index in range(112)),
    )
    baseline = build_public_voyage_batch_plan(components=components, token_counter=_TokenCounter())
    rotated = build_public_voyage_batch_plan(
        components=components,
        token_counter=_RotatedTokenizerCounter(),
    )

    assert len(baseline.batches) == len(rotated.batches) == 4
    assert baseline.plan_sha256 != rotated.plan_sha256
    assert {batch.batch_id for batch in baseline.batches}.isdisjoint(
        batch.batch_id for batch in rotated.batches
    )


def test_vector_accumulator_skips_completed_batches_and_restores_canonical_order() -> None:
    exact30 = tuple(
        _group("exact", index, (30_000, 30_000)) if index < 2 else _group("exact", index, (1,))
        for index in range(30)
    )
    oa112 = tuple(_group("oa", index, (1,)) for index in range(112))
    plan = build_public_voyage_batch_plan(
        components=_components(exact30=exact30, oa112=oa112),
        token_counter=_TokenCounter(),
    )
    assert len(plan.batches) == 4
    accumulator = VoyageBatchVectorAccumulator(plan=plan)
    for batch in reversed(plan.batches):
        vectors = np.zeros((batch.chunk_count, 1024), dtype=np.float32)
        for index in range(batch.chunk_count):
            vectors[index, index % 1024] = 1.0
        accumulator.record_success(batch=batch, vectors=vectors)
        assert batch.batch_id not in {item.batch_id for item in accumulator.pending_batches}

    assert accumulator.complete is True
    assert accumulator.completed_batch_ids == tuple(batch.batch_id for batch in plan.batches)
    ordered = accumulator.ordered_vectors(groups=exact30 + oa112)
    assert ordered.shape == (144, 1024)
    with pytest.raises(RagV2VoyageBatchingError, match="VOYAGE_BATCH_RESUME_STATE"):
        accumulator.record_success(
            batch=plan.batches[0],
            vectors=np.zeros((plan.batches[0].chunk_count, 1024), dtype=np.float32),
        )


def _components(
    *,
    exact30: tuple[VoyagePreChunkedDocumentGroup, ...],
    oa112: tuple[VoyagePreChunkedDocumentGroup, ...],
) -> tuple[VoyagePreparedComponent, ...]:
    return (
        VoyagePreparedComponent(component_scope="EXACT30", owner_scope_sha256=None, groups=exact30),
        VoyagePreparedComponent(component_scope="OA112", owner_scope_sha256=None, groups=oa112),
        VoyagePreparedComponent(
            component_scope="OWNER_PRIVATE", owner_scope_sha256=None, groups=()
        ),
    )


def _group(prefix: str, index: int, token_counts: tuple[int, ...]) -> VoyagePreChunkedDocumentGroup:
    source_id = f"src_{prefix}_{index:03d}"
    revision_id = f"srv_{prefix}_{index:03d}"
    return VoyagePreChunkedDocumentGroup(
        source_id=source_id,
        source_revision_id=revision_id,
        context_set_hash=f"{index + 1:064x}",
        chunks=tuple(
            VoyagePreChunkedChunk(
                chunk_id="rag_v2_chk_"
                + hashlib.sha256(f"{prefix}|{index}|{ordinal}".encode()).hexdigest()[:32],
                canonical_text=f"tokens:{token_count}",
                canonical_text_sha256=f"{index + ordinal + 2:064x}",
                embedding_input_hash=f"{index + ordinal + 3:064x}",
                token_count=1,
            )
            for ordinal, token_count in enumerate(token_counts)
        ),
    )
