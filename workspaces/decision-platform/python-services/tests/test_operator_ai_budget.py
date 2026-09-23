from __future__ import annotations

import pytest

from app.operator_ai_budget import (
    OperatorAiBudgetConfigurationError,
    deployment_hard_cap_microusd,
)


def test_local_mode_does_not_install_a_public_budget_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "LOCAL")
    monkeypatch.delenv("MARS_AI_DAILY_HARD_CAP_USD", raising=False)
    assert deployment_hard_cap_microusd() is None


@pytest.mark.parametrize("product_mode", ["FULL", "DEMO"])
def test_public_mode_requires_exact_positive_dollar_ceiling(
    monkeypatch: pytest.MonkeyPatch, product_mode: str
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", product_mode)
    monkeypatch.setenv("MARS_AI_DAILY_HARD_CAP_USD", "1.00")
    assert deployment_hard_cap_microusd() == 1_000_000
    monkeypatch.setenv("MARS_AI_DAILY_HARD_CAP_USD", "0")
    with pytest.raises(OperatorAiBudgetConfigurationError):
        deployment_hard_cap_microusd()
    monkeypatch.delenv("MARS_AI_DAILY_HARD_CAP_USD")
    with pytest.raises(OperatorAiBudgetConfigurationError):
        deployment_hard_cap_microusd()


def test_unknown_product_mode_cannot_disable_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "UNKNOWN")
    with pytest.raises(OperatorAiBudgetConfigurationError, match="MARS_PUBLIC_SURFACE_MODE"):
        deployment_hard_cap_microusd()
