from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.rag.official_evidence import (
    OFFICIAL_SOURCE_IDS,
    OfficialEvidenceError,
    validate_official_evidence_manifest,
)
from app.rag.official_evidence import (
    main as official_evidence_main,
)

_VERIFIED_AT = "2026-07-30T05:07:41Z"
_SOURCE_FIXTURES = (
    (
        "src_project_kis_adjusted_price_001",
        "kis",
        "kis_adjusted_price",
        ("src_kis_marketdata_daily_001",),
        "OFFICIAL_API_DOCUMENTATION",
        (
            "https://github.com/koreainvestment/open-trading-api/blob/"
            "b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/"
            "inquire_daily_itemchartprice/inquire_daily_itemchartprice.py"
        ),
        "kis-adjusted-price.txt",
        "OFFICIAL_PINNED_SOURCE_BOUNDED_EXCERPT",
    ),
    (
        "src_project_opendart_status_quota_001",
        "opendart",
        "opendart_status_quota",
        ("src_opendart_major_report_001",),
        "OFFICIAL_API_DOCUMENTATION",
        "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052",
        "opendart-status-quota.txt",
        "OFFICIAL_GUIDE_BOUNDED_EXCERPT",
    ),
    (
        "src_project_ecos_pit_availability_001",
        "ecos",
        "ecos_pit_availability",
        ("src_ecos_api_overview_001", "src_ecos_statistic_search_001"),
        "OFFICIAL_API_DOCUMENTATION",
        "https://ecos.bok.or.kr/api/",
        "ecos-statistic-search-output-fields.txt",
        "READ_ONLY_BROWSER_DOM_FIELD_LIST",
    ),
    (
        "src_project_krx_service_coverage_001",
        "krx",
        "krx_service_coverage",
        ("src_krx_openapi_service_catalog_001", "src_krx_openapi_terms_001"),
        "OFFICIAL_SERVICE_DOCUMENTATION",
        "https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd",
        "krx-service-coverage.txt",
        "OFFICIAL_SERVICE_CATALOG_BOUNDED_EXCERPT",
    ),
    (
        "src_project_gold_futures_etf_132030_001",
        "samsungfund",
        "gold_futures_etf_132030",
        ("src_samsungfund_gold_futures_etf_001",),
        "OFFICIAL_PRODUCT_DOCUMENTATION",
        "https://www.samsungfund.com/etf/product/view.do?id=2ETF24",
        "gold-futures-etf-132030.txt",
        "OFFICIAL_PRODUCT_PAGE_BOUNDED_EXCERPT",
    ),
)


def _write_card(
    root: Path,
    *,
    source_id: str,
    institution: str,
    topic: str,
    upstream_source_ids: tuple[str, ...],
    evidence_class: str,
    canonical_url: str,
    evidence_sha256: str,
    status: str = "VERIFIED",
) -> str:
    claim = f"{institution} 공식 근거는 bounded provenance와 함께 사용해야 한다."
    question = f"{institution} 공식 근거 provenance는 어떻게 확인하나요?"
    sequence = source_id.rsplit("_", 1)[1]
    front_matter: dict[str, Any] = {
        "schemaVersion": "1",
        "sourceId": source_id,
        "cardId": f"card_{topic}_{sequence}",
        "title": f"{institution} 공식 근거 provenance",
        "institution": institution,
        "topic": topic,
        "sourceType": "PROJECT_SOURCE_CARD",
        "tier": "PROJECT",
        "accessLevel": "PUBLIC",
        "claim": claim,
        "evidenceClass": evidence_class,
        "status": status,
        "verifiedAt": _VERIFIED_AT,
        "accessNote": "공식 공개 페이지를 읽기 전용으로 확인했다.",
        "licenseNote": "원문 대신 bounded evidence hash와 locator만 보존한다.",
        "attribution": f"{institution} official source",
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": hashlib.sha256(canonical_url.encode()).hexdigest(),
        "evidenceContentSha256": evidence_sha256,
        "upstreamSourceIds": list(upstream_source_ids),
        "retentionOwner": "python-rag-corpus-privacy",
        "retentionDays": 365,
        "externalProcessingAllowed": False,
        "adoptedSession": "S4.7A",
        "contradicts": [],
        "modelAssumptions": [],
        "limitations": ["현재 확인한 공식 범위 밖의 의미를 보장하지 않는다."],
        "allowedUses": ["reference-only provenance 설명"],
        "forbiddenInferences": ["투자 판단이나 수익 보장으로 확대하지 않는다."],
        "representativeQuestions": [question],
    }
    yaml_text = yaml.safe_dump(
        front_matter,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    )
    body = (
        f"# Source Card: {front_matter['title']}\n"
        "## 핵심 claim\n"
        f"{claim}\n"
        "## 적용 범위와 전제\n"
        "현재 확인한 공식 locator와 bounded evidence에만 적용한다.\n"
        "## 프로젝트 적용\n"
        f"{question}\n"
        "질문 응답에는 provenance를 함께 제시한다.\n"
        "## 한계와 반례\n"
        "향후 문서 변경이나 범위 밖 의미는 보장하지 않는다.\n"
        "## 허용 사용\n"
        "reference-only 설명에만 사용한다.\n"
        "## 금지 추론\n"
        "투자 판단이나 실시간 값으로 확대하지 않는다.\n"
        "## 근거 위치\n"
        "official locator와 bounded hash를 사용한다.\n"
    )
    payload = f"---\n{yaml_text}---\n{body}"
    (root / f"{source_id}.md").write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode()).hexdigest()


