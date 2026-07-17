"""NumPy/JAX public wrapper가 공유하는 host-side validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import NoReturn, TypeGuard, cast

import numpy as np
from numpy.typing import NDArray

from .errors import ResearchValidationError
from .models import EffectiveTrialProvenance, TransitionCounts

type FloatArray = NDArray[np.float64]
type IntArray = NDArray[np.int64]
type RealScalar = int | float | np.integer | np.floating

_REAL_SCALAR_TYPES = (int, float, np.integer, np.floating)
_INTEGER_SCALAR_TYPES = (int, np.integer)
_MAX_FLOAT64_INTEGER_MAGNITUDE = int(np.finfo(np.float64).max)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_METHODS = {
    "pre_registered_independent",
    "externally_estimated_effective_count",
}


@dataclass(frozen=True, slots=True)
class BacktestInputs:
    """검증이 끝난 loss/VaR와 strict exception sequence를 전달한다."""

    realized_losses: FloatArray
    forecast_vars: FloatArray
    exceptions: IntArray


def _raise(code: str) -> NoReturn:
    raise ResearchValidationError(code)


def _is_supported_real_scalar(value: object) -> TypeGuard[RealScalar]:
    return not isinstance(value, (bool, np.bool_)) and isinstance(value, _REAL_SCALAR_TYPES)


def validate_real_scalar(value: object, *, code: str = "research_input_invalid") -> float:
    """bool/complex를 제외한 finite real scalar를 Python float로 고정한다."""

    if not _is_supported_real_scalar(value):
        _raise(code)
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        _raise(code)
    if not math.isfinite(result):
        _raise(code)
    return result


def validate_integer_scalar(value: object, *, code: str) -> int:
    """bool을 integer로 오인하지 않고 exact integer만 허용한다."""

    if isinstance(value, (bool, np.bool_)) or not isinstance(value, _INTEGER_SCALAR_TYPES):
        _raise(code)
    return int(value)


def validate_confidence(value: object) -> float:
    """Confidence는 finite real이며 open unit interval이어야 한다."""

    confidence = validate_real_scalar(value)
    if not 0.0 < confidence < 1.0:
        _raise("research_input_invalid")
    return confidence


def validate_significance(value: object) -> float:
    """Significance의 type, finite, domain 위반을 단일 stable code로 매핑한다."""

    significance = validate_real_scalar(value, code="significance_invalid")
    if not 0.0 < significance < 1.0:
        _raise("significance_invalid")
    return significance


def validate_sample_size(value: object) -> int:
    """PSR/DSR sample size의 type과 최소 길이 오류를 구분한다."""

    sample_size = validate_integer_scalar(value, code="research_input_invalid")
    if sample_size <= 1:
        _raise("research_input_too_short")
    if sample_size > _MAX_FLOAT64_INTEGER_MAGNITUDE:
        _raise("research_input_invalid")
    return sample_size


def validate_trial_count(value: object) -> int:
    """DSR trial count는 반올림하지 않은 integer N>=2여야 한다."""

    trial_count = validate_integer_scalar(value, code="trial_count_invalid")
    if trial_count < 2:
        _raise("trial_count_invalid")
    if trial_count > _MAX_FLOAT64_INTEGER_MAGNITUDE:
        _raise("trial_count_invalid")
    return trial_count


def _coerce_sequence(
    values: object,
    *,
    dimension_code: str = "research_input_invalid",
) -> FloatArray:
    """지원 container를 복사된 finite float64 1-D array로 변환한다."""

    if isinstance(values, np.ndarray):
        if values.ndim != 1:
            _raise(dimension_code)
        if values.dtype.kind not in "iuf":
            _raise("research_input_invalid")
        try:
            result = np.array(values, dtype=np.float64, copy=True)
        except (OverflowError, TypeError, ValueError):
            _raise("research_input_invalid")
    elif type(values) in (list, tuple):
        sequence = cast(list[object] | tuple[object, ...], values)
        try:
            shape_probe = np.asarray(sequence)
        except (OverflowError, TypeError, ValueError):
            _raise(dimension_code)
        if shape_probe.ndim != 1:
            _raise(dimension_code)
        if any(not _is_supported_real_scalar(value) for value in sequence):
            _raise("research_input_invalid")
        try:
            result = np.array(sequence, dtype=np.float64, copy=True)
        except (OverflowError, TypeError, ValueError):
            _raise("research_input_invalid")
        if result.ndim != 1:
            _raise(dimension_code)
    else:
        _raise("research_input_invalid")

    if not bool(np.all(np.isfinite(result))):
        _raise("research_input_invalid")
    return result


def validate_sequence(values: object, *, minimum_length: int = 1) -> FloatArray:
    """단일-sequence validation precedence를 적용한다."""

    result = _coerce_sequence(values)
    if result.size < minimum_length:
        _raise("research_input_too_short")
    return result


def validate_backtest_sequences(
    realized_losses: object,
    forecast_vars: object,
    *,
    minimum_length: int,
) -> BacktestInputs:
    """VaR 입력을 realized→forecast→shape→length 순서로 검증한다."""

    realized = _coerce_sequence(realized_losses)
    forecast = _coerce_sequence(forecast_vars, dimension_code="forecast_shape_invalid")
    if realized.shape != forecast.shape:
        _raise("forecast_shape_invalid")
    if realized.size < minimum_length:
        _raise("research_input_too_short")
    if bool(np.any(forecast < 0.0)):
        _raise("forecast_var_negative")
    exceptions = np.asarray(realized > forecast, dtype=np.int64)
    return BacktestInputs(realized, forecast, exceptions)


def validate_moment_pair(skewness: float, kurtosis: float) -> None:
    """Pearson moment inequality를 float64 roundoff tolerance와 함께 검사한다."""

    lower_bound = skewness * skewness + 1.0
    tolerance = (
        64.0
        * np.finfo(np.float64).eps
        * max(1.0, abs(kurtosis), abs(lower_bound))
    )
    if not math.isfinite(lower_bound) or kurtosis + tolerance < lower_bound:
        _raise("moment_invalid")


def validate_trial_provenance(
    provenance: object,
    *,
    trial_count: int,
) -> EffectiveTrialProvenance:
    """DSR effective-N provenance의 schema/count/frequency/digest를 원자 검증한다."""

    if not isinstance(provenance, EffectiveTrialProvenance):
        _raise("trial_provenance_invalid")
    if (
        not isinstance(provenance.schema_version, str)
        or provenance.schema_version != "s1.4r-effective-trials-v1"
    ):
        _raise("trial_provenance_invalid")
    if (
        not isinstance(provenance.method, str)
        or provenance.method not in _PROVENANCE_METHODS
    ):
        _raise("trial_provenance_invalid")
    try:
        raw_count = validate_integer_scalar(
            provenance.raw_trial_count,
            code="trial_provenance_invalid",
        )
        effective_count = validate_integer_scalar(
            provenance.effective_trial_count,
            code="trial_provenance_invalid",
        )
    except ResearchValidationError:
        _raise("trial_provenance_invalid")
    if not raw_count >= effective_count >= 2:
        _raise("trial_provenance_invalid")
    if effective_count != trial_count:
        _raise("trial_provenance_invalid")
    if not isinstance(provenance.sampling_frequency, str):
        _raise("trial_provenance_invalid")
    if not provenance.sampling_frequency.strip():
        _raise("trial_provenance_invalid")
    if not isinstance(provenance.trial_registry_sha256, str):
        _raise("trial_provenance_invalid")
    if _SHA256_PATTERN.fullmatch(provenance.trial_registry_sha256) is None:
        _raise("trial_provenance_invalid")
    variance_ddof = validate_integer_scalar(
        provenance.variance_ddof,
        code="trial_provenance_invalid",
    )
    if variance_ddof != 1:
        _raise("trial_provenance_invalid")
    return provenance


def transition_counts(exceptions: IntArray) -> TransitionCounts:
    """고정 길이 slice와 reduction으로 2-state transition counts를 계산한다."""

    previous = exceptions[:-1]
    current = exceptions[1:]
    return TransitionCounts(
        n00=int(np.sum((previous == 0) & (current == 0), dtype=np.int64)),
        n01=int(np.sum((previous == 0) & (current == 1), dtype=np.int64)),
        n10=int(np.sum((previous == 1) & (current == 0), dtype=np.int64)),
        n11=int(np.sum((previous == 1) & (current == 1), dtype=np.int64)),
    )


def validate_transition_identifiability(counts: TransitionCounts) -> None:
    """두 Markov row가 모두 관측되지 않으면 LRind를 산출하지 않는다."""

    if counts.n00 + counts.n01 == 0 or counts.n10 + counts.n11 == 0:
        _raise("insufficient_sample")
