"""OA112의 최초 local-only quarantine bootstrap과 active registry activation을 제공한다.

새 후보는 첫 physical download 전에는 raw SHA-256을 알 수 없다. 이 모듈은 그 순환 의존을
후보 registry → one-shot quarantine download → observed hash 검증 → 14×8 active registry
activation으로 분리한다. 기존 hash-pinned downloader의 active 경로를 완화하거나 우회하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from app.rag import oa112_active_registry as active_registry
from app.rag import oa112_downloader as downloader
from app.rag.authorized_retrieval import ALLOWED_RAG_TOPICS
from app.rag.oa112_active_registry import (
    Oa112ActiveRegistry,
    Oa112ActiveRegistryError,
    canonical_oa112_active_registry_digest,
    load_oa112_active_registry,
)
from app.rag.oa112_downloader import (
    Oa112DnsResolver,
    Oa112DownloadBinding,
    Oa112DownloadError,
    Oa112DownloadPacket,
    Oa112HttpsTransport,
    consume_oa112_download_packet,
)
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.safe_io import (
    RagSafeIoError,
    list_approved_regular_files,
    read_approved_regular_file,
    write_approved_new_file,
)

_MAX_REGISTRY_BYTES = 2_000_000
_MAX_PACKET_BYTES = 64 * 1024
_MAX_SOURCE_BYTES = 100 * 1024 * 1024
_MAX_TOTAL_BYTES = 112 * _MAX_SOURCE_BYTES
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_REGISTRY_ID = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_OPERATOR = re.compile(r"^[A-Za-z0-9._-]{3,128}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_MIME_TYPES = frozenset({"application/pdf", "text/html", "text/plain"})
_ROOT_FIELDS = frozenset(
    {
        "automaticReservePromotion",
        "candidateSourceCount",
        "candidateSources",
        "contractId",
        "registryDigest",
        "registryId",
        "reserveSourceCount",
        "reserveSources",
        "schemaVersion",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {
        "accessEvidence",
        "authors",
        "canonicalUrl",
        "canonicalUrlSha256",
        "identifier",
        "languageTags",
        "licenseEvidenceDigest",
        "mimeType",
        "permissions",
        "retrievalTopics",
        "revision",
        "revisionDate",
        "sourceId",
        "sourceRevisionId",
        "title",
        "trackId",
    }
)
_ACCESS_EVIDENCE_FIELDS = frozenset(
    {"accessCheckedAt", "accessEvidenceDigest", "verificationState"}
)
_IDENTIFIER_FIELDS = frozenset({"scheme", "value"})
_PERMISSION_FIELDS = frozenset(
    {
        "externalEmbeddingAllowed",
        "externalGenerationAllowed",
        "localProcessingAllowed",
        "machineFetchAllowed",
    }
)
_CURATION_HISTORICAL_FIELDS = frozenset(
    {"contractId", "entries", "rawCorpusStored", "retrievedAt", "sourceCount"}
)
_CURATION_REPLACEMENT_FIELDS = frozenset(
    {
        "contractId",
        "historicalCurationReceiptSha256",
        "metadataAttemptCount",
        "rawCorpusStored",
        "retrievedAt",
        "tracks",
    }
)
_CURATION_HISTORICAL_ENTRY_FIELDS = frozenset(
    {
        "arxivIdentifier",
        "authors",
        "historicalRawContentSha256",
        "licenseUrl",
        "metadataSha256",
        "metadataUrl",
        "revisionDate",
        "sourceId",
        "sourceRevisionId",
        "state",
        "title",
        "trackId",
    }
)
_CURATION_HISTORICAL_INELIGIBLE_FIELDS = _CURATION_HISTORICAL_ENTRY_FIELDS | frozenset({"reason"})
_CURATION_REPLACEMENT_ENTRY_FIELDS = frozenset(
    {
        "arxivIdentifier",
        "authors",
        "canonicalUrl",
        "licenseUrl",
        "metadataSha256",
        "metadataUrl",
        "revisionDate",
        "sourceId",
        "sourceRevisionId",
        "state",
        "title",
        "trackId",
    }
)
_CURATION_TRACK_FIELDS = frozenset(
    {"historicalEligibleCount", "replacementCandidates", "state", "totalCandidateCount", "trackId"}
)
_TRACK_TOPICS: dict[str, tuple[str, ...]] = {
    "MICRO_GAME_INFO_MARKET_DESIGN": ("METHODOLOGY",),
    "MACRO_MONETARY_INTERNATIONAL": ("DATA", "METHODOLOGY", "RISK"),
    "PROBABILITY_STATISTICS_OPTIMIZATION": ("METHODOLOGY", "RISK"),
    "ECONOMETRICS_CAUSAL_EVENT_STUDY": ("DATA", "METHODOLOGY"),
    "TIME_SERIES_REGIME_VOLATILITY": ("DATA", "FINANCIAL_ENGINEERING", "METHODOLOGY", "RISK"),
    "ACCOUNTING_CORPORATE_FINANCE_VALUATION": ("FINANCIAL_ENGINEERING", "METHODOLOGY"),
    "ASSET_PRICING_FACTOR_PORTFOLIO": ("FINANCIAL_ENGINEERING", "METHODOLOGY", "RISK"),
    "FIXED_INCOME_RATES_CREDIT": ("FINANCIAL_ENGINEERING", "METHODOLOGY", "RISK"),
    "DERIVATIVES_STOCHASTIC_NUMERICS": ("FINANCIAL_ENGINEERING", "METHODOLOGY", "RISK"),
    "MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY": ("DATA", "FINANCIAL_ENGINEERING", "METHODOLOGY"),
    "RISK_STRESS_BACKTEST_MODEL_RISK": ("METHODOLOGY", "RISK"),
    "BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING": ("FINANCIAL_ENGINEERING", "METHODOLOGY", "RISK"),
    "FINANCIAL_ML_PIT_DATA_PROVENANCE": ("DATA", "METHODOLOGY", "RISK"),
    "CROSS_MARKET_COMMODITIES_POLICY_KOREA": ("DATA", "FINANCIAL_ENGINEERING", "METHODOLOGY"),
}


class Oa112BootstrapError(ValueError):
    """OA112 first-download bootstrap registry 또는 local activation 계약 위반을 나타낸다."""


@dataclass(frozen=True, slots=True)
class Oa112BootstrapCandidate:
    """raw hash가 아직 없는, 권리 확인 완료 OA112 후보 metadata다.

    원문·raw cache path·provider response는 이 객체에 저장하지 않는다. 최초 download 결과의 SHA-256은
    quarantine에만 남고, 정확히 112개가 모두 검증된 뒤에만 immutable active registry로 승격된다.
    """

    source_id: str
    source_revision_id: str
    track_id: str
    language_tags: tuple[str, ...]
    retrieval_topics: tuple[str, ...]
    title: str
    authors: tuple[str, ...]
    canonical_url: str
    identifier_scheme: str
    identifier_value: str
    revision: str
    revision_date: str
    mime_type: str
    license_evidence_sha256: str
    access_checked_at: str
    access_evidence_sha256: str
    machine_fetch_allowed: bool
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool


@dataclass(frozen=True, slots=True)
class Oa112BootstrapCandidateRegistry:
    """14×8 candidate selection을 raw hash 없이 local-only로 잠그는 registry다."""

    registry_id: str
    registry_digest: str
    active_entries: tuple[Oa112BootstrapCandidate, ...]

    @property
    def active_source_count(self) -> int:
        """bootstrap가 허용하는 active 후보 수는 언제나 112다."""

        return len(self.active_entries)

    @property
    def active_source_ids(self) -> tuple[str, ...]:
        """packet에 binding할 deterministic source 순서를 반환한다."""

        return tuple(entry.source_id for entry in self.active_entries)


@dataclass(frozen=True, slots=True)
class Oa112BootstrapDownloadedSourceReceipt:
    """quarantine raw path/body 없이 one source의 observed hash만 노출한다."""

    source_id: str
    source_revision_id: str
    raw_content_sha256: str
    bytes_read: int
    state: str


@dataclass(frozen=True, slots=True)
class Oa112BootstrapDownloadReceipt:
    """first-download physical count와 content-free observed-hash receipt다."""

    attempt_count: int
    physical_call_count: int
    quarantined_source_count: int
    reused_source_count: int
    sources: tuple[Oa112BootstrapDownloadedSourceReceipt, ...]

    def content_free_projection(self) -> dict[str, object]:
        """operator evidence에 raw URL, cache path, body를 포함하지 않는 projection을 만든다."""

        return {
            "attemptCount": self.attempt_count,
            "physicalCallCount": self.physical_call_count,
            "quarantinedSourceCount": self.quarantined_source_count,
            "reusedSourceCount": self.reused_source_count,
            "sources": [
                {
                    "bytesRead": item.bytes_read,
                    "rawContentSha256": item.raw_content_sha256,
                    "sourceId": item.source_id,
                    "sourceRevisionId": item.source_revision_id,
                    "state": item.state,
                }
                for item in self.sources
            ],
        }


def canonical_oa112_bootstrap_candidate_registry_digest(payload: Mapping[str, object]) -> str:
    """candidate registryDigest self-reference를 제외한 canonical UTF-8 SHA-256을 계산한다."""

    detached = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(detached, dict):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    detached["registryDigest"] = None
    return _canonical_hash(detached)


def load_oa112_bootstrap_candidate_registry(
    *,
    approved_root: Path,
    relative_path: str,
) -> Oa112BootstrapCandidateRegistry:
    """0600 local candidate registry를 descriptor-safe read로 읽고 exact 14×8 set을 검증한다."""

    if not _is_leaf(relative_path) or not _is_private_root(approved_root):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_UNSAFE")
    try:
        result = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=relative_path,
            max_bytes=_MAX_REGISTRY_BYTES,
        )
    except RagSafeIoError as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_UNSAFE") from error
    try:
        return validate_oa112_bootstrap_candidate_registry(_parse_canonical_json(result.content))
    except Oa112BootstrapError:
        raise
    except (TypeError, ValueError) as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID") from error


def validate_oa112_bootstrap_candidate_registry(
    payload: Mapping[str, object],
) -> Oa112BootstrapCandidateRegistry:
    """candidate registry가 active selection, rights, digest를 모두 닫는지 검증한다."""

    _require_exact_keys(payload, _ROOT_FIELDS)
    if (
        payload.get("contractId") != "rag-v2-oa112-bootstrap-candidate-registry-v1"
        or payload.get("schemaVersion") != 1
        or payload.get("automaticReservePromotion") is not False
        or payload.get("candidateSourceCount") != 112
        or payload.get("reserveSourceCount") != 0
        or payload.get("reserveSources") != []
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    registry_id = _required_text(payload, "registryId", maximum=128)
    if _REGISTRY_ID.fullmatch(registry_id) is None:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    registry_digest = _required_sha256(payload, "registryDigest")
    if registry_digest != canonical_oa112_bootstrap_candidate_registry_digest(payload):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_DIGEST_DRIFT")
    raw_entries = payload.get("candidateSources")
    if not isinstance(raw_entries, list) or len(raw_entries) != 112:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    entries = tuple(_parse_candidate(item) for item in raw_entries)
    _validate_candidate_distribution(entries)
    _validate_candidate_identities(entries)
    return Oa112BootstrapCandidateRegistry(
        registry_id=registry_id,
        registry_digest=registry_digest,
        active_entries=entries,
    )


def build_oa112_bootstrap_candidate_registry_from_curation(
    *,
    historical_curation: Mapping[str, object],
    replacement_curation: Mapping[str, object],
    registry_id: str = "oa112-bootstrap-ccby-v1",
) -> dict[str, object]:
    """local curation receipts에서 exact 30+82 CC-BY candidate registry payload를 만든다.

    historical hash는 evidence drift 검사용으로만 입력에서 검증하고 active registry에 복사하지 않는다.
    모든 source는 새 quarantine download에서 observed raw SHA-256을 얻어야 한다.
    """

    _require_exact_keys(historical_curation, _CURATION_HISTORICAL_FIELDS)
    _require_exact_keys(replacement_curation, _CURATION_REPLACEMENT_FIELDS)
    if (
        historical_curation.get("contractId") != "rag-v2-oa112-local-curation-receipt-v1"
        or replacement_curation.get("contractId")
        != "rag-v2-oa112-local-ccby-replacement-candidates-v1"
        or historical_curation.get("rawCorpusStored") is not False
        or replacement_curation.get("rawCorpusStored") is not False
        or historical_curation.get("sourceCount") != 84
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    historical_checked_at = _required_utc(historical_curation, "retrievedAt")
    replacement_checked_at = _required_utc(replacement_curation, "retrievedAt")
    historical_entries_raw = historical_curation.get("entries")
    replacement_tracks_raw = replacement_curation.get("tracks")
    if (
        not isinstance(historical_entries_raw, list)
        or len(historical_entries_raw) != 84
        or not isinstance(replacement_tracks_raw, list)
        or len(replacement_tracks_raw) != len(OA_TRACK_IDS)
        or _REGISTRY_ID.fullmatch(registry_id) is None
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")

    historical_by_track: dict[str, list[Mapping[str, object]]] = {
        track_id: [] for track_id in OA_TRACK_IDS
    }
    seen_historical: set[str] = set()
    for raw in historical_entries_raw:
        if not isinstance(raw, Mapping):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        state = raw.get("state")
        if state == "ELIGIBLE_CC_BY_4":
            _require_exact_keys(raw, _CURATION_HISTORICAL_ENTRY_FIELDS)
            _validate_curation_source(raw, historical=True)
        elif state == "NOT_ELIGIBLE":
            _require_exact_keys(raw, _CURATION_HISTORICAL_INELIGIBLE_FIELDS)
            _validate_ineligible_historical_curation_source(raw)
        else:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        source_id = _required_text(raw, "sourceId", maximum=128)
        if source_id in seen_historical:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        seen_historical.add(source_id)
        if state == "ELIGIBLE_CC_BY_4":
            historical_by_track[_required_track_id(raw)].append(raw)

    candidate_sources: list[dict[str, object]] = []
    seen_source_ids: set[str] = set()
    seen_revisions: set[str] = set()
    for track_id, raw_track in zip(OA_TRACK_IDS, replacement_tracks_raw, strict=True):
        if not isinstance(raw_track, Mapping):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        _require_exact_keys(raw_track, _CURATION_TRACK_FIELDS)
        if (
            raw_track.get("trackId") != track_id
            or raw_track.get("state") != "FULL_CANDIDATE_SET"
            or raw_track.get("totalCandidateCount") != 8
            or raw_track.get("historicalEligibleCount") != len(historical_by_track[track_id])
        ):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        replacements = raw_track.get("replacementCandidates")
        if not isinstance(replacements, list) or len(replacements) != 8 - len(historical_by_track[track_id]):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        selected = sorted(historical_by_track[track_id], key=lambda item: _required_text(item, "sourceId", maximum=128).encode("utf-8"))
        for raw in replacements:
            if not isinstance(raw, Mapping):
                raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
            _require_exact_keys(raw, _CURATION_REPLACEMENT_ENTRY_FIELDS)
            _validate_curation_source(raw, historical=False)
            if raw.get("trackId") != track_id or raw.get("state") != "CANDIDATE_CC_BY_4_RAW_HASH_PENDING":
                raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
            selected.append(raw)
        if len(selected) != 8:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
        for raw in sorted(selected, key=lambda item: _required_text(item, "sourceId", maximum=128).encode("utf-8")):
            candidate = _candidate_payload_from_curation(
                raw=raw,
                checked_at=historical_checked_at if raw in historical_by_track[track_id] else replacement_checked_at,
                historical=raw in historical_by_track[track_id],
            )
            source_id = _required_text(candidate, "sourceId", maximum=128)
            revision_id = _required_text(candidate, "sourceRevisionId", maximum=128)
            if source_id in seen_source_ids or revision_id in seen_revisions:
                raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
            seen_source_ids.add(source_id)
            seen_revisions.add(revision_id)
            candidate_sources.append(candidate)
    if len(candidate_sources) != 112:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    payload: dict[str, object] = {
        "automaticReservePromotion": False,
        "candidateSourceCount": 112,
        "candidateSources": candidate_sources,
        "contractId": "rag-v2-oa112-bootstrap-candidate-registry-v1",
        "registryDigest": None,
        "registryId": registry_id,
        "reserveSourceCount": 0,
        "reserveSources": [],
        "schemaVersion": 1,
    }
    payload["registryDigest"] = canonical_oa112_bootstrap_candidate_registry_digest(payload)
    validate_oa112_bootstrap_candidate_registry(payload)
    return payload


def oa112_bootstrap_source_endpoint_digest(entries: Sequence[Oa112BootstrapCandidate]) -> str:
    """candidate source/origin/endpoint ordered projection을 packet에 결속한다."""

    projection = [
        {
            "endpoint": downloader._endpoint_from_https_url(entry.canonical_url),
            "origin": downloader._origin_from_https_url(entry.canonical_url),
            "sourceId": entry.source_id,
        }
        for entry in entries
    ]
    return _canonical_hash(projection)


def oa112_bootstrap_quarantine_filename(entry: Oa112BootstrapCandidate) -> str:
    """caller path를 받지 않고 candidate MIME에서 fixed quarantine leaf를 만든다."""

    extension = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "text/plain": ".txt",
    }.get(entry.mime_type)
    if extension is None or _SOURCE_ID.fullmatch(entry.source_id) is None:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return f"{entry.source_id}{extension}"


def download_oa112_bootstrap_quarantine(
    *,
    registry: Oa112BootstrapCandidateRegistry,
    packet: Oa112DownloadPacket | None,
    local_cache_root: Path,
    packet_control_root: Path,
    execution_binding: Oa112DownloadBinding | None = None,
    resolver: Oa112DnsResolver | None = None,
    transport: Oa112HttpsTransport | None = None,
    now: datetime | None = None,
) -> Oa112BootstrapDownloadReceipt:
    """approved candidate를 one-shot HTTPS request로 quarantine에만 수집한다.

    cache hit은 network 없이 재검증하고 재사용한다. 하나라도 pending이면 current HEAD/tree/CI/security
    binding을 가진 packet을 atomically consume하며, first failure 뒤 남은 source request를 만들지 않는다.
    """

    selected = registry.active_entries if isinstance(registry, Oa112BootstrapCandidateRegistry) else ()
    check_now = now or datetime.now(UTC)
    if (
        len(selected) != 112
        or len({entry.source_id for entry in selected}) != 112
        or _SHA256.fullmatch(registry.registry_digest) is None
    ):
        raise Oa112DownloadError("OA112_BOOTSTRAP_DOWNLOAD_INPUT_INVALID")
    root_fd, quarantine_fd, staging_fd = _open_bootstrap_cache_layout(local_cache_root)
    attempt_count = 0
    physical_call_count = 0
    consumed_packet: Oa112DownloadPacket | None = None
    receipt_write_attempted = False
    receipts: dict[str, Oa112BootstrapDownloadedSourceReceipt] = {}
    try:
        pending: list[Oa112BootstrapCandidate] = []
        for entry in selected:
            cached = _read_quarantined_raw(
                quarantine_fd=quarantine_fd,
                entry=entry,
                maximum_source_bytes=_MAX_SOURCE_BYTES,
                maximum_pages=500,
            )
            if cached is None:
                pending.append(entry)
            else:
                receipts[entry.source_id] = cached
        completion_receipt_missing = not pending and not _has_complete_bootstrap_success_receipt(
            registry_root=packet_control_root,
            registry=registry,
        )
        if pending or completion_receipt_missing:
            if packet is None:
                raise Oa112DownloadError("OA112_PACKET_REQUIRED")
            if execution_binding is None:
                raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_REQUIRED")
            _validate_bootstrap_packet(
                packet=packet,
                entries=selected,
                candidate_registry_digest=registry.registry_digest,
                execution_binding=execution_binding,
                now=check_now,
            )
            consume_oa112_download_packet(packet=packet, control_root=packet_control_root)
            consumed_packet = packet
            active_resolver = resolver or downloader._SocketOa112DnsResolver()
            active_transport = transport or downloader._StdlibOa112HttpsTransport()
            total_bytes = 0
            for entry in pending:
                if physical_call_count >= packet.physical_call_cap:
                    raise Oa112DownloadError(
                        "OA112_PACKET_PHYSICAL_CAP",
                        attempt_count=attempt_count,
                        physical_call_count=physical_call_count,
                    )
                with downloader._Oa112SourceDeadline(expires_at=packet.expires_at) as deadline:
                    deadline.remaining_seconds()
                    attempt_count += 1

                    def record_provider_request() -> None:
                        nonlocal physical_call_count
                        if physical_call_count >= packet.physical_call_cap:
                            raise Oa112DownloadError(
                                "OA112_PACKET_PHYSICAL_CAP",
                                attempt_count=attempt_count,
                                physical_call_count=physical_call_count,
                            )
                        physical_call_count += 1

                    receipt = _download_bootstrap_candidate(
                        quarantine_fd=quarantine_fd,
                        staging_fd=staging_fd,
                        entry=entry,
                        maximum_source_bytes=min(
                            packet.maximum_source_bytes,
                            packet.maximum_total_bytes - total_bytes,
                        ),
                        maximum_pages=packet.maximum_pages,
                        resolver=active_resolver,
                        transport=active_transport,
                        deadline=deadline,
                        record_provider_request=record_provider_request,
                    )
                total_bytes += receipt.bytes_read
                if total_bytes > packet.maximum_total_bytes:
                    raise Oa112DownloadError(
                        "OA112_PACKET_TOTAL_BYTE_CAP",
                        attempt_count=attempt_count,
                        physical_call_count=physical_call_count,
                    )
                receipts[entry.source_id] = receipt
        ordered = tuple(receipts[entry.source_id] for entry in selected)
        result = Oa112BootstrapDownloadReceipt(
            attempt_count=attempt_count,
            physical_call_count=physical_call_count,
            quarantined_source_count=sum(item.state == "QUARANTINED" for item in ordered),
            reused_source_count=sum(item.state == "REUSED" for item in ordered),
            sources=ordered,
        )
        if consumed_packet is not None:
            receipt_write_attempted = True
            _write_bootstrap_run_receipt(
                control_root=packet_control_root,
                packet=consumed_packet,
                candidate_registry_digest=registry.registry_digest,
                state="SUCCEEDED",
                code="OA112_BOOTSTRAP_QUARANTINE_READY",
                receipt=result,
            )
        return result
    except Oa112DownloadError as error:
        surfaced = error
        if error.attempt_count != attempt_count or error.physical_call_count != physical_call_count:
            surfaced = Oa112DownloadError(
                error.code,
                attempt_count=attempt_count,
                physical_call_count=physical_call_count,
            )
        if consumed_packet is not None and not receipt_write_attempted:
            receipt_write_attempted = True
            failure = Oa112BootstrapDownloadReceipt(
                attempt_count=attempt_count,
                physical_call_count=physical_call_count,
                quarantined_source_count=sum(item.state == "QUARANTINED" for item in receipts.values()),
                reused_source_count=sum(item.state == "REUSED" for item in receipts.values()),
                sources=tuple(receipts.values()),
            )
            try:
                _write_bootstrap_run_receipt(
                    control_root=packet_control_root,
                    packet=consumed_packet,
                    candidate_registry_digest=registry.registry_digest,
                    state="FAILED",
                    code=surfaced.code,
                    receipt=failure,
                )
            except Oa112DownloadError as receipt_error:
                raise Oa112DownloadError(
                    receipt_error.code,
                    attempt_count=attempt_count,
                    physical_call_count=physical_call_count,
                ) from surfaced
            raise Oa112DownloadError(
                surfaced.code,
                attempt_count=attempt_count,
                physical_call_count=physical_call_count,
                failure_receipt_written=True,
            ) from error
        raise surfaced from error
    finally:
        os.close(staging_fd)
        os.close(quarantine_fd)
        os.close(root_fd)


def activate_oa112_bootstrap_quarantine(
    *,
    registry: Oa112BootstrapCandidateRegistry,
    local_cache_root: Path,
    registry_root: Path,
    registry_relative_path: str,
) -> Oa112ActiveRegistry:
    """complete quarantine의 observed hash를 immutable active registry와 `oa-raw` cache로 승격한다.

    모든 112 source를 validate한 뒤에만 first rename을 시작하며, active registry publication은 raw move가
    끝난 뒤 마지막에 one-shot으로 수행한다. 따라서 부분 download·부분 promotion은 active corpus가 되지 않는다.
    """

    if (
        not isinstance(registry, Oa112BootstrapCandidateRegistry)
        or registry.active_source_count != 112
        or not _is_leaf(registry_relative_path)
        or not _is_private_root(registry_root)
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_ACTIVATION_INPUT_INVALID")
    root_fd = downloader._open_private_root(local_cache_root, error_code="OA112_BOOTSTRAP_CACHE_UNSAFE")
    quarantine_fd = -1
    raw_fd = -1
    try:
        quarantine_fd = downloader._open_or_create_private_directory(root_fd, "oa112-quarantine")
        raw_fd = downloader._open_or_create_private_directory(root_fd, "oa-raw")
        observed_from_receipt = _load_complete_bootstrap_receipt(
            registry_root=registry_root,
            registry=registry,
        )
        observed = _validate_complete_bootstrap_quarantine(
            quarantine_fd=quarantine_fd,
            raw_fd=raw_fd,
            entries=registry.active_entries,
            observed_from_receipt=observed_from_receipt,
        )
        _require_absent_private_leaf(
            root=registry_root,
            leaf=registry_relative_path,
            error_code="OA112_BOOTSTRAP_ACTIVE_REGISTRY_EXISTS",
        )
        _promote_quarantine_to_raw(
            quarantine_fd=quarantine_fd,
            raw_fd=raw_fd,
            entries=registry.active_entries,
            observed_hashes=observed,
        )
        payload = _active_registry_payload(registry=registry, observed_hashes=observed)
        try:
            active_registry._validate_registry(payload)
        except Oa112ActiveRegistryError as error:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_ACTIVE_REGISTRY_INVALID") from error
        content = _canonical_json_bytes(payload)
        try:
            write_approved_new_file(
                approved_root=registry_root,
                relative_path=registry_relative_path,
                content=content,
                max_bytes=_MAX_REGISTRY_BYTES,
            )
        except RagSafeIoError as error:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_ACTIVE_REGISTRY_PUBLISH") from error
        try:
            return load_oa112_active_registry(
                approved_root=registry_root,
                relative_path=registry_relative_path,
            )
        except Oa112ActiveRegistryError as error:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_ACTIVE_REGISTRY_PUBLISH") from error
    except Oa112DownloadError as error:
        raise Oa112BootstrapError(error.code) from error
    finally:
        if raw_fd >= 0:
            os.close(raw_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        os.close(root_fd)


def _parse_candidate(value: object) -> Oa112BootstrapCandidate:
    if not isinstance(value, Mapping):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    _require_exact_keys(value, _CANDIDATE_FIELDS)
    source_id = _required_text(value, "sourceId", maximum=128)
    source_revision_id = _required_text(value, "sourceRevisionId", maximum=128)
    track_id = _required_track_id(value)
    if _SOURCE_ID.fullmatch(source_id) is None or _SOURCE_REVISION_ID.fullmatch(source_revision_id) is None:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    language_tags = _text_array(value.get("languageTags"), maximum=8, pattern=_LANGUAGE_TAG)
    retrieval_topics = _text_array(value.get("retrievalTopics"), maximum=len(ALLOWED_RAG_TOPICS), pattern=None)
    if not set(retrieval_topics) <= ALLOWED_RAG_TOPICS:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    title = _required_text(value, "title", maximum=500)
    authors = _text_array(value.get("authors"), maximum=50, pattern=None)
    if any(len(author) > 300 for author in authors):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    canonical_url = _required_text(value, "canonicalUrl", maximum=2_048)
    _validate_public_https_url(canonical_url)
    if hashlib.sha256(canonical_url.encode("utf-8")).hexdigest() != _required_sha256(
        value,
        "canonicalUrlSha256",
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    identifier = _required_mapping(value, "identifier")
    _require_exact_keys(identifier, _IDENTIFIER_FIELDS)
    identifier_scheme = _required_text(identifier, "scheme", maximum=16)
    identifier_value = _required_text(identifier, "value", maximum=256)
    if identifier_scheme not in {"ARXIV", "DOI", "ISBN"}:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    revision = _required_text(value, "revision", maximum=128)
    revision_date = _required_date(value, "revisionDate")
    mime_type = _required_text(value, "mimeType", maximum=128)
    if mime_type not in _MIME_TYPES:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    access = _required_mapping(value, "accessEvidence")
    _require_exact_keys(access, _ACCESS_EVIDENCE_FIELDS)
    access_checked_at = _required_utc(access, "accessCheckedAt")
    if access.get("verificationState") != "VERIFIED":
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    access_evidence_sha256 = _required_sha256(access, "accessEvidenceDigest")
    license_evidence_sha256 = _required_sha256(value, "licenseEvidenceDigest")
    permissions = _required_mapping(value, "permissions")
    _require_exact_keys(permissions, _PERMISSION_FIELDS)
    if any(type(permissions[key]) is not bool for key in _PERMISSION_FIELDS):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    if not all(permissions[key] is True for key in _PERMISSION_FIELDS):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_RIGHTS_REQUIRED")
    return Oa112BootstrapCandidate(
        source_id=source_id,
        source_revision_id=source_revision_id,
        track_id=track_id,
        language_tags=language_tags,
        retrieval_topics=retrieval_topics,
        title=title,
        authors=authors,
        canonical_url=canonical_url,
        identifier_scheme=identifier_scheme,
        identifier_value=identifier_value,
        revision=revision,
        revision_date=revision_date,
        mime_type=mime_type,
        license_evidence_sha256=license_evidence_sha256,
        access_checked_at=access_checked_at,
        access_evidence_sha256=access_evidence_sha256,
        machine_fetch_allowed=permissions["machineFetchAllowed"] is True,
        local_processing_allowed=permissions["localProcessingAllowed"] is True,
        external_embedding_allowed=permissions["externalEmbeddingAllowed"] is True,
        external_generation_allowed=permissions["externalGenerationAllowed"] is True,
    )


def _validate_candidate_distribution(entries: tuple[Oa112BootstrapCandidate, ...]) -> None:
    expected_tracks = tuple(track_id for track_id in OA_TRACK_IDS for _ in range(8))
    if tuple(entry.track_id for entry in entries) != expected_tracks:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_TRACK_DISTRIBUTION")
    cursor = 0
    for _track_id in OA_TRACK_IDS:
        source_ids = tuple(entry.source_id for entry in entries[cursor : cursor + 8])
        if source_ids != tuple(sorted(source_ids, key=lambda value: value.encode("utf-8"))):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_ORDER_INVALID")
        cursor += 8


def _validate_candidate_identities(entries: tuple[Oa112BootstrapCandidate, ...]) -> None:
    for values in (
        [entry.source_id for entry in entries],
        [entry.source_revision_id for entry in entries],
        [entry.canonical_url for entry in entries],
    ):
        if len(values) != len(set(values)):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_IDENTITY_DUPLICATE")


def _validate_curation_source(value: Mapping[str, object], *, historical: bool) -> None:
    expected = _CURATION_HISTORICAL_ENTRY_FIELDS if historical else _CURATION_REPLACEMENT_ENTRY_FIELDS
    _require_exact_keys(value, expected)
    if _required_track_id(value) not in OA_TRACK_IDS:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    source_id = _required_text(value, "sourceId", maximum=128)
    revision_id = _required_text(value, "sourceRevisionId", maximum=128)
    if _SOURCE_ID.fullmatch(source_id) is None or _SOURCE_REVISION_ID.fullmatch(revision_id) is None:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    _required_text(value, "arxivIdentifier", maximum=128)
    _required_text(value, "title", maximum=500)
    _curation_authors(value.get("authors"))
    _required_date(value, "revisionDate")
    _required_sha256(value, "metadataSha256")
    _required_text(value, "licenseUrl", maximum=2_048)
    _validate_public_https_url(_required_text(value, "metadataUrl", maximum=2_048))
    if value.get("licenseUrl") != "https://creativecommons.org/licenses/by/4.0/":
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_RIGHTS_REQUIRED")
    if historical:
        _required_sha256(value, "historicalRawContentSha256")
    else:
        _validate_public_https_url(_required_text(value, "canonicalUrl", maximum=2_048))


def _validate_ineligible_historical_curation_source(value: Mapping[str, object]) -> None:
    """비선정 historical record도 shape/identity만 닫되 license를 active 후보처럼 취급하지 않는다."""

    _required_track_id(value)
    source_id = _required_text(value, "sourceId", maximum=128)
    revision_id = _required_text(value, "sourceRevisionId", maximum=128)
    if _SOURCE_ID.fullmatch(source_id) is None or _SOURCE_REVISION_ID.fullmatch(revision_id) is None:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    _required_text(value, "reason", maximum=256)
    _required_text(value, "arxivIdentifier", maximum=128)
    _required_text(value, "title", maximum=500)
    _curation_authors(value.get("authors"))
    _required_date(value, "revisionDate")
    _required_sha256(value, "metadataSha256")
    _required_sha256(value, "historicalRawContentSha256")
    _validate_public_https_url(_required_text(value, "metadataUrl", maximum=2_048))


def _candidate_payload_from_curation(
    *,
    raw: Mapping[str, object],
    checked_at: str,
    historical: bool,
) -> dict[str, object]:
    track_id = _required_track_id(raw)
    arxiv_identifier = _required_text(raw, "arxivIdentifier", maximum=128)
    canonical_url = (
        f"https://arxiv.org/pdf/{arxiv_identifier}"
        if historical
        else _required_text(raw, "canonicalUrl", maximum=2_048)
    )
    metadata_sha256 = _required_sha256(raw, "metadataSha256")
    license_url = _required_text(raw, "licenseUrl", maximum=2_048)
    metadata_url = _required_text(raw, "metadataUrl", maximum=2_048)
    source_id = _required_text(raw, "sourceId", maximum=128)
    source_revision_id = _required_text(raw, "sourceRevisionId", maximum=128)
    return {
        "accessEvidence": {
            "accessCheckedAt": checked_at,
            "accessEvidenceDigest": _canonical_hash(
                {
                    "metadataSha256": metadata_sha256,
                    "metadataUrl": metadata_url,
                    "sourceId": source_id,
                    "sourceRevisionId": source_revision_id,
                }
            ),
            "verificationState": "VERIFIED",
        },
        "authors": list(sorted(_curation_authors(raw.get("authors")), key=lambda item: item.encode("utf-8"))),
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": hashlib.sha256(canonical_url.encode("utf-8")).hexdigest(),
        "identifier": {"scheme": "ARXIV", "value": arxiv_identifier},
        "languageTags": ["en"],
        "licenseEvidenceDigest": _canonical_hash(
            {"licenseUrl": license_url, "metadataSha256": metadata_sha256}
        ),
        "mimeType": "application/pdf",
        "permissions": {
            "externalEmbeddingAllowed": True,
            "externalGenerationAllowed": True,
            "localProcessingAllowed": True,
            "machineFetchAllowed": True,
        },
        "retrievalTopics": list(_TRACK_TOPICS[track_id]),
        "revision": arxiv_identifier,
        "revisionDate": _required_date(raw, "revisionDate"),
        "sourceId": source_id,
        "sourceRevisionId": source_revision_id,
        "title": _required_text(raw, "title", maximum=500),
        "trackId": track_id,
    }


def _validate_bootstrap_packet(
    *,
    packet: Oa112DownloadPacket,
    entries: tuple[Oa112BootstrapCandidate, ...],
    candidate_registry_digest: str,
    execution_binding: Oa112DownloadBinding,
    now: datetime,
) -> None:
    execution_binding.validate()
    source_ids = tuple(entry.source_id for entry in entries)
    if (
        packet.registry_digest != candidate_registry_digest
        or packet.source_ids != source_ids
        or packet.source_endpoint_digest != oa112_bootstrap_source_endpoint_digest(entries)
        or packet.head_sha != execution_binding.head_sha
        or packet.tree_sha256 != execution_binding.tree_sha256
        or packet.ci_digest != execution_binding.ci_digest
        or packet.security_digest != execution_binding.security_digest
    ):
        raise Oa112DownloadError("OA112_PACKET_EXECUTION_BINDING")
    if (
        _HEAD_SHA.fullmatch(packet.head_sha) is None
        or any(
            _SHA256.fullmatch(value) is None
            for value in (
                packet.tree_sha256,
                packet.ci_digest,
                packet.security_digest,
                packet.registry_digest,
                packet.source_endpoint_digest,
            )
        )
        or packet.logical_call_cap != len(entries)
        or packet.physical_call_cap != len(entries)
        or not 1 <= packet.maximum_source_bytes <= _MAX_SOURCE_BYTES
        or not packet.maximum_source_bytes <= packet.maximum_total_bytes <= _MAX_TOTAL_BYTES
        or packet.maximum_total_bytes > packet.maximum_source_bytes * len(entries)
        or packet.cost_cap_microusd != 0
        or packet.retry_count != 0
        or packet.tracked_raw_artifact_count != 0
        or packet.provider != "OA112_OFFICIAL_HTTPS"
        or packet.operation != "OA112_CANDIDATE_QUARANTINE_DOWNLOAD"
        or packet.query != "NONE"
        or packet.symbol != "NONE"
        or packet.date != "NONE"
        or _OPERATOR.fullmatch(packet.operator) is None
        or _NONCE.fullmatch(packet.nonce) is None
        or not 1 <= packet.maximum_pages <= 500
        or packet.expires_at.tzinfo != UTC
        or not now < packet.expires_at <= now + timedelta(hours=1)
        or any(
            not all(
                (
                    entry.machine_fetch_allowed,
                    entry.local_processing_allowed,
                    entry.external_embedding_allowed,
                    entry.external_generation_allowed,
                )
            )
            for entry in entries
        )
    ):
        raise Oa112DownloadError("OA112_PACKET_INVALID")


def _open_bootstrap_cache_layout(root: Path) -> tuple[int, int, int]:
    root_fd = downloader._open_private_root(root, error_code="OA112_BOOTSTRAP_CACHE_UNSAFE")
    quarantine_fd = -1
    staging_fd = -1
    try:
        quarantine_fd = downloader._open_or_create_private_directory(root_fd, "oa112-quarantine")
        staging_fd = downloader._open_or_create_private_directory(root_fd, "bootstrap-staging")
    except BaseException:
        if staging_fd >= 0:
            os.close(staging_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        os.close(root_fd)
        raise
    return root_fd, quarantine_fd, staging_fd


def _read_quarantined_raw(
    *,
    quarantine_fd: int,
    entry: Oa112BootstrapCandidate,
    maximum_source_bytes: int,
    maximum_pages: int,
) -> Oa112BootstrapDownloadedSourceReceipt | None:
    return _read_bootstrap_cached_raw(
        directory_fd=quarantine_fd,
        entry=entry,
        maximum_source_bytes=maximum_source_bytes,
        maximum_pages=maximum_pages,
        state="REUSED",
    )


def _read_promoted_raw(
    *,
    raw_fd: int,
    entry: Oa112BootstrapCandidate,
    maximum_source_bytes: int,
    maximum_pages: int,
) -> Oa112BootstrapDownloadedSourceReceipt | None:
    """crash 뒤 active registry publication 전 `oa-raw`로 이동한 source를 recovery input으로 읽는다."""

    return _read_bootstrap_cached_raw(
        directory_fd=raw_fd,
        entry=entry,
        maximum_source_bytes=maximum_source_bytes,
        maximum_pages=maximum_pages,
        state="PROMOTED",
    )


def _read_bootstrap_cached_raw(
    *,
    directory_fd: int,
    entry: Oa112BootstrapCandidate,
    maximum_source_bytes: int,
    maximum_pages: int,
    state: str,
) -> Oa112BootstrapDownloadedSourceReceipt | None:
    name = oa112_bootstrap_quarantine_filename(entry)
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Oa112DownloadError("OA112_BOOTSTRAP_CACHE_UNSAFE") from error
    try:
        downloader._validate_private_regular(metadata)
        payload = downloader._read_private_file(directory_fd, name, maximum=maximum_source_bytes)
        downloader._validate_complete_payload(
            payload=payload,
            mime_type=entry.mime_type,
            maximum_pages=maximum_pages,
        )
    except Oa112DownloadError:
        raise
    return Oa112BootstrapDownloadedSourceReceipt(
        source_id=entry.source_id,
        source_revision_id=entry.source_revision_id,
        raw_content_sha256=hashlib.sha256(payload).hexdigest(),
        bytes_read=len(payload),
        state=state,
    )


def _download_bootstrap_candidate(
    *,
    quarantine_fd: int,
    staging_fd: int,
    entry: Oa112BootstrapCandidate,
    maximum_source_bytes: int,
    maximum_pages: int,
    resolver: Oa112DnsResolver,
    transport: Oa112HttpsTransport,
    deadline: downloader._Oa112SourceDeadline,
    record_provider_request: Callable[[], None],
) -> Oa112BootstrapDownloadedSourceReceipt:
    if maximum_source_bytes <= 0:
        raise Oa112DownloadError("OA112_PACKET_TOTAL_BYTE_CAP")
    part_name = f"{entry.source_id}.bootstrap.part"
    final_name = oa112_bootstrap_quarantine_filename(entry)
    if downloader._lstat_or_none(staging_fd, part_name) is not None:
        raise Oa112DownloadError("OA112_BOOTSTRAP_STAGING_UNSAFE")
    try:
        part_fd = os.open(part_name, downloader._WRITE_NEW_FLAGS, 0o600, dir_fd=staging_fd)
        downloader._validate_private_regular(os.fstat(part_fd))
    except OSError as error:
        raise Oa112DownloadError("OA112_BOOTSTRAP_STAGING_UNSAFE") from error
    bytes_written = 0
    try:
        with downloader._open_checked_response(
            entry.canonical_url,
            resolver=resolver,
            transport=transport,
            deadline=deadline,
            record_provider_request=record_provider_request,
        ) as response:
            plan = _validate_bootstrap_response(
                response=response,
                entry=entry,
                maximum_source_bytes=maximum_source_bytes,
                deadline=deadline,
            )
            bytes_written = downloader._stream_to_part(
                response=response,
                part_fd=part_fd,
                initial_bytes=0,
                maximum_source_bytes=maximum_source_bytes,
                declared_bytes=plan.declared_bytes,
                expected_total_bytes=plan.total_bytes,
                deadline=deadline,
            )
        deadline.remaining_seconds()
        os.fsync(part_fd)
    except Oa112DownloadError:
        _remove_bootstrap_part(staging_fd=staging_fd, part_name=part_name)
        raise
    except OSError as error:
        _remove_bootstrap_part(staging_fd=staging_fd, part_name=part_name)
        raise Oa112DownloadError("OA112_DOWNLOAD_TRANSPORT") from error
    finally:
        os.close(part_fd)
    try:
        payload = downloader._read_private_file(staging_fd, part_name, maximum=maximum_source_bytes)
        if len(payload) != bytes_written:
            raise Oa112DownloadError("OA112_BOOTSTRAP_STAGING_UNSAFE")
        downloader._validate_complete_payload(
            payload=payload,
            mime_type=entry.mime_type,
            maximum_pages=maximum_pages,
        )
        observed_hash = hashlib.sha256(payload).hexdigest()
        downloader._publish_part_to_raw(
            staging_fd=staging_fd,
            raw_fd=quarantine_fd,
            part_name=part_name,
            raw_name=final_name,
        )
        os.fsync(staging_fd)
        os.fsync(quarantine_fd)
    except Oa112DownloadError:
        _remove_bootstrap_part(staging_fd=staging_fd, part_name=part_name)
        raise
    except OSError as error:
        _remove_bootstrap_part(staging_fd=staging_fd, part_name=part_name)
        raise Oa112DownloadError("OA112_BOOTSTRAP_STAGING_UNSAFE") from error
    return Oa112BootstrapDownloadedSourceReceipt(
        source_id=entry.source_id,
        source_revision_id=entry.source_revision_id,
        raw_content_sha256=observed_hash,
        bytes_read=bytes_written,
        state="QUARANTINED",
    )


def _validate_bootstrap_response(
    *,
    response: downloader.Oa112DownloadResponse,
    entry: Oa112BootstrapCandidate,
    maximum_source_bytes: int,
    deadline: downloader._Oa112SourceDeadline,
) -> downloader._ResponsePlan:
    if isinstance(response, downloader._DeferredHeaderResponse):
        response.bind_headers(
            {
                "Accept": entry.mime_type,
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Host": downloader._hostname(entry.canonical_url),
                "User-Agent": "capstone-oa112-local-materializer/1",
            }
        )
    deadline.remaining_seconds()
    headers = downloader._normalized_headers(response.headers)
    status = response.status_code
    deadline.remaining_seconds()
    if "location" in headers or status != 200 or "content-range" in headers:
        raise Oa112DownloadError("OA112_DOWNLOAD_REDIRECT_OR_STATUS")
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise Oa112DownloadError("OA112_DOWNLOAD_ENCODING")
    transfer_encoding = headers.get("transfer-encoding")
    if transfer_encoding is not None and transfer_encoding.lower() != "chunked":
        raise Oa112DownloadError("OA112_DOWNLOAD_TRANSFER_ENCODING")
    mime_type = headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if mime_type != entry.mime_type:
        raise Oa112DownloadError("OA112_DOWNLOAD_MIME")
    declared_bytes = downloader._content_length(headers)
    if declared_bytes is not None and (declared_bytes <= 0 or declared_bytes > maximum_source_bytes):
        raise Oa112DownloadError("OA112_DOWNLOAD_BYTE_BOUND")
    return downloader._ResponsePlan(declared_bytes=declared_bytes, total_bytes=declared_bytes)


def _remove_bootstrap_part(*, staging_fd: int, part_name: str) -> None:
    try:
        metadata = downloader._lstat_or_none(staging_fd, part_name)
        if metadata is not None:
            downloader._validate_private_regular(metadata)
            os.unlink(part_name, dir_fd=staging_fd)
    except (OSError, Oa112DownloadError):
        return


def _write_bootstrap_run_receipt(
    *,
    control_root: Path,
    packet: Oa112DownloadPacket,
    candidate_registry_digest: str,
    state: str,
    code: str,
    receipt: Oa112BootstrapDownloadReceipt,
) -> None:
    if state not in {"FAILED", "SUCCEEDED"} or not code:
        raise Oa112DownloadError("OA112_BOOTSTRAP_RECEIPT_UNAVAILABLE")
    packet_digest = downloader._packet_digest(packet)
    payload = {
        "candidateRegistryDigest": candidate_registry_digest,
        "code": code,
        "packetDigest": packet_digest,
        "receipt": receipt.content_free_projection(),
        "state": state,
    }
    content = _canonical_json_bytes(payload)
    if len(content) > _MAX_PACKET_BYTES:
        raise Oa112DownloadError("OA112_BOOTSTRAP_RECEIPT_UNAVAILABLE")
    root_fd = -1
    receipts_fd = -1
    try:
        root_fd = downloader._open_private_root(
            control_root,
            error_code="OA112_BOOTSTRAP_RECEIPT_UNAVAILABLE",
        )
        receipts_fd = downloader._open_or_create_private_directory(root_fd, "oa112-bootstrap-receipts")
        downloader._write_new_private_file(receipts_fd, f"{packet_digest}.json", content)
        os.fsync(receipts_fd)
    except (OSError, Oa112DownloadError) as error:
        if isinstance(error, Oa112DownloadError):
            raise Oa112DownloadError("OA112_BOOTSTRAP_RECEIPT_UNAVAILABLE") from error
        raise Oa112DownloadError("OA112_BOOTSTRAP_RECEIPT_UNAVAILABLE") from error
    finally:
        if receipts_fd >= 0:
            os.close(receipts_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _load_complete_bootstrap_receipt(
    *,
    registry_root: Path,
    registry: Oa112BootstrapCandidateRegistry,
) -> dict[str, str]:
    """active 승격 전 one-shot 성공 receipt의 exact observed hash set을 다시 검증한다.

    quarantine file만 존재한다고 source provenance를 주장하지 않는다. receipt는 packet consumer가 raw
    URL/body 없이 저장한 result이며, candidate registry digest와 112 source order가 모두 같아야 한다.
    """

    try:
        files = list_approved_regular_files(
            approved_root=registry_root,
            relative_directory="oa112-bootstrap-receipts",
            max_entries=128,
            max_bytes=_MAX_PACKET_BYTES,
        )
    except RagSafeIoError as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_REQUIRED") from error
    accepted: list[dict[str, str]] = []
    for name, content in files.items():
        if _SHA256.fullmatch(name.removesuffix(".json")) is None or not name.endswith(".json"):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
        try:
            payload = _parse_canonical_json(content)
            if _required_text(payload, "packetDigest", maximum=64) != name.removesuffix(".json"):
                raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
            state = payload.get("state")
            if state == "SUCCEEDED":
                observed = _validate_complete_bootstrap_receipt_payload(
                    payload=payload,
                    registry=registry,
                )
                accepted.append(observed)
            elif state == "FAILED":
                _validate_failed_bootstrap_receipt_payload(payload=payload, registry=registry)
            else:
                raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
        except Oa112BootstrapError as error:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID") from error
    if len(accepted) != 1:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_REQUIRED")
    return accepted[0]


def _has_complete_bootstrap_success_receipt(
    *,
    registry_root: Path,
    registry: Oa112BootstrapCandidateRegistry,
) -> bool:
    """raw 재사용만 남은 recovery도 성공 receipt를 빠뜨리지 않도록 확인한다."""

    try:
        _load_complete_bootstrap_receipt(registry_root=registry_root, registry=registry)
    except Oa112BootstrapError as error:
        if str(error) == "OA112_BOOTSTRAP_RECEIPT_REQUIRED":
            return False
        raise Oa112DownloadError("OA112_BOOTSTRAP_RECEIPT_INVALID") from error
    return True


def _validate_complete_bootstrap_receipt_payload(
    *,
    payload: Mapping[str, object],
    registry: Oa112BootstrapCandidateRegistry,
) -> dict[str, str]:
    _require_exact_keys(
        payload,
        frozenset({"candidateRegistryDigest", "code", "packetDigest", "receipt", "state"}),
    )
    if (
        payload.get("candidateRegistryDigest") != registry.registry_digest
        or payload.get("code") != "OA112_BOOTSTRAP_QUARANTINE_READY"
        or payload.get("state") != "SUCCEEDED"
        or _SHA256.fullmatch(_required_text(payload, "packetDigest", maximum=64)) is None
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
    receipt = _required_mapping(payload, "receipt")
    _require_exact_keys(
        receipt,
        frozenset(
            {
                "attemptCount",
                "physicalCallCount",
                "quarantinedSourceCount",
                "reusedSourceCount",
                "sources",
            }
        ),
    )
    attempts = _required_int(receipt, "attemptCount")
    physical = _required_int(receipt, "physicalCallCount")
    quarantined = _required_int(receipt, "quarantinedSourceCount")
    reused = _required_int(receipt, "reusedSourceCount")
    sources = receipt.get("sources")
    if (
        not isinstance(sources, list)
        or len(sources) != 112
        or attempts != physical
        or not 0 <= physical <= 112
        or quarantined < 0
        or reused < 0
        or quarantined != physical
        or quarantined + reused != 112
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
    observed: dict[str, str] = {}
    expected = registry.active_entries
    for raw, entry in zip(sources, expected, strict=True):
        if not isinstance(raw, Mapping):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
        _require_exact_keys(
            raw,
            frozenset({"bytesRead", "rawContentSha256", "sourceId", "sourceRevisionId", "state"}),
        )
        if (
            raw.get("sourceId") != entry.source_id
            or raw.get("sourceRevisionId") != entry.source_revision_id
            or raw.get("state") not in {"QUARANTINED", "REUSED"}
            or _required_int(raw, "bytesRead") <= 0
        ):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
        observed[entry.source_id] = _required_sha256(raw, "rawContentSha256")
    if (
        sum(raw.get("state") == "QUARANTINED" for raw in sources) != quarantined
        or sum(raw.get("state") == "REUSED" for raw in sources) != reused
        or len(observed) != 112
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
    return observed


def _validate_failed_bootstrap_receipt_payload(
    *,
    payload: Mapping[str, object],
    registry: Oa112BootstrapCandidateRegistry,
) -> None:
    """이전 failed packet은 audit로 보존하되 later success activation을 영구 차단하지 않는다."""

    _require_exact_keys(
        payload,
        frozenset({"candidateRegistryDigest", "code", "packetDigest", "receipt", "state"}),
    )
    if (
        payload.get("state") != "FAILED"
        or not _required_text(payload, "code", maximum=128)
        or _SHA256.fullmatch(_required_text(payload, "packetDigest", maximum=64)) is None
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
    receipt = _required_mapping(payload, "receipt")
    _require_exact_keys(
        receipt,
        frozenset(
            {
                "attemptCount",
                "physicalCallCount",
                "quarantinedSourceCount",
                "reusedSourceCount",
                "sources",
            }
        ),
    )
    attempts = _required_int(receipt, "attemptCount")
    physical = _required_int(receipt, "physicalCallCount")
    quarantined = _required_int(receipt, "quarantinedSourceCount")
    reused = _required_int(receipt, "reusedSourceCount")
    sources = receipt.get("sources")
    if (
        _SHA256.fullmatch(_required_text(payload, "candidateRegistryDigest", maximum=64)) is None
        or not isinstance(sources, list)
        or len(sources) > 112
        or min(attempts, physical, quarantined, reused) < 0
        or physical > attempts
        or quarantined > physical
        or quarantined + reused != len(sources)
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
    if payload.get("candidateRegistryDigest") != registry.registry_digest:
        return
    by_source_id = {entry.source_id: entry for entry in registry.active_entries}
    seen: set[str] = set()
    for raw in sources:
        if not isinstance(raw, Mapping):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
        _require_exact_keys(
            raw,
            frozenset({"bytesRead", "rawContentSha256", "sourceId", "sourceRevisionId", "state"}),
        )
        source_id = _required_text(raw, "sourceId", maximum=128)
        entry = by_source_id.get(source_id)
        if (
            entry is None
            or source_id in seen
            or raw.get("sourceRevisionId") != entry.source_revision_id
            or raw.get("state") not in {"QUARANTINED", "REUSED"}
            or _required_int(raw, "bytesRead") <= 0
        ):
            raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
        _required_sha256(raw, "rawContentSha256")
        seen.add(source_id)


def _validate_complete_bootstrap_quarantine(
    *,
    quarantine_fd: int,
    raw_fd: int,
    entries: tuple[Oa112BootstrapCandidate, ...],
    observed_from_receipt: Mapping[str, str],
) -> dict[str, str]:
    observed: dict[str, str] = {}
    for entry in entries:
        quarantined = _read_quarantined_raw(
            quarantine_fd=quarantine_fd,
            entry=entry,
            maximum_source_bytes=_MAX_SOURCE_BYTES,
            maximum_pages=500,
        )
        promoted = _read_promoted_raw(
            raw_fd=raw_fd,
            entry=entry,
            maximum_source_bytes=_MAX_SOURCE_BYTES,
            maximum_pages=500,
        )
        if quarantined is not None and promoted is not None:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_PROMOTION_COLLISION")
        receipt = promoted or quarantined
        if receipt is None:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_QUARANTINE_INCOMPLETE")
        if observed_from_receipt.get(entry.source_id) != receipt.raw_content_sha256:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_QUARANTINE_DRIFT")
        observed[entry.source_id] = receipt.raw_content_sha256
    if len(observed) != 112:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_QUARANTINE_INCOMPLETE")
    return observed


def _promote_quarantine_to_raw(
    *,
    quarantine_fd: int,
    raw_fd: int,
    entries: tuple[Oa112BootstrapCandidate, ...],
    observed_hashes: Mapping[str, str],
) -> None:
    for entry in entries:
        name = oa112_bootstrap_quarantine_filename(entry)
        raw_metadata = downloader._lstat_or_none(raw_fd, name)
        quarantine_metadata = downloader._lstat_or_none(quarantine_fd, name)
        if raw_metadata is not None and quarantine_metadata is not None:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_PROMOTION_COLLISION")
        if raw_metadata is not None:
            try:
                downloader._validate_private_regular(raw_metadata)
                payload = downloader._read_private_file(raw_fd, name, maximum=_MAX_SOURCE_BYTES)
            except Oa112DownloadError as error:
                raise Oa112BootstrapError("OA112_BOOTSTRAP_PROMOTION_COLLISION") from error
            if hashlib.sha256(payload).hexdigest() != observed_hashes.get(entry.source_id):
                raise Oa112BootstrapError("OA112_BOOTSTRAP_PROMOTION_COLLISION")
            continue
        if quarantine_metadata is None:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_QUARANTINE_INCOMPLETE")
        try:
            downloader._validate_private_regular(quarantine_metadata)
            os.rename(name, name, src_dir_fd=quarantine_fd, dst_dir_fd=raw_fd)
        except OSError as error:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_PROMOTION_UNAVAILABLE") from error
    os.fsync(quarantine_fd)
    os.fsync(raw_fd)


def _active_registry_payload(
    *,
    registry: Oa112BootstrapCandidateRegistry,
    observed_hashes: Mapping[str, str],
) -> dict[str, object]:
    active_sources: list[dict[str, object]] = []
    for candidate in registry.active_entries:
        observed = observed_hashes.get(candidate.source_id)
        if observed is None or _SHA256.fullmatch(observed) is None:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_QUARANTINE_INCOMPLETE")
        active_sources.append(
            {
                "languageTags": list(candidate.language_tags),
                "retrievalTopics": list(candidate.retrieval_topics),
                "sourceCard": {
                    "accessEvidence": {
                        "accessCheckedAt": candidate.access_checked_at,
                        "accessEvidenceDigest": candidate.access_evidence_sha256,
                        "verificationState": "VERIFIED",
                    },
                    "activeOa112Eligible": True,
                    "authors": list(candidate.authors),
                    "canonicalUrl": candidate.canonical_url,
                    "canonicalUrlSha256": hashlib.sha256(
                        candidate.canonical_url.encode("utf-8")
                    ).hexdigest(),
                    "contractId": "rag-source-card-v4",
                    "identifier": {
                        "scheme": candidate.identifier_scheme,
                        "value": candidate.identifier_value,
                    },
                    "licenseEvidenceDigest": candidate.license_evidence_sha256,
                    "mimeType": candidate.mime_type,
                    "permissions": {
                        "externalEmbeddingAllowed": candidate.external_embedding_allowed,
                        "externalGenerationAllowed": candidate.external_generation_allowed,
                        "localProcessingAllowed": candidate.local_processing_allowed,
                        "machineFetchAllowed": candidate.machine_fetch_allowed,
                    },
                    "rawContentSha256": observed,
                    "revision": candidate.revision,
                    "revisionDate": candidate.revision_date,
                    "schemaVersion": 4,
                    "sourceId": candidate.source_id,
                    "sourceKind": "OPEN_ACCESS_DOCUMENT",
                    "title": candidate.title,
                },
                "sourceRevisionId": candidate.source_revision_id,
                "trackId": candidate.track_id,
            }
        )
    payload: dict[str, object] = {
        "activeSourceCount": 112,
        "activeSources": active_sources,
        "automaticReservePromotion": False,
        "contractId": "rag-v2-oa112-local-activation-registry-v1",
        "registryDigest": None,
        "registryId": registry.registry_id,
        "reserveSourceCount": 0,
        "reserveSources": [],
        "schemaVersion": 1,
    }
    payload["registryDigest"] = canonical_oa112_active_registry_digest(payload)
    return payload


def _require_absent_private_leaf(*, root: Path, leaf: str, error_code: str) -> None:
    descriptor = -1
    try:
        descriptor = downloader._open_private_root(root, error_code=error_code)
        if downloader._lstat_or_none(descriptor, leaf) is not None:
            raise Oa112BootstrapError(error_code)
    except Oa112DownloadError as error:
        raise Oa112BootstrapError(error_code) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_canonical_json(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID") from error
    if (
        not text.endswith("\n")
        or "\r" in text
        or text.startswith("\ufeff")
        or unicodedata.normalize("NFC", text) != text
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError) as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID") from error
    if not isinstance(parsed, dict):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return parsed


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
        result[key] = value
    return result


def _required_mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return item


def _required_text(value: Mapping[str, object], key: str, *, maximum: int) -> str:
    item = value.get(key)
    if (
        not isinstance(item, str)
        or not item
        or item != item.strip()
        or len(item) > maximum
        or unicodedata.normalize("NFC", item) != item
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in item)
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return item


def _required_sha256(value: Mapping[str, object], key: str) -> str:
    item = _required_text(value, key, maximum=64)
    if _SHA256.fullmatch(item) is None:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return item


def _required_int(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_RECEIPT_INVALID")
    return item


def _required_track_id(value: Mapping[str, object]) -> str:
    track_id = _required_text(value, "trackId", maximum=128)
    if track_id not in OA_TRACK_IDS:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return track_id


def _required_date(value: Mapping[str, object], key: str) -> str:
    item = _required_text(value, key, maximum=10)
    try:
        parsed = datetime.fromisoformat(f"{item}T00:00:00+00:00")
    except ValueError as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID") from error
    if parsed.strftime("%Y-%m-%d") != item:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return item


def _required_utc(value: Mapping[str, object], key: str) -> str:
    item = _required_text(value, key, maximum=32)
    if not item.endswith("Z"):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    try:
        parsed = datetime.fromisoformat(item.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID") from error
    if parsed.tzinfo != UTC or parsed.isoformat().replace("+00:00", "Z") != item:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    return item


def _text_array(
    value: object,
    *,
    maximum: int,
    pattern: re.Pattern[str] | None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    if len(value) != len(set(value)):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    if pattern is not None and any(pattern.fullmatch(item) is None for item in value):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")
    ordered = tuple(sorted(value, key=lambda item: item.encode("utf-8")))
    if tuple(value) != ordered:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_ORDER_INVALID")
    return ordered


def _curation_authors(value: object) -> tuple[str, ...]:
    """upstream author order는 input에서 허용하되 active card에는 canonical sort로 고정한다."""

    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    if any(
        not isinstance(item, str)
        or not item
        or item != item.strip()
        or len(item) > 300
        or unicodedata.normalize("NFC", item) != item
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in item)
        for item in value
    ):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    if len(value) != len(set(value)):
        raise Oa112BootstrapError("OA112_BOOTSTRAP_CURATION_INVALID")
    return tuple(value)


def _require_exact_keys(value: Mapping[str, object], keys: frozenset[str]) -> None:
    if set(value) != keys:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID")


def _validate_public_https_url(value: str) -> None:
    try:
        active_registry._validate_public_https_url(value)
    except Oa112ActiveRegistryError as error:
        raise Oa112BootstrapError("OA112_BOOTSTRAP_REGISTRY_INVALID") from error


def _is_leaf(relative_path: str) -> bool:
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path or "\x00" in relative_path:
        return False
    parts = PurePosixPath(relative_path).parts
    return len(parts) == 1 and parts[0] not in {"", ".", ".."}


def _is_private_root(root: Path) -> bool:
    try:
        metadata = root.lstat()
    except OSError:
        return False
    return (
        root.is_absolute()
        and ".." not in root.parts
        and stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )
