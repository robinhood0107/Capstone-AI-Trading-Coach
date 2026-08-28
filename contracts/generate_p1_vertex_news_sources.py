"""Generate the registered news source catalog the Vertex buy-veto host verifies against.

`app/p1_owner/vertex_veto.py`는 근거 하나하나에 `sourceType`이 OFFICIAL_PRIMARY 또는
REGISTERED_INDEPENDENT인지, 그리고 그 사실을 신뢰 host가 관측했는지를 요구한다. 모델이 스스로
"공식 출처"라고 주장하는 것은 근거가 아니므로, host가 grounding URI를 이 카탈로그에 대조해
sourceType을 정한다. 카탈로그에 없는 domain은 근거로 세지 않는다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)
from contracts.generated_artifact_io import write_generated_path  # noqa: E402

CATALOG_PATH: Final[str] = "contracts/catalogs/p1-vertex-news-sources.v1.json"

# 규제기관·거래소·공시 시스템만 OFFICIAL_PRIMARY다. 발행사 IR 페이지는 자기보고라 넣지 않는다.
OFFICIAL_PRIMARY: Final[tuple[tuple[str, str], ...]] = (
    ("dart.fss.or.kr", "src_official_dart"),
    ("opendart.fss.or.kr", "src_official_opendart"),
    ("fss.or.kr", "src_official_fss"),
    ("fsc.go.kr", "src_official_fsc"),
    ("krx.co.kr", "src_official_krx"),
    ("data.krx.co.kr", "src_official_krx_data"),
    ("kind.krx.co.kr", "src_official_kind"),
    ("bok.or.kr", "src_official_bok"),
    ("ecos.bok.or.kr", "src_official_ecos"),
    ("kostat.go.kr", "src_official_kostat"),
    ("moef.go.kr", "src_official_moef"),
)

# 편집 책임이 있는 통신·경제지만 REGISTERED_INDEPENDENT다. 커뮤니티·블로그·집계 사이트는 제외한다.
REGISTERED_INDEPENDENT: Final[tuple[tuple[str, str], ...]] = (
    ("yna.co.kr", "src_press_yonhap"),
    ("en.yna.co.kr", "src_press_yonhap_en"),
    ("einfomax.co.kr", "src_press_infomax"),
    ("hankyung.com", "src_press_hankyung"),
    ("mk.co.kr", "src_press_maeil"),
    ("biz.chosun.com", "src_press_chosunbiz"),
    ("edaily.co.kr", "src_press_edaily"),
    ("sedaily.com", "src_press_seoul_economic"),
    ("reuters.com", "src_press_reuters"),
    ("bloomberg.com", "src_press_bloomberg"),
    ("ft.com", "src_press_ft"),
    ("wsj.com", "src_press_wsj"),
    ("apnews.com", "src_press_ap"),
    ("nikkei.com", "src_press_nikkei"),
)


def build_catalog() -> dict[str, object]:
    entries = [
        {"domain": domain, "sourceId": source_id, "sourceType": "OFFICIAL_PRIMARY"}
        for domain, source_id in OFFICIAL_PRIMARY
    ] + [
        {"domain": domain, "sourceId": source_id, "sourceType": "REGISTERED_INDEPENDENT"}
        for domain, source_id in REGISTERED_INDEPENDENT
    ]
    entries.sort(key=lambda item: str(item["domain"]))
    return {
        "contractId": "p1-vertex-news-sources.v1",
        "officialPrimaryCount": len(OFFICIAL_PRIMARY),
        "registeredIndependentCount": len(REGISTERED_INDEPENDENT),
        "sources": entries,
        "unregisteredDomainsAreNotEvidence": True,
    }


def validate_catalog(catalog: dict[str, object]) -> None:
    sources = catalog["sources"]
    if not isinstance(sources, list) or not sources:
        raise ContractValidationError("vertex news source catalog is empty")
    domains: set[str] = set()
    identifiers: set[str] = set()
    for item in sources:
        if not isinstance(item, dict) or set(item) != {"domain", "sourceId", "sourceType"}:
            raise ContractValidationError("vertex news source entry shape is invalid")
        domain = str(item["domain"])
        source_id = str(item["sourceId"])
        if domain in domains or source_id in identifiers:
            raise ContractValidationError("vertex news source entry is duplicated")
        if domain != domain.lower() or domain.startswith(".") or "/" in domain or " " in domain:
            raise ContractValidationError("vertex news source domain is invalid")
        if "." not in domain:
            raise ContractValidationError("vertex news source domain is invalid")
        if not source_id.startswith(("src_official_", "src_press_")):
            raise ContractValidationError("vertex news source identifier is invalid")
        if item["sourceType"] not in {"OFFICIAL_PRIMARY", "REGISTERED_INDEPENDENT"}:
            raise ContractValidationError("vertex news source type is invalid")
        expected_prefix = (
            "src_official_" if item["sourceType"] == "OFFICIAL_PRIMARY" else "src_press_"
        )
        if not source_id.startswith(expected_prefix):
            raise ContractValidationError("vertex news source identifier prefix drifted")
        domains.add(domain)
        identifiers.add(source_id)


def build_outputs() -> dict[str, bytes]:
    catalog = build_catalog()
    validate_catalog(catalog)
    return {CATALOG_PATH: canonical_json_bytes(catalog)}


def load_catalog() -> dict[str, object]:
    return json.loads((ROOT / CATALOG_PATH).read_text(encoding="utf-8"))


def generate(*, check: bool) -> int:
    for relative, payload in build_outputs().items():
        path = ROOT / relative
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                print(
                    f"P1_VERTEX_NEWS_SOURCES=FAIL: generated catalog drifted: {relative}",
                    file=sys.stderr,
                )
                return 1
        else:
            write_generated_path(ROOT, path, payload)
    print("P1_VERTEX_NEWS_SOURCES=PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    return generate(check=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
