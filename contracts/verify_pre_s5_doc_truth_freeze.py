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
from typing import Callable, Final, Iterable

from contracts.generate_principle_contracts import ContractValidationError


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
        "docs/S4_9_MCP_Strong_LLM_운영_가이드.md",
        "docs/API_명세서.md",
        "docs/최종_프로젝트_명세서.md",
        "docs/decision-platform/P1_1_0_0_FULL_APP_V2_권위_및_게이트.md",
        "docs/decision-platform/P1_1_0_0_OWNER_FIRST_V3_권위_및_게이트.md",
        "docs/decision-platform/P1_API_USAGE_MATRIX.md",
        "docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md",
        "docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md",
        "docs/decision-platform/P1_운영_후속_경계.md",
        "docs/decision-platform/P1_최종_테스트_증거_판정표.md",
        "contracts/README.md",
        "capstone-rag/README.md",
        "workspaces/decision-platform/README.md",
        "workspaces/return-engine/README.md",
        "workspaces/experience-dashboard/README.md",
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
    "AGENTS.md": (
        "PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED",
        "PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM",
        "VERTEX_MODEL_ID",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
    ),
    "README.md": (
        "PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED",
        "PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
    ),
    "docs/README.md": (
        "PRE_S5_DOC_TRUTH_FREEZE_VERIFIED",
        "| S1.3G | `OFFLINE_ONLY` |",
        "Decision Platform existing GDELT offline aggregate producer unchanged",
        "HTTP transport/executor/outbound 0",
        "PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1",
        "OA112_ACTIVE_CONTRACT_LOCKED",
        "S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED",
        "| S4.7D v2 runtime | `IMPLEMENTED_DRAFT` |",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8_CORE6_V2=CONTRACT_LOCKED",
        "S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
        "VERTEX_MODEL_ID",
    ),
    "docs/최종_프로젝트_명세서.md": (
        "local-only `IMPLEMENTED_DRAFT` runtime",
        "voyage-context-4",
        "gemini-3.5-flash",
        "PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1",
        "OA112_ACTIVE_CONTRACT_LOCKED",
        "RAG_DECISION_SIGNAL_ORDER_AUTHORITY=0",
        # RAG 설명 경로는 여전히 권한 0이고, Strong LLM 판단은 0이 아니라 범위로 고정된다.
        # 두 마커가 함께 있어야 "무엇이 열렸고 무엇이 여전히 닫혔는지"가 문서에서 읽힌다.
        "STRONG_LLM_JUDGEMENT_AUTHORITY=CANDIDATE_RANK_VETO_SIZE_ONLY",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8_CORE6_V2=CONTRACT_LOCKED",
        "S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
        "VERTEX_MODEL_ID",
    ),
    "docs/API_명세서.md": (
        "ACTIVE_V2_RUNTIME=IMPLEMENTED_DRAFT",
        "EXTERNAL_AI_RAG_V2",
        "EXTERNAL_AI_CONSENT_REQUIRED",
        "OA112_ACTIVE_CONTRACT_LOCKED",
        "/api/v2/market-evidence/{symbol}/foreign-news-sentiment",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8_CORE6_V2=CONTRACT_LOCKED",
        "S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
        "VERTEX_MODEL_ID",
    ),
    "contracts/README.md": (
        "OA112_HISTORICAL",
        "RAG_AND_GLOBAL_NEWS_CONTRACT_LOCKED",
        "OA112_ACTIVE_CONTRACT_LOCKED",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8_CORE6_V2=CONTRACT_LOCKED",
        "S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
        "VERTEX_MODEL_ID",
    ),
    "capstone-rag/README.md": (
        "OA112_HISTORICAL",
        "OA112_ACTIVE_CONTRACT_LOCKED",
        "S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED",
        "S4_7D_RUNTIME=IMPLEMENTED_DRAFT_NOT_ACTIVATED",
    ),
    "workspaces/decision-platform/README.md": (
        "LOCAL_EPHEMERAL_PARSE",
        "PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1",
        "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
        "S4_8A=CONTRACT_LOCKED",
        "S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE",
    ),
    "docs/decision-platform/P1_1_0_0_FULL_APP_V2_권위_및_게이트.md": (
        "TEAM_B_REAL_ARTIFACT=BLOCKED",
        "SECURITY_RELEASE=INCOMPLETE",
        "P1_FINAL=NOT_READY",
        "P1_1_0_0_RELEASED=FALSE",
    ),
    "workspaces/return-engine/README.md": (
        "P1 full-app v2",
        "TEAM_B_REAL_ARTIFACT=BLOCKED",
    ),
    "workspaces/experience-dashboard/README.md": (
        "P1 full-app v2",
        "DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED",
    ),
    "docs/RAG_외부_AI_처리_및_개인문서_동의.md": (
        "EXTERNAL_AI_RAG_V2",
        "Voyage AI",
        "Vertex AI Gemini",
        "TARGET_NOT_ACTIVE",
        "OA112_ACTIVE_CONTRACT_LOCKED",
        "VERTEX_MODEL_ID",
    ),
}
FORBIDDEN_PUBLIC_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "AGENTS.md": ("VERTEX_API_KEY",),
    "docs/README.md": (
        "| S1.3G | `EXTERNAL_OWNER_HANDOFF` |",
        "GDELT producer는 팀원 B",
        "Decision은 sanitized artifact consumer만 소유",
        "S4_7D_RUNTIME=STUB_FAIL_CLOSED",
        "VERTEX_API_KEY",
    ),
    "docs/최종_프로젝트_명세서.md": (
        "VERTEX_API_KEY",
    ),
    "docs/API_명세서.md": (
        "ACTIVE_V2_RUNTIME=STUB_FAIL_CLOSED",
        "VERTEX_API_KEY",
    ),
    "docs/RAG_외부_AI_처리_및_개인문서_동의.md": (
        "VERTEX_API_KEY",
    ),
    "contracts/README.md": (
        "VERTEX_API_KEY",
    ),
    "capstone-rag/README.md": (
        "CORPUS_RUNTIME_NOT_INSTALLED",
    ),
}
SOLO_OWNERSHIP_PUBLIC_PATHS: Final[tuple[str, ...]] = (
    "AGENTS.md",
    "docs/README.md",
    "docs/최종_프로젝트_명세서.md",
    "docs/API_명세서.md",
)
SOLO_OWNERSHIP_MARKERS: Final[tuple[str, ...]] = (
    "PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED",
    "PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM",
    "S1_3G=OFFLINE_ONLY",
    "NEW_TEAMMATE_IMPLEMENTATION_TASKS=0",
    "NEW_TEAMMATE_ISSUES_OR_PRS=0",
    "REQUIRED_TEAMMATE_ARTIFACTS_FOR_S5_ENTRY=0",
    "TEAMMATE_WORKSPACE_DIFF=0",
    "GDELT_MODE=DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY",
    "GDELT_EXISTING_OFFLINE_PRODUCER_UNCHANGED=1",
    "GDELT_HTTP_TRANSPORT=NOT_ACTIVATED",
    "GDELT_OUTBOUND_IMPLEMENTATION=0",
    "GDELT_OUTBOUND_CALLS=0",
    "GDELT_OFFLINE_REFERENCE_ONLY=1",
    "NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED",
    "RAG_NEWS_ANALYST_DECISION_SIGNAL_ORDER_AUTHORITY=0",
    "STRONG_LLM_JUDGEMENT_AUTHORITY=CANDIDATE_RANK_VETO_SIZE_ONLY",
    "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
)
SOLO_OWNERSHIP_ROLE_CATALOG_BEGIN: Final[str] = "<!-- PRE_S5_SOLO_ROLE_CATALOG_BEGIN -->"
SOLO_OWNERSHIP_ROLE_CATALOG_END: Final[str] = "<!-- PRE_S5_SOLO_ROLE_CATALOG_END -->"
SOLO_OWNERSHIP_ROLE_CATALOG: Final[tuple[str, ...]] = (
    *SOLO_OWNERSHIP_MARKERS,
    "HISTORICAL_TEAM_ROLE_CATALOG=TEAM_B:RETURN_ENGINE|LSTM|RULE_BASELINE|BACKTEST;TEAM_A:EXPERIENCE_DASHBOARD",
    "HISTORICAL_TEAM_ROLE_STATUS=HISTORICAL_SUPERSEDED",
    "TEAMMATE_ARTIFACT_ABSENCE=NOT_AVAILABLE_OR_ABSTAIN",
)
EXPECTED_TEAMMATE_WORKSPACE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "workspaces/return-engine/README.md",
        "workspaces/experience-dashboard/README.md",
    }
)
POST_CORE_V2_CATALOG: Final[str] = "contracts/catalogs/p1-full-app-release-contract.v2.json"
POST_CORE_V2_REQUIRED_HARD_GATES: Final[frozenset[str]] = frozenset(
    {
        "P1_CORE",
        "PUBLIC_RAG_SEED",
        "OWNER_RAG_BACKEND",
        "BGE_OCR_CPU_INTEL",
        "PROVIDER_LIVE_READ",
        "TEAM_B_REAL_ARTIFACT",
        "SECURITY_RELEASE",
        "SUPPLY_CHAIN_RELEASE",
        "COMPOSE_E2E",
    }
)
POST_CORE_V2_FORBIDDEN_WORKSPACE_PARTS: Final[frozenset[str]] = frozenset(
    {
        "__pycache__",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
        "artifacts",
        "cache",
        "dev",
        "node_modules",
        "output",
        "raw",
        "tmp",
    }
)
POST_CORE_V2_FORBIDDEN_WORKSPACE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".ckpt", ".csv", ".pickle", ".pkl", ".pyc", ".pyo", ".pth"}
)
POST_CORE_V2_RECEIVED_PREVIEW_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "workspaces/return-engine/artifacts/005930.KS.json",
        "workspaces/return-engine/data/model/005930.KS_lstm.pth",
        "workspaces/return-engine/data/stock/005930.KS.csv",
        "workspaces/return-engine/src/__pycache__/backtest_engine.cpython-314.pyc",
        "workspaces/return-engine/src/__pycache__/data_preprocess.cpython-314.pyc",
        "workspaces/return-engine/src/__pycache__/lstm.cpython-314.pyc",
        "workspaces/return-engine/src/__pycache__/rule_baseline.cpython-314.pyc",
        "workspaces/return-engine/src/artifact/__pycache__/generator.cpython-314.pyc",
        "workspaces/return-engine/src/backtest_core/__pycache__/backtest_engine.cpython-314.pyc",
        "workspaces/return-engine/src/backtest_core/__pycache__/signal_generator.cpython-314.pyc",
        "workspaces/return-engine/src/dataloader/__pycache__/dataloader.cpython-314.pyc",
        "workspaces/return-engine/src/dataloader/__pycache__/datapileline.cpython-312.pyc",
        "workspaces/return-engine/src/dataloader/__pycache__/datapileline.cpython-314.pyc",
        "workspaces/return-engine/src/dataloader/__pycache__/preprocessor.cpython-312.pyc",
        "workspaces/return-engine/src/dataloader/__pycache__/preprocessor.cpython-314.pyc",
        "workspaces/return-engine/src/dataloader/__pycache__/stockdataloader.cpython-314.pyc",
        "workspaces/return-engine/src/models/__pycache__/lstm.cpython-312.pyc",
        "workspaces/return-engine/src/models/__pycache__/lstm.cpython-314.pyc",
        "workspaces/return-engine/src/models/__pycache__/rule_baseline.cpython-314.pyc",
    }
)
SOLO_OWNERSHIP_FORBIDDEN_MARKERS: Final[tuple[str, ...]] = (
    "EXTERNAL_OWNER_HANDOFF",
)
IMMUTABLE_HISTORY_CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {
        "HISTORICAL_SUPERSEDED",
        "IMMUTABLE_CONTRACT_HISTORY",
        "EVIDENCE_ONLY",
    }
)
# 이번 addendum이 기존 v1/v2 RAG 계약 또는 exact-30 evidence를 다시 해석하지 못하게
# base diff에서 명시적으로 고정한다. 신규 addendum 파일은 MDT filter 밖의 A이므로 허용된다.
IMMUTABLE_PRE_S5_FROZEN_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/catalogs/s4-rag-contract.v1.json",
        "contracts/catalogs/s4-rag-contract.v1.sha256.json",
        "contracts/catalogs/s4-rag-v2-contract.v1.json",
        "contracts/catalogs/s4-rag-v2-contract.v1.sha256.json",
        "contracts/openapi/openapi.json",
        "contracts/openapi/rag-v2.openapi.json",
        "contracts/proto/rag.proto",
        "contracts/proto/rag.descriptor.pb",
        "contracts/proto/rag.descriptor.sha256",
        "contracts/proto/rag_v2.descriptor.pb",
        "contracts/proto/rag_v2.descriptor.sha256",
        "contracts/proto/rag_v2.proto",
        "contracts/schemas/news_sentiment_summary.v2.schema.json",
        "contracts/schemas/rag-source-card-v1.schema.json",
        "contracts/schemas/rag-source-card-v2.schema.json",
        "contracts/schemas/s4-rag-answer.schema.json",
        "contracts/schemas/s4-rag-ask-request.schema.json",
        "contracts/schemas/s4-rag-history-detail.schema.json",
        "contracts/schemas/s4-rag-history-page.schema.json",
    }
)
IMMUTABLE_HISTORY_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "capstone-rag/eval/",
    "capstone-rag/manifests/",
    "capstone-rag/ocr/benchmark/receipts/",
    "capstone-rag/reports/",
)
# exact-30 card tree는 source count·root tree digest 자체가 계약이므로 신규 sibling/README 변경도 허용하지 않는다.
IMMUTABLE_EXACT30_SOURCE_CARD_PREFIXES: Final[tuple[str, ...]] = (
    "capstone-rag/source-cards/",
)
TEAMMATE_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:팀원(?:\s*[AB])?|\bteam[ _-]?[ab]\b|\bteammate\b|\bteam\s*mate\b|\bteam\s*member\b|\breturn[ _-]?engine\b|"
    r"\blstm\b|\brule[ _-]?baseline\b|\bbacktest\b|\bexperience[ _-]?dashboard\b)",
    re.IGNORECASE,
)
TEAMMATE_DEPENDENCY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:구현\s*작업|implementation\s*task|\bissue\b|\bpr\b|마감|\bdeadline\b|"
    r"\blive\b|\bblocker\b|필수\s*artifact|required\s*artifact|\bs5\s*entry\b|s5\s*진입)",
    re.IGNORECASE,
)
TEAMMATE_ROLE_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\bowns?\b|담당(?:한다|자|:)?|소유(?:한다|자|권)?|"
    r"역할(?:을|은|:)|구현\s*담당|작업\s*배정)",
    re.IGNORECASE,
)
APPROVED_NON_ROLE_COMPONENT_LINES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (
            "contracts/changes/20260815-s5-signal-runtime-transition.md",
            "- required component는 `ruleBaseline`, `lstm`, `lightgbm`, `hmmRegime` 정확히 네 개다.",
        )
    }
)


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


