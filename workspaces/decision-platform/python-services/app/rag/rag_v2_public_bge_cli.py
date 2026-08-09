from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.rag.bge_acquisition import DEFAULT_MODEL_ROOT
from app.rag.authorized_retrieval_adapters import LocalBgeQueryEmbedder
from app.rag.bge_runtime import BgeRuntimeError, BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.oa112_active_registry import (
    Oa112ActiveRegistry,
    Oa112ActiveRegistryError,
    load_oa112_active_registry,
)
from app.rag.oa112_downloader import (
    Oa112DownloadError,
    Oa112DownloadReceipt,
    download_oa112_local_cache,
    load_oa112_execution_binding,
    load_oa112_download_packet,
)
from app.rag.rag_v2_exact30_bge_runner import (
    Exact30PublicBgeMaterialization,
    RagV2Exact30BgeRunnerError,
    materialize_exact30_public_bge_component,
)
from app.rag.rag_v2_oa112_bge_runner import (
    Oa112PublicBgeMaterialization,
    RagV2Oa112BgeRunnerError,
    materialize_oa112_public_bge_component,
)
from app.rag.rag_v2_public_bge_staging_repository import (
    PublicBgeStagingRepositoryError,
    PsycopgRagV2PublicBgeStagingRepository,
    RagV2PublicBgeStagingReceipt,
)
from app.rag.rag_v2_public_bge_activation_repository import (
    PublicBgeActivationError,
    PublicBgeActivationRequest,
    PsycopgRagV2PublicBgeActivationRepository,
)
from app.rag.rag_v2_public_bge_evaluator import (
    PublicBgePairEvaluationError,
    evaluate_public_bge_pair,
    evaluation_plan_digest,
    load_exact30_evaluation_queries,
    load_oa112_evaluation_queries,
    load_public_bge_pair_evaluation_evidence,
    write_public_bge_pair_evaluation_receipt,
)


@dataclass(frozen=True, slots=True)
class _Exact30StageResult:
    """CLI가 출력할 수 있는 content-free exact-30 stage summary다."""

    materialization: Exact30PublicBgeMaterialization
    receipts: tuple[RagV2PublicBgeStagingReceipt, ...]

    def content_free_receipt(self) -> dict[str, object]:
        """writer receipt의 count/state만 public operator output으로 투영한다."""

        if len(self.receipts) != self.materialization.context.expected_source_count:
            raise PublicBgeStagingRepositoryError("PUBLIC_BGE_STAGE_RECEIPT")
        final = self.receipts[-1]
        if final.state != "STAGED":
            raise PublicBgeStagingRepositoryError("PUBLIC_BGE_STAGE_RECEIPT")
        receipt = dict(self.materialization.content_free_receipt())
        receipt.update(
            {
                "sourceReusedCount": sum(item.source_reused for item in self.receipts),
                "state": final.state,
            }
        )
        return receipt


@dataclass(frozen=True, slots=True)
class _Oa112StageResult:
    """CLI가 출력할 수 있는 content-free OA112 stage summary다."""

    materialization: Oa112PublicBgeMaterialization
    receipts: tuple[RagV2PublicBgeStagingReceipt, ...]

    def content_free_receipt(self) -> dict[str, object]:
        """full 112-source receipt와 final staged state만 operator output에 남긴다."""

        if len(self.receipts) != self.materialization.context.expected_source_count:
            raise PublicBgeStagingRepositoryError("PUBLIC_BGE_STAGE_RECEIPT")
        final = self.receipts[-1]
        if final.state != "STAGED":
            raise PublicBgeStagingRepositoryError("PUBLIC_BGE_STAGE_RECEIPT")
        receipt = dict(self.materialization.content_free_receipt())
        receipt.update(
            {
                "sourceReusedCount": sum(item.source_reused for item in self.receipts),
                "state": final.state,
            }
        )
        return receipt


