from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from s1_4r_risk_research.errors import ResearchValidationError
from s1_4r_risk_research.models import EffectiveTrialProvenance
from s1_4r_risk_research.numpy_reference import (
    deflated_sharpe_ratio,
    historical_expected_shortfall,
    lo_adjusted_sharpe_ratio,
    probabilistic_sharpe_ratio,
    realized_variance,
    realized_volatility_intraday,
)

FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures/canonical/advanced_risk_v1.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["historicalExpectedShortfall"], ids=lambda x: x["id"])
def test_historical_es_hand_calculated_cases(case: dict[str, object]) -> None:
    result = historical_expected_shortfall(case["losses"], confidence=case["confidence"])
    assert isinstance(result, float)
    assert result == pytest.approx(case["expected"], rel=0.0, abs=1e-15)


@pytest.mark.parametrize(
    ("losses", "confidence", "expected"),
    [
        ([1e308, 1e308, 0.0, 0.0], 0.5, 1e308),
        ([1e-308], np.nextafter(1.0, 0.0), 1e-308),
        ([np.finfo(np.float64).max] * 8, 0.01, np.finfo(np.float64).max),
        ([np.finfo(np.float64).max] * 20, 0.5, np.finfo(np.float64).max),
    ],
    ids=[
        "representable-sum-after-normalization",
        "subnormal-worst-observation",
        "max-constant-fractional-tail",
        "max-constant-half-tail",
    ],
)
def test_historical_es_normalizes_tail_weights_before_multiplication(
    losses: list[float],
    confidence: float,
    expected: float,
) -> None:
    assert historical_expected_shortfall(losses, confidence=confidence) == expected


@pytest.mark.parametrize("case", FIXTURE["realized"], ids=lambda x: x["id"])
def test_realized_variance_and_volatility_hand_cases(case: dict[str, object]) -> None:
    variance = realized_variance(case["intradayLogReturns"])
    volatility = realized_volatility_intraday(case["intradayLogReturns"])
    assert variance == pytest.approx(case["variance"], rel=0.0, abs=1e-15)
    assert volatility == pytest.approx(case["volatility"], rel=0.0, abs=1e-15)
    assert isinstance(variance, float)
    assert isinstance(volatility, float)


def test_lo_adjusted_sharpe_hand_case() -> None:
    case = FIXTURE["loAdjustedSharpe"]
    result = lo_adjusted_sharpe_ratio(
        case["returns"],
        aggregation_periods=case["aggregationPeriods"],
        risk_free_rate=case["riskFreeRate"],
    )
    assert result == pytest.approx(case["expected"], rel=0.0, abs=1e-15)


def test_probabilistic_sharpe_paper_fixture_and_equal_benchmark() -> None:
    case = FIXTURE["probabilisticSharpe"]
    result = probabilistic_sharpe_ratio(
        case["observedSharpe"],
        benchmark_sharpe=case["benchmarkSharpe"],
        sample_size=case["sampleSize"],
        skewness=case["skewness"],
        kurtosis=case["pearsonKurtosis"],
    )
    equal = probabilistic_sharpe_ratio(
        case["observedSharpe"],
        benchmark_sharpe=case["observedSharpe"],
        sample_size=case["sampleSize"],
        skewness=case["skewness"],
        kurtosis=case["pearsonKurtosis"],
    )
    assert result == pytest.approx(case["expected"], rel=0.0, abs=1e-15)
    assert equal == 0.5


def _provenance() -> EffectiveTrialProvenance:
    return EffectiveTrialProvenance(**FIXTURE["deflatedSharpe"]["trialProvenance"])


def test_deflated_sharpe_paper_fixture_and_equal_benchmark() -> None:
    case = FIXTURE["deflatedSharpe"]
    kwargs = {
        "sample_size": case["sampleSize"],
        "skewness": case["skewness"],
        "kurtosis": case["pearsonKurtosis"],
        "trial_count": case["trialCount"],
        "sharpe_estimate_variance": case["sharpeEstimateVariance"],
        "trial_provenance": _provenance(),
    }
    result = deflated_sharpe_ratio(case["selectedSharpe"], **kwargs)
    equal = deflated_sharpe_ratio(case["expectedBenchmarkSharpe"], **kwargs)
    assert result == pytest.approx(case["expected"], rel=0.0, abs=1e-15)
    assert equal == pytest.approx(case["equalBenchmarkExpected"], rel=0.0, abs=1e-15)


def test_dsr_rejects_missing_or_mismatched_provenance() -> None:
    case = FIXTURE["deflatedSharpe"]
    base = {
        "sample_size": case["sampleSize"],
        "skewness": case["skewness"],
        "kurtosis": case["pearsonKurtosis"],
        "trial_count": case["trialCount"],
        "sharpe_estimate_variance": case["sharpeEstimateVariance"],
    }
    with pytest.raises(ResearchValidationError, match=r"^trial_provenance_invalid$"):
        deflated_sharpe_ratio(case["selectedSharpe"], **base, trial_provenance=None)

    mismatched = EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="pre_registered_independent",
        raw_trial_count=3,
        effective_trial_count=3,
        sampling_frequency="daily",
        trial_registry_sha256="b" * 64,
        variance_ddof=1,
    )
    with pytest.raises(ResearchValidationError, match=r"^trial_provenance_invalid$"):
        deflated_sharpe_ratio(case["selectedSharpe"], **base, trial_provenance=mismatched)


