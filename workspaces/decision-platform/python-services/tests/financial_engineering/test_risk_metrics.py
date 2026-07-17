from __future__ import annotations

import ast
import inspect
import math
from decimal import Decimal
from functools import partial
from pathlib import Path

import numpy as np
import pytest

import app.financial_engineering as financial_engineering
import app.financial_engineering.risk_metrics as risk_module
from app.financial_engineering import (
    annualized_volatility,
    cagr,
    cumulative_return,
    historical_cvar,
    historical_var,
    max_drawdown,
    realized_volatility,
    sharpe_ratio,
    simple_returns,
    sortino_ratio,
)
from app.financial_engineering.risk_metrics import (
    _threshold_tail_mean,
)
from app.financial_engineering.risk_metrics import (
    annualized_volatility as module_annualized_volatility,
)
from app.financial_engineering.risk_metrics import (
    historical_cvar as module_historical_cvar,
)
from app.financial_engineering.risk_metrics import (
    historical_var as module_historical_var,
)
from app.financial_engineering.risk_metrics import (
    max_drawdown as module_max_drawdown,
)
from app.financial_engineering.risk_metrics import (
    realized_volatility as module_realized_volatility,
)
from app.financial_engineering.risk_metrics import (
    sharpe_ratio as module_sharpe_ratio,
)
from app.financial_engineering.risk_metrics import (
    sortino_ratio as module_sortino_ratio,
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


class IntegerSubclass(int):
    pass


class FloatSubclass(float):
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


def test_package_risk_exports_are_exact_and_identical() -> None:
    assert financial_engineering.__all__ == EXPECTED_PUBLIC_NAMES
    assert financial_engineering.realized_volatility is module_realized_volatility
    assert financial_engineering.annualized_volatility is module_annualized_volatility
    assert financial_engineering.max_drawdown is module_max_drawdown
    assert financial_engineering.sharpe_ratio is module_sharpe_ratio
    assert financial_engineering.sortino_ratio is module_sortino_ratio
    assert financial_engineering.historical_var is module_historical_var
    assert financial_engineering.historical_cvar is module_historical_cvar
    assert "_threshold_tail_mean" not in financial_engineering.__all__


@pytest.mark.parametrize(
    ("function", "expected_parameters"),
    [
        (
            realized_volatility,
            (("log_returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),),
        ),
        (
            annualized_volatility,
            (
                ("log_returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                ("periods_per_year", inspect.Parameter.KEYWORD_ONLY, 252),
            ),
        ),
        (
            max_drawdown,
            (("equity_curve", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),),
        ),
        (
            sharpe_ratio,
            (
                ("returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                ("risk_free_rate", inspect.Parameter.KEYWORD_ONLY, 0.0),
                ("periods_per_year", inspect.Parameter.KEYWORD_ONLY, 252),
            ),
        ),
        (
            sortino_ratio,
            (
                ("returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                ("target_return", inspect.Parameter.KEYWORD_ONLY, 0.0),
                ("periods_per_year", inspect.Parameter.KEYWORD_ONLY, 252),
            ),
        ),
        (
            historical_var,
            (
                ("returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                ("confidence", inspect.Parameter.KEYWORD_ONLY, 0.95),
            ),
        ),
        (
            historical_cvar,
            (
                ("returns", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect._empty),
                ("confidence", inspect.Parameter.KEYWORD_ONLY, 0.95),
            ),
        ),
    ],
)
def test_risk_function_signatures_are_stable(
    function: object,
    expected_parameters: tuple[tuple[str, inspect._ParameterKind, object], ...],
) -> None:
    parameters = inspect.signature(function).parameters
    actual = tuple((item.name, item.kind, item.default) for item in parameters.values())
    assert actual == expected_parameters


@pytest.mark.parametrize(
    "function",
    [
        realized_volatility,
        annualized_volatility,
        max_drawdown,
        sharpe_ratio,
        sortino_ratio,
        historical_var,
        historical_cvar,
    ],
)
def test_public_risk_docstrings_reference_the_tracked_ssot(function: object) -> None:
    docstring = inspect.getdoc(function)
    assert docstring is not None
    assert "shared-docs/metrics_definitions.md" in docstring


def test_constant_fixture() -> None:
    log_return_values = [0.0, 0.0]
    assert realized_volatility(log_return_values) == 0.0
    assert annualized_volatility(log_return_values) == 0.0
    assert max_drawdown([100.0, 100.0, 100.0]) == 0.0
    assert historical_var([0.0, 0.0]) == 0.0
    assert historical_cvar([0.0, 0.0]) == 0.0
    assert_stable_error("denominator_zero", sharpe_ratio, [0.0, 0.0])
    assert_stable_error("denominator_zero", sortino_ratio, [0.0, 0.0])


def test_volatility_fixture_uses_sample_standard_deviation() -> None:
    log_return_values = [0.0, 0.1, -0.1]
    assert realized_volatility(log_return_values) == pytest.approx(
        0.1,
        rel=RTOL,
        abs=ATOL,
    )
    assert annualized_volatility(
        log_return_values,
        periods_per_year=4,
    ) == pytest.approx(0.2, rel=RTOL, abs=ATOL)


def test_drawdown_fixture_uses_signed_running_peak_losses() -> None:
    equity = [100.0, 120.0, 90.0, 108.0, 60.0]
    hand_drawdowns = [0.0, 0.0, -0.25, -0.1, -0.5]
    assert min(hand_drawdowns) == -0.5
    assert max_drawdown(equity) == -0.5


def test_tail_fixture_uses_linear_var_and_threshold_tail_cvar() -> None:
    returns = [-0.10, -0.05, 0.0, 0.05, 0.10]
    assert historical_var(returns, confidence=0.8) == pytest.approx(
        -0.06,
        rel=RTOL,
        abs=ATOL,
    )
    assert historical_cvar(returns, confidence=0.8) == pytest.approx(
        -0.10,
        rel=RTOL,
        abs=ATOL,
    )


def test_ratio_fixture_uses_sample_std_and_full_sample_downside_denominator() -> None:
    returns = [-0.01, 0.02, 0.02]
    assert sharpe_ratio(returns, periods_per_year=1) == pytest.approx(
        1.0 / math.sqrt(3.0),
        rel=RTOL,
        abs=ATOL,
    )
    assert sortino_ratio(returns, periods_per_year=1) == pytest.approx(
        math.sqrt(3.0),
        rel=RTOL,
        abs=ATOL,
    )


def test_threshold_ties_are_included_and_not_replaced_by_exact_es() -> None:
    returns = [-0.10, -0.05, -0.05, -0.05, 0.10]
    var = historical_var(returns, confidence=0.6)
    cvar = historical_cvar(returns, confidence=0.6)
    assert var == pytest.approx(-0.05, rel=RTOL, abs=ATOL)
    assert cvar == pytest.approx(-0.0625, rel=RTOL, abs=ATOL)


@pytest.mark.parametrize(
    "function",
    [
        realized_volatility,
        annualized_volatility,
        sharpe_ratio,
        sortino_ratio,
        historical_var,
        historical_cvar,
    ],
)
def test_minimum_two_observations_is_enforced(function: object) -> None:
    assert_stable_error("input_too_short", function, [0.0])


def test_max_drawdown_accepts_its_exact_minimum() -> None:
    assert max_drawdown([100.0]) == 0.0


@pytest.mark.parametrize(
    "function",
    [
        realized_volatility,
        annualized_volatility,
        max_drawdown,
        sharpe_ratio,
        sortino_ratio,
        historical_var,
        historical_cvar,
    ],
)
def test_empty_risk_input_is_rejected(function: object) -> None:
    assert_stable_error("input_empty", function, [])


@pytest.mark.parametrize(
    "call",
    [
        partial(realized_volatility, np.zeros(100_001)),
        partial(annualized_volatility, np.zeros(100_001)),
        partial(max_drawdown, np.ones(100_001)),
        partial(sharpe_ratio, np.zeros(100_001)),
        partial(sortino_ratio, np.zeros(100_001)),
        partial(historical_var, np.zeros(100_001)),
        partial(historical_cvar, np.zeros(100_001)),
    ],
)
def test_risk_functions_reject_100001_values(call: object) -> None:
    assert_stable_error("input_too_long", call)


def test_risk_functions_accept_100000_values() -> None:
    returns = np.resize(np.array([-0.01, 0.02], dtype=np.float64), 100_000)
    equity = np.ones(100_000, dtype=np.float64)

    results = [
        realized_volatility(returns),
        annualized_volatility(returns),
        max_drawdown(equity),
        sharpe_ratio(returns),
        sortino_ratio(returns),
        historical_var(returns),
        historical_cvar(returns),
    ]
    assert all(type(result) is float and math.isfinite(result) for result in results)


@pytest.mark.parametrize(
    ("equity", "code"),
    [
        ([0.0], "equity_initial_non_positive"),
        ([-1.0], "equity_initial_non_positive"),
        ([0.0, -1.0], "equity_initial_non_positive"),
        ([100.0, -1.0], "equity_negative"),
    ],
)
def test_max_drawdown_domain_errors(equity: list[float], code: str) -> None:
    assert_stable_error(code, max_drawdown, equity)


def test_max_drawdown_allows_zero_after_a_positive_initial_value() -> None:
    assert max_drawdown([100.0, 0.0]) == -1.0
    assert max_drawdown([100.0, 0.0, 10.0]) == -1.0


@pytest.mark.parametrize(
    "periods_per_year",
    [True, np.int64(252), 252.0, IntegerSubclass(252), 0, -1],
)
@pytest.mark.parametrize(
    "function",
    [annualized_volatility, sharpe_ratio, sortino_ratio],
)
def test_risk_functions_reject_invalid_periods(
    function: object,
    periods_per_year: object,
) -> None:
    values = [-0.01, 0.02, 0.03]
    assert_stable_error(
        "periods_per_year_invalid",
        function,
        values,
        periods_per_year=periods_per_year,
    )


@pytest.mark.parametrize(
    "risk_free_rate",
    [
        True,
        np.float64(0.0),
        np.int64(0),
        FloatSubclass(0.0),
        Decimal("0"),
        math.nan,
        math.inf,
        -math.inf,
        10**400,
    ],
)
def test_sharpe_rejects_invalid_risk_free_rate(risk_free_rate: object) -> None:
    assert_stable_error(
        "risk_free_rate_invalid",
        sharpe_ratio,
        [-0.01, 0.02, 0.03],
        risk_free_rate=risk_free_rate,
    )


@pytest.mark.parametrize(
    "target_return",
    [
        True,
        np.float64(0.0),
        np.int64(0),
        FloatSubclass(0.0),
        Decimal("0"),
        math.nan,
        math.inf,
        -math.inf,
        10**400,
    ],
)
def test_sortino_rejects_invalid_target_return(target_return: object) -> None:
    assert_stable_error(
        "target_return_invalid",
        sortino_ratio,
        [-0.01, 0.02, 0.03],
        target_return=target_return,
    )


@pytest.mark.parametrize(
    "confidence",
    [
        True,
        np.float64(0.95),
        np.int64(0),
        FloatSubclass(0.95),
        Decimal("0.95"),
        math.nan,
        math.inf,
        -math.inf,
        0,
        1,
        -0.1,
        1.1,
        10**400,
    ],
)
@pytest.mark.parametrize("function", [historical_var, historical_cvar])
def test_tail_metrics_reject_invalid_confidence(
    function: object,
    confidence: object,
) -> None:
    assert_stable_error(
        "confidence_invalid",
        function,
        [-0.1, 0.1],
        confidence=confidence,
    )


def test_keyword_sub_precedence_follows_signature_order() -> None:
    assert_stable_error(
        "risk_free_rate_invalid",
        sharpe_ratio,
        [-0.01, 0.02],
        risk_free_rate=np.float64(0.0),
        periods_per_year=0,
    )
    assert_stable_error(
        "target_return_invalid",
        sortino_ratio,
        [-0.01, 0.02],
        target_return=np.float64(0.0),
        periods_per_year=0,
    )


def test_exact_builtin_integer_scalar_keywords_are_supported() -> None:
    returns = [-0.01, 0.02, 0.02]
    assert sharpe_ratio(returns, risk_free_rate=0, periods_per_year=1) == pytest.approx(
        1.0 / math.sqrt(3.0),
        rel=RTOL,
        abs=ATOL,
    )
    assert sortino_ratio(returns, target_return=0, periods_per_year=1) == pytest.approx(
        math.sqrt(3.0),
        rel=RTOL,
        abs=ATOL,
    )


def test_input_validation_precedes_keyword_validation() -> None:
    assert_stable_error(
        "input_non_finite",
        sharpe_ratio,
        [math.nan, 0.0],
        risk_free_rate=np.float64(0.0),
        periods_per_year=0,
    )


def test_ratio_zero_denominators_are_explicit() -> None:
    assert_stable_error("denominator_zero", sharpe_ratio, [0.1, 0.1])
    assert_stable_error("denominator_zero", sortino_ratio, [0.01, 0.02])
    assert_stable_error(
        "denominator_zero",
        sortino_ratio,
        [0.01, 0.01],
        target_return=0.01,
    )


STABLE_ERROR_CASES = (
    ("input_type_invalid", partial(simple_returns, 1.0)),
    ("input_shape_invalid", partial(simple_returns, np.ones((1, 2), dtype=np.bool_))),
    ("input_empty", partial(cumulative_return, [])),
    ("input_too_short", partial(simple_returns, [1.0])),
    ("input_too_long", partial(cumulative_return, np.zeros(100_001))),
    ("input_bool_invalid", partial(cumulative_return, [True])),
    ("input_complex_invalid", partial(cumulative_return, [1j])),
    ("input_non_finite", partial(simple_returns, [10**400, 1])),
    ("prices_non_positive", partial(simple_returns, [100.0, 0.0])),
    ("equity_initial_non_positive", partial(max_drawdown, [0.0])),
    ("equity_negative", partial(max_drawdown, [100.0, -1.0])),
    ("simple_return_below_minus_one", partial(cumulative_return, [-1.0001])),
    (
        "periods_per_year_invalid",
        partial(cagr, [1.0, 2.0], periods_per_year=True),
    ),
    (
        "risk_free_rate_invalid",
        partial(sharpe_ratio, [-0.01, 0.02], risk_free_rate=np.float64(0.0)),
    ),
    (
        "target_return_invalid",
        partial(sortino_ratio, [-0.01, 0.02], target_return=Decimal("0")),
    ),
    (
        "confidence_invalid",
        partial(historical_var, [-0.1, 0.1], confidence=1),
    ),
    ("denominator_zero", partial(sharpe_ratio, [0.0, 0.0])),
    (
        "tail_empty",
        partial(_threshold_tail_mean, np.array([0.0, 1.0]), -1.0),
    ),
    (
        "result_non_finite",
        partial(
            cumulative_return,
            [
                float(np.finfo(np.float64).max),
                float(np.finfo(np.float64).max),
            ],
        ),
    ),
)


@pytest.mark.parametrize(("code", "call"), STABLE_ERROR_CASES)
def test_all_nineteen_stable_error_codes_have_an_exact_reachable_case(
    code: str,
    call: object,
) -> None:
    assert_stable_error(code, call)


def test_stable_error_matrix_contains_exactly_nineteen_unique_codes() -> None:
    codes = tuple(code for code, _ in STABLE_ERROR_CASES)
    assert len(codes) == 19
    assert len(set(codes)) == 19


def test_private_threshold_tail_empty_defense_does_not_patch_quantile() -> None:
    values = np.array([0.0, 1.0], dtype=np.float64)
    assert_stable_error("tail_empty", _threshold_tail_mean, values, -1.0)


@pytest.mark.parametrize(
    "call",
    [
        partial(
            realized_volatility,
            [
                float(np.finfo(np.float64).max),
                -float(np.finfo(np.float64).max),
            ],
        ),
        partial(
            annualized_volatility,
            [
                float(np.finfo(np.float64).max),
                -float(np.finfo(np.float64).max),
            ],
        ),
        partial(
            sharpe_ratio,
            [
                float(np.finfo(np.float64).max),
                -float(np.finfo(np.float64).max),
            ],
        ),
        partial(
            sortino_ratio,
            [
                float(np.finfo(np.float64).max),
                -float(np.finfo(np.float64).max),
            ],
        ),
        partial(
            historical_var,
            [
                -float(np.finfo(np.float64).max),
                float(np.finfo(np.float64).max),
            ],
            confidence=0.5,
        ),
        partial(
            historical_cvar,
            [
                -float(np.finfo(np.float64).max),
                float(np.finfo(np.float64).max),
            ],
            confidence=0.5,
        ),
        partial(annualized_volatility, [-0.01, 0.02], periods_per_year=10**400),
        partial(sharpe_ratio, [-0.01, 0.02], periods_per_year=10**400),
        partial(sortino_ratio, [-0.01, 0.02], periods_per_year=10**400),
    ],
)
def test_risk_kernel_numeric_overflow_is_normalized(call: object) -> None:
    assert_stable_error("result_non_finite", call)


def test_named_private_kernel_non_finite_result_is_rechecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def non_finite_var_kernel(values: np.ndarray, confidence: float) -> float:
        assert values.shape == (2,)
        assert confidence == 0.5
        return math.inf

    monkeypatch.setattr(risk_module, "_historical_var_kernel", non_finite_var_kernel)
    assert_stable_error(
        "result_non_finite",
        historical_var,
        [-0.1, 0.1],
        confidence=0.5,
    )


def test_all_successful_risk_results_are_finite_python_float() -> None:
    returns = [-0.02, 0.01, 0.03, -0.01]
    results = [
        realized_volatility(returns),
        annualized_volatility(returns),
        max_drawdown([100.0, 120.0, 90.0, 130.0]),
        sharpe_ratio(returns),
        sortino_ratio(returns),
        historical_var(returns),
        historical_cvar(returns),
    ]
    assert all(type(result) is float and math.isfinite(result) for result in results)


def test_each_risk_public_function_validates_and_snapshots_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = risk_module._validate_numeric_input
    observed = {"calls": 0}

    def counting_validator(values: object, *, min_length: int) -> np.ndarray:
        observed["calls"] += 1
        return original(values, min_length=min_length)

    monkeypatch.setattr(risk_module, "_validate_numeric_input", counting_validator)
    calls = [
        partial(realized_volatility, [-0.01, 0.02]),
        partial(annualized_volatility, [-0.01, 0.02], periods_per_year=1),
        partial(max_drawdown, [100.0, 90.0]),
        partial(sharpe_ratio, [-0.01, 0.02], periods_per_year=1),
        partial(sortino_ratio, [-0.01, 0.02], periods_per_year=1),
        partial(historical_var, [-0.01, 0.02], confidence=0.8),
        partial(historical_cvar, [-0.01, 0.02], confidence=0.8),
    ]
    for expected_count, call in enumerate(calls, start=1):
        call()
        assert observed["calls"] == expected_count


def test_input_arrays_remain_byte_identical_for_every_risk_function() -> None:
    returns = np.array([-0.02, 0.01, 0.03, -0.01], dtype=np.float64)
    equity = np.array([100.0, 120.0, 90.0, 130.0], dtype=np.float64)
    returns_before = returns.tobytes()
    equity_before = equity.tobytes()

    realized_volatility(returns)
    annualized_volatility(returns)
    sharpe_ratio(returns)
    sortino_ratio(returns)
    historical_var(returns)
    historical_cvar(returns)
    max_drawdown(equity)

    assert returns.tobytes() == returns_before
    assert equity.tobytes() == equity_before


def test_repeated_risk_calls_are_exactly_deterministic() -> None:
    returns = np.array([-0.02, 0.01, 0.03, -0.01], dtype=np.float64)
    equity = np.array([100.0, 120.0, 90.0, 130.0], dtype=np.float64)
    calls = [
        partial(realized_volatility, returns),
        partial(annualized_volatility, returns),
        partial(max_drawdown, equity),
        partial(sharpe_ratio, returns),
        partial(sortino_ratio, returns),
        partial(historical_var, returns),
        partial(historical_cvar, returns),
    ]
    for call in calls:
        assert call() == call()


def test_drawdown_metamorphic_properties() -> None:
    equity = np.array([100.0, 120.0, 90.0, 130.0, 65.0])
    result = max_drawdown(equity)
    assert result == max_drawdown(equity * 9.5)
    assert -1.0 <= result <= 0.0
    assert max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_volatility_shift_invariance_and_positive_scale_equivariance() -> None:
    returns = np.array([-0.02, 0.01, 0.03, -0.01])
    base = realized_volatility(returns)
    assert realized_volatility(returns + 3.5) == pytest.approx(
        base,
        rel=RTOL,
        abs=ATOL,
    )
    assert realized_volatility(returns * 7.0) == pytest.approx(
        base * 7.0,
        rel=RTOL,
        abs=ATOL,
    )


def test_signed_tail_metamorphic_properties() -> None:
    returns = np.array([-0.10, -0.04, -0.01, 0.02, 0.08, 0.12])
    confidence = 0.75
    var = historical_var(returns, confidence=confidence)
    cvar = historical_cvar(returns, confidence=confidence)
    assert cvar <= var

    scale = 3.0
    shift = 0.25
    assert historical_var(returns * scale, confidence=confidence) == pytest.approx(
        var * scale,
        rel=RTOL,
        abs=ATOL,
    )
    assert historical_cvar(returns * scale, confidence=confidence) == pytest.approx(
        cvar * scale,
        rel=RTOL,
        abs=ATOL,
    )
    assert historical_var(returns + shift, confidence=confidence) == pytest.approx(
        var + shift,
        rel=RTOL,
        abs=ATOL,
    )
    assert historical_cvar(returns + shift, confidence=confidence) == pytest.approx(
        cvar + shift,
        rel=RTOL,
        abs=ATOL,
    )
    assert historical_var(returns, confidence=0.95) <= historical_var(
        returns,
        confidence=confidence,
    )


def test_zero_benchmark_ratios_are_positive_scale_invariant() -> None:
    returns = np.array([-0.01, 0.02, 0.02])
    scale = 8.0
    assert sharpe_ratio(returns * scale, periods_per_year=1) == pytest.approx(
        sharpe_ratio(returns, periods_per_year=1),
        rel=RTOL,
        abs=ATOL,
    )
    assert sortino_ratio(returns * scale, periods_per_year=1) == pytest.approx(
        sortino_ratio(returns, periods_per_year=1),
        rel=RTOL,
        abs=ATOL,
    )


def test_local_risk_numeric_policy_does_not_mutate_global_numpy_error_state() -> None:
    before = np.geterr()
    realized_volatility([-0.01, 0.02])
    assert_stable_error(
        "result_non_finite",
        historical_var,
        [
            -float(np.finfo(np.float64).max),
            float(np.finfo(np.float64).max),
        ],
        confidence=0.5,
    )
    assert np.geterr() == before


def test_calculation_core_ast_has_no_forbidden_stateful_constructs() -> None:
    module_paths = [
        Path("app/financial_engineering/__init__.py"),
        Path("app/financial_engineering/_validation.py"),
        Path("app/financial_engineering/returns.py"),
        Path("app/financial_engineering/risk_metrics.py"),
    ]
    prohibited_nodes = (
        ast.Lambda,
        ast.GeneratorExp,
        ast.Yield,
        ast.YieldFrom,
        ast.AugAssign,
    )
    prohibited_import_roots = {
        "httpx",
        "logging",
        "os",
        "pathlib",
        "random",
        "requests",
        "socket",
        "time",
    }

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            assert not isinstance(node, prohibited_nodes), (
                f"{module_path}: forbidden AST node {type(node).__name__}"
            )
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", maxsplit=1)[0] for alias in node.names}
                assert not roots & prohibited_import_roots
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module.split(".", maxsplit=1)[0] not in prohibited_import_roots
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "np"
                ):
                    assert node.func.attr != "seterr"
                keyword_names = {keyword.arg for keyword in node.keywords}
                assert "out" not in keyword_names
                assert "overwrite_input" not in keyword_names
