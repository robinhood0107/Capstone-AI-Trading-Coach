"""S1.4R 연구 전용 stable validation errors."""

from __future__ import annotations

RESEARCH_ERROR_CODES = frozenset(
    {
        "research_input_invalid",
        "research_input_too_short",
        "aggregation_periods_invalid",
        "moment_invalid",
        "trial_count_invalid",
        "trial_variance_invalid",
        "trial_provenance_invalid",
        "significance_invalid",
        "forecast_shape_invalid",
        "forecast_var_negative",
        "insufficient_sample",
        "likelihood_invalid",
        "research_result_non_finite",
    }
)


class ResearchValidationError(ValueError):
    """연구 입력/수치 계약 위반을 stable code 하나로 전달한다."""

    code: str

    def __init__(self, code: str) -> None:
        if code not in RESEARCH_ERROR_CODES:
            raise ValueError(f"unknown research validation code: {code}")
        self.code = code
        super().__init__(code)
