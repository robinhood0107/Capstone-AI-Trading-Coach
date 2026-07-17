from __future__ import annotations

import inspect
import math
import warnings
from decimal import Decimal
from fractions import Fraction
from functools import partial

import numpy as np
import pytest

import app.financial_engineering as financial_engineering
import app.financial_engineering.returns as returns_module
from app.financial_engineering import cagr, cumulative_return, log_returns, simple_returns
from app.financial_engineering.returns import (
    cagr as module_cagr,
)
from app.financial_engineering.returns import (
    cumulative_return as module_cumulative_return,
)
from app.financial_engineering.returns import (
    log_returns as module_log_returns,
)
from app.financial_engineering.returns import (
    simple_returns as module_simple_returns,
)

RTOL = 1e-12
ATOL = 1e-12
EXPECTED_PUBLIC_NAMES = (
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
)


class ListSubclass(list[float]):
    pass


class TupleSubclass(tuple[float, ...]):
    pass


class IntegerSubclass(int):
    pass


class FloatSubclass(float):
    pass


class NdarraySubclass(np.ndarray):
    pass


def assert_stable_error(
    code: str,
    function: object,
    *args: object,
    **kwargs: object,
) -> None:
    with pytest.raises(ValueError) as caught:
        function(*args, **kwargs)  # type: ignore[operator]
    assert str(caught.value) == code


def test_package_exports_exactly_the_eleven_public_functions() -> None:
    assert financial_engineering.__all__ == EXPECTED_PUBLIC_NAMES
    assert financial_engineering.simple_returns is module_simple_returns
    assert financial_engineering.log_returns is module_log_returns
    assert financial_engineering.cumulative_return is module_cumulative_return
    assert financial_engineering.cagr is module_cagr


