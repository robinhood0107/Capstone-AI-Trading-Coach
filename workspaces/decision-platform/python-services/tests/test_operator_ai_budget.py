from __future__ import annotations

import pytest
import psycopg

from app.operator_ai_budget import (
    OperatorAiBudgetConfigurationError,
    OperatorAiBudgetReservationError,
    TradeAiGrossBudget,
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


def test_trade_budget_requires_the_automation_role_and_operator_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    monkeypatch.setenv("MARS_AI_DAILY_HARD_CAP_USD", "1.00")
    monkeypatch.setenv(
        "P1_AUTOMATION_DATABASE_DSN",
        "postgresql://decision_automation_runtime:fixture@localhost:5432/decision",
    )
    monkeypatch.setenv("P1_VERTEX_INPUT_MICROUSD_PER_TOKEN", "3")
    monkeypatch.setenv("P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN", "17")
    budget = TradeAiGrossBudget.from_environment()
    assert budget is not None and budget.hard_cap_microusd == 1_000_000
    monkeypatch.setenv("P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN", "9")
    with pytest.raises(OperatorAiBudgetConfigurationError, match="rate floor"):
        TradeAiGrossBudget.from_environment()
    monkeypatch.setenv("P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN", "17")
    monkeypatch.setenv(
        "P1_AUTOMATION_DATABASE_DSN", "postgresql://decision_app:fixture@localhost:5432/decision"
    )
    with pytest.raises(OperatorAiBudgetConfigurationError, match="role"):
        TradeAiGrossBudget.from_environment()


def test_trade_and_rag_owners_share_one_database_cost_ceiling(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    # This fixture applies migrations with SET ROLE flyway, leaving session_user as
    # its test superuser; V203's real flyway-login seed therefore needs this setup.
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            "UPDATE operator_ai_budget_policy SET daily_soft_cap_microusd = 1000000 WHERE singleton"
        )
    budget = TradeAiGrossBudget(
        database_dsn=isolated_postgres_cluster["automation_runtime_dsn"],
        hard_cap_microusd=30_000,
        input_microusd_per_token=10,
        output_microusd_per_token=10,
    )
    budget.reserve(
        owner_user_id="usr_alice",
        run_id="run_alice",
        payload_bytes=b'{"candidate":1}',
        output_token_cap=512,
    )
    with pytest.raises(OperatorAiBudgetReservationError, match="EXHAUSTED"):
        budget.reserve(
            owner_user_id="usr_bob1",
            run_id="run_bob1",
            payload_bytes=b'{"candidate":2}',
            output_token_cap=512,
        )
    with psycopg.connect(isolated_postgres_cluster["app_dsn"]) as connection:
        assert connection.execute(
            "SELECT public.reserve_operator_ai_gross_usage_v1(%s, %s, 'RAG_VERTEX', 'VERTEX', %s, %s)",
            ("aibr_" + "c" * 32, "usr_bob1", 15_000, 30_000),
        ).fetchone() == (False,)
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT owner_user_id, source, provider FROM operator_ai_gross_usage_reservations"
        ).fetchall() == [("usr_alice", "TRADE_AI", "VERTEX")]
