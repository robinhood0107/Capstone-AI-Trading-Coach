from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[5]
OA_RELEASE_MANIFEST_PATH = (
    REPO_ROOT / "capstone-rag/manifests/s4-7d-oa140-release.v1.json"
)
MAX_OA_MANIFEST_BYTES: Final[int] = 2_000_000
HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
SOURCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
REVISION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")

OA_TRACK_IDS: Final[tuple[str, ...]] = (
    "MICRO_GAME_INFO_MARKET_DESIGN",
    "MACRO_MONETARY_INTERNATIONAL",
    "PROBABILITY_STATISTICS_OPTIMIZATION",
    "ECONOMETRICS_CAUSAL_EVENT_STUDY",
    "TIME_SERIES_REGIME_VOLATILITY",
    "ACCOUNTING_CORPORATE_FINANCE_VALUATION",
    "ASSET_PRICING_FACTOR_PORTFOLIO",
    "FIXED_INCOME_RATES_CREDIT",
    "DERIVATIVES_STOCHASTIC_NUMERICS",
    "MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY",
    "RISK_STRESS_BACKTEST_MODEL_RISK",
    "BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING",
    "FINANCIAL_ML_PIT_DATA_PROVENANCE",
    "CROSS_MARKET_COMMODITIES_POLICY_KOREA",
)

REQUIRED_CURRICULUM_ROLES: Final[frozenset[str]] = frozenset(
    {
        "PUBLIC_TEACHING_MATERIAL",
        "ORIGINAL_RESEARCH",
        "MODERN_REVIEW_REPLICATION_CORRECTION",
    }
)
_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "contractId",
        "manifestId",
        "releaseStatus",
        "releaseDigest",
        "signedManifest",
        "sourceCount",
        "tracks",
        "sources",
        "rawRedistributed",
        "extractedTextRedistributed",
        "embeddingsRedistributed",
    }
)
_TRACK_KEYS: Final[frozenset[str]] = frozenset(
    {"trackId", "minimumSources", "maximumSources", "sourceCount"}
)
_SOURCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sourceId",
        "sourceRevisionId",
        "trackId",
        "qualityScore",
        "curriculumRoles",
        "canonicalUrl",
        "downloadUrl",
        "rawContentSha256",
        "machineFetchAllowed",
        "localProcessingAllowed",
        "fallbackAllowed",
    }
)
_BLOCKED_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }
)


class OaReleaseManifestError(ValueError):
    """OA140 release manifest가 수량·권리·digest·URL 계약을 위반할 때 발생한다."""


@dataclass(frozen=True)
class OaReleaseManifest:
    """검증된 OA release manifest의 public metadata projection.

    CLI와 installer는 이 projection만 출력하고 raw hash 목록, 원문 bytes, 로컬 경로를 노출하지
    않는다. 실제 원문 download/parse/embed는 이 manifest가 통과한 뒤 별도 runtime 단계가 맡는다.
    """

    manifest_id: str
    release_digest: str
    source_count: int
    track_counts: Mapping[str, int]
    public_corpus_version: str


def load_oa_release_manifest(path: Path | None = None) -> OaReleaseManifest:
    """tracked 또는 operator-supplied OA manifest를 RELEASED 상태로 검증한다.

    원천 URL을 직접 fetch하지 않으며, 여기서 통과했다는 사실은 hash가 공식 원천과
    일치한다는 뜻이 아니다. 원격 hash 확인은 별도 네트워크 검증 receipt가 책임진다.
    """

    manifest_path = path or OA_RELEASE_MANIFEST_PATH
    payload = _read_manifest_json(manifest_path)
    return validate_oa_release_manifest(payload, require_released=True)


