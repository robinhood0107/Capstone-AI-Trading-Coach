"""Closed manifest and immutable Parquet verification for neutral market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Mapping, cast

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.canonical_json import canonical_json_bytes
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file


SEED_MANIFEST_FILENAME = "manifest.json"
SEED_CONTRACT_ID = "market-data-seed.v1"
OPERATIONAL_HISTORY_MAX = 253
RESEARCH_HISTORY_MAX = 1_260
SOURCE_CHUNK_COUNT = 7_218
HISTORICAL_PROVIDER_INTENT_COUNT = 7_230
SOURCE_SESSION_COUNT = 1_072
HISTORICAL_UNIVERSE_UNION_COUNT = 270
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FIELDS = frozenset(
    {
        "kind",
        "relativePath",
        "sha256",
        "rowCount",
        "firstSessionDate",
        "lastSessionDate",
        "temporalQuality",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "contractId",
        "createdAt",
        "sourceManifestSha256",
        "sourceChunkCount",
        "historicalProviderIntentCount",
        "providerCallsDuringAdoption",
        "sourceSessionCount",
        "operationalHistoryMaxSessions",
        "researchHistoryMaxSessions",
        "historicalUniverseUnionCount",
        "temporalQuality",
        "strictPitPerformanceClaimAllowed",
        "rawChunkCopied",
        "hardlinkUsed",
        "sourcePathPersisted",
        "artifacts",
        "archiveSha256",
    }
)
_KINDS = frozenset({"BARS", "INDICES", "MACRO", "UNIVERSES"})
_ARTIFACT_COLUMNS = {
    "BARS": (
        "symbol",
        "sessionDate",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "currency",
        "temporalQuality",
        "sourceReceiptSha256",
    ),
    "INDICES": (
        "indexId",
        "sessionDate",
        "close",
        "temporalQuality",
        "sourceReceiptSha256",
    ),
    "MACRO": (
        "seriesId",
        "observationDate",
        "availableAt",
        "value",
        "temporalQuality",
        "sourceReceiptSha256",
    ),
    "UNIVERSES": (
        "membershipMonth",
        "selectionSession",
        "effectiveFromSession",
        "instrumentId",
        "symbol",
        "market",
        "rank",
        "isFixedMember",
        "temporalQuality",
        "sourceReceiptSha256",
    ),
}
_ARTIFACT_DATE_COLUMNS = {
    "BARS": "sessionDate",
    "INDICES": "sessionDate",
    "MACRO": "observationDate",
    "UNIVERSES": "effectiveFromSession",
}
_QUALITY = frozenset(
    {
        "PROVIDER_VINTAGE",
        "PROVIDER_AS_OF_NO_VINTAGE",
        "RECONSTRUCTED_FIXED_LAG",
        "COLLECTION_ONLY",
    }
)


class MarketDataArchiveError(ValueError):
    """The neutral archive failed a closed integrity or policy check."""


@dataclass(frozen=True, slots=True)
class MarketDataArtifact:
    kind: str
    relative_path: str
    sha256: str
    row_count: int
    first_session_date: date
    last_session_date: date
    temporal_quality: str


@dataclass(frozen=True, slots=True)
class MarketDataArchive:
    root: Path
    manifest_sha256: str
    archive_sha256: str
    source_manifest_sha256: str
    created_at: datetime
    artifacts: tuple[MarketDataArtifact, ...]

    def artifact(self, kind: str) -> MarketDataArtifact:
        matches = [artifact for artifact in self.artifacts if artifact.kind == kind]
        if len(matches) != 1:
            raise MarketDataArchiveError(f"archive must contain exactly one {kind} artifact")
        return matches[0]


def read_market_data_archive(root: Path) -> MarketDataArchive:
    """Verify the manifest trust boundary and all four immutable Parquet artifacts."""

    try:
        manifest_file = read_approved_regular_file(
            approved_root=root,
            relative_path=SEED_MANIFEST_FILENAME,
            max_bytes=MAX_MANIFEST_BYTES,
        )
    except RagSafeIoError as error:
        raise MarketDataArchiveError("market-data seed manifest boundary is invalid") from error
    try:
        decoded = json.loads(manifest_file.content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketDataArchiveError("market-data seed manifest is invalid JSON") from error
    if (
        not isinstance(decoded, dict)
        or set(decoded) != _MANIFEST_FIELDS
        or canonical_json_bytes(decoded) != manifest_file.content
    ):
        raise MarketDataArchiveError("market-data seed manifest is not closed canonical JSON")
    payload = cast(dict[str, object], decoded)
    _validate_manifest_constants(payload)
    created_at = _datetime(payload["createdAt"], "createdAt")
    source_manifest_sha256 = _sha(payload["sourceManifestSha256"], "sourceManifestSha256")
    archive_sha256 = _sha(payload["archiveSha256"], "archiveSha256")
    artifacts_value = payload["artifacts"]
    if not isinstance(artifacts_value, list) or len(artifacts_value) != 4:
        raise MarketDataArchiveError("market-data seed requires exactly four artifacts")
    artifacts = tuple(_artifact(value) for value in artifacts_value)
    if {artifact.kind for artifact in artifacts} != _KINDS:
        raise MarketDataArchiveError("market-data seed artifact kinds are incomplete")
    if _archive_digest(artifacts) != archive_sha256:
        raise MarketDataArchiveError("market-data archive aggregate digest mismatch")
    for artifact in artifacts:
        _verify_artifact(root, artifact)
    return MarketDataArchive(
        root=root,
        manifest_sha256=manifest_file.content_sha256,
        archive_sha256=archive_sha256,
        source_manifest_sha256=source_manifest_sha256,
        created_at=created_at,
        artifacts=artifacts,
    )


def archive_digest(artifacts: tuple[MarketDataArtifact, ...]) -> str:
    """Public deterministic archive identity used by the provider-free exporter."""

    return _archive_digest(artifacts)


def read_artifact_table(archive: MarketDataArchive, kind: str) -> pa.Table:
    """Rebind a table read to its verified immutable artifact receipt."""

    artifact = archive.artifact(kind)
    return _read_verified_artifact(archive.root, artifact)


def _validate_manifest_constants(payload: Mapping[str, object]) -> None:
    expected: dict[str, object] = {
        "contractId": SEED_CONTRACT_ID,
        "sourceChunkCount": SOURCE_CHUNK_COUNT,
        "historicalProviderIntentCount": HISTORICAL_PROVIDER_INTENT_COUNT,
        "providerCallsDuringAdoption": 0,
        "sourceSessionCount": SOURCE_SESSION_COUNT,
        "operationalHistoryMaxSessions": OPERATIONAL_HISTORY_MAX,
        "researchHistoryMaxSessions": RESEARCH_HISTORY_MAX,
        "historicalUniverseUnionCount": HISTORICAL_UNIVERSE_UNION_COUNT,
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "strictPitPerformanceClaimAllowed": False,
        "rawChunkCopied": False,
        "hardlinkUsed": False,
        "sourcePathPersisted": False,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise MarketDataArchiveError("market-data seed policy constants drifted")


def _artifact(value: object) -> MarketDataArtifact:
    if not isinstance(value, Mapping) or set(value) != _ARTIFACT_FIELDS:
        raise MarketDataArchiveError("market-data artifact receipt is not closed")
    kind = _text(value["kind"], "kind")
    if kind not in _KINDS:
        raise MarketDataArchiveError("market-data artifact kind is invalid")
    relative_path = _text(value["relativePath"], "relativePath")
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] != kind.lower()
        or path.suffix != ".parquet"
    ):
        raise MarketDataArchiveError("market-data artifact path is invalid")
    row_count = value["rowCount"]
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 1:
        raise MarketDataArchiveError("market-data artifact row count is invalid")
    first = _date(value["firstSessionDate"], "firstSessionDate")
    last = _date(value["lastSessionDate"], "lastSessionDate")
    if first > last:
        raise MarketDataArchiveError("market-data artifact date bounds are inverted")
    quality = _text(value["temporalQuality"], "temporalQuality")
    if quality not in _QUALITY:
        raise MarketDataArchiveError("market-data artifact temporal quality is invalid")
    return MarketDataArtifact(
        kind=kind,
        relative_path=relative_path,
        sha256=_sha(value["sha256"], "artifact sha256"),
        row_count=row_count,
        first_session_date=first,
        last_session_date=last,
        temporal_quality=quality,
    )


def _archive_digest(artifacts: tuple[MarketDataArtifact, ...]) -> str:
    ordered = sorted(artifacts, key=lambda artifact: artifact.kind)
    return hashlib.sha256(
        canonical_json_bytes(
            [
                {
                    "kind": artifact.kind,
                    "relativePath": artifact.relative_path,
                    "rowCount": artifact.row_count,
                    "sha256": artifact.sha256,
                }
                for artifact in ordered
            ]
        )
    ).hexdigest()


def _verify_artifact(root: Path, artifact: MarketDataArtifact) -> None:
    _read_verified_artifact(root, artifact)


def _read_verified_artifact(root: Path, artifact: MarketDataArtifact) -> pa.Table:
    try:
        safe = read_approved_regular_file(
            approved_root=root,
            relative_path=artifact.relative_path,
            max_bytes=MAX_ARTIFACT_BYTES,
        )
    except RagSafeIoError as error:
        raise MarketDataArchiveError("market-data artifact boundary is invalid") from error
    if safe.content_sha256 != artifact.sha256:
        raise MarketDataArchiveError("market-data artifact digest mismatch")
    try:
        table = pq.read_table(pa.BufferReader(safe.content))  # type: ignore[no-untyped-call]
    except (pa.ArrowException, OSError) as error:
        raise MarketDataArchiveError("market-data artifact is invalid Parquet") from error
    if table.num_rows != artifact.row_count:
        raise MarketDataArchiveError("market-data artifact row count mismatch")
    if tuple(table.column_names) != _ARTIFACT_COLUMNS[artifact.kind]:
        raise MarketDataArchiveError("market-data artifact schema columns drifted")
    dates = cast(list[date], table[_ARTIFACT_DATE_COLUMNS[artifact.kind]].to_pylist())
    if min(dates) != artifact.first_session_date or max(dates) != artifact.last_session_date:
        raise MarketDataArchiveError("market-data artifact date bounds mismatch")
    return table


def _sha(value: object, field: str) -> str:
    text = _text(value, field)
    if _SHA256.fullmatch(text) is None:
        raise MarketDataArchiveError(f"{field} must be lowercase sha256")
    return text


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MarketDataArchiveError(f"{field} must be non-empty text")
    return value


def _date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(_text(value, field))
    except ValueError as error:
        raise MarketDataArchiveError(f"{field} must be an ISO date") from error


def _datetime(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
    except ValueError as error:
        raise MarketDataArchiveError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise MarketDataArchiveError(f"{field} must be timezone aware")
    return parsed.astimezone(UTC)
