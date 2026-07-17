from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import jax
import jax.numpy as jnp
import numpy as np
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_SCHEMA_PATH = PROJECT_ROOT / "benchmarks/schemas/benchmark-plan.schema.json"
REPORT_SCHEMA_PATH = PROJECT_ROOT / "benchmarks/schemas/benchmark-report.schema.json"
WORKER_SOURCE_PATH = PROJECT_ROOT / "benchmarks/worker.py"

ALLOCATION_CAP_BYTES = 536_870_912
RNG_SEED = 20_260_717
ONE_DIMENSIONAL_SIZES = (32, 252, 1_000, 10_000, 100_000)
PATH_COUNTS = (100, 1_000, 10_000)
HORIZON = 252
DSR_TRIAL_SHARPE_ESTIMATES = (
    -0.1414213562373095,
    0.1414213562373095,
)
DSR_TRIAL_REGISTRY_SHA256 = "a40fd68290a4dfadabc80e16e9adba4226e8a470e336774afff548829825e706"
ONE_DIMENSIONAL_KERNELS = (
    "historical_expected_shortfall",
    "realized_variance",
    "realized_volatility_intraday",
    "lo_adjusted_sharpe_ratio",
    "kupiec_unconditional_coverage_test",
    "christoffersen_independence_test",
    "christoffersen_conditional_coverage_test",
)
SCALAR_EVALUATION_KERNELS = (
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
)
PATH_KERNELS = ONE_DIMENSIONAL_KERNELS + SCALAR_EVALUATION_KERNELS
BACKTEST_KERNELS = {
    "kupiec_unconditional_coverage_test",
    "christoffersen_independence_test",
    "christoffersen_conditional_coverage_test",
}

JsonObject = dict[str, Any]


def _load_json(path: Path) -> JsonObject:
    return json.loads(path.read_text(encoding="utf-8"))


def _strict_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validator(path: Path) -> Draft202012Validator:
    schema = _load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _benchmark_module() -> ModuleType:
    # benchmark 구현 commit 전에는 이 import가 실패해 계약 테스트가 의도대로 red를 유지한다.
    return importlib.import_module("benchmarks.run")


def _static_args(kernel: str) -> JsonObject:
    if kernel == "lo_adjusted_sharpe_ratio":
        return {"aggregationPeriods": 5}
    return {}


def _expected_cases() -> list[JsonObject]:
    cases: list[JsonObject] = []
    for kernel in ONE_DIMENSIONAL_KERNELS:
        for size in ONE_DIMENSIONAL_SIZES:
            cases.append(
                {
                    "caseId": f"{kernel}--n-{size}",
                    "kernel": kernel,
                    "axis": "one_dimensional",
                    "size": size,
                    "paths": None,
                    "horizon": None,
                    "timedInputKind": "observation_vector",
                    "throughputUnits": [
                        "calls_per_second",
                        "observations_per_second",
                    ],
                    "staticArgs": _static_args(kernel),
                }
            )
    for kernel in PATH_KERNELS:
        for paths in PATH_COUNTS:
            scalar_evaluation = kernel in SCALAR_EVALUATION_KERNELS
            cases.append(
                {
                    "caseId": f"{kernel}--paths-{paths}",
                    "kernel": kernel,
                    "axis": "path_batch",
                    "size": None,
                    "paths": paths,
                    "horizon": HORIZON,
                    "timedInputKind": (
                        "scalar_parameters" if scalar_evaluation else "path_observation_matrix"
                    ),
                    "throughputUnits": (
                        ["calls_per_second", "evaluations_per_second"]
                        if scalar_evaluation
                        else [
                            "calls_per_second",
                            "paths_per_second",
                            "path_observations_per_second",
                        ]
                    ),
                    "staticArgs": _static_args(kernel),
                }
            )
    return cases


def _dsr_trial_provenance() -> JsonObject:
    return {
        "schemaVersion": "s1.4r-effective-trials-v1",
        "method": "pre_registered_independent",
        "rawTrialCount": 2,
        "effectiveTrialCount": 2,
        "samplingFrequency": "daily",
        "trialRegistrySha256": DSR_TRIAL_REGISTRY_SHA256,
        "varianceDdof": 1,
        "sharpeEstimateVariance": 0.04,
        "registrySerialization": "strict-json-sort-keys-utf8-v1",
    }


def _valid_plan() -> JsonObject:
    return {
        "schemaVersion": "s1.4r-benchmark-plan-v1",
        "planId": "s1.4r-bounded-cpu-x64-v1",
        "allocationCapBytes": ALLOCATION_CAP_BYTES,
        "rng": {"algorithm": "PCG64", "seed": RNG_SEED},
        "fixedParameters": {
            "dtype": "float64",
            "aggregationPeriods": 5,
            "riskFreeRate": 0,
            "confidence": 0.95,
            "significance": 0.05,
            "horizon": HORIZON,
        },
        "fixtureSerialization": {
            "dtype": "float64",
            "byteOrder": "little",
            "arrayOrder": "C",
            "hashAlgorithm": "SHA-256",
        },
        "dsrTrialProvenance": _dsr_trial_provenance(),
        "protocol": {
            "coldFreshProcesses": 20,
            "untimedWarmups": 5,
            "timedWarmSamples": 50,
            "quantileMethod": "linear",
            "timer": "perf_counter_ns",
            "compilationCacheEnabled": False,
        },
        "timedImplementations": ["numpy", "jax_jit"],
        "cases": _expected_cases(),
    }


def _not_applicable(reason: str = "not used for this benchmark mode") -> JsonObject:
    return {"status": "not_applicable", "reason": reason}


def _latency(p50: float = 10.0, p95: float = 19.0) -> JsonObject:
    return {
        "status": "measured",
        "p50Nanoseconds": p50,
        "p95Nanoseconds": p95,
    }


def _throughput(value: float = 1_000.0) -> JsonObject:
    return {"status": "measured", "valuePerSecond": value}


def _allocation(implementation: str) -> JsonObject:
    if implementation == "numpy":
        values = {
            "hostInputBytes": 256,
            "hostOutputBytes": 8,
            "numpyTemporaryBytes": 68_096,
            "jaxArgumentBytes": 0,
            "jaxTemporaryBytes": 0,
            "jaxOutputBytes": 0,
            "jaxAliasBytes": 0,
        }
        estimator = "numpy_source_bound_plus_tracemalloc_preflight_v2"
        tracemalloc = {"status": "measured", "value": 400}
    else:
        values = {
            "hostInputBytes": 256,
            "hostOutputBytes": 8,
            "numpyTemporaryBytes": 0,
            "jaxArgumentBytes": 256,
            "jaxTemporaryBytes": 3_072,
            "jaxOutputBytes": 16,
            "jaxAliasBytes": 0,
        }
        estimator = "jax_compiled_memory_analysis_plus_host_v1"
        tracemalloc = _not_applicable("tracemalloc does not measure JAX/XLA native allocation")
    estimated = (
        values["hostInputBytes"]
        + values["hostOutputBytes"]
        + values["numpyTemporaryBytes"]
        + values["jaxArgumentBytes"]
        + values["jaxTemporaryBytes"]
        + values["jaxOutputBytes"]
        - values["jaxAliasBytes"]
    )
    return {
        "allocationCapBytes": ALLOCATION_CAP_BYTES,
        **values,
        "estimatedPeakAllocationBytes": estimated,
        "numpyTracemallocPeakBytes": tracemalloc,
        "rssBaselineBytes": 100_000_000,
        "rssPeakBytes": 120_000_000,
        "rssDeltaBytes": 20_000_000,
        "chunkSize": 1,
        "chunkCount": 1,
        "lastChunkValidPaths": 1,
        "paddingPaths": 0,
        "paddingStrategy": "repeat_last_valid",
        "allocationEstimator": estimator,
        "allocationEstimatorMonotone": True,
        "ledgerEquation": (
            "hostInputBytes+hostOutputBytes+numpyTemporaryBytes+jaxArgumentBytes+"
            "jaxTemporaryBytes+jaxOutputBytes-jaxAliasBytes"
        ),
        "allocationCapPassed": True,
    }


