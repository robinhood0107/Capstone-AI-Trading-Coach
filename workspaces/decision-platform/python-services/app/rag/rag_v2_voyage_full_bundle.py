"""Pre-S5 public Voyage profile의 resumable document-batch orchestration이다.

EXACT30 and OA112 are prepared before transport, then the exact manifest-bound response set is partitioned back
into the two immutable components. The public base always uses the explicit empty OWNER_PRIVATE sentinel: no
owner document is silently included in a global profile activation. Legacy full-bundle helpers remain internal
compatibility seams only; the active CLI and contract use the resumable batch plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from app.rag.ingest_pipeline import RagTokenizer
from app.rag.oa112_active_registry import Oa112ActiveRegistry
from app.rag.pre_s5_voyage_tokenizer import PreS5VoyageTokenCounter
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageBundleComponent,
    PreS5VoyageFullBundle,
    build_pre_s5_voyage_full_bundle,
)
from app.rag.rag_v2_bge_materializer import ApprovedDocumentParser
from app.rag.rag_v2_external_exact30_voyage_runner import (
    ExternalExact30PublicVoyageMaterialization,
    ExternalExact30PublicVoyagePreparation,
    materialize_prepared_external_exact30_public_voyage_component,
    prepare_external_exact30_public_voyage_component,
    validate_voyage_document_vectors,
)
from app.rag.rag_v2_oa112_voyage_runner import (
    Oa112PublicVoyageMaterialization,
    Oa112PublicVoyagePreparation,
    materialize_prepared_oa112_public_voyage_component,
    prepare_oa112_public_voyage_component,
)
from app.rag.rag_v2_voyage_batching import (
    PublicVoyageBatchPlan,
    VoyageBatchVectorAccumulator,
    VoyagePreparedComponent,
    build_public_voyage_batch_plan,
)
from app.rag.source_card_corpus import FrozenSourceCardCorpus


class RagV2VoyageFullBundleError(ValueError):
    """public full-bundle preparation, one-shot transport output, or component partition가 drift했다."""


class VoyageFullBundleEmbedder(Protocol):
    """hard-gated one-shot transport만 coordinator에 주입하는 narrow provider seam이다."""

    def embed_full_bundle(self, *, bundle: PreS5VoyageFullBundle) -> NDArray[np.float32]: ...


@dataclass(frozen=True, slots=True)
class PublicBaseVoyagePreparation:
    """one provider call 전 two public components와 signed input manifest를 묶는다."""

    exact30: ExternalExact30PublicVoyagePreparation
    oa112: Oa112PublicVoyagePreparation
    bundle: PreS5VoyageFullBundle

    def content_free_receipt(self) -> dict[str, object]:
        """canonical text, raw cache path, provider response 없이 packet binding identity만 반환한다."""

        return {
            "bundleManifestSha256": self.bundle.manifest_sha256,
            "exact30GroupCount": len(self.exact30.groups),
            "oa112GroupCount": len(self.oa112.groups),
            "ownerPrivateSentinel": {
                "orderedGroupCount": 0,
                "ownerScopeSha256": None,
            },
        }


@dataclass(frozen=True, slots=True)
class PublicBaseVoyageMaterialization:
    """one successful full-bundle response를 profile-isolated public component records로 partition한다."""

    exact30: ExternalExact30PublicVoyageMaterialization
    oa112: Oa112PublicVoyageMaterialization
    bundle_manifest_sha256: str

    def content_free_receipt(self) -> dict[str, object]:
        """stage/evaluate/CAS caller에 only aggregate generation identities를 전달한다."""

        return {
            "bundleManifestSha256": self.bundle_manifest_sha256,
            "exact30": self.exact30.content_free_receipt(),
            "oa112": self.oa112.content_free_receipt(),
            "ownerPrivateSentinel": {
                "orderedGroupCount": 0,
                "ownerScopeSha256": None,
            },
        }


@dataclass(frozen=True, slots=True)
class PublicBaseVoyageBatchPreparation:
    """provider 호출 전에 public components와 exact manifest-bound batch set을 모두 닫는다."""

    exact30: ExternalExact30PublicVoyagePreparation
    oa112: Oa112PublicVoyagePreparation
    plan: PublicVoyageBatchPlan

    def content_free_receipt(self) -> dict[str, object]:
        """canonical text 없이 checkpoint/batch/empty sentinel aggregate만 반환한다."""

        receipt = self.plan.content_free_receipt()
        receipt.update(
            {
                "checkpointReusedSourceCount": (
                    self.exact30.checkpoint_reused_count + self.oa112.checkpoint_reused_count
                ),
                "checkpointWrittenSourceCount": (
                    self.exact30.checkpoint_written_count + self.oa112.checkpoint_written_count
                ),
                "exact30GroupCount": 30,
                "oa112GroupCount": 112,
            }
        )
        return receipt


def prepare_public_base_voyage_batches(
    *,
    tokenizer: RagTokenizer,
    voyage_token_counter: PreS5VoyageTokenCounter,
    oa112_registry: Oa112ActiveRegistry,
    oa112_local_cache_root: Path,
    oa112_parser: ApprovedDocumentParser | None = None,
    exact30_corpus: FrozenSourceCardCorpus | None = None,
    checkpoint_local_corpus_root: Path | None = None,
) -> PublicBaseVoyageBatchPreparation:
    """EXACT30+OA112+empty sentinel을 준비하고 provider limit 이하 exact batch plan을 만든다."""

    try:
        exact30 = prepare_external_exact30_public_voyage_component(
            tokenizer=tokenizer,
            corpus=exact30_corpus,
            checkpoint_local_corpus_root=checkpoint_local_corpus_root,
        )
        oa112 = prepare_oa112_public_voyage_component(
            tokenizer=tokenizer,
            registry=oa112_registry,
            local_cache_root=oa112_local_cache_root,
            parser=oa112_parser,
            checkpoint_local_corpus_root=checkpoint_local_corpus_root,
        )
        plan = build_public_voyage_batch_plan(
            components=(
                VoyagePreparedComponent(
                    component_scope="EXACT30",
                    owner_scope_sha256=None,
                    groups=exact30.groups,
                ),
                VoyagePreparedComponent(
                    component_scope="OA112",
                    owner_scope_sha256=None,
                    groups=oa112.groups,
                ),
                VoyagePreparedComponent(
                    component_scope="OWNER_PRIVATE",
                    owner_scope_sha256=None,
                    groups=(),
                ),
            ),
            token_counter=voyage_token_counter,
        )
    except Exception as error:
        if isinstance(error, RagV2VoyageFullBundleError):
            raise
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_BATCH_PREPARATION") from error
    return PublicBaseVoyageBatchPreparation(exact30=exact30, oa112=oa112, plan=plan)


def materialize_public_base_voyage_batches(
    *,
    preparation: PublicBaseVoyageBatchPreparation,
    accumulator: VoyageBatchVectorAccumulator,
) -> PublicBaseVoyageMaterialization:
    """모든 batch가 성공한 뒤에만 canonical component order로 vector를 재조립한다."""

    if not isinstance(preparation, PublicBaseVoyageBatchPreparation) or not accumulator.complete:
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_BATCH_INCOMPLETE")
    try:
        effective_identities = preparation.plan.effective_chunk_identities()
        exact30 = materialize_prepared_external_exact30_public_voyage_component(
            preparation=preparation.exact30,
            vectors=accumulator.ordered_vectors(groups=preparation.exact30.groups),
            effective_chunk_identities=effective_identities,
        )
        oa112 = materialize_prepared_oa112_public_voyage_component(
            preparation=preparation.oa112,
            vectors=accumulator.ordered_vectors(groups=preparation.oa112.groups),
            effective_chunk_identities=effective_identities,
        )
    except Exception as error:
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_BATCH_MATERIALIZATION") from error
    return PublicBaseVoyageMaterialization(
        exact30=exact30,
        oa112=oa112,
        bundle_manifest_sha256=preparation.plan.plan_sha256,
    )


def prepare_public_base_voyage_full_bundle(
    *,
    tokenizer: RagTokenizer,
    oa112_registry: Oa112ActiveRegistry,
    oa112_local_cache_root: Path,
    oa112_parser: ApprovedDocumentParser | None = None,
    exact30_corpus: FrozenSourceCardCorpus | None = None,
) -> PublicBaseVoyagePreparation:
    """EXACT30+OA112와 empty owner sentinel을 one unmodifiable transport bundle로 prepare한다.

    No provider call occurs here. Any public source preparation failure prevents the bundle from being created, so
    callers cannot make an exact30-only or OA-only Voyage request using the activation packet.
    """

    try:
        exact30 = prepare_external_exact30_public_voyage_component(
            tokenizer=tokenizer,
            corpus=exact30_corpus,
        )
        oa112 = prepare_oa112_public_voyage_component(
            tokenizer=tokenizer,
            registry=oa112_registry,
            local_cache_root=oa112_local_cache_root,
            parser=oa112_parser,
        )
        bundle = build_pre_s5_voyage_full_bundle(
            components=(
                PreS5VoyageBundleComponent(
                    component_scope="EXACT30",
                    owner_scope_sha256=None,
                    groups=exact30.groups,
                ),
                PreS5VoyageBundleComponent(
                    component_scope="OA112",
                    owner_scope_sha256=None,
                    groups=oa112.groups,
                ),
                PreS5VoyageBundleComponent(
                    component_scope="OWNER_PRIVATE",
                    owner_scope_sha256=None,
                    groups=(),
                ),
            )
        )
    except Exception as error:
        if isinstance(error, RagV2VoyageFullBundleError):
            raise
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_FULL_BUNDLE_PREPARATION") from error
    if len(exact30.groups) != 30 or len(oa112.groups) != 112:
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_FULL_BUNDLE_PREPARATION")
    return PublicBaseVoyagePreparation(exact30=exact30, oa112=oa112, bundle=bundle)


def materialize_public_base_voyage_full_bundle(
    *,
    preparation: PublicBaseVoyagePreparation,
    embedder: VoyageFullBundleEmbedder,
) -> PublicBaseVoyageMaterialization:
    """exactly one full-bundle embedding invocation을 two profile-consistent components로 fan back 한다."""

    if (
        not isinstance(preparation, PublicBaseVoyagePreparation)
        or len(preparation.exact30.groups) != 30
        or len(preparation.oa112.groups) != 112
        or preparation.bundle.components[2].owner_scope_sha256 is not None
        or preparation.bundle.components[2].groups
    ):
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_FULL_BUNDLE_CONTEXT")
    expected_exact30_rows = sum(len(group.chunks) for group in preparation.exact30.groups)
    expected_oa112_rows = sum(len(group.chunks) for group in preparation.oa112.groups)
    if expected_exact30_rows < 30 or expected_oa112_rows < 112:
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_FULL_BUNDLE_CONTEXT")
    try:
        combined = validate_voyage_document_vectors(
            embedder.embed_full_bundle(bundle=preparation.bundle),
            expected_rows=expected_exact30_rows + expected_oa112_rows,
        )
        exact30 = materialize_prepared_external_exact30_public_voyage_component(
            preparation=preparation.exact30,
            vectors=combined[:expected_exact30_rows],
        )
        oa112 = materialize_prepared_oa112_public_voyage_component(
            preparation=preparation.oa112,
            vectors=combined[expected_exact30_rows:],
        )
    except Exception as error:
        if isinstance(error, RagV2VoyageFullBundleError):
            raise
        raise RagV2VoyageFullBundleError("VOYAGE_PUBLIC_FULL_BUNDLE_EMBEDDING") from error
    return PublicBaseVoyageMaterialization(
        exact30=exact30,
        oa112=oa112,
        bundle_manifest_sha256=preparation.bundle.manifest_sha256,
    )
