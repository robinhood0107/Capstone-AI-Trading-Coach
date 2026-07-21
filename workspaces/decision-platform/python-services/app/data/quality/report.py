from __future__ import annotations

import re

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.quality.models import KISDataQualityReport, ManifestReference
from app.data.quality.policy import MAX_REPORT_JSON_BYTES, MAX_REPORT_MARKDOWN_BYTES


_ABSOLUTE_PATH = re.compile(rb"(?:^|[\s\"'`])/(?:home|mnt|Users|private|tmp)/")
_SENSITIVE_OUTPUT = (
    b"authorization:",
    b"bearer ",
    b"access_token",
    b"appsecret",
    b"appkey",
    b"credential-token-canary",
)
_MARKDOWN_ESCAPED = frozenset("\\|`[]()<>!")


class QualityReportRenderError(ValueError):
    """typed report의 직렬화 상한·금지 출력 위반을 원문 없이 보고한다."""


def report_json_bytes(report: KISDataQualityReport) -> bytes:
    """strict typed report를 결정적 canonical JSON bytes로 한 번만 직렬화한다."""
    content = canonical_json_bytes(report.model_dump(mode="json", by_alias=True))
    if not 0 < len(content) <= MAX_REPORT_JSON_BYTES:
        raise QualityReportRenderError("quality report JSON exceeded the size limit")
    _validate_serialized_output(content)
    return content


def render_markdown(report: KISDataQualityReport) -> bytes:
    """JSON과 같은 typed model에서 metric 순서와 truth counts를 그대로 Markdown으로 렌더링한다."""
    counts = report.counts
    status = report.status
    provenance = report.input_provenance
    lines = [
        "# KIS Data Quality Report",
        "",
        "## Identity",
        "",
        f"- Report ID: `{report.report_id}`",
        f"- Analysis fingerprint: `{report.analysis_fingerprint}`",
        f"- Metric policy: `{report.metric_policy_version}`",
        f"- Evaluated at: `{report.evaluated_at.isoformat()}`",
        f"- Software revision: `{report.software_revision}`",
        "",
        "## Status",
        "",
        "| Axis | Value |",
        "|---|---|",
        f"| Execution | {status.execution_status} |",
        f"| Evidence | {status.evidence_completeness} |",
        f"| Quality | {status.quality_status} |",
        "",
        "## Counts",
        "",
        "| Item | Count |",
        "|---|---:|",
        f"| Symbols | {counts.symbols} |",
        f"| Sessions | {counts.sessions} |",
        f"| Files | {counts.files} |",
        f"| Rows | {counts.rows} |",
        f"| Samples | {counts.samples} |",
        "",
        "## Calendar and Provenance",
        "",
        f"- Calendar: `{report.calendar.name}` / `{report.calendar.timezone}`",
        f"- Window: `{report.calendar.window_start}` to `{report.calendar.window_end}`",
        (
            "- Expected last completed session: "
            f"`{report.calendar.expected_last_completed_xkrx_session}`"
        ),
        _reference_line("Universe manifest", provenance.universe_manifest),
        _reference_line("Dataset manifest", provenance.dataset_manifest),
        (
            _reference_line("Collection run", provenance.collection_run)
            if provenance.collection_run is not None
            else "- Collection run: `NOT_AVAILABLE`"
        ),
        "",
        "## Metrics",
        "",
        "| Metric | Status | Numerator | Denominator | Rate ppm | Samples |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for metric in report.metrics:
        numerator = "null" if metric.numerator is None else str(metric.numerator)
        denominator = "null" if metric.denominator is None else str(metric.denominator)
        rate = "null" if metric.rate_ppm is None else str(metric.rate_ppm)
        lines.append(
            f"| {escape_markdown(metric.metric_id)} | {metric.status} | "
            f"{numerator} | {denominator} | {rate} | {metric.sample_count} |"
        )
    lines.extend(
        [
            "",
            "## Bounded Derived Samples",
            "",
            "| Rule | Symbol | Session | Derived |",
            "|---|---|---|---|",
        ]
    )
    if report.bounded_samples:
        for sample in report.bounded_samples:
            derived = ", ".join(
                f"{escape_markdown(key)}={value}"
                for key, value in sorted(sample.derived.items())
            )
            lines.append(
                f"| {escape_markdown(sample.rule_code)} | {sample.symbol} | "
                f"{sample.session_date} | {derived} |"
            )
    else:
        lines.append("| No bounded samples | n/a | n/a | n/a |")
    lines.extend(
        [
            "",
            "## Classification and Retention",
            "",
            f"- Classification: `{report.data_classification.classification}`",
            "- Raw OHLCV included: `false`",
            "- Provider payload included: `false`",
            "- Sensitive data included: `false`",
            f"- Owner: `{escape_markdown(report.retention.owner)}`",
            f"- Policy: `{report.retention.policy_id}`",
            (
                "- Ordinary retention after evaluation event: "
                f"`{report.retention.ordinary_retention_days_after_evaluation_event}` days"
            ),
            f"- Pinned: `{str(report.retention.pinned).lower()}`",
            f"- Hold reason: `{report.retention.hold_reason or 'null'}`",
            "",
            (
                "Outlier flags are provisional project-policy evidence and do not by themselves "
                "distinguish a market event from a data error."
            ),
            "",
        ]
    )
    content = "\n".join(lines).encode("utf-8")
    if not 0 < len(content) <= MAX_REPORT_MARKDOWN_BYTES:
        raise QualityReportRenderError("quality report Markdown exceeded the size limit")
    _validate_serialized_output(content)
    return content


def escape_markdown(value: str) -> str:
    """table/control/URL 문법을 inert text로 바꾸며 줄바꿈을 단일 공백으로 정규화한다."""
    normalized = "".join(
        " " if character in "\r\n" or ord(character) < 32 or ord(character) == 127 else character
        for character in value
    )
    escaped = "".join(
        f"\\{character}" if character in _MARKDOWN_ESCAPED else character
        for character in normalized
    )
    # URL은 report의 허용 provenance가 아니므로 scheme delimiter도 Markdown에서 활성화하지 않는다.
    return escaped.replace("://", r"\://")


def _reference_line(label: str, reference: ManifestReference) -> str:
    identifier = escape_markdown(reference.identifier)
    sha256 = reference.sha256
    return f"- {label}: `{identifier}` (`{sha256}`)"


def _validate_serialized_output(content: bytes) -> None:
    lowered = content.lower()
    if _ABSOLUTE_PATH.search(content) is not None or b"http://" in lowered or b"https://" in lowered:
        raise QualityReportRenderError("quality report contained a forbidden path or URL")
    if any(marker in lowered for marker in _SENSITIVE_OUTPUT):
        raise QualityReportRenderError("quality report contained a forbidden sensitive marker")
    if any((byte < 32 and byte not in {9, 10, 13}) or byte == 127 for byte in content):
        raise QualityReportRenderError("quality report contained a control character")
