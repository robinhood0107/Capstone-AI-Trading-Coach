from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from app.p1_owner.after_hours_replay import AfterHoursReplayError, run_after_hours_replay

from .harness import Recorder, require_opt_in, write_report

_OPT_IN = "P1_AFTER_HOURS_HISTORICAL_REPLAY_E2E"


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--replay-output-root", type=Path, required=True)
    parser.add_argument("--observed-anchor-root", type=Path, required=True)
    parser.add_argument("--observed-anchor-manifest", type=Path, required=True)
    args = parser.parse_args(argv[1:])
    recorder = Recorder()
    dsn = os.environ.get("P1_AFTER_HOURS_REPLAY_DATABASE_DSN", "")
    try:
        result = run_after_hours_replay(
            database_dsn=dsn,
            manifest_sha256=args.manifest_sha256,
            output_root=args.replay_output_root,
            observed_anchor_root=args.observed_anchor_root,
            observed_anchor_manifest=args.observed_anchor_manifest,
            today=date.today(),
        )
        passed = (
            result["unexplainedRows"] == 0
            and result["syntheticMatrixStatus"] == "PASS"
            and result["observedAnchorStatus"] == "PASS"
        )
        recorder.add(
            "full historical row accounting and deterministic replay",
            "PASS" if passed else "FAIL",
            f"rows={result['inputRowCount']} rejected={result['rejectedRowCount']} reportSha256={result['reportSha256']}",
        )
    except AfterHoursReplayError as error:
        recorder.add("full historical row accounting and deterministic replay", "FAIL", str(error))
    report = write_report(
        contract_id="p1-after-hours-historical-replay-e2e.v1",
        marker="P1_AFTER_HOURS_HISTORICAL_REPLAY_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
