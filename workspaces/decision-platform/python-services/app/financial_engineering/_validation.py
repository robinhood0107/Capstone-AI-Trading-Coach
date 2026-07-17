from __future__ import annotations

import math
from collections.abc import Collection
from typing import Any, NoReturn, TypeAlias, cast

import numpy as np
import numpy.typing as npt

NumericInput: TypeAlias = list[int | float] | tuple[int | float, ...] | npt.NDArray[np.number[Any]]
FloatArray: TypeAlias = npt.NDArray[np.float64]

_MAX_INPUT_LENGTH = 100_000


def _raise_stable(code: str) -> NoReturn:
    raise ValueError(code) from None


def _is_nested_container(value: object) -> bool:
    if isinstance(value, np.ndarray):
        return True
    return isinstance(value, Collection) and not isinstance(value, (str, bytes))


def _is_numpy_complex_scalar(value: object) -> bool:
    return isinstance(value, np.generic) and value.dtype.kind == "c"


def _validate_sequence_elements(values: list[object] | tuple[object, ...]) -> None:
    # 오류 우선순위는 원소 순서와 무관해야 하므로 각 범주를 전체 순회한다.
    for value in values:
        if _is_nested_container(value):
            _raise_stable("input_shape_invalid")

    for value in values:
        if type(value) is bool or type(value) is np.bool_:
            _raise_stable("input_bool_invalid")

    for value in values:
        if type(value) is complex or _is_numpy_complex_scalar(value):
            _raise_stable("input_complex_invalid")

    for value in values:
        if type(value) is not int and type(value) is not float:
            _raise_stable("input_type_invalid")


def _validate_ndarray(values: npt.NDArray[Any]) -> None:
    if values.ndim != 1:
        _raise_stable("input_shape_invalid")
    if values.dtype.kind == "b":
        _raise_stable("input_bool_invalid")
    if values.dtype.kind == "c":
        _raise_stable("input_complex_invalid")
    if values.dtype.kind not in ("i", "u", "f"):
        _raise_stable("input_type_invalid")


def _validate_numeric_input(values: object, *, min_length: int) -> FloatArray:
    """허용된 1차원 숫자 입력을 검증하고 격리된 float64 snapshot을 반환한다."""
    if type(values) is bool or type(values) is np.bool_:
        _raise_stable("input_bool_invalid")

    if type(values) is np.ndarray:
        ndarray_values = cast(npt.NDArray[Any], values)
        _validate_ndarray(ndarray_values)
        input_length = len(ndarray_values)
    elif type(values) is list or type(values) is tuple:
        sequence_values = cast(list[object] | tuple[object, ...], values)
        _validate_sequence_elements(sequence_values)
        input_length = len(sequence_values)
    else:
        _raise_stable("input_type_invalid")

    if input_length == 0:
        _raise_stable("input_empty")
    if input_length > _MAX_INPUT_LENGTH:
        _raise_stable("input_too_long")
    if input_length < min_length:
        _raise_stable("input_too_short")

    try:
        with np.errstate(over="ignore", divide="ignore", invalid="ignore", under="ignore"):
            snapshot = np.array(values, dtype=np.float64, copy=True)
    except OverflowError:
        _raise_stable("input_non_finite")

    if not bool(np.all(np.isfinite(snapshot))):
        _raise_stable("input_non_finite")
    return snapshot


def _validate_periods_per_year(value: object) -> int:
    if type(value) is not int:
        _raise_stable("periods_per_year_invalid")
    if value <= 0:
        _raise_stable("periods_per_year_invalid")
    return value


def _validate_finite_scalar(value: object, *, code: str) -> float:
    if type(value) is not int and type(value) is not float:
        _raise_stable(code)
    try:
        result = float(value)
    except OverflowError:
        _raise_stable(code)
    if not math.isfinite(result):
        _raise_stable(code)
    return result


def _validate_confidence(value: object) -> float:
    confidence = _validate_finite_scalar(value, code="confidence_invalid")
    if confidence <= 0.0 or confidence >= 1.0:
        _raise_stable("confidence_invalid")
    return confidence


def _ensure_finite_scalar(value: float | np.floating[Any]) -> float:
    result = float(value)
    if not math.isfinite(result):
        _raise_stable("result_non_finite")
    return result


def _ensure_finite_array(values: FloatArray) -> FloatArray:
    if not bool(np.all(np.isfinite(values))):
        _raise_stable("result_non_finite")
    return values