def _write_bound_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    card_root = tmp_path / "cards"
    evidence_root = tmp_path / "evidence"
    card_root.mkdir()
    evidence_root.mkdir()
    rows: list[dict[str, Any]] = []
    for (
        source_id,
        institution,
        topic,
        upstream_source_ids,
        evidence_class,
        canonical_url,
        evidence_filename,
        capture_kind,
    ) in _SOURCE_FIXTURES:
        evidence = f"bounded official evidence for {source_id}\n".encode()
        (evidence_root / evidence_filename).write_bytes(evidence)
        evidence_hash = hashlib.sha256(evidence).hexdigest()
        card_hash = _write_card(
            card_root,
            source_id=source_id,
            institution=institution,
            topic=topic,
            upstream_source_ids=upstream_source_ids,
            evidence_class=evidence_class,
            canonical_url=canonical_url,
            evidence_sha256=evidence_hash,
        )
        row: dict[str, Any] = {
            "bytes": len(evidence),
            "canonicalUrl": canonical_url,
            "canonicalUrlSha256": hashlib.sha256(canonical_url.encode()).hexdigest(),
            "captureKind": capture_kind,
            "evidencePath": f"capstone-rag/evidence/s4-7a/{evidence_filename}",
            "institution": institution,
            "licenseDecision": "REFERENCE_ONLY_NO_EXTERNAL_PROCESSING",
            "locator": f"{institution} bounded fixture locator",
            "producer": "s4-7a-read-only-evidence-capture",
            "sha256": evidence_hash,
            "sourceCardContentSha256": card_hash,
            "sourceId": source_id,
            "verifiedAt": _VERIFIED_AT,
        }
        if institution == "ecos":
            row["scopeDecision"] = "PIT_SUPPORT_NOT_PROVEN"
        rows.append(row)
    manifest = {
        "canonicalization": "UTF-8 NFC LF WITH_FINAL_NEWLINE",
        "evidence": rows,
        "evidenceCount": 5,
        "manifestId": "s4-7a-official-evidence/v1",
        "rawEvidenceTracked": False,
        "schemaVersion": 1,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path, evidence_root, card_root


def test_tracked_official_manifest_locks_exact_five_semantics() -> None:
    receipt = validate_official_evidence_manifest(bind_local_artifacts=False)

    assert receipt.source_ids == OFFICIAL_SOURCE_IDS
    assert receipt.evidence_count == 5
    assert receipt.card_count == 5


def test_official_evidence_validator_binds_exact_cards_and_bytes(tmp_path: Path) -> None:
    manifest_path, evidence_root, card_root = _write_bound_fixture(tmp_path)

    receipt = validate_official_evidence_manifest(
        manifest_path=manifest_path,
        evidence_root=evidence_root,
        card_root=card_root,
    )

    assert receipt.source_ids == OFFICIAL_SOURCE_IDS
    assert receipt.evidence_count == receipt.card_count == 5


def test_official_evidence_validator_rejects_non_verified_card(tmp_path: Path) -> None:
    manifest_path, evidence_root, card_root = _write_bound_fixture(tmp_path)
    source = _SOURCE_FIXTURES[0]
    evidence = (evidence_root / source[6]).read_bytes()
    _write_card(
        card_root,
        source_id=source[0],
        institution=source[1],
        topic=source[2],
        upstream_source_ids=source[3],
        evidence_class=source[4],
        canonical_url=source[5],
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        status="BLOCKED_EVIDENCE",
    )

    with pytest.raises(OfficialEvidenceError):
        validate_official_evidence_manifest(
            manifest_path=manifest_path,
            evidence_root=evidence_root,
            card_root=card_root,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rawEvidenceTracked", True),
        ("evidenceCount", 4),
        ("manifestId", "s4-7a-official-evidence/v2"),
    ],
)
def test_official_evidence_validator_rejects_manifest_drift(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    manifest_path, _, _ = _write_bound_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OfficialEvidenceError):
        validate_official_evidence_manifest(
            manifest_path=manifest_path,
            bind_local_artifacts=False,
        )