def validate_oa_release_manifest(
    payload: Mapping[str, Any],
    *,
    require_released: bool,
) -> OaReleaseManifest:
    """OA manifest의 closed shape와 release 불변식을 검증한다."""

    _require_keys("OA manifest", payload, _TOP_LEVEL_KEYS)
    if payload["contractId"] != "rag-oa-manifest-v1":
        raise OaReleaseManifestError("OA manifest contractId drifted")
    release_status = payload["releaseStatus"]
    if require_released and release_status != "RELEASED":
        raise OaReleaseManifestError("OA release manifest is not installed")
    for key in (
        "rawRedistributed",
        "extractedTextRedistributed",
        "embeddingsRedistributed",
    ):
        if payload[key] is not False:
            raise OaReleaseManifestError("OA release forbids raw redistribution")
    if release_status != "RELEASED":
        return _draft_projection(payload)

    if payload["signedManifest"] is not True:
        raise OaReleaseManifestError("released OA manifest must be signed")
    sources = payload["sources"]
    tracks = payload["tracks"]
    if not isinstance(sources, list) or not isinstance(tracks, list):
        raise OaReleaseManifestError("OA release sources and tracks must be arrays")
    if not 112 <= len(sources) <= 140:
        raise OaReleaseManifestError("released OA manifest requires 112..140 sources")
    if payload["sourceCount"] != len(sources):
        raise OaReleaseManifestError("OA release sourceCount drifted")
    track_counts = _validate_tracks(tracks)
    by_track: dict[str, list[Mapping[str, Any]]] = {track_id: [] for track_id in OA_TRACK_IDS}
    source_ids: set[str] = set()
    revision_ids: set[str] = set()
    canonical_urls: set[str] = set()
    download_urls: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise OaReleaseManifestError("OA release source must be an object")
        _validate_source_shape(source)
        track_id = str(source["trackId"])
        by_track[track_id].append(source)
        _add_unique(source_ids, str(source["sourceId"]), "duplicate sourceId")
        _add_unique(revision_ids, str(source["sourceRevisionId"]), "duplicate revision")
        _add_unique(canonical_urls, str(source["canonicalUrl"]), "duplicate canonical URL")
        _add_unique(download_urls, str(source["downloadUrl"]), "duplicate download URL")
    for track_id in OA_TRACK_IDS:
        entries = by_track[track_id]
        if len(entries) != track_counts[track_id]:
            raise OaReleaseManifestError(f"OA release track count drifted for {track_id}")
        if not 8 <= len(entries) <= 10:
            raise OaReleaseManifestError(f"OA release track {track_id} requires 8..10 sources")
        roles = {
            role
            for source in entries
            for role in source["curriculumRoles"]
        }
        if not REQUIRED_CURRICULUM_ROLES.issubset(roles):
            raise OaReleaseManifestError(f"OA release track {track_id} lacks required roles")
    expected_digest = canonical_release_digest(payload)
    if payload["releaseDigest"] != expected_digest:
        raise OaReleaseManifestError("OA release digest drifted")
    manifest_id = str(payload["manifestId"])
    return OaReleaseManifest(
        manifest_id=manifest_id,
        release_digest=expected_digest,
        source_count=len(sources),
        track_counts=track_counts,
        public_corpus_version=f"exact30-v1+{manifest_id}",
    )


