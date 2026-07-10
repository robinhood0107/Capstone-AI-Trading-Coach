from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.data.opendart.models import NormalizedStatus, RawObservation


def request_fingerprint(method: str, path: str, params: dict[str, str]) -> str:
    """요청 값과 인증·계좌성 key 이름을 모두 버려 보안정보의 존재도 노출하지 않는다."""
    clean_path = path.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    if not re.fullmatch(r"/api/[A-Za-z][A-Za-z0-9]*\.(?:json|xml)", clean_path):
        clean_path = "/api/[redacted]"
    keys = ",".join(sorted(key for key in params if not _is_sensitive_key(key)))
    return f"{method.upper()} {clean_path}?keys={keys}"


def write_raw_observation(
    *,
    data_dir: Path,
    source_id: str,
    method: str,
    path: str,
    request_params: dict[str, str],
    payload: dict[str, Any],
    retrieved_at: datetime,
    window_from: date | None,
    window_to: date | None,
    normalized_status: NormalizedStatus,
    error_code: str | None = None,
    error_message: str | None = None,
) -> RawObservation:
    """민감 필드를 제거한 raw payload를 ignored data 경로에 저장하고 저장본 hash를 남긴다."""
    observation_id = str(uuid.uuid4())
    raw_dir = data_dir / "raw" / source_id / f"{retrieved_at:%Y%m%d}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{observation_id}.json"

    masked_payload = _mask_payload(payload)
    raw_bytes = json.dumps(masked_payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    # 저장본 기준 hash를 남긴다. raw 파일도 마스킹 후 ignored data 경로에만 보관한다.
    raw_path.write_bytes(raw_bytes)

    return RawObservation(
        observation_id=observation_id,
        source_id=source_id,
        retrieved_at=retrieved_at,
        window_from=window_from,
        window_to=window_to,
        request_fingerprint=request_fingerprint(method, path, request_params),
        payload_hash=hashlib.sha256(raw_bytes).hexdigest(),
        raw_storage_uri=str(raw_path),
        normalized_status=normalized_status,
        error_code=error_code,
        error_message=_mask_text(error_message) if error_message else None,
    )


def _mask_payload(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(str(key)):
                continue
            masked[key] = _mask_payload(child)
        return masked
    if isinstance(value, list):
        return [_mask_payload(child) for child in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def _mask_text(value: str | None) -> str:
    text = value or ""
    lowered = text.lower()
    markers = (
        "crtfc_key",
        "api_key",
        "apikey",
        "secret",
        "token",
        "authorization",
        "authentication",
        "credential",
        "인증키",
    )
    return "[redacted]" if any(marker in lowered for marker in markers) else text


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return any(
        marker in normalized
        for marker in ("key", "secret", "token", "auth", "credential", "account", "acct")
    )
