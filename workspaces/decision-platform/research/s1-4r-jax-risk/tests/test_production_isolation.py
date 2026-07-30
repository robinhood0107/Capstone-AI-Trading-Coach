from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXECUTION_BOUNDARY = os.environ.get("S1_4R_EXECUTION_BOUNDARY", "wsl2")
# OCI에는 research subtree만 `/app`에 복사되므로 host-only path 계산도 import-safe여야 한다.
REPO_ROOT = (
    PROJECT_ROOT
    if EXECUTION_BOUNDARY == "oci"
    else Path(__file__).resolve().parents[5]
)
PRODUCTION_ROOT = REPO_ROOT / "workspaces/decision-platform/python-services"
PRODUCTION_APP = PRODUCTION_ROOT / "app"
PRODUCTION_PYTHON = Path(
    os.environ.get(
        "S1_4R_PRODUCTION_PYTHON",
        PRODUCTION_ROOT / ".venv/bin/python",
    )
)
HOST_ONLY = pytest.mark.skipif(
    EXECUTION_BOUNDARY == "oci",
    reason="The research-only OCI image intentionally excludes the production checkout and venv",
)

EXPECTED_PRODUCTION_EXPORTS = (
    "simple_returns",
    "log_returns",
    "cumulative_return",
    "cagr",
    "realized_volatility",
    "annualized_volatility",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "historical_var",
    "historical_cvar",
)

FROZEN_PRODUCTION_FILES = {
    "shared-docs/metrics_definitions.md": (
        "139f76ae8284fd8f4eef7d41fd89df5af1e7eb38",
        "38d6a3fc244575a16498eb6b12d69bea473006b8c131b70f486030b79444d359",
    ),
    "workspaces/decision-platform/python-services/tests/financial_engineering/test_returns.py": (
        "c167c4bbe643768559e11ff0d2620af258824d09",
        "8553d03cd6693a1d7805377758b86475251c94fdd2270d78b2b87d9be8dce6aa",
    ),
    (
        "workspaces/decision-platform/python-services/tests/financial_engineering/"
        "test_risk_metrics.py"
    ): (
        "e9e4ad61e9c88ad8ae3a549800eb9a5245a9b753",
        "1697526ec70e9692f97a23998cea6a785fd759f66e50e418f063246391a5668c",
    ),
    "workspaces/decision-platform/python-services/app/financial_engineering/__init__.py": (
        "5d56c88a8c70bec6a22f8287391017340a23aa0b",
        "cf01244085ec46ca67b4b2c5d1d2dc3977c260051f7a815915f54b45ba6cdb31",
    ),
}

