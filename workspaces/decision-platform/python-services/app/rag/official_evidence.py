from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.rag.safe_io import RagSafeIoError, read_approved_regular_file
from app.rag.source_card import (
    OFFICIAL_SOURCE_CARD_ROOT,
    RAG_SOURCE_CARD_SCHEMA_PATH,
    RagSourceCardError,
    load_rag_source_cards,
)
from app.rag.source_registry import (
    RagSourceRegistry,
    RagSourceRegistryError,
    validate_canonical_https_url,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
OFFICIAL_EVIDENCE_MANIFEST_PATH = (
    REPO_ROOT / "capstone-rag/manifests/s4-7a-official-evidence.v1.json"
)
OFFICIAL_EVIDENCE_ROOT = REPO_ROOT / "capstone-rag/evidence/s4-7a"
OFFICIAL_SOURCE_IDS = (
    "src_project_kis_adjusted_price_001",
    "src_project_opendart_status_quota_001",
    "src_project_ecos_pit_availability_001",
    "src_project_krx_service_coverage_001",
    "src_project_gold_futures_etf_132030_001",
)
OFFICIAL_CARD_RELATIVE_PATHS = tuple(f"{source_id}.md" for source_id in OFFICIAL_SOURCE_IDS)
_EXPECTED_INSTITUTIONS = {
    "src_project_kis_adjusted_price_001": "kis",
    "src_project_opendart_status_quota_001": "opendart",
    "src_project_ecos_pit_availability_001": "ecos",
    "src_project_krx_service_coverage_001": "krx",
    "src_project_gold_futures_etf_132030_001": "samsungfund",
}
_EXPECTED_CANONICAL_URLS = {
    "src_project_kis_adjusted_price_001": (
        "https://github.com/koreainvestment/open-trading-api/blob/"
        "b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/"
        "inquire_daily_itemchartprice/inquire_daily_itemchartprice.py"
    ),
    "src_project_opendart_status_quota_001": (
        "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052"
    ),
    "src_project_ecos_pit_availability_001": "https://ecos.bok.or.kr/api/",
    "src_project_krx_service_coverage_001": (
        "https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd"
    ),
    "src_project_gold_futures_etf_132030_001": (
        "https://www.samsungfund.com/etf/product/view.do?id=2ETF24"
    ),
}
_EXPECTED_UPSTREAM_SOURCE_IDS = {
    "src_project_kis_adjusted_price_001": ("src_kis_marketdata_daily_001",),
    "src_project_opendart_status_quota_001": ("src_opendart_major_report_001",),
    "src_project_ecos_pit_availability_001": (
        "src_ecos_api_overview_001",
        "src_ecos_statistic_search_001",
    ),
    "src_project_krx_service_coverage_001": (
        "src_krx_openapi_service_catalog_001",
        "src_krx_openapi_terms_001",
    ),
    "src_project_gold_futures_etf_132030_001": ("src_samsungfund_gold_futures_etf_001",),
}
_EXPECTED_EVIDENCE_PATHS = {
    "src_project_kis_adjusted_price_001": "kis-adjusted-price.txt",
    "src_project_opendart_status_quota_001": "opendart-status-quota.txt",
    "src_project_ecos_pit_availability_001": "ecos-statistic-search-output-fields.txt",
    "src_project_krx_service_coverage_001": "krx-service-coverage.txt",
    "src_project_gold_futures_etf_132030_001": "gold-futures-etf-132030.txt",
}
_EXPECTED_CAPTURE_KINDS = {
    "src_project_kis_adjusted_price_001": "OFFICIAL_PINNED_SOURCE_BOUNDED_EXCERPT",
    "src_project_opendart_status_quota_001": "OFFICIAL_GUIDE_BOUNDED_EXCERPT",
    "src_project_ecos_pit_availability_001": "READ_ONLY_BROWSER_DOM_FIELD_LIST",
    "src_project_krx_service_coverage_001": "OFFICIAL_SERVICE_CATALOG_BOUNDED_EXCERPT",
    "src_project_gold_futures_etf_132030_001": "OFFICIAL_PRODUCT_PAGE_BOUNDED_EXCERPT",
}
_MANIFEST_FIELDS = {
    "canonicalization",
    "evidence",
    "evidenceCount",
    "manifestId",
    "rawEvidenceTracked",
    "schemaVersion",
}
_EVIDENCE_FIELDS = {
    "bytes",
    "canonicalUrl",
    "canonicalUrlSha256",
    "captureKind",
    "evidencePath",
    "institution",
    "licenseDecision",
    "locator",
    "producer",
    "sha256",
    "sourceCardContentSha256",
    "sourceId",
    "verifiedAt",
}
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_EVIDENCE_BYTES = 32_768


class OfficialEvidenceError(ValueError):
    """S4.7A official evidence/card batch가 fixed completion 계약을 위반할 때 발생한다."""


@dataclass(frozen=True)
class OfficialEvidenceReceipt:
    """원문을 노출하지 않는 exact-five offline verification receipt."""

    source_ids: tuple[str, ...]
    evidence_sha256: tuple[str, ...]
    source_card_content_sha256: tuple[str, ...]

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_sha256)

    @property
    def card_count(self) -> int:
        return len(self.source_card_content_sha256)