def _raw_result(implementation: str) -> JsonObject:
    cold_sample = (
        {"firstCall": 10, "coldTotal": 10}
        if implementation == "numpy"
        else {
            "traceLower": 10,
            "compile": 10,
            "hostToDevice": 10,
            "firstExecute": 10,
            "deviceToHost": 10,
            "coldTotal": 50,
        }
    )
    return {
        "caseId": "historical_expected_shortfall--n-32",
        "implementation": implementation,
        "cold": [copy.deepcopy(cold_sample) for _ in range(20)],
        "warm": [5 for _ in range(50)],
    }


def _valid_raw_document() -> JsonObject:
    return {
        "schemaVersion": "s1.4r-benchmark-raw-samples-v1",
        "runId": "run-20260717-0001",
        "results": [_raw_result("numpy"), _raw_result("jax_jit")],
    }


def _result(implementation: str) -> JsonObject:
    numpy = implementation == "numpy"
    return {
        "resultId": f"historical_expected_shortfall--n-32--{implementation}",
        "caseId": "historical_expected_shortfall--n-32",
        "kernel": "historical_expected_shortfall",
        "axis": "one_dimensional",
        "size": 32,
        "paths": None,
        "horizon": None,
        "timedInputKind": "observation_vector",
        "implementation": implementation,
        "timingBoundary": (
            "validated_public_reference" if numpy else "compiled_device_numeric_core"
        ),
        "fixtureSha256": "a" * 64,
        "rawSamplesSha256": hashlib.sha256(
            _strict_json_bytes(_raw_result(implementation))
        ).hexdigest(),
        "correctnessPassed": True,
        "compileSignature": (
            _not_applicable("NumPy has no compiled JAX signature")
            if numpy
            else {
                "status": "measured",
                "shape": [32],
                "dtype": "float64",
                "staticArgs": {},
            }
        ),
        "latencies": {
            "firstCall": (
                _latency(10.0, 10.0)
                if numpy
                else _not_applicable("JAX cold work is split into explicit phases")
            ),
            "traceLower": (
                _not_applicable("NumPy does not trace or lower") if numpy else _latency(10.0, 10.0)
            ),
            "compile": (
                _not_applicable("NumPy does not compile") if numpy else _latency(10.0, 10.0)
            ),
            "hostToDevice": (
                _not_applicable("NumPy has no host-to-device transfer")
                if numpy
                else _latency(10.0, 10.0)
            ),
            "firstExecute": (
                _not_applicable("NumPy first call is reported separately")
                if numpy
                else _latency(10.0, 10.0)
            ),
            "deviceToHost": (
                _not_applicable("NumPy has no device-to-host transfer")
                if numpy
                else _latency(10.0, 10.0)
            ),
            "coldTotal": (_latency(10.0, 10.0) if numpy else _latency(50.0, 50.0)),
            "warm": _latency(5.0, 5.0),
        },
        "throughput": {
            "callsPerSecond": _throughput(200_000_000.0),
            "observationsPerSecond": _throughput(6_400_000_000.0),
            "pathsPerSecond": _not_applicable("not applicable"),
            "pathObservationsPerSecond": _not_applicable("not applicable"),
            "evaluationsPerSecond": _not_applicable("not applicable"),
        },
        "allocation": _allocation(implementation),
        "maxAbsoluteError": 0.0,
        "maxRelativeError": 0.0,
        "maxToleranceRatio": 0.0,
    }


def _execution_fingerprint(execution: JsonObject) -> str:
    payload = {
        "kernel": execution["kernel"],
        "architecture": execution["architecture"],
        "cpuModel": execution["cpuModel"],
        "logicalCores": execution["logicalCores"],
        "memoryBytes": execution["memoryBytes"],
        "outerHostBoundary": execution["outerHostBoundary"],
    }
    return hashlib.sha256(_strict_json_bytes(payload)).hexdigest()


