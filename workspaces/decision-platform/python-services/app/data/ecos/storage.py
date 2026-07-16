from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from app.data._shared.canonical_json import canonical_json_bytes
from app.data._shared.secure_snapshot_storage import (
    PublishedSourceSnapshot,
    publish_source_snapshot,
)
from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.ecos.models import ECOSCollectionResult, ECOSMacroSnapshot
from app.data.ecos.quota import ECOS_QUOTA_POLICY_VERSION

ECOS_SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
ECOS_MANIFEST_MAX_BYTES = 256 * 1024

_ECOS_DOCUMENTATION_URL = "https://ecos.bok.or.kr/api/#/DevGuide/DevSpeciflcation"

_FORBIDDEN_FIELD = re.compile(
    r"(?:credential|secret|token|authorization|authentication|requesturl|rawbody|rawresponse)"
)


class ECOSSnapshotStorageError(ValueError):
    """snapshot 계약·sanitization·size 오류를 payload 값 없이 stable하게 보고한다."""


def serialize_ecos_snapshot(payload: object) -> bytes:
    """ECOS snapshot을 strict Pydantic 계약 검증 후 canonical 2 MiB 이하 bytes로 만든다.

    credential·request URL·raw body 계열 필드는 중첩 깊이와 무관하게 계약 검증 전에 거부한다.
    """
    candidate = _json_candidate(payload)
    _reject_forbidden_fields(candidate, ancestors=set())
    _reject_obviously_oversized(candidate)
    try:
        snapshot = ECOSMacroSnapshot.model_validate(candidate)
    except (ValidationError, TypeError, ValueError, RecursionError):
        raise ECOSSnapshotStorageError("ECOS snapshot contract is invalid") from None
    normalized = snapshot.model_dump(mode="json", by_alias=True)
    try:
        encoded = canonical_json_bytes(normalized)
    except (TypeError, ValueError, RecursionError):
        raise ECOSSnapshotStorageError("ECOS snapshot contract is invalid") from None
    if len(encoded) > ECOS_SNAPSHOT_MAX_BYTES:
        raise ECOSSnapshotStorageError("ECOS snapshot size exceeds the safety limit")
    return encoded


class ECOSSnapshotPublisher:
    """검증된 ECOS collection을 snapshot-first·manifest-last 공통 secure store에 게시한다."""

    def __init__(self, *, root: Path) -> None:
        self._root = root

    def __call__(self, result: object) -> None:
        if not isinstance(result, ECOSCollectionResult):
            raise ECOSSnapshotStorageError("ECOS publish contract is invalid")
        publish_ecos_collection(result, root=self._root)


def publish_ecos_collection(
    result: ECOSCollectionResult,
    *,
    root: Path,
    generated_at: datetime | None = None,
    snapshot_id: UUID | None = None,
) -> PublishedSourceSnapshot:
    """canonical snapshot과 provenance manifest를 no-overwrite commit marker 방식으로 게시한다."""
    snapshot_bytes = serialize_ecos_snapshot(result.snapshot)
    generated = generated_at or datetime.now(UTC)
    if generated.tzinfo is None or generated.utcoffset() != UTC.utcoffset(generated):
        raise ECOSSnapshotStorageError("ECOS manifest timestamp must be UTC")
    identifier = snapshot_id or uuid4()
    if identifier.version != 4 or str(identifier) != str(identifier).lower():
        raise ECOSSnapshotStorageError("ECOS snapshot identifier must be lowercase UUID v4")
    as_of = result.snapshot.as_of
    snapshot_path = f"ecos/{as_of:%Y/%m/%d}/{identifier}/snapshot.json"
    observation_count = sum(len(series.observations) for series in result.series_results)
    digest = sha256(snapshot_bytes).hexdigest()
    try:
        manifest = SourceSnapshotManifest.model_validate(
            {
                "schemaVersion": 1,
                "source": "ecos",
                "providerProfile": "ecos",
                "operation": "ecos-macro-collect",
                "generatedAt": generated,
                "asOf": as_of,
                "snapshotPath": snapshot_path,
                "snapshotSha256": digest,
                "recordCount": observation_count,
                "countBreakdown": {
                    "seriesCount": 2,
                    "observationCount": observation_count,
                    "duplicateCount": result.duplicate_count,
                },
                "partial": result.partial,
                "coverage": result.coverage,
                "deferredQueries": 0,
                "physicalAttemptCount": result.physical_attempt_count,
                "quotaPolicyVersion": ECOS_QUOTA_POLICY_VERSION,
                "provenance": {
                    "documentationUrl": _ECOS_DOCUMENTATION_URL,
                    "policyUrl": _ECOS_DOCUMENTATION_URL,
                },
                "sanitizationVersion": "s1.3-sanitization-v1",
                "retentionDays": 365,
                "deleteOwner": "decision-platform:source-snapshot-retention",
            }
        )
    except (ValidationError, TypeError, ValueError):
        raise ECOSSnapshotStorageError("ECOS manifest contract is invalid") from None
    manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json", by_alias=True))
    if len(manifest_bytes) > ECOS_MANIFEST_MAX_BYTES:
        raise ECOSSnapshotStorageError("ECOS manifest size exceeds the safety limit")
    return publish_source_snapshot(
        root=root,
        snapshot_path=snapshot_path,
        snapshot_bytes=snapshot_bytes,
        manifest_bytes=manifest_bytes,
    )


def _json_candidate(payload: object) -> object:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", by_alias=True)
    return payload


def _reject_obviously_oversized(payload: object) -> None:
    # extra field 하나가 cap을 넘긴 입력도 Pydantic extra 오류보다 size 오류로 먼저 안정적으로 분류한다.
    try:
        encoded = canonical_json_bytes(payload)
    except (TypeError, ValueError, RecursionError):
        return
    if len(encoded) > ECOS_SNAPSHOT_MAX_BYTES:
        raise ECOSSnapshotStorageError("ECOS snapshot size exceeds the safety limit")


def _reject_forbidden_fields(value: object, *, ancestors: set[int]) -> None:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise ECOSSnapshotStorageError("ECOS snapshot contract is invalid")
        ancestors.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ECOSSnapshotStorageError("ECOS snapshot contract is invalid")
                normalized = "".join(character for character in key.lower() if character.isalnum())
                if _FORBIDDEN_FIELD.search(normalized):
                    raise ECOSSnapshotStorageError("ECOS snapshot contains a forbidden field")
                _reject_forbidden_fields(child, ancestors=ancestors)
        finally:
            ancestors.remove(identity)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in ancestors:
            raise ECOSSnapshotStorageError("ECOS snapshot contract is invalid")
        ancestors.add(identity)
        try:
            for child in value:
                _reject_forbidden_fields(child, ancestors=ancestors)
        finally:
            ancestors.remove(identity)
