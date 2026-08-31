from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from app.p1_owner.after_hours_replay import AfterHoursReplayError, run_after_hours_replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p1-after-hours-replay")
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/decision-platform/after-hours-replay"),
    )
    parser.add_argument(
        "--observed-anchor-root",
        type=Path,
        default=Path("/observed-anchors"),
    )
    parser.add_argument(
        "--observed-anchor-manifest",
        type=Path,
        default=Path("/observed-anchor-manifest.json"),
    )
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)
    dsn = os.environ.get("P1_AFTER_HOURS_REPLAY_DATABASE_DSN", "").strip()
    if not dsn:
        print(json.dumps({"status": "BLOCKED", "reason": "AFTER_HOURS_REPLAY_DSN_MISSING"}))
        return 2
    try:
        result = run_after_hours_replay(
            database_dsn=dsn,
            manifest_sha256=args.manifest_sha256,
            output_root=args.output_root,
            observed_anchor_root=args.observed_anchor_root,
            observed_anchor_manifest=args.observed_anchor_manifest,
            today=args.today,
        )
    except AfterHoursReplayError as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "PASS", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