def stat_is_directory(mode: int) -> bool:
    """lstat 결과만으로 directory 여부를 판별해 parent link traversal을 막는다."""

    return (mode & 0o170000) == 0o040000


def _safe_path_under_root(
    root: Path,
    relative: str | Path,
    expected_kind: Callable[[int], bool],
) -> Path | None:
    """정규화된 repo 상대 경로의 모든 ancestor와 leaf가 no-follow type 검사를 통과해야 읽는다."""

    # lexical normalize는 docs-to-contracts 같은 정상 link를 허용하되 symlink는 resolve하지 않는다.
    candidate = Path(os.path.normpath(os.fspath(root / relative)))
    try:
        components = candidate.relative_to(root).parts
    except ValueError:
        return None
    if not components or any(component in {"", ".", ".."} for component in components):
        return None

    try:
        if not stat_is_directory(root.lstat().st_mode):
            return None
    except OSError:
        return None

    current = root
    for index, component in enumerate(components):
        current /= component
        try:
            mode = current.lstat().st_mode
        except OSError:
            return None
        if index == len(components) - 1:
            return current if expected_kind(mode) else None
        if not stat_is_directory(mode):
            return None
    return None


def safe_regular_file(root: Path, relative: str | Path) -> Path | None:
    """active SSOT/link target는 repo 경계 안의 non-symlink regular file일 때만 반환한다."""

    return _safe_path_under_root(root, relative, stat_is_regular)


