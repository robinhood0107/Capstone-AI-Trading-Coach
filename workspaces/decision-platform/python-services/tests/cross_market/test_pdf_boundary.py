from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.cross_market.pdf_boundary as pdf_boundary
from app.cross_market.pdf_boundary import (
    APPROVED_PDF_SECTIONS,
    BoundedPdfParseResult,
    EphemeralPdfApproval,
    ManualAnalystReportLink,
    PdfBoundaryError,
    build_manual_link_projection,
    process_licensed_ephemeral_pdf,
)


@dataclass
class _FakeParser:
    result: BoundedPdfParseResult
    target: Path
    calls: int = 0
    target_existed_during_parse: bool | None = None

    def parse(self, payload: memoryview) -> BoundedPdfParseResult:
        self.calls += 1
        self.target_existed_during_parse = self.target.exists()
        assert payload.tobytes().startswith(b"%PDF-")
        return self.result


def test_manual_link_default_keeps_metadata_only_and_never_downloads() -> None:
    projection = build_manual_link_projection(
        ManualAnalystReportLink(
            title="합성 리서치 제목",
            broker="fixture-broker",
            published_at=datetime(2026, 7, 30, tzinfo=UTC),
            url="https://research.example.com/reports/fixture-001",
        )
    )

    assert projection["mode"] == "MANUAL_LINK_ONLY"
    assert projection["automaticDownloadCount"] == 0
    assert projection["contentStored"] is False
    assert projection["embedded"] is False
    assert projection["externalLlmCalls"] == 0
    assert set(projection) == {
        "automaticDownloadCount",
        "broker",
        "contentStored",
        "embedded",
        "externalLlmCalls",
        "mode",
        "publishedAt",
        "title",
        "url",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://research.example.com/report",
        "https://127.0.0.1/report",
        "https://localhost/report",
        "https://user:password@research.example.com/report",
        "file:///tmp/report.pdf",
    ],
)
def test_manual_link_rejects_ssrf_or_credential_bearing_urls(url: str) -> None:
    with pytest.raises(PdfBoundaryError, match="MANUAL_LINK_URL_INVALID"):
        build_manual_link_projection(
            ManualAnalystReportLink(
                title="fixture",
                broker="fixture-broker",
                published_at=datetime(2026, 7, 30, tzinfo=UTC),
                url=url,
            )
        )


def test_ephemeral_pdf_is_deleted_before_local_parse_and_returns_no_raw_text(tmp_path: Path) -> None:
    target, approval = _approved_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(_valid_parse_result(), target)

    receipt = process_licensed_ephemeral_pdf(approval, parser)

    assert target.exists() is False
    assert parser.calls == 1
    assert parser.target_existed_during_parse is False
    assert receipt.input_sha256 == approval.expected_sha256
    assert receipt.normalized_tags == ("RISK", "VALUATION")
    assert receipt.raw_text_stored is False
    assert receipt.quote_stored is False
    assert receipt.external_llm_calls == 0
    assert receipt.section_names == APPROVED_PDF_SECTIONS


def test_derived_data_false_discards_even_user_confirmed_tags(tmp_path: Path) -> None:
    target, approval = _approved_pdf(tmp_path, derived_data_allowed=False)
    parser = _FakeParser(_valid_parse_result(), target)

    receipt = process_licensed_ephemeral_pdf(approval, parser)

    assert receipt.normalized_tags == ()
    assert receipt.derived_data_stored is False
    assert len(receipt.deletion_receipt_hash) == 64


def test_delete_failure_stops_before_parser_storage_or_outbound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, approval = _approved_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(_valid_parse_result(), target)

    def fail_delete(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic delete failure")

    monkeypatch.setattr(pdf_boundary, "_unlink_ephemeral_leaf", fail_delete)

    with pytest.raises(PdfBoundaryError, match="EPHEMERAL_DELETE_FAILED"):
        process_licensed_ephemeral_pdf(approval, parser)

    assert parser.calls == 0
    assert target.exists() is True


def test_symlink_traversal_and_wrong_mime_are_rejected_before_parser(tmp_path: Path) -> None:
    target, approval = _approved_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(_valid_parse_result(), target)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.pdf"
    outside.write_bytes(b"%PDF-1.7\nfixture")
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(PdfBoundaryError):
        process_licensed_ephemeral_pdf(approval, parser)
    assert parser.calls == 0

    target.unlink()
    target.write_bytes(b"not-a-pdf")
    os.chmod(target, 0o600)
    bad_approval = _approval_for(target, approval.approved_root, derived_data_allowed=True)
    with pytest.raises(PdfBoundaryError, match="EPHEMERAL_MIME_INVALID"):
        process_licensed_ephemeral_pdf(bad_approval, parser)
    assert parser.calls == 0


@pytest.mark.parametrize(
    "result",
    [
        BoundedPdfParseResult(
            section_names=("알 수 없는 절",),
            page_count=1,
            decompressed_bytes=100,
        ),
        BoundedPdfParseResult(
            section_names=APPROVED_PDF_SECTIONS,
            page_count=101,
            decompressed_bytes=100,
        ),
        BoundedPdfParseResult(
            section_names=APPROVED_PDF_SECTIONS,
            page_count=1,
            decompressed_bytes=33 * 1024 * 1024,
        ),
    ],
)
def test_parser_output_must_stay_within_section_page_and_decompression_bounds(
    tmp_path: Path,
    result: BoundedPdfParseResult,
) -> None:
    target, approval = _approved_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(result, target)

    with pytest.raises(PdfBoundaryError, match="EPHEMERAL_PARSE_BOUNDARY"):
        process_licensed_ephemeral_pdf(approval, parser)

    assert target.exists() is False
    assert parser.calls == 1


def _approved_pdf(
    root: Path,
    *,
    derived_data_allowed: bool,
) -> tuple[Path, EphemeralPdfApproval]:
    os.chmod(root, 0o700)
    target = root / "licensed-fixture.pdf"
    target.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Page >> endobj\n%%EOF")
    os.chmod(target, 0o600)
    return target, _approval_for(target, root, derived_data_allowed=derived_data_allowed)


def _approval_for(
    target: Path,
    root: Path,
    *,
    derived_data_allowed: bool,
) -> EphemeralPdfApproval:
    return EphemeralPdfApproval(
        approval_id="AUTH_LICENSED_EPHEMERAL_LOCAL_FIXTURE_20260731",
        approved_root=root,
        relative_path=target.name,
        expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        derived_data_allowed=derived_data_allowed,
        user_confirmed_tags=("VALUATION", "RISK"),
    )


def _valid_parse_result() -> BoundedPdfParseResult:
    return BoundedPdfParseResult(
        section_names=APPROVED_PDF_SECTIONS,
        page_count=1,
        decompressed_bytes=4096,
    )
