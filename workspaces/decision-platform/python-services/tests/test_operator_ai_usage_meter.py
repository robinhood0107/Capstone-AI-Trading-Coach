from __future__ import annotations

import psycopg
import pytest

from app.operator_ai_usage_meter import TradeAiUsageMeter


def test_full_meter_does_not_require_or_read_a_daily_dollar_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    monkeypatch.delenv("MARS_AI_DAILY_HARD_CAP_USD", raising=False)
    monkeypatch.setenv(
        "P1_AUTOMATION_DATABASE_DSN",
        "postgresql://decision_automation_runtime:fixture@localhost:5432/decision",
    )
    meter = TradeAiUsageMeter.from_environment()

    assert meter is not None
    assert meter.input_microusd_per_token == 3
    assert meter.output_microusd_per_token == 17


def test_meter_configuration_failure_disables_only_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    monkeypatch.setenv("P1_AUTOMATION_DATABASE_DSN", "not-a-dsn")

    assert TradeAiUsageMeter.from_environment() is None


def test_usage_database_failure_does_not_raise_or_reject_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meter = TradeAiUsageMeter("postgresql://decision_automation_runtime:fixture@localhost/decision")

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("usage database unavailable")

    monkeypatch.setattr("app.operator_ai_usage_meter.psycopg.connect", unavailable)

    assert not meter.record(
        owner_user_id="usr_alice",
        run_id="auto_run_abc",
        payload_bytes=b'{"candidate":1}',
        output_token_cap=1_024,
    )


def test_all_users_keep_operating_past_the_former_daily_amount(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    meter = TradeAiUsageMeter(
        database_dsn=isolated_postgres_cluster["automation_runtime_dsn"],
        input_microusd_per_token=10,
        output_microusd_per_token=10,
    )

    for owner, run in (("usr_alice", "run_alice"), ("usr_bob1", "run_bob1")):
        assert meter.record(
            owner_user_id=owner,
            run_id=run,
            payload_bytes=b'{"candidate":1}',
            output_token_cap=100_000,
        )

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        rows = connection.execute(
            "SELECT owner_user_id, source, provider, max_gross_microusd "
            "FROM operator_ai_gross_usage_reservations ORDER BY owner_user_id"
        ).fetchall()
    assert [row[:3] for row in rows] == [
        ("usr_alice", "TRADE_AI", "VERTEX"),
        ("usr_bob1", "TRADE_AI", "VERTEX"),
    ]
    assert all(row[3] > 1_000_000 for row in rows)


def test_repeated_meter_identity_is_idempotent_and_not_a_gate(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    meter = TradeAiUsageMeter(isolated_postgres_cluster["automation_runtime_dsn"])
    fields = {
        "owner_user_id": "usr_alice",
        "run_id": "run_repeated",
        "payload_bytes": b'{"candidate":1}',
        "output_token_cap": 1_024,
    }

    assert meter.record(**fields)
    assert meter.record(**fields)

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM operator_ai_gross_usage_reservations "
            "WHERE owner_user_id = 'usr_alice' AND source = 'TRADE_AI'"
        ).fetchone() == (1,)