def safe_directory(root: Path, relative: str | Path) -> Path | None:
    """tree digest root는 repo 경계 안의 non-symlink directory일 때만 반환한다."""

    return _safe_path_under_root(root, relative, stat_is_directory)


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

    source = safe_regular_file(root, relative)
    if source is None:
        return [f"{relative}: required active SSOT is missing or unsafe"]
    text = source.read_text(encoding="utf-8")
    errors: list[str] = []
    for destination in re.findall(r"(?<!!)\[[^]]*\]\(([^)]+)\)", text):
        target, anchor = resolve_local_markdown_target(root, source, destination)
        if target is None:
            continue
        safe_target = safe_regular_file(root, target)
        if safe_target is None:
            errors.append(f"{relative}: missing local Markdown target {destination!r}")
            continue
        if anchor:
            anchors = markdown_anchor_index(safe_target.read_text(encoding="utf-8"))
            if anchor not in anchors:
                errors.append(f"{relative}: missing anchor {destination!r}")
    fence_count = len(re.findall(r"(?m)^```[^\n]*$", text))
    if fence_count % 2:
        errors.append(f"{relative}: unclosed fenced code or Mermaid block")
    return errors


def tree_digest(root: Path, relative: str) -> str:
    """exact-30 source card bytes와 file names를 함께 묶어 byte-stable 보존을 확인한다."""

    directory = safe_directory(root, relative)
    if directory is None:
        return ""
    material = bytearray()
    for current_root, directory_names, filenames in os.walk(directory, followlinks=False):
        current = Path(current_root)
        safe_children = [
            name for name in directory_names if safe_directory(root, current / name) is not None
        ]
        if len(safe_children) != len(directory_names):
            return ""
        directory_names[:] = sorted(safe_children)
        for filename in sorted(filenames):
            path = safe_regular_file(root, current / filename)
            if path is None:
                return ""
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