def _valid_manifest() -> JsonObject:
    plan = _valid_plan()
    raw_document = _valid_raw_document()
    manifest = {
        "schemaVersion": "s1.4r-benchmark-manifest-v1",
        "matrix": "smallest",
        "caseCount": 1,
        "planSha256": hashlib.sha256(_strict_json_bytes(plan)).hexdigest(),
        "rawSamplesSha256": hashlib.sha256(_strict_json_bytes(raw_document)).hexdigest(),
        "dsrTrialProvenance": _dsr_trial_provenance(),
        "gitCommit": "e" * 40,
        "runId": "run-20260717-0001",
        "createdAtUtc": "2026-07-17T12:00:00Z",
        "execution": {
            "executionBoundary": "wsl2",
            "outerHostBoundary": "wsl2",
            "hostFingerprint": "",
            "os": "Linux",
            "kernel": "6.18.33.2-microsoft-standard-WSL2",
            "architecture": "x86_64",
            "wslVersion": {"status": "measured", "value": "2.7.10.0"},
            "cpuModel": "test CPU",
            "physicalCores": 4,
            "logicalCores": 8,
            "cpuAffinity": [0],
            "cpuGovernor": _not_applicable("WSL does not expose a CPU governor"),
            "memoryBytes": 16_000_000_000,
            "pythonVersion": "3.12.13",
            "numpyVersion": "2.5.1",
            "jaxVersion": "0.11.0",
            "jaxlibVersion": "0.11.0",
            "backend": "cpu",
            "devices": [{"id": 0, "platform": "cpu", "deviceKind": "cpu"}],
            "x64Enabled": True,
            "threadEnvironment": {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
            "containerRuntime": _not_applicable("host benchmark"),
            "containerImageId": _not_applicable("host benchmark"),
        },
        "protocol": {
            "coldFreshProcesses": 20,
            "untimedWarmups": 5,
            "timedWarmSamples": 50,
            "quantileMethod": "linear",
            "timer": "perf_counter_ns",
            "allocationCapBytes": ALLOCATION_CAP_BYTES,
        },
        "results": [_result("numpy"), _result("jax_jit")],
        "comparisons": [
            {
                "numpyResultId": "historical_expected_shortfall--n-32--numpy",
                "jaxResultId": "historical_expected_shortfall--n-32--jax_jit",
                "phase": "cold_total",
                "eligibility": {
                    "sameHost": True,
                    "sameRun": True,
                    "sameFixture": True,
                    "sameAffinity": True,
                    "sameThreads": True,
                    "sameExecutionBoundary": True,
                    "sameTimedBoundary": False,
                },
                "speedup": _not_applicable(
                    "NumPy validates the public boundary while JAX times the device core"
                ),
            },
            {
                "numpyResultId": "historical_expected_shortfall--n-32--numpy",
                "jaxResultId": "historical_expected_shortfall--n-32--jax_jit",
                "phase": "warm",
                "eligibility": {
                    "sameHost": True,
                    "sameRun": True,
                    "sameFixture": True,
                    "sameAffinity": True,
                    "sameThreads": True,
                    "sameExecutionBoundary": True,
                    "sameTimedBoundary": False,
                },
                "speedup": _not_applicable(
                    "NumPy validates the public boundary while JAX times the device core"
                ),
            },
        ],
        "artifacts": {
            "researchWheel": {"bytes": 10_000, "sha256": "1" * 64},
            "installedResearchEnvironment": {
                "bytes": 100_000_000,
                "measurementMethod": "apparent_bytes",
            },
            "ociImage": {
                "imageId": f"sha256:{'2' * 64}",
                "manifestDigest": f"sha256:{'3' * 64}",
                "engineReportedSizeBytes": 200_000_000,
                "measurementMethod": (
                    "docker_image_inspect_size_single_build_"
                    "docker_descriptor_matches_oci_manifest"
                ),
            },
            "ociArchive": {
                "format": "oci-layout",
                "uncompressedBytes": 210_000_000,
                "compressedBytes": 120_000_000,
                "compressedSha256": "4" * 64,
                "compression": "gzip-n",
            },
            "nativeExecutable": {
                "status": "not_applicable",
                "reason": "separate native executable is outside S1.4R scope",
            },
        },
    }
    manifest["execution"]["hostFingerprint"] = _execution_fingerprint(
        manifest["execution"]
    )
    return manifest


def _rebind_raw_evidence(
    manifest: JsonObject,
    raw_document: JsonObject,
) -> None:
    raw_by_key = {
        (raw["caseId"], raw["implementation"]): raw
        for raw in raw_document["results"]
    }
    for result in manifest["results"]:
        raw = raw_by_key[(result["caseId"], result["implementation"])]
        result["rawSamplesSha256"] = hashlib.sha256(
            _strict_json_bytes(raw)
        ).hexdigest()
    manifest["rawSamplesSha256"] = hashlib.sha256(
        _strict_json_bytes(raw_document)
    ).hexdigest()


def test_benchmark_schemas_are_valid_draft_2020_12() -> None:
    _validator(PLAN_SCHEMA_PATH)
    report_schema = _load_json(REPORT_SCHEMA_PATH)
    Draft202012Validator.check_schema(report_schema)
    assert report_schema["title"] == "S1.4R tracked benchmark manifest"
    assert "reports/benchmark-manifest.json" in report_schema["description"]


def test_plan_schema_accepts_only_the_frozen_62_case_matrix_shape() -> None:
    plan = _valid_plan()

    _validator(PLAN_SCHEMA_PATH).validate(plan)

    assert len(plan["cases"]) == 62
    assert len({case["caseId"] for case in plan["cases"]}) == 62


def test_plan_schema_rejects_cartesian_size_and_paths() -> None:
    plan = _valid_plan()
    plan["cases"][0]["paths"] = 100
    plan["cases"][0]["horizon"] = HORIZON

    with pytest.raises(ValidationError):
        _validator(PLAN_SCHEMA_PATH).validate(plan)


@pytest.mark.parametrize(
    ("field_path", "replacement"),
    [
        (("rng", "algorithm"), "MT19937"),
        (("rng", "seed"), 1),
        (("fixedParameters", "dtype"), "float32"),
        (("fixedParameters", "aggregationPeriods"), 4),
        (("fixedParameters", "confidence"), 0.99),
        (("fixedParameters", "significance"), 0.01),
        (("fixedParameters", "horizon"), 253),
        (("protocol", "coldFreshProcesses"), 19),
        (("protocol", "untimedWarmups"), 4),
        (("protocol", "timedWarmSamples"), 49),
        (("protocol", "quantileMethod"), "nearest"),
    ],
)
def test_plan_schema_rejects_protocol_drift(
    field_path: tuple[str, str],
    replacement: object,
) -> None:
    plan = _valid_plan()
    section, field = field_path
    plan[section][field] = replacement

    with pytest.raises(ValidationError):
        _validator(PLAN_SCHEMA_PATH).validate(plan)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("effectiveTrialCount", 3),
        ("samplingFrequency", "monthly"),
        ("trialRegistrySha256", "0" * 64),
        ("varianceDdof", 0),
        ("sharpeEstimateVariance", 0.02),
    ],
)
def test_plan_schema_rejects_dsr_trial_provenance_drift(
    field: str,
    replacement: object,
) -> None:
    plan = _valid_plan()
    plan["dsrTrialProvenance"][field] = replacement

    with pytest.raises(ValidationError):
        _validator(PLAN_SCHEMA_PATH).validate(plan)


