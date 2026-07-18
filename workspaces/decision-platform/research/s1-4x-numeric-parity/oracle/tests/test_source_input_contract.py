"""모든 format/lint/compile/profile consumer가 한 source file set만 참조하도록 검증한다."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from oracle_common import strict_json_load


def _validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contract"
        / "schemas"
        / "source-input-manifest.schema.json"
    )
    schema = strict_json_load(schema_path)
    assert isinstance(schema, dict)
    return Draft202012Validator(schema)


def _manifest() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-source-input-manifest-v1",
        "language": "scala",
        "files": {
            "src/main/scala/research/Kernel.scala": {
                "role": "main",
                "sha256": "0" * 64,
            }
        },
        "inputSets": {
            "tracked": "files",
            "manifest": "files",
            "format": "files",
            "compile": "files",
            "lint": "files",
            "profileRun": "files",
        },
        "canonicalManifestSha256": "1" * 64,
    }


def test_all_consumers_reference_the_single_canonical_file_set() -> None:
    validator = _validator()
    instance = _manifest()
    assert list(validator.iter_errors(instance)) == []

    divergent = copy.deepcopy(instance)
    divergent["inputSets"]["lint"] = ["src/main/scala/research/Kernel.scala"]
    assert list(validator.iter_errors(divergent))


def test_file_paths_are_object_keys_and_cannot_repeat_or_escape() -> None:
    validator = _validator()
    escaped = _manifest()
    escaped["files"]["../Outside.scala"] = escaped["files"].pop(
        "src/main/scala/research/Kernel.scala"
    )
    assert list(validator.iter_errors(escaped))


def test_haskell_manifest_is_hs_only_and_rejects_other_compilable_suffixes() -> None:
    validator = _validator()
    instance = _manifest()
    instance["language"] = "haskell"
    instance["files"] = {
        "src/S14X/Core/Kernel.hs": {
            "role": "main",
            "sha256": "0" * 64,
        },
        "package.yaml": {
            "role": "configuration",
            "sha256": "1" * 64,
        },
        "selected-profile.v1.json": {
            "role": "configuration",
            "sha256": "2" * 64,
        },
    }
    assert list(validator.iter_errors(instance)) == []

    for required_configuration in ("selected-profile.v1.json", "package.yaml"):
        missing_configuration = copy.deepcopy(instance)
        missing_configuration["files"].pop(required_configuration)
        assert list(validator.iter_errors(missing_configuration))

    for suffix in (".lhs", ".hsc", ".hs-boot"):
        escaped = copy.deepcopy(instance)
        escaped["files"][f"src/S14X/Core/Escape{suffix}"] = {
            "role": "main",
            "sha256": "2" * 64,
        }
        assert list(validator.iter_errors(escaped)), suffix

    non_hs_source = copy.deepcopy(instance)
    non_hs_source["files"]["src/S14X/Core/Kernel.txt"] = {
        "role": "tool",
        "sha256": "3" * 64,
    }
    assert list(validator.iter_errors(non_hs_source))

    generated_source = copy.deepcopy(instance)
    generated_source["files"]["src/S14X/Core/Generated.chs"] = {
        "role": "configuration",
        "sha256": "4" * 64,
    }
    assert list(validator.iter_errors(generated_source))

    unknown_configuration = copy.deepcopy(instance)
    unknown_configuration["files"]["custom-build.yaml"] = {
        "role": "configuration",
        "sha256": "5" * 64,
    }
    assert list(validator.iter_errors(unknown_configuration))