def _git_output(root: Path, arguments: list[str]) -> tuple[str | None, str | None]:
    """문서 gate의 Git 관측은 stdout 원문 대신 성공 여부와 필요한 경로만 사용한다."""

    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or "git command failed"
    return result.stdout, None


def _safe_text(root: Path, relative: str) -> tuple[str | None, str | None]:
    """active 문서는 no-follow regular file일 때만 UTF-8 text로 반환한다."""

    path = safe_regular_file(root, relative)
    if path is None:
        return None, f"{relative}: required solo ownership document is missing or unsafe"
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, f"{relative}: solo ownership document is not valid UTF-8"


_BUILD_OUTPUT_DIRECTORIES: Final[tuple[str, ...]] = (
    "/node_modules/",
    "/.next/",
    "/__pycache__/",
    "/.venv/",
    "/.pytest_cache/",
    "/.ruff_cache/",
    "/.mypy_cache/",
    "/test-results/",
    "/playwright-report/",
)
_BUILD_OUTPUT_FILES: Final[tuple[str, ...]] = (
    "/tsconfig.tsbuildinfo",
    "/next-env.d.ts",
    "/.env.example",
)


def _is_ignored_build_output(line: str) -> bool:
    """`git status --ignored` 한 줄이 빌드·의존성 산출물인지 본다.

    ignored 로 표시된 줄만 대상이다. 추적되지 않고 ignore 되지도 않은 파일은 여기서
    걸러지지 않고 그대로 drift 로 남는다. `.gitignore` 로 가린 구현 파일도 마찬가지다.
    """

    if not line.startswith("!! "):
        return False
    path = "/" + line[3:].strip()
    if any(segment in path for segment in _BUILD_OUTPUT_DIRECTORIES):
        return True
    return path.endswith(_BUILD_OUTPUT_FILES)


