"""S1.4X Gate 1 schema, registry, fixture, reference-lock closure를 검증한다."""

from __future__ import annotations

import argparse
import copy
import fnmatch
import math
import re
import struct
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote

from oracle_common import (
    OracleContractError,
    atomic_write_json,
    canonical_file_manifest,
    canonical_json_bytes,
    find_repo_root,
    require_lower_sha256,
    require_safe_basename,
    resolve_within,
    sha256_bytes,
    sha256_file,
    sorted_relative_files,
    strict_json_load,
)

REFERENCE_LOCK_VERSION = "s1.4x-reference-lock-v1"
PROJECT_RUNTIME_PROJECTION_VERSION = "s1.4x-python-project-runtime-projection-v1"
CONTRACT_MANIFEST_VERSION = "s1.4x-contract-manifest-v1"
BINARY_MANIFEST_VERSION = "s1.4x-binary-array-v1"
REQUEST_VERSION = "s1.4x-request-v1"
RESULT_BATCH_VERSION = "s1.4x-result-batch-v1"
HASKELL_MODULE_SAFETY_RESULT_VERSION = "s1.4x-haskell-module-safety-result-v1"
GHC_COMPATIBILITY_RESULT_VERSION = "s1.4x-ghc-compatibility-result-v1"
HASKELL_APPROVED_NON_HS_INPUTS = frozenset({"selected-profile.v1.json", "package.yaml"})
HASKELL_VECTOR_SOURCE_SHA256 = "28f203c786cbf8ac6dc3fea3378ec36f34173d505fb4a1dd60fc8418ad91c423"
HASKELL_VECTOR_PANTRY_TREE_SHA256 = (
    "12839cef1252eaa894d6a9adafaa2e1cdb449f03c343f765294e033c813261fc"
)
HASKELL_VECTOR_PROVENANCE = (
    "official Hackage vector-0.13.2.0 archive bytes; "
    "Stackage LTS 24.50 Pantry tree "
    f"sha256:{HASKELL_VECTOR_PANTRY_TREE_SHA256}"
)
HASKELL_VECTOR_APPROVED_PATHS = frozenset(
    {
        (
            "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
            "Data.Vector.Primitive -> Unsafe.Coerce",
            "unsafe-import",
        ),
        (
            "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
            "Data.Vector.Primitive -> Data.Vector.Primitive.Mutable -> Unsafe.Coerce",
            "unsafe-import",
        ),
        (
            "Data.Vector.Unboxed -> Data.Vector.Generic -> "
            "Data.Vector.Internal.Check -> GHC.Exts(Int#)",
            "compiler-primop",
        ),
    }
)
ALLOCATION_CAP_BYTES = 536_870_912
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_REFERENCE_RUNTIME = {
    "implementation": "CPython",
    "version": "3.12.13",
    "uvVersion": "0.11.26",
    "productionNumpyVersion": "2.5.1",
    "researchNumpyVersion": "2.5.1",
    "jaxVersion": "0.11.0",
    "jaxlibVersion": "0.11.0",
}
PROJECT_RUNTIME_SOURCE_ROLE_BY_PATH = {
    "workspaces/decision-platform/python-services/pyproject.toml": (
        "production-project-runtime-projection"
    ),
    "workspaces/decision-platform/research/s1-4r-jax-risk/pyproject.toml": (
        "research-project-runtime-projection"
    ),
}
PROJECT_RUNTIME_SOURCE_ROLES = frozenset(PROJECT_RUNTIME_SOURCE_ROLE_BY_PATH.values())

S1_4_FUNCTIONS = frozenset(
    {
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
    }
)
S1_4R_FUNCTIONS = frozenset(
    {
        "historical_expected_shortfall",
        "realized_variance",
        "realized_volatility_intraday",
        "lo_adjusted_sharpe_ratio",
        "probabilistic_sharpe_ratio",
        "deflated_sharpe_ratio",
        "kupiec_unconditional_coverage_test",
        "christoffersen_independence_test",
        "christoffersen_conditional_coverage_test",
    }
)
S1_4_ERROR_CODES = frozenset(
    {
        "input_type_invalid",
        "input_shape_invalid",
        "input_empty",
        "input_too_short",
        "input_too_long",
        "input_bool_invalid",
        "input_complex_invalid",
        "input_non_finite",
        "prices_non_positive",
        "equity_initial_non_positive",
        "equity_negative",
        "simple_return_below_minus_one",
        "periods_per_year_invalid",
        "risk_free_rate_invalid",
        "target_return_invalid",
        "confidence_invalid",
        "denominator_zero",
        "tail_empty",
        "result_non_finite",
    }
)
S1_4R_ERROR_CODES = frozenset(
    {
        "research_input_invalid",
        "research_input_too_short",
        "aggregation_periods_invalid",
        "moment_invalid",
        "trial_count_invalid",
        "trial_variance_invalid",
        "trial_provenance_invalid",
        "significance_invalid",
        "forecast_shape_invalid",
        "forecast_var_negative",
        "insufficient_sample",
        "likelihood_invalid",
        "research_result_non_finite",
    }
)
KNOWN_SCHEMALESS_VERSIONS = frozenset(
    {
        CONTRACT_MANIFEST_VERSION,
        REFERENCE_LOCK_VERSION,
        "s1.4x-function-registry-v1",
        "s1.4x-error-registry-v1",
        "s1.4x-property-seeds-v1",
        "s1.4x-dependency-native-edge-policy-v1",
        "s1.4x-ghc-compatibility-policy-v1",
        "s1.4x-haskell-module-safety-policy-v1",
        "s1.4x-scala-source-policy-v1",
    }
)
SEMANTIC_NONFINITE_CODES = frozenset({"input_non_finite", "research_input_invalid"})
REQUIRED_PROPERTY_IDS = frozenset(
    {
        "simple-returns.scale-invariant",
        "log-returns.scale-invariant",
        "cumulative-return.manual-product-identity",
        "volatility.translation-and-scale",
        "max-drawdown.bounds",
        "var-cvar.shift-and-positive-scale",
        "expected-shortfall.permutation-invariant",
        "realized.scale-laws",
        "psr.benchmark-equality",
        "dsr.provenance-count-consistency",
        "backtest.strict-loss-greater-than-var",
        "likelihood.record-invariants",
        "conditional-coverage.component-identity",
        "christoffersen.unidentifiable-transition-rejected",
    }
)
REQUIRED_NEGATIVE_FIXTURE_IDS = frozenset(
    {
        "request-unknown-key",
        "request-wrong-version",
        "request-unknown-function",
        "request-duplicate-decoded-key",
        "manifest-path-traversal",
        "manifest-absolute-path",
        "manifest-symlink-escape",
        "manifest-wrong-endian",
        "manifest-count-mismatch",
        "manifest-shape-mismatch",
        "manifest-byte-length-mismatch",
        "manifest-arbitrary-integer-overflow",
        "manifest-wrong-hash",
        "binary-truncated",
        "binary-trailing-bytes",
        "binary-non-finite-semantic",
    }
)
REQUIRED_SEMANTIC_ERROR_FIXTURE_IDS = (
    "invalid-production-top-level-bool",
    "invalid-production-nested-shape-before-bool",
    "invalid-production-empty",
    "invalid-production-too-short",
    "invalid-production-price-domain",
    "invalid-production-return-below-minus-one",
    "invalid-production-integer-decimal-token",
    "invalid-production-zero-denominator",
    "invalid-research-empty",
    "invalid-research-lo-n-not-greater-than-q",
    "invalid-research-psr-moment-inequality",
    "invalid-research-dsr-provenance-count",
    "invalid-research-forecast-shape",
    "invalid-research-forecast-negative",
    "invalid-research-unidentifiable-transitions",
)
NEGATIVE_LAYER_DISPOSITIONS = {
    "strict-json-pre-parser": frozenset({"exit-64-request_invalid"}),
    "request-schema": frozenset({"exit-64-request_invalid"}),
    "manifest-schema-and-path": frozenset({"exit-65-manifest_invalid"}),
    "manifest-schema": frozenset({"exit-65-manifest_invalid"}),
    "manifest-semantic": frozenset({"exit-65-manifest_invalid"}),
    "binary-integrity": frozenset({"exit-65-binary_invalid"}),
    "decoded-semantic": frozenset({"exit-0-input_non_finite", "exit-0-research_input_invalid"}),
}


def _require_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OracleContractError(f"{field} must be an object")
    return value