def test_report_schema_accepts_a_strict_finite_manifest() -> None:
    manifest = _valid_manifest()
    encoded = json.dumps(
        manifest,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    decoded = json.loads(
        encoded,
        parse_constant=lambda token: pytest.fail(f"non-finite JSON token: {token}"),
    )

    _validator(REPORT_SCHEMA_PATH).validate(decoded)
    assert hashlib.sha256(encoded).hexdigest() == hashlib.sha256(encoded).hexdigest()


def test_report_schema_rejects_non_sha_digests() -> None:
    manifest = _valid_manifest()
    manifest["rawSamplesSha256"] = "not-a-sha"

    with pytest.raises(ValidationError):
        _validator(REPORT_SCHEMA_PATH).validate(manifest)


def test_linux_proc_field_reader_removes_optional_colon_separator(
    tmp_path: Path,
) -> None:
    benchmark = _benchmark_module()
    proc_file = tmp_path / "cpuinfo"
    proc_file.write_text(
        "model name\t: AMD Ryzen Test CPU\nMemTotal:       1024 kB\n",
        encoding="utf-8",
    )

    assert benchmark._read_first_line(proc_file, "model name") == "AMD Ryzen Test CPU"
    assert benchmark._read_first_line(proc_file, "MemTotal:") == "1024 kB"


@pytest.mark.parametrize(
    "ineligible_field",
    [
        "sameHost",
        "sameRun",
        "sameFixture",
        "sameAffinity",
        "sameThreads",
        "sameExecutionBoundary",
        "sameTimedBoundary",
    ],
)
def test_speedup_requires_all_seven_context_equalities(
    ineligible_field: str,
) -> None:
    validator = _validator(REPORT_SCHEMA_PATH)
    manifest = _valid_manifest()
    comparison = manifest["comparisons"][0]
    comparison["eligibility"]["sameTimedBoundary"] = True
    comparison["speedup"] = {"status": "measured", "ratio": 1.25}
    validator.validate(manifest)

    comparison["eligibility"][ineligible_field] = False

    with pytest.raises(ValidationError):
        validator.validate(manifest)

    comparison["speedup"] = _not_applicable("comparison contexts are not ratio-eligible")
    validator.validate(manifest)


def test_report_schema_binds_matrix_case_count_and_timing_boundary() -> None:
    validator = _validator(REPORT_SCHEMA_PATH)
    manifest = _valid_manifest()

    manifest["caseCount"] = 62
    with pytest.raises(ValidationError):
        validator.validate(manifest)

    manifest = _valid_manifest()
    manifest["results"][0]["timingBoundary"] = "compiled_device_numeric_core"
    with pytest.raises(ValidationError):
        validator.validate(manifest)


def test_generated_plan_matches_frozen_schema_and_exact_matrix() -> None:
    plan = _benchmark_module().build_benchmark_plan()

    _validator(PLAN_SCHEMA_PATH).validate(plan)
    assert plan == _valid_plan()


def test_allocation_ledger_uses_the_exact_monotone_equation() -> None:
    benchmark = _benchmark_module()
    actual = benchmark.estimate_peak_allocation_bytes(
        host_input_bytes=100,
        host_output_bytes=10,
        numpy_temporary_bytes=20,
        jax_argument_bytes=30,
        jax_temporary_bytes=40,
        jax_output_bytes=50,
        jax_alias_bytes=5,
    )

    assert actual == 245
    with pytest.raises(ValueError):
        benchmark.estimate_peak_allocation_bytes(
            host_input_bytes=1,
            host_output_bytes=1,
            numpy_temporary_bytes=0,
            jax_argument_bytes=0,
            jax_temporary_bytes=0,
            jax_output_bytes=0,
            jax_alias_bytes=3,
        )


def test_chunk_selection_is_deterministic_binary_search_over_monotone_bound() -> None:
    benchmark = _benchmark_module()
    visited: list[int] = []

    def estimate(chunk_size: int) -> int:
        visited.append(chunk_size)
        return 100 + (10 * chunk_size)

    chunk_size = benchmark.select_deterministic_chunk_size(
        paths=10,
        allocation_cap_bytes=150,
        estimate_peak_bytes=estimate,
        estimator_is_monotone=True,
    )

    assert chunk_size == 5
    assert visited == [5, 8, 6]


def test_chunk_selection_stops_without_monotone_proof_or_safe_single_path() -> None:
    benchmark = _benchmark_module()

    with pytest.raises(ValueError, match="monotone"):
        benchmark.select_deterministic_chunk_size(
            paths=10,
            allocation_cap_bytes=150,
            estimate_peak_bytes=lambda chunk_size: chunk_size,
            estimator_is_monotone=False,
        )
    with pytest.raises(MemoryError):
        benchmark.select_deterministic_chunk_size(
            paths=10,
            allocation_cap_bytes=150,
            estimate_peak_bytes=lambda chunk_size: 151 + chunk_size,
            estimator_is_monotone=True,
        )


def test_chunk_layout_uses_fixed_shape_repeat_last_valid_padding() -> None:
    layout = _benchmark_module().build_chunk_layout(paths=10, chunk_size=4)

    assert layout == {
        "chunkSize": 4,
        "chunkCount": 3,
        "lastChunkValidPaths": 2,
        "paddingPaths": 2,
        "paddingStrategy": "repeat_last_valid",
    }


def test_linear_p50_p95_contract_is_exact() -> None:
    summary = _benchmark_module().linear_quantiles_ns([0, 10, 20, 30])

    assert summary == {
        "method": "linear",
        "p50Nanoseconds": 15.0,
        "p95Nanoseconds": 28.5,
    }


def test_strict_json_bytes_are_deterministic_finite_and_hashable() -> None:
    benchmark = _benchmark_module()
    encoded = benchmark.strict_json_bytes({"b": 1, "a": 2})

    assert encoded == b'{"a":2,"b":1}'
    assert hashlib.sha256(encoded).hexdigest() == (
        "d3626ac30a87e6f7a6428233b3c68299976865fa5508e4267c5415c76af7a772"
    )
    with pytest.raises((TypeError, ValueError)):
        benchmark.strict_json_bytes({"value": float("nan")})


def test_dsr_trial_registry_digest_and_sample_variance_are_frozen() -> None:
    benchmark = _benchmark_module()
    registry = {
        "schemaVersion": "s1.4r-trial-registry-v1",
        "samplingFrequency": "daily",
        "sharpeEstimates": list(DSR_TRIAL_SHARPE_ESTIMATES),
    }
    registry_bytes = benchmark.dsr_trial_registry_bytes()

    assert registry_bytes == _strict_json_bytes(registry)
    assert hashlib.sha256(registry_bytes).hexdigest() == DSR_TRIAL_REGISTRY_SHA256
    assert (
        np.var(
            np.asarray(DSR_TRIAL_SHARPE_ESTIMATES, dtype=np.float64),
            ddof=1,
        )
        == 0.04
    )
    assert benchmark.dsr_trial_provenance_record() == _dsr_trial_provenance()


def test_manifest_invariants_require_same_numpy_jax_chunk_and_padding() -> None:
    benchmark = _benchmark_module()
    manifest = _valid_manifest()
    benchmark.validate_manifest_invariants(
        manifest,
        _valid_plan(),
        _valid_raw_document(),
    )

    manifest["results"][1]["allocation"].update(
        {
            "chunkSize": 2,
            "chunkCount": 1,
            "lastChunkValidPaths": 1,
            "paddingPaths": 1,
        }
    )
    with pytest.raises(ValueError, match="chunk"):
        benchmark.validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


def test_manifest_invariants_bind_plan_raw_samples_and_dsr_provenance() -> None:
    benchmark = _benchmark_module()
    plan = _valid_plan()
    raw_document = _valid_raw_document()
    manifest = _valid_manifest()

    benchmark.validate_manifest_invariants(
        manifest,
        plan=plan,
        raw_document=raw_document,
    )

    bad_raw = copy.deepcopy(raw_document)
    bad_raw["results"][0]["warm"][0] = 6
    with pytest.raises(ValueError, match="raw"):
        benchmark.validate_manifest_invariants(
            manifest,
            plan=plan,
            raw_document=bad_raw,
        )

    bad_plan = copy.deepcopy(plan)
    bad_plan["fixedParameters"]["horizon"] = 253
    with pytest.raises((ValidationError, ValueError)):
        benchmark.validate_manifest_invariants(
            manifest,
            plan=bad_plan,
            raw_document=raw_document,
        )

    bad_manifest = copy.deepcopy(manifest)
    bad_manifest["dsrTrialProvenance"]["trialRegistrySha256"] = "0" * 64
    with pytest.raises((ValidationError, ValueError)):
        benchmark.validate_manifest_invariants(
            bad_manifest,
            plan=plan,
            raw_document=raw_document,
        )


def test_manifest_invariants_require_the_exact_frozen_plan() -> None:
    benchmark = _benchmark_module()
    plan = _valid_plan()
    manifest = _valid_manifest()
    plan["cases"][1]["kernel"] = "realized_variance"
    manifest["planSha256"] = hashlib.sha256(_strict_json_bytes(plan)).hexdigest()

    with pytest.raises(ValueError, match="canonical"):
        benchmark.validate_manifest_invariants(
            manifest,
            plan=plan,
            raw_document=_valid_raw_document(),
        )


def test_manifest_invariants_bind_allocation_to_the_deterministic_ledger() -> None:
    benchmark = _benchmark_module()
    manifest = _valid_manifest()
    allocation = manifest["results"][0]["allocation"]
    for field in (
        "hostInputBytes",
        "hostOutputBytes",
        "numpyTemporaryBytes",
        "jaxArgumentBytes",
        "jaxTemporaryBytes",
        "jaxOutputBytes",
        "jaxAliasBytes",
        "estimatedPeakAllocationBytes",
    ):
        allocation[field] = 0

    with pytest.raises(ValueError, match="deterministic"):
        benchmark.validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


def test_manifest_allocation_requires_the_canonical_maximal_shared_chunk() -> None:
    benchmark = _benchmark_module()
    case = next(
        case
        for case in _valid_plan()["cases"]
        if case["caseId"]
        == "christoffersen_conditional_coverage_test--paths-10000"
    )
    maximal_chunk = int(benchmark._preflight_case(case)["layout"]["chunkSize"])
    assert maximal_chunk == 7_360
    noncanonical_chunk = maximal_chunk - 1
    layout = benchmark.build_chunk_layout(
        paths=int(case["paths"]),
        chunk_size=noncanonical_chunk,
    )

    for implementation in ("numpy", "jax_jit"):
        ledger = benchmark._analytical_ledger(
            case,
            implementation,
            chunk_paths=noncanonical_chunk,
        )
        allocation = {
            "allocationCapBytes": ALLOCATION_CAP_BYTES,
            **ledger,
            "numpyTracemallocPeakBytes": (
                {"status": "measured", "value": 1}
                if implementation == "numpy"
                else _not_applicable(
                    "tracemalloc does not measure JAX/XLA native allocation"
                )
            ),
            "rssBaselineBytes": 100_000_000,
            "rssPeakBytes": 120_000_000,
            "rssDeltaBytes": 20_000_000,
            **layout,
            "allocationEstimator": (
                "numpy_source_bound_plus_tracemalloc_preflight_v2"
                if implementation == "numpy"
                else "jax_compiled_memory_analysis_plus_host_v1"
            ),
            "allocationEstimatorMonotone": True,
            "ledgerEquation": (
                "hostInputBytes+hostOutputBytes+numpyTemporaryBytes+jaxArgumentBytes+"
                "jaxTemporaryBytes+jaxOutputBytes-jaxAliasBytes"
            ),
            "allocationCapPassed": True,
        }
        result = {
            "axis": "path_batch",
            "paths": int(case["paths"]),
            "implementation": implementation,
            "allocation": allocation,
        }

        with pytest.raises(ValueError, match="canonical maximal"):
            benchmark._validate_allocation(result, case)


def test_manifest_invariants_require_same_fixture_and_parity_claims() -> None:
    benchmark = _benchmark_module()
    manifest = _valid_manifest()
    manifest["results"][1]["fixtureSha256"] = "b" * 64

    with pytest.raises(ValueError, match="fixture"):
        benchmark.validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )

    manifest = _valid_manifest()
    manifest["results"][1]["maxToleranceRatio"] = 0.5
    with pytest.raises(ValueError, match="parity"):
        benchmark.validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


