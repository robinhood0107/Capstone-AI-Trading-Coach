"""Fresh-process timing worker for one S1.4R benchmark case and implementation."""

from __future__ import annotations

import importlib
import json
import math
import os
import resource
import sys
import time
import tracemalloc
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    from s1_4r_risk_research.models import (
        ConditionalCoverageTestResult,
        EffectiveTrialProvenance,
        IndependenceTestResult,
        LikelihoodRatioTestResult,
    )

JsonObject = dict[str, Any]
Operation = Callable[[], object]

_CONFIDENCE = 0.95
_SIGNIFICANCE = 0.05
_AGGREGATION_PERIODS = 5
_RISK_FREE_RATE = 0.0


def _strict_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _load_job() -> JsonObject:
    return cast(
        JsonObject,
        json.loads(
            sys.stdin.buffer.read(),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token: {token}")
            ),
        ),
    )


def _rss_peak_bytes() -> int:
    # Linux ru_maxrss is KiB; all required boundaries are Linux/WSL/OCI.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _rss_current_bytes() -> int:
    """Linux `/proc`에서 현재 resident set을 읽어 historical peak와 구분한다."""

    resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
    return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))


def _set_affinity(job: JsonObject) -> None:
    affinity = {int(cpu) for cpu in job["cpuAffinity"]}
    if not affinity:
        raise ValueError("cpuAffinity must not be empty")
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, affinity)


def _load_fixture(job: JsonObject) -> np.ndarray:
    path = Path(job["fixturePath"])
    shape = tuple(int(dimension) for dimension in job["fixtureShape"])
    expected = math.prod(shape)
    if path.stat().st_size != expected * np.dtype("<f8").itemsize:
        raise ValueError(
            "fixture size mismatch: "
            f"expected {expected * np.dtype('<f8').itemsize} bytes, "
            f"received {path.stat().st_size}"
        )
    # Path matrix를 whole-case ndarray로 복사하지 않고 cap ledger의 chunk 전제를 보존한다.
    result = np.memmap(path, dtype="<f8", mode="r", shape=shape)
    if job["case"]["axis"] == "one_dimensional" and not np.isfinite(result).all():
        raise ValueError("fixture contains non-finite values")
    return result


def _trial_provenance() -> EffectiveTrialProvenance:
    from benchmarks.run import dsr_trial_provenance_record
    from s1_4r_risk_research.models import EffectiveTrialProvenance

    record = dsr_trial_provenance_record()
    if (
        record["schemaVersion"] != "s1.4r-effective-trials-v1"
        or record["method"] != "pre_registered_independent"
        or record["varianceDdof"] != 1
    ):
        raise ValueError("generated DSR provenance contract drifted")
    return EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="pre_registered_independent",
        raw_trial_count=int(record["rawTrialCount"]),
        effective_trial_count=int(record["effectiveTrialCount"]),
        sampling_frequency=str(record["samplingFrequency"]),
        trial_registry_sha256=str(record["trialRegistrySha256"]),
        variance_ddof=1,
    )


def _numpy_single(kernel: str, values: np.ndarray) -> object:
    import s1_4r_risk_research.numpy_reference as numpy_reference

    if kernel == "historical_expected_shortfall":
        return numpy_reference.historical_expected_shortfall(
            values,
            confidence=_CONFIDENCE,
        )
    if kernel == "realized_variance":
        return numpy_reference.realized_variance(values)
    if kernel == "realized_volatility_intraday":
        return numpy_reference.realized_volatility_intraday(values)
    if kernel == "lo_adjusted_sharpe_ratio":
        return numpy_reference.lo_adjusted_sharpe_ratio(
            values,
            aggregation_periods=_AGGREGATION_PERIODS,
            risk_free_rate=_RISK_FREE_RATE,
        )
    if kernel == "probabilistic_sharpe_ratio":
        return numpy_reference.probabilistic_sharpe_ratio(
            float(values[0]),
            benchmark_sharpe=float(values[1]),
            sample_size=int(values[2]),
            skewness=float(values[3]),
            kurtosis=float(values[4]),
        )
    if kernel == "deflated_sharpe_ratio":
        return numpy_reference.deflated_sharpe_ratio(
            float(values[0]),
            sample_size=int(values[1]),
            skewness=float(values[2]),
            kurtosis=float(values[3]),
            trial_count=int(values[4]),
            sharpe_estimate_variance=float(values[5]),
            trial_provenance=_trial_provenance(),
        )
    if kernel == "kupiec_unconditional_coverage_test":
        return numpy_reference.kupiec_unconditional_coverage_test(
            values[0],
            values[1],
            confidence=_CONFIDENCE,
            significance=_SIGNIFICANCE,
        )
    if kernel == "christoffersen_independence_test":
        return numpy_reference.christoffersen_independence_test(
            values[0],
            values[1],
            significance=_SIGNIFICANCE,
        )
    if kernel == "christoffersen_conditional_coverage_test":
        return numpy_reference.christoffersen_conditional_coverage_test(
            values[0],
            values[1],
            confidence=_CONFIDENCE,
            significance=_SIGNIFICANCE,
        )
    raise ValueError(f"unsupported NumPy kernel: {kernel}")