def validate_official_evidence_manifest(
    *,
    manifest_path: Path = OFFICIAL_EVIDENCE_MANIFEST_PATH,
    evidence_root: Path = OFFICIAL_EVIDENCE_ROOT,
    card_root: Path = OFFICIAL_SOURCE_CARD_ROOT,
    schema_path: Path = RAG_SOURCE_CARD_SCHEMA_PATH,
    registry: RagSourceRegistry | None = None,
    bind_local_artifacts: bool = True,
) -> OfficialEvidenceReceipt:
    """tracked manifest를 exact 5 local-only card와 bounded evidence bytes에 결합한다.

    기본 card 경로는 Git 작업트리 밖의 fixed local root이며 network/provider 호출을 만들지 않는다.
    경로 인자는 test fixture 격리를 위한 내부 API이고 CLI에는 노출하지 않는다.
    """

    manifest = _load_manifest(manifest_path)
    evidence_rows = _validate_manifest_semantics(manifest)
    receipt = OfficialEvidenceReceipt(
        source_ids=tuple(_require_text(row, "sourceId") for row in evidence_rows),
        evidence_sha256=tuple(_require_hash(row, "sha256") for row in evidence_rows),
        source_card_content_sha256=tuple(
            _require_hash(row, "sourceCardContentSha256") for row in evidence_rows
        ),
    )
    if not bind_local_artifacts:
        return receipt

    try:
        cards = load_rag_source_cards(
            approved_root=card_root,
            relative_paths=OFFICIAL_CARD_RELATIVE_PATHS,
            schema_path=schema_path,
            registry=registry,
        )
    except RagSourceCardError as error:
        raise OfficialEvidenceError("S4.7A official source-card batch is invalid.") from error
    if tuple(card.source_id for card in cards) != OFFICIAL_SOURCE_IDS:
        raise OfficialEvidenceError("S4.7A official source-card identities drifted.")
    if any(card.status != "VERIFIED" for card in cards):
        raise OfficialEvidenceError("S4.7A completion requires five VERIFIED source cards.")

    cards_by_source_id = {card.source_id: card for card in cards}
    for row in evidence_rows:
        source_id = _require_text(row, "sourceId")
        relative_evidence_path = _EXPECTED_EVIDENCE_PATHS[source_id]
        try:
            evidence = read_approved_regular_file(
                approved_root=evidence_root,
                relative_path=relative_evidence_path,
                max_bytes=_MAX_EVIDENCE_BYTES,
            )
        except RagSafeIoError as error:
            raise OfficialEvidenceError("S4.7A official evidence safe read failed.") from error
        _validate_canonical_evidence_bytes(evidence.content)
        if len(evidence.content) != _require_int(row, "bytes"):
            raise OfficialEvidenceError("S4.7A official evidence byte count mismatched.")
        if evidence.content_sha256 != _require_hash(row, "sha256"):
            raise OfficialEvidenceError("S4.7A official evidence content hash mismatched.")

        card = cards_by_source_id[source_id]
        if (
            card.content_sha256 != _require_hash(row, "sourceCardContentSha256")
            or card.institution != _require_text(row, "institution")
            or card.canonical_url != _require_text(row, "canonicalUrl")
            or card.canonical_url != _EXPECTED_CANONICAL_URLS[source_id]
            or card.canonical_url_sha256 != _require_hash(row, "canonicalUrlSha256")
            or card.evidence_content_sha256 != _require_hash(row, "sha256")
            or card.upstream_source_ids != _EXPECTED_UPSTREAM_SOURCE_IDS[source_id]
            or _format_utc(card.verified_at) != _require_text(row, "verifiedAt")
        ):
            raise OfficialEvidenceError(
                "S4.7A source card is not bound to its manifest evidence receipt."
            )
    return receipt


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        if not text.endswith("\n") or "\r" in text or unicodedata.normalize("NFC", text) != text:
            raise OfficialEvidenceError(
                "S4.7A official evidence manifest canonicalization drifted."
            )
        manifest = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OfficialEvidenceError(
            "S4.7A official evidence manifest must be strict UTF-8 JSON."
        ) from error
    if not isinstance(manifest, dict):
        raise OfficialEvidenceError("S4.7A official evidence manifest must be an object.")
    return manifest


