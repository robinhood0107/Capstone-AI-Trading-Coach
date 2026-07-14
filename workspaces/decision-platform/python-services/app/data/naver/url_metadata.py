from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PERCENT_ESCAPE = re.compile(r"%(?:[0-9A-Fa-f]{2})")
_SENSITIVE_QUERY_MARKERS = (
    "apikey",
    "auth",
    "credential",
    "key",
    "password",
    "passwd",
    "secret",
    "signature",
    "token",
)
_DEFAULT_MAX_CODE_POINTS = 2_048
_DEFAULT_MAX_UTF8_BYTES = 8_192


def normalize_metadata_url(
    value: str,
    *,
    max_code_points: int = _DEFAULT_MAX_CODE_POINTS,
    max_utf8_bytes: int = _DEFAULT_MAX_UTF8_BYTES,
) -> str | None:
    """기사 URL을 fetch하지 않고 표시용 HTTP(S) metadata로만 정규화한다.

    userinfo·control·local/non-global literal host와 credential query를 제거하거나 거부한다.
    """
    if not isinstance(value, str) or max_code_points <= 0 or max_utf8_bytes <= 0:
        return None
    if not value or not _within_bounds(value, max_code_points, max_utf8_bytes):
        return None
    if _contains_unsafe_character(value) or _has_invalid_percent_escape(value):
        return None
    decoded = unquote(value, errors="replace")
    if _contains_unsafe_character(decoded) or "\ufffd" in decoded:
        return None

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    normalized_host = _normalize_host(parsed.hostname)
    if normalized_host is None:
        return None
    netloc = _format_netloc(normalized_host, port)
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = _sanitize_query(parsed.query)
    normalized = urlunsplit((parsed.scheme, netloc, path, query, ""))
    if not _within_bounds(normalized, max_code_points, max_utf8_bytes):
        return None
    return normalized


def _normalize_host(host: str | None) -> str | None:
    if not host:
        return None
    candidate = host.rstrip(".").lower()
    if candidate == "localhost" or candidate.endswith(".local") or candidate.isdigit():
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        try:
            ascii_host = candidate.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        if len(ascii_host) > 253:
            return None
        labels = ascii_host.split(".")
        if len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
            return None
        return ascii_host
    return address.compressed if address.is_global else None


def _format_netloc(host: str, port: int | None) -> str:
    formatted_host = f"[{host}]" if ":" in host else host
    return formatted_host if port is None else f"{formatted_host}:{port}"


def _sanitize_query(query: str) -> str:
    if not query:
        return ""
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        return ""
    safe_pairs = [(name, value) for name, value in pairs if not _is_sensitive_query_name(name)]
    return urlencode(safe_pairs, doseq=True)


def _is_sensitive_query_name(name: str) -> bool:
    normalized = "".join(character for character in name.lower() if character.isalnum())
    return any(marker in normalized for marker in _SENSITIVE_QUERY_MARKERS)


def _contains_unsafe_character(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def _has_invalid_percent_escape(value: str) -> bool:
    without_valid_escapes = _PERCENT_ESCAPE.sub("", value)
    return "%" in without_valid_escapes


def _within_bounds(value: str, max_code_points: int, max_utf8_bytes: int) -> bool:
    return len(value) <= max_code_points and len(value.encode("utf-8")) <= max_utf8_bytes
