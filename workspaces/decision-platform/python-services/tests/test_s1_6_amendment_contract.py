from __future__ import annotations

import importlib
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[2]
EXPECTED_TESTCONTAINERS_DEPENDENCY = "testcontainers[postgres]==4.14.2"


def _toml_document(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_postgres_testcontainers_is_exactly_locked_and_importable() -> None:
    """S1.6 PostgreSQL 통합 테스트는 exact dev pin과 frozen lock을 함께 요구한다."""

    project = _toml_document(PROJECT_ROOT / "pyproject.toml")
    dev_dependencies = project.get("dependency-groups", {}).get("dev", [])
    assert EXPECTED_TESTCONTAINERS_DEPENDENCY in dev_dependencies, (
        "add testcontainers[postgres]==4.14.2 to dependency-groups.dev, then run uv lock"
    )

    lock = _toml_document(PROJECT_ROOT / "uv.lock")
    locked_versions = {
        package.get("version")
        for package in lock.get("package", [])
        if package.get("name") == "testcontainers"
    }
    assert locked_versions == {"4.14.2"}, (
        "uv.lock must resolve testcontainers exactly to 4.14.2; run uv lock"
    )

    try:
        installed_version = version("testcontainers")
    except PackageNotFoundError:
        pytest.fail(
            "testcontainers 4.14.2 is not installed; run uv sync --frozen before this test",
            pytrace=False,
        )
    assert installed_version == "4.14.2"

    postgres_module = importlib.import_module("testcontainers.postgres")
    assert getattr(postgres_module, "PostgresContainer", None) is not None


def test_s1_4x_provenance_uses_event_base_and_freezes_numeric_inputs() -> None:
    """PR과 main push 모두 실제 변경 구간에서 frozen 수치 입력의 drift를 거부한다."""

    workflow = (REPO_ROOT / ".github/workflows/s1-4x-contract-correctness.yml").read_text(
        encoding="utf-8"
    )
    required_fragments = (
        "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
        "PUSH_BEFORE_SHA: ${{ github.event.before }}",
        'DIFF_BASE="$PR_BASE_SHA"',
        'DIFF_BASE="$PUSH_BEFORE_SHA"',
        'git merge-base --is-ancestor "$REFERENCE_BASE" HEAD',
        'git diff --exit-code "$DIFF_BASE" HEAD --',
        '"$S1_4X/contract"',
        '":(exclude)$S1_4X/contract/contract-manifest.v1.json"',
        '":(exclude)$S1_4X/contract/reference-lock.v1.json"',
        '"$S1_4X/oracle"',
        '":(exclude)$S1_4X/oracle/tests/test_validate_contract.py"',
        '"$S1_4X/benchmarks"',
        '":(exclude)$S1_4X/benchmarks/benchmark-plan.v1.json"',
        '":(exclude)$S1_4X/benchmarks/benchmark-plan.v1.sha256"',
    )

    for fragment in required_fragments:
        assert fragment in workflow
    assert "git diff --exit-code origin/main" not in workflow

    runbook = (
        REPO_ROOT / "workspaces/decision-platform/research/s1-4x-numeric-parity/README.md"
    ).read_text(encoding="utf-8")
    assert "METHOD-MERGE-COMMIT" in runbook
    assert "squash/rebase" in runbook
    assert "gh pr merge <pr-number> --merge --match-head-commit <head-sha>" in runbook