def _validate_manifest_semantics(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    if set(manifest) != _MANIFEST_FIELDS:
        raise OfficialEvidenceError("S4.7A official evidence manifest root drifted.")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("manifestId") != "s4-7a-official-evidence/v1"
        or manifest.get("canonicalization") != "UTF-8 NFC LF WITH_FINAL_NEWLINE"
        or manifest.get("rawEvidenceTracked") is not False
        or manifest.get("evidenceCount") != len(OFFICIAL_SOURCE_IDS)
    ):
        raise OfficialEvidenceError("S4.7A official evidence manifest constants drifted.")
    rows = manifest.get("evidence")
    if not isinstance(rows, list) or len(rows) != len(OFFICIAL_SOURCE_IDS):
        raise OfficialEvidenceError("S4.7A official evidence manifest must contain exact five.")
    if not all(isinstance(row, dict) for row in rows):
        raise OfficialEvidenceError("S4.7A official evidence entries must be objects.")
    typed_rows = tuple(cast(dict[str, Any], row) for row in rows)
    if tuple(_require_text(row, "sourceId") for row in typed_rows) != OFFICIAL_SOURCE_IDS:
        raise OfficialEvidenceError("S4.7A official evidence source IDs/order drifted.")

    for row in typed_rows:
        source_id = _require_text(row, "sourceId")
        expected_fields = set(_EVIDENCE_FIELDS)
        if source_id == "src_project_ecos_pit_availability_001":
            expected_fields.add("scopeDecision")
        if set(row) != expected_fields:
            raise OfficialEvidenceError("S4.7A official evidence entry fields drifted.")
        if (
            _require_text(row, "institution") != _EXPECTED_INSTITUTIONS[source_id]
            or _require_text(row, "captureKind") != _EXPECTED_CAPTURE_KINDS[source_id]
            or _require_text(row, "licenseDecision") != "REFERENCE_ONLY_NO_EXTERNAL_PROCESSING"
            or _require_text(row, "producer") != "s4-7a-read-only-evidence-capture"
        ):
            raise OfficialEvidenceError("S4.7A official evidence authority metadata drifted.")
        expected_path = f"capstone-rag/evidence/s4-7a/{_EXPECTED_EVIDENCE_PATHS[source_id]}"
        if _require_text(row, "evidencePath") != expected_path:
            raise OfficialEvidenceError("S4.7A official evidence path drifted.")
        byte_count = _require_int(row, "bytes")
        if byte_count < 1 or byte_count > _MAX_EVIDENCE_BYTES:
            raise OfficialEvidenceError("S4.7A official evidence byte count is out of bounds.")
        canonical_url = _require_text(row, "canonicalUrl")
        if canonical_url != _EXPECTED_CANONICAL_URLS[source_id]:
            raise OfficialEvidenceError("S4.7A official evidence canonical URL drifted.")
        try:
            validate_canonical_https_url(canonical_url)
        except RagSourceRegistryError as error:
            raise OfficialEvidenceError("S4.7A official evidence URL is unsafe.") from error
        if hashlib.sha256(canonical_url.encode("utf-8")).hexdigest() != _require_hash(
            row,
            "canonicalUrlSha256",
        ):
            raise OfficialEvidenceError("S4.7A official evidence URL hash mismatched.")
        _require_hash(row, "sha256")
        _require_hash(row, "sourceCardContentSha256")
        if len(_require_text(row, "locator")) > 500:
            raise OfficialEvidenceError("S4.7A official evidence locator exceeds its bound.")
        _require_utc_datetime(row.get("verifiedAt"))
        if (
            source_id == "src_project_ecos_pit_availability_001"
            and row.get("scopeDecision") != "PIT_SUPPORT_NOT_PROVEN"
        ):
            raise OfficialEvidenceError("S4.7A ECOS scope decision drifted.")
    return typed_rows


