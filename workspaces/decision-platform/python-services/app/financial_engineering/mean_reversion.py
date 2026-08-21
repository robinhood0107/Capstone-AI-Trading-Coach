from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import cast

import numpy as np
from statsmodels.tsa.stattools import adfuller  # type: ignore[import-untyped]

from app.financial_engineering._validation import _raise_stable, _validate_numeric_input

WINDOW_OBSERVATIONS = 60
MAX_CLOSE_ROWS = 20_000


@dataclass(frozen=True)
class ADFReference:
    statistic: float
    p_value: float
    critical_values: tuple[tuple[str, float], ...]
    nobs: int
    regression: str = "c"
    autolag: str = "AIC"
    authority: str = "REFERENCE_ONLY"


@dataclass(frozen=True)
class MeanReversionReport:
    availability: str
    phi: float | None
    intercept: float | None
    theta: float | None
    long_run_mean: float | None
    half_life_sessions: float | None
    z_score: float | None
    classification: str
    warning_candidate: str | None
    adf: ADFReference | None
    decision_authority: str = "WARN_CANDIDATE_ONLY"
    window_observations: int = WINDOW_OBSERVATIONS


def _abstain() -> MeanReversionReport:
    return MeanReversionReport(
        availability="ABSTAIN",
        phi=None,
        intercept=None,
        theta=None,
        long_run_mean=None,
        half_life_sessions=None,
        z_score=None,
        classification="NOT_ESTIMABLE",
        warning_candidate=None,
        adf=None,
    )


def diagnose_mean_reversion(closes: object) -> MeanReversionReport:
    """마지막 60개 positive close의 log-level AR(1)과 exact OU mapping을 진단한다.

    ADF와 z-score는 설명/WARN 후보일 뿐 trading 또는 hard risk gate가 아니다.
    """
    values = _validate_numeric_input(closes, min_length=WINDOW_OBSERVATIONS)
    if values.size > MAX_CLOSE_ROWS:
        _raise_stable("mean_reversion_input_too_long")
    if bool(np.any(values <= 0.0)):
        _raise_stable("prices_non_positive")
    window = np.log(values[-WINDOW_OBSERVATIONS:])
    sample_std = float(np.std(window, ddof=1, dtype=np.float64))
    if not math.isfinite(sample_std) or sample_std == 0.0:
        return _abstain()
    dependent = window[1:]
    design = np.column_stack((np.ones(WINDOW_OBSERVATIONS - 1), window[:-1]))
    coefficients, _, rank, _ = np.linalg.lstsq(design, dependent, rcond=None)
    if rank != 2 or not bool(np.all(np.isfinite(coefficients))):
        return _abstain()
    intercept = float(coefficients[0])
    phi = float(coefficients[1])
    z_score = float((window[-1] - float(np.mean(window, dtype=np.float64))) / sample_std)
    if z_score > 2.0:
        warning = "ABOVE_TWO_SIGMA"
    elif z_score < -2.0:
        warning = "BELOW_MINUS_TWO_SIGMA"
    else:
        warning = None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            adf_raw = adfuller(window, regression="c", autolag="AIC")
        critical = cast(dict[str, float], adf_raw[4])
        if not all(
            math.isfinite(float(value))
            for value in (adf_raw[0], adf_raw[1], *critical.values())
        ):
            raise ValueError("adf_non_finite")
        adf = ADFReference(
            statistic=float(adf_raw[0]),
            p_value=float(adf_raw[1]),
            critical_values=tuple(
                sorted((str(key), float(value)) for key, value in critical.items())
            ),
            nobs=int(adf_raw[3]),
        )
    except (ValueError, np.linalg.LinAlgError):
        adf = None

    if not 0.0 < phi < 1.0:
        return MeanReversionReport(
            availability="AVAILABLE",
            phi=phi,
            intercept=intercept,
            theta=None,
            long_run_mean=None,
            half_life_sessions=None,
            z_score=z_score,
            classification="NOT_MEAN_REVERTING",
            warning_candidate=warning,
            adf=adf,
        )
    theta = -math.log(phi)
    long_run_mean = intercept / (1.0 - phi)
    half_life = math.log(2.0) / theta
    if not all(math.isfinite(value) for value in (theta, long_run_mean, half_life, z_score)):
        return _abstain()
    return MeanReversionReport(
        availability="AVAILABLE",
        phi=phi,
        intercept=intercept,
        theta=theta,
        long_run_mean=long_run_mean,
        half_life_sessions=half_life,
        z_score=z_score,
        classification="MEAN_REVERTING",
        warning_candidate=warning,
        adf=adf,
    )


def rolling_mean_reversion(closes: object) -> tuple[MeanReversionReport, ...]:
    """각 prefix의 current observation을 포함한 60-window causal 진단을 반환한다."""
    values = _validate_numeric_input(closes, min_length=WINDOW_OBSERVATIONS)
    if values.size > MAX_CLOSE_ROWS:
        _raise_stable("mean_reversion_input_too_long")
    return tuple(
        diagnose_mean_reversion(values[index - WINDOW_OBSERVATIONS + 1 : index + 1])
        for index in range(WINDOW_OBSERVATIONS - 1, values.size)
    )