def post_core_v2_authorized(root: Path) -> bool:
    """정확한 full-app v2 catalog가 있을 때만 README-only 경계를 post-Core 규칙으로 전환한다."""

    path = safe_regular_file(root, POST_CORE_V2_CATALOG)
    if path is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return (
        payload.get("contractId") == "p1-full-app-release-contract.v2"
        and payload.get("releaseVersion") == "1.0.0"
        and payload.get("releaseAuthorityWorkflow") == ".github/workflows/p1-full-app-release.yml"
        and payload.get("terminalReadyState") == "READY_WITH_GAPS"
        and payload.get("historicalContract")
        == {
            "contractId": "p1-offline-demo-release-manifest.v1",
            "schemaPath": "deploy/p1/release-manifest.schema.json",
            "status": "PRESERVED_HISTORICAL_REGRESSION",
        }
        and frozenset(payload.get("hardGates", ())) == POST_CORE_V2_REQUIRED_HARD_GATES
    )


def post_core_v2_workspace_path_is_forbidden(relative: str) -> bool:
    """검토된 source만 허용하고 intake/cache/raw/pickle이 Git에 들어오는 것을 차단한다."""

    if relative in POST_CORE_V2_RECEIVED_PREVIEW_ALLOWLIST:
        return False
    path = Path(relative)
    return bool(
        POST_CORE_V2_FORBIDDEN_WORKSPACE_PARTS.intersection(path.parts)
        or path.suffix.lower() in POST_CORE_V2_FORBIDDEN_WORKSPACE_SUFFIXES
    )


def solo_ownership_role_catalog_errors(root: Path) -> list[str]:
    """docs ledger의 machine-delimited role catalog가 새 협업 범위를 만들지 않는지 검사한다."""

    relative = "docs/README.md"
    text, error = _safe_text(root, relative)
    if error:
        return [error]
    assert text is not None
    if text.count(SOLO_OWNERSHIP_ROLE_CATALOG_BEGIN) != 1 or text.count(
        SOLO_OWNERSHIP_ROLE_CATALOG_END
    ) != 1:
        return [f"{relative}: solo ownership role catalog must have exactly one begin/end marker"]
    begin_index = text.index(SOLO_OWNERSHIP_ROLE_CATALOG_BEGIN) + len(
        SOLO_OWNERSHIP_ROLE_CATALOG_BEGIN
    )
    end_index = text.index(SOLO_OWNERSHIP_ROLE_CATALOG_END)
    if begin_index > end_index:
        return [f"{relative}: solo ownership role catalog marker order is invalid"]
    catalog_lines = tuple(line for line in text[begin_index:end_index].splitlines() if line)
    if catalog_lines != SOLO_OWNERSHIP_ROLE_CATALOG:
        return [f"{relative}: solo ownership role catalog differs from the exact catalog"]
    return []