def test_report_schema_rejects_failed_normalized_parity_claim() -> None:
    manifest = _valid_manifest()
    manifest["results"][0]["maxToleranceRatio"] = 1.000_000_1

    with pytest.raises(ValidationError):
        _validator(REPORT_SCHEMA_PATH).validate(manifest)


@pytest.mark.parametrize(
    ("implementation", "cold_total"),
    [("numpy", 9), ("jax_jit", 49)],
)
def test_manifest_invariants_reject_impossible_cold_phase_accounting(
    implementation: str,
    cold_total: int,
) -> None:
    benchmark = _benchmark_module()
    manifest = _valid_manifest()
    raw_document = _valid_raw_document()
    raw = next(
        item
        for item in raw_document["results"]
        if item["implementation"] == implementation
    )
    for sample in raw["cold"]:
        sample["coldTotal"] = cold_total
    result = next(
        item for item in manifest["results"] if item["implementation"] == implementation
    )
    result["latencies"]["coldTotal"] = _latency(
        float(cold_total),
        float(cold_total),
    )
    _rebind_raw_evidence(manifest, raw_document)

    with pytest.raises(ValueError, match="cold"):
        benchmark.validate_manifest_invariants(
            manifest,
            _valid_plan(),
            raw_document,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("shape", [31]),
        ("dtype", "float32"),
        ("staticArgs", {"aggregationPeriods": 5}),
    ],
)
def test_manifest_invariants_bind_jax_compile_signature_to_case(
    field: str,
    replacement: object,
) -> None:
    manifest = _valid_manifest()
    manifest["results"][1]["compileSignature"][field] = replacement

    with pytest.raises(
        (ValidationError, ValueError),
        match=r"compile|float64",
    ):
        _benchmark_module().validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


def test_manifest_invariants_derive_result_identity() -> None:
    manifest = _valid_manifest()
    manifest["results"][0]["resultId"] = "tampered-result-id"

    with pytest.raises(ValueError, match="resultId"):
        _benchmark_module().validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


