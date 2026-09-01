from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis.parsers import DailyBar
from app.data.kis.universe import UniverseManifest, UniverseManifestSymbol
from app.data.market_data.automation_bootstrap import (
    AutomationBootstrapError,
    BootstrapWindow,
    KisAutomationBootstrapSource,
    PostgresAutomationMarketReader,
    build_bootstrap_plan,
    collect_automation_bootstrap,
    read_automation_bootstrap_archive,
    stage_automation_bootstrap,
)
from app.data.market_data.automation_bootstrap_cli import _approval_packet


class _FixtureSource:
    def __init__(self, *, missing: tuple[str, date] | None = None) -> None:
        self.physical_calls = 0
        self.missing = missing

    def fetch(self, window: BootstrapWindow) -> tuple[DailyBar, ...]:
        self.physical_calls += 1
        rows: list[DailyBar] = []
        for ordinal, session in enumerate(window.sessions, start=1):
            if self.missing == (window.symbol, session):
                continue
            price = 10_000 + session.toordinal() % 10_000 + ordinal
            rows.append(
                DailyBar(
                    symbol=window.symbol,
                    date=session,
                    open=price,
                    high=price + 100,
                    low=price - 100,
                    close=price + 10,
                    volume=100_000 + ordinal,
                )
            )
        return tuple(rows)


def _universe(*, as_of: date = date(2026, 9, 30)) -> UniverseManifest:
    return UniverseManifest(
        schema_version=1,
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
        as_of_date=as_of,
        source="krx-open-api:fixture",
        source_sha256="1" * 64,
        ranking_rule="market-cap-desc,trading-value-desc,symbol-asc",
        limit=30,
        symbols=tuple(
            UniverseManifestSymbol(
                rank=rank,
                symbol=f"{100_000 + rank:06d}",
                name=f"종목 {rank}",
                market="KOSPI" if rank <= 20 else "KOSDAQ",
                market_cap=1_000_000_000 - rank,
                trading_value=100_000_000 - rank,
            )
            for rank in range(1, 31)
        ),
    )


def test_plan_is_exact31_exact756_with_one_transient_retry_cap() -> None:
    plan = build_bootstrap_plan(_universe(), end_session=date(2026, 9, 30))

    assert len(plan.members) == 31
    assert plan.members[-1].symbol == "132030"
    assert plan.members[-1].rank == 31
    assert plan.members[-1].is_fixed_member is True
    assert len(plan.sessions) == 756
    assert len(plan.windows) == 248
    assert plan.provider_caps == {
        "kisDaily": 496,
        "kisToken": 1,
        "krxMembership": 5,
        "retry": 1,
    }
    assert all(1 <= len(window.sessions) <= 100 for window in plan.windows)
    assert sum(len(window.sessions) for window in plan.windows) == 31 * 756


def test_quick_readiness_plan_keeps_exact31_and_derives_retry_bound_call_cap() -> None:
    plan = build_bootstrap_plan(
        _universe(),
        end_session=date(2026, 9, 30),
        session_count=100,
    )

    assert len(plan.members) == 31
    assert len(plan.sessions) == 100
    assert len(plan.windows) == 31
    assert plan.provider_caps["kisDaily"] == 62
    assert date(2026, 6, 3) not in plan.sessions
    assert date(2026, 7, 17) not in plan.sessions


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda manifest: manifest.__class__(**{**manifest.__dict__, "limit": 29}), "exact 30"),
        (
            lambda manifest: manifest.__class__(
                **{
                    **manifest.__dict__,
                    "symbols": (*manifest.symbols[:-1], manifest.symbols[0]),
                }
            ),
            "unique",
        ),
    ],
)
def test_plan_rejects_non_authoritative_universe(mutator: object, match: str) -> None:
    invalid = mutator(_universe())  # type: ignore[operator]
    with pytest.raises(AutomationBootstrapError, match=match):
        build_bootstrap_plan(invalid, end_session=date(2026, 9, 30))


def test_collection_is_manifest_last_deterministic_and_rejects_mid_gap(tmp_path: Path) -> None:
    plan = build_bootstrap_plan(_universe(), end_session=date(2026, 9, 30))
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_source = _FixtureSource()
    second_source = _FixtureSource()

    first = collect_automation_bootstrap(
        plan=plan,
        source=first_source,
        output_root=first_root,
        created_at=datetime(2026, 10, 1, 8, 10, tzinfo=UTC),
        token_physical_calls=1,
        krx_membership_physical_calls=1,
    )
    second = collect_automation_bootstrap(
        plan=plan,
        source=second_source,
        output_root=second_root,
        created_at=datetime(2026, 10, 1, 8, 10, tzinfo=UTC),
        token_physical_calls=1,
        krx_membership_physical_calls=1,
    )

    assert first.manifest_sha256 == second.manifest_sha256
    assert first.bars_sha256 == second.bars_sha256
    assert first.row_count == second.row_count == 31 * 756
    assert first_source.physical_calls == second_source.physical_calls == 248
    assert (first_root / "manifest.json").is_file()
    assert read_automation_bootstrap_archive(first_root).manifest_sha256 == first.manifest_sha256

    missing_session = plan.sessions[100]
    with pytest.raises(AutomationBootstrapError, match="middle session gap"):
        collect_automation_bootstrap(
            plan=plan,
            source=_FixtureSource(missing=("100001", missing_session)),
            output_root=tmp_path / "missing",
            created_at=datetime(2026, 10, 1, 8, 10, tzinfo=UTC),
            token_physical_calls=1,
            krx_membership_physical_calls=1,
        )


