from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from pathlib import Path
import sys
import time
from collections.abc import Callable
from typing import NoReturn

from app.data.quality.kis_daily import QualityReadLimits, load_quality_snapshot
from app.data.quality.metrics import analyze_quality
from app.data.quality.models import EvidenceCompleteness, QualityStatus
from app.data.quality.policy import WALL_DEADLINE_SECONDS
from app.data.quality.storage import publish_quality_bundle


class _UsageError(ValueError):
    pass


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse의 기본 error는 raw argv/value를 echo하므로 내부 CLI의 stable output 계약에 맞지 않는다.
        raise _UsageError from None


def main(argv: list[str] | None = None) -> int:
    """provider/network path 없이 manifest-pinned snapshot을 분석·게시하는 내부 CLI 진입점이다."""
    try:
        args = _parse_args(argv)
    except _UsageError:
        _print_error("USAGE_ERROR")
        return 2

    deadline_check = _wall_deadline_check(time.monotonic())
    try:
        deadline_check()
        root = Path(os.environ.get("KIS_DATA_DIR", "data/kis"))
        window_start = date.fromisoformat(args.window_start)
        window_end = date.fromisoformat(args.window_end)
        evaluated_at = _parse_evaluated_at(args.evaluated_at)
        snapshot = load_quality_snapshot(
            root=root,
            universe_identifier=args.universe_manifest,
            dataset_identifier=args.dataset_manifest,
            collection_identifier=args.collection_run,
            window_start=window_start,
            window_end=window_end,
            evaluated_at=evaluated_at,
            software_revision=args.software_revision,
            limits=QualityReadLimits(),
            deadline_check=deadline_check,
        )
        report = analyze_quality(
            snapshot.context,
            snapshot.datasets,
            deadline_check=deadline_check,
        )
        published = publish_quality_bundle(
            root,
            report,
            deadline_check=deadline_check,
        )
        deadline_check()
    except KeyboardInterrupt:
        _print_error("INPUT_OR_PUBLISH_ERROR")
        return 2
    except Exception:
        # arbitrary exception text, path, argv, credential configured 여부는 stderr로 전달하지 않는다.
        _print_error("INPUT_OR_PUBLISH_ERROR")
        return 2

    print(
        "KIS_DATA_QUALITY "
        f"status={report.status.quality_status} "
        f"evidence={report.status.evidence_completeness} "
        f"reportId={report.report_id} "
        f"bundle={published.bundle_identifier} "
        f"idempotent={str(not published.created).lower()}"
    )
    if (
        args.require_complete_evidence
        and report.status.evidence_completeness != EvidenceCompleteness.COMPLETE
    ):
        return 3
    if args.fail_on_quality and report.status.quality_status == QualityStatus.FAIL:
        return 1
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _StableArgumentParser(
        description="Generate an offline manifest-pinned KIS data quality report",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_StableArgumentParser,
    )
    generate = subparsers.add_parser(
        "generate",
        help="Validate, analyze, and publish a deterministic report bundle",
    )
    generate.add_argument("--window-start", required=True, metavar="YYYY-MM-DD")
    generate.add_argument("--window-end", required=True, metavar="YYYY-MM-DD")
    generate.add_argument("--evaluated-at", required=True, metavar="RFC3339")
    generate.add_argument("--universe-manifest", required=True, metavar="RELATIVE_ID")
    generate.add_argument("--dataset-manifest", required=True, metavar="RELATIVE_ID")
    generate.add_argument("--collection-run", metavar="RELATIVE_ID")
    generate.add_argument("--software-revision", required=True, metavar="HEX_REVISION")
    generate.add_argument("--fail-on-quality", action="store_true")
    generate.add_argument("--require-complete-evidence", action="store_true")
    return parser.parse_args(argv)


def _parse_evaluated_at(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("evaluatedAt must be timezone-aware")
    return parsed


def _print_error(code: str) -> None:
    print(f"KIS_DATA_QUALITY_ERROR code={code}", file=sys.stderr)


def _wall_deadline_check(started: float) -> Callable[[], None]:
    deadline = started + WALL_DEADLINE_SECONDS

    def check() -> None:
        if time.monotonic() > deadline:
            raise ValueError("quality report deadline exceeded")

    return check


if __name__ == "__main__":
    raise SystemExit(main())
