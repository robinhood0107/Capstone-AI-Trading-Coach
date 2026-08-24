from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.data.calendar.errors import PrivacyProjectionError

_FORBIDDEN_KEYS = frozenset(
    {
        "reporter",
        "reportername",
        "repror",
        "corpname",
        "personname",
        "name",
        "address",
        "adres",
        "phone",
        "phoneno",
        "phnno",
        "fax",
        "faxno",
        "jurirno",
        "bizrno",
        "raw",
        "rawresponse",
        "rawbody",
        "headers",
        "requesturl",
        "apikey",
        "crtfckey",
        "secret",
        "token",
        "authorization",
        "credential",
        "accountnumber",
    }
)
_FORBIDDEN_TEXT_MARKERS = (
    "crtfc_key=",
    "api_key=",
    "authorization:",
    "bearer ",
    "x-api-key",
    "raw_response",
    "request_url",
)
_MAX_SANITIZED_DEPTH = 32
_MAX_SANITIZED_NODES = 10_000
_MAX_TEXT_CHARS = 4_096


@dataclass(frozen=True)
class OwnershipProjection:
    """DS004에서 corp/role/date/share 수치만 남긴 PII-free canonical projection이다."""

    corp_code: str
    role_category: str
    occurred_on: date
    share_count: int | None
    share_ratio_bps: int | None

    def as_dict(self) -> dict[str, object]:
        """DB/Kafka/artifact 경계가 같은 allowlist를 재사용하도록 고정 field만 반환한다."""
        return {
            "corp_code": self.corp_code,
            "role_category": self.role_category,
            "occurred_on": self.occurred_on.isoformat(),
            "share_count": self.share_count,
            "share_ratio_bps": self.share_ratio_bps,
        }


def project_ds004_ownership(source_type: str, row: dict[str, Any]) -> OwnershipProjection:
    """provider row를 materialize하기 전에 DS004 allowlist projection과 numeric validation을 수행한다."""
    corp_code = _corp_code(row.get("corp_code"))
    occurred_on = _date(row.get("rcept_dt"))
    if source_type == "majorstock":
        role_category = "MAJOR_HOLDER"
        share_count = _optional_int(row.get("stkqy"))
        share_ratio_bps = _optional_ratio_bps(row.get("stkrt"))
    elif source_type == "elestock":
        if row.get("isu_exctv_rgist_at") == "Y":
            role_category = "REGISTERED_EXECUTIVE"
        elif row.get("isu_main_shrholdr") == "Y":
            role_category = "MAIN_SHAREHOLDER"
        else:
            role_category = "DISCLOSED_HOLDER"
        share_count = _optional_int(row.get("sp_stock_lmp_cnt"))
        share_ratio_bps = _optional_ratio_bps(row.get("sp_stock_lmp_rate"))
    else:
        raise PrivacyProjectionError("unsupported DS004 structured source")
    projection = OwnershipProjection(
        corp_code=corp_code,
        role_category=role_category,
        occurred_on=occurred_on,
        share_count=share_count,
        share_ratio_bps=share_ratio_bps,
    )
    assert_sanitized_payload(projection.as_dict())
    return projection


def assert_sanitized_payload(payload: object) -> None:
    """PII/secret/query/raw/header가 observation·canonical 경계로 넘어오기 전에 fail-closed한다."""
    stack: list[tuple[object, int]] = [(payload, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_SANITIZED_NODES or depth > _MAX_SANITIZED_DEPTH:
            raise PrivacyProjectionError("sanitized payload exceeded structural bounds")
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = "".join(
                    character for character in str(key).lower() if character.isalnum()
                )
                if normalized in _FORBIDDEN_KEYS:
                    raise PrivacyProjectionError("sanitized payload contains a forbidden field")
                stack.append((child, depth + 1))
        elif isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            if len(value) > _MAX_TEXT_CHARS:
                raise PrivacyProjectionError("sanitized payload text exceeded the limit")
            lowered = value.lower()
            if any(marker in lowered for marker in _FORBIDDEN_TEXT_MARKERS):
                raise PrivacyProjectionError("sanitized payload contains secret or raw material")
        elif value is not None and type(value) not in {bool, int, float, Decimal, date, datetime}:
            raise PrivacyProjectionError("sanitized payload contains an unsupported value type")


def sanitize_source_ref(*, source_id: str, stable_key: str) -> str:
    """public/source link에 provider identity나 natural key 대신 purpose-bound opaque hash만 둔다."""
    if not source_id or not stable_key:
        raise PrivacyProjectionError("source ref inputs are required")
    return hashlib.sha256(f"s1.6-source-ref\0{source_id}\0{stable_key}".encode()).hexdigest()


def _corp_code(value: object) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
        raise PrivacyProjectionError("DS004 corp_code is invalid")
    return value


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise PrivacyProjectionError("DS004 event date is invalid")
    for date_format in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise PrivacyProjectionError("DS004 event date is invalid")


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise PrivacyProjectionError("DS004 share count is invalid")
    try:
        parsed = int(str(value).replace(",", "").strip())
    except ValueError:
        raise PrivacyProjectionError("DS004 share count is invalid") from None
    if parsed < 0:
        raise PrivacyProjectionError("DS004 share count is invalid")
    return parsed


def _optional_ratio_bps(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        ratio = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise PrivacyProjectionError("DS004 share ratio is invalid") from None
    scaled = ratio * 100
    if ratio < 0 or ratio > 100 or scaled != scaled.to_integral_value():
        raise PrivacyProjectionError("DS004 share ratio is invalid")
    return int(scaled)
