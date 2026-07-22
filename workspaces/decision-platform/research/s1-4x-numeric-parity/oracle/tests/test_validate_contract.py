from __future__ import annotations

import math
import shutil
import struct
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

import validate_contract as validator_module
from oracle_common import (
    OracleContractError,
    atomic_write_json,
    canonical_file_manifest,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    strict_json_load,
)
from validate_contract import (
    CONTRACT_MANIFEST_VERSION,
    materialize_contract_manifest,
    validate_binary_manifests,
    validate_contract_manifest,
    validate_json_schemas,
    validate_negative_fixtures,
    validate_property_plan,
    validate_reference_lock,
    validate_registries,
    validate_request_fixtures,
    validate_sha256_sidecars,
    validate_workflow_path_coverage,
    write_contract_manifest,
)

_VECTOR_SOURCE_SHA256 = "28f203c786cbf8ac6dc3fea3378ec36f34173d505fb4a1dd60fc8418ad91c423"
_VECTOR_PROVENANCE = (
    "official Hackage vector-0.13.2.0 archive bytes; "
    "Stackage LTS 24.50 Pantry tree "
    "sha256:12839cef1252eaa894d6a9adafaa2e1cdb449f03c343f765294e033c813261fc"
)


def _source_contract() -> Path:
    return Path(__file__).resolve().parents[2] / "contract"


def _haskell_module_safety_result() -> dict[str, Any]:
    mandatory_core_extensions = [
        "NoForeignFunctionInterface",
        "NoTemplateHaskell",
        "NoCPP",
        "NoRebindableSyntax",
        "NoLinearTypes",
        "NoMagicHash",
        "NoStrict",
        "NoGeneralizedNewtypeDeriving",
        "NoDerivingVia",
        "NoDeriveAnyClass",
    ]
    sha256 = "a" * 64
    return {
        "schemaVersion": "s1.4x-haskell-module-safety-result-v1",
        "policySha256": sha256,
        "sourceInputManifestSha256": sha256,
        "modules": [
            {
                "moduleName": "Risk.Scalar",
                "path": "src/Risk/Scalar.hs",
                "category": "safe-scalar",
                "compileMode": "Safe",
                "extensions": ["Safe", *mandatory_core_extensions],
                "sourceSha256": sha256,
            },
            {
                "moduleName": "Risk.Vector",
                "path": "src/Risk/Vector.hs",
                "category": "audited-pure-vector",
                "compileMode": "SafeHaskell-None-with-audited-purity-gate",
                "extensions": mandatory_core_extensions,
                "sourceSha256": sha256,
            },
            {
                "moduleName": "Risk.Shell",
                "path": "app/Risk/Shell.hs",
                "category": "io-shell",
                "compileMode": "ordinary",
                "extensions": [],
                "sourceSha256": sha256,
            },
        ],
        "candidateDirectImports": [
            {
                "fromModule": "Risk.Vector",
                "fromCategory": "audited-pure-vector",
                "importedModule": "Data.Vector.Unboxed",
                "classification": "allowed-pure",
            }
        ],
        "candidateHomeModuleEdges": [
            {
                "fromModule": "Risk.Scalar",
                "fromCategory": "safe-scalar",
                "toModule": "Risk.Vector",
                "toCategory": "audited-pure-vector",
                "classification": "core-to-core",
            },
            {
                "fromModule": "Risk.Shell",
                "fromCategory": "io-shell",
                "toModule": "Risk.Scalar",
                "toCategory": "safe-scalar",
                "classification": "shell-to-core",
            },
        ],
        "upstreamTransitiveEdges": [
            {
                "package": "vector",
                "version": "0.13.2.0",
                "sourceSha256": _VECTOR_SOURCE_SHA256,
                "importPath": (
                    "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
                    "Data.Vector.Primitive -> Unsafe.Coerce"
                ),
                "provenance": _VECTOR_PROVENANCE,
                "edgeKind": "unsafe-import",
                "allowlisted": True,
            },
            {
                "package": "vector",
                "version": "0.13.2.0",
                "sourceSha256": _VECTOR_SOURCE_SHA256,
                "importPath": (
                    "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
                    "Data.Vector.Primitive -> Data.Vector.Primitive.Mutable -> "
                    "Unsafe.Coerce"
                ),
                "provenance": _VECTOR_PROVENANCE,
                "edgeKind": "unsafe-import",
                "allowlisted": True,
            },
            {
                "package": "vector",
                "version": "0.13.2.0",
                "sourceSha256": _VECTOR_SOURCE_SHA256,
                "importPath": (
                    "Data.Vector.Unboxed -> Data.Vector.Generic -> "
                    "Data.Vector.Internal.Check -> GHC.Exts(Int#)"
                ),
                "provenance": _VECTOR_PROVENANCE,
                "edgeKind": "compiler-primop",
                "allowlisted": True,
            },
        ],
        "unclassifiedModuleCount": 0,
        "candidateTrustworthyUnsafeDeclarationCount": 0,
        "candidateDirectUnsafeIoForeignImportCount": 0,
        "coreToShellEdgeCount": 0,
        "unknownTransitiveEdgeCount": 0,
        "staleAllowlistCount": 0,
        "aggregateStatus": "PASS",
    }


