from __future__ import annotations

import importlib
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
