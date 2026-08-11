"""Resumable public EXACT30+OA112 Voyage materialization, evaluation, and CAS commands.

The provider-opening commands are materialize-stage-public-base and the stricter
materialize-stage-evaluate-public-base. Both prepare all public documents and the empty
OWNER_PRIVATE sentinel before consuming the exact pending document-batch packets; the latter then
consumes one EXACT30 and one OA112 evaluation-batch packet against the same staged vectors. No failed
database write is retried against Voyage, and no raw corpus, vector, credential, approval packet, or
provider response is persisted in a receipt or emitted to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from app.rag.benchmark_receipt_io import BenchmarkReceiptIoError, write_benchmark_receipt
from app.rag.bge_acquisition import DEFAULT_MODEL_ROOT
from app.rag.bge_runtime import BgeRuntimeError, BgeStaticTokenizer
from app.rag.oa112_active_registry import Oa112ActiveRegistry, Oa112ActiveRegistryError, load_oa112_active_registry
from app.rag.oa112_downloader import Oa112DownloadError, load_oa112_execution_binding
from app.rag.owner_file_io import OwnerFileIoError, read_owner_regular_file
from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5ProviderBinding,
    load_pre_s5_voyage_document_batch_activation,
    resolve_voyage_api_key,
)
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageContext4Transport,
    PreS5VoyageTransportError,
    UrllibPreS5VoyageHttpSender,
)
from app.rag.pre_s5_voyage_tokenizer import (
    LocalPreS5VoyageContext4Tokenizer,
    PreS5VoyageTokenizerError,
)
from app.rag.pre_s5_voyage_query_usage_repository import (
    PreS5VoyageQueryUsageRepositoryError,
    PsycopgPreS5VoyageQueryUsageRepository,
)
from app.rag.pre_s5_voyage_usage_repository import (
    PreS5VoyageUsageRepositoryError,
    PsycopgPreS5VoyageUsageRepository,
)
from app.rag.rag_v2_external_exact30_voyage_runner import RagV2PublicVoyageComponentContext
from app.rag.rag_v2_oa112_voyage_runner import RagV2Oa112VoyageComponentContext
from app.rag.rag_v2_public_voyage_activation_repository import (
    PublicVoyageActivationError,
    PublicVoyageActivationRequest,
    PsycopgRagV2PublicVoyageActivationRepository,
)
from app.rag.rag_v2_public_voyage_staging_repository import (
    PublicVoyageEvaluationEvidence,
    PublicVoyageStagingRepositoryError,
    PsycopgRagV2PublicVoyageStagingRepository,
)
from app.rag.rag_v2_public_voyage_evaluator import (
    PacketGatedPublicVoyageEvaluationBatchEmbedder,
    PublicVoyagePairEvaluationError,
    evaluate_public_voyage_pair,
    load_public_voyage_evaluation_inputs,
)
from app.rag.rag_v2_voyage_batch_repository import (
    PsycopgRagV2VoyageBatchRepository,
    RagV2VoyageBatchRepositoryError,
)
from app.rag.rag_v2_voyage_batching import RagV2VoyageBatchingError
from app.rag.rag_v2_voyage_full_bundle import (
    PublicBaseVoyageBatchPreparation,
    PublicBaseVoyageMaterialization,
    RagV2VoyageFullBundleError,
    materialize_public_base_voyage_batches,
    prepare_public_base_voyage_batches,
)

_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_STAGING_DIRECTORY = "staging"
_STAGING_FILENAME = "public-voyage-pair.v1.json"
_EVALUATION_DIRECTORY = "evaluation"
_EVALUATION_FILENAME = "public-voyage-pair.v1.json"
_BATCH_PLAN_DIRECTORY = "batch-plans"
_BATCH_PLAN_FILENAME = "public-voyage-batches.v1.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_STAGING_RECEIPT_FIELDS = frozenset(
    {
        "bundleManifestSha256",
        "documentEmbeddingProviderPhysicalCallCount",
        "embeddingProfileId",
        "exact30",
        "oa112",
        "schemaVersion",
        "state",
    }
)
_EXACT30_CONTEXT_FIELDS = frozenset(
    {
        "chunkCount",
        "componentGenerationId",
        "componentScope",
        "embeddingProfileId",
        "generationHash",
        "manifestHash",
        "materializationRunId",
        "memberDigests",
        "sourceCardCorpusManifestSha256",
        "sourceCount",
    }
)
_OA112_CONTEXT_FIELDS = frozenset(
    {
        "chunkCount",
        "componentGenerationId",
        "componentScope",
        "embeddingProfileId",
        "generationHash",
        "manifestHash",
        "materializationRunId",
        "memberDigests",
        "registryDigest",
        "registryId",
        "sourceCount",
    }
)
_EVALUATION_RECEIPT_FIELDS = frozenset(
    {
        "bundleManifestSha256",
        "contractId",
        "embeddingProfileId",
        "exact30",
        "exact30GenerationId",
        "oa112",
        "oa112GenerationId",
        "schemaVersion",
    }
)
_EVALUATION_EVIDENCE_FIELDS = frozenset(
    {
        "citationCoverage",
        "crossOwnerLeakCount",
        "directAdviceBlockRate",
        "evaluationDigest",
        "evaluationScopeClaimSha256",
        "exactTop5HitRate",
        "mixedProfileRowCount",
        "ownerDeleteResidualRowCount",
        "providerPhysicalCallCount",
        "schemaVersion",
        "trackRecallAt5",
        "warmP95Millis",
    }
)


class PublicVoyageCliError(ValueError):
    """Operator command failure with an optional content-free post-attempt summary only."""

    def __init__(self, code: str, *, attempt_summary: Mapping[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.attempt_summary = dict(attempt_summary) if attempt_summary is not None else None


@dataclass(frozen=True, slots=True)
class PublicVoyageStagedPair:
    """The two staged component identities and the one consumed document-embedding attempt."""

    bundle_manifest_sha256: str
    exact30: RagV2PublicVoyageComponentContext
    oa112: RagV2Oa112VoyageComponentContext
    document_embedding_provider_physical_call_count: int

    def content_free_receipt(self) -> dict[str, object]:
        """Return the durable resume projection; it intentionally excludes source content and vectors."""

        return {
            "bundleManifestSha256": self.bundle_manifest_sha256,
            "documentEmbeddingProviderPhysicalCallCount": self.document_embedding_provider_physical_call_count,
            "embeddingProfileId": _VOYAGE_PROFILE_ID,
            "exact30": _exact30_context_payload(self.exact30),
            "oa112": _oa112_context_payload(self.oa112),
            "schemaVersion": 1,
            "state": "STAGED",
        }


@dataclass(frozen=True, slots=True)
class PublicVoyageEvaluationPair:
    """Local-only evidence for both public components, already bound to one staged pair identity."""

    bundle_manifest_sha256: str
    exact30_generation_id: str
    oa112_generation_id: str
    exact30: PublicVoyageEvaluationEvidence
    oa112: PublicVoyageEvaluationEvidence


@dataclass(frozen=True, slots=True)
class _StagedPublicVoyageAttempt:
    """Process-local stage result that retains provider input/output only until the matching evaluation ends."""

    pair: PublicVoyageStagedPair
    materialization: PublicBaseVoyageMaterialization
    local_root: Path
    registry: Oa112ActiveRegistry
    binding: PreS5ProviderBinding
    api_key: str
    tokenizer_sha256: str


def main(argv: Sequence[str] | None = None) -> int:
    """Run one fixed, argv-secret-free public Voyage operation and emit a content-free JSON receipt."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("prepare-public-base-batches",):
        try:
            preparation = _prepare_public_base_batch_plan()
            receipt = preparation.content_free_receipt()
            write_benchmark_receipt(
                approved_root=_local_root(),
                relative_directory=_BATCH_PLAN_DIRECTORY,
                filename=_BATCH_PLAN_FILENAME,
                payload=_canonical_json(
                    {
                        **receipt,
                        "batches": [
                            batch.content_free_receipt() for batch in preparation.plan.batches
                        ],
                        "checkpointExpectedSourceCount": 142,
                        "providerPhysicalCallCount": 0,
                        "schemaVersion": 1,
                        "state": "PREPARED",
                    }
                ),
            )
        except (
            BenchmarkReceiptIoError,
            BgeRuntimeError,
            Oa112ActiveRegistryError,
            PreS5VoyageTokenizerError,
            RagV2VoyageFullBundleError,
            PublicVoyageCliError,
            ValueError,
        ):
            return _failure("PUBLIC_VOYAGE_BATCH_PREPARATION_FAILED")
        _emit(
            {
                "batchCount": len(preparation.plan.batches),
                "chunkCount": preparation.plan.chunk_count,
                "code": "PUBLIC_VOYAGE_BATCH_PLAN_PREPARED",
                "planSha256": preparation.plan.plan_sha256,
                "providerPhysicalCallCount": 0,
                "sourceCount": preparation.plan.source_count,
                "state": "PREPARED",
                "tokenCount": preparation.plan.token_count,
            }
        )
        return 0
    if arguments == ("materialize-stage-public-base",):
        writer_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not writer_dsn:
            return _failure("PUBLIC_VOYAGE_STAGE_DATABASE_DSN")
        try:
            result = _stage_public_base(writer_dsn=writer_dsn)
            _write_staged_pair_receipt(local_root=_local_root(), pair=result)
        except PublicVoyageCliError as error:
            return _failure(error.code, attempt_summary=error.attempt_summary)
        _emit(
            {
                "code": "PUBLIC_VOYAGE_PUBLIC_BASE_STAGED",
                "documentEmbeddingProviderPhysicalCallCount": result.document_embedding_provider_physical_call_count,
                "embeddingProfileId": _VOYAGE_PROFILE_ID,
                "exact30GenerationId": result.exact30.component_generation_id,
                "oa112GenerationId": result.oa112.component_generation_id,
                "state": "STAGED",
            }
        )
        return 0
    if arguments == ("materialize-stage-evaluate-public-base",):
        writer_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not writer_dsn:
            return _failure("PUBLIC_VOYAGE_STAGE_DATABASE_DSN")
        try:
            pair, evaluation = _stage_and_evaluate_public_base(writer_dsn=writer_dsn)
        except PublicVoyageCliError as error:
            return _failure(error.code, attempt_summary=error.attempt_summary)
        _emit(
            {
                "code": "PUBLIC_VOYAGE_PUBLIC_BASE_EVALUATED",
                "documentEmbeddingProviderPhysicalCallCount": pair.document_embedding_provider_physical_call_count,
                "embeddingProfileId": _VOYAGE_PROFILE_ID,
                "exact30GenerationId": pair.exact30.component_generation_id,
                "exact30QueryPhysicalCallCount": evaluation.exact30.provider_physical_call_count,
                "oa112GenerationId": pair.oa112.component_generation_id,
                "oa112QueryPhysicalCallCount": evaluation.oa112.provider_physical_call_count,
                "state": "EVALUATED",
            }
        )
        return 0
    if arguments == ("evaluate-public-base",):
        writer_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not writer_dsn:
            return _failure("PUBLIC_VOYAGE_EVALUATION_DATABASE_DSN")
        try:
            pair = _load_staged_pair(local_root=_local_root())
            evaluation = _load_evaluation_pair(local_root=_local_root(), pair=pair)
            repository = PsycopgRagV2PublicVoyageStagingRepository(database_dsn=writer_dsn)
            repository.evaluate(context=pair.exact30, evidence=evaluation.exact30)
            repository.evaluate(context=pair.oa112, evidence=evaluation.oa112)
        except PublicVoyageCliError as error:
            return _failure(error.code)
        except PublicVoyageStagingRepositoryError:
            return _failure("PUBLIC_VOYAGE_EVALUATION_UNAVAILABLE")
        _emit(
            {
                "code": "PUBLIC_VOYAGE_PUBLIC_BASE_EVALUATED",
                "embeddingProfileId": _VOYAGE_PROFILE_ID,
                "exact30GenerationId": pair.exact30.component_generation_id,
                "exact30QueryPhysicalCallCount": evaluation.exact30.provider_physical_call_count,
                "oa112GenerationId": pair.oa112.component_generation_id,
                "oa112QueryPhysicalCallCount": evaluation.oa112.provider_physical_call_count,
                "state": "EVALUATED",
            }
        )
        return 0
    if arguments == ("activate-public-base",):
        admin_dsn = os.environ.get("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "").strip()
        if not admin_dsn:
            return _failure("PUBLIC_VOYAGE_ACTIVATION_DATABASE_DSN")
        try:
            pair = _load_staged_pair(local_root=_local_root())
            activation = PsycopgRagV2PublicVoyageActivationRepository(database_dsn=admin_dsn).activate(
                request=PublicVoyageActivationRequest(exact30=pair.exact30, oa112=pair.oa112)
            )
        except PublicVoyageCliError as error:
            return _failure(error.code)
        except PublicVoyageActivationError as error:
            if str(error) in {"PUBLIC_VOYAGE_ACTIVATION_NOT_READY", "PUBLIC_VOYAGE_ACTIVATION_CONFLICT"}:
                return _failure(str(error))
            return _failure("PUBLIC_VOYAGE_ACTIVATION_UNAVAILABLE")
        _emit(
            {
                "code": "PUBLIC_VOYAGE_BASE_ACTIVE",
                "embeddingProfileId": activation.embedding_profile_id,
                "exact30GenerationId": activation.exact30_generation_id,
                "newPointerVersion": activation.new_pointer_version,
                "oa112GenerationId": activation.oa112_generation_id,
                "previousPointerVersion": activation.previous_pointer_version,
                "state": activation.state,
            }
        )
        return 0
    return _failure("PUBLIC_VOYAGE_COMMAND_INVALID")


def _stage_public_base(*, writer_dsn: str) -> PublicVoyageStagedPair:
    """Prepare all public groups, consume only pending batch packets, then stage both components once.

    A writer failure after the provider response is terminal for this invocation.  The command never
    recreates a transport or retries the packet; the operator receives a content-free marker and must
    use a new approved packet after fixing the local/DB cause.
    """

    return _stage_public_base_attempt(writer_dsn=writer_dsn).pair


def _stage_and_evaluate_public_base(
    *,
    writer_dsn: str,
) -> tuple[PublicVoyageStagedPair, PublicVoyageEvaluationPair]:
    """Run the real profile evaluation after all document batches complete, with no BGE substitution.

    Staging is made durable before query evaluation begins, so a later packet/provider failure never causes
    the document response to be logged or silently retried.  The process holds canonical text and vectors only
    long enough to execute the two component-batched RRF suites and then writes content-free evidence.
    """

    attempt = _stage_public_base_attempt(writer_dsn=writer_dsn)
    _write_staged_pair_receipt(local_root=attempt.local_root, pair=attempt.pair)
    try:
        exact30_queries, exact30_fixture_digest, oa112_queries, oa112_manifest_digest = (
            load_public_voyage_evaluation_inputs(
                local_root=attempt.local_root,
                registry=attempt.registry,
                exact30_context=attempt.materialization.exact30.context,
            )
        )
        query_embedder = PacketGatedPublicVoyageEvaluationBatchEmbedder(
            local_root=attempt.local_root,
            binding=attempt.binding,
            api_key=attempt.api_key,
            usage_repository=PsycopgPreS5VoyageQueryUsageRepository(database_dsn=writer_dsn),
            exact30_queries=exact30_queries,
            oa112_queries=oa112_queries,
            tokenizer_sha256=attempt.tokenizer_sha256,
            sender=UrllibPreS5VoyageHttpSender(),
        )
    except (
        Oa112ActiveRegistryError,
        PreS5ProviderActivationError,
        PreS5VoyageQueryUsageRepositoryError,
        PublicVoyagePairEvaluationError,
        ValueError,
    ):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_PRECONDITION") from None

    try:
        evaluation = evaluate_public_voyage_pair(
            exact30_records=attempt.materialization.exact30.records,
            exact30_context=attempt.materialization.exact30.context,
            oa112_records=attempt.materialization.oa112.records,
            oa112_context=attempt.materialization.oa112.context,
            oa112_registry_digest=attempt.registry.registry_digest,
            exact30_queries=exact30_queries,
            exact30_fixture_digest=exact30_fixture_digest,
            oa112_queries=oa112_queries,
            oa112_manifest_digest=oa112_manifest_digest,
            query_embedder=query_embedder,
        )
    except PublicVoyagePairEvaluationError:
        raise PublicVoyageCliError(
            "PUBLIC_VOYAGE_EVALUATION_QUERY_REQUIRED",
            attempt_summary=query_embedder.content_free_summary(),
        ) from None
    if not evaluation.acceptance_passed:
        raise PublicVoyageCliError(
            "PUBLIC_VOYAGE_EVALUATION_THRESHOLDS_FAILED",
            attempt_summary=query_embedder.content_free_summary(),
        )

    pair_evaluation = PublicVoyageEvaluationPair(
        bundle_manifest_sha256=attempt.pair.bundle_manifest_sha256,
        exact30_generation_id=attempt.pair.exact30.component_generation_id,
        oa112_generation_id=attempt.pair.oa112.component_generation_id,
        exact30=evaluation.exact30,
        oa112=evaluation.oa112,
    )
    write_public_voyage_pair_evaluation_receipt(
        local_root=attempt.local_root,
        pair=attempt.pair,
        exact30=pair_evaluation.exact30,
        oa112=pair_evaluation.oa112,
    )
    try:
        repository = PsycopgRagV2PublicVoyageStagingRepository(database_dsn=writer_dsn)
        repository.evaluate(context=attempt.pair.exact30, evidence=pair_evaluation.exact30)
        repository.evaluate(context=attempt.pair.oa112, evidence=pair_evaluation.oa112)
    except PublicVoyageStagingRepositoryError:
        raise PublicVoyageCliError(
            "PUBLIC_VOYAGE_EVALUATION_WRITER_REQUIRED",
            attempt_summary=query_embedder.content_free_summary(),
        ) from None
    return attempt.pair, pair_evaluation


def _prepare_public_base_batch_plan() -> PublicBaseVoyageBatchPreparation:
    """provider/DB capability 없이 checkpoint 142개와 exact content-free batch set만 준비한다."""

    local_root = _local_root()
    registry = _load_oa112_registry(local_root)
    tokenizer = BgeStaticTokenizer.from_file(_bge_packet_root() / "onnx" / "tokenizer.json")
    token_counter = LocalPreS5VoyageContext4Tokenizer.from_local_root(
        local_root=local_root,
        expected_sha256=_voyage_tokenizer_sha256(),
    )
    return prepare_public_base_voyage_batches(
        tokenizer=tokenizer,
        voyage_token_counter=token_counter,
        oa112_registry=registry,
        oa112_local_cache_root=local_root,
        checkpoint_local_corpus_root=local_root,
    )


def _stage_public_base_attempt(*, writer_dsn: str) -> _StagedPublicVoyageAttempt:
    """checkpoint를 reuse하고 DB 완료 batch를 건너뛰며 미완료 packet만 순서대로 한 번씩 소비한다."""

    try:
        local_root = _local_root()
        registry = _load_oa112_registry(local_root)
        tokenizer = BgeStaticTokenizer.from_file(_bge_packet_root() / "onnx" / "tokenizer.json")
        tokenizer_sha256 = _voyage_tokenizer_sha256()
        token_counter = LocalPreS5VoyageContext4Tokenizer.from_local_root(
            local_root=local_root,
            expected_sha256=tokenizer_sha256,
        )
        preparation = prepare_public_base_voyage_batches(
            tokenizer=tokenizer,
            voyage_token_counter=token_counter,
            oa112_registry=registry,
            oa112_local_cache_root=local_root,
            checkpoint_local_corpus_root=local_root,
        )
        binding = _execution_binding(local_root=local_root)
        api_key = resolve_voyage_api_key(os.environ)
        usage_repository = PsycopgPreS5VoyageUsageRepository(database_dsn=writer_dsn)
        batch_repository = PsycopgRagV2VoyageBatchRepository(database_dsn=writer_dsn)
        accumulator = batch_repository.resume(plan=preparation.plan)
    except (
        BgeRuntimeError,
        Oa112ActiveRegistryError,
        Oa112DownloadError,
        PreS5ProviderActivationError,
        PreS5VoyageTokenizerError,
        PreS5VoyageUsageRepositoryError,
        RagV2VoyageBatchRepositoryError,
        RagV2VoyageBatchingError,
        RagV2VoyageFullBundleError,
        ValueError,
    ):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_PRECONDITION") from None

    for batch in accumulator.pending_batches:
        try:
            activation = load_pre_s5_voyage_document_batch_activation(
                local_root=local_root,
                binding=binding,
                batch_plan_sha256=preparation.plan.plan_sha256,
                batch_id=batch.batch_id,
                batch_manifest_sha256=batch.batch_manifest_sha256,
                batch_ordinal=batch.batch_ordinal,
                batch_count=batch.batch_count,
                token_count=batch.token_count,
                chunk_count=batch.chunk_count,
                group_count=batch.group_count,
            )
            lease = usage_repository.reserve_document_batch(
                activation=activation,
                plan=preparation.plan,
                batch=batch,
            )
            transport = PreS5VoyageContext4Transport(
                activation=activation,
                api_key=api_key,
                lease=lease,
                token_counter=token_counter,
                sender=UrllibPreS5VoyageHttpSender(),
            )
            vectors = transport.embed_document_batch(
                batch_plan_sha256=preparation.plan.plan_sha256,
                batch=batch,
            )
            batch_repository.stage_success(
                activation=activation,
                plan=preparation.plan,
                batch=batch,
                vectors=vectors,
            )
            accumulator.record_success(batch=batch, vectors=vectors)
        except (
            PreS5ProviderActivationError,
            PreS5VoyageUsageRepositoryError,
            PreS5VoyageTransportError,
            RagV2VoyageBatchRepositoryError,
            RagV2VoyageBatchingError,
        ):
            raise PublicVoyageCliError(
                "PUBLIC_VOYAGE_DOCUMENT_BATCH_FAILED",
                attempt_summary={
                    "batchCount": len(preparation.plan.batches),
                    "completedBatchCount": len(accumulator.completed_batch_ids),
                    "failedBatchId": batch.batch_id,
                    "rawArtifactCount": 0,
                },
            ) from None
    try:
        materialization = materialize_public_base_voyage_batches(
            preparation=preparation,
            accumulator=accumulator,
        )
        repository = PsycopgRagV2PublicVoyageStagingRepository(database_dsn=writer_dsn)
        exact30_receipts = repository.stage_component(
            records=materialization.exact30.records,
            context=materialization.exact30.context,
        )
        oa112_receipts = repository.stage_component(
            records=materialization.oa112.records,
            context=materialization.oa112.context,
        )
        _validate_staging_receipts(
            materialization=materialization,
            exact30_count=len(exact30_receipts),
            oa112_count=len(oa112_receipts),
        )
    except (RagV2VoyageFullBundleError, PublicVoyageStagingRepositoryError):
        raise PublicVoyageCliError(
            "PUBLIC_VOYAGE_POSTCALL_STAGING_REQUIRED",
            attempt_summary={
                "batchCount": len(preparation.plan.batches),
                "completedBatchCount": len(accumulator.completed_batch_ids),
                "rawArtifactCount": 0,
            },
        ) from None
    pair = PublicVoyageStagedPair(
        bundle_manifest_sha256=preparation.plan.plan_sha256,
        exact30=materialization.exact30.context,
        oa112=materialization.oa112.context,
        document_embedding_provider_physical_call_count=len(preparation.plan.batches),
    )
    return _StagedPublicVoyageAttempt(
        pair=pair,
        materialization=materialization,
        local_root=local_root,
        registry=registry,
        binding=binding,
        api_key=api_key,
        tokenizer_sha256=tokenizer_sha256,
    )


def write_public_voyage_pair_evaluation_receipt(
    *,
    local_root: Path,
    pair: PublicVoyageStagedPair,
    exact30: PublicVoyageEvaluationEvidence,
    oa112: PublicVoyageEvaluationEvidence,
) -> None:
    """Persist a content-free evaluator result before the independent writer transition.

    The query evaluator, not this CLI, supplies the metrics.  This writer freezes the staged component
    IDs and exact 10/112 logical query counts so an old evaluation cannot be applied to a new bundle.
    """

    _validate_evaluation_counts(exact30=exact30, oa112=oa112)
    payload = {
        "bundleManifestSha256": pair.bundle_manifest_sha256,
        "contractId": "rag-v2-public-voyage-pair-evaluation-receipt-v1",
        "embeddingProfileId": _VOYAGE_PROFILE_ID,
        "exact30": exact30.as_payload(),
        "exact30GenerationId": pair.exact30.component_generation_id,
        "oa112": oa112.as_payload(),
        "oa112GenerationId": pair.oa112.component_generation_id,
        "schemaVersion": 1,
    }
    try:
        write_benchmark_receipt(
            approved_root=local_root,
            relative_directory=_EVALUATION_DIRECTORY,
            filename=_EVALUATION_FILENAME,
            payload=_canonical_json(payload),
        )
    except BenchmarkReceiptIoError:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT") from None


def _write_staged_pair_receipt(*, local_root: Path, pair: PublicVoyageStagedPair) -> None:
    """Make a staged-pair resume identity durable only after every public source was staged."""

    try:
        write_benchmark_receipt(
            approved_root=local_root,
            relative_directory=_STAGING_DIRECTORY,
            filename=_STAGING_FILENAME,
            payload=_canonical_json(pair.content_free_receipt()),
        )
    except BenchmarkReceiptIoError:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT") from None


def _load_staged_pair(*, local_root: Path) -> PublicVoyageStagedPair:
    """Load one exact staged pair without accepting a path, profile, or component selector from argv."""

    payload = _read_local_json(
        local_root=local_root,
        relative_path=f"{_STAGING_DIRECTORY}/{_STAGING_FILENAME}",
        code="PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED",
    )
    if set(payload) != _STAGING_RECEIPT_FIELDS:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    if (
        payload.get("schemaVersion") != 1
        or payload.get("state") != "STAGED"
        or payload.get("embeddingProfileId") != _VOYAGE_PROFILE_ID
        or type(payload.get("documentEmbeddingProviderPhysicalCallCount")) is not int
        or not 1 <= cast(int, payload.get("documentEmbeddingProviderPhysicalCallCount")) <= 10_000
        or not _is_sha256(payload.get("bundleManifestSha256"))
    ):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return PublicVoyageStagedPair(
        bundle_manifest_sha256=_required_hash(payload.get("bundleManifestSha256")),
        exact30=_parse_exact30_context(payload.get("exact30")),
        oa112=_parse_oa112_context(payload.get("oa112")),
        document_embedding_provider_physical_call_count=cast(
            int, payload.get("documentEmbeddingProviderPhysicalCallCount")
        ),
    )


def _load_evaluation_pair(*, local_root: Path, pair: PublicVoyageStagedPair) -> PublicVoyageEvaluationPair:
    """Require local evaluation evidence to bind exactly to the staged pair before writer evaluation."""

    payload = _read_local_json(
        local_root=local_root,
        relative_path=f"{_EVALUATION_DIRECTORY}/{_EVALUATION_FILENAME}",
        code="PUBLIC_VOYAGE_EVALUATION_RECEIPT_REQUIRED",
    )
    if (
        set(payload) != _EVALUATION_RECEIPT_FIELDS
        or payload.get("schemaVersion") != 1
        or payload.get("contractId") != "rag-v2-public-voyage-pair-evaluation-receipt-v1"
        or payload.get("embeddingProfileId") != _VOYAGE_PROFILE_ID
        or payload.get("bundleManifestSha256") != pair.bundle_manifest_sha256
        or payload.get("exact30GenerationId") != pair.exact30.component_generation_id
        or payload.get("oa112GenerationId") != pair.oa112.component_generation_id
    ):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT_REQUIRED")
    try:
        exact30 = _parse_evaluation_evidence(payload.get("exact30"))
        oa112 = _parse_evaluation_evidence(payload.get("oa112"))
        _validate_evaluation_counts(exact30=exact30, oa112=oa112)
    except (PublicVoyageStagingRepositoryError, PublicVoyageCliError, ValueError):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT_REQUIRED") from None
    return PublicVoyageEvaluationPair(
        bundle_manifest_sha256=pair.bundle_manifest_sha256,
        exact30_generation_id=pair.exact30.component_generation_id,
        oa112_generation_id=pair.oa112.component_generation_id,
        exact30=exact30,
        oa112=oa112,
    )


def _execution_binding(*, local_root: Path) -> PreS5ProviderBinding:
    """Reuse the OA execution evidence's clean HEAD/tree and CI/security digests for Voyage packets."""

    binding = load_oa112_execution_binding(
        approved_root=local_root,
        relative_path="oa112-execution-evidence.v1.json",
        repository_root=_repository_root(),
    )
    return PreS5ProviderBinding(
        head_commit=binding.head_sha,
        tree_object=binding.tree_sha256,
        ci_digest=binding.ci_digest,
        security_digest=binding.security_digest,
    )


def _load_oa112_registry(local_root: Path) -> Oa112ActiveRegistry:
    """Read only the fixed local active registry; reserve sources never enter a public Voyage bundle."""

    return load_oa112_active_registry(
        approved_root=local_root,
        relative_path="oa112-active-registry.v1.json",
    )


def _bge_packet_root() -> Path:
    """Use the existing pinned tokenizer packet without loading a BGE embedding model or fallback."""

    value = os.environ.get("CAPSTONE_RAG_BGE_PACKET_ROOT", "").strip()
    if not value:
        return DEFAULT_MODEL_ROOT
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts:
        raise BgeRuntimeError("BGE_PACKET_VERIFICATION_FAILED")
    return root


def _voyage_tokenizer_sha256() -> str:
    """acquisition evidence가 주입한 non-secret exact digest만 받아 local artifact를 검증한다."""

    value = os.environ.get("CAPSTONE_RAG_VOYAGE_TOKENIZER_SHA256", "").strip()
    if _SHA256.fullmatch(value) is None:
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_SHA256")
    return value


def _local_root() -> Path:
    """Resolve the one local control/cache root without accepting raw paths through command arguments."""

    value = os.environ.get("CAPSTONE_RAG_LOCAL_ROOT", "").strip()
    root = Path(value)
    if not value or not root.is_absolute() or ".." in root.parts:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_LOCAL_CONTROL_REQUIRED")
    return root


def _repository_root() -> Path:
    """Provider packet binding uses the real checkout, never an installed package snapshot."""

    for candidate in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
        if (candidate / ".git").exists():
            return candidate
    raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE")


def _validate_staging_receipts(
    *,
    materialization: PublicBaseVoyageMaterialization,
    exact30_count: int,
    oa112_count: int,
) -> None:
    """The in-memory response can be acknowledged only when both complete source sets reached V45 staging."""

    if (
        exact30_count != materialization.exact30.context.expected_source_count
        or oa112_count != materialization.oa112.context.expected_source_count
        or exact30_count != 30
        or oa112_count != 112
    ):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")


def _validate_evaluation_counts(
    *,
    exact30: PublicVoyageEvaluationEvidence,
    oa112: PublicVoyageEvaluationEvidence,
) -> None:
    """Prevent an evaluator from hiding live query attempts or treating a partial corpus as accepted."""

    if exact30.provider_physical_call_count != 1 or oa112.provider_physical_call_count != 1:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT")


def _exact30_context_payload(context: RagV2PublicVoyageComponentContext) -> dict[str, object]:
    """Copy only the identity fields required to reconstruct a future admin activation request."""

    if context.embedding_profile_id != _VOYAGE_PROFILE_ID or context.component_scope != "EXACT30":
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT")
    return {
        "chunkCount": context.expected_chunk_count,
        "componentGenerationId": context.component_generation_id,
        "componentScope": context.component_scope,
        "embeddingProfileId": context.embedding_profile_id,
        "generationHash": context.generation_hash,
        "manifestHash": context.manifest_hash,
        "materializationRunId": context.materialization_run_id,
        "memberDigests": list(context.member_digests),
        "sourceCardCorpusManifestSha256": context.source_card_corpus_manifest_sha256,
        "sourceCount": context.expected_source_count,
    }


def _oa112_context_payload(context: RagV2Oa112VoyageComponentContext) -> dict[str, object]:
    """Copy the OA registry binding but no raw cache location or rights text into the resume receipt."""

    if context.embedding_profile_id != _VOYAGE_PROFILE_ID or context.component_scope != "OA112":
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT")
    return {
        "chunkCount": context.expected_chunk_count,
        "componentGenerationId": context.component_generation_id,
        "componentScope": context.component_scope,
        "embeddingProfileId": context.embedding_profile_id,
        "generationHash": context.generation_hash,
        "manifestHash": context.manifest_hash,
        "materializationRunId": context.materialization_run_id,
        "memberDigests": list(context.member_digests),
        "registryDigest": context.registry_digest,
        "registryId": context.registry_id,
        "sourceCount": context.expected_source_count,
    }


def _parse_exact30_context(value: object) -> RagV2PublicVoyageComponentContext:
    """Rehydrate only a closed receipt shape; V45/V43 independently recheck it against persisted rows."""

    if not isinstance(value, dict) or set(value) != _EXACT30_CONTEXT_FIELDS:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    context = RagV2PublicVoyageComponentContext(
        component_scope=_required_text(value.get("componentScope")),
        component_generation_id=_required_text(value.get("componentGenerationId")),
        materialization_run_id=_required_text(value.get("materializationRunId")),
        generation_hash=_required_hash(value.get("generationHash")),
        manifest_hash=_required_hash(value.get("manifestHash")),
        expected_source_count=_required_int(value.get("sourceCount")),
        expected_chunk_count=_required_int(value.get("chunkCount")),
        embedding_profile_id=cast(
            Literal["voyage_context_4_1024_v1"],
            _required_text(value.get("embeddingProfileId")),
        ),
        member_digests=_required_hash_tuple(value.get("memberDigests"), count=30),
        source_card_corpus_manifest_sha256=_required_hash(value.get("sourceCardCorpusManifestSha256")),
    )
    if (
        context.component_scope != "EXACT30"
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or context.expected_source_count != 30
        or context.expected_chunk_count < 30
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
    ):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return context


def _parse_oa112_context(value: object) -> RagV2Oa112VoyageComponentContext:
    """Rehydrate the closed OA public component identity without a raw registry document or cache path."""

    if not isinstance(value, dict) or set(value) != _OA112_CONTEXT_FIELDS:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    context = RagV2Oa112VoyageComponentContext(
        component_scope=cast(Literal["OA112"], _required_text(value.get("componentScope"))),
        component_generation_id=_required_text(value.get("componentGenerationId")),
        materialization_run_id=_required_text(value.get("materializationRunId")),
        generation_hash=_required_hash(value.get("generationHash")),
        manifest_hash=_required_hash(value.get("manifestHash")),
        expected_source_count=_required_int(value.get("sourceCount")),
        expected_chunk_count=_required_int(value.get("chunkCount")),
        embedding_profile_id=cast(
            Literal["voyage_context_4_1024_v1"],
            _required_text(value.get("embeddingProfileId")),
        ),
        member_digests=_required_hash_tuple(value.get("memberDigests"), count=112),
        registry_id=_required_text(value.get("registryId")),
        registry_digest=_required_hash(value.get("registryDigest")),
    )
    if (
        context.component_scope != "OA112"
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or context.expected_source_count != 112
        or context.expected_chunk_count < 112
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
    ):
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return context


def _parse_evaluation_evidence(value: object) -> PublicVoyageEvaluationEvidence:
    """Parse one content-free metric object and leave count/profile enforcement to the pair loader."""

    if not isinstance(value, dict) or set(value) != _EVALUATION_EVIDENCE_FIELDS:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT_REQUIRED")
    return PublicVoyageEvaluationEvidence(
        evaluation_digest=_required_hash(value.get("evaluationDigest")),
        evaluation_scope_claim_sha256=_required_hash(value.get("evaluationScopeClaimSha256")),
        exact_top5_hit_rate=_required_ratio(value.get("exactTop5HitRate")),
        track_recall_at5=_required_ratio(value.get("trackRecallAt5")),
        citation_coverage=_required_ratio(value.get("citationCoverage")),
        direct_advice_block_rate=_required_ratio(value.get("directAdviceBlockRate")),
        cross_owner_leak_count=_required_int(value.get("crossOwnerLeakCount")),
        mixed_profile_row_count=_required_int(value.get("mixedProfileRowCount")),
        owner_delete_residual_row_count=_required_int(value.get("ownerDeleteResidualRowCount")),
        warm_p95_millis=_required_positive_float(value.get("warmP95Millis")),
        provider_physical_call_count=_required_int(value.get("providerPhysicalCallCount")),
    )


def _read_local_json(*, local_root: Path, relative_path: str, code: str) -> dict[str, object]:
    """Read a bounded local owner receipt without propagating its pathname or bytes to callers."""

    try:
        raw = read_owner_regular_file(
            approved_root=local_root,
            relative_path=relative_path,
            max_bytes=64 * 1024,
        ).content
        parsed = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    except (OwnerFileIoError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise PublicVoyageCliError(code) from None
    if not isinstance(parsed, dict):
        raise PublicVoyageCliError(code)
    return parsed


def _canonical_json(value: Mapping[str, object]) -> bytes:
    """Write receipt bytes canonically so digest/identity comparisons never depend on local formatting."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or value != value.strip():
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return value


def _required_hash(value: object) -> str:
    text = _required_text(value)
    if _SHA256.fullmatch(text) is None:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return text


def _required_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return value


def _required_hash_tuple(value: object, *, count: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != count:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    hashes = tuple(_required_hash(item) for item in value)
    if len(set(hashes)) != count:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_STAGE_RECEIPT_REQUIRED")
    return hashes


def _required_ratio(value: object) -> float:
    if type(value) is not float or not 0 <= value <= 1:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT_REQUIRED")
    return value


def _required_positive_float(value: object) -> float:
    if type(value) is not float or value <= 0:
        raise PublicVoyageCliError("PUBLIC_VOYAGE_EVALUATION_RECEIPT_REQUIRED")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _reject_duplicate_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _failure(code: str, *, attempt_summary: Mapping[str, object] | None = None) -> int:
    """Emit only a stable terminal code plus safe post-attempt state, never chained provider/DB detail."""

    payload: dict[str, object] = {"code": code, "state": "FAILED"}
    if attempt_summary is not None:
        payload["attempt"] = dict(attempt_summary)
    _emit(payload)
    return 2


def _emit(value: Mapping[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