def test_draft_2020_12_validation_is_offline_and_fail_closed(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    schemas = contract / "schemas"
    schemas.mkdir(parents=True)
    atomic_write_json(
        schemas / "sample.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.invalid/sample.schema.json",
            "type": "object",
            "additionalProperties": False,
            "required": ["schemaVersion", "value"],
            "properties": {
                "schemaVersion": {"const": "sample-v1"},
                "value": {"type": "integer"},
            },
        },
    )
    atomic_write_json(contract / "sample.json", {"schemaVersion": "sample-v1", "value": 1})

    assert validate_json_schemas(contract) == {"sample.json": "sample.schema.json"}

    atomic_write_json(
        contract / "sample.json",
        {"schemaVersion": "sample-v1", "value": 1, "unknown": True},
    )
    with pytest.raises(OracleContractError, match="schema validation failed"):
        validate_json_schemas(contract)

    atomic_write_json(contract / "sample.json", {"schemaVersion": "unknown-v1"})
    with pytest.raises(OracleContractError, match="has no schema"):
        validate_json_schemas(contract)


def test_haskell_result_semantic_join_is_integrated_and_fail_closed(
    tmp_path: Path,
) -> None:
    contract = tmp_path / "contract"
    schemas = contract / "schemas"
    schemas.mkdir(parents=True)
    schema_name = "haskell-module-safety-result.schema.json"
    shutil.copy2(_source_contract() / "schemas" / schema_name, schemas / schema_name)
    source_manifest_schema_name = "source-input-manifest.schema.json"
    shutil.copy2(
        _source_contract() / "schemas" / source_manifest_schema_name,
        schemas / source_manifest_schema_name,
    )
    shutil.copy2(
        _source_contract() / "haskell-module-safety-policy.v1.json",
        contract / "haskell-module-safety-policy.v1.json",
    )
    haskell_root = contract.parent / "haskell"
    haskell_root.mkdir()
    source_manifest_path = haskell_root / "source-inputs.v1.json"
    source_manifest: dict[str, Any] = {
        "schemaVersion": "s1.4x-source-input-manifest-v1",
        "language": "haskell",
        "files": {
            "src/Risk/Scalar.hs": {"role": "main", "sha256": "a" * 64},
            "src/Risk/Vector.hs": {"role": "main", "sha256": "a" * 64},
            "app/Risk/Shell.hs": {"role": "main", "sha256": "a" * 64},
            "package.yaml": {"role": "configuration", "sha256": "b" * 64},
            "selected-profile.v1.json": {
                "role": "configuration",
                "sha256": "c" * 64,
            },
        },
        "inputSets": {
            "tracked": "files",
            "manifest": "files",
            "format": "files",
            "compile": "files",
            "lint": "files",
            "profileRun": "files",
        },
        "canonicalManifestSha256": "c" * 64,
    }
    atomic_write_json(source_manifest_path, source_manifest)
    reports = contract.parent / "reports"
    reports.mkdir()
    result_path = reports / "haskell-module-safety-result.v1.json"
    positive = _haskell_module_safety_result()
    positive["policySha256"] = sha256_file(contract / "haskell-module-safety-policy.v1.json")
    positive["sourceInputManifestSha256"] = sha256_file(source_manifest_path)
    atomic_write_json(result_path, positive)

    assert validate_json_schemas(contract) == {
        "haskell/source-inputs.v1.json": source_manifest_schema_name,
        f"reports/{result_path.name}": schema_name,
    }

    false_direct_category = deepcopy(positive)
    false_direct_category["candidateDirectImports"][0]["fromModule"] = "Risk.Scalar"
    false_direct_category["candidateDirectImports"][0]["fromCategory"] = "io-shell"

    false_home_category = deepcopy(positive)
    false_home_category["candidateHomeModuleEdges"][0].update(
        {
            "toModule": "Risk.Shell",
            "toCategory": "safe-scalar",
        }
    )

    missing_endpoint = deepcopy(positive)
    missing_endpoint["candidateDirectImports"][0].update(
        {
            "fromModule": "Risk.Missing",
            "fromCategory": "audited-pure-vector",
        }
    )

    duplicate_module_path = deepcopy(positive)
    duplicate_module = deepcopy(duplicate_module_path["modules"][0])
    duplicate_module["moduleName"] = "Risk.OtherScalar"
    duplicate_module_path["modules"].append(duplicate_module)

    source_hash_mismatch = deepcopy(positive)
    source_hash_mismatch["modules"][0]["sourceSha256"] = "e" * 64

    wrong_manifest_hash = deepcopy(positive)
    wrong_manifest_hash["sourceInputManifestSha256"] = "f" * 64

    for counterexample, expected in (
        (false_direct_category, "fromCategory does not match"),
        (false_home_category, "toCategory does not match"),
        (missing_endpoint, "fromModule is not present"),
        (duplicate_module_path, "duplicate module path"),
        (source_hash_mismatch, "does not match source-input manifest"),
        (wrong_manifest_hash, "does not match the manifest bytes"),
    ):
        atomic_write_json(result_path, counterexample)
        with pytest.raises(OracleContractError, match=expected):
            validate_json_schemas(contract)

    invented_upstream = deepcopy(positive)
    invented_edge = deepcopy(invented_upstream["upstreamTransitiveEdges"][0])
    invented_edge.update({"package": "invented-vector", "version": "99.0.0"})
    invented_upstream["upstreamTransitiveEdges"].append(invented_edge)

    stale_upstream = deepcopy(positive)
    stale_upstream["upstreamTransitiveEdges"].pop()

    hash_drift = deepcopy(positive)
    hash_drift["upstreamTransitiveEdges"][0]["sourceSha256"] = "0" * 64

    path_drift = deepcopy(positive)
    path_drift["upstreamTransitiveEdges"][0]["importPath"] = (
        "Data.Vector.Unboxed -> Invented.Unsafe"
    )

    provenance_drift = deepcopy(positive)
    provenance_drift["upstreamTransitiveEdges"][0]["provenance"] = "unverified local archive"

    for counterexample in (
        invented_upstream,
        stale_upstream,
        hash_drift,
        path_drift,
        provenance_drift,
    ):
        atomic_write_json(result_path, counterexample)
        with pytest.raises(
            OracleContractError,
            match="upstream transitive allowlist exact-set mismatch",
        ):
            validate_json_schemas(contract)

    wrong_language_manifest = deepcopy(source_manifest)
    wrong_language_manifest["language"] = "scala"
    atomic_write_json(source_manifest_path, wrong_language_manifest)
    wrong_language_result = deepcopy(positive)
    wrong_language_result["sourceInputManifestSha256"] = sha256_file(source_manifest_path)
    atomic_write_json(result_path, wrong_language_result)
    with pytest.raises(OracleContractError, match="requires a Haskell source-input"):
        validate_json_schemas(contract)

    missing_source_manifest = deepcopy(source_manifest)
    missing_source_manifest["files"].pop("src/Risk/Vector.hs")
    extra_source_manifest = deepcopy(source_manifest)
    extra_source_manifest["files"]["src/Risk/Extra.hs"] = {
        "role": "main",
        "sha256": "d" * 64,
    }
    for mutated_manifest in (
        missing_source_manifest,
        extra_source_manifest,
    ):
        atomic_write_json(source_manifest_path, mutated_manifest)
        manifest_tied_result = deepcopy(positive)
        manifest_tied_result["sourceInputManifestSha256"] = sha256_file(source_manifest_path)
        atomic_write_json(result_path, manifest_tied_result)
        with pytest.raises(
            OracleContractError,
            match="Haskell source path set mismatch",
        ):
            validate_json_schemas(contract)

    for forbidden_suffix in (".lhs", ".hsc", ".hs-boot"):
        escaped_manifest = deepcopy(source_manifest)
        escaped_manifest["files"][f"src/Risk/Escape{forbidden_suffix}"] = {
            "role": "main",
            "sha256": "d" * 64,
        }
        atomic_write_json(source_manifest_path, escaped_manifest)
        suffix_tied_result = deepcopy(positive)
        suffix_tied_result["sourceInputManifestSha256"] = sha256_file(source_manifest_path)
        atomic_write_json(result_path, suffix_tied_result)
        with pytest.raises(
            OracleContractError,
            match=r"schema validation failed|forbidden Haskell source suffix",
        ):
            validate_json_schemas(contract)

    for required_configuration in ("selected-profile.v1.json", "package.yaml"):
        missing_configuration_manifest = deepcopy(source_manifest)
        missing_configuration_manifest["files"].pop(required_configuration)
        atomic_write_json(source_manifest_path, missing_configuration_manifest)
        missing_configuration_result = deepcopy(positive)
        missing_configuration_result["sourceInputManifestSha256"] = sha256_file(
            source_manifest_path
        )
        atomic_write_json(result_path, missing_configuration_result)
        with pytest.raises(
            OracleContractError,
            match=(
                r"schema validation failed|"
                r"Haskell non-\\.hs configuration path set mismatch"
            ),
        ):
            validate_json_schemas(contract)

    atomic_write_json(source_manifest_path, source_manifest)
    policy_path = contract / "haskell-module-safety-policy.v1.json"
    extended_policy = strict_json_load(policy_path)
    extended_policy["mandatoryCoreExtensions"].append("NoImplicitPrelude")
    atomic_write_json(policy_path, extended_policy)
    policy_tied_result = deepcopy(positive)
    policy_tied_result["policySha256"] = sha256_file(policy_path)
    policy_tied_result["sourceInputManifestSha256"] = sha256_file(source_manifest_path)
    atomic_write_json(result_path, policy_tied_result)
    with pytest.raises(OracleContractError, match="omits mandatory core extensions"):
        validate_json_schemas(contract)


