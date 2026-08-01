from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cross_market.pdf_boundary import (
    APPROVED_PDF_SECTIONS,
    BoundedPdfParseResult,
    LocalPdfRequest,
    ManualAnalystReportLink,
    PdfBoundaryError,
    build_manual_link_projection,
    process_local_ephemeral_pdf,
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


def test_owner_pdf_is_read_only_during_local_parse_and_returns_no_raw_text(tmp_path: Path) -> None:
    target, request = _local_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(_valid_parse_result(), target)
    before = (target.stat().st_ino, target.stat().st_mtime_ns, target.read_bytes())

    receipt = process_local_ephemeral_pdf(request, parser)

    assert target.exists() is True
    assert parser.calls == 1
    assert parser.target_existed_during_parse is True
    assert receipt.input_sha256 == request.expected_sha256
    assert receipt.document_id == request.document_id
    assert receipt.processing_mode == "LOCAL_EPHEMERAL_PARSE"
    assert receipt.normalized_tags == ("RISK", "VALUATION")
    assert receipt.raw_text_stored is False
    assert receipt.quote_stored is False
    assert receipt.external_llm_calls == 0
    assert receipt.section_names == APPROVED_PDF_SECTIONS
    assert receipt.page_count == 1
    assert (target.stat().st_ino, target.stat().st_mtime_ns, target.read_bytes()) == before


def test_derived_data_false_discards_even_user_confirmed_tags(tmp_path: Path) -> None:
    target, request = _local_pdf(tmp_path, derived_data_allowed=False)
    parser = _FakeParser(_valid_parse_result(), target)

    receipt = process_local_ephemeral_pdf(request, parser)

    assert receipt.normalized_tags == ()
    assert receipt.section_names == ()
    assert receipt.page_count is None
    assert receipt.derived_data_stored is False


def test_symlink_traversal_and_wrong_mime_are_rejected_before_parser(tmp_path: Path) -> None:
    target, request = _local_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(_valid_parse_result(), target)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.pdf"
    outside.write_bytes(b"%PDF-1.7\nfixture")
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(PdfBoundaryError):
        process_local_ephemeral_pdf(request, parser)
    assert parser.calls == 0

    target.unlink()
    target.write_bytes(b"not-a-pdf")
    os.chmod(target, 0o600)
    bad_request = _request_for(target, request.approved_root, derived_data_allowed=True)
    with pytest.raises(PdfBoundaryError, match="LOCAL_PDF_MIME_INVALID"):
        process_local_ephemeral_pdf(bad_request, parser)
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
    target, request = _local_pdf(tmp_path, derived_data_allowed=True)
    parser = _FakeParser(result, target)

    with pytest.raises(PdfBoundaryError, match="LOCAL_PDF_PARSE_BOUNDARY"):
        process_local_ephemeral_pdf(request, parser)

    assert target.exists() is True
    assert parser.calls == 1


def _local_pdf(
    root: Path,
    *,
    derived_data_allowed: bool,
) -> tuple[Path, LocalPdfRequest]:
    os.chmod(root, 0o700)
    target = root / "owner-fixture.pdf"
    target.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Page >> endobj\n%%EOF")
    os.chmod(target, 0o600)
    return target, _request_for(target, root, derived_data_allowed=derived_data_allowed)


def _request_for(
    target: Path,
    root: Path,
    *,
    derived_data_allowed: bool,
) -> LocalPdfRequest:
    return LocalPdfRequest(
        document_id="doc_owner_fixture_001",
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
