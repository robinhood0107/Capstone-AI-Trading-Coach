from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.data.opendart.models import NormalizedStatus, RawObservation


def request_fingerprint(method: str, path: str, params: dict[str, str]) -> str:
    """요청 값은 버리고 path와 query key 목록만 남겨 secret과 계좌성 값을 노출하지 않는다."""
    keys = ",".join(sorted(params))
    return f"{method.upper()} {path}?keys={keys}"


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
    known_secrets: list[str | None] | None = None,
) -> RawObservation:
    """마스킹된 raw payload를 ignored data 경로에 저장하고 저장본 기준 sha256을 남긴다."""
    secrets = known_secrets or []
    observation_id = str(uuid.uuid4())
    raw_dir = data_dir / "raw" / source_id / f"{retrieved_at:%Y%m%d}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{observation_id}.json"

    masked_payload = _mask_payload(payload, secrets)
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
        error_message=_mask_text(error_message, secrets) if error_message else None,
    )


def _mask_payload(value: Any, secrets: list[str | None]) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(str(key)):
                masked[key] = "***"
            else:
                masked[key] = _mask_payload(child, secrets)
        return masked
    if isinstance(value, list):
        return [_mask_payload(child, secrets) for child in value]
    if isinstance(value, str):
        return _mask_text(value, secrets)
    return value


def _mask_text(value: str | None, secrets: list[str | None]) -> str:
    masked = value or ""
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***")
    return masked


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("key", "secret", "token", "account", "acct"))
