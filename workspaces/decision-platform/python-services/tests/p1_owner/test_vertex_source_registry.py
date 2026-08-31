"""등록 출처 판정은 host가 하고, 모델 주장은 근거가 되지 못한다."""

from __future__ import annotations

import pytest

from app.p1_owner.vertex_source_registry import (
    registered_source_for_uri,
    registered_sources,
)


def test_catalog_covers_official_and_independent_sources() -> None:
    catalog = registered_sources()
    types = {source_type for _, source_type in catalog.values()}

    assert types == {"OFFICIAL_PRIMARY", "REGISTERED_INDEPENDENT"}
    assert catalog["dart.fss.or.kr"][1] == "OFFICIAL_PRIMARY"
    assert catalog["reuters.com"][1] == "REGISTERED_INDEPENDENT"


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1", "src_official_dart"),
        # 하위 domain은 인정한다.
        ("https://news.reuters.com/article/x", "src_press_reuters"),
        # 더 구체적인 등록 domain이 상위 domain을 이긴다.
        ("https://kind.krx.co.kr/disclosure/x", "src_official_kind"),
    ],
)
def test_registered_uris_resolve_to_their_source(uri: str, expected: str) -> None:
    resolved = registered_source_for_uri(uri)

    assert resolved is not None
    assert resolved[0] == expected


@pytest.mark.parametrize(
    "uri",
    [
        # 등록되지 않은 domain
        "https://example.com/article",
        # 접미사만 우연히 겹치는 domain은 통과하지 못한다.
        "https://notreuters.com/article",
        "https://evil-krx.co.kr/x",
        # http, credential 삽입, 형식 오류
        "http://reuters.com/article",
        "https://user:pass@reuters.com/article",
        "not-a-uri",
        "",
    ],
)
def test_unregistered_or_unsafe_uris_are_not_evidence(uri: str) -> None:
    assert registered_source_for_uri(uri) is None