def test_registry_exact_counts_sets_and_bidirectional_edges(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    contract.mkdir()
    for name in ("function-registry.v1.json", "error-registry.v1.json"):
        shutil.copy2(_source_contract() / name, contract / name)

    functions, errors = validate_registries(contract)

    assert len(functions) == 20
    assert len(errors) == 32

    mutated = strict_json_load(contract / "error-registry.v1.json")
    mutated["entries"][0]["applicableFunctionIds"].remove("simple_returns")
    atomic_write_json(contract / "error-registry.v1.json", mutated)
    with pytest.raises(OracleContractError, match="not bidirectional"):
        validate_registries(contract)


def test_property_plan_requires_all_25_frozen_invariant_ids(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    contract.mkdir()
    for name in (
        "function-registry.v1.json",
        "error-registry.v1.json",
        "property-plan.v1.json",
    ):
        shutil.copy2(_source_contract() / name, contract / name)
    functions, _ = validate_registries(contract)

    assert validate_property_plan(contract, functions=functions) == 25

    mutated = strict_json_load(contract / "property-plan.v1.json")
    mutated["properties"] = [
        item
        for item in mutated["properties"]
        if item["propertyId"] != "cumulative-return.manual-product-identity"
    ]
    mutated["properties"].append(mutated["properties"][0])
    atomic_write_json(contract / "property-plan.v1.json", mutated)
    with pytest.raises(
        OracleContractError,
        match=r"duplicate propertyId|required invariant",
    ):
        validate_property_plan(contract, functions=functions)


def _binary_manifest() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-binary-array-v1",
        "fixtureId": "tiny-prices",
        "argumentName": "prices",
        "fileName": "tiny-prices.f64le",
        "encoding": "ieee754-binary64",
        "dtype": "float64",
        "byteOrder": "little",
        "arrayOrder": "C",
        "shape": [2],
        "count": 2,
        "byteLength": 16,
        "sha256": "0" * 64,
        "generator": {
            "algorithm": "numpy-pcg64",
            "seed": 1,
            "generatorVersion": "numpy-2.5.1",
            "distribution": "lognormal",
            "parameters": {"mean": 1.0, "sigma": 0.1},
            "chunkLength": 2,
        },
    }


def test_request_semantics_preserve_integer_tokens_and_binary_basename(
    tmp_path: Path,
) -> None:
    functions_contract = tmp_path / "registry"
    functions_contract.mkdir()
    for name in ("function-registry.v1.json", "error-registry.v1.json"):
        shutil.copy2(_source_contract() / name, functions_contract / name)
    functions, errors = validate_registries(functions_contract)

    contract = tmp_path / "contract"
    large = contract / "fixtures" / "large"
    small = contract / "fixtures" / "small"
    large.mkdir(parents=True)
    small.mkdir(parents=True)
    atomic_write_json(large / "tiny.manifest.json", _binary_manifest())
    request: dict[str, Any] = {
        "schemaVersion": "s1.4x-request-v1",
        "requestId": "request-1",
        "cases": [
            {
                "fixtureId": "case-1",
                "functionId": "cagr",
                "arguments": {"prices": [1.0, 2.0], "periods_per_year": 252},
            },
            {
                "fixtureId": "case-2",
                "functionId": "simple_returns",
                "arguments": {
                    "prices": {
                        "kind": "binaryFloat64",
                        "manifestFile": "tiny.manifest.json",
                    }
                },
            },
        ],
    }
    atomic_write_json(small / "request.json", request)

    assert validate_request_fixtures(contract, functions, errors) == {}

    request["cases"][0]["arguments"]["periods_per_year"] = 252.0
    atomic_write_json(small / "request.json", request)
    with pytest.raises(OracleContractError, match="bare JSON integer"):
        validate_request_fixtures(contract, functions, errors)


def test_binary_manifest_rejects_rank_size_hash_and_nonfinite_raw(tmp_path: Path) -> None:
    large = tmp_path / "fixtures" / "large"
    generated = large / "generated"
    generated.mkdir(parents=True)
    manifest = _binary_manifest()
    raw = b"\x00\x00\x00\x00\x00\x00\xf0\x3f" * 2
    manifest["sha256"] = sha256_bytes(raw)
    atomic_write_json(large / "tiny.manifest.json", manifest)
    (generated / manifest["fileName"]).write_bytes(raw)

    assert validate_binary_manifests(tmp_path, allowed_nonfinite={}) == 1

    manifest["shape"] = [1, 2]
    atomic_write_json(large / "tiny.manifest.json", manifest)
    with pytest.raises(OracleContractError, match="rank"):
        validate_binary_manifests(tmp_path, allowed_nonfinite={})


def test_negative_catalog_reproduces_every_declared_layer_and_disposition() -> None:
    contract = _source_contract()
    catalog = strict_json_load(contract / "fixtures" / "invalid" / "invalid-fixtures.v1.json")

    outcomes = validate_negative_fixtures(contract)

    assert len(outcomes) == len(catalog["entries"]) == 16
    assert set(outcomes) == {entry["fixtureId"] for entry in catalog["entries"]}
    for entry in catalog["entries"]:
        outcome = outcomes[entry["fixtureId"]]
        assert outcome["validationLayer"] == entry["validationLayer"]
        assert outcome["disposition"] == entry["expectedDisposition"]
        assert outcome["reason"]


def test_negative_catalog_does_not_trust_a_declared_expected_layer(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    shutil.copytree(_source_contract(), contract)
    catalog_path = contract / "fixtures" / "invalid" / "invalid-fixtures.v1.json"
    catalog = strict_json_load(catalog_path)
    catalog["entries"][0]["validationLayer"] = "strict-json-pre-parser"
    atomic_write_json(catalog_path, catalog)

    with pytest.raises(OracleContractError, match="negative outcome mismatch"):
        validate_negative_fixtures(contract)


def test_arbitrary_size_manifest_reaches_semantic_allocation_cap() -> None:
    contract = _source_contract()
    schema = strict_json_load(contract / "schemas" / "binary-array-manifest.schema.json")
    fixture = strict_json_load(
        contract / "fixtures" / "invalid" / "manifest-arithmetic-overflow.json"
    )

    assert list(Draft202012Validator(schema).iter_errors(fixture)) == []
    outcome = validate_negative_fixtures(contract)["manifest-arbitrary-integer-overflow"]
    assert outcome["validationLayer"] == "manifest-semantic"
    assert "allocation cap" in outcome["reason"]


def test_nonfinite_fixture_uses_honest_nan_bytes_without_tracked_raw() -> None:
    contract = _source_contract()
    schema = strict_json_load(contract / "schemas" / "binary-array-manifest.schema.json")
    fixture = strict_json_load(
        contract / "fixtures" / "invalid" / "manifest-non-finite-semantic.json"
    )
    payload = bytes.fromhex(fixture["generator"]["payloadHex"])

    assert len(payload) == fixture["byteLength"] == 8
    assert sha256_bytes(payload) == fixture["sha256"]
    assert payload == bytes.fromhex("000000000000f87f")
    assert math.isnan(struct.unpack("<d", payload)[0])
    assert not (contract / "fixtures" / "invalid" / fixture["fileName"]).exists()

    fixture["generator"]["generatorVersion"] = "unfrozen-literal-generator"
    assert list(Draft202012Validator(schema).iter_errors(fixture))


def test_reference_tree_closure_and_workflow_trigger_coverage(tmp_path: Path) -> None:
    repo = tmp_path
    contract = repo / "contract"
    source_root = repo / "shared"
    canonical = (
        repo
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4r-jax-risk"
        / "tests"
        / "fixtures"
        / "canonical"
        / "advanced_risk_v1.json"
    )
    contract.mkdir()
    source_root.mkdir()
    canonical.parent.mkdir(parents=True)
    source = source_root / "source.txt"
    source.write_text("source", encoding="utf-8")
    canonical.write_text("fixture", encoding="utf-8")
    payload, files = canonical_file_manifest(source_root, [source])
    lock: dict[str, Any] = {
        "schemaVersion": "s1.4x-reference-lock-v1",
        "referenceBaseCommit": "a" * 40,
        "pythonRuntime": {
            "implementation": "CPython",
            "version": "3.12.13",
            "uvVersion": "0.11.26",
            "productionNumpyVersion": "2.5.1",
            "researchNumpyVersion": "2.5.1",
            "jaxVersion": "0.11.0",
            "jaxlibVersion": "0.11.0",
        },
        "functionCount": 20,
        "stableErrorCodeCount": 32,
        "s1_4r_canonical_fixture_sha256": sha256_file(canonical),
        "sources": [{"role": "source", "path": "shared/source.txt", "sha256": sha256_file(source)}],
        "sourceTrees": [
            {
                "role": "source-tree",
                "root": "shared",
                "includeGlobs": ["*.txt"],
                "fileCount": 1,
                "canonicalManifestSha256": sha256_bytes(payload),
                "files": files,
            }
        ],
    }
    atomic_write_json(contract / "reference-lock.v1.json", lock)
    reference = validate_reference_lock(repo, contract)
    assert reference["sourceCount"] == 1

    lock["pythonRuntime"]["jaxVersion"] = "unfrozen"
    atomic_write_json(contract / "reference-lock.v1.json", lock)
    with pytest.raises(OracleContractError, match="runtime identity"):
        validate_reference_lock(repo, contract)
    lock["pythonRuntime"]["jaxVersion"] = "0.11.0"

    saved_sources = lock["sources"]
    lock["sources"] = []
    atomic_write_json(contract / "reference-lock.v1.json", lock)
    with pytest.raises(OracleContractError, match="source path set"):
        validate_reference_lock(repo, contract)
    lock["sources"] = saved_sources
    atomic_write_json(contract / "reference-lock.v1.json", lock)

    workflow = repo / ".github" / "workflows" / "s1-4x-contract-correctness.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "on:\n"
        "  pull_request:\n"
        "    paths:\n"
        "      - 'shared/**'\n"
        "  push:\n"
        "    branches: [main]\n"
        "    paths:\n"
        "      - 'shared/**'\n",
        encoding="utf-8",
    )
    assert validate_workflow_path_coverage(repo, reference["sourcePaths"]) == 1

    workflow.write_text(
        "on:\n"
        "  pull_request:\n"
        "    paths:\n"
        "      - 'shared/**'\n"
        "  push:\n"
        "    branches: [main]\n"
        "    paths:\n"
        "      - 'other/**'\n",
        encoding="utf-8",
    )
    with pytest.raises(OracleContractError, match="path trigger sets must be identical"):
        validate_workflow_path_coverage(repo, reference["sourcePaths"])

    source_root.joinpath("new.txt").write_text("drift", encoding="utf-8")
    with pytest.raises(OracleContractError, match=r"fileCount mismatch|closure drift"):
        validate_reference_lock(repo, contract)


def test_reference_project_runtime_projection_ignores_scripts_and_locks_runtime(
    tmp_path: Path,
) -> None:
    repo = tmp_path
    contract = repo / "contract"
    project_root = repo / "workspaces" / "decision-platform" / "python-services"
    project_file = project_root / "pyproject.toml"
    uv_lock = project_root / "uv.lock"
    canonical = (
        repo
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4r-jax-risk"
        / "tests"
        / "fixtures"
        / "canonical"
        / "advanced_risk_v1.json"
    )
    contract.mkdir()
    project_root.mkdir(parents=True)
    canonical.parent.mkdir(parents=True)
    canonical.write_text("fixture", encoding="utf-8")
    uv_lock.write_text("version = 1\n", encoding="utf-8")

    def write_project(
        *,
        dependencies: tuple[str, ...],
        dev_dependencies: tuple[str, ...] = ("pytest>=8", "ruff>=0.5"),
        script: str,
        uv_package: bool = False,
        ruff_line_length: int = 100,
    ) -> None:
        dependency_lines = "\n".join(f'  "{dependency}",' for dependency in dependencies)
        dev_dependency_lines = "\n".join(
            f'  "{dependency}",' for dependency in dev_dependencies
        )
        project_file.write_text(
            "[project]\n"
            'name = "decision-platform-services"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.12"\n'
            "dependencies = [\n"
            f"{dependency_lines}\n"
            "]\n\n"
            "[project.scripts]\n"
            f'report = "{script}"\n\n'
            "[build-system]\n"
            'requires = ["hatchling"]\n'
            'build-backend = "hatchling.build"\n\n'
            "[dependency-groups]\n"
            "dev = [\n"
            f"{dev_dependency_lines}\n"
            "]\n\n"
            "[tool.uv]\n"
            f"package = {str(uv_package).lower()}\n\n"
            "[tool.hatch.build.targets.wheel]\n"
            'packages = ["app"]\n\n'
            "[tool.ruff]\n"
            f"line-length = {ruff_line_length}\n",
            encoding="utf-8",
        )

    dependencies = ("numpy==2.5.1", "pandas>=2.2")
    write_project(dependencies=dependencies, script="app.old:main")
    projection = {
        "schemaVersion": "s1.4x-python-project-runtime-projection-v1",
        "build-system": {
            "build-backend": "hatchling.build",
            "requires": ["hatchling"],
        },
        "dependency-groups": {"dev": ["pytest>=8", "ruff>=0.5"]},
        "project": {
            "dependencies": ["numpy==2.5.1", "pandas>=2.2"],
            "requires-python": ">=3.12",
        },
        "tool": {
            "hatch": {"build": {"targets": {"wheel": {"packages": ["app"]}}}},
            "uv": {"package": False},
        },
    }
    projection_sha256 = sha256_bytes(canonical_json_bytes(projection))
    uv_lock_sha256 = sha256_file(uv_lock)
    manifest_payload = (
        f"{projection_sha256}  pyproject.toml\n"
        f"{uv_lock_sha256}  uv.lock\n"
    ).encode()
    lock: dict[str, Any] = {
        "schemaVersion": "s1.4x-reference-lock-v1",
        "referenceBaseCommit": "a" * 40,
        "pythonRuntime": {
            "implementation": "CPython",
            "version": "3.12.13",
            "uvVersion": "0.11.26",
            "productionNumpyVersion": "2.5.1",
            "researchNumpyVersion": "2.5.1",
            "jaxVersion": "0.11.0",
            "jaxlibVersion": "0.11.0",
        },
        "functionCount": 20,
        "stableErrorCodeCount": 32,
        "s1_4r_canonical_fixture_sha256": sha256_file(canonical),
        "sources": [
            {
                "role": "production-project-runtime-projection",
                "path": (
                    "workspaces/decision-platform/python-services/pyproject.toml"
                ),
                "sha256": projection_sha256,
            },
            {
                "role": "production-environment-lock",
                "path": "workspaces/decision-platform/python-services/uv.lock",
                "sha256": uv_lock_sha256,
            },
        ],
        "sourceTrees": [
            {
                "role": "production-reference-tree",
                "root": "workspaces/decision-platform/python-services",
                "includeGlobs": ["pyproject.toml", "uv.lock"],
                "fileCount": 2,
                "canonicalManifestSha256": sha256_bytes(manifest_payload),
                "files": [
                    {"path": "pyproject.toml", "sha256": projection_sha256},
                    {"path": "uv.lock", "sha256": uv_lock_sha256},
                ],
            }
        ],
    }
    atomic_write_json(contract / "reference-lock.v1.json", lock)

    assert validate_reference_lock(repo, contract)["sourceCount"] == 2

    # CLI entrypoint와 lint 설정은 수치 oracle의 dependency/runtime identity가 아니다.
    write_project(
        dependencies=tuple(reversed(dependencies)),
        dev_dependencies=("ruff>=0.5", "pytest>=8"),
        script="app.new:main",
        ruff_line_length=120,
    )
    assert validate_reference_lock(repo, contract)["sourceCount"] == 2

    write_project(dependencies=("numpy==2.6.0", "pandas>=2.2"), script="app.new:main")
    with pytest.raises(OracleContractError, match="reference source SHA-256 mismatch"):
        validate_reference_lock(repo, contract)

    write_project(
        dependencies=dependencies,
        dev_dependencies=("pytest>=8", "ruff>=0.6"),
        script="app.new:main",
    )
    with pytest.raises(OracleContractError, match="reference source SHA-256 mismatch"):
        validate_reference_lock(repo, contract)

    write_project(dependencies=dependencies, script="app.new:main", uv_package=True)
    with pytest.raises(OracleContractError, match="reference source SHA-256 mismatch"):
        validate_reference_lock(repo, contract)

    write_project(dependencies=dependencies, script="app.new:main")
    uv_lock.write_text("version = 1\n# byte drift\n", encoding="utf-8")
    with pytest.raises(OracleContractError, match="reference source SHA-256 mismatch"):
        validate_reference_lock(repo, contract)


def test_contract_manifest_self_exclusion_sorting_and_added_file_drift(
    tmp_path: Path,
) -> None:
    repo = tmp_path
    tree = repo / "tree"
    tree.mkdir()
    (repo / "fixed.txt").write_text("fixed", encoding="utf-8")
    (tree / "z.txt").write_text("z", encoding="utf-8")
    (tree / "a.txt").write_text("a", encoding="utf-8")
    manifest_path = tree / "contract-manifest.v1.json"
    atomic_write_json(
        manifest_path,
        {
            "schemaVersion": CONTRACT_MANIFEST_VERSION,
            "immutableFiles": ["fixed.txt"],
            "immutableRoots": [
                {
                    "root": "tree",
                    "includeGlobs": ["**/*"],
                    "excludeGlobs": [],
                }
            ],
        },
    )

    written = write_contract_manifest(repo, manifest_path)

    assert validate_contract_manifest(repo, manifest_path, required=True) == 3
    root_entry = written["immutableRoots"][0]
    assert [entry["path"] for entry in root_entry["files"]] == ["a.txt", "z.txt"]
    assert all(entry["path"] != manifest_path.name for entry in root_entry["files"])

    (tree / "new.txt").write_text("new", encoding="utf-8")
    with pytest.raises(OracleContractError, match="closure drift"):
        validate_contract_manifest(repo, manifest_path, required=True)


def test_contract_manifest_rejects_duplicate_file_across_sections(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("a", encoding="utf-8")
    manifest = {
        "schemaVersion": CONTRACT_MANIFEST_VERSION,
        "immutableFiles": ["tree/a.txt"],
        "immutableRoots": [{"root": "tree", "includeGlobs": ["*.txt"], "excludeGlobs": []}],
    }
    manifest_path = tmp_path / "manifest.json"
    atomic_write_json(manifest_path, manifest)

    with pytest.raises(OracleContractError, match="lists a file twice"):
        materialize_contract_manifest(tmp_path, manifest_path, manifest)


def test_sha256_sidecar_validates_sibling_bytes(tmp_path: Path) -> None:
    payload = tmp_path / "fixture.json"
    payload.write_bytes(b"{}\n")
    sidecar = tmp_path / "fixture.sha256"
    sidecar.write_text(f"{sha256_file(payload)}  fixture.json\n", encoding="ascii")

    assert validate_sha256_sidecars(tmp_path) == 1

    sidecar.write_text(f"{'0' * 64}  fixture.json\n", encoding="ascii")
    with pytest.raises(OracleContractError, match="mismatch"):
        validate_sha256_sidecars(tmp_path)


def test_top_level_validation_aggregates_independent_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_schemas(_: Path) -> dict[str, str]:
        raise OracleContractError("schema drift")

    def fail_reference(_: Path, __: Path) -> dict[str, Any]:
        raise OracleContractError("reference drift")

    monkeypatch.setattr(validator_module, "validate_json_schemas", fail_schemas)
    monkeypatch.setattr(
        validator_module,
        "validate_registries",
        lambda _: ({"f": {}}, frozenset({"e"})),
    )
    monkeypatch.setattr(validator_module, "validate_property_plan", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        validator_module,
        "validate_request_fixtures",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        validator_module,
        "validate_expected_results",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        validator_module,
        "validate_negative_fixtures",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        validator_module,
        "validate_binary_manifests",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(validator_module, "validate_sha256_sidecars", lambda *_args: 0)
    monkeypatch.setattr(validator_module, "validate_reference_lock", fail_reference)
    monkeypatch.setattr(
        validator_module,
        "validate_contract_manifest",
        lambda *_args, **_kwargs: 0,
    )

    with pytest.raises(OracleContractError) as captured:
        validator_module.validate_contract(
            repo_root=tmp_path,
            contract_root=tmp_path,
            manifest_path=tmp_path / "manifest.json",
            check_all=True,
        )

    assert "schemas: schema drift" in str(captured.value)
    assert "reference-lock: reference drift" in str(captured.value)
