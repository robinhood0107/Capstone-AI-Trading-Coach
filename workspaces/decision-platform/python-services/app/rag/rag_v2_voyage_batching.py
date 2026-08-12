"""Voyage public corpus를 manifest-bound 단일 호출 묶음으로 결정적으로 분할한다.

이 모듈은 tokenizer count와 content-free manifest만 만들며 socket, credential, DB writer를 소유하지
않는다. EXACT30·OA112의 기존 chunk/locator/source identity를 바꾸지 않고, provider의 120K ceiling보다
낮은 60K request cap과 32K contextual segment cap을 함께 적용한다. 60K cap은 한 번
`UNKNOWN_BILLING`으로 종료된 110K plan과 identity를 분리해 append-only evidence를 보존하면서
forward recovery를 허용한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from app.rag.pre_s5_voyage_tokenizer import PreS5VoyageTokenCounter
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)

VoyageComponentScope = Literal["EXACT30", "OA112", "OWNER_PRIVATE"]

_PROFILE_ID: Final = "voyage_context_4_1024_v1"
_MODEL: Final = "voyage-context-4"
# 소비된 110K plan을 재사용하지 않고 response/request 크기를 함께 줄이는 forward-only cap이다.
_TOKEN_CAP: Final = 60_000
# voyage-context-4는 request 전체 120K와 별개로 한 contextual group이 참조하는 window를 32K로 제한한다.
_CONTEXT_SEGMENT_TOKEN_CAP: Final = 32_000
_GROUP_CAP: Final = 1_000
_CHUNK_CAP: Final = 16_000
_DOCUMENT_BATCH_MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
# float32 1024개를 JSON number로 돌려받을 때 chunk당 24 KiB를 예약하고 envelope 여유를 둔다.
# provider ceiling(16K chunks)보다 훨씬 작은 operational cap이라 정상 응답도 bounded reader를 넘지 않는다.
_RESPONSE_ENVELOPE_HEADROOM_BYTES: Final = 256 * 1024
_RESPONSE_BYTES_PER_CHUNK: Final = 24 * 1024
_RESPONSE_CHUNK_CAP: Final = (
    _DOCUMENT_BATCH_MAX_RESPONSE_BYTES - _RESPONSE_ENVELOPE_HEADROOM_BYTES
) // _RESPONSE_BYTES_PER_CHUNK
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCOPES: Final[tuple[VoyageComponentScope, ...]] = ("EXACT30", "OA112", "OWNER_PRIVATE")


class RagV2VoyageBatchingError(ValueError):
    """public membership, tokenizer count 또는 deterministic batch cap이 drift했다."""


@dataclass(frozen=True, slots=True)
class VoyagePreparedComponent:
    """provider 호출 전 확정된 component와 empty owner sentinel의 profile-neutral view다."""

    component_scope: VoyageComponentScope
    owner_scope_sha256: str | None
    groups: tuple[VoyagePreChunkedDocumentGroup, ...]


@dataclass(frozen=True, slots=True)
class VoyageContextSegment:
    """한 source group의 순서를 보존한 contiguous provider context segment다."""

    component_scope: Literal["EXACT30", "OA112"]
    source_id: str
    source_revision_id: str
    segment_ordinal: int
    segment_count: int
    token_count: int
    group: VoyagePreChunkedDocumentGroup
    segment_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class VoyageDocumentBatch:
    """별도 nonce/TTL/physical cap 1 packet에 결속할 정확한 one-request input이다."""

    batch_id: str
    batch_ordinal: int
    batch_count: int
    token_count: int
    chunk_count: int
    group_count: int
    estimated_response_bytes: int
    segments: tuple[VoyageContextSegment, ...]
    batch_manifest_sha256: str

    @property
    def groups(self) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
        """segment 순서와 원래 chunk 순서를 유지한 transport input을 반환한다."""

        return tuple(segment.group for segment in self.segments)

    def content_free_receipt(self) -> dict[str, object]:
        """canonical text 없이 packet authoring에 필요한 aggregate identity만 반환한다."""

        return {
            "batchId": self.batch_id,
            "batchManifestSha256": self.batch_manifest_sha256,
            "batchOrdinal": self.batch_ordinal,
            "batchCount": self.batch_count,
            "chunkCount": self.chunk_count,
            "groupCount": self.group_count,
            "estimatedResponseBytes": self.estimated_response_bytes,
            "tokenCount": self.token_count,
        }


@dataclass(frozen=True, slots=True)
class PublicVoyageBatchPlan:
    """EXACT30+OA112와 empty OWNER_PRIVATE sentinel을 묶는 immutable batch set이다."""

    plan_sha256: str
    tokenizer_sha256: str
    batches: tuple[VoyageDocumentBatch, ...]
    source_count: int
    chunk_count: int
    token_count: int
    owner_scope_sha256: None
    owner_private_ordered_group_count: Literal[0]

    def effective_chunk_identities(self) -> dict[str, tuple[str, str]]:
        """실제 provider context segmentation에 결속된 chunk별 input/context hash를 반환한다."""

        identities: dict[str, tuple[str, str]] = {}
        for batch in self.batches:
            for segment in batch.segments:
                for chunk in segment.group.chunks:
                    if chunk.chunk_id in identities:
                        raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
                    identities[chunk.chunk_id] = (
                        chunk.embedding_input_hash,
                        segment.group.context_set_hash,
                    )
        if len(identities) != self.chunk_count:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
        return identities

    def content_free_receipt(self) -> dict[str, object]:
        """원문·경로·credential 없이 exact approval manifest가 참조할 plan summary를 만든다."""

        return {
            "batchCount": len(self.batches),
            "chunkCount": self.chunk_count,
            "embeddingProfileId": _PROFILE_ID,
            "ownerPrivateSentinel": {
                "orderedGroupCount": self.owner_private_ordered_group_count,
                "ownerScopeSha256": self.owner_scope_sha256,
            },
            "planSha256": self.plan_sha256,
            "sourceCount": self.source_count,
            "tokenCount": self.token_count,
            "tokenizerSha256": self.tokenizer_sha256,
        }


class VoyageBatchVectorAccumulator:
    """성공 batch vector만 chunk identity에 결속하고 완료 batch 재호출을 거부하는 resume state다.

    이 object는 process-local vector를 보유한다. production runner는 각 successful batch를 DB ledger에
    즉시 stage한 뒤 동일 상태를 재구성해야 하며, provider response 원문은 전달받지 않는다.
    """

    def __init__(self, *, plan: PublicVoyageBatchPlan) -> None:
        if not isinstance(plan, PublicVoyageBatchPlan) or not plan.batches:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_PLAN")
        self._plan = plan
        self._batch_by_id = {batch.batch_id: batch for batch in plan.batches}
        self._vectors: dict[str, NDArray[np.float32]] = {}
        self._completed: set[str] = set()

    @property
    def pending_batches(self) -> tuple[VoyageDocumentBatch, ...]:
        """plan order를 유지하면서 아직 성공하지 않은 batch만 반환한다."""

        return tuple(batch for batch in self._plan.batches if batch.batch_id not in self._completed)

    @property
    def completed_batch_ids(self) -> tuple[str, ...]:
        """receipt용 content-free completed batch identity를 plan order로 반환한다."""

        return tuple(batch.batch_id for batch in self._plan.batches if batch.batch_id in self._completed)

    @property
    def complete(self) -> bool:
        """모든 manifest member vector가 성공 batch에서 한 번씩 들어왔는지 나타낸다."""

        return len(self._completed) == len(self._plan.batches)

    def record_success(self, *, batch: VoyageDocumentBatch, vectors: object) -> None:
        """한 성공 batch를 exactly once 기록하고 duplicate/mixed/invalid vector를 거부한다."""

        expected = self._batch_by_id.get(batch.batch_id) if isinstance(batch, VoyageDocumentBatch) else None
        if expected != batch or batch.batch_id in self._completed:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_RESUME_STATE")
        expected_rows = batch.chunk_count
        try:
            array = np.asarray(vectors)
        except Exception:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_VECTOR") from None
        if (
            not isinstance(array, np.ndarray)
            or array.dtype != np.float32
            or array.shape != (expected_rows, 1024)
            or not bool(np.isfinite(array).all())
        ):
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_VECTOR")
        norms = np.linalg.norm(array, axis=1)
        if not bool(np.allclose(norms, np.ones_like(norms), rtol=0.0, atol=1e-5)):
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_VECTOR")
        chunk_ids = tuple(chunk.chunk_id for group in batch.groups for chunk in group.chunks)
        if len(chunk_ids) != expected_rows or len(set(chunk_ids)) != expected_rows:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_RESUME_STATE")
        if any(chunk_id in self._vectors for chunk_id in chunk_ids):
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_RESUME_STATE")
        for chunk_id, vector in zip(chunk_ids, array, strict=True):
            self._vectors[chunk_id] = np.array(vector, dtype=np.float32, copy=True)
        self._completed.add(batch.batch_id)

    def ordered_vectors(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> NDArray[np.float32]:
        """complete plan일 때만 caller의 canonical component order로 vector를 재배열한다."""

        if not self.complete:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_INCOMPLETE")
        chunk_ids = tuple(chunk.chunk_id for group in groups for chunk in group.chunks)
        if not chunk_ids or len(set(chunk_ids)) != len(chunk_ids) or any(chunk_id not in self._vectors for chunk_id in chunk_ids):
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_RESUME_STATE")
        return np.stack(tuple(self._vectors[chunk_id] for chunk_id in chunk_ids)).astype(np.float32, copy=False)


def build_public_voyage_batch_plan(
    *,
    components: tuple[VoyagePreparedComponent, ...],
    token_counter: PreS5VoyageTokenCounter,
) -> PublicVoyageBatchPlan:
    """public base를 110K 이하의 deterministic ordered first-fit batch set으로 만든다.

    Token count는 official local artifact로 각 canonical chunk를 정확히 계산한다. 이 함수가 성공해도
    outbound authority는 생기지 않으며, 각 returned batch에는 별도의 승인 packet이 필요하다.
    """

    exact30, oa112 = _validate_public_components(components)
    if (
        not isinstance(token_counter, PreS5VoyageTokenCounter)
        or token_counter.model != _MODEL
        or _SHA256.fullmatch(token_counter.tokenizer_sha256) is None
    ):
        raise RagV2VoyageBatchingError("VOYAGE_BATCH_TOKENIZER")
    segments: list[VoyageContextSegment] = []
    for component in (exact30, oa112):
        for group in component.groups:
            segments.extend(
                _segment_group(
                    component_scope=component.component_scope,
                    group=group,
                    token_counter=token_counter,
                    tokenizer_sha256=token_counter.tokenizer_sha256,
                )
            )
    plan_seed_sha256 = _canonical_hash(
        {
            "embeddingProfileId": _PROFILE_ID,
            "schemaVersion": 1,
            "segments": [segment.segment_manifest_sha256 for segment in segments],
            "tokenizerSha256": token_counter.tokenizer_sha256,
        }
    )
    provisional: list[list[VoyageContextSegment]] = []
    for segment in segments:
        placed = False
        for batch_segments in provisional:
            if _fits(batch_segments, segment):
                batch_segments.append(segment)
                placed = True
                break
        if not placed:
            provisional.append([segment])
    batch_count = len(provisional)
    batches = tuple(
        _build_batch(
            segments=tuple(items),
            ordinal=index + 1,
            count=batch_count,
            plan_seed_sha256=plan_seed_sha256,
        )
        for index, items in enumerate(provisional)
    )
    source_count = len(exact30.groups) + len(oa112.groups)
    chunk_count = sum(batch.chunk_count for batch in batches)
    token_count = sum(batch.token_count for batch in batches)
    plan_manifest = {
        "batches": [
            {
                "batchManifestSha256": batch.batch_manifest_sha256,
                "batchOrdinal": batch.batch_ordinal,
            }
            for batch in batches
        ],
        "chunkCount": chunk_count,
        "embeddingProfileId": _PROFILE_ID,
        "ownerPrivateSentinel": {"orderedGroupCount": 0, "ownerScopeSha256": None},
        "schemaVersion": 1,
        "sourceCount": source_count,
        "tokenCount": token_count,
        "tokenizerSha256": token_counter.tokenizer_sha256,
    }
    return PublicVoyageBatchPlan(
        plan_sha256=_canonical_hash(plan_manifest),
        tokenizer_sha256=token_counter.tokenizer_sha256,
        batches=batches,
        source_count=source_count,
        chunk_count=chunk_count,
        token_count=token_count,
        owner_scope_sha256=None,
        owner_private_ordered_group_count=0,
    )


def _validate_public_components(
    components: object,
) -> tuple[VoyagePreparedComponent, VoyagePreparedComponent]:
    if not isinstance(components, tuple) or len(components) != 3:
        raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
    for expected, component in zip(_SCOPES, components, strict=True):
        if not isinstance(component, VoyagePreparedComponent) or component.component_scope != expected:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
    exact30, oa112, owner = components
    if (
        exact30.owner_scope_sha256 is not None
        or oa112.owner_scope_sha256 is not None
        or owner.owner_scope_sha256 is not None
        or owner.groups
        or len(exact30.groups) != 30
        or len(oa112.groups) != 112
    ):
        raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
    all_groups = exact30.groups + oa112.groups
    all_chunk_ids = tuple(chunk.chunk_id for group in all_groups for chunk in group.chunks)
    if (
        len({group.source_id for group in all_groups}) != 142
        or len({group.source_revision_id for group in all_groups}) != 142
        or len(set(all_chunk_ids)) != len(all_chunk_ids)
        or any(not group.chunks for group in all_groups)
    ):
        raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
    return exact30, oa112


def _segment_group(
    *,
    component_scope: VoyageComponentScope,
    group: VoyagePreChunkedDocumentGroup,
    token_counter: PreS5VoyageTokenCounter,
    tokenizer_sha256: str,
) -> tuple[VoyageContextSegment, ...]:
    if component_scope not in {"EXACT30", "OA112"}:
        raise RagV2VoyageBatchingError("VOYAGE_BATCH_PUBLIC_MEMBERSHIP")
    counted: list[tuple[VoyagePreChunkedChunk, int]] = []
    for chunk in group.chunks:
        try:
            token_count = token_counter.count_texts(
                texts=(chunk.canonical_text,),
                token_cap=_CONTEXT_SEGMENT_TOKEN_CAP,
            )
        except Exception:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_CHUNK_TOKEN_CAP") from None
        if token_count > _CONTEXT_SEGMENT_TOKEN_CAP:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_CHUNK_TOKEN_CAP")
        counted.append((chunk, token_count))
    partitions: list[list[tuple[VoyagePreChunkedChunk, int]]] = []
    current: list[tuple[VoyagePreChunkedChunk, int]] = []
    current_tokens = 0
    for item in counted:
        chunk, token_count = item
        if current and (
            current_tokens + token_count > _CONTEXT_SEGMENT_TOKEN_CAP
            or len(current) >= min(_CHUNK_CAP, _RESPONSE_CHUNK_CAP)
        ):
            partitions.append(current)
            current = []
            current_tokens = 0
        current.append((chunk, token_count))
        current_tokens += token_count
    if current:
        partitions.append(current)
    segment_count = len(partitions)
    effective_context_set_hash = group.context_set_hash
    if segment_count > 1:
        effective_context_set_hash = _canonical_hash(
            {
                "embeddingProfileId": _PROFILE_ID,
                "originalContextSetHash": group.context_set_hash,
                "partitions": [
                    [
                        {
                            "canonicalTextSha256": chunk.canonical_text_sha256,
                            "chunkId": chunk.chunk_id,
                            "originalEmbeddingInputHash": chunk.embedding_input_hash,
                            "tokenCount": token_count,
                        }
                        for chunk, token_count in partition
                    ]
                    for partition in partitions
                ],
                "schemaVersion": 1,
                "sourceId": group.source_id,
                "sourceRevisionId": group.source_revision_id,
                "tokenizerSha256": tokenizer_sha256,
            }
        )
    result: list[VoyageContextSegment] = []
    for index, partition in enumerate(partitions):
        chunks = tuple(
            item[0]
            if segment_count == 1
            else replace(
                item[0],
                embedding_input_hash=_canonical_hash(
                    {
                        "chunkId": item[0].chunk_id,
                        "effectiveContextSetHash": effective_context_set_hash,
                        "embeddingProfileId": _PROFILE_ID,
                        "originalEmbeddingInputHash": item[0].embedding_input_hash,
                        "schemaVersion": 1,
                        "segmentOrdinal": index + 1,
                    }
                ),
            )
            for item in partition
        )
        tokens = sum(item[1] for item in partition)
        if not 1 <= tokens <= _CONTEXT_SEGMENT_TOKEN_CAP:
            raise RagV2VoyageBatchingError("VOYAGE_BATCH_CONTEXT_WINDOW")
        segment_group = VoyagePreChunkedDocumentGroup(
            source_id=group.source_id,
            source_revision_id=group.source_revision_id,
            context_set_hash=effective_context_set_hash,
            chunks=chunks,
        )
        manifest = _segment_manifest(
            component_scope=component_scope,
            group=segment_group,
            segment_ordinal=index + 1,
            segment_count=segment_count,
            token_count=tokens,
        )
        result.append(
            VoyageContextSegment(
                component_scope=component_scope,
                source_id=group.source_id,
                source_revision_id=group.source_revision_id,
                segment_ordinal=index + 1,
                segment_count=segment_count,
                token_count=tokens,
                group=segment_group,
                segment_manifest_sha256=_canonical_hash(manifest),
            )
        )
    return tuple(result)


def _fits(existing: list[VoyageContextSegment], candidate: VoyageContextSegment) -> bool:
    return (
        sum(item.token_count for item in existing) + candidate.token_count <= _TOKEN_CAP
        and sum(len(item.group.chunks) for item in existing) + len(candidate.group.chunks) <= _CHUNK_CAP
        and _estimated_response_bytes(
            sum(len(item.group.chunks) for item in existing) + len(candidate.group.chunks)
        )
        <= _DOCUMENT_BATCH_MAX_RESPONSE_BYTES
        and len(existing) + 1 <= _GROUP_CAP
        and all(item.source_id != candidate.source_id for item in existing)
    )


def _build_batch(
    *,
    segments: tuple[VoyageContextSegment, ...],
    ordinal: int,
    count: int,
    plan_seed_sha256: str,
) -> VoyageDocumentBatch:
    token_count = sum(item.token_count for item in segments)
    chunk_count = sum(len(item.group.chunks) for item in segments)
    group_count = len(segments)
    estimated_response_bytes = _estimated_response_bytes(chunk_count)
    if not (
        _SHA256.fullmatch(plan_seed_sha256) is not None
        and 1 <= token_count <= _TOKEN_CAP
        and 1 <= chunk_count <= min(_CHUNK_CAP, _RESPONSE_CHUNK_CAP)
        and 1 <= group_count <= _GROUP_CAP
        and estimated_response_bytes <= _DOCUMENT_BATCH_MAX_RESPONSE_BYTES
    ):
        raise RagV2VoyageBatchingError("VOYAGE_BATCH_CAP")
    manifest = {
        "batchCount": count,
        "batchOrdinal": ordinal,
        "chunkCount": chunk_count,
        "embeddingProfileId": _PROFILE_ID,
        "groupCount": group_count,
        "estimatedResponseBytes": estimated_response_bytes,
        "planSeedSha256": plan_seed_sha256,
        "schemaVersion": 1,
        "segments": [
            {
                "componentScope": segment.component_scope,
                "segmentManifestSha256": segment.segment_manifest_sha256,
                "segmentOrdinal": segment.segment_ordinal,
                "sourceId": segment.source_id,
                "sourceRevisionId": segment.source_revision_id,
            }
            for segment in segments
        ],
        "tokenCount": token_count,
    }
    manifest_sha256 = _canonical_hash(manifest)
    return VoyageDocumentBatch(
        batch_id=f"ps5_voyage_doc_{ordinal:04d}_{manifest_sha256[:16]}",
        batch_ordinal=ordinal,
        batch_count=count,
        token_count=token_count,
        chunk_count=chunk_count,
        group_count=group_count,
        estimated_response_bytes=estimated_response_bytes,
        segments=segments,
        batch_manifest_sha256=manifest_sha256,
    )


def _segment_manifest(
    *,
    component_scope: VoyageComponentScope,
    group: VoyagePreChunkedDocumentGroup,
    segment_ordinal: int,
    segment_count: int,
    token_count: int,
) -> dict[str, object]:
    return {
        "chunks": [
            {
                "canonicalTextSha256": chunk.canonical_text_sha256,
                "chunkId": chunk.chunk_id,
                "embeddingInputHash": chunk.embedding_input_hash,
            }
            for chunk in group.chunks
        ],
        "componentScope": component_scope,
        "contextSetHash": group.context_set_hash,
        "schemaVersion": 1,
        "segmentCount": segment_count,
        "segmentOrdinal": segment_ordinal,
        "sourceId": group.source_id,
        "sourceRevisionId": group.source_revision_id,
        "tokenCount": token_count,
    }


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _estimated_response_bytes(chunk_count: int) -> int:
    """provider raw body를 저장하지 않으면서 packet byte cap에 필요한 보수적 상한만 계산한다."""

    return _RESPONSE_ENVELOPE_HEADROOM_BYTES + chunk_count * _RESPONSE_BYTES_PER_CHUNK