def test_stage_is_atomic_replay_safe_and_runtime_reads_only_bounded_function(
    tmp_path: Path,
    postgres_cluster: dict[str, str],
) -> None:
    plan = build_bootstrap_plan(_universe(), end_session=date(2026, 9, 30))
    archive_root = tmp_path / "archive"
    archive = collect_automation_bootstrap(
        plan=plan,
        source=_FixtureSource(),
        output_root=archive_root,
        created_at=datetime(2026, 10, 1, 8, 10, tzinfo=UTC),
        token_physical_calls=1,
        krx_membership_physical_calls=1,
    )

    inserted = stage_automation_bootstrap(
        database_dsn=postgres_cluster["market_writer_dsn"],
        archive_root=archive_root,
        expected_manifest_sha256=archive.manifest_sha256,
    )
    replayed = stage_automation_bootstrap(
        database_dsn=postgres_cluster["market_writer_dsn"],
        archive_root=archive_root,
        expected_manifest_sha256=archive.manifest_sha256,
    )

    assert inserted.outcome == "INSERTED"
    assert inserted.bars == 31 * 756
    assert inserted.universes == 31
    assert inserted.provider_calls == 0
    assert replayed.outcome == "NO_OP"

    reader = PostgresAutomationMarketReader(postgres_cluster["automation_runtime_dsn"])
    inventory = reader.inventory()
    assert inventory.manifest_count >= 1
    assert inventory.bar_count >= 31 * 756
    assert inventory.current_universe_count == 31
    assert inventory.status == "READY"
    bars = reader.read_atr_bars("100001", as_of_session=date(2026, 10, 1), limit=23)
    assert len(bars) == 23
    assert tuple(item.session_date for item in bars) == tuple(
        sorted(item.session_date for item in bars)
    )
    reader.close()

    with psycopg.connect(postgres_cluster["automation_runtime_dsn"]) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from market_data_bars").fetchone()


def test_archive_identity_is_verified_before_opening_database(tmp_path: Path) -> None:
    plan = build_bootstrap_plan(_universe(), end_session=date(2026, 9, 30))
    archive_root = tmp_path / "archive"
    collect_automation_bootstrap(
        plan=plan,
        source=_FixtureSource(),
        output_root=archive_root,
        created_at=datetime(2026, 10, 1, 8, 10, tzinfo=UTC),
        token_physical_calls=1,
        krx_membership_physical_calls=1,
    )

    with pytest.raises(AutomationBootstrapError, match="operator binding"):
        stage_automation_bootstrap(
            database_dsn="postgresql://must-not-connect.invalid/db",
            archive_root=archive_root,
            expected_manifest_sha256="f" * 64,
        )


def test_live_bootstrap_is_default_off_before_client_or_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("P1_AUTOMATION_MARKET_BOOTSTRAP_ENABLED", raising=False)
    with pytest.raises(AutomationBootstrapError, match="disabled"):
        KisAutomationBootstrapSource.from_environment()


def test_execution_packet_binds_plan_caps_mode_and_owner_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = build_bootstrap_plan(_universe(), end_session=date(2026, 9, 30))
    packet = tmp_path / "approved-packet.json"
    payload = {
        "approvalId": "P1-MOCK-BOOTSTRAP-20260902",
        "contractId": "p1-automation-market-bootstrap-execution.v1",
        "kisMode": "mock",
        "krxMembershipPhysicalCalls": 0,
        "planSha256": plan.plan_sha256,
        "providerCaps": plan.provider_caps,
    }
    packet.write_bytes(canonical_json_bytes(payload))
    packet.chmod(0o600)
    monkeypatch.setenv("P1_AUTOMATION_MARKET_BOOTSTRAP_KIS_MODE", "mock")

    assert _approval_packet(packet, plan) == payload

    packet.write_text(json.dumps({**payload, "planSha256": "f" * 64}), encoding="utf-8")
    packet.chmod(0o600)
    with pytest.raises(AutomationBootstrapError, match="canonical|binding"):
        _approval_packet(packet, plan)
