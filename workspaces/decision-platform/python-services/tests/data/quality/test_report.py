from datetime import UTC, date, datetime
import json

import pytest
from pydantic import ValidationError

from app.data.quality.metrics import analyze_quality
from app.data.quality.models import AnalysisContext, ManifestReference, SymbolDataset
from app.data.quality.policy import CANONICAL_DAILY_COLUMNS
from app.data.quality.report import escape_markdown, render_markdown, report_json_bytes


def _report(*, revision: str = "7131f695293472ea16ee05322ed9b05f7b69d129"):
    session = date(2026, 7, 21)
    context = AnalysisContext(
        evaluated_at=datetime(2026, 7, 21, 7, tzinfo=UTC),
        software_revision=revision,
        window_start=session,
        window_end=session,
        expected_last_completed_xkrx_session=session,
        sessions=(session,),
        universe_symbols=("005930",),
        universe_manifest=ManifestReference(
            identifier="universe_manifest.json",
            sha256="a" * 64,
        ),
        dataset_manifest=ManifestReference(
            identifier="datasets/2026/07/21/manifest.json",
            sha256="b" * 64,
        ),
        collection_run=None,
        collection_summary=None,
        dataset_file_count=1,
    )
    raw_canary = 987_654_321_123
    row = {
        "symbol": "005930",
        "date": session,
        "open": raw_canary,
        "high": raw_canary + 10,
        "low": raw_canary - 10,
        "close": raw_canary + 1,
        "volume": raw_canary + 2,
        "turnover": raw_canary + 3,
    }
    return analyze_quality(
        context,
        [SymbolDataset(symbol="005930", columns=CANONICAL_DAILY_COLUMNS, rows=(row,))],
    )


def test_json_and_markdown_share_identity_status_counts_rates_and_metric_order() -> None:
    report = _report()
    payload = json.loads(report_json_bytes(report))
    markdown = render_markdown(report).decode("utf-8")

    assert payload["reportId"] == str(report.report_id)
    assert str(report.report_id) in markdown
    assert payload["status"]["qualityStatus"] in markdown
    assert f"Rows | {payload['counts']['rows']}" in markdown
    positions = [markdown.index(metric["metricId"]) for metric in payload["metrics"]]
    assert positions == sorted(positions)
    for metric in payload["metrics"]:
        rate = "null" if metric["ratePpm"] is None else str(metric["ratePpm"])
        assert f"| {metric['metricId']} | {metric['status']} |" in markdown
        assert rate in markdown


def test_report_serialization_is_deterministic_has_no_self_hash_nan_or_raw_ohlcv() -> None:
    report = _report()
    first = report_json_bytes(report)
    second = report_json_bytes(report)
    markdown = render_markdown(report)

    assert first == second
    assert b"reportSha" not in first
    assert b"markdownSha" not in first
    assert b"NaN" not in first and b"Infinity" not in first
    assert b"987654321123" not in first + markdown
    assert b'"open"' not in first and b'"close"' not in first


def test_markdown_escape_removes_structure_and_control_injection() -> None:
    escaped = escape_markdown("x|`y\r\nz\x00[link](https://invalid.example)")

    assert "\r" not in escaped and "\n" not in escaped and "\x00" not in escaped
    assert "\\|" in escaped and "\\`" in escaped
    assert "https://" not in escaped


def test_manifest_reference_rejects_absolute_traversal_and_control_injection() -> None:
    for identifier in (
        "/tmp/report.json",
        "../report.json",
        "x/./report.json",
        "x|`y\n.json",
    ):
        with pytest.raises(ValidationError):
            ManifestReference(identifier=identifier, sha256="a" * 64)