def _require_array(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise OracleContractError(f"{field} must be an array")
    return value


def _require_exact_integer(value: Any, *, field: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise OracleContractError(f"{field} must use a bare JSON integer token")
    if minimum is not None and value < minimum:
        raise OracleContractError(f"{field} must be >= {minimum}")
    return value


def _require_unique_strings(values: Iterable[Any], *, field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise OracleContractError(f"{field} entries must be non-empty strings")
        if value in seen:
            raise OracleContractError(f"{field} contains duplicate value {value!r}")
        seen.add(value)
        result.append(value)
    return result


def _schema_version(schema: Mapping[str, Any]) -> str | None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    version = properties.get("schemaVersion")
    if not isinstance(version, Mapping):
        return None
    constant = version.get("const")
    return constant if isinstance(constant, str) else None


def _decode_json_pointer_token(token: str, *, reference: str) -> str:
    if re.search(r"~(?:[^01]|$)", token):
        raise OracleContractError(f"invalid local schema JSON Pointer escape: {reference}")
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_local_schema_reference(
    root_schema: Mapping[str, Any],
    reference: Any,
) -> Mapping[str, Any]:
    """현재 schema document 내부의 URI fragment JSON Pointer만 안전하게 해석한다."""

    if not isinstance(reference, str) or not reference.startswith("#"):
        raise OracleContractError("executable annotations require a local schema $ref")
    if re.search(r"%(?![0-9A-Fa-f]{2})", reference[1:]):
        raise OracleContractError(f"invalid percent escape in local schema reference: {reference}")
    try:
        pointer = unquote(reference[1:], encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OracleContractError(f"invalid UTF-8 local schema reference: {reference}") from exc
    if pointer == "":
        return root_schema
    if not pointer.startswith("/"):
        raise OracleContractError(f"local schema reference is not a JSON Pointer: {reference}")
    current: Any = root_schema
    for raw_token in pointer[1:].split("/"):
        token = _decode_json_pointer_token(raw_token, reference=reference)
        if isinstance(current, Mapping):
            if token not in current:
                raise OracleContractError(f"local schema reference does not resolve: {reference}")
            current = current[token]
        elif isinstance(current, list):
            if (
                not token.isdecimal()
                or (len(token) > 1 and token.startswith("0"))
                or int(token) >= len(current)
            ):
                raise OracleContractError(
                    f"local schema reference has an invalid array index: {reference}"
                )
            current = current[int(token)]
        else:
            raise OracleContractError(f"local schema reference traverses a scalar: {reference}")
    if not isinstance(current, Mapping):
        raise OracleContractError(
            f"local schema reference target is not a schema object: {reference}"
        )
    return current


def validate_executable_schema_annotations(
    schema: Mapping[str, Any],
    instance: Any,
    *,
    field: str = "$",
    _root_schema: Mapping[str, Any] | None = None,
    _active_pairs: set[tuple[int, int]] | None = None,
) -> None:
    """Gate 1 custom uniqueness annotation을 local-ref-aware 실행 규칙으로 만든다."""

    root_schema = schema if _root_schema is None else _root_schema
    active_pairs = set() if _active_pairs is None else _active_pairs
    active_key = (id(schema), id(instance))
    if active_key in active_pairs:
        return
    active_pairs.add(active_key)
    try:
        reference = schema.get("$ref")
        if reference is not None:
            referenced_schema = _resolve_local_schema_reference(root_schema, reference)
            validate_executable_schema_annotations(
                referenced_schema,
                instance,
                field=field,
                _root_schema=root_schema,
                _active_pairs=active_pairs,
            )

        unique_by = schema.get("x-s1-4x-unique-by")
        unique_composite = schema.get("x-s1-4x-unique-by-composite")
        if unique_by is not None or unique_composite is not None:
            if not isinstance(instance, list):
                raise OracleContractError(f"{field} uniqueness annotation requires an array")
            if unique_by is not None:
                if not isinstance(unique_by, str) or not unique_by:
                    raise OracleContractError(f"{field} has an invalid unique-by annotation")
                key_fields = [unique_by]
            else:
                if (
                    not isinstance(unique_composite, list)
                    or not unique_composite
                    or any(not isinstance(item, str) or not item for item in unique_composite)
                ):
                    raise OracleContractError(
                        f"{field} has an invalid composite uniqueness annotation"
                    )
                key_fields = unique_composite
            seen: set[tuple[bytes, ...]] = set()
            for index, item in enumerate(instance):
                if not isinstance(item, dict) or any(key not in item for key in key_fields):
                    raise OracleContractError(
                        f"{field}[{index}] is missing annotated uniqueness fields"
                    )
                identity = tuple(canonical_json_bytes(item[key]) for key in key_fields)
                if identity in seen:
                    joined = ",".join(key_fields)
                    raise OracleContractError(
                        f"{field} contains duplicate annotated identity ({joined})"
                    )
                seen.add(identity)

        properties = schema.get("properties")
        if isinstance(instance, dict) and isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in instance and isinstance(child_schema, Mapping):
                    validate_executable_schema_annotations(
                        child_schema,
                        instance[key],
                        field=f"{field}.{key}",
                        _root_schema=root_schema,
                        _active_pairs=active_pairs,
                    )
        if isinstance(instance, list):
            prefix_items = schema.get("prefixItems")
            if isinstance(prefix_items, list):
                for index, child_schema in enumerate(prefix_items):
                    if index < len(instance) and isinstance(child_schema, Mapping):
                        validate_executable_schema_annotations(
                            child_schema,
                            instance[index],
                            field=f"{field}[{index}]",
                            _root_schema=root_schema,
                            _active_pairs=active_pairs,
                        )
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                for index, item in enumerate(instance):
                    validate_executable_schema_annotations(
                        item_schema,
                        item,
                        field=f"{field}[{index}]",
                        _root_schema=root_schema,
                        _active_pairs=active_pairs,
                    )
    finally:
        active_pairs.remove(active_key)


def validate_haskell_module_safety_result(
    instance: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    source_manifest: Mapping[str, Any],
    source_manifest_sha256: str,
    field: str,
) -> int:
    """Haskell module inventory와 edge category를 join해 schema 간 참조를 fail-closed한다."""

    if instance.get("schemaVersion") != HASKELL_MODULE_SAFETY_RESULT_VERSION:
        raise OracleContractError(f"{field} has an invalid Haskell result schemaVersion")
    if policy.get("schemaVersion") != "s1.4x-haskell-module-safety-policy-v1":
        raise OracleContractError("Haskell module-safety policy schemaVersion mismatch")
    require_lower_sha256(policy_sha256, field="haskellModuleSafetyPolicy.sha256")
    if instance.get("policySha256") != policy_sha256:
        raise OracleContractError(f"{field}.policySha256 does not match the policy bytes")
    require_lower_sha256(
        source_manifest_sha256,
        field="haskellSourceInputManifest.sha256",
    )
    if instance.get("sourceInputManifestSha256") != source_manifest_sha256:
        raise OracleContractError(
            f"{field}.sourceInputManifestSha256 does not match the manifest bytes"
        )
    if (
        source_manifest.get("schemaVersion") != "s1.4x-source-input-manifest-v1"
        or source_manifest.get("language") != "haskell"
    ):
        raise OracleContractError("Haskell result requires a Haskell source-input manifest")

    mandatory_extensions = frozenset(
        _require_unique_strings(
            _require_array(
                policy.get("mandatoryCoreExtensions"),
                field="haskellModuleSafetyPolicy.mandatoryCoreExtensions",
            ),
            field="haskellModuleSafetyPolicy.mandatoryCoreExtensions",
        )
    )
    forbidden_positive_extensions = frozenset(
        _require_unique_strings(
            _require_array(
                policy.get("forbiddenCorePositiveExtensions"),
                field="haskellModuleSafetyPolicy.forbiddenCorePositiveExtensions",
            ),
            field="haskellModuleSafetyPolicy.forbiddenCorePositiveExtensions",
        )
    )
    edge_contract = _require_object(
        policy.get("resultEdgeCategoryContract"),
        field="haskellModuleSafetyPolicy.resultEdgeCategoryContract",
    )
    if edge_contract.get("embeddedCategoriesMustMatchModuleInventory") is not True:
        raise OracleContractError(
            "Haskell policy must require embedded edge categories to match modules"
        )
    core_categories = frozenset(
        _require_unique_strings(
            _require_array(
                edge_contract.get("coreCategories"),
                field="haskellModuleSafetyPolicy.coreCategories",
            ),
            field="haskellModuleSafetyPolicy.coreCategories",
        )
    )
    if core_categories != {"safe-scalar", "audited-pure-vector"}:
        raise OracleContractError("Haskell policy core category set drifted")

    source_suffix_policy = _require_object(
        policy.get("candidateSourceSuffixPolicy"),
        field="haskellModuleSafetyPolicy.candidateSourceSuffixPolicy",
    )
    allowed_source_suffixes = frozenset(
        _require_unique_strings(
            _require_array(
                source_suffix_policy.get("allowedSourceSuffixes"),
                field=(
                    "haskellModuleSafetyPolicy.candidateSourceSuffixPolicy.allowedSourceSuffixes"
                ),
            ),
            field=("haskellModuleSafetyPolicy.candidateSourceSuffixPolicy.allowedSourceSuffixes"),
        )
    )
    if allowed_source_suffixes != {".hs"}:
        raise OracleContractError("Haskell policy must allow only the .hs source suffix")
    forbidden_compilable_suffixes = frozenset(
        _require_unique_strings(
            _require_array(
                source_suffix_policy.get("forbiddenCompilableSuffixes"),
                field=(
                    "haskellModuleSafetyPolicy.candidateSourceSuffixPolicy."
                    "forbiddenCompilableSuffixes"
                ),
            ),
            field=(
                "haskellModuleSafetyPolicy.candidateSourceSuffixPolicy.forbiddenCompilableSuffixes"
            ),
        )
    )
    if forbidden_compilable_suffixes != {".lhs", ".hsc", ".hs-boot"}:
        raise OracleContractError("Haskell policy forbidden compilable suffix set drifted")
    allowed_non_hs_inputs = frozenset(
        _require_unique_strings(
            _require_array(
                source_suffix_policy.get("allowedNonHsConfigurationPaths"),
                field=(
                    "haskellModuleSafetyPolicy.candidateSourceSuffixPolicy."
                    "allowedNonHsConfigurationPaths"
                ),
            ),
            field=(
                "haskellModuleSafetyPolicy.candidateSourceSuffixPolicy."
                "allowedNonHsConfigurationPaths"
            ),
        )
    )
    if allowed_non_hs_inputs != HASKELL_APPROVED_NON_HS_INPUTS:
        raise OracleContractError("Haskell policy non-source configuration path set drifted")
    if source_suffix_policy.get("nonHsEntriesMustHaveRole") != "configuration":
        raise OracleContractError(
            "Haskell policy must classify every non-.hs input as configuration"
        )

    vector_provenance = _require_object(
        policy.get("vectorProvenance"),
        field="haskellModuleSafetyPolicy.vectorProvenance",
    )
    approved_vector_identity: dict[str, Any] = {
        "package": "vector",
        "version": "0.13.2.0",
        "module": "Data.Vector.Unboxed",
        "safeHaskell": "None",
        "sourceSha256Semantics": "official Hackage source archive byte SHA-256",
        "officialArchiveUri": (
            "https://hackage.haskell.org/package/vector-0.13.2.0/vector-0.13.2.0.tar.gz"
        ),
        "officialArchiveSha256": HASKELL_VECTOR_SOURCE_SHA256,
        "stackageSnapshotUri": (
            "https://raw.githubusercontent.com/commercialhaskell/"
            "stackage-snapshots/master/lts/24/50.yaml"
        ),
        "stackageCabalRevisionSha256": (
            "3ec12de580ee31ceac89b314fd00b5057ed40198c0b3d9e9dafa54c1941e6942"
        ),
        "stackageCabalRevisionSize": 8804,
        "pantryTreeSha256": HASKELL_VECTOR_PANTRY_TREE_SHA256,
    }
    for identity_field, expected_value in approved_vector_identity.items():
        if vector_provenance.get(identity_field) != expected_value:
            raise OracleContractError(
                f"Haskell vector approved source identity drifted at {identity_field}"
            )
    require_lower_sha256(
        vector_provenance.get("officialArchiveSha256"),
        field="haskellModuleSafetyPolicy.vectorProvenance.officialArchiveSha256",
    )
    require_lower_sha256(
        vector_provenance.get("stackageCabalRevisionSha256"),
        field=("haskellModuleSafetyPolicy.vectorProvenance.stackageCabalRevisionSha256"),
    )
    require_lower_sha256(
        vector_provenance.get("pantryTreeSha256"),
        field="haskellModuleSafetyPolicy.vectorProvenance.pantryTreeSha256",
    )
    if vector_provenance.get("sourceSha256RequiredAtGate2") is not True:
        raise OracleContractError("Haskell vector source SHA-256 must be required at Gate 2")
    if vector_provenance.get("upstreamTransitiveAllowlistMode") != "exact-set-equality":
        raise OracleContractError(
            "Haskell upstream transitive allowlist must use exact-set equality"
        )
    required_upstream_fields = _require_unique_strings(
        _require_array(
            vector_provenance.get("upstreamTransitiveAllowlistRequiredFields"),
            field=(
                "haskellModuleSafetyPolicy.vectorProvenance."
                "upstreamTransitiveAllowlistRequiredFields"
            ),
        ),
        field=(
            "haskellModuleSafetyPolicy.vectorProvenance.upstreamTransitiveAllowlistRequiredFields"
        ),
    )
    if frozenset(required_upstream_fields) != {
        "package",
        "version",
        "sourceSha256",
        "importPath",
        "provenance",
        "edgeKind",
    }:
        raise OracleContractError("Haskell upstream transitive identity field set drifted")
    allowed_upstream_edge_kinds = frozenset(
        _require_unique_strings(
            _require_array(
                vector_provenance.get("upstreamTransitiveAllowedEdgeKinds"),
                field=(
                    "haskellModuleSafetyPolicy.vectorProvenance.upstreamTransitiveAllowedEdgeKinds"
                ),
            ),
            field=("haskellModuleSafetyPolicy.vectorProvenance.upstreamTransitiveAllowedEdgeKinds"),
        )
    )
    if allowed_upstream_edge_kinds != {"unsafe-import", "compiler-primop"}:
        raise OracleContractError("Haskell upstream transitive edge-kind set drifted")

    def upstream_identity(
        raw_edge: Any,
        *,
        edge_field: str,
        require_allowlisted: bool,
    ) -> tuple[str, ...]:
        edge = _require_object(raw_edge, field=edge_field)
        values: list[str] = []
        for required_field in required_upstream_fields:
            value = edge.get(required_field)
            if not isinstance(value, str) or not value:
                raise OracleContractError(
                    f"{edge_field}.{required_field} must be a non-empty string"
                )
            values.append(value)
        source_sha256 = edge.get("sourceSha256")
        require_lower_sha256(
            source_sha256,
            field=f"{edge_field}.sourceSha256",
        )
        if (
            edge.get("package") != "vector"
            or edge.get("version") != "0.13.2.0"
            or source_sha256 != HASKELL_VECTOR_SOURCE_SHA256
            or edge.get("provenance") != HASKELL_VECTOR_PROVENANCE
        ):
            raise OracleContractError(
                "upstream transitive allowlist exact-set mismatch: "
                f"{edge_field} source identity drifted"
            )
        edge_kind = edge.get("edgeKind")
        import_path = edge.get("importPath")
        if (
            edge_kind not in allowed_upstream_edge_kinds
            or (import_path, edge_kind) not in HASKELL_VECTOR_APPROVED_PATHS
        ):
            raise OracleContractError(
                "upstream transitive allowlist exact-set mismatch: "
                f"{edge_field} path or edge kind drifted"
            )
        if require_allowlisted and edge.get("allowlisted") is not True:
            raise OracleContractError(f"{edge_field}.allowlisted must be true")
        return tuple(values)

    raw_policy_allowlist = _require_array(
        vector_provenance.get("upstreamTransitiveAllowlist"),
        field=("haskellModuleSafetyPolicy.vectorProvenance.upstreamTransitiveAllowlist"),
    )
    policy_allowlist_identities = [
        upstream_identity(
            raw_edge,
            edge_field=(
                f"haskellModuleSafetyPolicy.vectorProvenance.upstreamTransitiveAllowlist[{index}]"
            ),
            require_allowlisted=False,
        )
        for index, raw_edge in enumerate(raw_policy_allowlist)
    ]
    if len(policy_allowlist_identities) != len(set(policy_allowlist_identities)):
        raise OracleContractError(
            "Haskell upstream transitive policy allowlist contains duplicates"
        )
    if {
        (
            identity[required_upstream_fields.index("importPath")],
            identity[required_upstream_fields.index("edgeKind")],
        )
        for identity in policy_allowlist_identities
    } != HASKELL_VECTOR_APPROVED_PATHS:
        raise OracleContractError("Haskell upstream transitive policy allowlist path set drifted")

    module_categories: dict[str, str] = {}
    module_paths: dict[str, tuple[str, str]] = {}
    modules = _require_array(instance.get("modules"), field=f"{field}.modules")
    for index, raw_module in enumerate(modules):
        module = _require_object(raw_module, field=f"{field}.modules[{index}]")
        module_name = module.get("moduleName")
        category = module.get("category")
        if not isinstance(module_name, str) or not module_name:
            raise OracleContractError(f"{field}.modules[{index}].moduleName is invalid")
        if not isinstance(category, str) or not category:
            raise OracleContractError(f"{field}.modules[{index}].category is invalid")
        if module_name in module_categories:
            raise OracleContractError(f"{field}.modules has duplicate moduleName {module_name}")
        module_path = module.get("path")
        source_sha256 = module.get("sourceSha256")
        if not isinstance(module_path, str) or not module_path:
            raise OracleContractError(f"{field}.modules[{index}].path is invalid")
        if module_path in module_paths:
            raise OracleContractError(f"{field}.modules has duplicate module path {module_path}")
        if not isinstance(source_sha256, str):
            raise OracleContractError(f"{field}.modules[{index}].sourceSha256 is invalid")
        module_categories[module_name] = category
        module_paths[module_path] = (module_name, source_sha256)

        if category not in core_categories:
            continue
        extensions = frozenset(
            _require_unique_strings(
                _require_array(
                    module.get("extensions"),
                    field=f"{field}.modules[{index}].extensions",
                ),
                field=f"{field}.modules[{index}].extensions",
            )
        )
        missing = sorted(mandatory_extensions - extensions)
        if missing:
            raise OracleContractError(
                f"{field}.modules[{index}] omits mandatory core extensions: {missing}"
            )
        contradictory = sorted(forbidden_positive_extensions & extensions)
        if contradictory:
            raise OracleContractError(
                f"{field}.modules[{index}] enables forbidden core extensions: {contradictory}"
            )
        if category == "safe-scalar" and "Safe" not in extensions:
            raise OracleContractError(f"{field}.modules[{index}] safe-scalar must enable Safe")
        if category == "audited-pure-vector" and "Safe" in extensions:
            raise OracleContractError(
                f"{field}.modules[{index}] audited-pure-vector must not enable Safe"
            )

    manifest_files = _require_object(
        source_manifest.get("files"),
        field="haskellSourceInputManifest.files",
    )
    haskell_files: dict[str, dict[str, Any]] = {}
    non_hs_input_paths: set[str] = set()
    for path, raw_metadata in manifest_files.items():
        if not isinstance(path, str) or not path:
            raise OracleContractError(
                "haskellSourceInputManifest.files path keys must be non-empty strings"
            )
        metadata = _require_object(
            raw_metadata,
            field=f"haskellSourceInputManifest.files[{path!r}]",
        )
        if any(path.endswith(suffix) for suffix in forbidden_compilable_suffixes):
            raise OracleContractError(f"forbidden Haskell source suffix in manifest path {path!r}")
        if any(path.endswith(suffix) for suffix in allowed_source_suffixes):
            haskell_files[path] = metadata
            continue
        if path not in allowed_non_hs_inputs:
            raise OracleContractError(f"Haskell manifest has an unapproved non-.hs path {path!r}")
        if metadata.get("role") != "configuration":
            raise OracleContractError(f"Haskell non-.hs path {path!r} must have role configuration")
        non_hs_input_paths.add(path)
    if non_hs_input_paths != allowed_non_hs_inputs:
        missing_non_hs_inputs = sorted(allowed_non_hs_inputs - non_hs_input_paths)
        raise OracleContractError(
            f"Haskell non-.hs configuration path set mismatch: missing={missing_non_hs_inputs}"
        )
    module_path_set = set(module_paths)
    manifest_path_set = set(haskell_files)
    if module_path_set != manifest_path_set:
        missing_from_modules = sorted(manifest_path_set - module_path_set)
        missing_from_manifest = sorted(module_path_set - manifest_path_set)
        raise OracleContractError(
            "Haskell source path set mismatch: "
            f"missingFromModules={missing_from_modules}, "
            f"missingFromManifest={missing_from_manifest}"
        )
    for module_path, (_, module_source_sha256) in module_paths.items():
        if haskell_files[module_path].get("sha256") != module_source_sha256:
            raise OracleContractError(
                f"{field}.modules path {module_path!r} sourceSha256 "
                "does not match source-input manifest"
            )

    def require_endpoint_category(
        edge: Mapping[str, Any],
        *,
        edge_field: str,
        endpoint_field: str,
        category_field: str,
    ) -> None:
        endpoint = edge.get(endpoint_field)
        claimed_category = edge.get(category_field)
        if not isinstance(endpoint, str) or endpoint not in module_categories:
            raise OracleContractError(f"{edge_field}.{endpoint_field} is not present in modules")
        if claimed_category != module_categories[endpoint]:
            raise OracleContractError(
                f"{edge_field}.{category_field} does not match modules[{endpoint!r}]"
            )

    direct_imports = _require_array(
        instance.get("candidateDirectImports"),
        field=f"{field}.candidateDirectImports",
    )
    for index, raw_edge in enumerate(direct_imports):
        edge_field = f"{field}.candidateDirectImports[{index}]"
        edge = _require_object(raw_edge, field=edge_field)
        require_endpoint_category(
            edge,
            edge_field=edge_field,
            endpoint_field="fromModule",
            category_field="fromCategory",
        )

    home_edges = _require_array(
        instance.get("candidateHomeModuleEdges"),
        field=f"{field}.candidateHomeModuleEdges",
    )
    for index, raw_edge in enumerate(home_edges):
        edge_field = f"{field}.candidateHomeModuleEdges[{index}]"
        edge = _require_object(raw_edge, field=edge_field)
        require_endpoint_category(
            edge,
            edge_field=edge_field,
            endpoint_field="fromModule",
            category_field="fromCategory",
        )
        require_endpoint_category(
            edge,
            edge_field=edge_field,
            endpoint_field="toModule",
            category_field="toCategory",
        )

    upstream_edges = _require_array(
        instance.get("upstreamTransitiveEdges"),
        field=f"{field}.upstreamTransitiveEdges",
    )
    result_allowlist_identities = [
        upstream_identity(
            raw_edge,
            edge_field=f"{field}.upstreamTransitiveEdges[{index}]",
            require_allowlisted=True,
        )
        for index, raw_edge in enumerate(upstream_edges)
    ]
    policy_identity_set = set(policy_allowlist_identities)
    result_identity_set = set(result_allowlist_identities)
    if (
        len(result_allowlist_identities) != len(result_identity_set)
        or len(result_allowlist_identities) != len(policy_allowlist_identities)
        or result_identity_set != policy_identity_set
    ):
        unknown_count = len(result_identity_set - policy_identity_set)
        stale_count = len(policy_identity_set - result_identity_set)
        duplicate_count = len(result_allowlist_identities) - len(result_identity_set)
        raise OracleContractError(
            "upstream transitive allowlist exact-set mismatch: "
            f"unknown={unknown_count}, stale={stale_count}, "
            f"duplicates={duplicate_count}"
        )
    return len(modules) + len(direct_imports) + len(home_edges) + len(upstream_edges)


def validate_ghc_compatibility_result(
    instance: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    field: str,
) -> int:
    """typed phase status와 command exit/order 증거를 policy phase 순서로 join한다."""

    if instance.get("schemaVersion") != GHC_COMPATIBILITY_RESULT_VERSION:
        raise OracleContractError(f"{field} has an invalid GHC result schemaVersion")
    if policy.get("schemaVersion") != "s1.4x-ghc-compatibility-policy-v1":
        raise OracleContractError("GHC compatibility policy schemaVersion mismatch")
    require_lower_sha256(policy_sha256, field="ghcCompatibilityPolicy.sha256")
    if instance.get("compatibilityPolicySha256") != policy_sha256:
        raise OracleContractError(
            f"{field}.compatibilityPolicySha256 does not match the policy bytes"
        )
    if instance.get("laneId") != policy.get("laneId"):
        raise OracleContractError(f"{field}.laneId does not match the policy")

    phase_order = _require_unique_strings(
        _require_array(policy.get("phaseOrder"), field="ghcCompatibilityPolicy.phaseOrder"),
        field="ghcCompatibilityPolicy.phaseOrder",
    )
    typed_evidence = _require_object(
        policy.get("typedPhaseEvidence"),
        field="ghcCompatibilityPolicy.typedPhaseEvidence",
    )
    qualification_fields = _require_unique_strings(
        _require_array(
            typed_evidence.get("qualificationFields"),
            field="ghcCompatibilityPolicy.typedPhaseEvidence.qualificationFields",
        ),
        field="ghcCompatibilityPolicy.typedPhaseEvidence.qualificationFields",
    )
    replay_fields = _require_unique_strings(
        _require_array(
            typed_evidence.get("replayFields"),
            field="ghcCompatibilityPolicy.typedPhaseEvidence.replayFields",
        ),
        field="ghcCompatibilityPolicy.typedPhaseEvidence.replayFields",
    )
    typed_fields = [*qualification_fields, *replay_fields]
    if len(phase_order) != len(typed_fields):
        raise OracleContractError(
            "GHC compatibility policy phaseOrder and typed fields differ in length"
        )
    phase_field = dict(zip(phase_order, typed_fields, strict=True))
    phase_status: dict[str, str] = {}
    for phase in phase_order:
        typed_field = phase_field[phase]
        phase_result = _require_object(
            instance.get(typed_field),
            field=f"{field}.{typed_field}",
        )
        status = phase_result.get("status")
        if status not in {"PASS", "FAIL", "NOT_RUN"}:
            raise OracleContractError(f"{field}.{typed_field}.status is invalid")
        phase_status[phase] = status

    commands = _require_array(instance.get("commands"), field=f"{field}.commands")
    if not commands:
        raise OracleContractError(f"{field}.commands must contain evidence")
    commands_by_phase: dict[str, list[int]] = {phase: [] for phase in phase_order}
    phase_index = {phase: index for index, phase in enumerate(phase_order)}
    previous_index = -1
    for index, raw_command in enumerate(commands):
        command = _require_object(raw_command, field=f"{field}.commands[{index}]")
        command_phase = command.get("phase")
        if not isinstance(command_phase, str) or command_phase not in phase_index:
            raise OracleContractError(f"{field}.commands[{index}].phase is not in policy")
        current_index = phase_index[command_phase]
        if current_index < previous_index:
            raise OracleContractError(f"{field}.commands phase order is not nondecreasing")
        previous_index = current_index
        exit_code = _require_exact_integer(
            command.get("exitCode"),
            field=f"{field}.commands[{index}].exitCode",
            minimum=0,
        )
        if exit_code > 255:
            raise OracleContractError(f"{field}.commands[{index}].exitCode exceeds 255")
        commands_by_phase[command_phase].append(exit_code)

    result = instance.get("result")
    if not isinstance(result, str):
        raise OracleContractError(f"{field}.result is invalid")
    result_policy = _require_object(
        policy.get("resultPolicy"),
        field="ghcCompatibilityPolicy.resultPolicy",
    )
    rule = _require_object(
        result_policy.get(result),
        field=f"ghcCompatibilityPolicy.resultPolicy[{result!r}]",
    )
    failure_phase = instance.get("failurePhase")
    if result == "PASS":
        if failure_phase is not None:
            raise OracleContractError(f"{field}.failurePhase must be null for PASS")
        expected_status = {phase: "PASS" for phase in phase_order}
    else:
        if not isinstance(failure_phase, str) or failure_phase not in phase_index:
            raise OracleContractError(f"{field}.failurePhase is invalid")
        frozen_failure_phase = rule.get("failurePhase")
        allowed_failure_phases = rule.get("allowedFailurePhases")
        if frozen_failure_phase is not None and failure_phase != frozen_failure_phase:
            raise OracleContractError(f"{field}.failurePhase does not match result policy")
        if allowed_failure_phases is not None:
            allowed = frozenset(
                _require_unique_strings(
                    _require_array(
                        allowed_failure_phases,
                        field=(
                            f"ghcCompatibilityPolicy.resultPolicy[{result!r}].allowedFailurePhases"
                        ),
                    ),
                    field=(f"ghcCompatibilityPolicy.resultPolicy[{result!r}].allowedFailurePhases"),
                )
            )
            if failure_phase not in allowed:
                raise OracleContractError(f"{field}.failurePhase is not allowed by result policy")
        failure_index = phase_index[failure_phase]
        expected_status = {
            phase: (
                "PASS" if index < failure_index else "FAIL" if index == failure_index else "NOT_RUN"
            )
            for index, phase in enumerate(phase_order)
        }
        expected_downstream = phase_order[failure_index + 1 :]
        if instance.get("downstreamNotRun") != expected_downstream:
            raise OracleContractError(f"{field}.downstreamNotRun is not the ordered policy suffix")

    if phase_status != expected_status:
        raise OracleContractError(
            f"{field} typed phase statuses do not match the result/failure phase"
        )
    for phase in phase_order:
        exits = commands_by_phase[phase]
        status = phase_status[phase]
        if status == "PASS" and any(exit_code != 0 for exit_code in exits):
            raise OracleContractError(f"{field} PASS phase {phase} has a nonzero command")
        if status == "FAIL" and not any(exit_code != 0 for exit_code in exits):
            raise OracleContractError(f"{field} FAIL phase {phase} has no nonzero command")
        if status == "NOT_RUN" and exits:
            raise OracleContractError(f"{field} NOT_RUN phase {phase} has command evidence")
    return len(commands)


def validate_json_schemas(contract_root: Path) -> dict[str, str]:
    """Schema와 optional language/report instance를 같은 registry로 offline 검증한다."""

    try:
        from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
        from jsonschema.exceptions import (  # type: ignore[import-untyped]
            SchemaError,
            ValidationError,
        )
        from referencing import Registry, Resource
        from referencing.exceptions import Unresolvable
    except ImportError as exc:
        raise OracleContractError("jsonschema 4.26.0 is required") from exc

    schema_root = contract_root / "schemas"
    schema_paths = sorted(schema_root.glob("*.json"), key=lambda path: path.name.encode())
    if not schema_paths:
        raise OracleContractError("contract/schemas contains no JSON Schema")
    schemas: list[tuple[Path, dict[str, Any]]] = []
    version_to_schema: dict[str, tuple[Path, dict[str, Any]]] = {}
    resources: list[tuple[str, Any]] = []
    for path in schema_paths:
        schema = _require_object(strict_json_load(path), field=path.name)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise OracleContractError(f"invalid JSON Schema {path.name}: {exc.message}") from exc
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise OracleContractError(f"schema {path.name} is missing a non-empty $id")
        try:
            resource = Resource.from_contents(schema)
        except Exception as exc:  # referencing exposes implementation-specific detail types.
            raise OracleContractError(f"unable to register schema {path.name}") from exc
        resources.append((schema_id, resource))
        schemas.append((path, schema))
        version = _schema_version(schema)
        if version is not None:
            if version in version_to_schema:
                raise OracleContractError(f"duplicate schemaVersion const: {version}")
            version_to_schema[version] = (path, schema)
    registry = Registry().with_resources(resources)

    instance_paths = list(contract_root.rglob("*.json"))
    reports_root = contract_root.parent / "reports"
    if reports_root.is_dir():
        instance_paths.extend(reports_root.rglob("*.json"))
    for language in ("scala", "haskell"):
        source_manifest_path = contract_root.parent / language / "source-inputs.v1.json"
        if source_manifest_path.is_file():
            instance_paths.append(source_manifest_path)

    validated: dict[str, str] = {}
    haskell_policy: dict[str, Any] | None = None
    haskell_policy_sha256: str | None = None
    haskell_source_manifest: dict[str, Any] | None = None
    haskell_source_manifest_sha256: str | None = None
    ghc_compatibility_policy: dict[str, Any] | None = None
    ghc_compatibility_policy_sha256: str | None = None
    for path in sorted(set(instance_paths), key=lambda item: item.as_posix().encode()):
        try:
            contract_relative = path.relative_to(contract_root)
        except ValueError:
            contract_relative = None
        if contract_relative is not None:
            if path.parent == schema_root or "invalid" in contract_relative.parts:
                continue
            display_path = contract_relative.as_posix()
        else:
            try:
                display_path = path.relative_to(contract_root.parent).as_posix()
            except ValueError as exc:
                raise OracleContractError("schema instance escaped the S1.4X root") from exc
        instance = strict_json_load(path)
        if not isinstance(instance, dict):
            continue
        version = instance.get("schemaVersion")
        if not isinstance(version, str):
            continue
        selected = version_to_schema.get(version)
        if selected is None:
            if version not in KNOWN_SCHEMALESS_VERSIONS:
                raise OracleContractError(f"{display_path} has no schema for {version!r}")
            continue
        schema_path, schema = selected
        try:
            Draft202012Validator(
                schema,
                registry=registry,
                format_checker=FormatChecker(),
            ).validate(instance)
        except (ValidationError, Unresolvable) as exc:
            message = getattr(exc, "message", str(exc))
            raise OracleContractError(
                f"schema validation failed for {display_path} against {schema_path.name}: {message}"
            ) from exc
        validate_executable_schema_annotations(
            schema,
            instance,
            field=display_path,
        )
        if version == HASKELL_MODULE_SAFETY_RESULT_VERSION:
            policy_path = contract_root / "haskell-module-safety-policy.v1.json"
            if haskell_policy is None or haskell_policy_sha256 is None:
                if not policy_path.is_file():
                    raise OracleContractError(
                        "Haskell result requires haskell-module-safety-policy.v1.json"
                    )
                haskell_policy = _require_object(
                    strict_json_load(policy_path),
                    field=policy_path.name,
                )
                haskell_policy_sha256 = sha256_file(policy_path)
            source_manifest_path = contract_root.parent / "haskell" / "source-inputs.v1.json"
            if haskell_source_manifest is None or haskell_source_manifest_sha256 is None:
                if not source_manifest_path.is_file():
                    raise OracleContractError(
                        "Haskell result requires haskell/source-inputs.v1.json"
                    )
                haskell_source_manifest = _require_object(
                    strict_json_load(source_manifest_path),
                    field="haskell/source-inputs.v1.json",
                )
                haskell_source_manifest_sha256 = sha256_file(source_manifest_path)
            validate_haskell_module_safety_result(
                instance,
                policy=haskell_policy,
                policy_sha256=haskell_policy_sha256,
                source_manifest=haskell_source_manifest,
                source_manifest_sha256=haskell_source_manifest_sha256,
                field=display_path,
            )
        if version == GHC_COMPATIBILITY_RESULT_VERSION:
            policy_path = contract_root / "ghc-compatibility-policy.v1.json"
            if ghc_compatibility_policy is None or ghc_compatibility_policy_sha256 is None:
                if not policy_path.is_file():
                    raise OracleContractError(
                        "GHC result requires ghc-compatibility-policy.v1.json"
                    )
                ghc_compatibility_policy = _require_object(
                    strict_json_load(policy_path),
                    field=policy_path.name,
                )
                ghc_compatibility_policy_sha256 = sha256_file(policy_path)
            validate_ghc_compatibility_result(
                instance,
                policy=ghc_compatibility_policy,
                policy_sha256=ghc_compatibility_policy_sha256,
                field=display_path,
            )
        validated[display_path] = schema_path.name
    return validated


def validate_registries(
    contract_root: Path,
) -> tuple[dict[str, dict[str, Any]], frozenset[str]]:
    """20-function/32-error exact set과 양방향 적용 관계를 검증한다."""

    function_registry = _require_object(
        strict_json_load(contract_root / "function-registry.v1.json"),
        field="function-registry",
    )
    if function_registry.get("schemaVersion") != "s1.4x-function-registry-v1":
        raise OracleContractError("function registry schemaVersion mismatch")
    functions = _require_array(function_registry.get("entries"), field="function-registry.entries")
    if function_registry.get("functionCount") != 20 or len(functions) != 20:
        raise OracleContractError("function registry must contain exactly 20 functions")
    by_function: dict[str, dict[str, Any]] = {}
    by_track: dict[str, set[str]] = {"s1.4": set(), "s1.4r": set()}
    for index, raw_entry in enumerate(functions):
        entry = _require_object(raw_entry, field=f"function-registry.entries[{index}]")
        function_id = entry.get("functionId")
        track = entry.get("track")
        if not isinstance(function_id, str) or track not in by_track:
            raise OracleContractError("function registry contains invalid functionId/track")
        if function_id in by_function:
            raise OracleContractError(f"duplicate functionId: {function_id}")
        parameters = _require_array(entry.get("parameters"), field=f"{function_id}.parameters")
        parameter_names = _require_unique_strings(
            (
                _require_object(parameter, field=f"{function_id}.parameter").get("name")
                for parameter in parameters
            ),
            field=f"{function_id}.parameters.name",
        )
        if len(parameter_names) != len(parameters):
            raise OracleContractError(f"{function_id} parameter registry mismatch")
        applicable = frozenset(
            _require_unique_strings(
                _require_array(
                    entry.get("applicableErrorCodes"),
                    field=f"{function_id}.applicableErrorCodes",
                ),
                field=f"{function_id}.applicableErrorCodes",
            )
        )
        entry["_validatedApplicableCodes"] = applicable
        by_function[function_id] = entry
        by_track[track].add(function_id)
    if by_track["s1.4"] != set(S1_4_FUNCTIONS):
        raise OracleContractError("S1.4 function set drifted from the frozen 11-function set")
    if by_track["s1.4r"] != set(S1_4R_FUNCTIONS):
        raise OracleContractError("S1.4R function set drifted from the frozen 9-function set")

    error_registry = _require_object(
        strict_json_load(contract_root / "error-registry.v1.json"),
        field="error-registry",
    )
    if error_registry.get("schemaVersion") != "s1.4x-error-registry-v1":
        raise OracleContractError("error registry schemaVersion mismatch")
    errors = _require_array(error_registry.get("entries"), field="error-registry.entries")
    if error_registry.get("errorCodeCount") != 32 or len(errors) != 32:
        raise OracleContractError("error registry must contain exactly 32 error codes")
    if error_registry.get("trackCounts") != {"s1.4": 19, "s1.4r": 13}:
        raise OracleContractError("error registry trackCounts must be exactly 19/13")
    by_error: dict[str, dict[str, Any]] = {}
    track_errors: dict[str, set[str]] = {"s1.4": set(), "s1.4r": set()}
    for index, raw_entry in enumerate(errors):
        entry = _require_object(raw_entry, field=f"error-registry.entries[{index}]")
        code = entry.get("code")
        track = entry.get("track")
        if not isinstance(code, str) or track not in track_errors:
            raise OracleContractError("error registry contains invalid code/track")
        if code in by_error:
            raise OracleContractError(f"duplicate error code: {code}")
        applicable_ids = frozenset(
            _require_unique_strings(
                _require_array(
                    entry.get("applicableFunctionIds"),
                    field=f"{code}.applicableFunctionIds",
                ),
                field=f"{code}.applicableFunctionIds",
            )
        )
        if not applicable_ids or not applicable_ids <= by_function.keys():
            raise OracleContractError(f"{code} has unknown or empty applicableFunctionIds")
        if any(by_function[item]["track"] != track for item in applicable_ids):
            raise OracleContractError(f"{code} crosses the frozen S1.4/S1.4R track boundary")
        entry["_validatedApplicableFunctions"] = applicable_ids
        by_error[code] = entry
        track_errors[track].add(code)
    if track_errors["s1.4"] != set(S1_4_ERROR_CODES):
        raise OracleContractError("S1.4 error set drifted from the frozen 19-code set")
    if track_errors["s1.4r"] != set(S1_4R_ERROR_CODES):
        raise OracleContractError("S1.4R error set drifted from the frozen 13-code set")
    for function_id, entry in by_function.items():
        applicable_codes = entry["_validatedApplicableCodes"]
        if not applicable_codes or not applicable_codes <= by_error.keys():
            raise OracleContractError(f"{function_id} has unknown applicableErrorCodes")
        reverse_codes = {
            code
            for code, error in by_error.items()
            if function_id in error["_validatedApplicableFunctions"]
        }
        if applicable_codes != reverse_codes:
            raise OracleContractError(f"{function_id} error applicability is not bidirectional")
    return by_function, frozenset(by_error)


def validate_property_plan(
    contract_root: Path,
    *,
    functions: Mapping[str, dict[str, Any]],
) -> int:
    """25-property plan의 필수 invariant ID와 frozen function 참조를 검증한다."""

    path = contract_root / "property-plan.v1.json"
    plan = _require_object(strict_json_load(path), field=path.name)
    if plan.get("schemaVersion") != "s1.4x-property-plan-v1":
        raise OracleContractError("property plan schemaVersion mismatch")
    properties = _require_array(plan.get("properties"), field="property-plan.properties")
    if len(properties) != 25:
        raise OracleContractError("property plan must contain exactly 25 properties")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw_property in enumerate(properties):
        property_entry = _require_object(
            raw_property,
            field=f"property-plan.properties[{index}]",
        )
        property_id = property_entry.get("propertyId")
        if not isinstance(property_id, str) or not property_id or property_id in by_id:
            raise OracleContractError("property plan has invalid or duplicate propertyId")
        function_ids = frozenset(
            _require_unique_strings(
                _require_array(
                    property_entry.get("functionIds"),
                    field=f"{property_id}.functionIds",
                ),
                field=f"{property_id}.functionIds",
            )
        )
        if not function_ids or not function_ids <= functions.keys():
            raise OracleContractError(f"{property_id} references an unknown functionId")
        by_id[property_id] = property_entry
    missing = sorted(REQUIRED_PROPERTY_IDS - by_id.keys())
    if missing:
        raise OracleContractError(f"property plan omits required invariant IDs: {missing}")
    return len(by_id)


def _validate_provenance_integer_tokens(value: Any, *, field: str) -> None:
    if not isinstance(value, dict):
        return
    if value.get("schema_version") != "s1.4r-effective-trials-v1":
        return
    for name in ("raw_trial_count", "effective_trial_count", "variance_ddof"):
        _require_exact_integer(value.get(name), field=f"{field}.{name}")


def validate_request_fixtures(
    contract_root: Path,
    functions: Mapping[str, dict[str, Any]],
    errors: frozenset[str],
) -> dict[str, str]:
    """canonical request의 registry-aware parameter·integer·binary 참조를 검증한다."""

    allowed_nonfinite: dict[str, str] = {}
    request_paths = sorted(
        (
            path
            for path in contract_root.rglob("*.json")
            if "invalid" not in path.relative_to(contract_root).parts
        ),
        key=lambda path: path.as_posix().encode(),
    )
    for path in request_paths:
        loaded = strict_json_load(path)
        if not isinstance(loaded, dict) or loaded.get("schemaVersion") != REQUEST_VERSION:
            continue
        cases = _require_array(loaded.get("cases"), field=f"{path.name}.cases")
        seen: set[str] = set()
        for case_index, raw_case in enumerate(cases):
            case = _require_object(raw_case, field=f"{path.name}.cases[{case_index}]")
            fixture_id = case.get("fixtureId")
            function_id = case.get("functionId")
            if not isinstance(fixture_id, str) or fixture_id in seen:
                raise OracleContractError(f"{path.name} has invalid or duplicate fixtureId")
            seen.add(fixture_id)
            if not isinstance(function_id, str) or function_id not in functions:
                raise OracleContractError(f"{path.name} references unknown functionId")
            entry = functions[function_id]
            arguments = _require_object(
                case.get("arguments"),
                field=f"{path.name}.{fixture_id}.arguments",
            )
            parameters = entry["parameters"]
            parameter_names = [parameter["name"] for parameter in parameters]
            required_names = {
                parameter["name"] for parameter in parameters if parameter.get("required") is True
            }
            if not required_names <= arguments.keys() or not arguments.keys() <= set(
                parameter_names
            ):
                raise OracleContractError(
                    f"{fixture_id} argument names do not match function registry"
                )
            expected_error = case.get("expectedSemanticError")
            if expected_error is not None:
                if expected_error not in errors:
                    raise OracleContractError(
                        f"{fixture_id} references unknown semantic error code"
                    )
                if expected_error not in entry["_validatedApplicableCodes"]:
                    raise OracleContractError(
                        f"{fixture_id} semantic error is not applicable to {function_id}"
                    )
            by_name = {parameter["name"]: parameter for parameter in parameters}
            for argument_name, value in arguments.items():
                parameter = by_name[argument_name]
                if parameter.get("wireValueKind") == "integerToken":
                    _require_exact_integer(
                        value,
                        field=f"{fixture_id}.arguments.{argument_name}",
                    )
                _validate_provenance_integer_tokens(
                    value,
                    field=f"{fixture_id}.arguments.{argument_name}",
                )
                if isinstance(value, dict) and value.get("kind") == "binaryFloat64":
                    manifest_name = require_safe_basename(
                        value.get("manifestFile"),
                        field=f"{fixture_id}.{argument_name}.manifestFile",
                    )
                    manifest_path = resolve_within(
                        contract_root / "fixtures" / "large",
                        manifest_name,
                        must_exist=True,
                    )
                    manifest = _require_object(
                        strict_json_load(manifest_path),
                        field=manifest_name,
                    )
                    if manifest.get("argumentName") != argument_name:
                        raise OracleContractError(
                            f"{fixture_id} binary manifest argumentName mismatch"
                        )
                    if expected_error in SEMANTIC_NONFINITE_CODES:
                        allowed_nonfinite[manifest_name] = expected_error
    return allowed_nonfinite


def _product_exact(values: Sequence[int]) -> int:
    result = 1
    for value in values:
        result *= value
    return result


def _binary_values_are_finite(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            while payload := stream.read(8 * 8192):
                if len(payload) % 8 != 0:
                    return False
                if any(not math.isfinite(item[0]) for item in struct.iter_unpack("<d", payload)):
                    return False
    except OSError as exc:
        raise OracleContractError(f"unable to read generated binary {path.name!r}") from exc
    return True


def _first_schema_failure(
    schema_path: Path,
    instance: Any,
) -> tuple[str, tuple[str | int, ...]] | None:
    """한 instance의 첫 Draft 2020-12 failure를 deterministic path 순서로 반환한다."""

    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError as exc:
        raise OracleContractError("jsonschema 4.26.0 is required") from exc
    schema = _require_object(strict_json_load(schema_path), field=schema_path.name)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise OracleContractError(f"invalid JSON Schema {schema_path.name}: {exc.message}") from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: error.json_path,
    )
    if not errors:
        return None
    first = errors[0]
    return first.message, tuple(first.path)


def _validate_negative_manifest_semantics(
    manifest: Mapping[str, Any],
    *,
    field: str,
) -> tuple[str, int, str]:
    """schema 이후 count/shape/byte cap을 arbitrary-size integer로 검증한다."""

    file_name = require_safe_basename(manifest.get("fileName"), field=f"{field}.fileName")
    shape = _require_array(manifest.get("shape"), field=f"{field}.shape")
    if len(shape) != 1:
        raise OracleContractError(f"{field}.shape rank must be exactly one")
    dimensions = [
        _require_exact_integer(value, field=f"{field}.shape", minimum=1) for value in shape
    ]
    count = _require_exact_integer(
        manifest.get("count"),
        field=f"{field}.count",
        minimum=1,
    )
    byte_length = _require_exact_integer(
        manifest.get("byteLength"),
        field=f"{field}.byteLength",
        minimum=8,
    )
    # Python int의 arbitrary precision을 그대로 사용해 machine-word wraparound를 허용하지 않는다.
    if count != _product_exact(dimensions):
        raise OracleContractError(f"{field}.count != product(shape)")
    if byte_length != count * 8:
        raise OracleContractError(f"{field}.byteLength != count * 8")
    if byte_length > ALLOCATION_CAP_BYTES:
        raise OracleContractError(f"{field} exceeds the allocation cap")
    expected_sha = require_lower_sha256(
        manifest.get("sha256"),
        field=f"{field}.sha256",
    )
    return file_name, byte_length, expected_sha


def _literal_negative_payload(manifest: Mapping[str, Any], *, field: str) -> bytes:
    """tracked raw 대신 manifest에 동결된 lowercase literal bits를 materialize한다."""

    generator = _require_object(manifest.get("generator"), field=f"{field}.generator")
    if generator.get("algorithm") != "literal-ieee754-bits":
        raise OracleContractError(
            f"{field} reaches binary validation without literal-ieee754-bits provenance"
        )
    if generator.get("generatorVersion") != "s1.4x-literal-ieee754-bits-v1":
        raise OracleContractError(f"{field} literal generatorVersion mismatch")
    payload_hex = generator.get("payloadHex")
    if (
        not isinstance(payload_hex, str)
        or not payload_hex
        or len(payload_hex) % 2 != 0
        or any(character not in "0123456789abcdef" for character in payload_hex)
    ):
        raise OracleContractError(f"{field}.generator.payloadHex must be lowercase bytes")
    return bytes.fromhex(payload_hex)


def _evaluate_negative_fixture(
    *,
    contract_root: Path,
    entry: Mapping[str, Any],
    fixture_path: Path,
) -> dict[str, str]:
    """catalog 기대값을 보지 않고 fixture bytes에서 실제 layer/disposition을 판정한다."""

    file_name = fixture_path.name
    if file_name.startswith("request-"):
        try:
            instance = strict_json_load(fixture_path)
        except OracleContractError as exc:
            return {
                "validationLayer": "strict-json-pre-parser",
                "disposition": "exit-64-request_invalid",
                "reason": str(exc),
            }
        failure = _first_schema_failure(
            contract_root / "schemas" / "canonical-request.schema.json",
            instance,
        )
        if failure is None:
            raise OracleContractError(f"{file_name} does not fail request validation")
        return {
            "validationLayer": "request-schema",
            "disposition": "exit-64-request_invalid",
            "reason": failure[0],
        }

    instance = _require_object(strict_json_load(fixture_path), field=file_name)
    failure = _first_schema_failure(
        contract_root / "schemas" / "binary-array-manifest.schema.json",
        instance,
    )
    if failure is not None:
        layer = (
            "manifest-schema-and-path"
            if failure[1] and failure[1][0] == "fileName"
            else "manifest-schema"
        )
        return {
            "validationLayer": layer,
            "disposition": "exit-65-manifest_invalid",
            "reason": failure[0],
        }
    try:
        binary_name, expected_length, expected_sha = _validate_negative_manifest_semantics(
            instance, field=file_name
        )
    except OracleContractError as exc:
        return {
            "validationLayer": "manifest-semantic",
            "disposition": "exit-65-manifest_invalid",
            "reason": str(exc),
        }

    payload = _literal_negative_payload(instance, field=file_name)
    placement = entry.get("binaryPlacement", "regular-file")
    with TemporaryDirectory(prefix="s1-4x-invalid-binary-") as temporary:
        temporary_root = Path(temporary)
        fixture_root = temporary_root / "fixture-root"
        fixture_root.mkdir()
        candidate = fixture_root / binary_name
        if placement == "symlink-escape":
            outside = temporary_root / "outside.f64le"
            outside.write_bytes(payload)
            candidate.symlink_to(outside)
        elif placement == "regular-file":
            candidate.write_bytes(payload)
        else:
            raise OracleContractError(f"unsupported binaryPlacement: {placement!r}")

        try:
            resolved = resolve_within(fixture_root, binary_name, must_exist=True)
        except OracleContractError as exc:
            return {
                "validationLayer": "manifest-schema-and-path",
                "disposition": "exit-65-manifest_invalid",
                "reason": str(exc),
            }
        if candidate.is_symlink() or not resolved.is_file():
            return {
                "validationLayer": "manifest-schema-and-path",
                "disposition": "exit-65-manifest_invalid",
                "reason": f"{binary_name} must be a regular non-symlink file",
            }
        if resolved.stat().st_size != expected_length:
            return {
                "validationLayer": "binary-integrity",
                "disposition": "exit-65-binary_invalid",
                "reason": f"{binary_name} byte length mismatch",
            }
        if sha256_file(resolved) != expected_sha:
            return {
                "validationLayer": "binary-integrity",
                "disposition": "exit-65-binary_invalid",
                "reason": f"{binary_name} SHA-256 mismatch",
            }
        if not _binary_values_are_finite(resolved):
            expected_error = instance.get("expectedSemanticError")
            if expected_error not in SEMANTIC_NONFINITE_CODES:
                raise OracleContractError(
                    f"{file_name} has non-finite bits without an allowed semantic error"
                )
            return {
                "validationLayer": "decoded-semantic",
                "disposition": f"exit-0-{expected_error}",
                "reason": f"{binary_name} decodes to a non-finite numeric input",
            }
    raise OracleContractError(f"{file_name} unexpectedly passes every validation layer")


def validate_negative_fixtures(contract_root: Path) -> dict[str, dict[str, str]]:
    """negative catalog의 각 파일이 선언한 최초 failure layer와 disposition을 재현한다."""

    invalid_root = contract_root / "fixtures" / "invalid"
    catalog_path = invalid_root / "invalid-fixtures.v1.json"
    catalog = _require_object(strict_json_load(catalog_path), field=catalog_path.name)
    if set(catalog) != {"schemaVersion", "entries"}:
        raise OracleContractError("invalid fixture catalog has wrong exact fields")
    if catalog.get("schemaVersion") != "s1.4x-invalid-fixture-catalog-v1":
        raise OracleContractError("invalid fixture catalog schemaVersion mismatch")
    entries = _require_array(catalog.get("entries"), field="invalid-fixtures.entries")
    outcomes: dict[str, dict[str, str]] = {}
    seen_files: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _require_object(raw_entry, field=f"invalid-fixtures.entries[{index}]")
        allowed_fields = {
            "fixtureId",
            "file",
            "validationLayer",
            "expectedDisposition",
            "binaryPlacement",
            "note",
        }
        required_fields = {
            "fixtureId",
            "file",
            "validationLayer",
            "expectedDisposition",
        }
        if not required_fields <= entry.keys() or not entry.keys() <= allowed_fields:
            raise OracleContractError(
                f"invalid fixture catalog entry {index} has wrong exact fields"
            )
        fixture_id = entry.get("fixtureId")
        if (
            not isinstance(fixture_id, str)
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", fixture_id)
            or fixture_id in outcomes
        ):
            raise OracleContractError("invalid fixture catalog has invalid or duplicate fixtureId")
        catalog_file = require_safe_basename(
            entry.get("file"),
            field=f"{fixture_id}.file",
        )
        if catalog_file in seen_files:
            raise OracleContractError(f"invalid fixture catalog repeats file {catalog_file!r}")
        seen_files.add(catalog_file)
        fixture_path = resolve_within(invalid_root, catalog_file, must_exist=True)
        if fixture_path.is_symlink() or not fixture_path.is_file():
            raise OracleContractError(f"negative fixture must be a regular file: {catalog_file}")
        layer = entry.get("validationLayer")
        disposition = entry.get("expectedDisposition")
        if (
            not isinstance(layer, str)
            or layer not in NEGATIVE_LAYER_DISPOSITIONS
            or disposition not in NEGATIVE_LAYER_DISPOSITIONS[layer]
        ):
            raise OracleContractError(f"{fixture_id} has an invalid layer/disposition pair")
        if fixture_id == "manifest-symlink-escape":
            if entry.get("binaryPlacement") != "symlink-escape":
                raise OracleContractError("symlink escape fixture must declare its placement")
        elif "binaryPlacement" in entry:
            raise OracleContractError(f"{fixture_id} must not override binaryPlacement")
        note = entry.get("note")
        if note is not None and (not isinstance(note, str) or not note.strip()):
            raise OracleContractError(f"{fixture_id}.note must be a non-empty string")

        actual = _evaluate_negative_fixture(
            contract_root=contract_root,
            entry=entry,
            fixture_path=fixture_path,
        )
        if actual["validationLayer"] != layer or actual["disposition"] != disposition:
            raise OracleContractError(
                f"{fixture_id} negative outcome mismatch: "
                f"expected={layer}/{disposition}, "
                f"actual={actual['validationLayer']}/{actual['disposition']}"
            )
        outcomes[fixture_id] = actual
    if outcomes.keys() != REQUIRED_NEGATIVE_FIXTURE_IDS:
        missing = sorted(REQUIRED_NEGATIVE_FIXTURE_IDS - outcomes.keys())
        extra = sorted(outcomes.keys() - REQUIRED_NEGATIVE_FIXTURE_IDS)
        raise OracleContractError(
            f"invalid fixture catalog coverage mismatch: missing={missing}, extra={extra}"
        )
    return outcomes


def validate_semantic_error_fixtures(
    contract_root: Path,
    *,
    functions: Mapping[str, dict[str, Any]],
    errors: frozenset[str],
) -> int:
    """15개 semantic-invalid request와 frozen stable error 결과를 exact 대응시킨다."""

    invalid_root = contract_root / "fixtures" / "invalid"
    request_path = invalid_root / "semantic-errors.v1.json"
    expected_path = invalid_root / "semantic-errors.expected.v1.json"
    request = _require_object(strict_json_load(request_path), field=request_path.name)
    expected = _require_object(strict_json_load(expected_path), field=expected_path.name)
    for schema_name, instance, label in (
        ("canonical-request.schema.json", request, request_path.name),
        ("canonical-result.schema.json", expected, expected_path.name),
    ):
        failure = _first_schema_failure(
            contract_root / "schemas" / schema_name,
            instance,
        )
        if failure is not None:
            raise OracleContractError(f"{label} schema validation failed: {failure[0]}")

    if expected.get("implementation") != "python-frozen-oracle":
        raise OracleContractError("semantic error expected implementation must be frozen oracle")
    if expected.get("requestId") != request.get("requestId"):
        raise OracleContractError("semantic error request/result requestId mismatch")
    cases = _require_array(request.get("cases"), field="semantic-errors.cases")
    results = _require_array(expected.get("results"), field="semantic-errors.expected.results")
    fixture_ids = tuple(
        _require_object(case, field="semantic-errors.case").get("fixtureId") for case in cases
    )
    if fixture_ids != REQUIRED_SEMANTIC_ERROR_FIXTURE_IDS:
        raise OracleContractError("semantic error fixture ID/order drifted from frozen corpus")
    if len(results) != len(cases):
        raise OracleContractError("semantic error result count mismatch")
    for index, (raw_case, raw_result) in enumerate(zip(cases, results, strict=True)):
        case = _require_object(raw_case, field=f"semantic-errors.cases[{index}]")
        result = _require_object(raw_result, field=f"semantic-errors.results[{index}]")
        function_id = case.get("functionId")
        expected_code = case.get("expectedSemanticError")
        if not isinstance(function_id, str) or function_id not in functions:
            raise OracleContractError("semantic error fixture references unknown functionId")
        if (
            not isinstance(expected_code, str)
            or expected_code not in errors
            or expected_code not in functions[function_id]["_validatedApplicableCodes"]
        ):
            raise OracleContractError("semantic error fixture has inapplicable error code")
        if (
            result.get("fixtureId") != case.get("fixtureId")
            or result.get("functionId") != function_id
            or result.get("status") != "error"
            or result.get("errorCode") != expected_code
        ):
            raise OracleContractError(f"semantic error expected result mismatch at index {index}")
    return len(cases)


def validate_binary_manifests(
    contract_root: Path,
    *,
    allowed_nonfinite: Mapping[str, str],
) -> int:
    """rank-1 Float64 LE manifest와 존재하는 generated raw의 size/hash/finite를 검증한다."""

    large_root = contract_root / "fixtures" / "large"
    manifests = sorted(large_root.glob("*.manifest.json"), key=lambda path: path.name.encode())
    seen_fixture_ids: set[str] = set()
    seen_file_names: set[str] = set()
    for path in manifests:
        manifest = _require_object(strict_json_load(path), field=path.name)
        if manifest.get("schemaVersion") != BINARY_MANIFEST_VERSION:
            raise OracleContractError(f"{path.name} has wrong schemaVersion")
        for field, expected in {
            "encoding": "ieee754-binary64",
            "dtype": "float64",
            "byteOrder": "little",
            "arrayOrder": "C",
        }.items():
            if manifest.get(field) != expected:
                raise OracleContractError(f"{path.name}.{field} must be {expected!r}")
        fixture_id = manifest.get("fixtureId")
        if not isinstance(fixture_id, str) or not fixture_id or fixture_id in seen_fixture_ids:
            raise OracleContractError("binary manifests contain invalid or duplicate fixtureId")
        seen_fixture_ids.add(fixture_id)
        file_name = require_safe_basename(manifest.get("fileName"))
        if file_name in seen_file_names:
            raise OracleContractError(f"duplicate binary fileName: {file_name}")
        seen_file_names.add(file_name)
        shape = _require_array(manifest.get("shape"), field=f"{path.name}.shape")
        if len(shape) != 1:
            raise OracleContractError(f"{path.name}.shape rank must be exactly one")
        dimensions = [
            _require_exact_integer(value, field=f"{path.name}.shape", minimum=1) for value in shape
        ]
        count = _require_exact_integer(manifest.get("count"), field=f"{path.name}.count", minimum=1)
        byte_length = _require_exact_integer(
            manifest.get("byteLength"),
            field=f"{path.name}.byteLength",
            minimum=8,
        )
        if count != _product_exact(dimensions):
            raise OracleContractError(f"{path.name}.count != product(shape)")
        if byte_length != count * 8:
            raise OracleContractError(f"{path.name}.byteLength != count * 8")
        if byte_length > ALLOCATION_CAP_BYTES:
            raise OracleContractError(f"{path.name} exceeds the allocation cap")
        expected_sha = require_lower_sha256(manifest.get("sha256"), field=f"{path.name}.sha256")
        generated_path = large_root / "generated" / file_name
        if generated_path.exists():
            if generated_path.is_symlink() or not generated_path.is_file():
                raise OracleContractError(f"{file_name} must be a regular non-symlink file")
            if generated_path.stat().st_size != byte_length:
                raise OracleContractError(f"{file_name} byte length mismatch")
            if sha256_file(generated_path) != expected_sha:
                raise OracleContractError(f"{file_name} SHA-256 mismatch")
            if path.name not in allowed_nonfinite and not _binary_values_are_finite(generated_path):
                raise OracleContractError(f"{file_name} contains non-finite success input")
    return len(manifests)


def _reject_negative_zero(value: Any, *, path: str = "$") -> None:
    if isinstance(value, float):
        if value == 0.0 and math.copysign(1.0, value) < 0.0:
            raise OracleContractError(f"negative zero is not normalized at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_negative_zero(item, path=f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_negative_zero(item, path=f"{path}.{key}")


def validate_expected_results(
    contract_root: Path,
    *,
    functions: Mapping[str, dict[str, Any]],
    errors: frozenset[str],
) -> int:
    """expected batch의 exact result IDs, finite/zero normalization, error registry를 검증한다."""

    request_path = contract_root / "fixtures" / "small" / "canonical-inputs.v1.json"
    result_path = contract_root / "fixtures" / "expected" / "canonical-results.v1.json"
    if not request_path.is_file() and not result_path.is_file():
        return 0
    if not request_path.is_file() or not result_path.is_file():
        raise OracleContractError("canonical request/result fixtures must be committed together")
    request = _require_object(strict_json_load(request_path), field=request_path.name)
    batch = _require_object(strict_json_load(result_path), field=result_path.name)
    if batch.get("schemaVersion") != RESULT_BATCH_VERSION:
        raise OracleContractError("canonical result schemaVersion mismatch")
    if batch.get("requestId") != request.get("requestId"):
        raise OracleContractError("canonical result requestId mismatch")
    cases = _require_array(request.get("cases"), field="canonical request cases")
    results = _require_array(batch.get("results"), field="canonical results")
    expected_pairs = [(case.get("fixtureId"), case.get("functionId")) for case in cases]
    actual_pairs = [(result.get("fixtureId"), result.get("functionId")) for result in results]
    if actual_pairs != expected_pairs:
        raise OracleContractError("canonical results must preserve exact request ID/order")
    for index, result in enumerate(results):
        item = _require_object(result, field=f"results[{index}]")
        if item.get("functionId") not in functions:
            raise OracleContractError("canonical result has unknown functionId")
        status = item.get("status")
        if status == "ok":
            if set(item) != {"schemaVersion", "functionId", "fixtureId", "status", "values"}:
                raise OracleContractError("canonical ok result has wrong exact field set")
        elif status == "error":
            if set(item) != {"schemaVersion", "functionId", "fixtureId", "status", "errorCode"}:
                raise OracleContractError("canonical error result has wrong exact field set")
            if item.get("errorCode") not in errors:
                raise OracleContractError("canonical result has unknown errorCode")
        else:
            raise OracleContractError("canonical result has invalid status")
        _reject_negative_zero(item, path=f"results[{index}]")
    return len(results)


def validate_sha256_sidecars(contract_root: Path) -> int:
    """tracked `.sha256` sidecar를 sibling JSON byte hash와 exact 비교한다."""

    sidecars = sorted(contract_root.rglob("*.sha256"), key=lambda path: path.as_posix().encode())
    for sidecar in sidecars:
        try:
            text = sidecar.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise OracleContractError(f"invalid SHA-256 sidecar {sidecar.name}") from exc
        fields = text.split()
        if not fields or not SHA256_PATTERN.fullmatch(fields[0]):
            raise OracleContractError(f"invalid SHA-256 sidecar content: {sidecar.name}")
        if len(fields) == 1:
            target = sidecar.with_suffix(".json")
        elif len(fields) == 2:
            file_name = fields[1].removeprefix("*")
            require_safe_basename(file_name, field=f"{sidecar.name}.fileName")
            target = sidecar.parent / file_name
        else:
            raise OracleContractError(f"invalid SHA-256 sidecar shape: {sidecar.name}")
        if not target.is_file() or target.is_symlink():
            raise OracleContractError(f"SHA-256 sidecar target missing: {sidecar.name}")
        if sha256_file(target) != fields[0]:
            raise OracleContractError(f"SHA-256 sidecar mismatch: {sidecar.name}")
    return len(sidecars)


def _normalize_runtime_array(value: Any, *, field: str) -> list[Any]:
    """순서가 의미 없는 dependency 배열을 canonical JSON byte 순서로 정규화한다."""

    values = _require_array(value, field=field)
    normalized = [copy.deepcopy(item) for item in values]
    try:
        normalized.sort(key=lambda item: canonical_json_bytes(item, trailing_newline=False))
    except OracleContractError as exc:
        raise OracleContractError(f"{field} contains a non-canonical runtime value") from exc
    return normalized


def _project_runtime_projection(path: Path) -> dict[str, Any]:
    """pyproject에서 dependency resolution과 package runtime에 영향 주는 필드만 추출한다."""

    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise OracleContractError(f"invalid project runtime TOML: {path.name}") from exc

    project = _require_object(document.get("project"), field="pyproject.project")
    projected_project: dict[str, Any] = {}
    requires_python = project.get("requires-python")
    if requires_python is not None:
        if not isinstance(requires_python, str) or not requires_python:
            raise OracleContractError("pyproject.project.requires-python must be a string")
        projected_project["requires-python"] = requires_python
    if "dependencies" in project:
        projected_project["dependencies"] = _normalize_runtime_array(
            project["dependencies"],
            field="pyproject.project.dependencies",
        )
    if "optional-dependencies" in project:
        optional = _require_object(
            project["optional-dependencies"],
            field="pyproject.project.optional-dependencies",
        )
        projected_project["optional-dependencies"] = {
            group: _normalize_runtime_array(
                dependencies,
                field=f"pyproject.project.optional-dependencies.{group}",
            )
            for group, dependencies in optional.items()
        }
    if "dynamic" in project:
        dynamic = _require_unique_strings(
            _require_array(project["dynamic"], field="pyproject.project.dynamic"),
            field="pyproject.project.dynamic",
        )
        if {"dependencies", "optional-dependencies"}.intersection(dynamic):
            raise OracleContractError(
                "dynamic dependency metadata cannot be runtime-projection locked"
            )
        projected_project["dynamic"] = sorted(dynamic, key=str.encode)

    projection: dict[str, Any] = {
        "schemaVersion": PROJECT_RUNTIME_PROJECTION_VERSION,
        "project": projected_project,
    }
    if "build-system" in document:
        build_system = copy.deepcopy(
            _require_object(document["build-system"], field="pyproject.build-system")
        )
        if "requires" in build_system:
            build_system["requires"] = _normalize_runtime_array(
                build_system["requires"],
                field="pyproject.build-system.requires",
            )
        projection["build-system"] = build_system
    if "dependency-groups" in document:
        groups = _require_object(
            document["dependency-groups"],
            field="pyproject.dependency-groups",
        )
        projection["dependency-groups"] = {
            group: _normalize_runtime_array(
                dependencies,
                field=f"pyproject.dependency-groups.{group}",
            )
            for group, dependencies in groups.items()
        }

    tool = document.get("tool")
    if tool is not None:
        tool_object = _require_object(tool, field="pyproject.tool")
        projected_tool: dict[str, Any] = {}
        if "uv" in tool_object:
            projected_tool["uv"] = copy.deepcopy(
                _require_object(tool_object["uv"], field="pyproject.tool.uv")
            )
        hatch = tool_object.get("hatch")
        if hatch is not None:
            hatch_object = _require_object(hatch, field="pyproject.tool.hatch")
            if "build" in hatch_object:
                projected_tool["hatch"] = {
                    "build": copy.deepcopy(
                        _require_object(
                            hatch_object["build"],
                            field="pyproject.tool.hatch.build",
                        )
                    )
                }
        if projected_tool:
            projection["tool"] = projected_tool

    # 선택된 TOML 값에 datetime 등 JSON으로 고정할 수 없는 타입이 섞이면 fail-closed한다.
    canonical_json_bytes(projection)
    return projection


def _reference_source_sha256(path: Path, *, role: str) -> str:
    """일반 source는 원문, project source는 dependency/runtime projection을 hash한다."""

    if role not in PROJECT_RUNTIME_SOURCE_ROLES:
        return sha256_file(path)
    if path.name != "pyproject.toml":
        raise OracleContractError("project runtime projection role must target pyproject.toml")
    return sha256_bytes(canonical_json_bytes(_project_runtime_projection(path)))


def _reference_tree_manifest(
    root: Path,
    files: Iterable[Path],
    *,
    root_relative: str,
    source_roles_by_path: Mapping[str, str],
) -> tuple[bytes, list[dict[str, str]]]:
    """source tree에서 project runtime entry만 의미 기반 hash로 치환한다."""

    _payload, entries = canonical_file_manifest(root, files)
    for entry in entries:
        repo_relative = (Path(root_relative) / entry["path"]).as_posix()
        role = source_roles_by_path.get(repo_relative)
        if role in PROJECT_RUNTIME_SOURCE_ROLES:
            resolved = resolve_within(root, entry["path"], must_exist=True)
            entry["sha256"] = _reference_source_sha256(resolved, role=role)
    payload = "".join(
        f"{entry['sha256']}  {entry['path']}\n" for entry in entries
    ).encode("utf-8")
    return payload, entries


def validate_reference_lock(repo_root: Path, contract_root: Path) -> dict[str, Any]:
    """reference byte source와 project runtime projection closure를 검증한다."""

    path = contract_root / "reference-lock.v1.json"
    lock = _require_object(strict_json_load(path), field=path.name)
    expected_fields = {
        "schemaVersion",
        "referenceBaseCommit",
        "pythonRuntime",
        "functionCount",
        "stableErrorCodeCount",
        "s1_4r_canonical_fixture_sha256",
        "sources",
        "sourceTrees",
    }
    if set(lock) != expected_fields:
        raise OracleContractError("reference lock has wrong exact top-level fields")
    if lock.get("schemaVersion") != REFERENCE_LOCK_VERSION:
        raise OracleContractError("reference lock schemaVersion mismatch")
    if not isinstance(lock.get("referenceBaseCommit"), str) or not COMMIT_PATTERN.fullmatch(
        lock["referenceBaseCommit"]
    ):
        raise OracleContractError("referenceBaseCommit must be a lowercase 40-character SHA")
    if lock.get("functionCount") != 20 or lock.get("stableErrorCodeCount") != 32:
        raise OracleContractError("reference lock function/error counts must be exactly 20/32")
    if lock.get("pythonRuntime") != EXPECTED_REFERENCE_RUNTIME:
        raise OracleContractError("reference lock Python runtime identity mismatch")
    sources = _require_array(lock.get("sources"), field="reference-lock.sources")
    source_paths: set[str] = set()
    source_roles: set[str] = set()
    source_roles_by_path: dict[str, str] = {}
    for index, raw_source in enumerate(sources):
        source = _require_object(raw_source, field=f"reference-lock.sources[{index}]")
        if set(source) != {"role", "path", "sha256"}:
            raise OracleContractError("reference-lock source has wrong exact fields")
        role = source.get("role")
        relative = source.get("path")
        if not isinstance(role, str) or not role or role in source_roles:
            raise OracleContractError("reference-lock source role is invalid or duplicate")
        if not isinstance(relative, str) or relative in source_paths:
            raise OracleContractError("reference-lock source path is invalid or duplicate")
        expected_project_role = PROJECT_RUNTIME_SOURCE_ROLE_BY_PATH.get(relative)
        if expected_project_role is not None and role != expected_project_role:
            raise OracleContractError(
                f"reference project source has wrong runtime projection role: {relative}"
            )
        if role in PROJECT_RUNTIME_SOURCE_ROLES and expected_project_role != role:
            raise OracleContractError(
                "project runtime projection role targets an unsupported source path"
            )
        source_roles.add(role)
        source_paths.add(relative)
        source_roles_by_path[relative] = role
        resolved = resolve_within(repo_root, relative, must_exist=True)
        if not resolved.is_file() or resolved.is_symlink():
            raise OracleContractError(f"reference source is not a regular file: {relative}")
        expected_sha = require_lower_sha256(source.get("sha256"), field=f"{relative}.sha256")
        if _reference_source_sha256(resolved, role=role) != expected_sha:
            raise OracleContractError(f"reference source SHA-256 mismatch: {relative}")

    trees = _require_array(lock.get("sourceTrees"), field="reference-lock.sourceTrees")
    tree_roles: set[str] = set()
    tree_source_paths: set[str] = set()
    for index, raw_tree in enumerate(trees):
        tree = _require_object(raw_tree, field=f"reference-lock.sourceTrees[{index}]")
        if set(tree) != {
            "role",
            "root",
            "includeGlobs",
            "fileCount",
            "canonicalManifestSha256",
            "files",
        }:
            raise OracleContractError("reference sourceTree has wrong exact fields")
        role = tree.get("role")
        root_relative = tree.get("root")
        if not isinstance(role, str) or not role or role in tree_roles:
            raise OracleContractError("reference sourceTree role is invalid or duplicate")
        if not isinstance(root_relative, str):
            raise OracleContractError("reference sourceTree root must be a path string")
        tree_roles.add(role)
        root = resolve_within(repo_root, root_relative, must_exist=True)
        if not root.is_dir():
            raise OracleContractError(
                f"reference sourceTree root is not a directory: {root_relative}"
            )
        include_globs = _require_unique_strings(
            _require_array(tree.get("includeGlobs"), field=f"{role}.includeGlobs"),
            field=f"{role}.includeGlobs",
        )
        files = sorted_relative_files(root, include_globs)
        manifest_payload, entries = _reference_tree_manifest(
            root,
            files,
            root_relative=root_relative,
            source_roles_by_path=source_roles_by_path,
        )
        if tree.get("fileCount") != len(entries):
            raise OracleContractError(f"reference sourceTree fileCount mismatch: {role}")
        if tree.get("files") != entries:
            raise OracleContractError(f"reference sourceTree closure drift: {role}")
        expected_manifest_sha = require_lower_sha256(
            tree.get("canonicalManifestSha256"),
            field=f"{role}.canonicalManifestSha256",
        )
        if sha256_bytes(manifest_payload) != expected_manifest_sha:
            raise OracleContractError(f"reference sourceTree manifest SHA mismatch: {role}")
        for entry in entries:
            tree_source_path = (Path(root_relative) / entry["path"]).as_posix()
            if tree_source_path in tree_source_paths:
                raise OracleContractError(
                    f"reference sourceTree path appears in multiple trees: {tree_source_path}"
                )
            tree_source_paths.add(tree_source_path)

    if tree_source_paths != source_paths:
        raise OracleContractError(
            "reference source path set must equal the normalized sourceTrees union"
        )

    canonical_sha = require_lower_sha256(
        lock.get("s1_4r_canonical_fixture_sha256"),
        field="s1_4r_canonical_fixture_sha256",
    )
    canonical_path = (
        repo_root
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4r-jax-risk"
        / "tests"
        / "fixtures"
        / "canonical"
        / "advanced_risk_v1.json"
    )
    if sha256_file(canonical_path) != canonical_sha:
        raise OracleContractError("S1.4R canonical fixture SHA mismatch")
    return {"sourceCount": len(sources), "sourceTreeCount": len(trees), "sourcePaths": source_paths}


def _yaml_scalar(value: str) -> str:
    return value.strip().strip("'\"")


def _workflow_on_block(lines: Sequence[str]) -> tuple[int, list[str]]:
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(?P<indent>\s*)(?:on|'on'|\"on\"):\s*", line)
        if match is None:
            continue
        base_indent = len(match.group("indent"))
        block: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= base_indent:
                break
            block.append(candidate)
        return base_indent, block
    raise OracleContractError("contract workflow has no structured on mapping")


def _workflow_event_block(text: str, event: str) -> tuple[int, list[str]]:
    base_indent, on_block = _workflow_on_block(text.splitlines())
    event_indent = base_indent + 2
    for index, line in enumerate(on_block):
        if re.fullmatch(rf"\s{{{event_indent}}}{re.escape(event)}:\s*", line) is None:
            continue
        block: list[str] = []
        for candidate in on_block[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= event_indent:
                break
            block.append(candidate)
        return event_indent, block
    raise OracleContractError(f"contract workflow on mapping has no {event} event")


def _workflow_event_list(text: str, *, event: str, field: str) -> list[str]:
    event_indent, event_block = _workflow_event_block(text, event)
    field_indent = event_indent + 2
    for index, line in enumerate(event_block):
        match = re.fullmatch(
            rf"\s{{{field_indent}}}{re.escape(field)}:\s*(?P<inline>.*)",
            line,
        )
        if match is None:
            continue
        inline = match.group("inline").strip()
        if inline:
            if not inline.startswith("[") or not inline.endswith("]"):
                raise OracleContractError(f"workflow {event}.{field} must be a YAML list")
            return [_yaml_scalar(item) for item in inline[1:-1].split(",") if _yaml_scalar(item)]
        values: list[str] = []
        for candidate in event_block[index + 1 :]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= field_indent:
                break
            item_match = re.fullmatch(r"\s*-\s*(?P<value>.+?)\s*", candidate)
            if item_match is not None:
                values.append(_yaml_scalar(item_match.group("value")))
        return values
    raise OracleContractError(f"contract workflow {event} has no {field} list")


def validate_workflow_path_coverage(repo_root: Path, source_paths: Iterable[str]) -> int:
    """reference lock source가 PR/main workflow path trigger에 모두 포함되는지 확인한다."""

    workflow = repo_root / ".github" / "workflows" / "s1-4x-contract-correctness.yml"
    if not workflow.is_file():
        raise OracleContractError("S1.4X contract correctness workflow is missing")
    try:
        text = workflow.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OracleContractError("unable to read S1.4X contract correctness workflow") from exc
    pull_patterns = _workflow_event_list(text, event="pull_request", field="paths")
    push_patterns = _workflow_event_list(text, event="push", field="paths")
    push_branches = _workflow_event_list(text, event="push", field="branches")
    if push_branches != ["main"]:
        raise OracleContractError("contract workflow push branches must be exactly [main]")
    if not pull_patterns or not push_patterns:
        raise OracleContractError("workflow event paths must contain non-empty patterns")
    if set(pull_patterns) != set(push_patterns):
        raise OracleContractError("pull_request and push path trigger sets must be identical")
    locked_paths = list(source_paths)
    for event, patterns in (
        ("pull_request", pull_patterns),
        ("push", push_patterns),
    ):
        uncovered = sorted(
            path
            for path in locked_paths
            if not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
        )
        if uncovered:
            raise OracleContractError(
                f"workflow {event} paths do not cover reference sources: {uncovered}"
            )
    return len(set(pull_patterns))


def _manifest_relative_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(repo_root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise OracleContractError("contract manifest must be inside the repository") from exc


def materialize_contract_manifest(
    repo_root: Path,
    manifest_path: Path,
    skeleton: Mapping[str, Any],
) -> dict[str, Any]:
    """manifest skeleton에서 self-free immutable file/root hash closure를 계산한다."""

    manifest = copy.deepcopy(dict(skeleton))
    if manifest.get("schemaVersion") != CONTRACT_MANIFEST_VERSION:
        raise OracleContractError("contract manifest schemaVersion mismatch")
    immutable_files = _require_unique_strings(
        _require_array(manifest.get("immutableFiles"), field="immutableFiles"),
        field="immutableFiles",
    )
    immutable_roots = _require_array(manifest.get("immutableRoots"), field="immutableRoots")
    self_relative = _manifest_relative_path(repo_root, manifest_path)
    if self_relative in immutable_files:
        raise OracleContractError("contract manifest must exclude itself")

    top_entries: list[dict[str, str]] = []
    listed_repo_paths: set[str] = set()
    for relative in sorted(immutable_files, key=lambda item: item.encode()):
        resolved = resolve_within(repo_root, relative, must_exist=True)
        if not resolved.is_file() or resolved.is_symlink():
            raise OracleContractError(f"immutable file is not regular: {relative}")
        listed_repo_paths.add(relative)
        top_entries.append({"path": relative, "sha256": sha256_file(resolved)})
    manifest["files"] = top_entries

    materialized_roots: list[dict[str, Any]] = []
    root_names: set[str] = set()
    for index, raw_root in enumerate(immutable_roots):
        root_entry = _require_object(raw_root, field=f"immutableRoots[{index}]")
        root_relative = root_entry.get("root")
        if not isinstance(root_relative, str) or not root_relative or root_relative in root_names:
            raise OracleContractError("immutable root is invalid or duplicate")
        root_names.add(root_relative)
        root_path = resolve_within(repo_root, root_relative, must_exist=True)
        if not root_path.is_dir():
            raise OracleContractError(f"immutable root is not a directory: {root_relative}")
        include_globs = _require_unique_strings(
            _require_array(root_entry.get("includeGlobs"), field=f"{root_relative}.includeGlobs"),
            field=f"{root_relative}.includeGlobs",
        )
        exclude_globs = _require_unique_strings(
            _require_array(
                root_entry.get("excludeGlobs", []),
                field=f"{root_relative}.excludeGlobs",
            ),
            field=f"{root_relative}.excludeGlobs",
        )
        selected = sorted_relative_files(
            root_path,
            include_globs,
            exclude_globs=exclude_globs,
        )
        selected = [
            path for path in selected if _manifest_relative_path(repo_root, path) != self_relative
        ]
        payload, entries = canonical_file_manifest(root_path, selected)
        for entry in entries:
            repo_relative = (Path(root_relative) / entry["path"]).as_posix()
            if repo_relative in listed_repo_paths:
                raise OracleContractError(f"contract manifest lists a file twice: {repo_relative}")
            listed_repo_paths.add(repo_relative)
        computed = copy.deepcopy(root_entry)
        computed["fileCount"] = len(entries)
        computed["canonicalManifestSha256"] = sha256_bytes(payload)
        computed["files"] = entries
        materialized_roots.append(computed)
    manifest["immutableRoots"] = materialized_roots
    return manifest


def write_contract_manifest(repo_root: Path, manifest_path: Path) -> dict[str, Any]:
    """tracked skeleton을 현재 immutable closure로 갱신하되 self hash는 넣지 않는다."""

    skeleton = _require_object(strict_json_load(manifest_path), field=manifest_path.name)
    materialized = materialize_contract_manifest(repo_root, manifest_path, skeleton)
    atomic_write_json(manifest_path, materialized)
    return materialized


def validate_contract_manifest(
    repo_root: Path,
    manifest_path: Path,
    *,
    required: bool,
) -> int:
    """precommitted contract manifest가 현재 include-glob closure와 byte-identical인지 검증한다."""

    if not manifest_path.is_file():
        if required:
            raise OracleContractError("contract-manifest.v1.json is missing")
        return 0
    current = _require_object(strict_json_load(manifest_path), field=manifest_path.name)
    expected = materialize_contract_manifest(repo_root, manifest_path, current)
    if current != expected:
        raise OracleContractError("contract manifest closure drift; run --write-manifest")
    serialized = canonical_json_bytes(current)
    if manifest_path.read_bytes() != serialized:
        raise OracleContractError("contract manifest must use canonical JSON UTF-8 bytes")
    return len(_require_array(current.get("files"), field="contract-manifest.files")) + sum(
        _require_exact_integer(root.get("fileCount"), field="immutableRoot.fileCount", minimum=0)
        for root in _require_array(current.get("immutableRoots"), field="immutableRoots")
        if isinstance(root, dict)
    )


def validate_contract(
    *,
    repo_root: Path,
    contract_root: Path,
    manifest_path: Path,
    check_all: bool,
) -> dict[str, Any]:
    """Gate 1 portable contract 전체를 fail-closed로 검사하고 typed summary를 반환한다."""

    failures: list[str] = []
    validated_schemas: dict[str, str] = {}
    functions: dict[str, dict[str, Any]] = {}
    errors: frozenset[str] = frozenset()
    allowed_nonfinite: dict[str, str] = {}
    property_count = 0
    negative_fixture_count = 0
    semantic_error_fixture_count = 0
    binary_count = 0
    result_count = 0
    sidecar_count = 0
    reference: dict[str, Any] = {
        "sourceCount": 0,
        "sourceTreeCount": 0,
        "sourcePaths": set(),
    }
    workflow_pattern_count = 0
    manifest_file_count = 0

    try:
        validated_schemas = validate_json_schemas(contract_root)
    except OracleContractError as exc:
        failures.append(f"schemas: {exc}")
    try:
        negative_fixture_count = len(validate_negative_fixtures(contract_root))
    except OracleContractError as exc:
        failures.append(f"negative-fixtures: {exc}")
    try:
        functions, errors = validate_registries(contract_root)
    except OracleContractError as exc:
        failures.append(f"registries: {exc}")
    if functions and errors:
        try:
            property_count = validate_property_plan(contract_root, functions=functions)
        except OracleContractError as exc:
            failures.append(f"property-plan: {exc}")
        try:
            allowed_nonfinite = validate_request_fixtures(
                contract_root,
                functions,
                errors,
            )
        except OracleContractError as exc:
            failures.append(f"requests: {exc}")
        try:
            result_count = validate_expected_results(
                contract_root,
                functions=functions,
                errors=errors,
            )
        except OracleContractError as exc:
            failures.append(f"expected-results: {exc}")
        try:
            semantic_error_fixture_count = validate_semantic_error_fixtures(
                contract_root,
                functions=functions,
                errors=errors,
            )
        except OracleContractError as exc:
            failures.append(f"semantic-error-fixtures: {exc}")
    try:
        binary_count = validate_binary_manifests(
            contract_root,
            allowed_nonfinite=allowed_nonfinite,
        )
    except OracleContractError as exc:
        failures.append(f"binary-manifests: {exc}")
    try:
        sidecar_count = validate_sha256_sidecars(contract_root)
    except OracleContractError as exc:
        failures.append(f"sha256-sidecars: {exc}")
    try:
        reference = validate_reference_lock(repo_root, contract_root)
    except OracleContractError as exc:
        failures.append(f"reference-lock: {exc}")
    if reference["sourcePaths"]:
        try:
            workflow_pattern_count = validate_workflow_path_coverage(
                repo_root,
                reference["sourcePaths"],
            )
        except OracleContractError as exc:
            failures.append(f"workflow-paths: {exc}")
    try:
        manifest_file_count = validate_contract_manifest(
            repo_root,
            manifest_path,
            required=check_all,
        )
    except OracleContractError as exc:
        failures.append(f"contract-manifest: {exc}")
    if failures:
        joined = "; ".join(failures)
        raise OracleContractError(
            f"contract validation collected {len(failures)} failure(s): {joined}"
        )
    return {
        "schemaVersion": "s1.4x-contract-validation-v1",
        "status": "PASS",
        "checkAll": check_all,
        "validatedSchemaInstanceCount": len(validated_schemas),
        "functionCount": len(functions),
        "errorCodeCount": len(errors),
        "propertyCount": property_count,
        "negativeFixtureCount": negative_fixture_count,
        "semanticErrorFixtureCount": semantic_error_fixture_count,
        "binaryManifestCount": binary_count,
        "expectedResultCount": result_count,
        "sha256SidecarCount": sidecar_count,
        "referenceSourceCount": reference["sourceCount"],
        "referenceSourceTreeCount": reference["sourceTreeCount"],
        "workflowPathPatternCount": workflow_pattern_count,
        "contractManifestFileCount": manifest_file_count,
    }


def _default_s1_4x_root() -> Path:
    return (
        find_repo_root() / "workspaces" / "decision-platform" / "research" / "s1-4x-numeric-parity"
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_root = _default_s1_4x_root()
    parser = argparse.ArgumentParser(
        description="Validate the frozen S1.4X Gate 1 contract without provider calls.",
    )
    parser.add_argument("--root", type=Path, default=find_repo_root())
    parser.add_argument("--contract", type=Path, default=default_root / "contract")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default_root / "contract" / "contract-manifest.v1.json",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Recompute the predeclared immutableFiles/immutableRoots closure.",
    )
    parser.add_argument(
        "--check-all",
        action="store_true",
        help="Require and validate the complete precommitted contract manifest closure.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI contract: 성공은 canonical JSON과 exit 0, 검증 실패는 sanitized exit 2다."""

    args = _parse_args(argv)
    try:
        repo_root = args.root.resolve(strict=True)
        contract_root = args.contract.resolve(strict=True)
        manifest_path = args.manifest.resolve(strict=False)
        if args.write_manifest:
            write_contract_manifest(repo_root, manifest_path)
        report = validate_contract(
            repo_root=repo_root,
            contract_root=contract_root,
            manifest_path=manifest_path,
            check_all=args.check_all or args.write_manifest,
        )
        if args.output is not None:
            atomic_write_json(args.output, report)
        sys.stdout.buffer.write(canonical_json_bytes(report))
        return 0
    except (OracleContractError, OSError) as exc:
        failure = {
            "schemaVersion": "s1.4x-contract-validation-v1",
            "status": "FAIL",
            "error": str(exc),
        }
        sys.stderr.buffer.write(canonical_json_bytes(failure))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