@pytest.mark.parametrize("target", ["document", "result"])
def test_manifest_invariants_require_closed_raw_evidence(target: str) -> None:
    manifest = _valid_manifest()
    raw_document = _valid_raw_document()
    if target == "document":
        raw_document["unexpected"] = True
    else:
        raw_document["results"][0]["unexpected"] = True
    _rebind_raw_evidence(manifest, raw_document)

    with pytest.raises(ValueError, match="raw"):
        _benchmark_module().validate_manifest_invariants(
            manifest,
            _valid_plan(),
            raw_document,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda execution: execution.update({"cpuModel": "mutated CPU"}),
        lambda execution: execution.update(
            {"physicalCores": 9, "logicalCores": 8}
        ),
        lambda execution: execution.update({"cpuAffinity": [8]}),
        lambda execution: execution.update(
            {"threadEnvironment": {"OMP_NUM_THREADS": "2"}}
        ),
        lambda execution: execution.update(
            {
                "containerRuntime": {"status": "measured", "value": "docker"},
                "containerImageId": {
                    "status": "measured",
                    "value": f"sha256:{'9' * 64}",
                },
            }
        ),
    ],
    ids=[
        "stale-host-fingerprint",
        "impossible-core-topology",
        "affinity-outside-logical-cores",
        "thread-environment-drift",
        "container-identity-on-host",
    ],
)
def test_manifest_invariants_bind_execution_environment(
    mutate: Callable[[JsonObject], object],
) -> None:
    manifest = _valid_manifest()
    mutate(manifest["execution"])

    with pytest.raises((ValidationError, ValueError)):
        _benchmark_module().validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda artifacts: artifacts["researchWheel"].update({"bytes": 0}),
        lambda artifacts: artifacts["installedResearchEnvironment"].update(
            {"measurementMethod": "allocated_disk_bytes"}
        ),
        lambda artifacts: artifacts["ociImage"].update(
            {"measurementMethod": "unbound-inspection"}
        ),
        lambda artifacts: artifacts["ociArchive"].update(
            {
                "uncompressedBytes": 100,
                "compressedBytes": 101,
            }
        ),
        lambda artifacts: artifacts["nativeExecutable"].update(
            {"reason": "changed scope"}
        ),
    ],
    ids=[
        "zero-sized-wheel",
        "environment-method",
        "image-method",
        "compressed-larger-than-source",
        "native-status",
    ],
)
def test_manifest_invariants_bind_artifact_claims(
    mutate: Callable[[JsonObject], object],
) -> None:
    manifest = _valid_manifest()
    mutate(manifest["artifacts"])

    with pytest.raises((ValidationError, ValueError)):
        _benchmark_module().validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["results"][0]["allocation"].update(
            {"estimatedPeakAllocationBytes": 1}
        ),
        lambda manifest: manifest["results"][0]["allocation"].update(
            {
                "estimatedPeakAllocationBytes": ALLOCATION_CAP_BYTES + 1,
                "allocationCapPassed": True,
            }
        ),
        lambda manifest: manifest["results"][0]["latencies"]["warm"].update(
            {"p50Nanoseconds": 20.0, "p95Nanoseconds": 10.0}
        ),
        lambda manifest: manifest["results"][0]["throughput"].update(
            {"pathsPerSecond": _throughput()}
        ),
        lambda manifest: manifest["results"][0]["throughput"].update(
            {"callsPerSecond": _throughput(1.0)}
        ),
    ],
    ids=[
        "ledger-equation",
        "allocation-cap",
        "quantile-order",
        "throughput-unit",
        "throughput-value",
    ],
)
def test_manifest_cross_field_invariants_reject_invalid_claims(
    mutate: Callable[[JsonObject], object],
) -> None:
    manifest = copy.deepcopy(_valid_manifest())
    mutate(manifest)

    with pytest.raises(ValueError):
        _benchmark_module().validate_manifest_invariants(
            manifest,
            _valid_plan(),
            _valid_raw_document(),
        )


def test_speedup_eligibility_derives_seven_required_equalities() -> None:
    benchmark = _benchmark_module()
    left = {
        "hostFingerprint": "a" * 64,
        "runId": "run-0001",
        "fixtureSha256": "b" * 64,
        "cpuAffinity": [0],
        "threadEnvironment": {"OMP_NUM_THREADS": "1"},
        "executionBoundary": "wsl2",
        "timingBoundary": "compiled_device_numeric_core",
    }
    right = copy.deepcopy(left)

    assert benchmark.speedup_eligibility(left, right) == {
        "sameHost": True,
        "sameRun": True,
        "sameFixture": True,
        "sameAffinity": True,
        "sameThreads": True,
        "sameExecutionBoundary": True,
        "sameTimedBoundary": True,
        "eligible": True,
    }

    right["executionBoundary"] = "oci"
    eligibility = benchmark.speedup_eligibility(left, right)
    assert eligibility["sameExecutionBoundary"] is False
    assert eligibility["eligible"] is False

    right = copy.deepcopy(left)
    right["timingBoundary"] = "validated_public_reference"
    eligibility = benchmark.speedup_eligibility(left, right)
    assert eligibility["sameTimedBoundary"] is False
    assert eligibility["eligible"] is False


def _path_fixture(kernel: str) -> np.ndarray:
    returns = np.asarray(
        [
            [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0],
            [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0],
            [4.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0],
        ],
        dtype=np.float64,
    )
    if kernel == "historical_expected_shortfall":
        return np.abs(returns)
    if kernel == "probabilistic_sharpe_ratio":
        return np.asarray(
            [
                [1.0, 0.0, 8.0, 0.0, 3.0],
                [0.5, 0.1, 8.0, 0.2, 3.0],
                [-0.25, -0.5, 8.0, -0.1, 3.0],
            ],
            dtype=np.float64,
        )
    if kernel == "deflated_sharpe_ratio":
        return np.asarray(
            [
                [1.0, 8.0, 0.0, 3.0, 2.0, 0.04],
                [0.5, 8.0, 0.2, 3.0, 2.0, 0.04],
                [-0.25, 8.0, -0.1, 3.0, 2.0, 0.04],
            ],
            dtype=np.float64,
        )
    if kernel in BACKTEST_KERNELS:
        exceptions = np.asarray(
            [
                [0, 0, 1, 0, 1, 1, 0, 0],
                [1, 0, 0, 1, 1, 0, 1, 0],
                [0, 1, 1, 0, 0, 1, 0, 1],
            ],
            dtype=np.float64,
        )
        realized = np.where(exceptions == 1.0, 2.0, 0.0)
        forecast = np.ones_like(realized)
        return np.stack((realized, forecast), axis=1)
    return returns


def _numpy_vmap_expected(
    worker: ModuleType,
    kernel: str,
    fixture: np.ndarray,
) -> tuple[np.ndarray, ...]:
    results = [worker._numpy_single(kernel, row) for row in fixture]  # type: ignore[attr-defined]
    rows = [
        worker._numpy_parity_fields(kernel, result)  # type: ignore[attr-defined]
        for result in results
    ]
    return tuple(
        np.asarray([row[index] for row in rows], dtype=np.float64) for index in range(len(rows[0]))
    )


@pytest.mark.parametrize("kernel", PATH_KERNELS)
def test_all_nine_path_kernels_use_jit_vmap_with_numpy_parity(kernel: str) -> None:
    worker = importlib.import_module("benchmarks.worker")
    fixture = _path_fixture(kernel)
    case = {"kernel": kernel, "axis": "path_batch"}
    mapped = worker._jax_packed_kernel(case)  # type: ignore[attr-defined]
    device_fixture = jnp.asarray(fixture, dtype=jnp.float64)
    actual_tree = jax.jit(mapped)(device_fixture)
    actual_tree = jax.tree.map(lambda value: value.block_until_ready(), actual_tree)
    actual = jax.device_get(actual_tree)
    actual_fields = actual if isinstance(actual, tuple) else (actual,)
    expected_fields = _numpy_vmap_expected(worker, kernel, fixture)

    assert len(actual_fields) == len(expected_fields)
    for actual_field, expected_field in zip(
        actual_fields,
        expected_fields,
        strict=True,
    ):
        actual_array = np.asarray(actual_field)
        assert actual_array.dtype == np.dtype(np.float64)
        assert actual_array.shape == (fixture.shape[0],)
        assert np.isfinite(actual_array).all()
        np.testing.assert_allclose(
            actual_array,
            expected_field,
            rtol=1e-10,
            atol=1e-12,
        )


