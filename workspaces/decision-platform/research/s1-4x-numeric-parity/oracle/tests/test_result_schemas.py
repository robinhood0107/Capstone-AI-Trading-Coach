"""Gate 2 결과가 PASS/ADOPT 증거와 모순될 때 스키마 단계에서 거부되는지 검증한다."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

_SHA256 = "a" * 64
_VECTOR_SOURCE_SHA256 = "28f203c786cbf8ac6dc3fea3378ec36f34173d505fb4a1dd60fc8418ad91c423"
_VECTOR_PROVENANCE = (
    "official Hackage vector-0.13.2.0 archive bytes; "
    "Stackage LTS 24.50 Pantry tree "
    "sha256:12839cef1252eaa894d6a9adafaa2e1cdb449f03c343f765294e033c813261fc"
)
_MANDATORY_CORE_EXTENSIONS = (
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
)
_FORBIDDEN_CORE_POSITIVE_EXTENSIONS = (
    "ForeignFunctionInterface",
    "TemplateHaskell",
    "CPP",
    "RebindableSyntax",
    "LinearTypes",
    "MagicHash",
    "Strict",
    "GeneralizedNewtypeDeriving",
    "DerivingVia",
    "DeriveAnyClass",
)
_SCALA_SMOKE_IDS = (
    "scala-toolchain-identity",
    "scala-stable-compiler-profile",
    "scala-scalafmt-idempotence",
    "scala-scalafix-disable-syntax",
    "scala-source-policy",
    "scala-explicit-result-types",
    "scala-jmh-native-json",
    "scala-profile-abc-correctness",
)


def _upstream_vector_edges() -> list[dict[str, Any]]:
    common = {
        "package": "vector",
        "version": "0.13.2.0",
        "sourceSha256": _VECTOR_SOURCE_SHA256,
        "provenance": _VECTOR_PROVENANCE,
        "allowlisted": True,
    }
    return [
        {
            **common,
            "importPath": (
                "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
                "Data.Vector.Primitive -> Unsafe.Coerce"
            ),
            "edgeKind": "unsafe-import",
        },
        {
            **common,
            "importPath": (
                "Data.Vector.Unboxed -> Data.Vector.Unboxed.Base -> "
                "Data.Vector.Primitive -> Data.Vector.Primitive.Mutable -> "
                "Unsafe.Coerce"
            ),
            "edgeKind": "unsafe-import",
        },
        {
            **common,
            "importPath": (
                "Data.Vector.Unboxed -> Data.Vector.Generic -> "
                "Data.Vector.Internal.Check -> GHC.Exts(Int#)"
            ),
            "edgeKind": "compiler-primop",
        },
    ]


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "contract" / "schemas" / name
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _valid(schema_name: str, instance: dict[str, Any]) -> bool:
    return bool(Draft202012Validator(_schema(schema_name)).is_valid(instance))


def _capability_result() -> dict[str, Any]:
    results = [
        {
            "smokeId": smoke_id,
            "compilerStatus": "stable",
            "argv": ["run-smoke", smoke_id],
            "exitCode": 0,
            "stdoutSha256": _SHA256,
            "stderrSha256": _SHA256,
            "artifactSha256": _SHA256,
            "status": "PASS",
            "disposition": "ADOPT",
            "provenFallback": "predeclared fallback",
            "fallbackExecuted": False,
        }
        for smoke_id in _SCALA_SMOKE_IDS
    ]
    return {
        "schemaVersion": "s1.4x-capability-smoke-result-v1",
        "planSha256": _SHA256,
        "language": "scala",
        "toolchainIdentitySha256": _SHA256,
        "results": results,
        "aggregateStatus": "PASS",
    }


def _feature_decision_result() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-feature-decision-results-v1",
        "plannedDecisionSha256": _SHA256,
        "capabilitySmokeResultSha256": _SHA256,
        "entries": [
            {
                "featureId": "scala.opaque-boundaries",
                "plannedDecision": "ADOPT",
                "effectiveDecision": "ADOPT",
                "smokeStatus": "PASS",
                "lintStatus": "PASS",
                "testStatus": "PASS",
                "parityMismatchCount": 0,
                "evidenceStatus": "PASS",
                "fallbackExecuted": False,
                "fallbackStatus": "NOT_RUN",
                "evidenceSha256": _SHA256,
            }
        ],
    }


def _module_safety_result() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-haskell-module-safety-result-v1",
        "policySha256": _SHA256,
        "sourceInputManifestSha256": _SHA256,
        "modules": [
            {
                "moduleName": "Risk.Scalar",
                "path": "src/Risk/Scalar.hs",
                "category": "safe-scalar",
                "compileMode": "Safe",
                "extensions": ["Safe", *_MANDATORY_CORE_EXTENSIONS],
                "sourceSha256": _SHA256,
            }
        ],
        "candidateDirectImports": [],
        "candidateHomeModuleEdges": [],
        "upstreamTransitiveEdges": _upstream_vector_edges(),
        "unclassifiedModuleCount": 0,
        "candidateTrustworthyUnsafeDeclarationCount": 0,
        "candidateDirectUnsafeIoForeignImportCount": 0,
        "coreToShellEdgeCount": 0,
        "unknownTransitiveEdgeCount": 0,
        "staleAllowlistCount": 0,
        "aggregateStatus": "PASS",
    }


def _native_edge_result() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-dependency-native-edge-result-v1",
        "policySha256": _SHA256,
        "laneId": "authoritative",
        "compilerVersion": "9.10.3",
        "packagePlanSha256": _SHA256,
        "sourceArchiveManifestSha256": _SHA256,
        "linkMetadataSha256": _SHA256,
        "edges": [
            {
                "category": "ghc-rts",
                "package": "rts",
                "version": "1.0.2",
                "sourceSha256": _SHA256,
                "effectiveFlags": {},
                "edgeKind": "rts-runtime",
                "linkedLibrary": "libHSrts.a",
                "provenance": "GHC 9.10.3 boot-set manifest",
                "candidateAuthored": False,
                "coreReachable": True,
                "shellOnly": False,
                "timedRegion": True,
                "reviewDisposition": "ALLOW_FROZEN",
            }
        ],
        "newNonBootEdgeCount": 0,
        "candidateAuthoredEdgeCount": 0,
        "candidateAddedNativeDependencyCount": 0,
        "candidateCoreDirectNativeBindingImportCount": 0,
        "candidateCoreDirectNativeBindingCallCount": 0,
        "timedKernelExplicitCandidateNativeInteropCallCount": 0,
        "unknownEdgeCount": 0,
        "staleAllowlistEntryCount": 0,
        "nonBootPlanEquivalentToAuthoritative": True,
        "aggregateStatus": "PASS",
    }


def _scala_source_policy_result() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-scala-source-policy-result-v1",
        "policySha256": _SHA256,
        "sourceInputManifestSha256": _SHA256,
        "checkerMode": "semanticdb",
        "semanticSmokeStatus": "PASS",
        "checkedFiles": ["scala/src/main/scala/Risk.scala"],
        "violations": [],
        "usedAllowlistEntries": [],
        "staleAllowlistEntries": [],
        "sourceSetExact": True,
        "aggregateStatus": "PASS",
    }


def test_result_schemas_are_valid_draft_2020_12_and_accept_consistent_passes() -> None:
    assert _valid("capability-smoke-result.schema.json", _capability_result())
    assert _valid("feature-decision-result.schema.json", _feature_decision_result())
    assert _valid("haskell-module-safety-result.schema.json", _module_safety_result())
    assert _valid("dependency-native-edge-result.schema.json", _native_edge_result())
    assert _valid("scala-source-policy-result.schema.json", _scala_source_policy_result())


def test_capability_pass_rejects_failed_or_missing_required_smoke() -> None:
    failed = _capability_result()
    failed["results"][0].update(
        {
            "status": "FAIL",
            "disposition": "BLOCKED_CONTRACT",
        }
    )
    assert not _valid("capability-smoke-result.schema.json", failed)

    missing = _capability_result()
    missing["results"].pop()
    assert not _valid("capability-smoke-result.schema.json", missing)

    contradictory_item = _capability_result()
    contradictory_item["results"][0]["disposition"] = "BLOCKED_TOOLCHAIN"
    assert not _valid("capability-smoke-result.schema.json", contradictory_item)


def test_feature_adopt_rejects_any_failed_or_missing_evidence() -> None:
    cases: list[dict[str, Any]] = []
    for field in ("smokeStatus", "lintStatus", "testStatus", "evidenceStatus"):
        case = _feature_decision_result()
        case["entries"][0][field] = "FAIL"
        cases.append(case)

    parity = _feature_decision_result()
    parity["entries"][0]["parityMismatchCount"] = 1
    cases.append(parity)

    fallback = _feature_decision_result()
    fallback["entries"][0]["fallbackExecuted"] = True
    fallback["entries"][0]["fallbackStatus"] = "PASS"
    cases.append(fallback)

    for case in cases:
        assert not _valid("feature-decision-result.schema.json", case)


def test_module_safety_pass_rejects_category_and_edge_contradictions() -> None:
    wrong_mode = _module_safety_result()
    wrong_mode["modules"][0]["compileMode"] = "SafeHaskell-None-with-audited-purity-gate"
    assert not _valid("haskell-module-safety-result.schema.json", wrong_mode)

    trustworthy_safe_scalar = _module_safety_result()
    trustworthy_safe_scalar["modules"][0]["extensions"].append("Trustworthy")
    assert not _valid(
        "haskell-module-safety-result.schema.json",
        trustworthy_safe_scalar,
    )

    forbidden_import = _module_safety_result()
    forbidden_import["candidateDirectImports"].append(
        {
            "fromModule": "Risk.Scalar",
            "fromCategory": "safe-scalar",
            "importedModule": "Foreign.C",
            "classification": "allowed-pure",
        }
    )
    assert not _valid("haskell-module-safety-result.schema.json", forbidden_import)

    vector_claimed_by_safe_scalar = _module_safety_result()
    vector_claimed_by_safe_scalar["candidateDirectImports"].append(
        {
            "fromModule": "Risk.Scalar",
            "fromCategory": "safe-scalar",
            "importedModule": "Data.Vector.Unboxed",
            "classification": "allowed-pure",
        }
    )
    assert not _valid(
        "haskell-module-safety-result.schema.json",
        vector_claimed_by_safe_scalar,
    )

    debug_trace_claimed_pure = _module_safety_result()
    debug_trace_claimed_pure["candidateDirectImports"].append(
        {
            "fromModule": "Risk.Scalar",
            "fromCategory": "safe-scalar",
            "importedModule": "Debug.Trace",
            "classification": "allowed-pure",
        }
    )
    assert not _valid(
        "haskell-module-safety-result.schema.json",
        debug_trace_claimed_pure,
    )

    forbidden_home_edge = _module_safety_result()
    forbidden_home_edge["modules"].append(
        {
            "moduleName": "Risk.Shell",
            "path": "app/Risk/Shell.hs",
            "category": "io-shell",
            "compileMode": "ordinary",
            "extensions": [],
            "sourceSha256": _SHA256,
        }
    )
    forbidden_home_edge["candidateHomeModuleEdges"].append(
        {
            "fromModule": "Risk.Scalar",
            "fromCategory": "safe-scalar",
            "toModule": "Risk.Shell",
            "toCategory": "io-shell",
            "classification": "core-to-core",
        }
    )
    assert not _valid("haskell-module-safety-result.schema.json", forbidden_home_edge)

    unallowlisted = _module_safety_result()
    unallowlisted["upstreamTransitiveEdges"][0]["allowlisted"] = False
    assert not _valid("haskell-module-safety-result.schema.json", unallowlisted)


def test_module_safety_accepts_typed_consistent_edges() -> None:
    consistent = _module_safety_result()
    consistent["modules"].extend(
        [
            {
                "moduleName": "Risk.Vector",
                "path": "src/Risk/Vector.hs",
                "category": "audited-pure-vector",
                "compileMode": "SafeHaskell-None-with-audited-purity-gate",
                "extensions": list(_MANDATORY_CORE_EXTENSIONS),
                "sourceSha256": _SHA256,
            },
            {
                "moduleName": "Risk.Shell",
                "path": "app/Risk/Shell.hs",
                "category": "io-shell",
                "compileMode": "ordinary",
                "extensions": [],
                "sourceSha256": _SHA256,
            },
        ]
    )
    consistent["candidateDirectImports"].append(
        {
            "fromModule": "Risk.Vector",
            "fromCategory": "audited-pure-vector",
            "importedModule": "Data.Vector.Unboxed",
            "classification": "allowed-pure",
        }
    )
    consistent["candidateHomeModuleEdges"].extend(
        [
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
                "toModule": "Risk.Vector",
                "toCategory": "audited-pure-vector",
                "classification": "shell-to-core",
            },
        ]
    )
    assert _valid("haskell-module-safety-result.schema.json", consistent)

    missing_edge_kind = deepcopy(consistent)
    del missing_edge_kind["upstreamTransitiveEdges"][0]["edgeKind"]
    assert not _valid(
        "haskell-module-safety-result.schema.json",
        missing_edge_kind,
    )


def test_module_safety_requires_exact_core_extension_partition() -> None:
    safe_scalar = _module_safety_result()
    for extension in _MANDATORY_CORE_EXTENSIONS:
        missing = deepcopy(safe_scalar)
        missing["modules"][0]["extensions"].remove(extension)
        assert not _valid(
            "haskell-module-safety-result.schema.json",
            missing,
        ), extension

    missing_safe = deepcopy(safe_scalar)
    missing_safe["modules"][0]["extensions"].remove("Safe")
    assert not _valid("haskell-module-safety-result.schema.json", missing_safe)

    audited_vector = _module_safety_result()
    audited_vector["modules"][0].update(
        {
            "category": "audited-pure-vector",
            "compileMode": "SafeHaskell-None-with-audited-purity-gate",
            "extensions": list(_MANDATORY_CORE_EXTENSIONS),
        }
    )
    assert _valid("haskell-module-safety-result.schema.json", audited_vector)

    false_safe_claim = deepcopy(audited_vector)
    false_safe_claim["modules"][0]["extensions"].append("Safe")
    assert not _valid(
        "haskell-module-safety-result.schema.json",
        false_safe_claim,
    )

    for base in (safe_scalar, audited_vector):
        for extension in _FORBIDDEN_CORE_POSITIVE_EXTENSIONS:
            contradictory = deepcopy(base)
            contradictory["modules"][0]["extensions"].append(extension)
            assert not _valid(
                "haskell-module-safety-result.schema.json",
                contradictory,
            ), extension


def test_native_edge_pass_rejects_lane_and_hard_failure_contradictions() -> None:
    wrong_compiler = _native_edge_result()
    wrong_compiler["compilerVersion"] = "9.14.1"
    assert not _valid("dependency-native-edge-result.schema.json", wrong_compiler)

    rejected_edge = _native_edge_result()
    rejected_edge["edges"][0]["reviewDisposition"] = "REJECT"
    assert not _valid("dependency-native-edge-result.schema.json", rejected_edge)

    candidate_edge = _native_edge_result()
    candidate_edge["edges"][0]["category"] = "candidate-authored"
    candidate_edge["edges"][0]["candidateAuthored"] = True
    assert not _valid("dependency-native-edge-result.schema.json", candidate_edge)

    for field in (
        "newNonBootEdgeCount",
        "candidateAuthoredEdgeCount",
        "candidateAddedNativeDependencyCount",
        "candidateCoreDirectNativeBindingImportCount",
        "candidateCoreDirectNativeBindingCallCount",
        "timedKernelExplicitCandidateNativeInteropCallCount",
        "unknownEdgeCount",
        "staleAllowlistEntryCount",
    ):
        hard_failure = deepcopy(_native_edge_result())
        hard_failure[field] = 1
        assert not _valid(
            "dependency-native-edge-result.schema.json",
            hard_failure,
        ), field

    drift = _native_edge_result()
    drift["nonBootPlanEquivalentToAuthoritative"] = False
    assert not _valid("dependency-native-edge-result.schema.json", drift)


def test_native_edge_uses_policy_category_flags_and_pure_math_flags() -> None:
    stale_vocabulary = _native_edge_result()
    edge = stale_vocabulary["edges"][0]
    edge["category"] = "compiler-rts"
    edge["flagSetSha256"] = edge.pop("effectiveFlags")
    assert not _valid("dependency-native-edge-result.schema.json", stale_vocabulary)

    math_edge = _native_edge_result()
    math_edge["edges"][0].update(
        {
            "category": "lts-24.50-transitive",
            "package": "math-functions",
            "version": "0.3.4.4",
            "effectiveFlags": {
                "system-erf": True,
                "system-expm1": False,
            },
            "edgeKind": "linked-library",
        }
    )
    assert not _valid("dependency-native-edge-result.schema.json", math_edge)


def test_scala_source_policy_binds_semantic_smoke_to_checker_mode() -> None:
    fallback = _scala_source_policy_result()
    fallback["semanticSmokeStatus"] = "FALLBACK"
    fallback["checkerMode"] = "typed-tree"
    assert _valid("scala-source-policy-result.schema.json", fallback)

    alias_fallback = deepcopy(fallback)
    alias_fallback["checkerMode"] = "alias-aware-ast-fallback"
    assert _valid("scala-source-policy-result.schema.json", alias_fallback)

    fallback_claiming_semanticdb = deepcopy(fallback)
    fallback_claiming_semanticdb["checkerMode"] = "semanticdb"
    assert not _valid("scala-source-policy-result.schema.json", fallback_claiming_semanticdb)

    pass_claiming_fallback = _scala_source_policy_result()
    pass_claiming_fallback["checkerMode"] = "typed-tree"
    assert not _valid("scala-source-policy-result.schema.json", pass_claiming_fallback)