def test_official_evidence_validator_rejects_canonical_url_drift(tmp_path: Path) -> None:
    manifest_path, _, _ = _write_bound_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    drifted_url = "https://example.com/official-looking"
    manifest["evidence"][0]["canonicalUrl"] = drifted_url
    manifest["evidence"][0]["canonicalUrlSha256"] = hashlib.sha256(drifted_url.encode()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OfficialEvidenceError, match=r"canonical URL drifted"):
        validate_official_evidence_manifest(
            manifest_path=manifest_path,
            bind_local_artifacts=False,
        )


def test_official_evidence_validator_rejects_wrong_upstream_binding(
    tmp_path: Path,
) -> None:
    manifest_path, evidence_root, card_root = _write_bound_fixture(tmp_path)
    source = _SOURCE_FIXTURES[0]
    evidence = (evidence_root / source[6]).read_bytes()
    card_hash = _write_card(
        card_root,
        source_id=source[0],
        institution=source[1],
        topic=source[2],
        upstream_source_ids=("src_kis_trading_cash_order_001",),
        evidence_class=source[4],
        canonical_url=source[5],
        evidence_sha256=hashlib.sha256(evidence).hexdigest(),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence"][0]["sourceCardContentSha256"] = card_hash
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OfficialEvidenceError, match=r"not bound"):
        validate_official_evidence_manifest(
            manifest_path=manifest_path,
            evidence_root=evidence_root,
            card_root=card_root,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (b"changed official evidence with same byte count\n", "content hash"),
        (b"short\n", "byte count"),
        ("Cafe\u0301 evidence\n".encode("utf-8"), "canonicalization"),
        (b"noncanonical\r\n", "canonicalization"),
        (b"missing-final-newline", "canonicalization"),
        (b"\xff\n", "strict UTF-8"),
    ],
)
def test_official_evidence_validator_rejects_evidence_byte_hash_and_text_drift(
    tmp_path: Path,
    mutation: bytes,
    message: str,
) -> None:
    manifest_path, evidence_root, card_root = _write_bound_fixture(tmp_path)
    first_evidence = evidence_root / _SOURCE_FIXTURES[0][6]
    if message == "content hash":
        original = first_evidence.read_bytes()
        mutation = (b"x" * (len(original) - 1)) + b"\n"
    first_evidence.write_bytes(mutation)

    with pytest.raises(OfficialEvidenceError, match=message):
        validate_official_evidence_manifest(
            manifest_path=manifest_path,
            evidence_root=evidence_root,
            card_root=card_root,
        )


def test_official_evidence_validator_rejects_non_exact_manifest_row_count(
    tmp_path: Path,
) -> None:
    manifest_path, _, _ = _write_bound_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence"] = manifest["evidence"][:-1]
    manifest["evidenceCount"] = 4
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OfficialEvidenceError, match=r"constants|exact five"):
        validate_official_evidence_manifest(
            manifest_path=manifest_path,
            bind_local_artifacts=False,
        )


def test_official_evidence_cli_rejects_operator_supplied_roots(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        official_evidence_main(["--manifest-path", str(tmp_path / "manifest.json")])