FORBIDDEN_PRODUCTION_IMPORTS = {
    "jax",
    "jaxlib",
    "s1_4r_risk_research",
}
FORBIDDEN_PRODUCTION_PACKAGES = {
    "hypothesis",
    "jax",
    "jaxlib",
    "pyperf",
    "s1-4r-risk-research",
}
FROZEN_DIFF_PATHS = (
    "workspaces/decision-platform/python-services",
    "shared-docs/metrics_definitions.md",
    "docs/최종_프로젝트_명세서.md",
    "docs/API_명세서.md",
    "contracts",
    "workspaces/return-engine",
    "workspaces/experience-dashboard",
)
ALLOWED_BRANCH_DIFF_PATHS = (
    "workspaces/decision-platform/research/s1-4r-jax-risk/",
    ".github/workflows/s1-4r-research-correctness.yml",
    ".github/workflows/s1-4r-research-benchmark.yml",
    ".github/workflows/s1-4x-contract-correctness.yml",
)


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def _production_import_roots() -> dict[Path, set[str]]:
    imports: dict[Path, set[str]] = {}
    for path in sorted(PRODUCTION_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                roots.add(node.module.partition(".")[0])
        imports[path] = roots
    return imports


def _normalized_requirement_name(requirement: str) -> str:
    name = re.split(r"[\s\[<>=!~;]", requirement, maxsplit=1)[0]
    return name.lower().replace("_", "-")


def _production_lock_package_names() -> set[str]:
    with (PRODUCTION_ROOT / "uv.lock").open("rb") as file:
        lock = tomllib.load(file)
    return {package["name"].lower() for package in lock["package"]}


def _production_direct_requirement_names() -> set[str]:
    with (PRODUCTION_ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)
    requirements = list(project["project"]["dependencies"])
    for group in project.get("dependency-groups", {}).values():
        requirements.extend(group)
    return {_normalized_requirement_name(requirement) for requirement in requirements}


def _production_all_ast() -> tuple[str, ...]:
    init_path = PRODUCTION_APP / "financial_engineering/__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, tuple)
        assert all(isinstance(name, str) for name in value)
        return value
    raise AssertionError("production app.financial_engineering.__all__ is missing")


def test_research_environment_is_isolated_without_demanding_jax_absence() -> None:
    # 연구 venv/OCI에는 JAX가 있어야 하지만 production `app` package는 설치되면 안 된다.
    assert importlib.util.find_spec("s1_4r_risk_research") is not None
    assert importlib.util.find_spec("jax") is not None
    assert importlib.util.find_spec("jaxlib") is not None
    assert importlib.util.find_spec("app") is None


@HOST_ONLY
def test_four_production_freeze_files_preserve_git_blob_and_sha256() -> None:
    for relative_path, (expected_blob, expected_sha256) in FROZEN_PRODUCTION_FILES.items():
        data = (REPO_ROOT / relative_path).read_bytes()
        assert _git_blob_sha1(data) == expected_blob, relative_path
        assert hashlib.sha256(data).hexdigest() == expected_sha256, relative_path


@HOST_ONLY
def test_production_financial_engineering_import_surface_remains_exact() -> None:
    assert _production_all_ast() == EXPECTED_PRODUCTION_EXPORTS


@HOST_ONLY
def test_production_ast_has_no_research_or_jax_import() -> None:
    imports = _production_import_roots()
    leaked = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(roots & FORBIDDEN_PRODUCTION_IMPORTS)
        for path, roots in imports.items()
        if roots & FORBIDDEN_PRODUCTION_IMPORTS
    }
    assert leaked == {}


@HOST_ONLY
def test_production_metadata_and_lock_have_no_research_dependencies() -> None:
    assert _production_direct_requirement_names().isdisjoint(FORBIDDEN_PRODUCTION_PACKAGES)
    assert _production_lock_package_names().isdisjoint(FORBIDDEN_PRODUCTION_PACKAGES)


@HOST_ONLY
def test_production_venv_cannot_resolve_jax_or_research_package() -> None:
    assert PRODUCTION_PYTHON.is_file(), (
        "production venv is required for runtime isolation evidence; run "
        "`uv sync --frozen` in workspaces/decision-platform/python-services"
    )
    script = """
import importlib.util
import json
from app.financial_engineering import __all__

names = ("jax", "jaxlib", "s1_4r_risk_research")
print(json.dumps({
    "absent": {name: importlib.util.find_spec(name) is None for name in names},
    "exports": list(__all__),
}, allow_nan=False, sort_keys=True))
"""
    completed = subprocess.run(
        [str(PRODUCTION_PYTHON), "-I", "-c", script],
        cwd=PRODUCTION_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(
        completed.stdout,
        parse_constant=lambda token: pytest.fail(f"non-finite JSON token: {token}"),
    )

    assert evidence["absent"] == {
        "jax": True,
        "jaxlib": True,
        "s1_4r_risk_research": True,
    }
    assert tuple(evidence["exports"]) == EXPECTED_PRODUCTION_EXPORTS


@HOST_ONLY
def test_branch_diff_does_not_touch_frozen_or_out_of_scope_paths() -> None:
    diff = subprocess.run(
        ["git", "diff", "--name-status", "origin/main", "--", *FROZEN_DIFF_PATHS],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    untracked_or_modified = subprocess.run(
        [
            "git",
            "status",
            "--short",
            "--untracked-files=all",
            "--",
            *FROZEN_DIFF_PATHS,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert diff.stdout == ""
    assert untracked_or_modified.stdout == ""


@HOST_ONLY
def test_branch_diff_is_confined_to_the_research_project_and_governing_workflows() -> None:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "origin/main"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = [line for line in completed.stdout.splitlines() if line]
    unexpected = [
        path
        for path in changed_paths
        if not any(
            path == allowed or path.startswith(allowed)
            for allowed in ALLOWED_BRANCH_DIFF_PATHS
        )
    ]
    assert unexpected == []
