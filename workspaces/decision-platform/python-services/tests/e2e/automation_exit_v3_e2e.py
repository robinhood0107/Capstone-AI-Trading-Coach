from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal

from app.p1_owner.automation import AutomationPolicySnapshot
from app.p1_owner.automation_atr import CompletedDailyBar, advance_trailing_stop, wilder_atr

from .harness import Recorder, require_opt_in, write_report

_OPT_IN = "P1_AUTOMATION_EXIT_V3_E2E"


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])
    recorder = Recorder()
    policies = [
        AutomationPolicySnapshot.from_v3_preset(
            policy_id="auto_pol_" + str(index) * 32,
            version=index,
            capital_limit_krw=10_000_000,
            preset=preset,
        )
        for index, preset in enumerate(("CONSERVATIVE", "BALANCED", "AGGRESSIVE"), 1)
    ]
    recorder.add(
        "owner presets and unlimited expiry",
        "PASS" if [item.max_holding_sessions for item in policies] == [20, 60, 0] else "FAIL",
        f"holding={[item.max_holding_sessions for item in policies]}",
    )
    bars = tuple(
        CompletedDailyBar(date(2026, 8, day), 100, 110 + day, 90, 100 + day) for day in range(3, 10)
    )
    atr = wilder_atr(bars, period=5, as_of_session=date(2026, 8, 10))
    trailing = advance_trailing_stop(
        previous_peak_price_krw=100,
        completed_high_price_krw=125,
        current_quote_price_krw=120,
        atr_value_krw=atr.value_krw,
        atr_multiplier_milli=3_000,
        previous_trailing_stop_krw=None,
    )
    recorder.add(
        "Decimal Wilder ATR and monotonic trailing stop",
        "PASS" if isinstance(atr.value_krw, Decimal) and trailing.peak_price_krw == 125 else "FAIL",
        f"atr={atr.value_krw} peak={trailing.peak_price_krw} stop={trailing.trailing_stop_krw}",
    )
    report = write_report(
        contract_id="p1-automation-exit-v3-e2e.v1",
        marker="P1_AUTOMATION_EXIT_V3_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
