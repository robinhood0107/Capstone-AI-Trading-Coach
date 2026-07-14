from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser


_WHITESPACE = re.compile(r"\s+")


class NaverSanitizationError(ValueError):
    """뉴스 텍스트가 저장 가능한 plain-text 계약을 만족하지 못했음을 값 비노출로 알린다."""


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(" ")

    def handle_comment(self, data: str) -> None:
        self.parts.append(" ")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(" ")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(" ")


def sanitize_news_text(
    value: str,
    *,
    max_code_points: int,
    max_utf8_bytes: int,
) -> str:
    """Naver title/description을 단 한 번 entity decode한 NFC plain text로 정규화한다.

    입력·출력 상한과 control/BiDi 제거를 함께 적용하며 실패 예외에는 원문을 넣지 않는다.
    """
    if max_code_points <= 0 or max_utf8_bytes <= 0:
        raise ValueError("sanitization_limit_invalid")
    if not isinstance(value, str) or not _within_bounds(value, max_code_points, max_utf8_bytes):
        raise NaverSanitizationError("invalid_naver_text")

    parser = _PlainTextParser()
    try:
        parser.feed(value)
        parser.close()
        # parser가 entity token을 보존한 뒤 여기서 한 번만 decode해 double decode를 차단한다.
        decoded_once = html.unescape("".join(parser.parts))
    except (AssertionError, RecursionError, ValueError):
        raise NaverSanitizationError("invalid_naver_text") from None

    without_controls = "".join(_safe_character(character) for character in decoded_once)
    normalized = unicodedata.normalize("NFC", without_controls)
    sanitized = _WHITESPACE.sub(" ", normalized).strip()
    if not sanitized or not _within_bounds(sanitized, max_code_points, max_utf8_bytes):
        raise NaverSanitizationError("invalid_naver_text")
    return sanitized


def _within_bounds(value: str, max_code_points: int, max_utf8_bytes: int) -> bool:
    return len(value) <= max_code_points and len(value.encode("utf-8")) <= max_utf8_bytes


def _safe_character(character: str) -> str:
    category = unicodedata.category(character)
    if category == "Cc":
        return " " if character.isspace() else ""
    if category == "Cf":
        return ""
    return character
