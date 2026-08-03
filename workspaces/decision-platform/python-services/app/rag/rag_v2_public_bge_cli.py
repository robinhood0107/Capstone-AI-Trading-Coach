from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.rag.bge_acquisition import DEFAULT_MODEL_ROOT
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

    이 CLI는 local model materialization과 writer-only staging까지만 수행한다. evaluation evidence,
    admin pointer activation, OA112 download/provider transport와 raw/vector/DSN argv는 의도적으로
    지원하지 않는다.
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
    if arguments == ("oa112-download",):
        try:
            download_receipt = _download_oa112()
        except Oa112ActiveRegistryError:
            return _failure("OA112_ACTIVE_REGISTRY_REQUIRED")
        except Oa112DownloadError:
            return _failure("OA112_LOCAL_CACHE_DOWNLOAD_FAILED")
        _emit(
            {
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
    return download_oa112_local_cache(
        entries=registry.active_entries,
        registry_digest=registry.registry_digest,
        packet=packet,
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