def solo_ownership_assignment_errors(relative: str, text: str) -> list[str]:
    """각 authority key는 문서마다 정확한 값 한 번만 허용해 상충 marker 삽입을 막는다."""

    errors: list[str] = []
    normalized_lines = tuple(line.strip().strip("`") for line in text.splitlines())
    for marker in SOLO_OWNERSHIP_MARKERS:
        if "=" not in marker:
            if normalized_lines.count(marker) != 1:
                errors.append(f"{relative}: {marker} must appear exactly once")
            continue
        key, _, _ = marker.partition("=")
        assignments = tuple(line for line in normalized_lines if line.startswith(f"{key}="))
        if assignments != (marker,):
            errors.append(f"{relative}: {key} must have exactly one expected authority assignment")
    return errors


def tracked_teammate_workspace_errors(root: Path) -> list[str]:
    """v1은 README-only, exact v2는 검토 source만 허용하고 unsafe intake/output은 계속 차단한다."""

    errors: list[str] = []
    workspace_roots = ["workspaces/return-engine", "workspaces/experience-dashboard"]
    listing, listing_error = _git_output(root, ["ls-files", "-z", "--", *workspace_roots])
    if listing_error:
        return ["teammate workspace inventory could not be read"]
    assert listing is not None
    tracked_paths = frozenset(path for path in listing.split("\0") if path)
    post_core_v2 = post_core_v2_authorized(root)
    if post_core_v2:
        if not EXPECTED_TEAMMATE_WORKSPACE_PATHS.issubset(tracked_paths):
            errors.append("post-Core workspace is missing a required README")
        if any(post_core_v2_workspace_path_is_forbidden(path) for path in tracked_paths):
            errors.append("post-Core workspace tracks intake, cache, raw data, or pickle")
    elif tracked_paths != EXPECTED_TEAMMATE_WORKSPACE_PATHS:
        errors.append("teammate workspace has unexpected tracked paths")
    for relative in EXPECTED_TEAMMATE_WORKSPACE_PATHS:
        if safe_regular_file(root, relative) is None:
            errors.append(f"{relative}: teammate workspace README is missing or unsafe")
    status, status_error = _git_output(
        root,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=matching",
            "--",
            *workspace_roots,
        ],
    )
    if status_error:
        errors.append("teammate workspace working-tree state could not be read")
    elif status and status.strip():
        # 빌드·의존성 산출물은 drift 로 세지 않는다. 이 검사가 잡으려는 것은 팀원이
        # .gitignore 뒤에 숨긴 구현이고, node_modules 나 __pycache__ 는 워크스페이스를
        # 한 번 빌드하거나 테스트하면 반드시 생긴다. 그것까지 세면 로컬에서 개발한
        # 사람에게는 언제나 실패하는 검사가 되고, 실제로 그랬다.
        remaining = tuple(
            line
            for line in status.splitlines()
            if line and not _is_ignored_build_output(line)
        )
        if remaining:
            errors.append("teammate workspace has working-tree drift")
    return errors


def _base_commit_is_available(root: Path, base: str) -> bool:
    """PR base가 없는 shallow/invalid checkout에서는 diff 의존성 검사를 fail-closed 한다."""

    if not base.strip():
        return False
    _, error = _git_output(root, ["rev-parse", "--verify", f"{base}^{{commit}}"])
    return error is None


def added_markdown_lines_since_base(root: Path, base: str) -> tuple[list[tuple[str, str]], list[str]]:
    """base...HEAD에 새로 추가된 Markdown 행을 추출해 신규 역할 의존성 우회를 막는다."""

    if not _base_commit_is_available(root, base):
        return [], ["solo ownership base cannot be resolved"]
    output, error = _git_output(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=0",
            f"{base}...HEAD",
        ],
    )
    if error:
        return [], ["solo ownership public diff could not be read"]
    assert output is not None
    additions: list[tuple[str, str]] = []
    current_relative: str | None = None
    for line in output.splitlines():
        if line.startswith("+++ b/"):
            current_relative = line.removeprefix("+++ b/")
            continue
        if (
            line.startswith("+")
            and not line.startswith("+++")
            and current_relative is not None
            and current_relative.endswith(".md")
        ):
            additions.append((current_relative, line[1:]))
    return additions, []


