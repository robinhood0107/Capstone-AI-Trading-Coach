"""fixed local root에서 OA112 first-download bootstrap을 운영하는 command boundary다."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence

from app.rag import oa112_bootstrap
from app.rag.oa112_active_registry import Oa112ActiveRegistryError, load_oa112_active_registry
from app.rag.oa112_bootstrap import Oa112BootstrapError
from app.rag.oa112_downloader import (
    Oa112DownloadError,
    load_oa112_download_packet,
    load_oa112_execution_binding,
)
from app.rag.oa_release_manifest import REPO_ROOT
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file, write_approved_new_file

_LOCAL_ROOT = REPO_ROOT / "capstone-rag/runtime/local-corpus"
_HISTORICAL_CURATION = "oa112-historical-arxiv-curation.v1.json"
_REPLACEMENT_CURATION = "oa112-ccby-replacement-candidates.v1.json"
_CANDIDATE_REGISTRY = "oa112-bootstrap-candidate-registry.v1.json"
_ACTIVE_REGISTRY = "oa112-active-registry.v1.json"
_DOWNLOAD_PACKET = "oa112-bootstrap-download-packet.v1.json"
_EXECUTION_EVIDENCE = "oa112-execution-evidence.v1.json"


def main(argv: Sequence[str] | None = None) -> int:
    """path/credential/owner 인수 없이 fixed local control records만 소비한다.

    `prepare-candidates`는 raw를 내려받지 않는다. `download`는 current clean Git identity와 one-shot
    packet을 다시 묶은 뒤 quarantine만 채우며, `activate`가 모든 112 observed hash를 active registry에
    고정하기 전에는 public corpus가 ready라고 선언하지 않는다.
    """

    parser = argparse.ArgumentParser(description="Bootstrap OA112 local-only corpus activation.")
    parser.add_argument("command", choices=("prepare-candidates", "download", "activate", "status"))
    args = parser.parse_args(tuple(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "prepare-candidates":
            return _prepare_candidates()
        if args.command == "download":
            return _download()
        if args.command == "activate":
            return _activate()
        return _status()
    except (Oa112BootstrapError, Oa112DownloadError, Oa112ActiveRegistryError, RagSafeIoError) as error:
        code = error.code if isinstance(error, Oa112DownloadError) else str(error)
        _emit({"code": code, "state": "FAILED"})
        return 2


def _prepare_candidates() -> int:
    historical = _load_local_json(_HISTORICAL_CURATION)
    replacement = _load_local_json(_REPLACEMENT_CURATION)
    payload = oa112_bootstrap.build_oa112_bootstrap_candidate_registry_from_curation(
        historical_curation=historical,
        replacement_curation=replacement,
    )
    expected = oa112_bootstrap.validate_oa112_bootstrap_candidate_registry(payload)
    target = _LOCAL_ROOT / _CANDIDATE_REGISTRY
    try:
        target.lstat()
    except FileNotFoundError:
        content = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            + b"\n"
        )
        write_approved_new_file(
            approved_root=_LOCAL_ROOT,
            relative_path=_CANDIDATE_REGISTRY,
            content=content,
            max_bytes=2_000_000,
        )
        registry = oa112_bootstrap.load_oa112_bootstrap_candidate_registry(
            approved_root=_LOCAL_ROOT,
            relative_path=_CANDIDATE_REGISTRY,
        )
    else:
        registry = oa112_bootstrap.load_oa112_bootstrap_candidate_registry(
            approved_root=_LOCAL_ROOT,
            relative_path=_CANDIDATE_REGISTRY,
        )
        if registry.registry_digest != expected.registry_digest:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CANDIDATE_REGISTRY_DRIFT")
    _emit(
        {
            "activeSourceCount": registry.active_source_count,
            "code": "OA112_BOOTSTRAP_CANDIDATES_READY",
            "registryDigest": registry.registry_digest,
            "state": "CANDIDATES_READY",
        }
    )
    return 0


def _download() -> int:
    registry = oa112_bootstrap.load_oa112_bootstrap_candidate_registry(
        approved_root=_LOCAL_ROOT,
        relative_path=_CANDIDATE_REGISTRY,
    )
    packet = load_oa112_download_packet(
        approved_root=_LOCAL_ROOT,
        relative_path=_DOWNLOAD_PACKET,
    )
    binding = load_oa112_execution_binding(
        approved_root=_LOCAL_ROOT,
        relative_path=_EXECUTION_EVIDENCE,
        repository_root=REPO_ROOT,
    )
    receipt = oa112_bootstrap.download_oa112_bootstrap_quarantine(
        registry=registry,
        packet=packet,
        execution_binding=binding,
        local_cache_root=_LOCAL_ROOT,
        packet_control_root=_LOCAL_ROOT,
    )
    projection = receipt.content_free_projection()
    projection["code"] = "OA112_BOOTSTRAP_QUARANTINE_READY"
    projection["state"] = "QUARANTINE_READY"
    _emit(projection)
    return 0


def _activate() -> int:
    registry = oa112_bootstrap.load_oa112_bootstrap_candidate_registry(
        approved_root=_LOCAL_ROOT,
        relative_path=_CANDIDATE_REGISTRY,
    )
    active = oa112_bootstrap.activate_oa112_bootstrap_quarantine(
        registry=registry,
        local_cache_root=_LOCAL_ROOT,
        registry_root=_LOCAL_ROOT,
        registry_relative_path=_ACTIVE_REGISTRY,
    )
    _emit(
        {
            "activeSourceCount": active.active_source_count,
            "code": "OA112_BOOTSTRAP_ACTIVE_REGISTRY_READY",
            "registryDigest": active.registry_digest,
            "state": "ACTIVE_REGISTRY_READY",
        }
    )
    return 0


def _status() -> int:
    candidate = oa112_bootstrap.load_oa112_bootstrap_candidate_registry(
        approved_root=_LOCAL_ROOT,
        relative_path=_CANDIDATE_REGISTRY,
    )
    try:
        active = load_oa112_active_registry(
            approved_root=_LOCAL_ROOT,
            relative_path=_ACTIVE_REGISTRY,
        )
    except Oa112ActiveRegistryError:
        active = None
    _emit(
        {
            "activeRegistryPresent": active is not None,
            "candidateRegistryDigest": candidate.registry_digest,
            "candidateSourceCount": candidate.active_source_count,
            "code": "OA112_BOOTSTRAP_STATUS",
            "state": "ACTIVE_READY" if active is not None else "CANDIDATES_READY",
        }
    )
    return 0


def _load_local_json(name: str) -> Mapping[str, object]:
    result = read_approved_regular_file(
        approved_root=_LOCAL_ROOT,
        relative_path=name,
        max_bytes=2_000_000,
    )
    payload = oa112_bootstrap._parse_canonical_json(result.content)
    return payload


def _emit(value: Mapping[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
