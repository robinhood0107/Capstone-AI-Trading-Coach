from __future__ import annotations

import ast
import inspect
import math
import os
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import jax
import numpy as np
import pytest

from s1_4r_risk_research import jax_reference
from s1_4r_risk_research.models import (
    ConditionalCoverageTestResult,
    EffectiveTrialProvenance,
    IndependenceTestResult,
    LikelihoodRatioTestResult,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JAX_SOURCE_PATHS = (
    PROJECT_ROOT / "src/s1_4r_risk_research/_jax_kernels.py",
    PROJECT_ROOT / "src/s1_4r_risk_research/jax_reference.py",
)
KERNEL_SOURCE_PATH = PROJECT_ROOT / "src/s1_4r_risk_research/_jax_kernels.py"

_TRIAL_PROVENANCE = EffectiveTrialProvenance(
    schema_version="s1.4r-effective-trials-v1",
    method="pre_registered_independent",
    raw_trial_count=2,
    effective_trial_count=2,
    sampling_frequency="daily",
    trial_registry_sha256="a" * 64,
    variance_ddof=1,
)


def _runtime_calls() -> tuple[tuple[str, tuple[Any, ...], dict[str, Any], type[Any]], ...]:
    alternating_losses = np.asarray([0.0, 2.0, 0.0, 2.0, 0.0], dtype=np.float64)
    alternating_vars = np.ones(5, dtype=np.float64)
    return (
        (
            "historical_expected_shortfall",
            (np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float64),),
            {"confidence": 0.625},
            float,
        ),
        (
            "realized_variance",
            (np.asarray([1.0, -2.0, 2.0], dtype=np.float64),),
            {},
            float,
        ),
        (
            "realized_volatility_intraday",
            (np.asarray([0.01, -0.02, 0.03], dtype=np.float64),),
            {},
            float,
        ),
        (
            "lo_adjusted_sharpe_ratio",
            (np.asarray([-1.0, 0.0, 1.0, 2.0], dtype=np.float64),),
            {"aggregation_periods": 2, "risk_free_rate": 0.0},
            float,
        ),
        (
            "probabilistic_sharpe_ratio",
            (1.0,),
            {
                "benchmark_sharpe": 0.0,
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
            },
            float,
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": 2,
                "sharpe_estimate_variance": 1.0,
                "trial_provenance": _TRIAL_PROVENANCE,
            },
            float,
        ),
        (
            "kupiec_unconditional_coverage_test",
            (
                np.asarray([0.0, 0.0, 0.0, 0.0], dtype=np.float64),
                np.ones(4, dtype=np.float64),
            ),
            {"confidence": 0.75, "significance": 0.05},
            LikelihoodRatioTestResult,
        ),
        (
            "christoffersen_independence_test",
            (alternating_losses, alternating_vars),
            {"significance": 0.05},
            IndependenceTestResult,
        ),
        (
            "christoffersen_conditional_coverage_test",
            (alternating_losses, alternating_vars),
            {"confidence": 0.6, "significance": 0.05},
            ConditionalCoverageTestResult,
        ),
    )


def _assert_host_python_value(value: object) -> None:
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_host_python_value(getattr(value, field.name))
        return
    if isinstance(value, bool):
        assert type(value) is bool
        return
    if isinstance(value, int):
        assert type(value) is int
        return
    if isinstance(value, float):
        assert type(value) is float
        assert math.isfinite(value)
        return
    pytest.fail(f"JAX public wrapper returned a non-host value: {type(value)!r}")


def _terminal_call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _assigned_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _assigned_names(element)
        }
    return set()


def _is_boolean_expression(node: ast.AST) -> bool:
    if isinstance(node, (ast.Compare, ast.BoolOp)):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return True
    if isinstance(node, ast.Call):
        name = _terminal_call_name(node)
        return name in {
            "isfinite",
            "isinf",
            "isnan",
            "logical_and",
            "logical_not",
            "logical_or",
            "logical_xor",
        }
    return False


def _boolean_array_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if _is_boolean_expression(value):
                for target in targets:
                    names.update(_assigned_names(target))
    return names


def _looks_like_dynamic_boolean_index(index: ast.AST, boolean_names: set[str]) -> bool:
    if _is_boolean_expression(index):
        return True
    for node in ast.walk(index):
        if not isinstance(node, ast.Name):
            continue
        normalized = node.id.lower()
        if node.id in boolean_names or any(
            marker in normalized
            for marker in ("mask", "selector", "condition", "indicator")
        ):
            return True
    return False