def main(argv: Sequence[str] | None = None) -> int:
    """exact-30 local BGE operator command를 content-free JSON으로만 실행한다.

    이 CLI는 local model materialization, writer-only staging, evaluated public pair의 admin CAS
    activation만 수행한다. evaluation metric/raw/vector/DSN argv는 지원하지 않으며 OA112 download와
    provider transport는 별도 approval packet을 가진 명령으로 유지한다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("exact30-materialize",):
        try:
            materialization = _materialize_exact30()
        except (BgeRuntimeError, RagV2Exact30BgeRunnerError):
            return _failure("EXACT30_LOCAL_BGE_MATERIALIZATION_FAILED")
        receipt = dict(materialization.content_free_receipt())
        receipt.update(
            {
                "code": "EXACT30_LOCAL_BGE_MATERIALIZED",
                "state": "MATERIALIZED",
            }
        )
        _emit(receipt)
        return 0
    if arguments == ("exact30-stage",):
        database_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not database_dsn:
            return _failure("PUBLIC_BGE_STAGE_DATABASE_DSN")
        try:
            staged = _stage_exact30(database_dsn=database_dsn)
            receipt = staged.content_free_receipt()
        except (BgeRuntimeError, RagV2Exact30BgeRunnerError):
            return _failure("EXACT30_LOCAL_BGE_MATERIALIZATION_FAILED")
        except PublicBgeStagingRepositoryError:
            return _failure("PUBLIC_BGE_STAGE_UNAVAILABLE")
        receipt.update(
            {
                "code": "EXACT30_PUBLIC_BGE_STAGED",
            }
        )
        _emit(receipt)
        return 0
    if arguments == ("activate-public-base",):
        admin_database_dsn = os.environ.get("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "").strip()
        if not admin_database_dsn:
            return _failure("PUBLIC_BGE_ACTIVATION_DATABASE_DSN")
        try:
            exact30_materialization = _materialize_exact30()
            oa112_materialization = _materialize_oa112()
            activation = PsycopgRagV2PublicBgeActivationRepository(
                database_dsn=admin_database_dsn
            ).activate(
                request=PublicBgeActivationRequest(
                    exact30=exact30_materialization.context,
                    oa112=oa112_materialization.context,
                )
            )
        except Oa112ActiveRegistryError:
            return _failure("OA112_ACTIVE_REGISTRY_REQUIRED")
        except Oa112DownloadError:
            return _failure("OA112_LOCAL_CONTROL_REQUIRED")
        except (BgeRuntimeError, RagV2Exact30BgeRunnerError, RagV2Oa112BgeRunnerError):
            return _failure("PUBLIC_BGE_ACTIVATION_MATERIALIZATION_FAILED")
        except PublicBgeActivationError as error:
            if str(error) in {
                "PUBLIC_BGE_ACTIVATION_NOT_READY",
                "PUBLIC_BGE_ACTIVATION_CONFLICT",
            }:
                return _failure(str(error))
            return _failure("PUBLIC_BGE_ACTIVATION_UNAVAILABLE")
        _emit(
            {
                "code": "PUBLIC_BGE_BASE_ACTIVE",
                "embeddingProfileId": activation.embedding_profile_id,
                "exact30GenerationId": activation.exact30_generation_id,
                "newPointerVersion": activation.new_pointer_version,
                "oa112GenerationId": activation.oa112_generation_id,
                "previousPointerVersion": activation.previous_pointer_version,
                "state": activation.state,
            }
        )
        return 0
    if arguments == ("evaluate-public-base",):
        database_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not database_dsn:
            return _failure("PUBLIC_BGE_EVALUATION_DATABASE_DSN")
        try:
            receipt, reused = _evaluate_public_base(database_dsn=database_dsn)
        except Oa112ActiveRegistryError:
            return _failure("OA112_ACTIVE_REGISTRY_REQUIRED")
        except Oa112DownloadError:
            return _failure("OA112_LOCAL_CONTROL_REQUIRED")
        except (BgeRuntimeError, RagV2Exact30BgeRunnerError, RagV2Oa112BgeRunnerError):
            return _failure("PUBLIC_BGE_EVALUATION_MATERIALIZATION_FAILED")
        except PublicBgePairEvaluationError as error:
            code = str(error)
            if code.startswith("PUBLIC_BGE_EVALUATION_"):
                return _failure(code)
            return _failure("PUBLIC_BGE_EVALUATION_UNAVAILABLE")
        except PublicBgeStagingRepositoryError:
            return _failure("PUBLIC_BGE_EVALUATION_UNAVAILABLE")
        _emit(
            {
                "code": "PUBLIC_BGE_PAIR_EVALUATION_REUSED" if reused else "PUBLIC_BGE_PAIR_EVALUATED",
                "embeddingProfileId": receipt["embeddingProfileId"],
                "exact30GenerationId": receipt["exact30GenerationId"],
                "oa112GenerationId": receipt["oa112GenerationId"],
                "state": "EVALUATED",
            }
        )
        return 0
    if arguments == ("oa112-download",):
        try:
            download_receipt = _download_oa112()
        except Oa112ActiveRegistryError:
            return _failure("OA112_ACTIVE_REGISTRY_REQUIRED")
        except Oa112DownloadError as error:
            _emit(
                {
                    "attemptCount": error.attempt_count,
                    "code": error.code,
                    "failureReceiptWritten": error.failure_receipt_written,
                    "physicalCallCount": error.physical_call_count,
                    "state": "FAILED",
                }
            )
            return 2
        _emit(
            {
                "attemptCount": download_receipt.attempt_count,
                "code": "OA112_LOCAL_CACHE_READY",
                "downloadedSourceCount": download_receipt.downloaded_source_count,
                "physicalCallCount": download_receipt.physical_call_count,
                "reusedSourceCount": download_receipt.reused_source_count,
                "state": "DOWNLOADED",
            }
        )
        return 0
    if arguments == ("oa112-materialize",):
        try:
            oa112_materialization = _materialize_oa112()
        except Oa112ActiveRegistryError:
            return _failure("OA112_ACTIVE_REGISTRY_REQUIRED")
        except Oa112DownloadError:
            return _failure("OA112_LOCAL_CONTROL_REQUIRED")
        except (BgeRuntimeError, RagV2Oa112BgeRunnerError):
            return _failure("OA112_LOCAL_BGE_MATERIALIZATION_FAILED")
        receipt = dict(oa112_materialization.content_free_receipt())
        receipt.update(
            {
                "code": "OA112_LOCAL_BGE_MATERIALIZED",
                "state": "MATERIALIZED",
            }
        )
        _emit(receipt)
        return 0
    if arguments == ("oa112-stage",):
        database_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not database_dsn:
            return _failure("PUBLIC_BGE_STAGE_DATABASE_DSN")
        try:
            oa112_staged = _stage_oa112(database_dsn=database_dsn)
            receipt = oa112_staged.content_free_receipt()
        except Oa112ActiveRegistryError:
            return _failure("OA112_ACTIVE_REGISTRY_REQUIRED")
        except Oa112DownloadError:
            return _failure("OA112_LOCAL_CONTROL_REQUIRED")
        except (BgeRuntimeError, RagV2Oa112BgeRunnerError):
            return _failure("OA112_LOCAL_BGE_MATERIALIZATION_FAILED")
        except PublicBgeStagingRepositoryError:
            return _failure("PUBLIC_BGE_STAGE_UNAVAILABLE")
        receipt.update(
            {
                "code": "OA112_PUBLIC_BGE_STAGED",
            }
        )
        _emit(receipt)
        return 0
    return _failure("PUBLIC_BGE_COMMAND_INVALID")


def _materialize_exact30() -> Exact30PublicBgeMaterialization:
    """pinned local packet만 load해 one full exact-30 in-memory component를 만든다."""

    packet_root = _bge_packet_root()
    tokenizer = BgeStaticTokenizer.from_file(packet_root / "onnx" / "tokenizer.json")
    embedder = load_bge_onnx_embedder(packet_root)
    return materialize_exact30_public_bge_component(
        tokenizer=tokenizer,
        embedder=embedder,
    )


def _stage_exact30(*, database_dsn: str) -> _Exact30StageResult:
    """writer role의 exact definer function으로 full component만 resumable stage한다."""

    materialization = _materialize_exact30()
    repository = PsycopgRagV2PublicBgeStagingRepository(database_dsn=database_dsn)
    receipts = repository.stage_component(
        records=materialization.records,
        context=materialization.context,
    )
    return _Exact30StageResult(
        materialization=materialization,
        receipts=receipts,
    )


def _download_oa112() -> Oa112DownloadReceipt:
    """one-shot local approval packet이 있을 때만 OA112 downloader를 호출한다."""

    local_root = _local_root()
    registry = _load_oa112_registry(local_root)
    packet = load_oa112_download_packet(
        approved_root=local_root,
        relative_path="oa112-download-packet.v1.json",
    )
    execution_binding = load_oa112_execution_binding(
        approved_root=local_root,
        relative_path="oa112-execution-evidence.v1.json",
        repository_root=_repository_root(),
    )
    return download_oa112_local_cache(
        entries=registry.active_entries,
        registry_digest=registry.registry_digest,
        packet=packet,
        execution_binding=execution_binding,
        local_cache_root=local_root,
        packet_control_root=local_root,
    )


def _materialize_oa112() -> Oa112PublicBgeMaterialization:
    """downloaded local cache와 frozen active registry만 OA112 BGE component에 연결한다."""

    local_root = _local_root()
    registry = _load_oa112_registry(local_root)
    packet_root = _bge_packet_root()
    tokenizer = BgeStaticTokenizer.from_file(packet_root / "onnx" / "tokenizer.json")
    embedder = load_bge_onnx_embedder(packet_root)
    return materialize_oa112_public_bge_component(
        tokenizer=tokenizer,
        embedder=embedder,
        registry=registry,
        local_cache_root=local_root,
    )


def _stage_oa112(*, database_dsn: str) -> _Oa112StageResult:
    """writer-only capability로 complete OA112 component만 stage하고 activation은 하지 않는다."""

    materialization = _materialize_oa112()
    repository = PsycopgRagV2PublicBgeStagingRepository(database_dsn=database_dsn)
    receipts = repository.stage_component(
        records=materialization.records,
        context=materialization.context,
    )
    return _Oa112StageResult(
        materialization=materialization,
        receipts=receipts,
    )


def _evaluate_public_base(*, database_dsn: str) -> tuple[dict[str, object], bool]:
    """One local exact30+OA112 pair만 receipt→writer evaluation 순서로 record한다.

    stage와 activation을 만들지 않으므로 operator는 `exact30-stage`, `oa112-stage`, 이 command,
    `activate-public-base`를 분리해 resume할 수 있다. same plan receipt을 먼저 재사용해 component별
    writer transition 사이 interruption이 있어도 warm p95/digest를 바꾸지 않는다.
    """

    local_root = _local_root()
    registry = _load_oa112_registry(local_root)
    packet_root = _bge_packet_root()
    tokenizer = BgeStaticTokenizer.from_file(packet_root / "onnx" / "tokenizer.json")
    embedder = load_bge_onnx_embedder(packet_root)
    exact30 = materialize_exact30_public_bge_component(
        tokenizer=tokenizer,
        embedder=embedder,
    )
    oa112 = materialize_oa112_public_bge_component(
        tokenizer=tokenizer,
        embedder=embedder,
        registry=registry,
        local_cache_root=local_root,
    )
    exact_queries, exact_fixture_digest = load_exact30_evaluation_queries(
        source_card_corpus_manifest_sha256=exact30.source_card_corpus_manifest_sha256,
    )
    oa112_queries, oa112_manifest_digest = load_oa112_evaluation_queries(
        approved_root=local_root,
        registry=registry,
    )
    plan_digest = evaluation_plan_digest(
        exact30_context=exact30.context,
        oa112_context=oa112.context,
        oa112_registry_digest=registry.registry_digest,
        exact30_fixture_digest=exact_fixture_digest,
        oa112_manifest_digest=oa112_manifest_digest,
    )
    evidence = load_public_bge_pair_evaluation_evidence(
        approved_root=local_root,
        evaluation_plan_digest=plan_digest,
    )
    reused = evidence is not None
    receipt: dict[str, object]
    if evidence is None:
        evaluation = evaluate_public_bge_pair(
            exact30_records=exact30.records,
            exact30_context=exact30.context,
            oa112_records=oa112.records,
            oa112_context=oa112.context,
            oa112_registry_digest=registry.registry_digest,
            exact30_queries=exact_queries,
            exact30_fixture_digest=exact_fixture_digest,
            oa112_queries=oa112_queries,
            oa112_manifest_digest=oa112_manifest_digest,
            query_embedder=LocalBgeQueryEmbedder(embedder),
        )
        if not evaluation.acceptance_passed:
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ACCEPTANCE_FAILED")
        # DB pair evaluation은 component별 transaction이다. 먼저 same-plan receipt을 durable하게
        # 남겨 exact transition 뒤 interruption이 생겨도 다음 resume이 byte-identical evidence를 쓴다.
        write_public_bge_pair_evaluation_receipt(
            approved_root=local_root,
            evaluation=evaluation,
        )
        evidence = evaluation.evidence()
        receipt = evaluation.content_free_receipt()
    else:
        receipt = {
            "embeddingProfileId": "bge_m3_local_1024_v1",
            "exact30GenerationId": exact30.context.component_generation_id,
            "oa112GenerationId": oa112.context.component_generation_id,
        }
    repository = PsycopgRagV2PublicBgeStagingRepository(database_dsn=database_dsn)
    repository.evaluate(context=exact30.context, evidence=evidence)
    repository.evaluate(context=oa112.context, evidence=evidence)
    return receipt, reused


def _bge_packet_root() -> Path:
    """operator env가 없으면 repository의 hash-pinned local packet만 선택한다."""

    value = os.environ.get("CAPSTONE_RAG_BGE_PACKET_ROOT", "").strip()
    if not value:
        return DEFAULT_MODEL_ROOT
    root = Path(value)
    if not root.is_absolute():
        raise BgeRuntimeError("BGE_PACKET_VERIFICATION_FAILED")
    return root


def _local_root() -> Path:
    """local-only registry/packet/cache root는 argv가 아닌 one fixed environment root만 사용한다."""

    value = os.environ.get("CAPSTONE_RAG_LOCAL_ROOT", "").strip()
    if not value:
        raise Oa112DownloadError("OA112_PACKET_UNSAFE")
    root = Path(value)
    if not root.is_absolute():
        raise Oa112DownloadError("OA112_PACKET_UNSAFE")
    return root


def _repository_root() -> Path:
    """OA physical execution은 package snapshot이 아닌 clean tracked checkout을 반드시 확인한다."""

    for candidate in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
        if (candidate / ".git").exists():
            return candidate
    raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE")


def _load_oa112_registry(local_root: Path) -> Oa112ActiveRegistry:
    """local 0700 root의 fixed activation registry leaf만 active input으로 허용한다."""

    return load_oa112_active_registry(
        approved_root=local_root,
        relative_path="oa112-active-registry.v1.json",
    )


def _failure(code: str) -> int:
    _emit({"code": code, "state": "FAILED"})
    return 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