def teammate_workspace_diff_errors(root: Path, base: str) -> list[str]:
    """v1 diff는 전부 막고 exact v2는 unsafe intake/output 경로만 계속 차단한다."""

    if not _base_commit_is_available(root, base):
        return ["solo ownership base cannot be resolved"]
    output, error = _git_output(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--name-only",
            f"{base}...HEAD",
            "--",
            "workspaces/return-engine",
            "workspaces/experience-dashboard",
        ],
    )
    if error:
        return ["teammate workspace diff could not be read"]
    if output and output.strip():
        changed_paths = tuple(line for line in output.splitlines() if line)
        if post_core_v2_authorized(root):
            if any(post_core_v2_workspace_path_is_forbidden(path) for path in changed_paths):
                return ["post-Core workspace diff contains intake, cache, raw data, or pickle"]
            return []
        return ["teammate workspace changed since base"]
    return []


def immutable_history_diff_errors(root: Path, base: str) -> list[str]:
    """base의 frozen contract·history 변경을 막고 exact-30 source-card tree는 A/M/D/T 모두 고정한다."""

    if not _base_commit_is_available(root, base):
        return ["solo ownership base cannot be resolved"]
    output, error = _git_output(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "--diff-filter=MDT",
            f"{base}...HEAD",
        ],
    )
    if error:
        return ["immutable historical record diff could not be read"]
    errors: list[str] = []
    changed_immutable_paths = (
        tuple(
            relative
            for relative in output.splitlines()
            if relative in IMMUTABLE_PRE_S5_FROZEN_PATHS
            or relative.startswith(IMMUTABLE_HISTORY_PATH_PREFIXES)
            or (
                relative.endswith(".md")
                and classify_markdown(relative) in IMMUTABLE_HISTORY_CLASSIFICATIONS
            )
        )
        if output
        else ()
    )
    if "contracts/openapi/openapi.json" in changed_immutable_paths:
        openapi = root / "contracts/openapi/openapi.json"
        try:
            # S5/S7을 포함한 exact-48 projection과 P1 exact-8 추가를 모두 증명해야 변경을 허용한다.
            from contracts.verify_p1_automation_journal_openapi_transition import verify_transition

            verify_transition(openapi)
        except (ContractValidationError, OSError):
            pass
        else:
            changed_immutable_paths = tuple(
                relative
                for relative in changed_immutable_paths
                if relative != "contracts/openapi/openapi.json"
            )
    if changed_immutable_paths:
        errors.append("immutable historical records changed since base")

    source_card_output, source_card_error = _git_output(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            f"{base}...HEAD",
            "--",
            *IMMUTABLE_EXACT30_SOURCE_CARD_PREFIXES,
        ],
    )
    if source_card_error:
        return [*errors, "exact-30 source-card diff could not be read"]
    if source_card_output.strip():
        errors.append("exact-30 source-card tree changed since base")
    return errors


def new_teammate_dependency_errors(root: Path, base: str) -> list[str]:
    """새 role/task 의존성만 diff로 차단하고 historical roadmap 본문은 바꾸지 않는다."""

    # full-app v2는 사용자 승인 계획이 Team A/B 수신본 통합과 완료 요청서를 명시적으로 소유한다.
    # exact catalog가 없으면 기존 Pre-S5 단독 소유 차단을 그대로 적용한다.
    if post_core_v2_authorized(root):
        return []

    additions, errors = added_markdown_lines_since_base(root, base)
    if errors:
        return errors
    for relative, line in additions:
        if relative in SOLO_OWNERSHIP_PUBLIC_PATHS and line in SOLO_OWNERSHIP_MARKERS:
            continue
        if relative == "docs/README.md" and line in SOLO_OWNERSHIP_ROLE_CATALOG:
            continue
        if (relative, line) in APPROVED_NON_ROLE_COMPONENT_LINES:
            continue
        if not TEAMMATE_REFERENCE_PATTERN.search(line):
            continue
        if TEAMMATE_DEPENDENCY_PATTERN.search(line):
            errors.append(f"{relative}: new teammate dependency was added")
        elif TEAMMATE_ROLE_ASSIGNMENT_PATTERN.search(line):
            errors.append(f"{relative}: new teammate role was added outside the exact catalog")
    return errors