def test_dsr_benchmark_packed_kernel_routes_trials_and_variance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from s1_4r_risk_research import _jax_kernels

    worker = importlib.import_module("benchmarks.worker")
    original = _jax_kernels.deflated_sharpe_ratio
    calls: list[tuple[float, ...]] = []

    def recording_kernel(*arguments: jax.Array) -> jax.Array:
        calls.append(
            tuple(float(jax.device_get(argument)) for argument in arguments)
        )
        return original(*arguments)

    monkeypatch.setattr(
        _jax_kernels,
        "deflated_sharpe_ratio",
        recording_kernel,
    )
    packed = jnp.asarray([1.0, 8.0, 0.0, 3.0, 2.0, 0.04], dtype=jnp.float64)
    kernel = worker._jax_packed_kernel(  # type: ignore[attr-defined]
        {"kernel": "deflated_sharpe_ratio", "axis": "one_dimensional"}
    )

    result = kernel(packed)
    result.block_until_ready()

    assert np.isfinite(float(jax.device_get(result)))
    assert calls == [(1.0, 8.0, 0.0, 3.0, 2.0, 0.04)]


def _terminal_call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_benchmark_worker_has_only_explicit_leading_axis_vmap() -> None:
    tree = ast.parse(
        WORKER_SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(WORKER_SOURCE_PATH),
    )
    vmap_calls: list[ast.Call] = []
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _terminal_call_name(node)
        if name == "grad":
            violations.append((node.lineno, "grad"))
        if name in {"argwhere", "compress", "flatnonzero", "nonzero"}:
            violations.append((node.lineno, name))
        if name == "where":
            keyword_names = {keyword.arg for keyword in node.keywords}
            if len(node.args) < 3 and not {"x", "y"} <= keyword_names:
                violations.append((node.lineno, "one-argument-where"))
        if name != "vmap":
            continue
        vmap_calls.append(node)
        keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
        for axis_name in ("in_axes", "out_axes"):
            axis = keyword_values.get(axis_name)
            if not isinstance(axis, ast.Constant) or axis.value != 0:
                violations.append((node.lineno, f"{axis_name}-not-leading-zero"))

    assert len(vmap_calls) == 1
    assert violations == [], f"forbidden benchmark transform found: {violations}"


def test_benchmark_worker_streams_path_chunks_without_materializing_iterator() -> None:
    tree = ast.parse(
        WORKER_SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(WORKER_SOURCE_PATH),
    )
    violations: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _terminal_call_name(node) in {"list", "tuple"}
            and any(
                isinstance(child, ast.Call)
                and _terminal_call_name(child) == "_iter_path_chunks"
                for argument in node.args
                for child in ast.walk(argument)
            )
        ):
            violations.append(node.lineno)

    assert violations == [], (
        "path chunk iterator must remain streaming; materialization at "
        f"lines {violations} retains unledgered host chunks"
    )


def test_benchmark_worker_exposes_only_controller_produced_modes() -> None:
    tree = ast.parse(
        WORKER_SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(WORKER_SOURCE_PATH),
    )
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "_correctness" not in function_names
    assert "_jax_public_single" not in function_names
    assert "correctness" not in string_literals


