"""Deterministic controller for the bounded S1.4R NumPy/JAX benchmark."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    FormatChecker,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_SCHEMA_PATH = PROJECT_ROOT / "benchmarks/schemas/benchmark-plan.schema.json"
REPORT_SCHEMA_PATH = PROJECT_ROOT / "benchmarks/schemas/benchmark-report.schema.json"

ALLOCATION_CAP_BYTES = 536_870_912
RNG_SEED = 20_260_717
ONE_DIMENSIONAL_SIZES = (32, 252, 1_000, 10_000, 100_000)
PATH_COUNTS = (100, 1_000, 10_000)
HORIZON = 252
COLD_FRESH_PROCESSES = 20
UNTIMED_WARMUPS = 5
TIMED_WARM_SAMPLES = 50
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
DSR_TRIAL_SHARPE_ESTIMATES = (
    -0.1414213562373095,
    0.1414213562373095,
)
PATH_KERNELS = ONE_DIMENSIONAL_KERNELS + SCALAR_EVALUATION_KERNELS
BACKTEST_KERNELS = {
    "kupiec_unconditional_coverage_test",
    "christoffersen_independence_test",
    "christoffersen_conditional_coverage_test",
}
# Source-level upper bounds on simultaneously live float64-equivalent buffers.
# They include validation copies, padding, sort/reduction intermediates, boolean
# transition masks, and one extra safety buffer; measured preflight must still pass.
NUMPY_LIVE_BUFFER_UPPER_BOUNDS = {
    "historical_expected_shortfall": 10,
    "realized_variance": 4,
    "realized_volatility_intraday": 4,
    "lo_adjusted_sharpe_ratio": 12,
    "probabilistic_sharpe_ratio": 4,
    "deflated_sharpe_ratio": 4,
    "kupiec_unconditional_coverage_test": 16,
    "christoffersen_independence_test": 16,
    "christoffersen_conditional_coverage_test": 16,
}
# Traced result containers and fixed ndarray metadata are data-working-set
# overheads but do not scale with input bytes; import/runtime baseline is primed away.
NUMPY_TRACED_FIXED_OVERHEAD_BYTES = 65_536
JAX_ANALYTICAL_BUFFER_UPPER_BOUNDS = {
    "historical_expected_shortfall": 12,
    "realized_variance": 8,
    "realized_volatility_intraday": 8,
    "lo_adjusted_sharpe_ratio": 12,
    "probabilistic_sharpe_ratio": 8,
    "deflated_sharpe_ratio": 8,
    "kupiec_unconditional_coverage_test": 16,
    "christoffersen_independence_test": 16,
    "christoffersen_conditional_coverage_test": 16,
}
LEDGER_EQUATION = (
    "hostInputBytes+hostOutputBytes+numpyTemporaryBytes+jaxArgumentBytes+"
    "jaxTemporaryBytes+jaxOutputBytes-jaxAliasBytes"
)
THREAD_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}
PARITY_RELATIVE_TOLERANCE = 1e-10
PARITY_ABSOLUTE_TOLERANCE = 1e-12

JsonObject = dict[str, Any]


def strict_json_bytes(value: object) -> bytes:
    """Canonical strict JSON bytes를 만들어 NaN/Infinity와 비결정적 key 순서를 거부한다."""

    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def dsr_trial_registry_bytes() -> bytes:
    """Tracked plan과 generated artifact가 공유하는 canonical trial registry bytes다."""

    return strict_json_bytes(
        {
            "schemaVersion": "s1.4r-trial-registry-v1",
            "samplingFrequency": "daily",
            "sharpeEstimates": list(DSR_TRIAL_SHARPE_ESTIMATES),
        }
    )


def dsr_trial_provenance_record() -> JsonObject:
    """Deterministic two-trial registry에서 DSR variance와 provenance를 파생한다."""

    registry_bytes = dsr_trial_registry_bytes()
    variance = float(
        np.var(
            np.asarray(DSR_TRIAL_SHARPE_ESTIMATES, dtype=np.float64),
            ddof=1,
        )
    )
    return {
        "schemaVersion": "s1.4r-effective-trials-v1",
        "method": "pre_registered_independent",
        "rawTrialCount": 2,
        "effectiveTrialCount": 2,
        "samplingFrequency": "daily",
        "trialRegistrySha256": _sha256_bytes(registry_bytes),
        "varianceDdof": 1,
        "sharpeEstimateVariance": variance,
        "registrySerialization": "strict-json-sort-keys-utf8-v1",
    }


def _load_json(path: Path) -> JsonObject:
    return cast(
        JsonObject,
        json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token: {token}")
            ),
        ),
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(strict_json_bytes(value) + b"\n")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validator(path: Path) -> Draft202012Validator:
    schema = _load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _static_args(kernel: str) -> JsonObject:
    return {"aggregationPeriods": 5} if kernel == "lo_adjusted_sharpe_ratio" else {}


def _benchmark_cases() -> list[JsonObject]:
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
            scalar = kernel in SCALAR_EVALUATION_KERNELS
            cases.append(
                {
                    "caseId": f"{kernel}--paths-{paths}",
                    "kernel": kernel,
                    "axis": "path_batch",
                    "size": None,
                    "paths": paths,
                    "horizon": HORIZON,
                    "timedInputKind": (
                        "scalar_parameters" if scalar else "path_observation_matrix"
                    ),
                    "throughputUnits": (
                        ["calls_per_second", "evaluations_per_second"]
                        if scalar
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


def build_benchmark_plan() -> JsonObject:
    """고정된 62-case non-Cartesian benchmark plan을 반환한다."""

    plan: JsonObject = {
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
        "dsrTrialProvenance": dsr_trial_provenance_record(),
        "protocol": {
            "coldFreshProcesses": COLD_FRESH_PROCESSES,
            "untimedWarmups": UNTIMED_WARMUPS,
            "timedWarmSamples": TIMED_WARM_SAMPLES,
            "quantileMethod": "linear",
            "timer": "perf_counter_ns",
            "compilationCacheEnabled": False,
        },
        "timedImplementations": ["numpy", "jax_jit"],
        "cases": _benchmark_cases(),
    }
    _validator(PLAN_SCHEMA_PATH).validate(plan)
    return plan


def estimate_peak_allocation_bytes(
    *,
    host_input_bytes: int,
    host_output_bytes: int,
    numpy_temporary_bytes: int,
    jax_argument_bytes: int,
    jax_temporary_bytes: int,
    jax_output_bytes: int,
    jax_alias_bytes: int,
) -> int:
    """Monotone analytical ledger의 exact working-set byte 합을 반환한다."""

    values = (
        host_input_bytes,
        host_output_bytes,
        numpy_temporary_bytes,
        jax_argument_bytes,
        jax_temporary_bytes,
        jax_output_bytes,
        jax_alias_bytes,
    )
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("allocation ledger values must be non-negative integers")
    if jax_alias_bytes > jax_argument_bytes + jax_output_bytes:
        raise ValueError("JAX alias bytes exceed aliasable argument/output bytes")
    result = (
        host_input_bytes
        + host_output_bytes
        + numpy_temporary_bytes
        + jax_argument_bytes
        + jax_temporary_bytes
        + jax_output_bytes
        - jax_alias_bytes
    )
    if result < 0:
        raise ValueError("allocation ledger became negative")
    return result


def select_deterministic_chunk_size(
    *,
    paths: int,
    allocation_cap_bytes: int,
    estimate_peak_bytes: Callable[[int], int],
    estimator_is_monotone: bool,
) -> int:
    """증명된 monotone upper bound에서 cap을 만족하는 최대 chunk를 이진 탐색한다."""

    if type(paths) is not int or paths < 1:
        raise ValueError("paths must be a positive integer")
    if type(allocation_cap_bytes) is not int or allocation_cap_bytes < 1:
        raise ValueError("allocation cap must be a positive integer")
    if estimator_is_monotone is not True:
        raise ValueError("deterministic binary search requires a monotone estimator")
    low = 1
    high = paths
    best = 0
    while low <= high:
        middle = (low + high) // 2
        estimate = estimate_peak_bytes(middle)
        if type(estimate) is not int or estimate < 0:
            raise ValueError("allocation estimator returned an invalid byte count")
        if estimate <= allocation_cap_bytes:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    if best == 0:
        raise MemoryError("allocation cap is exceeded even with chunkSize=1")
    return best


def build_chunk_layout(*, paths: int, chunk_size: int) -> JsonObject:
    """고정 shape의 repeat-last-valid padding layout을 반환한다."""

    if type(paths) is not int or paths < 1:
        raise ValueError("paths must be a positive integer")
    if type(chunk_size) is not int or chunk_size < 1 or chunk_size > paths:
        raise ValueError("chunk_size must be in [1, paths]")
    chunk_count = math.ceil(paths / chunk_size)
    last_valid = paths - ((chunk_count - 1) * chunk_size)
    return {
        "chunkSize": chunk_size,
        "chunkCount": chunk_count,
        "lastChunkValidPaths": last_valid,
        "paddingPaths": chunk_size - last_valid,
        "paddingStrategy": "repeat_last_valid",
    }


def linear_quantiles_ns(samples: Sequence[int | float]) -> JsonObject:
    """NumPy linear method로 p50/p95 nanosecond summary를 계산한다."""

    if not samples:
        raise ValueError("at least one timing sample is required")
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 1 or not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("timing samples must be finite non-negative values")
    p50, p95 = np.quantile(values, [0.5, 0.95], method="linear")
    return {
        "method": "linear",
        # JSON evidence에서 28.499999999999996 같은 binary 표현 잡음을 제거한다.
        "p50Nanoseconds": float(np.round(p50, decimals=6)),
        "p95Nanoseconds": float(np.round(p95, decimals=6)),
    }


def speedup_eligibility(left: JsonObject, right: JsonObject) -> JsonObject:
    """두 result context가 speedup ratio 자격의 여섯 equality를 만족하는지 계산한다."""

    eligibility: JsonObject = {
        "sameHost": left["hostFingerprint"] == right["hostFingerprint"],
        "sameRun": left["runId"] == right["runId"],
        "sameFixture": left["fixtureSha256"] == right["fixtureSha256"],
        "sameAffinity": left["cpuAffinity"] == right["cpuAffinity"],
        "sameThreads": left["threadEnvironment"] == right["threadEnvironment"],
        "sameExecutionBoundary": (left["executionBoundary"] == right["executionBoundary"]),
        "sameTimedBoundary": left["timingBoundary"] == right["timingBoundary"],
    }
    eligibility["eligible"] = all(bool(value) for value in eligibility.values())
    return eligibility


def _is_measured(value: JsonObject) -> bool:
    return value.get("status") == "measured"


def _validate_throughput(result: JsonObject) -> None:
    throughput = result["throughput"]
    measured = {name for name, value in throughput.items() if _is_measured(value)}
    if result["axis"] == "one_dimensional":
        expected = {"callsPerSecond", "observationsPerSecond"}
    elif result["timedInputKind"] == "scalar_parameters":
        expected = {"callsPerSecond", "evaluationsPerSecond"}
    else:
        expected = {
            "callsPerSecond",
            "pathsPerSecond",
            "pathObservationsPerSecond",
        }
    if measured != expected:
        raise ValueError(
            f"throughput units do not match {result['timedInputKind']}: {sorted(measured)}"
        )


def _validate_allocation(
    result: JsonObject,
    plan_case: JsonObject,
) -> None:
    """Canonical maximal shared chunk와 ledger를 다시 계산해 자기주장을 거부한다."""

    allocation = result["allocation"]
    implementation = str(result["implementation"])
    canonical_preflight = _preflight_case(plan_case)
    expected_layout = canonical_preflight["layout"]
    for field, expected_value in expected_layout.items():
        if allocation[field] != expected_value:
            raise ValueError(
                f"chunk layout differs from canonical maximal layout field {field}"
            )
    deterministic = canonical_preflight["ledgers"][implementation]
    for field, expected_value in deterministic.items():
        if allocation[field] != expected_value:
            raise ValueError(
                f"allocation differs from deterministic ledger field {field}"
            )
    expected = int(deterministic["estimatedPeakAllocationBytes"])
    if allocation["ledgerEquation"] != LEDGER_EQUATION:
        raise ValueError("allocation ledger equation drifted")
    if expected > allocation["allocationCapBytes"]:
        raise ValueError("allocation cap exceeded")
    if allocation["allocationCapPassed"] is not True:
        raise ValueError("cap-compliant result was not marked as passed")
    if (
        allocation["rssPeakBytes"] - allocation["rssBaselineBytes"]
        != allocation["rssDeltaBytes"]
    ):
        raise ValueError("RSS delta does not match peak minus baseline")
    expected_estimator = (
        "numpy_source_bound_plus_tracemalloc_preflight_v2"
        if implementation == "numpy"
        else "jax_compiled_memory_analysis_plus_host_v1"
    )
    if allocation["allocationEstimator"] != expected_estimator:
        raise ValueError("allocation estimator does not match implementation")
    tracemalloc = allocation["numpyTracemallocPeakBytes"]
    if implementation == "numpy":
        if (
            tracemalloc.get("status") != "measured"
            or type(tracemalloc.get("value")) is not int
            or int(tracemalloc["value"]) > int(allocation["numpyTemporaryBytes"])
        ):
            raise ValueError("NumPy tracemalloc evidence exceeds its deterministic bound")
    elif tracemalloc.get("status") != "not_applicable":
        raise ValueError("JAX allocation must not claim tracemalloc measurement")


def _require_non_negative_integer(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _validate_raw_result(
    raw: JsonObject,
    result: JsonObject,
    *,
    cold_count: int,
    warm_count: int,
) -> None:
    """Raw samples가 summary와 digest의 단일 진실 소스인지 재계산한다."""

    if set(raw) != {"caseId", "implementation", "cold", "warm"}:
        raise ValueError("raw result object has an unexpected field set")
    if (
        raw.get("caseId") != result["caseId"]
        or raw.get("implementation") != result["implementation"]
    ):
        raise ValueError("raw sample identity does not match its manifest result")
    if result["rawSamplesSha256"] != _sha256_bytes(strict_json_bytes(raw)):
        raise ValueError("result raw sample digest does not match canonical raw bytes")
    cold = raw.get("cold")
    warm = raw.get("warm")
    if not isinstance(cold, list) or len(cold) != cold_count:
        raise ValueError("raw cold sample count does not match the frozen protocol")
    if not isinstance(warm, list) or len(warm) != warm_count:
        raise ValueError("raw warm sample count does not match the frozen protocol")
    warm_samples = [_require_non_negative_integer(value, label="raw warm sample") for value in warm]
    expected_cold_phases = (
        ("firstCall", "coldTotal")
        if result["implementation"] == "numpy"
        else (
            "traceLower",
            "compile",
            "hostToDevice",
            "firstExecute",
            "deviceToHost",
            "coldTotal",
        )
    )
    cold_samples: dict[str, list[int]] = {phase: [] for phase in expected_cold_phases}
    for sample in cold:
        if not isinstance(sample, dict) or set(sample) != set(expected_cold_phases):
            raise ValueError("raw cold phase set does not match the implementation")
        validated_sample = {
            phase: _require_non_negative_integer(
                sample[phase],
                label=f"raw {phase} sample",
            )
            for phase in expected_cold_phases
        }
        if result["implementation"] == "numpy":
            if validated_sample["firstCall"] != validated_sample["coldTotal"]:
                raise ValueError("NumPy cold total must equal its first-call boundary")
        elif validated_sample["coldTotal"] < sum(
            validated_sample[phase]
            for phase in (
                "traceLower",
                "compile",
                "hostToDevice",
                "firstExecute",
                "deviceToHost",
            )
        ):
            raise ValueError("JAX cold total is smaller than its measured phases")
        for phase in expected_cold_phases:
            cold_samples[phase].append(validated_sample[phase])
    expected_summaries = {
        phase: _latency_summary(samples) for phase, samples in cold_samples.items()
    }
    expected_summaries["warm"] = _latency_summary(warm_samples)
    for phase, expected in expected_summaries.items():
        if result["latencies"][phase] != expected:
            raise ValueError(f"{phase} summary does not match raw samples")


def _validate_dsr_provenance(record: JsonObject) -> None:
    expected = dsr_trial_provenance_record()
    if record != expected:
        raise ValueError("DSR provenance does not match the frozen trial registry")
    if record["trialRegistrySha256"] != _sha256_bytes(dsr_trial_registry_bytes()):
        raise ValueError("DSR registry digest does not match canonical registry bytes")


def _validate_compile_and_latency(
    result: JsonObject,
    plan_case: JsonObject,
) -> None:
    """Implementation별 compile signature와 적용 가능한 latency phase를 고정한다."""

    implementation = str(result["implementation"])
    latencies = result["latencies"]
    if implementation == "numpy":
        expected_statuses = {
            "firstCall": "measured",
            "traceLower": "not_applicable",
            "compile": "not_applicable",
            "hostToDevice": "not_applicable",
            "firstExecute": "not_applicable",
            "deviceToHost": "not_applicable",
            "coldTotal": "measured",
            "warm": "measured",
        }
        if result["compileSignature"].get("status") != "not_applicable":
            raise ValueError("NumPy compile signature must be not applicable")
    else:
        expected_statuses = {
            "firstCall": "not_applicable",
            "traceLower": "measured",
            "compile": "measured",
            "hostToDevice": "measured",
            "firstExecute": "measured",
            "deviceToHost": "measured",
            "coldTotal": "measured",
            "warm": "measured",
        }
        chunk_paths = (
            None
            if plan_case["axis"] == "one_dimensional"
            else int(result["allocation"]["chunkSize"])
        )
        expected_signature = {
            "status": "measured",
            "shape": list(_case_shape(plan_case, chunk_paths)),
            "dtype": "float64",
            "staticArgs": _static_args(str(plan_case["kernel"])),
        }
        if result["compileSignature"] != expected_signature:
            raise ValueError("JAX compile signature drifted from the exact case shape")
    actual_statuses = {
        phase: str(summary.get("status"))
        for phase, summary in latencies.items()
    }
    if actual_statuses != expected_statuses:
        raise ValueError("latency applicability does not match implementation")


def _validate_execution(execution: JsonObject) -> None:
    """환경 fingerprint와 내부 topology/boundary 일관성을 다시 계산한다."""

    physical = int(execution["physicalCores"])
    logical = int(execution["logicalCores"])
    affinity = list(execution["cpuAffinity"])
    if physical < 1 or logical < 1 or physical > logical:
        raise ValueError("execution core topology is impossible")
    if not affinity or len(set(affinity)) != len(affinity):
        raise ValueError("CPU affinity must be non-empty and unique")
    if any(type(cpu) is not int or cpu < 0 or cpu >= logical for cpu in affinity):
        raise ValueError("CPU affinity falls outside the logical core topology")
    if execution["threadEnvironment"] != THREAD_ENVIRONMENT:
        raise ValueError("thread environment drifted from the frozen protocol")
    if execution["os"] != "Linux":
        raise ValueError("S1.4R benchmark evidence must come from Linux")
    if (
        execution["backend"] != "cpu"
        or execution["x64Enabled"] is not True
        or not execution["devices"]
        or any(device["platform"] != "cpu" for device in execution["devices"])
    ):
        raise ValueError("execution must bind JAX CPU x64 devices")
    device_ids = [int(device["id"]) for device in execution["devices"]]
    if len(set(device_ids)) != len(device_ids):
        raise ValueError("execution device IDs must be unique")

    boundary = str(execution["executionBoundary"])
    outer = str(execution["outerHostBoundary"])
    if boundary in {"wsl2", "native-linux"} and boundary != outer:
        raise ValueError("host execution boundary differs from its outer host")
    expected_container_status = "measured" if boundary == "oci" else "not_applicable"
    if any(
        execution[field].get("status") != expected_container_status
        for field in ("containerRuntime", "containerImageId")
    ):
        raise ValueError("container identity does not match the execution boundary")
    expected_wsl_status = "measured" if outer == "wsl2" else "not_applicable"
    if execution["wslVersion"].get("status") != expected_wsl_status:
        raise ValueError("WSL version applicability differs from the outer host")

    fingerprint_payload = {
        "kernel": execution["kernel"],
        "architecture": execution["architecture"],
        "cpuModel": execution["cpuModel"],
        "logicalCores": execution["logicalCores"],
        "memoryBytes": execution["memoryBytes"],
        "outerHostBoundary": execution["outerHostBoundary"],
    }
    expected_fingerprint = _sha256_bytes(strict_json_bytes(fingerprint_payload))
    if execution["hostFingerprint"] != expected_fingerprint:
        raise ValueError("host fingerprint does not match its environment fields")


def _validate_artifacts(artifacts: JsonObject) -> None:
    """Tracked artifact size/method/status claims의 내부 일관성을 검증한다."""

    positive_sizes = (
        artifacts["researchWheel"]["bytes"],
        artifacts["installedResearchEnvironment"]["bytes"],
        artifacts["ociImage"]["engineReportedSizeBytes"],
        artifacts["ociArchive"]["uncompressedBytes"],
        artifacts["ociArchive"]["compressedBytes"],
    )
    if any(type(value) is not int or value < 1 for value in positive_sizes):
        raise ValueError("artifact byte counts must be positive integers")
    if (
        artifacts["installedResearchEnvironment"]["measurementMethod"]
        != "apparent_bytes"
    ):
        raise ValueError("research environment measurement method drifted")
    image_methods = {
        (
            "docker_image_inspect_size_single_build_"
            "docker_descriptor_matches_oci_manifest"
        ),
        (
            "docker_image_inspect_size_single_build_"
            "docker_legacy_config_and_rootfs_match_oci"
        ),
    }
    if artifacts["ociImage"]["measurementMethod"] not in image_methods:
        raise ValueError("OCI image measurement is not bound to the single build")
    archive = artifacts["ociArchive"]
    if int(archive["compressedBytes"]) > int(archive["uncompressedBytes"]):
        raise ValueError("compressed OCI archive is larger than its source archive")
    if artifacts["nativeExecutable"] != {
        "status": "not_applicable",
        "reason": "separate native executable is outside S1.4R scope",
    }:
        raise ValueError("native executable scope claim drifted")


def validate_manifest_invariants(
    manifest: JsonObject,
    plan: JsonObject,
    raw_document: JsonObject,
) -> None:
    """Schema 밖의 plan/raw/ledger/chunk/summary/comparison 관계를 검증한다."""

    _validator(REPORT_SCHEMA_PATH).validate(manifest)
    _validate_execution(manifest["execution"])
    _validate_artifacts(manifest["artifacts"])
    matrix = str(manifest["matrix"])
    expected_case_count = 1 if matrix == "smallest" else len(_benchmark_cases())
    if int(manifest["caseCount"]) != expected_case_count:
        raise ValueError("manifest caseCount does not match its matrix")
    if len(manifest["results"]) != expected_case_count * 2:
        raise ValueError("manifest must contain one NumPy/JAX result pair per case")
    if len(manifest["comparisons"]) != expected_case_count * 2:
        raise ValueError("manifest must contain cold and warm comparisons per case")
    protocol = manifest["protocol"]
    if (
        protocol["coldFreshProcesses"] != COLD_FRESH_PROCESSES
        or protocol["untimedWarmups"] != UNTIMED_WARMUPS
        or protocol["timedWarmSamples"] != TIMED_WARM_SAMPLES
        or protocol["quantileMethod"] != "linear"
        or protocol["timer"] != "perf_counter_ns"
        or protocol["allocationCapBytes"] != ALLOCATION_CAP_BYTES
    ):
        raise ValueError("manifest protocol drifted from the frozen benchmark plan")
    _validate_dsr_provenance(manifest["dsrTrialProvenance"])

    _validator(PLAN_SCHEMA_PATH).validate(plan)
    if plan != build_benchmark_plan():
        raise ValueError("benchmark plan differs from the exact canonical plan")
    if manifest["planSha256"] != _sha256_bytes(strict_json_bytes(plan)):
        raise ValueError("manifest plan digest does not match canonical plan bytes")
    if manifest["dsrTrialProvenance"] != plan["dsrTrialProvenance"]:
        raise ValueError("manifest and plan DSR provenance differ")
    selected_cases = _selected_cases(plan, matrix)
    if len(selected_cases) != expected_case_count:
        raise ValueError("selected plan case count does not match the matrix")
    plan_cases = {str(case["caseId"]): case for case in selected_cases}

    raw_by_key: dict[tuple[str, str], JsonObject] = {}
    if set(raw_document) != {"schemaVersion", "runId", "results"}:
        raise ValueError("raw sample document has an unexpected field set")
    if raw_document.get("schemaVersion") != "s1.4r-benchmark-raw-samples-v1":
        raise ValueError("raw sample schemaVersion is invalid")
    if raw_document.get("runId") != manifest["runId"]:
        raise ValueError("raw sample runId differs from the manifest")
    if manifest["rawSamplesSha256"] != _sha256_bytes(strict_json_bytes(raw_document)):
        raise ValueError("manifest raw document digest does not match canonical bytes")
    raw_results = raw_document.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != len(manifest["results"]):
        raise ValueError("raw result count differs from the manifest")
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ValueError("raw result must be an object")
        key = (str(raw.get("caseId")), str(raw.get("implementation")))
        if key in raw_by_key:
            raise ValueError("raw sample identities must be unique")
        raw_by_key[key] = raw

    layouts: dict[str, tuple[object, ...]] = {}
    paired_results: dict[str, dict[str, JsonObject]] = {}
    result_ids: set[str] = set()
    for result in manifest["results"]:
        result_id = str(result["resultId"])
        if result_id in result_ids:
            raise ValueError("resultId values must be unique")
        result_ids.add(result_id)
        case_id = str(result["caseId"])
        if case_id not in plan_cases:
            raise ValueError("manifest contains a case outside its selected plan matrix")
        plan_case = plan_cases[case_id]
        for field in (
            "kernel",
            "axis",
            "size",
            "paths",
            "horizon",
            "timedInputKind",
        ):
            if result[field] != plan_case[field]:
                raise ValueError(f"manifest result drifted from plan field {field}")
        implementation = str(result["implementation"])
        if result_id != f"{case_id}--{implementation}":
            raise ValueError("resultId is not derived from caseId and implementation")
        expected_boundary = (
            "validated_public_reference"
            if implementation == "numpy"
            else "compiled_device_numeric_core"
        )
        if result["timingBoundary"] != expected_boundary:
            raise ValueError("result timing boundary does not match its implementation")
        pair = paired_results.setdefault(case_id, {})
        if implementation in pair:
            raise ValueError("each case must have one result per implementation")
        pair[implementation] = result
        _validate_allocation(result, plan_case)
        _validate_compile_and_latency(result, plan_case)
        _validate_throughput(result)
        expected_throughput = _result_throughput(
            plan_case,
            float(result["latencies"]["warm"]["p50Nanoseconds"]),
        )
        if result["throughput"] != expected_throughput:
            raise ValueError("throughput does not match the bound warm p50 latency")
        for summary in result["latencies"].values():
            if _is_measured(summary) and summary["p50Nanoseconds"] > summary["p95Nanoseconds"]:
                raise ValueError("p50 exceeds p95")
        allocation = result["allocation"]
        layout = (
            allocation["chunkSize"],
            allocation["chunkCount"],
            allocation["lastChunkValidPaths"],
            allocation["paddingPaths"],
            allocation["paddingStrategy"],
        )
        prior = layouts.setdefault(case_id, layout)
        if prior != layout:
            raise ValueError("NumPy and JAX chunk/padding layouts differ")
        raw_key = (case_id, implementation)
        if raw_key not in raw_by_key:
            raise ValueError("manifest result has no matching raw sample record")
        _validate_raw_result(
            raw_by_key[raw_key],
            result,
            cold_count=COLD_FRESH_PROCESSES,
            warm_count=TIMED_WARM_SAMPLES,
        )
    if set(paired_results) != set(plan_cases) or any(
        set(pair) != {"numpy", "jax_jit"} for pair in paired_results.values()
    ):
        raise ValueError("manifest result pairs do not exactly cover the selected plan")
    if set(raw_by_key) != {
        (case_id, implementation)
        for case_id in plan_cases
        for implementation in ("numpy", "jax_jit")
    }:
        raise ValueError("raw sample records do not exactly cover the selected plan")
    for case_id, pair in paired_results.items():
        numpy_result = pair["numpy"]
        jax_result = pair["jax_jit"]
        if numpy_result["fixtureSha256"] != jax_result["fixtureSha256"]:
            raise ValueError(f"paired fixture digest differs for {case_id}")
        parity_fields = (
            "maxAbsoluteError",
            "maxRelativeError",
            "maxToleranceRatio",
        )
        if any(
            numpy_result[field] != jax_result[field]
            for field in parity_fields
        ):
            raise ValueError(f"paired parity evidence differs for {case_id}")

    comparison_keys: set[tuple[str, str]] = set()
    for comparison in manifest["comparisons"]:
        numpy_id = str(comparison["numpyResultId"])
        jax_id = str(comparison["jaxResultId"])
        phase = str(comparison["phase"])
        key = (numpy_id.removesuffix("--numpy"), phase)
        if key in comparison_keys:
            raise ValueError("comparison identities must be unique")
        comparison_keys.add(key)
        case_id = key[0]
        if case_id not in paired_results:
            raise ValueError("comparison refers to an unknown case")
        numpy_result = paired_results[case_id]["numpy"]
        jax_result = paired_results[case_id]["jax_jit"]
        if numpy_id != numpy_result["resultId"] or jax_id != jax_result["resultId"]:
            raise ValueError("comparison result IDs do not match their case pair")
        expected_eligibility = {
            "sameHost": True,
            "sameRun": True,
            "sameFixture": True,
            "sameAffinity": True,
            "sameThreads": True,
            "sameExecutionBoundary": True,
            "sameTimedBoundary": (numpy_result["timingBoundary"] == jax_result["timingBoundary"]),
        }
        if comparison["eligibility"] != expected_eligibility:
            raise ValueError("comparison eligibility does not match result contexts")
        eligible = all(expected_eligibility.values())
        speedup = comparison["speedup"]
        if eligible:
            latency_phase = "coldTotal" if phase == "cold_total" else "warm"
            expected_ratio = float(
                numpy_result["latencies"][latency_phase]["p50Nanoseconds"]
            ) / float(jax_result["latencies"][latency_phase]["p50Nanoseconds"])
            if speedup != {"status": "measured", "ratio": expected_ratio}:
                raise ValueError("speedup ratio does not match paired p50 latencies")
        elif speedup["status"] != "not_applicable":
            raise ValueError("ineligible comparison must not report a speedup ratio")
    expected_comparisons = {
        (case_id, phase) for case_id in plan_cases for phase in ("cold_total", "warm")
    }
    if comparison_keys != expected_comparisons:
        raise ValueError("comparisons do not exactly cover cold and warm case pairs")


def _case_shape(case: JsonObject, paths_override: int | None = None) -> tuple[int, ...]:
    kernel = str(case["kernel"])
    if case["axis"] == "one_dimensional":
        size = int(case["size"])
        return (2, size) if kernel in BACKTEST_KERNELS else (size,)
    paths = int(case["paths"] if paths_override is None else paths_override)
    if kernel == "probabilistic_sharpe_ratio":
        return (paths, 5)
    if kernel == "deflated_sharpe_ratio":
        return (paths, 6)
    if kernel in BACKTEST_KERNELS:
        return (paths, 2, HORIZON)
    return (paths, HORIZON)


def _output_scalars(kernel: str) -> int:
    if kernel == "kupiec_unconditional_coverage_test":
        return 7
    if kernel == "christoffersen_independence_test":
        return 12
    if kernel == "christoffersen_conditional_coverage_test":
        return 16
    return 1


def _parity_output_scalars(kernel: str) -> int:
    """Timed raw JAX tree와 canonical NumPy projection의 scalar field 수다."""

    if kernel == "kupiec_unconditional_coverage_test":
        return 5
    if kernel == "christoffersen_independence_test":
        return 9
    if kernel == "christoffersen_conditional_coverage_test":
        return 12
    return 1


def _analytical_ledger(
    case: JsonObject,
    implementation: str,
    *,
    chunk_paths: int | None,
) -> JsonObject:
    shape = _case_shape(case, chunk_paths)
    input_bytes = math.prod(shape) * 8
    result_count = 1 if case["axis"] == "one_dimensional" else int(case["paths"])
    output_bytes = result_count * _output_scalars(str(case["kernel"])) * 8
    kernel = str(case["kernel"])
    if implementation == "numpy":
        numpy_temporary = (
            NUMPY_TRACED_FIXED_OVERHEAD_BYTES + input_bytes * NUMPY_LIVE_BUFFER_UPPER_BOUNDS[kernel]
        )
        jax_argument = jax_temporary = jax_output = jax_alias = 0
    else:
        numpy_temporary = 0
        jax_argument = input_bytes
        jax_temporary = input_bytes * JAX_ANALYTICAL_BUFFER_UPPER_BOUNDS[kernel]
        jax_output = (
            (1 if case["axis"] == "one_dimensional" else int(chunk_paths or 1))
            * _parity_output_scalars(str(case["kernel"]))
            * 16
        )
        jax_alias = 0
    estimated = estimate_peak_allocation_bytes(
        host_input_bytes=input_bytes,
        host_output_bytes=output_bytes,
        numpy_temporary_bytes=numpy_temporary,
        jax_argument_bytes=jax_argument,
        jax_temporary_bytes=jax_temporary,
        jax_output_bytes=jax_output,
        jax_alias_bytes=jax_alias,
    )
    return {
        "hostInputBytes": input_bytes,
        "hostOutputBytes": output_bytes,
        "numpyTemporaryBytes": numpy_temporary,
        "jaxArgumentBytes": jax_argument,
        "jaxTemporaryBytes": jax_temporary,
        "jaxOutputBytes": jax_output,
        "jaxAliasBytes": jax_alias,
        "estimatedPeakAllocationBytes": estimated,
    }


def _preflight_case(case: JsonObject) -> JsonObject:
    if case["axis"] == "one_dimensional":
        ledgers = {
            implementation: _analytical_ledger(
                case,
                implementation,
                chunk_paths=None,
            )
            for implementation in ("numpy", "jax_jit")
        }
        if any(
            ledger["estimatedPeakAllocationBytes"] > ALLOCATION_CAP_BYTES
            for ledger in ledgers.values()
        ):
            raise MemoryError(f"one-dimensional case exceeds cap: {case['caseId']}")
        layout = {
            "chunkSize": 1,
            "chunkCount": 1,
            "lastChunkValidPaths": 1,
            "paddingPaths": 0,
            "paddingStrategy": "repeat_last_valid",
        }
        return {"layout": layout, "ledgers": ledgers}

    paths = int(case["paths"])

    def worst_estimate(chunk_size: int) -> int:
        return max(
            int(
                _analytical_ledger(
                    case,
                    implementation,
                    chunk_paths=chunk_size,
                )["estimatedPeakAllocationBytes"]
            )
            for implementation in ("numpy", "jax_jit")
        )

    chunk_size = select_deterministic_chunk_size(
        paths=paths,
        allocation_cap_bytes=ALLOCATION_CAP_BYTES,
        estimate_peak_bytes=worst_estimate,
        estimator_is_monotone=True,
    )
    return {
        "layout": build_chunk_layout(paths=paths, chunk_size=chunk_size),
        "ledgers": {
            implementation: _analytical_ledger(
                case,
                implementation,
                chunk_paths=chunk_size,
            )
            for implementation in ("numpy", "jax_jit")
        },
    }


def _case_seed(case_id: str) -> int:
    case_digest = hashlib.sha256(case_id.encode()).digest()
    return RNG_SEED ^ int.from_bytes(case_digest[:8], byteorder="little")


def _backtest_fixture(
    paths: int | None,
    observations: int,
    *,
    path_offset: int = 0,
) -> np.ndarray:
    pattern = np.asarray([0, 0, 1, 0, 1, 1], dtype=np.float64)
    if paths is None:
        exceptions = np.resize(pattern, observations)
        forecasts = np.ones(observations, dtype=np.float64)
        realized = np.where(exceptions == 1.0, 2.0, 0.0)
        return np.stack((realized, forecasts), axis=0)
    output = np.empty((paths, 2, observations), dtype=np.float64)
    for index in range(paths):
        path_index = path_offset + index
        exceptions = np.resize(
            np.roll(pattern, path_index % pattern.size),
            observations,
        )
        output[index, 0] = np.where(exceptions == 1.0, 2.0, 0.0)
        output[index, 1] = 1.0
    return output


def _scalar_parameter_fixture(
    kernel: str,
    paths: int,
    generator: np.random.Generator,
) -> np.ndarray:
    returns = generator.normal(0.001, 0.02, size=(paths, HORIZON))
    centered = returns - np.mean(returns, axis=1, keepdims=True)
    m2 = np.mean(centered**2, axis=1)
    observed = np.mean(returns, axis=1) / np.sqrt(m2)
    skewness = np.mean(centered**3, axis=1) / np.power(m2, 1.5)
    kurtosis = np.mean(centered**4, axis=1) / np.square(m2)
    if kernel == "probabilistic_sharpe_ratio":
        return np.column_stack(
            (
                observed,
                np.zeros(paths),
                np.full(paths, HORIZON),
                skewness,
                kurtosis,
            )
        )
    provenance = dsr_trial_provenance_record()
    return np.column_stack(
        (
            observed,
            np.full(paths, HORIZON),
            skewness,
            kurtosis,
            np.full(paths, 2),
            np.full(paths, provenance["sharpeEstimateVariance"]),
        )
    )


def _generate_fixture(case: JsonObject, fixture_dir: Path) -> JsonObject:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    generator = np.random.Generator(np.random.PCG64(_case_seed(str(case["caseId"]))))
    kernel = str(case["kernel"])
    path = fixture_dir / f"{case['caseId']}.float64le.bin"
    if case["axis"] == "path_batch":
        shape = _case_shape(case)
        output = np.memmap(path, dtype="<f8", mode="w+", shape=shape)
        paths = int(case["paths"])
        generation_chunk = min(256, paths)
        for start in range(0, paths, generation_chunk):
            count = min(generation_chunk, paths - start)
            if kernel in BACKTEST_KERNELS:
                chunk = _backtest_fixture(
                    count,
                    HORIZON,
                    path_offset=start,
                )
            elif kernel in SCALAR_EVALUATION_KERNELS:
                chunk = _scalar_parameter_fixture(kernel, count, generator)
            else:
                chunk = generator.normal(
                    0.001,
                    0.02,
                    size=(count, HORIZON),
                )
                if kernel == "historical_expected_shortfall":
                    chunk = np.abs(chunk)
            little_endian_chunk = np.asarray(chunk, dtype="<f8", order="C")
            if not np.isfinite(little_endian_chunk).all():
                raise ValueError(f"generated fixture is non-finite: {case['caseId']}")
            output[start : start + count] = little_endian_chunk
        output.flush()
        del output
    else:
        if kernel in BACKTEST_KERNELS:
            values = _backtest_fixture(None, int(case["size"]))
        else:
            shape = _case_shape(case)
            values = generator.normal(0.001, 0.02, size=shape)
            if kernel == "historical_expected_shortfall":
                values = np.abs(values)
        little_endian = np.asarray(values, dtype="<f8", order="C")
        if not np.isfinite(little_endian).all():
            raise ValueError(f"generated fixture is non-finite: {case['caseId']}")
        little_endian.tofile(path)
    return {
        "path": path,
        "shape": list(_case_shape(case)),
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _allowed_affinity() -> list[int]:
    if hasattr(os, "sched_getaffinity"):
        affinity = sorted(os.sched_getaffinity(0))
        if affinity:
            return [affinity[0]]
    return [0]


def _worker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(THREAD_ENVIRONMENT)
    environment.update(
        {
            "JAX_PLATFORMS": "cpu",
            "JAX_ENABLE_X64": "1",
            "JAX_ENABLE_COMPILATION_CACHE": "0",
            "PYTHONHASHSEED": "0",
            "TMPDIR": "/tmp",
            "TEMP": "/tmp",
            "TMP": "/tmp",
        }
    )
    return environment


def _worker_job(
    case: JsonObject,
    fixture: JsonObject,
    layout: JsonObject,
    *,
    implementation: str,
    mode: str,
) -> JsonObject:
    return {
        "case": case,
        "fixturePath": str(fixture["path"]),
        "fixtureShape": fixture["shape"],
        "chunkSize": layout["chunkSize"],
        "implementation": implementation,
        "timingBoundary": (
            "validated_public_reference"
            if implementation == "numpy"
            else "compiled_device_numeric_core"
        ),
        "mode": mode,
        "warmups": UNTIMED_WARMUPS,
        "samples": TIMED_WARM_SAMPLES,
        "cpuAffinity": _allowed_affinity(),
    }


def _run_worker(job: JsonObject) -> JsonObject:
    completed = subprocess.run(
        [sys.executable, "-m", "benchmarks.worker"],
        cwd=PROJECT_ROOT,
        env=_worker_environment(),
        input=strict_json_bytes(job),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "benchmark worker failed\n" + completed.stderr.decode("utf-8", errors="replace")
        )
    return cast(
        JsonObject,
        json.loads(
            completed.stdout,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite worker JSON token: {token}")
            ),
        ),
    )


def _ledger_total(ledger: JsonObject) -> int:
    return estimate_peak_allocation_bytes(
        host_input_bytes=int(ledger["hostInputBytes"]),
        host_output_bytes=int(ledger["hostOutputBytes"]),
        numpy_temporary_bytes=int(ledger["numpyTemporaryBytes"]),
        jax_argument_bytes=int(ledger["jaxArgumentBytes"]),
        jax_temporary_bytes=int(ledger["jaxTemporaryBytes"]),
        jax_output_bytes=int(ledger["jaxOutputBytes"]),
        jax_alias_bytes=int(ledger["jaxAliasBytes"]),
    )


def _compiled_jax_preflight(
    case: JsonObject,
    chunk_size: int,
) -> tuple[JsonObject, JsonObject]:
    compile_shape = (
        _case_shape(case) if case["axis"] == "one_dimensional" else _case_shape(case, chunk_size)
    )
    worker = _run_worker(
        {
            "case": case,
            "compileShape": list(compile_shape),
            "implementation": "jax_jit",
            "mode": "memory_preflight",
            "cpuAffinity": _allowed_affinity(),
        }
    )
    compiled = worker["memoryAnalysis"]
    ledger = _analytical_ledger(
        case,
        "jax_jit",
        chunk_paths=None if case["axis"] == "one_dimensional" else chunk_size,
    )
    ledger["jaxArgumentBytes"] = max(
        int(ledger["jaxArgumentBytes"]),
        int(compiled["argumentBytes"]),
    )
    ledger["jaxTemporaryBytes"] = max(
        int(ledger["jaxTemporaryBytes"]),
        int(compiled["temporaryBytes"]),
    )
    ledger["jaxOutputBytes"] = max(
        int(ledger["jaxOutputBytes"]),
        int(compiled["outputBytes"]),
    )
    ledger["jaxAliasBytes"] = min(
        int(ledger["jaxArgumentBytes"]) + int(ledger["jaxOutputBytes"]),
        int(compiled["aliasBytes"]),
    )
    ledger["estimatedPeakAllocationBytes"] = _ledger_total(ledger)
    return ledger, worker


def _measured_numpy_preflight(
    case: JsonObject,
    fixture: JsonObject,
    chunk_size: int,
) -> tuple[JsonObject, JsonObject]:
    layout = (
        build_chunk_layout(paths=1, chunk_size=1)
        if case["axis"] == "one_dimensional"
        else build_chunk_layout(paths=int(case["paths"]), chunk_size=chunk_size)
    )
    worker = _run_worker(
        _worker_job(
            case,
            fixture,
            layout,
            implementation="numpy",
            mode="numpy_memory_preflight",
        )
    )
    ledger = _analytical_ledger(
        case,
        "numpy",
        chunk_paths=None if case["axis"] == "one_dimensional" else chunk_size,
    )
    ledger["numpyTemporaryBytes"] = max(
        int(ledger["numpyTemporaryBytes"]),
        int(worker["numpyTracemallocPeakBytes"]),
    )
    ledger["estimatedPeakAllocationBytes"] = _ledger_total(ledger)
    return ledger, worker


def _refine_preflight_with_measured_memory(
    case: JsonObject,
    fixture: JsonObject,
    preflight: JsonObject,
) -> None:
    """Analytical max chunk가 compiled/traced upper bound 안인지 timing 전에 확인한다."""

    chunk_size = int(preflight["layout"]["chunkSize"])
    analytical_jax = preflight["ledgers"]["jax_jit"]
    analytical_numpy = preflight["ledgers"]["numpy"]
    measured_jax, jax_worker = _compiled_jax_preflight(case, chunk_size)
    measured_numpy, numpy_worker = _measured_numpy_preflight(
        case,
        fixture,
        chunk_size,
    )
    compiled = jax_worker["memoryAnalysis"]
    if (
        int(compiled["argumentBytes"]) > int(analytical_jax["jaxArgumentBytes"])
        or int(compiled["temporaryBytes"]) > int(analytical_jax["jaxTemporaryBytes"])
        or int(compiled["outputBytes"]) > int(analytical_jax["jaxOutputBytes"])
    ):
        raise MemoryError(
            f"JAX compiled analysis exceeded the proven monotone upper bound: {case['caseId']}"
        )
    if int(numpy_worker["numpyTracemallocPeakBytes"]) > int(
        analytical_numpy["numpyTemporaryBytes"]
    ):
        raise MemoryError(
            f"NumPy tracing exceeded the proven monotone upper bound: {case['caseId']}"
        )
    if (
        int(measured_jax["estimatedPeakAllocationBytes"]) > ALLOCATION_CAP_BYTES
        or int(measured_numpy["estimatedPeakAllocationBytes"]) > ALLOCATION_CAP_BYTES
    ):
        raise MemoryError(f"measured preflight exceeds cap: {case['caseId']}")
    # Manifest ledger는 측정값보다 큰 source-level upper bound를 보존한다.
    preflight["memoryPreflight"] = {
        "jax_jit": jax_worker,
        "numpy": numpy_worker,
    }


def _flatten_numeric(value: object) -> Iterable[float]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten_numeric(item)
        return
    if isinstance(value, bool):
        yield float(value)
        return
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("non-finite parity value")
        yield numeric
        return
    raise TypeError(f"non-numeric parity value: {type(value)!r}")


def _fixture_parity(
    case: JsonObject,
    fixture: JsonObject,
    layout: JsonObject,
) -> tuple[float, float, float]:
    values: dict[str, np.ndarray] = {}
    for implementation in ("numpy", "jax_jit"):
        job = _worker_job(
            case,
            fixture,
            layout,
            implementation=implementation,
            mode="parity",
        )
        result = _run_worker(job)
        expected_evaluations = 1 if case["axis"] == "one_dimensional" else int(case["paths"])
        if int(result["evaluations"]) != expected_evaluations:
            raise RuntimeError(f"fixture parity omitted paths: {case['caseId']}")
        implementation_values = np.asarray(
            list(_flatten_numeric(result["values"])),
            dtype=np.float64,
        )
        expected_values = expected_evaluations * _parity_output_scalars(str(case["kernel"]))
        if implementation_values.size != expected_values:
            raise RuntimeError(f"fixture parity output cardinality mismatch: {case['caseId']}")
        values[implementation] = implementation_values
    numpy_values = values["numpy"]
    jax_values = values["jax_jit"]
    if numpy_values.shape != jax_values.shape:
        raise RuntimeError(f"fixture parity shape mismatch: {case['caseId']}")
    absolute = np.abs(numpy_values - jax_values)
    relative = absolute / np.maximum(np.abs(numpy_values), 1e-300)
    tolerance_ratio = absolute / (
        PARITY_ABSOLUTE_TOLERANCE
        + PARITY_RELATIVE_TOLERANCE * np.abs(numpy_values)
    )
    max_absolute = float(np.max(absolute, initial=0.0))
    max_relative = float(np.max(relative, initial=0.0))
    max_tolerance_ratio = float(np.max(tolerance_ratio, initial=0.0))
    if not np.allclose(
        jax_values,
        numpy_values,
        rtol=PARITY_RELATIVE_TOLERANCE,
        atol=PARITY_ABSOLUTE_TOLERANCE,
    ):
        raise RuntimeError(
            f"fixture parity failed for {case['caseId']}: "
            f"abs={max_absolute} rel={max_relative} ratio={max_tolerance_ratio}"
        )
    if max_tolerance_ratio > 1.0:
        raise RuntimeError(
            f"normalized fixture parity failed for {case['caseId']}: "
            f"ratio={max_tolerance_ratio}"
        )
    return max_absolute, max_relative, max_tolerance_ratio


def _not_applicable(reason: str) -> JsonObject:
    return {"status": "not_applicable", "reason": reason}


def _latency_summary(samples: Sequence[int | float]) -> JsonObject:
    summary = linear_quantiles_ns(samples)
    return {
        "status": "measured",
        "p50Nanoseconds": summary["p50Nanoseconds"],
        "p95Nanoseconds": summary["p95Nanoseconds"],
    }


def _throughput(value: float) -> JsonObject:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("throughput must be positive and finite")
    return {"status": "measured", "valuePerSecond": value}


def _result_throughput(case: JsonObject, warm_p50_ns: float) -> JsonObject:
    calls_per_second = 1_000_000_000.0 / warm_p50_ns
    result = {
        "callsPerSecond": _not_applicable("not applicable"),
        "observationsPerSecond": _not_applicable("not applicable"),
        "pathsPerSecond": _not_applicable("not applicable"),
        "pathObservationsPerSecond": _not_applicable("not applicable"),
        "evaluationsPerSecond": _not_applicable("not applicable"),
    }
    result["callsPerSecond"] = _throughput(calls_per_second)
    if case["axis"] == "one_dimensional":
        result["observationsPerSecond"] = _throughput(calls_per_second * int(case["size"]))
    elif case["timedInputKind"] == "scalar_parameters":
        result["evaluationsPerSecond"] = _throughput(calls_per_second * int(case["paths"]))
    else:
        paths = int(case["paths"])
        result["pathsPerSecond"] = _throughput(calls_per_second * paths)
        result["pathObservationsPerSecond"] = _throughput(
            calls_per_second * paths * int(case["horizon"])
        )
    return result


def _max_memory_analysis(workers: Sequence[JsonObject]) -> JsonObject:
    fields = ("argumentBytes", "temporaryBytes", "outputBytes")
    result = {
        field: max(int(worker["memoryAnalysis"][field]) for worker in workers) for field in fields
    }
    # Alias는 많을수록 peak를 줄이므로 worst-case aggregate는 최소값이다.
    result["aliasBytes"] = min(int(worker["memoryAnalysis"]["aliasBytes"]) for worker in workers)
    return result


def _allocation_manifest(
    implementation: str,
    ledger: JsonObject,
    layout: JsonObject,
    workers: Sequence[JsonObject],
    warm: JsonObject,
    preflight_worker: JsonObject,
) -> JsonObject:
    all_workers = [preflight_worker, *workers, warm]
    rss_worker = max(all_workers, key=lambda item: int(item["rssDeltaBytes"]))
    values = dict(ledger)
    if implementation == "jax_jit":
        compiled = _max_memory_analysis([preflight_worker, *workers, warm])
        if (
            int(compiled["argumentBytes"]) > int(values["jaxArgumentBytes"])
            or int(compiled["temporaryBytes"]) > int(values["jaxTemporaryBytes"])
            or int(compiled["outputBytes"]) > int(values["jaxOutputBytes"])
        ):
            raise MemoryError("timed JAX compilation exceeded its preflight upper bound")
    else:
        traced_peak = max(
            int(preflight_worker["numpyTracemallocPeakBytes"]),
            int(warm["numpyTracemallocPeakBytes"]),
        )
        if traced_peak > int(values["numpyTemporaryBytes"]):
            raise MemoryError("timed NumPy tracing exceeded its preflight upper bound")
    estimated = estimate_peak_allocation_bytes(
        host_input_bytes=int(values["hostInputBytes"]),
        host_output_bytes=int(values["hostOutputBytes"]),
        numpy_temporary_bytes=int(values["numpyTemporaryBytes"]),
        jax_argument_bytes=int(values["jaxArgumentBytes"]),
        jax_temporary_bytes=int(values["jaxTemporaryBytes"]),
        jax_output_bytes=int(values["jaxOutputBytes"]),
        jax_alias_bytes=int(values["jaxAliasBytes"]),
    )
    if estimated > ALLOCATION_CAP_BYTES:
        raise MemoryError(
            f"compiled allocation evidence exceeds cap for {implementation}: {estimated}"
        )
    return {
        "allocationCapBytes": ALLOCATION_CAP_BYTES,
        **{
            field: int(values[field])
            for field in (
                "hostInputBytes",
                "hostOutputBytes",
                "numpyTemporaryBytes",
                "jaxArgumentBytes",
                "jaxTemporaryBytes",
                "jaxOutputBytes",
                "jaxAliasBytes",
            )
        },
        "estimatedPeakAllocationBytes": estimated,
        "numpyTracemallocPeakBytes": (
            {
                "status": "measured",
                "value": max(
                    int(preflight_worker["numpyTracemallocPeakBytes"]),
                    int(warm["numpyTracemallocPeakBytes"]),
                ),
            }
            if implementation == "numpy"
            else _not_applicable("tracemalloc does not measure JAX/XLA native allocation")
        ),
        "rssBaselineBytes": int(rss_worker["rssBaselineBytes"]),
        "rssPeakBytes": int(rss_worker["rssPeakBytes"]),
        "rssDeltaBytes": int(rss_worker["rssDeltaBytes"]),
        **layout,
        "allocationEstimator": (
            "numpy_source_bound_plus_tracemalloc_preflight_v2"
            if implementation == "numpy"
            else "jax_compiled_memory_analysis_plus_host_v1"
        ),
        "allocationEstimatorMonotone": True,
        "ledgerEquation": LEDGER_EQUATION,
        "allocationCapPassed": True,
    }


def _benchmark_one_implementation(
    case: JsonObject,
    fixture: JsonObject,
    preflight: JsonObject,
    *,
    implementation: str,
    max_absolute_error: float,
    max_relative_error: float,
    max_tolerance_ratio: float,
) -> tuple[JsonObject, JsonObject]:
    layout = preflight["layout"]
    cold_workers = [
        _run_worker(
            _worker_job(
                case,
                fixture,
                layout,
                implementation=implementation,
                mode="cold",
            )
        )
        for _ in range(COLD_FRESH_PROCESSES)
    ]
    warm = _run_worker(
        _worker_job(
            case,
            fixture,
            layout,
            implementation=implementation,
            mode="warm",
        )
    )
    warm_samples = [int(sample) for sample in warm["warmSamples"]]
    raw: JsonObject = {
        "caseId": case["caseId"],
        "implementation": implementation,
        "cold": [worker["samples"] for worker in cold_workers],
        "warm": warm_samples,
    }
    raw_sha = _sha256_bytes(strict_json_bytes(raw))
    if implementation == "numpy":
        latencies = {
            "firstCall": _latency_summary(
                [worker["samples"]["firstCall"] for worker in cold_workers]
            ),
            "traceLower": _not_applicable("NumPy does not trace or lower"),
            "compile": _not_applicable("NumPy does not compile"),
            "hostToDevice": _not_applicable("NumPy has no device transfer"),
            "firstExecute": _not_applicable("NumPy first call is reported separately"),
            "deviceToHost": _not_applicable("NumPy has no device transfer"),
            "coldTotal": _latency_summary(
                [worker["samples"]["coldTotal"] for worker in cold_workers]
            ),
            "warm": _latency_summary(warm_samples),
        }
        compile_signature = _not_applicable("NumPy has no compiled JAX signature")
    else:
        latencies = {
            "firstCall": _not_applicable("JAX cold work is split into explicit phases"),
            "traceLower": _latency_summary(
                [worker["samples"]["traceLower"] for worker in cold_workers]
            ),
            "compile": _latency_summary([worker["samples"]["compile"] for worker in cold_workers]),
            "hostToDevice": _latency_summary(
                [worker["samples"]["hostToDevice"] for worker in cold_workers]
            ),
            "firstExecute": _latency_summary(
                [worker["samples"]["firstExecute"] for worker in cold_workers]
            ),
            "deviceToHost": _latency_summary(
                [worker["samples"]["deviceToHost"] for worker in cold_workers]
            ),
            "coldTotal": _latency_summary(
                [worker["samples"]["coldTotal"] for worker in cold_workers]
            ),
            "warm": _latency_summary(warm_samples),
        }
        compile_shape = (
            _case_shape(case)
            if case["axis"] == "one_dimensional"
            else _case_shape(case, int(layout["chunkSize"]))
        )
        compile_signature = {
            "status": "measured",
            "shape": list(compile_shape),
            "dtype": "float64",
            "staticArgs": _static_args(str(case["kernel"])),
        }
    warm_p50 = float(latencies["warm"]["p50Nanoseconds"])
    result: JsonObject = {
        "resultId": f"{case['caseId']}--{implementation}",
        "caseId": case["caseId"],
        "kernel": case["kernel"],
        "axis": case["axis"],
        "size": case["size"],
        "paths": case["paths"],
        "horizon": case["horizon"],
        "timedInputKind": case["timedInputKind"],
        "implementation": implementation,
        "timingBoundary": (
            "validated_public_reference"
            if implementation == "numpy"
            else "compiled_device_numeric_core"
        ),
        "fixtureSha256": fixture["sha256"],
        "rawSamplesSha256": raw_sha,
        "correctnessPassed": True,
        "compileSignature": compile_signature,
        "latencies": latencies,
        "throughput": _result_throughput(case, warm_p50),
        "allocation": _allocation_manifest(
            implementation,
            preflight["ledgers"][implementation],
            layout,
            cold_workers,
            warm,
            preflight["memoryPreflight"][implementation],
        ),
        "maxAbsoluteError": max_absolute_error,
        "maxRelativeError": max_relative_error,
        "maxToleranceRatio": max_tolerance_ratio,
    }
    return result, raw


def _run_correctness_prerequisite() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=PROJECT_ROOT,
        env=_worker_environment(),
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("correctness prerequisite failed; benchmark was not started")


def _read_first_line(path: Path, prefix: str) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(prefix):
                return line.removeprefix(prefix).strip().removeprefix(":").strip()
    except OSError:
        return None
    return None


def _physical_core_count() -> int:
    """Linux cpuinfo의 physical/core ID pair를 세고 추측값을 만들지 않는다."""

    try:
        blocks = Path("/proc/cpuinfo").read_text(encoding="utf-8").split("\n\n")
    except OSError:
        return os.cpu_count() or 1
    pairs: set[tuple[str, str]] = set()
    for block in blocks:
        fields = {
            key.strip(): value.strip()
            for line in block.splitlines()
            if ":" in line
            for key, value in [line.split(":", maxsplit=1)]
        }
        if "physical id" in fields and "core id" in fields:
            pairs.add((fields["physical id"], fields["core id"]))
    return len(pairs) if pairs else (os.cpu_count() or 1)


def _wsl_version_evidence(outer_boundary: str) -> JsonObject:
    if outer_boundary != "wsl2":
        return _not_applicable("not running under WSL")
    executable = shutil.which("wsl.exe")
    if executable is None:
        candidate = Path("/mnt/c/Windows/System32/wsl.exe")
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        return _not_applicable("wsl.exe is unavailable inside this boundary")
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _not_applicable("wsl.exe --version could not be measured")
    raw = completed.stdout or completed.stderr
    for encoding in ("utf-16-le", "utf-8"):
        try:
            decoded = raw.decode(encoding).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
        lines = [line.strip() for line in decoded.splitlines() if line.strip()]
        if completed.returncode == 0 and lines:
            return {"status": "measured", "value": " | ".join(lines[:4])}
    return _not_applicable("wsl.exe --version returned no parseable value")


def _execution_manifest() -> JsonObject:
    import jax

    release = platform.release()
    boundary = os.environ.get(
        "S1_4R_EXECUTION_BOUNDARY",
        "wsl2" if "microsoft" in release.lower() else "native-linux",
    )
    outer = "wsl2" if "microsoft" in release.lower() else "native-linux"
    affinity = _allowed_affinity()
    cpu_model = _read_first_line(Path("/proc/cpuinfo"), "model name") or "unknown"
    memory_kib = _read_first_line(Path("/proc/meminfo"), "MemTotal:")
    memory_bytes = int(memory_kib.split()[0]) * 1024 if memory_kib is not None else 1
    governor_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    governor = (
        {"status": "measured", "value": governor_path.read_text().strip()}
        if governor_path.is_file()
        else _not_applicable("CPU governor is not exposed by this execution boundary")
    )
    fingerprint_payload = {
        "kernel": release,
        "architecture": platform.machine(),
        "cpuModel": cpu_model,
        "logicalCores": os.cpu_count(),
        "memoryBytes": memory_bytes,
        "outerHostBoundary": outer,
    }
    devices = [
        {
            "id": int(device.id),
            "platform": str(device.platform),
            "deviceKind": str(device.device_kind),
        }
        for device in jax.devices()
    ]
    if (
        jax.default_backend() != "cpu"
        or not devices
        or any(device["platform"] != "cpu" for device in devices)
        or jax.config.jax_enable_x64 is not True
    ):
        raise RuntimeError("benchmark requires JAX CPU backend with x64 enabled")
    container_runtime = _not_applicable("host benchmark")
    container_image = _not_applicable("host benchmark")
    if boundary == "oci":
        runtime = os.environ.get("S1_4R_CONTAINER_RUNTIME")
        image_id = os.environ.get("S1_4R_CONTAINER_IMAGE_ID")
        if not runtime or not image_id:
            raise RuntimeError("OCI benchmark requires container runtime and image ID env")
        container_runtime = {"status": "measured", "value": runtime}
        container_image = {"status": "measured", "value": image_id}
    return {
        "executionBoundary": boundary,
        "outerHostBoundary": outer,
        "hostFingerprint": _sha256_bytes(strict_json_bytes(fingerprint_payload)),
        "os": platform.system(),
        "kernel": release,
        "architecture": platform.machine(),
        "wslVersion": _wsl_version_evidence(outer),
        "cpuModel": cpu_model,
        "physicalCores": _physical_core_count(),
        "logicalCores": os.cpu_count() or 1,
        "cpuAffinity": affinity,
        "cpuGovernor": governor,
        "memoryBytes": memory_bytes,
        "pythonVersion": platform.python_version(),
        "numpyVersion": np.__version__,
        "jaxVersion": jax.__version__,
        "jaxlibVersion": importlib.metadata.version("jaxlib"),
        "backend": "cpu",
        "devices": devices,
        "x64Enabled": True,
        "threadEnvironment": dict(THREAD_ENVIRONMENT),
        "containerRuntime": container_runtime,
        "containerImageId": container_image,
    }


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip()
    if len(commit) != 40:
        raise RuntimeError("git commit is not a full SHA")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout:
        raise RuntimeError(
            "benchmark evidence requires a clean source tree; commit scoped changes first"
        )
    return commit


def _directory_apparent_bytes(path: Path) -> int:
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def _oci_archive_identity(archive: Path) -> JsonObject:
    """OCI archive descriptor/blob hashes와 config rootfs identity를 검증한다."""

    with tarfile.open(archive, mode="r") as tar:
        members = {member.name.lstrip("./"): member for member in tar.getmembers()}

        def read_member(name: str) -> bytes:
            member = members.get(name)
            extracted = tar.extractfile(member) if member is not None else None
            if extracted is None:
                raise RuntimeError(f"OCI archive is missing {name}")
            return extracted.read()

        def descriptor_blob(descriptor: object, *, label: str) -> bytes:
            if not isinstance(descriptor, dict):
                raise RuntimeError(f"OCI {label} descriptor is invalid")
            digest = descriptor.get("digest")
            size = descriptor.get("size")
            if (
                not isinstance(digest, str)
                or not digest.startswith("sha256:")
                or len(digest) != 71
            ):
                raise RuntimeError(f"OCI {label} digest is not SHA-256")
            if type(size) is not int or size < 1:
                raise RuntimeError(f"OCI {label} descriptor size is invalid")
            payload = read_member(
                f"blobs/sha256/{digest.removeprefix('sha256:')}"
            )
            if len(payload) != size:
                raise RuntimeError(
                    f"OCI {label} blob size does not match its descriptor"
                )
            if f"sha256:{_sha256_bytes(payload)}" != digest:
                raise RuntimeError(
                    f"OCI {label} blob does not match its descriptor digest"
                )
            return payload

        index_bytes = read_member("index.json")
        try:
            index = json.loads(index_bytes)
        except json.JSONDecodeError as error:
            raise RuntimeError("OCI index.json is invalid") from error
        manifests = index.get("manifests", [])
        if len(manifests) != 1:
            raise RuntimeError("OCI archive must contain exactly one manifest descriptor")
        manifest_digest = str(manifests[0]["digest"])
        manifest_bytes = descriptor_blob(manifests[0], label="manifest")
        try:
            manifest = json.loads(manifest_bytes)
        except json.JSONDecodeError as error:
            raise RuntimeError("OCI image manifest is invalid") from error
        config_descriptor = manifest.get("config")
        config_bytes = descriptor_blob(config_descriptor, label="config")
        if not isinstance(config_descriptor, dict):
            raise RuntimeError("OCI config descriptor is invalid")
        config_digest = str(config_descriptor["digest"])
        try:
            config = json.loads(config_bytes)
        except json.JSONDecodeError as error:
            raise RuntimeError("OCI image config is invalid") from error
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise RuntimeError("OCI image manifest layers are invalid")
        for index_value, layer in enumerate(layers):
            descriptor_blob(layer, label=f"layer {index_value}")
    diff_ids = config.get("rootfs", {}).get("diff_ids")
    if (
        not isinstance(diff_ids, list)
        or not diff_ids
        or not all(isinstance(digest, str) and digest.startswith("sha256:") for digest in diff_ids)
    ):
        raise RuntimeError("OCI config rootfs.diff_ids is invalid")
    if len(diff_ids) != len(layers):
        raise RuntimeError("OCI layer descriptors and rootfs diff IDs differ in count")
    return {
        "manifestDigest": manifest_digest,
        "configDigest": config_digest,
        "rootfsDiffIds": diff_ids,
    }


def _verify_loaded_image_identity(
    image: JsonObject,
    archive_identity: JsonObject,
) -> str:
    """같은 BuildKit solve의 Docker exporter와 OCI exporter identity를 결속한다."""

    descriptor = image.get("Descriptor")
    if isinstance(descriptor, dict) and descriptor.get("digest") is not None:
        if descriptor["digest"] != archive_identity["manifestDigest"]:
            raise RuntimeError("loaded Docker descriptor differs from OCI manifest")
        return "docker_descriptor_matches_oci_manifest"
    if image.get("Id") != archive_identity["configDigest"]:
        raise RuntimeError("loaded Docker image ID differs from OCI config digest")
    rootfs = image.get("RootFS")
    if not isinstance(rootfs, dict) or rootfs.get("Layers") != archive_identity["rootfsDiffIds"]:
        raise RuntimeError("loaded Docker rootfs differs from OCI config diff IDs")
    return "docker_legacy_config_and_rootfs_match_oci"


def _gzip_deterministic(source: Path, destination: Path) -> None:
    with (
        source.open("rb") as input_file,
        destination.open("wb") as output_file,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=output_file,
            compresslevel=9,
            mtime=0,
        ) as compressed,
    ):
        shutil.copyfileobj(input_file, compressed)


def _collect_artifacts(output_dir: Path, commit: str) -> JsonObject:
    artifact_dir = output_dir / "artifacts"
    wheel_dir = artifact_dir / "wheel"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    docker = shutil.which("docker")
    if uv is None or docker is None:
        raise RuntimeError("uv and Docker are required for reproducible artifacts")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=PROJECT_ROOT,
        check=True,
        env=_worker_environment(),
    )
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("exactly one research wheel was expected")
    wheel = wheels[0]
    image_tag = f"s1-4r-risk-research:{commit[:12]}"
    archive = artifact_dir / "s1-4r.oci.tar"
    compressed_archive = artifact_dir / "s1-4r.oci.tar.gz"
    if archive.exists():
        archive.unlink()
    if compressed_archive.exists():
        compressed_archive.unlink()
    # 두 exporter는 하나의 BuildKit solve 결과를 공유한다. OCI archive와
    # Docker에 load된 runnable image가 다른 rebuild에서 나오지 않게 한다.
    build = [
        docker,
        "buildx",
        "build",
        "--file",
        "Containerfile",
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--sbom=false",
        "--output",
        f"type=oci,dest={archive},name={image_tag},oci-mediatypes=true",
        "--output",
        f"type=docker,name={image_tag},oci-mediatypes=true",
        ".",
    ]
    subprocess.run(
        build,
        cwd=PROJECT_ROOT,
        check=True,
    )
    subprocess.run(
        [
            docker,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            image_tag,
            "pytest",
            "-q",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    inspect = subprocess.run(
        [docker, "image", "inspect", image_tag],
        check=True,
        capture_output=True,
        text=True,
    )
    image = json.loads(inspect.stdout)[0]
    archive_identity = _oci_archive_identity(archive)
    identity_method = _verify_loaded_image_identity(image, archive_identity)
    _gzip_deterministic(archive, compressed_archive)
    venv = PROJECT_ROOT / ".venv"
    if not venv.is_dir():
        raise RuntimeError("research .venv is required for environment size evidence")
    return {
        "researchWheel": {
            "bytes": wheel.stat().st_size,
            "sha256": _sha256_file(wheel),
        },
        "installedResearchEnvironment": {
            "bytes": _directory_apparent_bytes(venv),
            "measurementMethod": "apparent_bytes",
        },
        "ociImage": {
            "imageId": image["Id"],
            "manifestDigest": archive_identity["manifestDigest"],
            "engineReportedSizeBytes": int(image["Size"]),
            "measurementMethod": (f"docker_image_inspect_size_single_build_{identity_method}"),
        },
        "ociArchive": {
            "format": "oci-layout",
            "uncompressedBytes": archive.stat().st_size,
            "compressedBytes": compressed_archive.stat().st_size,
            "compressedSha256": _sha256_file(compressed_archive),
            "compression": "gzip-n",
        },
        "nativeExecutable": {
            "status": "not_applicable",
            "reason": "separate native executable is outside S1.4R scope",
        },
    }


def _selected_cases(plan: JsonObject, matrix: str) -> list[JsonObject]:
    cases = list(plan["cases"])
    if matrix == "smallest":
        return [cases[0]]
    if matrix == "full":
        return cases
    raise ValueError(f"unsupported matrix: {matrix}")


def _comparison(
    numpy_result: JsonObject,
    jax_result: JsonObject,
    execution: JsonObject,
    run_id: str,
    *,
    phase: str,
) -> JsonObject:
    left = {
        "hostFingerprint": execution["hostFingerprint"],
        "runId": run_id,
        "fixtureSha256": numpy_result["fixtureSha256"],
        "cpuAffinity": execution["cpuAffinity"],
        "threadEnvironment": execution["threadEnvironment"],
        "executionBoundary": execution["executionBoundary"],
        "timingBoundary": numpy_result["timingBoundary"],
    }
    right = dict(left)
    right["fixtureSha256"] = jax_result["fixtureSha256"]
    right["timingBoundary"] = jax_result["timingBoundary"]
    eligibility = speedup_eligibility(left, right)
    eligible = bool(eligibility.pop("eligible"))
    numpy_latency = float(numpy_result["latencies"][phase]["p50Nanoseconds"])
    jax_latency = float(jax_result["latencies"][phase]["p50Nanoseconds"])
    return {
        "numpyResultId": numpy_result["resultId"],
        "jaxResultId": jax_result["resultId"],
        "phase": "cold_total" if phase == "coldTotal" else "warm",
        "eligibility": eligibility,
        "speedup": (
            {"status": "measured", "ratio": numpy_latency / jax_latency}
            if eligible
            else _not_applicable("comparison contexts are not ratio-eligible")
        ),
    }


def _latency_report_cell(summary: JsonObject) -> str:
    if not _is_measured(summary):
        return "N/A"
    return f"{float(summary['p50Nanoseconds']):.3f} / {float(summary['p95Nanoseconds']):.3f}"


def _throughput_report_cell(value: JsonObject) -> str:
    if not _is_measured(value):
        return "N/A"
    return f"{float(value['valuePerSecond']):.6g}"


def _report_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _write_report(path: Path, manifest: JsonObject, matrix: str) -> None:
    """Manifest의 모든 필수 performance/environment/limit 근거를 Markdown으로 낸다."""

    execution = manifest["execution"]
    provenance = manifest["dsrTrialProvenance"]
    artifacts = manifest["artifacts"]
    lines = [
        "# S1.4R bounded benchmark report / 제한형 벤치마크 보고서",
        "",
        "## Question and non-goals / 질문과 비목표",
        "",
        (
            "- KR: 동일한 canonical fixture와 512 MiB data-working-set 한도에서 "
            "검증형 NumPy reference와 JAX CPU/x64 compiled numeric core의 cold 단계와 "
            "steady-state 특성을 분리해 관찰한다."
        ),
        (
            "- EN: Observe cold phases and steady-state behavior of the validated "
            "NumPy reference and JAX CPU/x64 compiled numeric core with identical "
            "canonical fixtures under a 512 MiB data-working-set cap."
        ),
        (
            "- KR 비목표: production 교체 결정, 성능 합격선, GPU/grad, annualization, "
            "서로 다른 timed boundary 사이의 speedup 주장."
        ),
        (
            "- EN non-goals: production replacement, performance thresholds, "
            "GPU/grad, annualization, or speedup claims across different timed boundaries."
        ),
        "",
        "## Run and matrix / 실행과 행렬",
        "",
        f"- Matrix: `{matrix}`",
        f"- Cases: {manifest['caseCount']} (results: {len(manifest['results'])})",
        f"- Run ID: `{manifest['runId']}`",
        f"- Git commit: `{manifest['gitCommit']}`",
        f"- Created UTC: `{manifest['createdAtUtc']}`",
        f"- Plan SHA-256: `{manifest['planSha256']}`",
        f"- Raw samples SHA-256: `{manifest['rawSamplesSha256']}`",
        (
            "- Two axes: one-dimensional sizes "
            "`[32, 252, 1000, 10000, 100000]`; path batches "
            "`[100, 1000, 10000] x horizon 252` without Cartesian materialization."
        ),
        (
            f"- Protocol: {COLD_FRESH_PROCESSES} fresh cold processes, "
            f"{UNTIMED_WARMUPS} untimed warmups, {TIMED_WARM_SAMPLES} timed warm "
            "samples, NumPy `linear` quantiles."
        ),
        f"- Allocation cap: {ALLOCATION_CAP_BYTES} bytes.",
        "",
        "## Correctness and timing boundaries / 정확성과 측정 경계",
        "",
        (
            "- Every generated fixture was checked over all valid evaluations against "
            "the same chunked JIT/vmap numeric path before timing (`rtol=1e-10`, "
            "`atol=1e-12`)."
        ),
        (
            "- NumPy timing boundary: `validated_public_reference`; JAX timing boundary: "
            "`compiled_device_numeric_core`. Validation, fixture generation, JSON/file "
            "decode, and JAX transfers are excluded from JAX warm execution."
        ),
        "",
        "Latency values are `p50 / p95` nanoseconds.",
        "",
        (
            "| Case | Impl | Boundary | First call | Trace/lower | Compile | H→D | "
            "First execute | D→H | Cold total | Warm | Max abs error | Max rel error | "
            "Max tolerance ratio |"
        ),
        (
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        ),
    ]
    for result in manifest["results"]:
        latency = result["latencies"]
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{result['caseId']}`",
                    f"`{result['implementation']}`",
                    f"`{result['timingBoundary']}`",
                    _latency_report_cell(latency["firstCall"]),
                    _latency_report_cell(latency["traceLower"]),
                    _latency_report_cell(latency["compile"]),
                    _latency_report_cell(latency["hostToDevice"]),
                    _latency_report_cell(latency["firstExecute"]),
                    _latency_report_cell(latency["deviceToHost"]),
                    _latency_report_cell(latency["coldTotal"]),
                    _latency_report_cell(latency["warm"]),
                    f"{float(result['maxAbsoluteError']):.6g}",
                    f"{float(result['maxRelativeError']):.6g}",
                    f"{float(result['maxToleranceRatio']):.6g}",
                )
            )
            + " |"
        )

    lines.extend(
        (
            "",
            "## Throughput / 처리량",
            "",
            (
                "| Case | Impl | calls/s | observations/s | paths/s | "
                "path-observations/s | evaluations/s |"
            ),
            "|---|---|---:|---:|---:|---:|---:|",
        )
    )
    for result in manifest["results"]:
        throughput = result["throughput"]
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{result['caseId']}`",
                    f"`{result['implementation']}`",
                    _throughput_report_cell(throughput["callsPerSecond"]),
                    _throughput_report_cell(throughput["observationsPerSecond"]),
                    _throughput_report_cell(throughput["pathsPerSecond"]),
                    _throughput_report_cell(throughput["pathObservationsPerSecond"]),
                    _throughput_report_cell(throughput["evaluationsPerSecond"]),
                )
            )
            + " |"
        )

    lines.extend(
        (
            "",
            "## Memory and chunking / 메모리와 청킹",
            "",
            (
                "RSS is diagnostic process evidence and is not added to the bounded "
                "input/temporary/output data-working-set ledger."
            ),
            "",
            (
                "| Case | Impl | Estimated/cap bytes | NumPy traced bytes | "
                "RSS baseline/peak/delta | Chunk size/count/last/padding | Estimator |"
            ),
            "|---|---|---:|---:|---:|---:|---|",
        )
    )
    for result in manifest["results"]:
        allocation = result["allocation"]
        traced = allocation["numpyTracemallocPeakBytes"]
        traced_text = str(traced["value"]) if _is_measured(traced) else "N/A"
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{result['caseId']}`",
                    f"`{result['implementation']}`",
                    (
                        f"{allocation['estimatedPeakAllocationBytes']} / "
                        f"{allocation['allocationCapBytes']}"
                    ),
                    traced_text,
                    (
                        f"{allocation['rssBaselineBytes']} / "
                        f"{allocation['rssPeakBytes']} / "
                        f"{allocation['rssDeltaBytes']}"
                    ),
                    (
                        f"{allocation['chunkSize']} / {allocation['chunkCount']} / "
                        f"{allocation['lastChunkValidPaths']} / "
                        f"{allocation['paddingPaths']}"
                    ),
                    f"`{allocation['allocationEstimator']}`",
                )
            )
            + " |"
        )

    lines.extend(
        (
            "",
            "## Comparison eligibility / 비교 자격",
            "",
            (
                "| Case | Phase | All eligible | Same timed boundary | Speedup | "
                "Eligibility record |"
            ),
            "|---|---|---|---|---:|---|",
        )
    )
    for comparison in manifest["comparisons"]:
        eligibility = comparison["eligibility"]
        all_eligible = all(bool(value) for value in eligibility.values())
        speedup = comparison["speedup"]
        speedup_text = f"{float(speedup['ratio']):.6g}x" if _is_measured(speedup) else "N/A"
        case_id = str(comparison["numpyResultId"]).removesuffix("--numpy")
        lines.append(
            f"| `{case_id}` | `{comparison['phase']}` | "
            f"{str(all_eligible).lower()} | "
            f"{str(eligibility['sameTimedBoundary']).lower()} | "
            f"{speedup_text} | `{_report_json(eligibility)}` |"
        )

    lines.extend(
        (
            "",
            "## Environment / 환경",
            "",
            f"- Execution boundary: `{execution['executionBoundary']}`",
            f"- Outer host boundary: `{execution['outerHostBoundary']}`",
            f"- Host fingerprint: `{execution['hostFingerprint']}`",
            (
                f"- OS/kernel/architecture: `{execution['os']}` / "
                f"`{execution['kernel']}` / `{execution['architecture']}`"
            ),
            f"- WSL version evidence: `{_report_json(execution['wslVersion'])}`",
            f"- CPU model: `{execution['cpuModel']}`",
            (
                f"- Physical/logical cores: {execution['physicalCores']} / "
                f"{execution['logicalCores']}"
            ),
            f"- CPU affinity: `{_report_json(execution['cpuAffinity'])}`",
            f"- CPU governor: `{_report_json(execution['cpuGovernor'])}`",
            f"- Memory bytes: {execution['memoryBytes']}",
            (
                f"- Python/NumPy/JAX/JAXLIB: `{execution['pythonVersion']}` / "
                f"`{execution['numpyVersion']}` / `{execution['jaxVersion']}` / "
                f"`{execution['jaxlibVersion']}`"
            ),
            (
                f"- Backend/devices/x64: `{execution['backend']}` / "
                f"`{_report_json(execution['devices'])}` / {execution['x64Enabled']}"
            ),
            (f"- Thread environment: `{_report_json(execution['threadEnvironment'])}`"),
            (
                f"- Container runtime/image: "
                f"`{_report_json(execution['containerRuntime'])}` / "
                f"`{_report_json(execution['containerImageId'])}`"
            ),
            "",
            "## DSR provenance / DSR 출처",
            "",
            f"- Record: `{_report_json(provenance)}`",
            (
                "- The benchmark uses the strict-JSON trial registry digest, two "
                "pre-registered independent daily trials, and ddof=1 variance. "
                "The host validates provenance and independently checks the expected "
                "probability before timing. The JAX DSR numeric core recomputes SR* "
                "from N and variance, including log-tail inverse-normal arithmetic, "
                "inside the compiled timing boundary."
            ),
            "",
            "## Artifact sizes and identity / 산출물 크기와 동일성",
            "",
            (
                f"- Research wheel: {artifacts['researchWheel']['bytes']} bytes; "
                f"SHA-256 `{artifacts['researchWheel']['sha256']}`"
            ),
            (
                "- Installed research environment: "
                f"{artifacts['installedResearchEnvironment']['bytes']} bytes "
                f"({artifacts['installedResearchEnvironment']['measurementMethod']})"
            ),
            (
                f"- OCI image: {artifacts['ociImage']['engineReportedSizeBytes']} bytes; "
                f"ID `{artifacts['ociImage']['imageId']}`; manifest "
                f"`{artifacts['ociImage']['manifestDigest']}`; "
                f"{artifacts['ociImage']['measurementMethod']}"
            ),
            (
                f"- OCI archive: {artifacts['ociArchive']['uncompressedBytes']} "
                f"uncompressed bytes; {artifacts['ociArchive']['compressedBytes']} "
                f"compressed bytes; SHA-256 "
                f"`{artifacts['ociArchive']['compressedSha256']}`"
            ),
            (f"- Native executable: `{_report_json(artifacts['nativeExecutable'])}`"),
            "",
            "## Limitations and conclusion / 한계와 결론",
            "",
            (
                "- KR: 수치는 이 host/run/affinity/thread 환경에 한정되며 CPU 주파수, "
                "스케줄링, compiler 상태의 영향을 받는다. PSR/DSR은 작은 표본에서 "
                "asymptotic 한계가 있다. RSS는 runtime baseline을 포함한 진단값이다."
            ),
            (
                "- EN: Measurements are specific to this host/run/affinity/thread "
                "context and remain sensitive to CPU frequency, scheduling, and "
                "compiler state. PSR/DSR retain small-sample asymptotic limitations. "
                "RSS is diagnostic and includes runtime baseline effects."
            ),
            (
                "- KR/EN: NumPy와 JAX의 timed boundary가 다르므로 이 manifest는 "
                "speedup ratio를 보고하지 않는다 / This manifest reports no speedup "
                "ratio because the NumPy and JAX timed boundaries differ."
            ),
            (
                "- KR/EN: 이는 격리된 research evidence이며 production 구현 교체 "
                "결론을 내리지 않는다 / This is isolated research evidence and makes "
                "no production replacement conclusion."
            ),
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_matrix(matrix: str, output_dir: Path) -> JsonObject:
    commit = _git_commit()
    plan = build_benchmark_plan()
    cases = _selected_cases(plan, matrix)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "benchmark-plan.json", plan)
    _run_correctness_prerequisite()
    execution = _execution_manifest()
    run_id = f"run-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    results: list[JsonObject] = []
    raw_results: list[JsonObject] = []
    comparisons: list[JsonObject] = []
    fixture_dir = output_dir / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "dsr-trial-registry-v1.json").write_bytes(dsr_trial_registry_bytes())
    for case in cases:
        preflight = _preflight_case(case)
        fixture = _generate_fixture(case, fixture_dir)
        _refine_preflight_with_measured_memory(case, fixture, preflight)
        max_absolute, max_relative, max_tolerance_ratio = _fixture_parity(
            case,
            fixture,
            preflight["layout"],
        )
        paired: dict[str, JsonObject] = {}
        for implementation in ("numpy", "jax_jit"):
            result, raw = _benchmark_one_implementation(
                case,
                fixture,
                preflight,
                implementation=implementation,
                max_absolute_error=max_absolute,
                max_relative_error=max_relative,
                max_tolerance_ratio=max_tolerance_ratio,
            )
            paired[implementation] = result
            results.append(result)
            raw_results.append(raw)
        comparisons.extend(
            (
                _comparison(
                    paired["numpy"],
                    paired["jax_jit"],
                    execution,
                    run_id,
                    phase="coldTotal",
                ),
                _comparison(
                    paired["numpy"],
                    paired["jax_jit"],
                    execution,
                    run_id,
                    phase="warm",
                ),
            )
        )
    raw_document = {
        "schemaVersion": "s1.4r-benchmark-raw-samples-v1",
        "runId": run_id,
        "results": raw_results,
    }
    _write_json(output_dir / "raw-samples.json", raw_document)
    manifest: JsonObject = {
        "schemaVersion": "s1.4r-benchmark-manifest-v1",
        "matrix": matrix,
        "caseCount": len(cases),
        "planSha256": _sha256_bytes(strict_json_bytes(plan)),
        "rawSamplesSha256": _sha256_bytes(strict_json_bytes(raw_document)),
        "dsrTrialProvenance": plan["dsrTrialProvenance"],
        "gitCommit": commit,
        "runId": run_id,
        "createdAtUtc": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "execution": execution,
        "protocol": {
            "coldFreshProcesses": COLD_FRESH_PROCESSES,
            "untimedWarmups": UNTIMED_WARMUPS,
            "timedWarmSamples": TIMED_WARM_SAMPLES,
            "quantileMethod": "linear",
            "timer": "perf_counter_ns",
            "allocationCapBytes": ALLOCATION_CAP_BYTES,
        },
        "results": results,
        "comparisons": comparisons,
        "artifacts": _collect_artifacts(output_dir, commit),
    }
    validate_manifest_invariants(manifest, plan, raw_document)
    _write_json(output_dir / "benchmark-manifest.json", manifest)
    _write_report(output_dir / "benchmark-report.md", manifest, matrix)
    return manifest


def _dry_run(matrix: str) -> JsonObject:
    plan = build_benchmark_plan()
    cases = _selected_cases(plan, matrix)
    preflight = [
        {
            "caseId": case["caseId"],
            **_preflight_case(case),
        }
        for case in cases
    ]
    return {
        "status": "dry_run_passed",
        "matrix": matrix,
        "caseCount": len(cases),
        "planSha256": _sha256_bytes(strict_json_bytes(plan)),
        "allocationCapBytes": ALLOCATION_CAP_BYTES,
        "preflight": preflight,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="emit the frozen 62-case plan")
    plan_parser.add_argument("--output", type=Path)
    dry_parser = subparsers.add_parser(
        "dry-run",
        help="validate plan/schema/allocation without timing",
    )
    dry_parser.add_argument(
        "--matrix",
        choices=("smallest", "full"),
        default="smallest",
    )
    run_parser = subparsers.add_parser(
        "run",
        help="run correctness, benchmark, OCI correctness, and artifact evidence",
    )
    run_parser.add_argument(
        "--matrix",
        choices=("smallest", "full"),
        required=True,
    )
    run_parser.add_argument("--output-dir", type=Path, required=True)
    validate_parser = subparsers.add_parser(
        "validate-manifest",
        help="validate a tracked benchmark manifest",
    )
    validate_parser.add_argument("manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command == "plan":
        plan = build_benchmark_plan()
        if arguments.output is not None:
            _write_json(arguments.output, plan)
        else:
            sys.stdout.buffer.write(strict_json_bytes(plan) + b"\n")
        return 0
    if arguments.command == "dry-run":
        sys.stdout.buffer.write(strict_json_bytes(_dry_run(arguments.matrix)) + b"\n")
        return 0
    if arguments.command == "run":
        manifest = _run_matrix(arguments.matrix, arguments.output_dir)
        sys.stdout.buffer.write(
            strict_json_bytes(
                {
                    "status": "benchmark_complete",
                    "manifest": str(arguments.output_dir / "benchmark-manifest.json"),
                    "manifestSha256": _sha256_bytes(strict_json_bytes(manifest)),
                }
            )
            + b"\n"
        )
        return 0
    if arguments.command == "validate-manifest":
        manifest = _load_json(arguments.manifest)
        plan_path = arguments.manifest.with_name("benchmark-plan.json")
        raw_path = arguments.manifest.with_name("raw-samples.json")
        if not plan_path.is_file() or not raw_path.is_file():
            raise RuntimeError(
                "manifest validation requires sibling benchmark-plan.json "
                "and raw-samples.json evidence"
            )
        validate_manifest_invariants(
            manifest,
            _load_json(plan_path),
            _load_json(raw_path),
        )
        sys.stdout.buffer.write(
            strict_json_bytes(
                {
                    "status": "manifest_valid",
                    "path": str(arguments.manifest),
                    "sha256": _sha256_file(arguments.manifest),
                }
            )
            + b"\n"
        )
        return 0
    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
