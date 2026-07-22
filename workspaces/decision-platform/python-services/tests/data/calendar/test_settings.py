from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data.calendar.settings import OpenDARTQuotaConfig, OpenDARTQuotaSettings


REQUIRED_ENV = {
    "OPENDART_DAILY_CALL_LIMIT": "20000",
    "OPENDART_DAILY_CALL_BUDGET": "17500",
    "OPENDART_MAX_CALLS_PER_RUN": "8000",
    "OPENDART_MAX_SYMBOLS_PER_RUN": "100",
}


def test_online_quota_settings_have_no_code_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        OpenDARTQuotaSettings()


@pytest.mark.parametrize("missing", list(REQUIRED_ENV))
def test_any_missing_online_quota_setting_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    for name, value in REQUIRED_ENV.items():
        if name == missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        OpenDARTQuotaSettings()


@pytest.mark.parametrize(
    "limit, budget, per_run, symbols, expected",
    [
        (20_000, 17_501, 8_000, 100, "budget"),
        (10_000, 8_751, 8_000, 100, "budget"),
        (20_000, 17_500, 8_001, 100, "per-run"),
        (1_000, 875, 876, 100, "per-run"),
        (20_000, 17_500, 8_000, 0, "symbols"),
    ],
)
def test_quota_config_rejects_values_above_project_safety_caps(
    limit: int,
    budget: int,
    per_run: int,
    symbols: int,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        OpenDARTQuotaConfig(
            daily_call_limit=limit,
            daily_call_budget=budget,
            max_calls_per_run=per_run,
            max_symbols_per_run=symbols,
        )


def test_valid_online_settings_convert_to_immutable_quota_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)

    config = OpenDARTQuotaSettings().to_config()
    assert config == OpenDARTQuotaConfig(
        daily_call_limit=20_000,
        daily_call_budget=17_500,
        max_calls_per_run=8_000,
        max_symbols_per_run=100,
    )