def _iter_path_chunks(values: np.ndarray, chunk_size: int) -> Iterator[np.ndarray]:
    for start in range(0, values.shape[0], chunk_size):
        chunk = values[start : start + chunk_size]
        if not np.isfinite(chunk).all():
            raise ValueError("fixture contains non-finite values")
        if chunk.shape[0] == chunk_size:
            yield chunk
            continue
        padding = np.repeat(chunk[-1:], chunk_size - chunk.shape[0], axis=0)
        yield np.concatenate((chunk, padding), axis=0)


def _iter_runtime_chunks(
    case: JsonObject,
    fixture: np.ndarray,
    chunk_size: int,
) -> Iterator[np.ndarray]:
    """한 번에 한 chunk만 유지해 allocation ledger의 buffer 수명과 맞춘다."""

    if case["axis"] == "one_dimensional":
        yield fixture
        return
    yield from _iter_path_chunks(fixture, chunk_size)


def _numpy_operation(case: JsonObject, fixture: np.ndarray, chunk_size: int) -> Operation:
    kernel = str(case["kernel"])
    if case["axis"] == "one_dimensional":
        return lambda: _numpy_single(kernel, fixture)

    def operation() -> object:
        outputs: list[object] = []
        for chunk in _iter_path_chunks(fixture, chunk_size):
            outputs.extend(_numpy_single(kernel, row) for row in chunk)
        return outputs[: int(case["paths"])]

    return operation


def _block_tree(value: object) -> object:
    import jax

    return jax.tree.map(lambda leaf: leaf.block_until_ready(), value)


def _jax_packed_kernel(case: JsonObject) -> Callable[[object], object]:
    import jax
    import jax.numpy as jnp

    import s1_4r_risk_research._jax_kernels as _jax_kernels

    kernel = str(case["kernel"])

    def single(packed: Any) -> object:
        if kernel == "historical_expected_shortfall":
            return _jax_kernels.historical_expected_shortfall(
                packed,
                jnp.asarray(_CONFIDENCE, dtype=jnp.float64),
            )
        if kernel == "realized_variance":
            return _jax_kernels.realized_variance(packed)
        if kernel == "realized_volatility_intraday":
            return _jax_kernels.realized_volatility_intraday(packed)
        if kernel == "lo_adjusted_sharpe_ratio":
            return _jax_kernels.lo_adjusted_sharpe_ratio(
                packed,
                aggregation_periods=_AGGREGATION_PERIODS,
                risk_free_rate=jnp.asarray(_RISK_FREE_RATE, dtype=jnp.float64),
            )
        if kernel == "probabilistic_sharpe_ratio":
            return _jax_kernels.probabilistic_sharpe_ratio(
                packed[0],
                packed[1],
                packed[2],
                packed[3],
                packed[4],
            )
        if kernel == "deflated_sharpe_ratio":
            # Provenance record의 N/variance까지 포함한 실제 DSR numeric core를 측정한다.
            return _jax_kernels.deflated_sharpe_ratio(
                packed[0],
                packed[1],
                packed[2],
                packed[3],
                packed[4],
                packed[5],
            )
        if kernel == "kupiec_unconditional_coverage_test":
            return _jax_kernels.kupiec_unconditional_coverage_test(
                packed[0],
                packed[1],
                jnp.asarray(_CONFIDENCE, dtype=jnp.float64),
            )
        if kernel == "christoffersen_independence_test":
            return _jax_kernels.christoffersen_independence_test(
                packed[0],
                packed[1],
            )
        if kernel == "christoffersen_conditional_coverage_test":
            return _jax_kernels.christoffersen_conditional_coverage_test(
                packed[0],
                packed[1],
                jnp.asarray(_CONFIDENCE, dtype=jnp.float64),
            )
        raise ValueError(f"unsupported JAX kernel: {kernel}")

    return jax.vmap(single, in_axes=0, out_axes=0) if case["axis"] == "path_batch" else single


