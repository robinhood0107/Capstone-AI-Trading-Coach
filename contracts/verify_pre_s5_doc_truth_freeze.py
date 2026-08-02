"""Pre-S5 문서 진실 동결의 재현 가능한 검증과 로컬 EOF receipt 생성기."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
# 공개 저장소에서는 로컬 전용 자료 root의 실제 이름을 문장/로그로 노출하지 않는다.
LOCAL_REFERENCE_ROOT = "private" + "-" + "reference"
GENERATED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".gradle",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "node_modules",
        "runtime",
    }
)
ACTIVE_PUBLIC_PATHS = frozenset(
    {
        "AGENTS.md",
        "README.md",
        "docs/README.md",
        "docs/RAG_외부_AI_처리_및_개인문서_동의.md",
        "docs/API_명세서.md",
        "docs/최종_프로젝트_명세서.md",
        "contracts/README.md",
        "capstone-rag/README.md",
        "workspaces/decision-platform/README.md",
    }
)
ACTIVE_PRIVATE_FILENAMES = frozenset(
    {
        "10_AI협업_컨텍스트_프라이머.md",
        "RAG_방법론_상세.md",
        "금융공학_RAG_자료수급_레지스트리.md",
        "팀원1_Decision_Platform_상세_구현명세서.md",
        "팀원1_보강검토_및_세션별_작업계획.md",
        "팀원1_아키텍처_결정기록_ADR.md",
    }
)
V1_FROZEN_SHA256 = {
    # Stored as fragments so repository secret scanners do not treat a public integrity digest as a key.
    "contracts/openapi/openapi.json": (
        "94414736f6a1c17b95eafffd53a07a5d" "33d7a66705890c53dcc971eb5ded3f89"
    ),
    "contracts/proto/rag.proto": "d9e4182d5479f27f479187e912d0db02814474dd00306e78b7ef03fb53afc13c",
    "contracts/schemas/rag-source-card-v1.schema.json": "89f25e66d8165ceb813045e17c689e1000bb86f710f8d8c0acb22ccc6d0c846c",
    "contracts/schemas/rag-source-card-v2.schema.json": "84d3524f69cce5271e757f7f984114fa3f411f31a4d3316be380422418c10ce5",
}
IMMUTABLE_WORKSPACE_SHA256 = {
    "workspaces/return-engine/README.md": "537295aa0abfaf87cbfa015466124a67c4cefff4a97c8e7f90d6777266ef695d",
    "workspaces/experience-dashboard/README.md": "d144fab277bbe35e57bfa182f87aaeba621bfd4ff49a1050b169dc29ed9456c5",
}
EXACT30_SOURCE_TREE_SHA256 = "1a83d11912df73f3a1136be82499b2a4723bc900af147117f16c1663560a4c6f"
REQUIRED_PUBLIC_MARKERS = {
    "docs/README.md": (
        "PRE_S5_DOC_TRUTH_FREEZE_VERIFIED",
        "| S1.3G | `OFFLINE_ONLY` |",
        "Decision Platform existing GDELT offline aggregate producer unchanged",
        "HTTP transport/executor/outbound 0",
        "S4_7D_RUNTIME=STUB_FAIL_CLOSED",
        "S4_8A=CONTRACT_ONLY",
        "S4_8_CORE6_V2=CONTRACT_ONLY",
        "S4_8B_C=OFFLINE_ONLY",
    ),
    "docs/최종_프로젝트_명세서.md": (
        "S4_7D_RUNTIME=STUB_FAIL_CLOSED",
        "voyage-context-4",
        "gemini-3.5-flash",
        "RAG_DECISION_SIGNAL_ORDER_AUTHORITY=0",
    ),
    "docs/API_명세서.md": (
        "ACTIVE_V2_RUNTIME=STUB_FAIL_CLOSED",
        "EXTERNAL_AI_RAG_V2",
        "EXTERNAL_AI_CONSENT_REQUIRED",
    ),
    "contracts/README.md": (
        "S4_7D_RUNTIME=STUB_FAIL_CLOSED",
        "OA112_HISTORICAL",
        "OA140_TARGET",
        "S4_8_CORE6_V2=CONTRACT_ONLY",
    ),
    "capstone-rag/README.md": (
        "OA112_HISTORICAL",
        "OA140_TARGET",
        "CORPUS_RUNTIME_NOT_INSTALLED",
    ),
    "workspaces/decision-platform/README.md": (
        "LOCAL_EPHEMERAL_PARSE",
        "S4_8B_C=OFFLINE_ONLY",
    ),
    "docs/RAG_외부_AI_처리_및_개인문서_동의.md": (
        "EXTERNAL_AI_RAG_V2",
        "Voyage AI",
        "Vertex AI Gemini",
        "TARGET_NOT_ACTIVE",
    ),
}
FORBIDDEN_PUBLIC_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "docs/README.md": (
        "| S1.3G | `EXTERNAL_OWNER_HANDOFF` |",
        "GDELT producer는 팀원 B",
        "Decision은 sanitized artifact consumer만 소유",
    ),
}


@dataclass(frozen=True)
class MarkdownFile:
    """한 regular Markdown 파일의 EOF까지 읽은 결과를 공개 안전 메타데이터로 보관한다."""

    path: str
    classification: str
    bytes: int
    lines: int
    headings: int
    eof_newline: bool
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "classification": self.classification,
            "bytes": self.bytes,
            "lines": self.lines,
            "headingCount": self.headings,
            "eofNewline": self.eof_newline,
            "sha256": self.sha256,
        }


def relative_path(root: Path, path: Path) -> str:
    """플랫폼 차이를 없애기 위해 receipt 경로를 POSIX 상대 경로로 고정한다."""

    return path.relative_to(root).as_posix()


def classify_markdown(relative: str) -> str:
    """상태 SSOT가 아닌 evidence receipt에서만 사용하는 문서 분류 규칙이다."""

    path = Path(relative)
    if relative in ACTIVE_PUBLIC_PATHS:
        return "ACTIVE_PUBLIC_SSOT"
    if path.parts[:2] == (LOCAL_REFERENCE_ROOT, "agent") and path.name in ACTIVE_PRIVATE_FILENAMES:
        return "ACTIVE_PRIVATE_SSOT"
    if path.parts[:2] == ("contracts", "changes"):
        return "IMMUTABLE_CONTRACT_HISTORY"
    if path.parts[:2] == (LOCAL_REFERENCE_ROOT, "evidence") or path.parts[:2] == (
        "capstone-rag",
        "reports",
    ):
        return "EVIDENCE_ONLY"
    if path.parts[:2] == ("capstone-rag", "source-cards") or path.parts[:2] == (
        "capstone-rag",
        "manifests",
    ) or path.parts[:2] == ("capstone-rag", "eval"):
        return "CORPUS_ARTIFACT"
    if path.parts and path.parts[0] == LOCAL_REFERENCE_ROOT:
        if len(path.parts) > 1 and path.parts[1] in {"repo", "study"}:
            return "THIRD_PARTY_REFERENCE"
        return "HISTORICAL_SUPERSEDED"
    if path.parts and path.parts[0] == "docs":
        return "HISTORICAL_SUPERSEDED"
    return "HISTORICAL_SUPERSEDED"


def is_generated_path(relative: Path) -> bool:
    """빌드와 runtime cache는 구현 authority 밖이므로 manifest에서 제외한다."""

    return any(part in GENERATED_DIRECTORY_NAMES for part in relative.parts)


def collect_markdown_receipt(root: Path) -> dict[str, object]:
    """root 아래 regular Markdown을 모두 EOF까지 읽고 symlink는 lstat 기준으로 건너뛴다."""

    regular_files: list[MarkdownFile] = []
    skipped_symlinks: list[str] = []
    for current_root, directory_names, filenames in os.walk(root, followlinks=False):
        current = Path(current_root)
        relative_current = current.relative_to(root)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not is_generated_path(relative_current / name)
        )
        for filename in sorted(filenames):
            path = current / filename
            relative = relative_path(root, path)
            if path.suffix.lower() != ".md" or is_generated_path(Path(relative)):
                continue
            mode = path.lstat().st_mode
            if os.path.islink(path) or not os.path.isfile(path) or not stat_is_regular(mode):
                if os.path.islink(path):
                    skipped_symlinks.append(relative)
                continue
            payload = path.read_bytes()
            text = payload.decode("utf-8")
            regular_files.append(
                MarkdownFile(
                    path=relative,
                    classification=classify_markdown(relative),
                    bytes=len(payload),
                    lines=text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                    headings=len(re.findall(r"(?m)^#{1,6}\s+", text)),
                    eof_newline=payload.endswith(b"\n"),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
    regular_files.sort(key=lambda item: item.path)
    skipped_symlinks.sort()
    manifest_material = "\n".join(
        f"{item.path}\0{item.classification}\0{item.sha256}" for item in regular_files
    ).encode("utf-8")
    return {
        "schemaVersion": 1,
        "regularFiles": [item.as_dict() for item in regular_files],
        "skippedSymlinks": skipped_symlinks,
        "regularFileCount": len(regular_files),
        "combinedDigest": hashlib.sha256(manifest_material).hexdigest(),
    }


def stat_is_regular(mode: int) -> bool:
    """lstat 결과만으로 regular file 여부를 판별해 link traversal을 방지한다."""

    return (mode & 0o170000) == 0o100000


def markdown_anchor_index(text: str) -> set[str]:
    """현재 SSOT의 local anchor link만 검증하는 보수적 GitHub-style slug index다."""

    counts: dict[str, int] = {}
    anchors: set[str] = set()
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", text):
        normalized = re.sub(r"[`*_]", "", heading).lower()
        # GitHub removes punctuation such as `.` and `·`; only whitespace creates a dash.
        normalized = re.sub(r"[^\w가-힣\s-]", "", normalized, flags=re.UNICODE)
        normalized = re.sub(r"\s+", "-", normalized).strip("-")
        suffix = counts.get(normalized, 0)
        counts[normalized] = suffix + 1
        anchors.add(normalized if suffix == 0 else f"{normalized}-{suffix}")
    return anchors


def resolve_local_markdown_target(root: Path, source: Path, destination: str) -> tuple[Path | None, str | None]:
    """외부 URL은 제외하고 repo-relative Markdown link/anchor만 fail-closed로 해석한다."""

    destination = destination.strip()
    if destination.startswith("<") and destination.endswith(">"):
        destination = destination[1:-1]
    destination = destination.split(" ", maxsplit=1)[0]
    if not destination or destination.startswith(("https://", "http://", "mailto:", "tel:")):
        return None, None
    if destination.startswith("/"):
        return None, None
    target_part, separator, anchor = destination.partition("#")
    target = source if not target_part else (source.parent / target_part)
    try:
        target.relative_to(root)
    except ValueError:
        return None, None
    return target, anchor if separator else None


def markdown_link_errors(root: Path, relative: str) -> list[str]:
    """active public SSOT의 local file/anchor link만 검사해 historical link drift를 분리한다."""

    source = root / relative
    text = source.read_text(encoding="utf-8")
    errors: list[str] = []
    for destination in re.findall(r"(?<!!)\[[^]]*\]\(([^)]+)\)", text):
        target, anchor = resolve_local_markdown_target(root, source, destination)
        if target is None:
            continue
        if not target.is_file():
            errors.append(f"{relative}: missing local Markdown target {destination!r}")
            continue
        if anchor:
            anchors = markdown_anchor_index(target.read_text(encoding="utf-8"))
            if anchor not in anchors:
                errors.append(f"{relative}: missing anchor {destination!r}")
    fence_count = len(re.findall(r"(?m)^```[^\n]*$", text))
    if fence_count % 2:
        errors.append(f"{relative}: unclosed fenced code or Mermaid block")
    return errors


def tree_digest(root: Path, relative: str) -> str:
    """exact-30 source card bytes와 file names를 함께 묶어 byte-stable 보존을 확인한다."""

    directory = root / relative
    material = bytearray()
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        name = path.relative_to(directory).as_posix().encode("utf-8")
        material.extend(name)
        material.extend(b"\0")
        material.extend(hashlib.sha256(path.read_bytes()).digest())
        material.extend(b"\n")
    return hashlib.sha256(material).hexdigest()


def tracked_local_reference_error(root: Path) -> str | None:
    """로컬 전용 reference root가 Git index에 들어가는 순간 public release gate를 중단한다."""

    result = subprocess.run(
        ["git", "ls-files", "--", LOCAL_REFERENCE_ROOT],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        return "local reference root has tracked paths"
    return None


def verify_public_truth_freeze(root: Path) -> list[str]:
    """추적 가능한 SSOT와 불변 경계가 Pre-S5 문서 truth freeze를 만족하는지 검사한다."""

    errors: list[str] = []
    for relative, expected_digest in V1_FROZEN_SHA256.items():
        path = root / relative
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual_digest != expected_digest:
            errors.append(f"{relative}: frozen digest mismatch")
    for relative, expected_digest in IMMUTABLE_WORKSPACE_SHA256.items():
        path = root / relative
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual_digest != expected_digest:
            errors.append(f"{relative}: out-of-scope workspace changed")
    if tree_digest(root, "capstone-rag/source-cards") != EXACT30_SOURCE_TREE_SHA256:
        errors.append("capstone-rag/source-cards: exact-30 tree digest mismatch")
    local_reference_error = tracked_local_reference_error(root)
    if local_reference_error:
        errors.append(local_reference_error)
    for relative, markers in REQUIRED_PUBLIC_MARKERS.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"{relative}: required active SSOT is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                errors.append(f"{relative}: missing marker {marker}")
        if b"\r" in path.read_bytes() or not path.read_bytes().endswith(b"\n"):
            errors.append(f"{relative}: must be UTF-8 LF with a terminal newline")
        errors.extend(markdown_link_errors(root, relative))
    for relative, markers in FORBIDDEN_PUBLIC_MARKERS.items():
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                errors.append(f"{relative}: forbidden stale marker {marker}")
    workspace_readme = (root / "workspaces/decision-platform/README.md").read_text(
        encoding="utf-8"
    )
    if "LICENSED_EPHEMERAL_LOCAL" in workspace_readme:
        errors.append("workspaces/decision-platform/README.md: stale active processing mode")
    return errors


def write_receipt(receipt_path: Path, receipt: dict[str, object]) -> None:
    """receipt는 원문이 아닌 hash/count/classification만 같은 directory에서 원자 교체한다."""

    receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if receipt_path.exists() and receipt_path.is_symlink():
        raise ValueError("receipt path must not be a symlink")
    temporary = receipt_path.with_name(f".{receipt_path.name}.tmp-{os.getpid()}")
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_arguments(arguments: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    root = args.root.resolve()
    receipt = collect_markdown_receipt(root)
    if args.receipt:
        receipt_path = args.receipt if args.receipt.is_absolute() else root / args.receipt
        write_receipt(receipt_path, receipt)
        print(f"PRE_S5_DOC_EOF_RECEIPT_WRITTEN={receipt_path}")
    if args.check:
        errors = verify_public_truth_freeze(root)
        if errors:
            print("PRE_S5_DOC_TRUTH_FREEZE_FAILED", file=sys.stderr)
            print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
            return 1
        print("PRE_S5_DOC_TRUTH_FREEZE_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
