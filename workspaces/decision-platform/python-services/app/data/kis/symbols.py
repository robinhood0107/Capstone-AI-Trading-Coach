from __future__ import annotations

import re

_SYMBOL_PATTERN = re.compile(r"[0-9]{6}")


def normalize_symbol(value: str) -> str:
    """종목 식별자는 경로·fixture·provider 요청에 쓰이므로 ASCII 숫자 6자리만 허용한다."""
    normalized = value.strip()
    if not _SYMBOL_PATTERN.fullmatch(normalized):
        raise ValueError("KIS symbol must contain exactly six digits")
    return normalized