def _literal_static_argnames(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        names: set[str] = set()
        for element in node.elts:
            names.update(_literal_static_argnames(element))
        return names
    pytest.fail("JIT static_argnames must be a literal string collection")


def test_jax_runtime_is_explicit_cpu_x64() -> None:
    assert os.environ.get("JAX_PLATFORMS") == "cpu"
    assert os.environ.get("JAX_ENABLE_X64") == "1"
    assert jax.default_backend() == "cpu"
    assert jax.devices()
    assert all(device.platform == "cpu" for device in jax.devices())
    assert jax.config.jax_enable_x64 is True


def test_jax_module_exposes_exact_research_function_set() -> None:
    public_functions = {
        name
        for name, value in vars(jax_reference).items()
        if (
            not name.startswith("_")
            and inspect.isfunction(value)
            and value.__module__ == jax_reference.__name__
        )
    }
    assert public_functions == {case[0] for case in _runtime_calls()}


@pytest.mark.parametrize("jit", [False, True], ids=["eager", "jit"])
@pytest.mark.parametrize(
    ("function_name", "args", "kwargs", "expected_type"),
    _runtime_calls(),
    ids=[case[0] for case in _runtime_calls()],
)
def test_all_public_jax_paths_return_host_values(
    function_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    expected_type: type[Any],
    *,
    jit: bool,
) -> None:
    function = getattr(jax_reference, function_name)

    result = function(*args, **kwargs, jit=jit)

    assert type(result) is expected_type
    _assert_host_python_value(result)


@pytest.mark.parametrize("jit", [False, True], ids=["eager", "jit"])
def test_realized_variance_preserves_float64_precision(*, jit: bool) -> None:
    # 이 값은 float32로 내리면 첫 관측치의 +1이 사라져 입력 dtype 위반을 드러낸다.
    returns = np.asarray([134_217_729.0, -134_217_728.0], dtype=np.float64)
    expected = float(np.sum(np.square(returns), dtype=np.float64))

    actual = jax_reference.realized_variance(returns, jit=jit)

    assert math.isclose(actual, expected, rel_tol=1e-15, abs_tol=0.0)


@pytest.mark.parametrize("aggregation_periods", [1, 2])
def test_lo_jit_accepts_only_the_declared_shape_control(
    aggregation_periods: int,
) -> None:
    result = jax_reference.lo_adjusted_sharpe_ratio(
        np.asarray([-1.0, 0.0, 1.0, 2.0], dtype=np.float64),
        aggregation_periods=aggregation_periods,
        risk_free_rate=0.0,
        jit=True,
    )

    assert type(result) is float
    assert math.isfinite(result)


@pytest.mark.parametrize("source_path", JAX_SOURCE_PATHS, ids=lambda path: path.name)
def test_jax_source_never_uses_uninitialized_arrays(source_path: Path) -> None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"jax.numpy", "jax._src.numpy"}:
            for alias in node.names:
                if alias.name in {"empty", "empty_like"}:
                    violations.append((node.lineno, alias.name))
        if isinstance(node, ast.Call):
            name = _terminal_call_name(node)
            if name in {"empty", "empty_like"}:
                violations.append((node.lineno, name))

    assert violations == [], f"uninitialized JAX allocation found: {violations}"


def test_jax_kernels_have_no_dynamic_boolean_shape_operations() -> None:
    tree = ast.parse(
        KERNEL_SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(KERNEL_SOURCE_PATH),
    )
    boolean_names = _boolean_array_names(tree)
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _looks_like_dynamic_boolean_index(
            node.slice,
            boolean_names,
        ):
            violations.append((node.lineno, "boolean-index"))
        if not isinstance(node, ast.Call):
            continue
        name = _terminal_call_name(node)
        if name == "where":
            keyword_names = {keyword.arg for keyword in node.keywords}
            has_fixed_outputs = len(node.args) >= 3 or {"x", "y"} <= keyword_names
            if not has_fixed_outputs:
                violations.append((node.lineno, "one-argument-where"))
        if name in {"argwhere", "compress", "flatnonzero", "nonzero"}:
            violations.append((node.lineno, name))

    assert violations == [], f"dynamic-shape JAX operation found: {violations}"


def test_jax_source_static_and_transform_contracts() -> None:
    trees = [
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in JAX_SOURCE_PATHS
    ]
    static_names: set[str] = set()
    violations: list[tuple[int, str]] = []

    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "jax":
                for alias in node.names:
                    if alias.name == "grad":
                        violations.append((node.lineno, "grad-import"))
            if not isinstance(node, ast.Call):
                continue
            name = _terminal_call_name(node)
            if name == "grad":
                violations.append((node.lineno, "grad"))
            if name == "vmap":
                in_axes = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "in_axes"),
                    None,
                )
                if in_axes is None:
                    violations.append((node.lineno, "vmap-without-explicit-leading-axis"))
                elif isinstance(in_axes, ast.Constant):
                    if in_axes.value != 0:
                        violations.append((node.lineno, "vmap-nonleading-axis"))
                elif isinstance(in_axes, (ast.Tuple, ast.List)):
                    values = [
                        element.value if isinstance(element, ast.Constant) else object()
                        for element in in_axes.elts
                    ]
                    if (
                        not values
                        or 0 not in values
                        or any(value not in {0, None} for value in values)
                    ):
                        violations.append((node.lineno, "vmap-nonleading-axis"))
                else:
                    violations.append((node.lineno, "vmap-dynamic-axis"))
            if name != "jit":
                continue
            for keyword in node.keywords:
                if keyword.arg == "static_argnums":
                    violations.append((node.lineno, "unnamed-static-arg"))
                elif keyword.arg == "static_argnames":
                    static_names.update(_literal_static_argnames(keyword.value))

    assert violations == [], f"forbidden JAX transform contract found: {violations}"
    assert static_names == {"aggregation_periods"}