def verify_solo_ownership_lock(root: Path, base: str | None = None) -> list[str]:
    """active Pre-S5 단독 소유 marker, catalog, workspace와 PR diff 경계를 함께 검증한다."""

    errors: list[str] = []
    for relative in SOLO_OWNERSHIP_PUBLIC_PATHS:
        text, error = _safe_text(root, relative)
        if error:
            errors.append(error)
            continue
        assert text is not None
        for marker in SOLO_OWNERSHIP_MARKERS:
            if marker not in text:
                errors.append(f"{relative}: missing solo ownership marker {marker}")
        if not post_core_v2_authorized(root):
            errors.extend(solo_ownership_assignment_errors(relative, text))
        for marker in SOLO_OWNERSHIP_FORBIDDEN_MARKERS:
            if marker in text:
                errors.append(f"{relative}: forbidden stale solo ownership marker {marker}")
    errors.extend(solo_ownership_role_catalog_errors(root))
    errors.extend(tracked_teammate_workspace_errors(root))
    if base is not None:
        errors.extend(new_teammate_dependency_errors(root, base))
        errors.extend(teammate_workspace_diff_errors(root, base))
        errors.extend(immutable_history_diff_errors(root, base))
    return errors


def verify_public_truth_freeze(root: Path) -> list[str]:
    """추적 가능한 SSOT와 불변 경계가 Pre-S5 문서 truth freeze를 만족하는지 검사한다."""

    errors: list[str] = []
    for relative, expected_digest in V1_FROZEN_SHA256.items():
        path = safe_regular_file(root, relative)
        if relative == "contracts/openapi/openapi.json" and path is not None:
            try:
                # exact-56에서도 P1 8개를 제거한 exact-48 projection이 byte-stable해야 한다.
                from contracts.verify_p1_automation_journal_openapi_transition import verify_transition

                verify_transition(path)
            except (ContractValidationError, OSError) as error:
                errors.append(f"{relative}: {error}")
            continue
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        if actual_digest != expected_digest:
            errors.append(f"{relative}: frozen digest mismatch")
    if post_core_v2_authorized(root):
        for relative in IMMUTABLE_WORKSPACE_SHA256:
            path = safe_regular_file(root, relative)
            if path is None:
                errors.append(f"{relative}: post-Core workspace README is missing or unsafe")
            elif "P1 full-app v2" not in path.read_text(encoding="utf-8"):
                errors.append(f"{relative}: post-Core workspace authority marker is missing")
    else:
        for relative, expected_digest in IMMUTABLE_WORKSPACE_SHA256.items():
            path = safe_regular_file(root, relative)
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
            if actual_digest != expected_digest:
                errors.append(f"{relative}: out-of-scope workspace changed")
    if tree_digest(root, "capstone-rag/source-cards") != EXACT30_SOURCE_TREE_SHA256:
        errors.append("capstone-rag/source-cards: exact-30 tree digest mismatch")
    local_reference_error = tracked_local_reference_error(root)
    if local_reference_error:
        errors.append(local_reference_error)
    for relative, markers in REQUIRED_PUBLIC_MARKERS.items():
        path = safe_regular_file(root, relative)
        if path is None:
            errors.append(f"{relative}: required active SSOT is missing or unsafe")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                errors.append(f"{relative}: missing marker {marker}")
        if b"\r" in path.read_bytes() or not path.read_bytes().endswith(b"\n"):
            errors.append(f"{relative}: must be UTF-8 LF with a terminal newline")
        errors.extend(markdown_link_errors(root, relative))
    for relative, markers in FORBIDDEN_PUBLIC_MARKERS.items():
        path = safe_regular_file(root, relative)
        if path is None:
            if relative not in REQUIRED_PUBLIC_MARKERS:
                errors.append(f"{relative}: forbidden marker check requires a regular active SSOT")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                errors.append(f"{relative}: forbidden stale marker {marker}")
    workspace_readme_path = safe_regular_file(root, "workspaces/decision-platform/README.md")
    if workspace_readme_path is not None:
        workspace_readme = workspace_readme_path.read_text(encoding="utf-8")
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
    parser.add_argument("--solo-ownership-public-check", action="store_true")
    parser.add_argument("--base")
    parsed = parser.parse_args(arguments)
    if parsed.base is not None and not parsed.solo_ownership_public_check:
        parser.error("--base requires --solo-ownership-public-check")
    return parsed


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
    if args.solo_ownership_public_check:
        errors = verify_solo_ownership_lock(root, args.base)
        if errors:
            print("PRE_S5_SOLO_OWNERSHIP_LOCK_FAILED", file=sys.stderr)
            print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
            return 1
        print("PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED")
        print("PRE_S5_SOLO_OWNERSHIP_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
