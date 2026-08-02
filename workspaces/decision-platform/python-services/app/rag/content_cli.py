from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from app.rag.oa_release_manifest import (
    OaReleaseManifestError,
    load_oa_release_manifest,
)


_IMPORT_COMMANDS: Final = {
    "import-auto",
    "import-cpu",
    "import-intel-gpu",
    "import-nvidia-gpu",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Windows BAT가 호출하는 content command를 stable JSON으로 중계한다.

    setup은 public OA release manifest의 bounded metadata만 검증한다. 원문 download,
    parser/OCR, embedding, DB activation은 별도 runtime 단계이며 private path나 raw hash를
    출력하지 않는다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return _failure("CONTENT_COMMAND_INVALID")
    command = arguments[0]
    if command == "status" and len(arguments) == 1:
        return _status()
    if command == "setup" and len(arguments) == 1:
        return _setup_public_oa_release()
    if command in _IMPORT_COMMANDS and len(arguments) == 2:
        return _failure("CORPUS_RUNTIME_NOT_INSTALLED")
    if command == "remove-document" and len(arguments) == 2:
        return _failure("CORPUS_RUNTIME_NOT_INSTALLED")
    if command == "cache-clean" and len(arguments) == 1:
        return _failure("CORPUS_RUNTIME_NOT_INSTALLED")
    return _failure("CONTENT_COMMAND_INVALID")


def _setup_public_oa_release() -> int:
    try:
        release = load_oa_release_manifest(path=_operator_manifest_path())
    except OaReleaseManifestError:
        return _failure("CONTENT_RELEASE_NOT_INSTALLED")
    _emit(
        {
            "code": "OA_RELEASE_MANIFEST_VERIFIED",
            "progressPercent": 1,
            "publicCorpusVersion": release.public_corpus_version,
            "sourceCount": release.source_count,
            "state": "BUILDING",
        }
    )
    return 0


def _status() -> int:
    try:
        release = load_oa_release_manifest(path=_operator_manifest_path())
    except OaReleaseManifestError:
        _emit(
            {
                "code": "CONTENT_SETUP_REQUIRED",
                "progressPercent": 0,
                "state": "BUILDING",
            }
        )
        return 0
    _emit(
        {
            "code": "OA_RELEASE_MANIFEST_AVAILABLE",
            "progressPercent": 0,
            "publicCorpusVersion": release.public_corpus_version,
            "sourceCount": release.source_count,
            "state": "CORE_READY",
        }
    )
    return 0


def _operator_manifest_path() -> Path | None:
    value = os.environ.get("CAPSTONE_RAG_OA_MANIFEST_PATH")
    if not value:
        return None
    return Path(value)


def _failure(code: str) -> int:
    _emit({"code": code, "state": "FAILED"})
    return 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
