from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from app.data.naver.sanitizer import NaverSanitizationError, sanitize_news_text


_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "naver"


def test_sanitizer_strips_tags_and_decodes_entities_once() -> None:
    sanitized = sanitize_news_text(
        "  <b>합성&nbsp;전자</b> &amp; <i>전망</i>  ",
        max_code_points=512,
        max_utf8_bytes=2_048,
    )

    assert sanitized == "합성 전자 & 전망"
    assert "<" not in sanitized
    assert ">" not in sanitized


def test_double_encoded_markup_does_not_reappear_as_live_tag() -> None:
    sanitized = sanitize_news_text(
        "&amp;lt;b&amp;gt;합성 위험&amp;lt;/b&amp;gt;",
        max_code_points=512,
        max_utf8_bytes=2_048,
    )

    assert sanitized == "&lt;b&gt;합성 위험&lt;/b&gt;"
    assert "<b>" not in sanitized


def test_single_encoded_markup_is_removed_after_the_one_allowed_decode() -> None:
    sanitized = sanitize_news_text(
        "&lt;img src=x onerror=alert(1)&gt;합성 뉴스",
        max_code_points=512,
        max_utf8_bytes=2_048,
    )

    assert sanitized == "합성 뉴스"
    assert "<" not in sanitized
    assert ">" not in sanitized


def test_control_bidi_whitespace_and_unicode_are_normalized() -> None:
    raw = "  A\u030a\t주식\x00\u202e  뉴스\u0085 "

    sanitized = sanitize_news_text(raw, max_code_points=512, max_utf8_bytes=2_048)

    assert sanitized == "Å 주식 뉴스"
    assert unicodedata.is_normalized("NFC", sanitized)
    assert all(unicodedata.category(character) not in {"Cc", "Cf"} for character in sanitized)


def test_unsafe_metadata_fixture_is_sanitized_without_raw_markup_or_controls() -> None:
    payload = json.loads((_FIXTURE_DIR / "unsafe_metadata_cases.json").read_text(encoding="utf-8"))

    sanitized = sanitize_news_text(
        payload["unsafeText"],
        max_code_points=512,
        max_utf8_bytes=2_048,
    )

    assert sanitized == "합성 뉴스 fixture"
    assert "<" not in sanitized
    assert all(unicodedata.category(character) not in {"Cc", "Cf"} for character in sanitized)


def test_malformed_markup_is_plain_text_and_never_preserves_executable_tags() -> None:
    sanitized = sanitize_news_text(
        "<b>합성</b><script>alert(1)</script><!--fixture--><i broken>뉴스",
        max_code_points=512,
        max_utf8_bytes=2_048,
    )

    assert sanitized == "합성 alert(1) 뉴스"
    assert all(marker not in sanitized.lower() for marker in ("<script", "<!--", "<i"))


@pytest.mark.parametrize(
    ("value", "max_code_points", "max_utf8_bytes"),
    [
        ("x" * 513, 512, 2_048),
        ("한글가", 512, 8),
        ("\x00\u202e", 512, 2_048),
    ],
)
def test_empty_or_oversized_text_fails_without_echoing_input(
    value: str,
    max_code_points: int,
    max_utf8_bytes: int,
) -> None:
    with pytest.raises(NaverSanitizationError) as exc_info:
        sanitize_news_text(
            value,
            max_code_points=max_code_points,
            max_utf8_bytes=max_utf8_bytes,
        )

    assert value not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_lone_surrogate_fails_with_stable_sanitization_error() -> None:
    with pytest.raises(NaverSanitizationError) as exc_info:
        sanitize_news_text(
            "synthetic\ud800value",
            max_code_points=512,
            max_utf8_bytes=2_048,
        )

    assert str(exc_info.value) == "invalid_naver_text"
    assert not hasattr(exc_info.value, "object")
    assert exc_info.value.__cause__ is None
