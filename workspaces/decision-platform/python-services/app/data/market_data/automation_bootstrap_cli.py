"""CLI for provider-free planning/inspection and explicitly gated read-only bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis.universe import load_universe_manifest
from app.data.market_data.automation_bootstrap import (
    AutomationBootstrapError,
    KisAutomationBootstrapSource,
    PostgresAutomationMarketReader,
    build_bootstrap_plan,
    collect_automation_bootstrap,
    read_automation_bootstrap_archive,
    stage_automation_bootstrap,
)

_APPROVAL_ID = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 Automation V3 market-data bootstrap")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    _plan_arguments(plan_parser)

    collect_parser = subparsers.add_parser("collect")
    _plan_arguments(collect_parser)
    collect_parser.add_argument("--output-root", type=Path, required=True)
    collect_parser.add_argument("--kis-daily-physical-cap", type=int, required=True)
    collect_parser.add_argument("--kis-token-physical-cap", type=int, required=True)
    collect_parser.add_argument("--krx-membership-physical-calls", type=int, required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--archive-root", type=Path, required=True)
    validate_parser.add_argument("--expected-manifest-sha256", required=True)

    stage_parser = subparsers.add_parser("stage")
    stage_parser.add_argument("--archive-root", type=Path, required=True)
    stage_parser.add_argument("--expected-manifest-sha256", required=True)

    subparsers.add_parser("inventory")

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = _plan(args)
            _print(_plan_projection(plan))
        elif args.command == "collect":
            plan = _plan(args)
            if (
                args.kis_daily_physical_cap != plan.provider_caps["kisDaily"]
                or args.kis_token_physical_cap != plan.provider_caps["kisToken"]
                or not 0
                <= args.krx_membership_physical_calls
                <= plan.provider_caps["krxMembership"]
            ):
                raise AutomationBootstrapError("automation bootstrap CLI caps drifted")
            _require_approval(plan.plan_sha256)
            source = KisAutomationBootstrapSource.from_environment()
            try:
                collected = collect_automation_bootstrap(
                    plan=plan,
                    source=source,
                    output_root=args.output_root,
                    created_at=datetime.now().astimezone(),
                    token_physical_calls=args.kis_token_physical_cap,
                    krx_membership_physical_calls=args.krx_membership_physical_calls,
                )
            finally:
                source.close()
            _print(
                {
                    "bars": collected.row_count,
                    "barsSha256": collected.bars_sha256,
                    "manifestSha256": collected.manifest_sha256,
                    "orderCalls": 0,
                }
            )
        elif args.command == "validate":
            archive = read_automation_bootstrap_archive(args.archive_root)
            if archive.manifest_sha256 != args.expected_manifest_sha256:
                raise AutomationBootstrapError("automation bootstrap validate binding drifted")
            _print(
                {
                    "bars": archive.row_count,
                    "manifestSha256": archive.manifest_sha256,
                    "providerCalls": 0,
                    "status": "VALID",
                }
            )
        elif args.command == "stage":
            database_dsn = os.environ.get("MARKET_DATA_WRITER_DSN", "").strip()
            if not database_dsn:
                raise AutomationBootstrapError("MARKET_DATA_WRITER_DSN is required")
            staged = stage_automation_bootstrap(
                database_dsn=database_dsn,
                archive_root=args.archive_root,
                expected_manifest_sha256=args.expected_manifest_sha256,
            )
            _print(
                {
                    "bars": staged.bars,
                    "manifestSha256": staged.manifest_sha256,
                    "outcome": staged.outcome,
                    "providerCalls": 0,
                    "universes": staged.universes,
                }
            )
        else:
            database_dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
            if not database_dsn:
                raise AutomationBootstrapError("P1_AUTOMATION_DATABASE_DSN is required")
            reader = PostgresAutomationMarketReader(database_dsn)
            try:
                value = reader.inventory()
            finally:
                reader.close()
            _print(
                {
                    "barCount": value.bar_count,
                    "currentUniverseCount": value.current_universe_count,
                    "latestSession": (
                        value.latest_session.isoformat() if value.latest_session else None
                    ),
                    "manifestCount": value.manifest_count,
                    "providerCalls": 0,
                    "status": value.status,
                }
            )
    except (AutomationBootstrapError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"P1_AUTOMATION_MARKET_DATA=FAIL:{error}")
        return 1
    return 0


def _plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--universe-manifest", type=Path, required=True)
    parser.add_argument("--end-session", type=date.fromisoformat, required=True)


def _plan(args: argparse.Namespace) -> Any:
    return build_bootstrap_plan(
        load_universe_manifest(args.universe_manifest),
        end_session=args.end_session,
    )


def _plan_projection(plan: Any) -> dict[str, object]:
    return {
        "contractId": "p1-automation-market-bootstrap-plan.v1",
        "endSession": plan.selection_session.isoformat(),
        "membership": [member.symbol for member in plan.members],
        "planSha256": plan.plan_sha256,
        "providerCaps": plan.provider_caps,
        "providerCalls": 0,
        "requestedSessionCount": len(plan.sessions),
        "windowCount": len(plan.windows),
    }


def _require_approval(plan_sha256: str) -> None:
    approved_sha = os.environ.get("P1_AUTOMATION_MARKET_BOOTSTRAP_PACKET_SHA256", "")
    approval_id = os.environ.get("P1_AUTOMATION_MARKET_BOOTSTRAP_APPROVAL_ID", "")
    if approved_sha != plan_sha256 or _APPROVAL_ID.fullmatch(approval_id) is None:
        raise AutomationBootstrapError("automation bootstrap approval binding is unavailable")


def _print(value: object) -> None:
    print(canonical_json_bytes(value).decode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
