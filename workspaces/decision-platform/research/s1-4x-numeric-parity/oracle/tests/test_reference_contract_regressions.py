"""Frozen reference의 입력 domain과 재귀 source-tree closure 회귀를 검증한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oracle_common import strict_json_load


def _contract_file(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "contract" / name
    loaded = strict_json_load(path)
    assert isinstance(loaded, dict)
    return loaded


def test_s1_4r_vector_domains_do_not_invent_a_100000_observation_cap() -> None:
    registry = _contract_file("function-registry.v1.json")
    research_entries = [
        entry for entry in registry["entries"] if entry.get("track") == "s1.4r"
    ]
    vector_parameters = [
        parameter
        for entry in research_entries
        for parameter in entry["parameters"]
        if parameter.get("wireValueKind") == "array"
    ]

    assert vector_parameters
    assert all("100000" not in parameter["domain"] for parameter in vector_parameters)


def test_python_reference_source_trees_use_recursive_globs() -> None:
    reference_lock = _contract_file("reference-lock.v1.json")
    python_trees = [
        tree
        for tree in reference_lock["sourceTrees"]
        if tree["role"]
        in {
            "production-reference-tree",
            "research-reference-tree",
            "research-authoritative-test-tree",
        }
    ]

    assert len(python_trees) == 3
    for tree in python_trees:
        python_globs = [pattern for pattern in tree["includeGlobs"] if pattern.endswith(".py")]
        assert python_globs
        assert all("**/*.py" in pattern for pattern in python_globs)