@pytest.mark.parametrize(
    "provenance",
    [
        EffectiveTrialProvenance(
            schema_version="s1.4r-effective-trials-v1",
            method=[],  # type: ignore[arg-type]
            raw_trial_count=2,
            effective_trial_count=2,
            sampling_frequency="daily",
            trial_registry_sha256="a" * 64,
            variance_ddof=1,
        ),
        EffectiveTrialProvenance(
            schema_version="s1.4r-effective-trials-v1",
            method="pre_registered_independent",
            raw_trial_count=2,
            effective_trial_count=2,
            sampling_frequency="daily",
            trial_registry_sha256="a" * 64,
            variance_ddof=1.0,  # type: ignore[arg-type]
        ),
        EffectiveTrialProvenance(
            schema_version="s1.4r-effective-trials-v1",
            method="pre_registered_independent",
            raw_trial_count=2,
            effective_trial_count=2,
            sampling_frequency="daily",
            trial_registry_sha256="a" * 64,
            variance_ddof=1 + 0j,  # type: ignore[arg-type]
        ),
    ],
    ids=["unhashable-method", "float-ddof", "complex-ddof"],
)
def test_dsr_malformed_provenance_fields_use_stable_error(
    provenance: EffectiveTrialProvenance,
) -> None:
    case = FIXTURE["deflatedSharpe"]
    with pytest.raises(ResearchValidationError, match=r"^trial_provenance_invalid$"):
        deflated_sharpe_ratio(
            case["selectedSharpe"],
            sample_size=case["sampleSize"],
            skewness=case["skewness"],
            kurtosis=case["pearsonKurtosis"],
            trial_count=case["trialCount"],
            sharpe_estimate_variance=case["sharpeEstimateVariance"],
            trial_provenance=provenance,
        )


def test_dsr_uses_lower_tail_inverse_for_large_representable_trial_count() -> None:
    trial_count = 10**20
    provenance = EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="externally_estimated_effective_count",
        raw_trial_count=trial_count,
        effective_trial_count=trial_count,
        sampling_frequency="daily",
        trial_registry_sha256="d" * 64,
        variance_ddof=1,
    )
    result = deflated_sharpe_ratio(
        10.0,
        sample_size=100,
        skewness=0.0,
        kurtosis=3.0,
        trial_count=trial_count,
        sharpe_estimate_variance=1.0,
        trial_provenance=provenance,
    )
    assert math.isfinite(result)
    assert 0.0 <= result <= 1.0


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda: historical_expected_shortfall([], confidence=0.95), "research_input_too_short"),
        (lambda: historical_expected_shortfall([1.0, math.inf]), "research_input_invalid"),
        (
            lambda: lo_adjusted_sharpe_ratio([1.0, 2.0], aggregation_periods=True),
            "aggregation_periods_invalid",
        ),
        (
            lambda: lo_adjusted_sharpe_ratio([1.0], aggregation_periods=True),
            "research_input_too_short",
        ),
        (
            lambda: lo_adjusted_sharpe_ratio(
                [1.0, 2.0, 3.0],
                aggregation_periods=0,
                risk_free_rate="bad",
            ),
            "research_input_invalid",
        ),
        (
            lambda: lo_adjusted_sharpe_ratio([1.0, 1.0, 1.0], aggregation_periods=1),
            "moment_invalid",
        ),
        (
            lambda: probabilistic_sharpe_ratio(
                1.0, benchmark_sharpe=0.0, sample_size=1, skewness=0.0, kurtosis=3.0
            ),
            "research_input_too_short",
        ),
        (
            lambda: probabilistic_sharpe_ratio(
                1.0, benchmark_sharpe=0.0, sample_size=6, skewness=2.0, kurtosis=3.0
            ),
            "moment_invalid",
        ),
        (
            lambda: probabilistic_sharpe_ratio(
                1.0,
                benchmark_sharpe=0.0,
                sample_size=10**400,
                skewness=0.0,
                kurtosis=3.0,
            ),
            "research_input_invalid",
        ),
        (
            lambda: deflated_sharpe_ratio(
                1.0,
                sample_size=6,
                skewness=0.0,
                kurtosis=3.0,
                trial_count=10**400,
                sharpe_estimate_variance=1.0,
                trial_provenance=_provenance(),
            ),
            "trial_count_invalid",
        ),
    ],
)
def test_reference_validation_codes(call: object, code: str) -> None:
    with pytest.raises(ResearchValidationError, match=f"^{code}$"):
        call()


def test_numpy_reference_does_not_mutate_or_alias_inputs() -> None:
    values = np.array([1.0, 4.0, 2.0, 3.0], dtype=np.float64)
    before = values.copy()
    result = historical_expected_shortfall(values, confidence=0.625)
    assert np.array_equal(values, before)
    assert isinstance(result, float)
