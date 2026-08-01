from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPSTONE_RAG_ROOT = Path(__file__).resolve().parent
PYTHON_SERVICE = ROOT / "workspaces/decision-platform/python-services"
if str(PYTHON_SERVICE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE))

from app.rag.s4_5_evaluation import (  # noqa: E402
    S4_5_EVAL_MANIFEST_PATH,
    S4_5_REPORT_PATH,
    build_s4_5_manifest,
    evaluate_s4_5_manifest,
)
from app.rag.provider_control_plane import (  # noqa: E402
    S4_5_PROVIDER_REPORT_PATH,
    build_s4_5_provider_report,
)
from app.rag.safe_io import (  # noqa: E402
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_generated_file,
)


_MAX_GENERATED_ARTIFACT_BYTES = 2 * 1024 * 1024


def _bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _artifact_relative_path(path: Path) -> str:
    """generated S4.5 output은 repo root가 아니라 owned `capstone-rag` subtree에만 쓴다."""

    try:
        return path.relative_to(CAPSTONE_RAG_ROOT).as_posix()
    except ValueError as error:
        raise RagSafeIoError("S4.5 artifact path escapes the approved capstone-rag root.") from error


def _read_artifact(path: Path) -> bytes:
    return read_approved_regular_file(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_path=_artifact_relative_path(path),
        max_bytes=_MAX_GENERATED_ARTIFACT_BYTES,
    ).content


def _write_artifact(path: Path, content: bytes) -> None:
    write_approved_generated_file(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_path=_artifact_relative_path(path),
        content=content,
        max_bytes=_MAX_GENERATED_ARTIFACT_BYTES,
    )


def _artifacts() -> dict[Path, bytes]:
    manifest = build_s4_5_manifest()
    return {
        S4_5_EVAL_MANIFEST_PATH: _bytes(manifest),
        S4_5_REPORT_PATH: _bytes(evaluate_s4_5_manifest(manifest)),
        S4_5_PROVIDER_REPORT_PATH: _bytes(build_s4_5_provider_report()),
    }


def _check() -> list[str]:
    """tracked evaluation bytes가 expected-name 안전 reader와도 정확히 일치하는지 확인한다."""

    drifted: list[str] = []
    for path, expected in _artifacts().items():
        try:
            actual = _read_artifact(path)
        except RagSafeIoError:
            drifted.append(_artifact_relative_path(path))
            continue
        if actual != expected:
            drifted.append(_artifact_relative_path(path))
    return drifted


def _write() -> None:
    """fixture generator output은 approved capstone-rag root 안의 safe regular leaf만 갱신한다."""

    for path, content in _artifacts().items():
        _write_artifact(path, content)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the S4.5 exact-60 fixture evaluation artifacts."
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        drifted = _check()
        if drifted:
            for path in drifted:
                print(f"S4_5_EVALUATION_DRIFT: {path}", file=sys.stderr)
            return 1
        print("S4_5_EXACT_60_FIXTURE_EVALUATION_VERIFIED")
        return 0
    _write()
    print("S4_5_EXACT_60_FIXTURE_EVALUATION_GENERATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