def _jax_compiled(
    case: JsonObject,
    example_shape: Sequence[int],
) -> tuple[Callable[[object], object], JsonObject, int, int]:
    import jax
    import jax.numpy as jnp

    packed_kernel = _jax_packed_kernel(case)
    jitted = jax.jit(packed_kernel)
    abstract = jax.ShapeDtypeStruct(  # type: ignore[no-untyped-call]
        tuple(example_shape),
        jnp.float64,
    )
    trace_start = time.perf_counter_ns()
    lowered = jitted.lower(abstract)
    trace_lower_ns = time.perf_counter_ns() - trace_start
    compile_start = time.perf_counter_ns()
    compiled = lowered.compile()
    compile_ns = time.perf_counter_ns() - compile_start
    analysis = compiled.memory_analysis()
    memory = {
        "argumentBytes": int(getattr(analysis, "argument_size_in_bytes", 0) or 0),
        "temporaryBytes": int(getattr(analysis, "temp_size_in_bytes", 0) or 0),
        "outputBytes": int(getattr(analysis, "output_size_in_bytes", 0) or 0),
        "aliasBytes": int(getattr(analysis, "alias_size_in_bytes", 0) or 0),
    }
    return cast(Callable[[object], object], compiled), memory, trace_lower_ns, compile_ns


def _numpy_parity_fields(kernel: str, value: object) -> tuple[float, ...]:
    """Public NumPy result에서 timed raw JAX kernel과 공통인 observable fields를 뽑는다."""

    from s1_4r_risk_research._numeric_common import (
        confidence_exception_log_likelihood,
        independence_likelihood_components,
        kupiec_likelihood_components,
    )

    if kernel not in {
        "kupiec_unconditional_coverage_test",
        "christoffersen_independence_test",
        "christoffersen_conditional_coverage_test",
    }:
        return (float(cast(float, value)),)
    likelihood = cast("LikelihoodRatioTestResult", value)
    statistic = float(likelihood.statistic)
    p_value = float(likelihood.p_value)
    exceptions = float(likelihood.exceptions)
    if kernel == "kupiec_unconditional_coverage_test":
        _, null_log, alternative_log = kupiec_likelihood_components(
            likelihood.observations,
            likelihood.exceptions,
            _CONFIDENCE,
        )
        return statistic, p_value, exceptions, null_log, alternative_log
    independence = cast("IndependenceTestResult", value)
    transitions = independence.transitions
    _, independent_log, markov_log = independence_likelihood_components(
        transitions.n00,
        transitions.n01,
        transitions.n10,
        transitions.n11,
    )
    fields = (
        statistic,
        p_value,
        exceptions,
        float(transitions.n00),
        float(transitions.n01),
        float(transitions.n10),
        float(transitions.n11),
        independent_log,
        markov_log,
    )
    if kernel == "christoffersen_independence_test":
        return fields
    conditional = cast("ConditionalCoverageTestResult", value)
    conditional_null_log = confidence_exception_log_likelihood(
        conditional.conditioned_observations,
        conditional.conditioned_exceptions,
        _CONFIDENCE,
    )
    return (
        *fields[:7],
        conditional_null_log,
        independent_log,
        markov_log,
        float(conditional.unconditional_component_statistic),
        float(conditional.independence_component_statistic),
    )


def _jax_parity_columns(kernel: str, value: object) -> tuple[np.ndarray, ...]:
    """Raw JAX output tree를 NumPy public observable field 순서로 투영한다."""

    if not isinstance(value, tuple):
        return (np.asarray(value, dtype=np.float64),)
    if kernel not in {
        "kupiec_unconditional_coverage_test",
        "christoffersen_independence_test",
        "christoffersen_conditional_coverage_test",
    }:
        raise ValueError(f"unexpected tuple output for parity: {kernel}")
    return tuple(np.asarray(field, dtype=np.float64) for field in value)