def test_worker_environment_freezes_cpu_x64_threads_cache_and_tmp() -> None:
    environment = _benchmark_module()._worker_environment()

    assert {
        "JAX_PLATFORMS": environment["JAX_PLATFORMS"],
        "JAX_ENABLE_X64": environment["JAX_ENABLE_X64"],
        "JAX_ENABLE_COMPILATION_CACHE": environment["JAX_ENABLE_COMPILATION_CACHE"],
        "PYTHONHASHSEED": environment["PYTHONHASHSEED"],
        "TMPDIR": environment["TMPDIR"],
        "TEMP": environment["TEMP"],
        "TMP": environment["TMP"],
        "OMP_NUM_THREADS": environment["OMP_NUM_THREADS"],
        "OPENBLAS_NUM_THREADS": environment["OPENBLAS_NUM_THREADS"],
        "MKL_NUM_THREADS": environment["MKL_NUM_THREADS"],
        "NUMEXPR_NUM_THREADS": environment["NUMEXPR_NUM_THREADS"],
    } == {
        "JAX_PLATFORMS": "cpu",
        "JAX_ENABLE_X64": "1",
        "JAX_ENABLE_COMPILATION_CACHE": "0",
        "PYTHONHASHSEED": "0",
        "TMPDIR": "/tmp",
        "TEMP": "/tmp",
        "TMP": "/tmp",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def test_measured_preflight_preserves_analytical_max_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = _benchmark_module()
    case = benchmark.build_benchmark_plan()["cases"][0]
    preflight = benchmark._preflight_case(case)
    jax_ledger = copy.deepcopy(preflight["ledgers"]["jax_jit"])
    numpy_ledger = copy.deepcopy(preflight["ledgers"]["numpy"])
    jax_worker = {
        "memoryAnalysis": {
            "argumentBytes": jax_ledger["jaxArgumentBytes"],
            "temporaryBytes": jax_ledger["jaxTemporaryBytes"],
            "outputBytes": jax_ledger["jaxOutputBytes"],
            "aliasBytes": 0,
        }
    }
    numpy_worker = {
        "numpyTracemallocPeakBytes": numpy_ledger["numpyTemporaryBytes"],
    }
    monkeypatch.setattr(
        benchmark,
        "_compiled_jax_preflight",
        lambda _case, _chunk: (jax_ledger, jax_worker),
    )
    monkeypatch.setattr(
        benchmark,
        "_measured_numpy_preflight",
        lambda _case, _fixture, _chunk: (numpy_ledger, numpy_worker),
    )
    original_layout = copy.deepcopy(preflight["layout"])

    benchmark._refine_preflight_with_measured_memory(case, {}, preflight)

    assert preflight["layout"] == original_layout
    assert preflight["ledgers"] == {
        "numpy": numpy_ledger,
        "jax_jit": jax_ledger,
    }
    assert preflight["memoryPreflight"] == {
        "numpy": numpy_worker,
        "jax_jit": jax_worker,
    }


def test_memory_analysis_aggregation_uses_worst_case_minimum_alias() -> None:
    benchmark = _benchmark_module()
    workers = [
        {
            "memoryAnalysis": {
                "argumentBytes": 10,
                "temporaryBytes": 20,
                "outputBytes": 30,
                "aliasBytes": 30,
            }
        },
        {
            "memoryAnalysis": {
                "argumentBytes": 40,
                "temporaryBytes": 5,
                "outputBytes": 6,
                "aliasBytes": 0,
            }
        },
    ]

    assert benchmark._max_memory_analysis(workers) == {
        "argumentBytes": 40,
        "temporaryBytes": 20,
        "outputBytes": 30,
        "aliasBytes": 0,
    }


@pytest.mark.parametrize("implementation", ["numpy", "jax_jit"])
def test_measured_preflight_fails_when_source_upper_bound_is_exceeded(
    implementation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = _benchmark_module()
    case = benchmark.build_benchmark_plan()["cases"][0]
    preflight = benchmark._preflight_case(case)
    jax_ledger = copy.deepcopy(preflight["ledgers"]["jax_jit"])
    numpy_ledger = copy.deepcopy(preflight["ledgers"]["numpy"])
    jax_worker = {
        "memoryAnalysis": {
            "argumentBytes": jax_ledger["jaxArgumentBytes"],
            "temporaryBytes": jax_ledger["jaxTemporaryBytes"]
            + (1 if implementation == "jax_jit" else 0),
            "outputBytes": jax_ledger["jaxOutputBytes"],
            "aliasBytes": 0,
        }
    }
    numpy_worker = {
        "numpyTracemallocPeakBytes": numpy_ledger["numpyTemporaryBytes"]
        + (1 if implementation == "numpy" else 0),
    }
    monkeypatch.setattr(
        benchmark,
        "_compiled_jax_preflight",
        lambda _case, _chunk: (jax_ledger, jax_worker),
    )
    monkeypatch.setattr(
        benchmark,
        "_measured_numpy_preflight",
        lambda _case, _fixture, _chunk: (numpy_ledger, numpy_worker),
    )

    with pytest.raises(MemoryError, match="upper bound"):
        benchmark._refine_preflight_with_measured_memory(case, {}, preflight)


def test_fixture_parity_rejects_truncated_worker_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = _benchmark_module()
    case = benchmark.build_benchmark_plan()["cases"][0]
    monkeypatch.setattr(
        benchmark,
        "_run_worker",
        lambda _job: {"evaluations": 1, "values": []},
    )

    with pytest.raises(RuntimeError, match="cardinality"):
        benchmark._fixture_parity(
            case,
            {"path": Path("/unused"), "shape": [32], "sha256": "a" * 64},
            {
                "chunkSize": 1,
                "chunkCount": 1,
                "lastChunkValidPaths": 1,
                "paddingPaths": 0,
                "paddingStrategy": "repeat_last_valid",
            },
        )


def test_worker_parity_consumes_non_divisible_padding_and_all_raw_fields() -> None:
    worker = importlib.import_module("benchmarks.worker")
    kernel = "christoffersen_conditional_coverage_test"
    fixture = _path_fixture(kernel)
    base_job = {
        "case": {
            "caseId": "conditional-padding",
            "kernel": kernel,
            "axis": "path_batch",
            "paths": 3,
        },
        "chunkSize": 2,
    }

    numpy_result = worker._parity(  # type: ignore[attr-defined]
        {**base_job, "implementation": "numpy"},
        fixture,
    )
    jax_result = worker._parity(  # type: ignore[attr-defined]
        {**base_job, "implementation": "jax_jit"},
        fixture,
    )

    assert numpy_result["evaluations"] == jax_result["evaluations"] == 3
    assert len(numpy_result["values"]) == len(jax_result["values"]) == 3 * 12
    np.testing.assert_allclose(
        numpy_result["values"],
        jax_result["values"],
        rtol=1e-10,
        atol=1e-12,
    )


def _add_tar_bytes(
    archive: tarfile.TarFile,
    name: str,
    payload: bytes,
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))


def _write_test_oci_archive(
    path: Path,
    *,
    include_layer: bool,
    layer_size_delta: int = 0,
) -> JsonObject:
    layer_bytes = b"compressed-layer"
    layer_digest = f"sha256:{hashlib.sha256(layer_bytes).hexdigest()}"
    diff_id = f"sha256:{hashlib.sha256(b'uncompressed-layer').hexdigest()}"
    config_bytes = _strict_json_bytes(
        {
            "architecture": "amd64",
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
        }
    )
    config_digest = f"sha256:{hashlib.sha256(config_bytes).hexdigest()}"
    manifest_bytes = _strict_json_bytes(
        {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config_bytes),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": layer_digest,
                    "size": len(layer_bytes) + layer_size_delta,
                }
            ],
        }
    )
    manifest_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    index_bytes = _strict_json_bytes(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": manifest_digest,
                    "size": len(manifest_bytes),
                }
            ],
        }
    )
    with tarfile.open(path, mode="w") as archive:
        _add_tar_bytes(archive, "index.json", index_bytes)
        _add_tar_bytes(
            archive,
            f"blobs/sha256/{manifest_digest.removeprefix('sha256:')}",
            manifest_bytes,
        )
        _add_tar_bytes(
            archive,
            f"blobs/sha256/{config_digest.removeprefix('sha256:')}",
            config_bytes,
        )
        if include_layer:
            _add_tar_bytes(
                archive,
                f"blobs/sha256/{layer_digest.removeprefix('sha256:')}",
                layer_bytes,
            )
    return {
        "manifestDigest": manifest_digest,
        "configDigest": config_digest,
        "rootfsDiffIds": [diff_id],
    }


def test_oci_archive_identity_verifies_every_descriptor_and_layer(
    tmp_path: Path,
) -> None:
    benchmark = _benchmark_module()
    valid = tmp_path / "valid.oci.tar"
    expected = _write_test_oci_archive(valid, include_layer=True)

    assert benchmark._oci_archive_identity(valid) == expected

    missing = tmp_path / "missing-layer.oci.tar"
    _write_test_oci_archive(missing, include_layer=False)
    with pytest.raises(RuntimeError, match="missing"):
        benchmark._oci_archive_identity(missing)

    wrong_size = tmp_path / "wrong-layer-size.oci.tar"
    _write_test_oci_archive(
        wrong_size,
        include_layer=True,
        layer_size_delta=1,
    )
    with pytest.raises(RuntimeError, match="size"):
        benchmark._oci_archive_identity(wrong_size)


def test_loaded_image_identity_accepts_descriptor_and_rejects_drift() -> None:
    benchmark = _benchmark_module()
    identity = {
        "manifestDigest": f"sha256:{'a' * 64}",
        "configDigest": f"sha256:{'b' * 64}",
        "rootfsDiffIds": [f"sha256:{'c' * 64}"],
    }
    image = {
        "Id": identity["configDigest"],
        "Descriptor": {"digest": identity["manifestDigest"]},
        "RootFS": {"Layers": identity["rootfsDiffIds"]},
    }

    assert (
        benchmark._verify_loaded_image_identity(image, identity)
        == "docker_descriptor_matches_oci_manifest"
    )
    image["Descriptor"]["digest"] = f"sha256:{'d' * 64}"
    with pytest.raises(RuntimeError, match="differs"):
        benchmark._verify_loaded_image_identity(image, identity)


def test_generated_report_contains_all_required_evidence_sections(
    tmp_path: Path,
) -> None:
    benchmark = _benchmark_module()
    report_path = tmp_path / "benchmark-report.md"

    benchmark._write_report(report_path, _valid_manifest(), "smallest")

    report = report_path.read_text(encoding="utf-8")
    for marker in (
        "Question and non-goals",
        "Run and matrix",
        "Correctness and timing boundaries",
        "Trace/lower",
        "Throughput",
        "Memory and chunking",
        "Comparison eligibility",
        "Environment",
        "DSR provenance",
        "DSR numeric core recomputes",
        "Artifact sizes and identity",
        "Limitations and conclusion",
        "no production replacement conclusion",
    ):
        assert marker in report
    assert "provenance/benchmark construction remains outside" not in report