def _validate_canonical_evidence_bytes(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OfficialEvidenceError("S4.7A evidence must be strict UTF-8.") from error
    if (
        not text
        or "\r" in text
        or not text.endswith("\n")
        or unicodedata.normalize("NFC", text) != text
    ):
        raise OfficialEvidenceError("S4.7A evidence canonicalization drifted.")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OfficialEvidenceError("S4.7A official evidence manifest has duplicate keys.")
        result[key] = value
    return result


def _require_text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item or item != item.strip():
        raise OfficialEvidenceError(f"S4.7A manifest {field} must be canonical text.")
    return item


def _require_hash(value: Mapping[str, Any], field: str) -> str:
    item = _require_text(value, field)
    if _HASH_PATTERN.fullmatch(item) is None:
        raise OfficialEvidenceError(f"S4.7A manifest {field} must be a lowercase SHA-256.")
    return item


def _require_int(value: Mapping[str, Any], field: str) -> int:
    item = value.get(field)
    if type(item) is not int:
        raise OfficialEvidenceError(f"S4.7A manifest {field} must be an integer.")
    return item


def _require_utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OfficialEvidenceError("S4.7A verifiedAt must be canonical UTC.")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise OfficialEvidenceError("S4.7A verifiedAt must be canonical UTC.") from error
    if parsed.tzinfo != UTC:
        raise OfficialEvidenceError("S4.7A verifiedAt must use UTC.")
    if _format_utc(parsed) != value:
        raise OfficialEvidenceError("S4.7A verifiedAt must use canonical second precision.")
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the fixed S4.7A official evidence and source-card batch offline.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """fixed approved roots만 읽고 exact-five bounded receipt를 출력한다."""

    args = _build_parser().parse_args(argv)
    try:
        receipt = validate_official_evidence_manifest()
    except OfficialEvidenceError as error:
        print(
            json.dumps(
                {"error": "S4_7A_OFFICIAL_EVIDENCE_INVALID", "message": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    summary = {
        "cardCount": receipt.card_count,
        "evidenceCount": receipt.evidence_count,
        "sourceIds": list(receipt.source_ids),
        "evidenceSha256": list(receipt.evidence_sha256),
        "sourceCardContentSha256": list(receipt.source_card_content_sha256),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("S4_7A_OFFICIAL_SOURCE_CARDS_5_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