def _parity(job: JsonObject, fixture: np.ndarray) -> JsonObject:
    """전체 generated fixture를 timed numeric path와 같은 chunk/JIT/vmap로 검증한다."""

    import jax

    case = job["case"]
    kernel = str(case["kernel"])
    implementation = str(job["implementation"])
    chunk_size = int(job["chunkSize"])
    values: list[float] = []
    if implementation == "numpy":
        if case["axis"] == "one_dimensional":
            values.extend(_numpy_parity_fields(kernel, _numpy_single(kernel, fixture)))
            evaluations = 1
        else:
            evaluations = int(case["paths"])
            consumed = 0
            for chunk in _iter_path_chunks(fixture, chunk_size):
                valid = min(chunk_size, evaluations - consumed)
                for row in chunk[:valid]:
                    result = _numpy_single(kernel, row)
                    values.extend(_numpy_parity_fields(kernel, result))
                consumed += valid
            if consumed != evaluations:
                raise RuntimeError("NumPy parity did not consume every valid path")
    elif implementation == "jax_jit":
        shape = (
            fixture.shape if case["axis"] == "one_dimensional" else (chunk_size, *fixture.shape[1:])
        )
        compiled, _, _, _ = _jax_compiled(case, shape)
        if case["axis"] == "one_dimensional":
            device_values, _ = _device_put(fixture)
            output = jax.device_get(_block_tree(compiled(device_values)))
            del device_values
            columns = _jax_parity_columns(kernel, output)
            values.extend(float(item) for column in columns for item in column.reshape(-1))
            evaluations = 1
        else:
            evaluations = int(case["paths"])
            consumed = 0
            row_width: int | None = None
            for chunk in _iter_path_chunks(fixture, chunk_size):
                device_values, _ = _device_put(chunk)
                output = jax.device_get(_block_tree(compiled(device_values)))
                del device_values
                columns = _jax_parity_columns(kernel, output)
                valid = min(chunk_size, evaluations - consumed)
                matrix = np.column_stack([column.reshape(chunk_size)[:valid] for column in columns])
                row_width = matrix.shape[1]
                values.extend(float(item) for item in matrix.reshape(-1))
                consumed += valid
                del columns, matrix, output
            if consumed != evaluations or row_width is None:
                raise RuntimeError("JAX parity did not consume every valid path")
    else:
        raise ValueError(f"unsupported parity implementation: {implementation}")
    if not np.isfinite(np.asarray(values, dtype=np.float64)).all():
        raise ValueError("parity output contains non-finite values")
    return {
        "mode": "parity",
        "implementation": implementation,
        "evaluations": evaluations,
        "values": values,
    }


def _jax_memory_preflight(job: JsonObject) -> JsonObject:
    """Timing 전에 exact compiled shape의 XLA memory analysis를 별도 process에서 수집한다."""

    _, memory, _, _ = _jax_compiled(
        job["case"],
        tuple(int(dimension) for dimension in job["compileShape"]),
    )
    return {
        "mode": "memory_preflight",
        "implementation": "jax_jit",
        "memoryAnalysis": memory,
    }


def _numpy_memory_preflight(job: JsonObject, fixture: np.ndarray) -> JsonObject:
    """동일 chunk operation을 timing 전에 tracing해 analytical cap을 보강한다."""

    # Import/cache 초기화는 compiler/runtime baseline이며 data-working-set cap이 아니다.
    importlib.import_module("s1_4r_risk_research.numpy_reference")
    if job["case"]["kernel"] == "deflated_sharpe_ratio":
        _trial_provenance()
    operation = _numpy_operation(job["case"], fixture, int(job["chunkSize"]))
    baseline = _rss_current_bytes()
    tracemalloc.start()
    result = operation()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss = _rss_peak_bytes()
    del result
    return {
        "mode": "memory_preflight",
        "implementation": "numpy",
        "numpyTracemallocPeakBytes": int(peak),
        "rssBaselineBytes": baseline,
        "rssPeakBytes": peak_rss,
        "rssDeltaBytes": max(0, peak_rss - baseline),
    }


def _device_put(values: np.ndarray) -> tuple[object, int]:
    import jax

    start = time.perf_counter_ns()
    device_values = jax.device_put(values, may_alias=False)
    device_values.block_until_ready()
    return device_values, time.perf_counter_ns() - start


def _jax_cold(job: JsonObject, fixture: np.ndarray) -> JsonObject:
    import jax

    case = job["case"]
    chunk_size = int(job["chunkSize"])
    compile_shape = (
        fixture.shape
        if case["axis"] == "one_dimensional"
        else (chunk_size, *fixture.shape[1:])
    )
    cold_start = time.perf_counter_ns()
    compiled, memory, trace_lower_ns, compile_ns = _jax_compiled(
        case,
        compile_shape,
    )
    host_to_device_ns = 0
    first_execute_ns = 0
    device_to_host_ns = 0
    executed = False
    for chunk in _iter_runtime_chunks(case, fixture, chunk_size):
        device_values, transfer_ns = _device_put(chunk)
        host_to_device_ns += transfer_ns
        execute_start = time.perf_counter_ns()
        result = _block_tree(compiled(device_values))
        first_execute_ns += time.perf_counter_ns() - execute_start
        device_get_start = time.perf_counter_ns()
        jax.device_get(result)
        device_to_host_ns += time.perf_counter_ns() - device_get_start
        # 다음 chunk upload 전에 이전 device buffers를 해제해 double buffering을 막는다.
        del result, device_values
        executed = True
    if not executed:
        raise RuntimeError("JAX cold run produced no result")
    cold_total_ns = time.perf_counter_ns() - cold_start
    return {
        "mode": "cold",
        "implementation": "jax_jit",
        "samples": {
            "traceLower": trace_lower_ns,
            "compile": compile_ns,
            "hostToDevice": host_to_device_ns,
            "firstExecute": first_execute_ns,
            "deviceToHost": device_to_host_ns,
            "coldTotal": cold_total_ns,
        },
        "memoryAnalysis": memory,
    }