def canonical_release_digest(payload: Mapping[str, Any]) -> str:
    """`releaseDigest` self-reference를 제거한 canonical JSON SHA-256."""

    detached = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(detached, dict):
        raise OaReleaseManifestError("OA manifest must be an object")
    detached["releaseDigest"] = None
    encoded = json.dumps(
        detached,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_manifest_json(path: Path) -> Mapping[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError as error:
        raise OaReleaseManifestError("OA release manifest is not installed") from error
    if not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_OA_MANIFEST_BYTES:
        raise OaReleaseManifestError("OA release manifest is not a bounded regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OaReleaseManifestError("OA release manifest is unreadable") from error
    if not isinstance(payload, Mapping):
        raise OaReleaseManifestError("OA release manifest must be an object")
    return payload


def _draft_projection(payload: Mapping[str, Any]) -> OaReleaseManifest:
    manifest_id = str(payload["manifestId"])
    return OaReleaseManifest(
        manifest_id=manifest_id,
        release_digest="",
        source_count=0,
        track_counts={track_id: 0 for track_id in OA_TRACK_IDS},
        public_corpus_version=f"exact30-v1+{manifest_id}",
    )


def _validate_tracks(tracks: list[Any]) -> dict[str, int]:
    if len(tracks) != len(OA_TRACK_IDS):
        raise OaReleaseManifestError("OA release track order drifted")
    counts: dict[str, int] = {}
    for expected_track_id, track in zip(OA_TRACK_IDS, tracks, strict=True):
        if not isinstance(track, Mapping):
            raise OaReleaseManifestError("OA release track must be an object")
        _require_keys("OA release track", track, _TRACK_KEYS)
        if track["trackId"] != expected_track_id:
            raise OaReleaseManifestError("OA release track order drifted")
        if track["minimumSources"] != 8 or track["maximumSources"] != 10:
            raise OaReleaseManifestError("OA release track bounds drifted")
        count = track["sourceCount"]
        if not isinstance(count, int) or not 8 <= count <= 10:
            raise OaReleaseManifestError("OA release track source count drifted")
        counts[expected_track_id] = count
    return counts


def _validate_source_shape(source: Mapping[str, Any]) -> None:
    _require_keys("OA release source", source, _SOURCE_KEYS)
    if not SOURCE_ID_PATTERN.fullmatch(str(source["sourceId"])):
        raise OaReleaseManifestError("OA release sourceId shape drifted")
    if not REVISION_ID_PATTERN.fullmatch(str(source["sourceRevisionId"])):
        raise OaReleaseManifestError("OA release sourceRevisionId shape drifted")
    if source["trackId"] not in OA_TRACK_IDS:
        raise OaReleaseManifestError("OA release source track is unknown")
    score = source["qualityScore"]
    if not isinstance(score, int) or not 80 <= score <= 100:
        raise OaReleaseManifestError("OA release source quality score is below threshold")
    roles = source["curriculumRoles"]
    if (
        not isinstance(roles, list)
        or not roles
        or len(roles) != len(set(roles))
        or not set(roles).issubset(REQUIRED_CURRICULUM_ROLES)
    ):
        raise OaReleaseManifestError("OA release source roles are invalid")
    for key in ("canonicalUrl", "downloadUrl"):
        _validate_public_https_url(str(source[key]))
    if not HASH_PATTERN.fullmatch(str(source["rawContentSha256"])):
        raise OaReleaseManifestError("OA release source raw hash is invalid")
    if source["machineFetchAllowed"] is not True or source["localProcessingAllowed"] is not True:
        raise OaReleaseManifestError("OA release source processing flags drifted")
    if source["fallbackAllowed"] is not False:
        raise OaReleaseManifestError("OA release source fallback is forbidden")


def _validate_public_https_url(value: str) -> None:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().strip("[]")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname in _BLOCKED_HOSTS
        or hostname.endswith(".local")
        or hostname.endswith(".localhost")
        or hostname.startswith("10.")
        or hostname.startswith("192.168.")
        or _is_private_172(hostname)
    ):
        raise OaReleaseManifestError("OA release source URL must be public HTTPS")


def _is_private_172(hostname: str) -> bool:
    parts = hostname.split(".")
    if len(parts) < 2 or parts[0] != "172" or not parts[1].isdigit():
        return False
    return 16 <= int(parts[1]) <= 31


def _add_unique(values: set[str], value: str, message: str) -> None:
    if value in values:
        raise OaReleaseManifestError(f"OA release duplicate value: {message}")
    values.add(value)


def _require_keys(label: str, value: Mapping[str, Any], allowed: frozenset[str]) -> None:
    keys = set(value)
    if keys != allowed:
        raise OaReleaseManifestError(f"{label} must use the closed OA release shape")