@pytest.mark.parametrize(
    ("function", "expected_parameters"),
    [
        (simple_returns, (("prices", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),)),
        (log_returns, (("prices", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),)),
        (
            cumulative_return,
            (("returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),),
        ),
        (
            cagr,
            (
                ("prices", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                ("periods_per_year", inspect.Parameter.KEYWORD_ONLY, 252),
            ),
        ),
    ],
)
def test_return_function_signatures_are_stable(
    function: object,
    expected_parameters: tuple[tuple[str, inspect._ParameterKind, object], ...],
) -> None:
    parameters = inspect.signature(function).parameters
    actual = tuple((item.name, item.kind, item.default) for item in parameters.values())
    assert actual == expected_parameters


@pytest.mark.parametrize(
    "function",
    [simple_returns, log_returns, cumulative_return, cagr],
)
def test_public_return_docstrings_reference_the_tracked_ssot(function: object) -> None:
    docstring = inspect.getdoc(function)
    assert docstring is not None
    assert "shared-docs/metrics_definitions.md" in docstring


def test_constant_fixture() -> None:
    prices = [100.0, 100.0, 100.0]
    np.testing.assert_array_equal(simple_returns(prices), np.array([0.0, 0.0]))
    np.testing.assert_array_equal(log_returns(prices), np.array([0.0, 0.0]))
    assert cumulative_return([0.0, 0.0]) == 0.0
    assert cagr(prices) == 0.0


def test_compounding_fixture_uses_independent_hand_calculation() -> None:
    prices = [100.0, 110.0, 99.0]
    expected_simple = np.array([float(Fraction(1, 10)), float(Fraction(-1, 10))])
    expected_log = np.array([math.log(1.1), math.log(0.9)])
    expected_total = float(Fraction(-1, 100))

    np.testing.assert_allclose(simple_returns(prices), expected_simple, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(log_returns(prices), expected_log, rtol=RTOL, atol=ATOL)
    assert cumulative_return(expected_simple.tolist()) == pytest.approx(
        expected_total,
        rel=RTOL,
        abs=ATOL,
    )
    assert cagr(prices, periods_per_year=2) == pytest.approx(
        expected_total,
        rel=RTOL,
        abs=ATOL,
    )


def test_vector_results_are_fresh_float64_arrays() -> None:
    prices = np.array([100, 110, 99], dtype=np.int64)
    simple = simple_returns(prices)
    logarithmic = log_returns(prices)

    assert type(simple) is np.ndarray
    assert type(logarithmic) is np.ndarray
    assert simple.dtype == np.dtype(np.float64)
    assert logarithmic.dtype == np.dtype(np.float64)
    assert not np.shares_memory(simple, prices)
    assert not np.shares_memory(logarithmic, prices)
    assert not np.shares_memory(simple, logarithmic)


def test_scalar_results_are_exact_python_float() -> None:
    assert type(cumulative_return([0.1, -0.1])) is float
    assert type(cagr([100.0, 99.0], periods_per_year=1)) is float


@pytest.mark.parametrize(
    ("function", "values"),
    [
        (simple_returns, [1.0]),
        (log_returns, [1.0]),
        (cagr, [1.0]),
    ],
)
def test_price_functions_reject_single_value(function: object, values: list[float]) -> None:
    assert_stable_error("input_too_short", function, values)


def test_cumulative_return_accepts_its_exact_minimum() -> None:
    assert cumulative_return([0.25]) == 0.25


def test_exact_builtin_tuple_is_a_supported_container() -> None:
    np.testing.assert_allclose(
        simple_returns((100, 110.0, 99)),
        np.array([0.1, -0.1]),
        rtol=RTOL,
        atol=ATOL,
    )


@pytest.mark.parametrize(
    "function",
    [simple_returns, log_returns, cumulative_return, cagr],
)
def test_empty_numeric_input_is_rejected(function: object) -> None:
    assert_stable_error("input_empty", function, [])
    assert_stable_error("input_empty", function, np.array([], dtype=np.float64))


@pytest.mark.parametrize(
    "call",
    [
        partial(simple_returns, np.ones(100_001, dtype=np.float64)),
        partial(log_returns, np.ones(100_001, dtype=np.float64)),
        partial(cumulative_return, np.zeros(100_001, dtype=np.float64)),
        partial(cagr, np.ones(100_001, dtype=np.float64)),
        partial(cumulative_return, [0.0] * 100_001),
        partial(cumulative_return, (0.0,) * 100_001),
    ],
)
def test_100001_values_are_rejected_before_copy(call: object) -> None:
    assert_stable_error("input_too_long", call)


def test_100000_values_are_accepted() -> None:
    prices = np.ones(100_000, dtype=np.float64)
    returns = np.zeros(100_000, dtype=np.float64)

    assert simple_returns(prices).shape == (99_999,)
    assert log_returns(prices).shape == (99_999,)
    assert cumulative_return(returns) == 0.0
    assert cagr(prices) == 0.0


@pytest.mark.parametrize(
    ("values", "code"),
    [
        (True, "input_bool_invalid"),
        (np.bool_(True), "input_bool_invalid"),
        (1.0, "input_type_invalid"),
        (1j, "input_type_invalid"),
        (np.complex64(1j), "input_type_invalid"),
        ("1,2", "input_type_invalid"),
        (b"1,2", "input_type_invalid"),
        (iter([1.0, 2.0]), "input_type_invalid"),
        (ListSubclass([1.0, 2.0]), "input_type_invalid"),
        (TupleSubclass((1.0, 2.0)), "input_type_invalid"),
        ([IntegerSubclass(1), 2], "input_type_invalid"),
        ([FloatSubclass(1.0), 2.0], "input_type_invalid"),
        ([np.int64(1), 2], "input_type_invalid"),
        ([np.float64(1.0), 2.0], "input_type_invalid"),
        ([Decimal("1"), 2.0], "input_type_invalid"),
        ([Fraction(1, 1), 2.0], "input_type_invalid"),
        ([True, 2.0], "input_bool_invalid"),
        ([np.bool_(True), 2.0], "input_bool_invalid"),
        ([1j, 2.0], "input_complex_invalid"),
        ([np.complex128(1j), 2.0], "input_complex_invalid"),
        ([[1.0], [2.0]], "input_shape_invalid"),
        ([np.array([1.0]), 2.0], "input_shape_invalid"),
        ([frozenset({1.0}), 2.0], "input_shape_invalid"),
        ([range(1), 2.0], "input_shape_invalid"),
        (np.array(1.0), "input_shape_invalid"),
        (np.ones((1, 2), dtype=np.float64), "input_shape_invalid"),
        (np.ones((1, 2), dtype=np.bool_), "input_shape_invalid"),
        (np.array([True, False]), "input_bool_invalid"),
        (np.array([1j, 2j]), "input_complex_invalid"),
        (np.array([1, "2"], dtype=object), "input_type_invalid"),
        (np.array(["1", "2"]), "input_type_invalid"),
        (np.array(["2026-01-01"], dtype="datetime64[D]"), "input_type_invalid"),
        (np.ma.array([1.0, 2.0]), "input_type_invalid"),
        (np.array([1.0, 2.0]).view(NdarraySubclass), "input_type_invalid"),
    ],
)
def test_exact_runtime_type_contract(values: object, code: str) -> None:
    assert_stable_error(code, simple_returns, values)


def test_memmap_is_rejected_as_an_ndarray_subclass(tmp_path: object) -> None:
    path = tmp_path / "values.dat"  # type: ignore[operator]
    values = np.memmap(path, dtype=np.float64, mode="w+", shape=(2,))
    values[:] = [1.0, 2.0]
    assert_stable_error("input_type_invalid", simple_returns, values)


def test_generator_is_rejected_without_iteration() -> None:
    observed = {"iterations": 0}

    def one_shot_values() -> object:
        observed["iterations"] += 1
        yield 100.0
        observed["iterations"] += 1
        yield 101.0

    values = one_shot_values()
    assert_stable_error("input_type_invalid", simple_returns, values)
    assert observed == {"iterations": 0}


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ([True, [1.0]], "input_shape_invalid"),
        ([1j, True], "input_bool_invalid"),
        (["not-numeric", 1j], "input_complex_invalid"),
        ([math.nan], "input_too_short"),
        (np.full(100_001, math.nan), "input_too_long"),
        (np.array([], dtype=object), "input_type_invalid"),
    ],
)
def test_validation_precedence_is_global_and_canonical(values: object, code: str) -> None:
    assert_stable_error(code, simple_returns, values)


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_non_finite_input_is_rejected(non_finite: float) -> None:
    assert_stable_error("input_non_finite", simple_returns, [100.0, non_finite])


def test_float64_conversion_overflow_is_normalized() -> None:
    assert_stable_error("input_non_finite", simple_returns, [10**400, 1])


def test_float64_ndarray_cast_warning_is_normalized_under_strict_warning_policy() -> None:
    values = np.array(
        [np.longdouble(1.0), np.finfo(np.longdouble).max],
        dtype=np.longdouble,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert_stable_error("input_non_finite", simple_returns, values)


@pytest.mark.parametrize("function", [simple_returns, log_returns, cagr])
@pytest.mark.parametrize("invalid_price", [0.0, -1.0])
def test_price_domain_requires_strictly_positive_values(
    function: object,
    invalid_price: float,
) -> None:
    assert_stable_error("prices_non_positive", function, [100.0, invalid_price])
    assert_stable_error("prices_non_positive", function, [invalid_price, 100.0])


def test_keyword_error_precedes_price_domain_error() -> None:
    assert_stable_error(
        "periods_per_year_invalid",
        cagr,
        [0.0, 1.0],
        periods_per_year=0,
    )


def test_input_error_precedes_keyword_error() -> None:
    assert_stable_error(
        "input_non_finite",
        cagr,
        [math.nan, 1.0],
        periods_per_year=0,
    )


@pytest.mark.parametrize(
    "periods_per_year",
    [True, np.int64(252), 252.0, IntegerSubclass(252), 0, -1],
)
def test_cagr_rejects_invalid_periods_per_year(periods_per_year: object) -> None:
    assert_stable_error(
        "periods_per_year_invalid",
        cagr,
        [100.0, 101.0],
        periods_per_year=periods_per_year,
    )


def test_cumulative_return_domain_and_exact_total_loss() -> None:
    assert_stable_error("simple_return_below_minus_one", cumulative_return, [-1.0001])
    assert_stable_error(
        "simple_return_below_minus_one",
        cumulative_return,
        [0.0, -1.0001],
    )
    assert cumulative_return([-1.0]) == -1.0
    assert cumulative_return([0.5, -1.0, 10.0]) == -1.0


def test_exact_total_loss_short_circuits_irrelevant_intermediate_overflow() -> None:
    maximum = float(np.finfo(np.float64).max)
    assert cumulative_return([maximum, maximum, -1.0]) == -1.0


def test_input_ndarray_is_byte_identical_after_success_and_numeric_error() -> None:
    prices = np.array([100.0, 110.0, 99.0], dtype=np.float64)
    before = prices.tobytes()
    simple_returns(prices)
    log_returns(prices)
    cagr(prices)
    assert prices.tobytes() == before

    extreme = np.array(
        [np.nextafter(0.0, 1.0), np.finfo(np.float64).max],
        dtype=np.float64,
    )
    extreme_before = extreme.tobytes()
    assert_stable_error("result_non_finite", simple_returns, extreme)
    assert extreme.tobytes() == extreme_before


def test_return_functions_validate_and_snapshot_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = returns_module._validate_numeric_input
    observed = {"calls": 0}

    def counting_validator(values: object, *, min_length: int) -> np.ndarray:
        observed["calls"] += 1
        return original(values, min_length=min_length)

    monkeypatch.setattr(returns_module, "_validate_numeric_input", counting_validator)

    calls = [
        partial(simple_returns, [100.0, 101.0]),
        partial(log_returns, [100.0, 101.0]),
        partial(cumulative_return, [0.01]),
        partial(cagr, [100.0, 101.0], periods_per_year=1),
    ]
    for expected_count, call in enumerate(calls, start=1):
        call()
        assert observed["calls"] == expected_count


def test_repeated_calls_are_exactly_deterministic_and_outputs_do_not_alias() -> None:
    prices = np.array([100.0, 110.0, 99.0])
    first_simple = simple_returns(prices)
    second_simple = simple_returns(prices)
    first_log = log_returns(prices)
    second_log = log_returns(prices)

    np.testing.assert_array_equal(first_simple, second_simple)
    np.testing.assert_array_equal(first_log, second_log)
    assert first_simple is not second_simple
    assert first_log is not second_log
    assert not np.shares_memory(first_simple, second_simple)
    assert not np.shares_memory(first_log, second_log)
    assert cumulative_return(first_simple.tolist()) == cumulative_return(first_simple.tolist())
    assert cagr(prices, periods_per_year=2) == cagr(prices, periods_per_year=2)


def test_price_scale_invariance_and_return_identities() -> None:
    prices = np.array([101.0, 98.0, 105.0, 111.0])
    scaled = prices * 7.25
    simple = simple_returns(prices)
    logarithmic = log_returns(prices)

    np.testing.assert_allclose(simple_returns(scaled), simple, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(log_returns(scaled), logarithmic, rtol=RTOL, atol=ATOL)
    assert cumulative_return(simple) == pytest.approx(
        prices[-1] / prices[0] - 1.0,
        rel=RTOL,
        abs=ATOL,
    )
    assert math.fsum(logarithmic.tolist()) == pytest.approx(
        math.log(prices[-1]) - math.log(prices[0]),
        rel=RTOL,
        abs=ATOL,
    )


def test_extreme_log_returns_do_not_form_an_overflowing_price_ratio() -> None:
    tiny = float(np.nextafter(0.0, 1.0))
    maximum = float(np.finfo(np.float64).max)
    result = log_returns([tiny, maximum])
    expected = math.log(maximum) - math.log(tiny)
    assert result[0] == pytest.approx(expected, rel=RTOL, abs=ATOL)
    assert math.isfinite(float(result[0]))


@pytest.mark.parametrize(
    "call",
    [
        partial(
            simple_returns,
            [
                float(np.nextafter(0.0, 1.0)),
                float(np.finfo(np.float64).max),
            ],
        ),
        partial(
            cumulative_return,
            [
                float(np.finfo(np.float64).max),
                float(np.finfo(np.float64).max),
            ],
        ),
        partial(
            cagr,
            [
                float(np.nextafter(0.0, 1.0)),
                float(np.finfo(np.float64).max),
            ],
        ),
        partial(cagr, [1.0, 2.0], periods_per_year=10**400),
    ],
)
def test_kernel_overflow_is_normalized_to_result_non_finite(call: object) -> None:
    assert_stable_error("result_non_finite", call)


def test_finite_underflow_results_are_allowed() -> None:
    tiny = float(np.nextafter(0.0, 1.0))
    maximum = float(np.finfo(np.float64).max)

    assert simple_returns([maximum, tiny])[0] == -1.0
    assert cumulative_return([-0.9999999999999999] * 1_000) == -1.0
    assert cagr([maximum, tiny], periods_per_year=1) == -1.0


def test_local_numeric_policy_does_not_mutate_global_numpy_error_state() -> None:
    before = np.geterr()
    simple_returns([100.0, 101.0])
    assert_stable_error(
        "result_non_finite",
        cumulative_return,
        [
            float(np.finfo(np.float64).max),
            float(np.finfo(np.float64).max),
        ],
    )
    assert np.geterr() == before