def _jax_warm(job: JsonObject, fixture: np.ndarray) -> JsonObject:
    case = job["case"]
    chunk_size = int(job["chunkSize"])
    compile_shape = (
        fixture.shape
        if case["axis"] == "one_dimensional"
        else (chunk_size, *fixture.shape[1:])
    )
    compiled, memory, _, _ = _jax_compiled(case, compile_shape)

    def one_sample() -> int:
        elapsed = 0
        for chunk in _iter_runtime_chunks(case, fixture, chunk_size):
            device_values, _ = _device_put(chunk)
            start = time.perf_counter_ns()
            result = _block_tree(compiled(device_values))
            elapsed += time.perf_counter_ns() - start
            # Timed execution 종료 뒤 다음 upload 전 buffer 수명을 명시적으로 닫는다.
            del result, device_values
        return elapsed

    for _ in range(int(job["warmups"])):
        one_sample()
    samples = [one_sample() for _ in range(int(job["samples"]))]
    return {
        "mode": "warm",
        "implementation": "jax_jit",
        "warmSamples": samples,
        "memoryAnalysis": memory,
        "numpyTracemallocPeakBytes": None,
    }


def _numpy_cold(job: JsonObject, fixture: np.ndarray) -> JsonObject:
    operation = _numpy_operation(job["case"], fixture, int(job["chunkSize"]))
    start = time.perf_counter_ns()
    operation()
    elapsed = time.perf_counter_ns() - start
    return {
        "mode": "cold",
        "implementation": "numpy",
        "samples": {
            "firstCall": elapsed,
            "coldTotal": elapsed,
        },
        "memoryAnalysis": {
            "argumentBytes": 0,
            "temporaryBytes": 0,
            "outputBytes": 0,
            "aliasBytes": 0,
        },
    }


def _numpy_warm(job: JsonObject, fixture: np.ndarray) -> JsonObject:
    operation = _numpy_operation(job["case"], fixture, int(job["chunkSize"]))
    for _ in range(int(job["warmups"])):
        operation()
    samples: list[int] = []
    for _ in range(int(job["samples"])):
        start = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - start)
    tracemalloc.start()
    operation()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "mode": "warm",
        "implementation": "numpy",
        "warmSamples": samples,
        "memoryAnalysis": {
            "argumentBytes": 0,
            "temporaryBytes": 0,
            "outputBytes": 0,
            "aliasBytes": 0,
        },
        "numpyTracemallocPeakBytes": int(peak),
    }


def main() -> int:
    job = _load_job()
    _set_affinity(job)
    mode = str(job["mode"])
    if mode == "memory_preflight":
        baseline = _rss_current_bytes()
        result = _jax_memory_preflight(job)
        peak = _rss_peak_bytes()
        result["rssBaselineBytes"] = baseline
        result["rssPeakBytes"] = peak
        result["rssDeltaBytes"] = max(0, peak - baseline)
        sys.stdout.buffer.write(_strict_json_bytes(result))
        return 0
    fixture = _load_fixture(job)
    baseline = _rss_current_bytes()
    implementation = str(job["implementation"])
    if mode == "parity":
        result = _parity(job, fixture)
    elif mode == "numpy_memory_preflight" and implementation == "numpy":
        result = _numpy_memory_preflight(job, fixture)
    elif implementation == "numpy" and mode == "cold":
        result = _numpy_cold(job, fixture)
    elif implementation == "numpy" and mode == "warm":
        result = _numpy_warm(job, fixture)
    elif implementation == "jax_jit" and mode == "cold":
        result = _jax_cold(job, fixture)
    elif implementation == "jax_jit" and mode == "warm":
        result = _jax_warm(job, fixture)
    else:
        raise ValueError(f"unsupported worker request: {implementation}/{mode}")
    peak = _rss_peak_bytes()
    result["rssBaselineBytes"] = baseline
    result["rssPeakBytes"] = peak
    result["rssDeltaBytes"] = max(0, peak - baseline)
    sys.stdout.buffer.write(_strict_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
