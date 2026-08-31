from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

from app.data.kis.universe import KRX_EXPORT_RANKING_RULE, UniverseManifest, UniverseManifestSymbol
from app.data.market_data.automation_bootstrap import (
    KIS_DAILY_PHYSICAL_MAX,
    SESSION_COUNT,
    UNIVERSE_SIZE,
    build_bootstrap_plan,
)

from .harness import Recorder, require_opt_in, write_report

_OPT_IN = "P1_MARKET_DATA_BOOTSTRAP_E2E"


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])
    recorder = Recorder()
    manifest = UniverseManifest(
        1,
        datetime(2026, 8, 31, tzinfo=UTC),
        date(2026, 8, 31),
        "fixture",
        "a" * 64,
        KRX_EXPORT_RANKING_RULE,
        30,
        tuple(
            UniverseManifestSymbol(
                index, f"{index:06d}", f"fixture-{index}", "KOSPI", 1_000_000 - index, 100 - index
            )
            for index in range(1, 31)
        ),
    )
    plan = build_bootstrap_plan(manifest, end_session=date(2026, 8, 31))
    recorder.add(
        "exact-31/1260/403 bootstrap plan",
        "PASS"
        if len(plan.members) == UNIVERSE_SIZE
        and len(plan.sessions) == SESSION_COUNT
        and len(plan.windows) == KIS_DAILY_PHYSICAL_MAX
        and plan.members[-1].symbol == "132030"
        else "FAIL",
        f"members={len(plan.members)} sessions={len(plan.sessions)} windows={len(plan.windows)} providerCalls=0",
    )
    report = write_report(
        contract_id="p1-market-data-bootstrap-e2e.v1",
        marker="P1_MARKET_DATA_BOOTSTRAP_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
