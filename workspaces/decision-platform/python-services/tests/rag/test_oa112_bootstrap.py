from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator, Mapping, Sequence

import pytest

from app.rag.oa112_active_registry import load_oa112_active_registry
from app.rag.oa112_bootstrap import (
    Oa112BootstrapError,
    Oa112BootstrapCandidateRegistry,
    activate_oa112_bootstrap_quarantine,
    canonical_oa112_bootstrap_candidate_registry_digest,
    download_oa112_bootstrap_quarantine,
    oa112_bootstrap_quarantine_filename,
    oa112_bootstrap_source_endpoint_digest,
    validate_oa112_bootstrap_candidate_registry,
)
from app.rag.oa112_downloader import (
    Oa112DownloadBinding,
    Oa112DownloadError,
    Oa112DownloadPacket,
    Oa112DownloadResponse,
)
from app.rag.oa_release_manifest import OA_TRACK_IDS


def test_bootstrap_download_quarantines_observed_raw_and_records_content_free_receipt(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    registry = _registry()
    candidate = registry.active_entries[0]
    body = f"{candidate.source_id}\n".encode("utf-8")
    _write_quarantine(cache_root=cache_root, registry=registry, skip_source_id=candidate.source_id)
    transport = _FixtureTransport([_Response(200, _headers("text/plain", body), body)])

    receipt = download_oa112_bootstrap_quarantine(
        registry=registry,
        packet=_packet(registry),
        execution_binding=_binding(),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=transport,
    )

    assert receipt.attempt_count == receipt.physical_call_count == 1
    assert receipt.quarantined_source_count == 1
    assert receipt.reused_source_count == 111
    assert receipt.sources[0].raw_content_sha256 == hashlib.sha256(body).hexdigest()
    assert (cache_root / "oa112-quarantine" / "src_oa_bootstrap_000.txt").read_bytes() == body
    assert not (cache_root / "oa-raw").exists()
    assert transport.requests[0].headers["Accept"] == "text/plain"
    serialized = json.dumps(receipt.content_free_projection(), sort_keys=True)
    assert candidate.canonical_url not in serialized
    assert "path" not in serialized.lower()


def test_bootstrap_download_checks_current_evidence_before_consuming_packet_or_transport(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    registry = _registry()
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_EXECUTION_BINDING"):
        download_oa112_bootstrap_quarantine(
            registry=registry,
            packet=_packet(registry),
            execution_binding=Oa112DownloadBinding(
                head_sha="e" * 40,
                tree_sha256="b" * 64,
                ci_digest="c" * 64,
                security_digest="d" * 64,
            ),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert transport.requests == []
    assert not (control_root / "oa112-packet-claims").exists()


def test_bootstrap_activation_requires_all_112_quarantine_files_before_publishing_registry(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    registry = _registry()
    quarantine = cache_root / "oa112-quarantine"
    quarantine.mkdir(mode=0o700)
    os.chmod(quarantine, 0o700)
    for candidate in registry.active_entries:
        path = quarantine / oa112_bootstrap_quarantine_filename(candidate)
        path.write_bytes(f"{candidate.source_id}\n".encode("utf-8"))
        os.chmod(path, 0o600)
    _write_complete_receipt(control_root=control_root, registry=registry)

    active = activate_oa112_bootstrap_quarantine(
        registry=registry,
        local_cache_root=cache_root,
        registry_root=control_root,
        registry_relative_path="oa112-active-registry.v1.json",
    )

    assert active.active_source_count == 112
    assert active.track_counts == {track_id: 8 for track_id in OA_TRACK_IDS}
    assert len(tuple((cache_root / "oa-raw").iterdir())) == 112
    assert tuple(quarantine.iterdir()) == ()
    loaded = load_oa112_active_registry(
        approved_root=control_root,
        relative_path="oa112-active-registry.v1.json",
    )
    assert loaded.registry_digest == active.registry_digest
    assert (control_root / "oa112-active-registry.v1.json").stat().st_mode & 0o777 == 0o600


def test_bootstrap_activation_keeps_registry_unpublished_when_a_quarantine_source_is_missing(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    registry = _registry()
    quarantine = cache_root / "oa112-quarantine"
    quarantine.mkdir(mode=0o700)
    os.chmod(quarantine, 0o700)
    for candidate in registry.active_entries[:-1]:
        path = quarantine / oa112_bootstrap_quarantine_filename(candidate)
        path.write_bytes(f"{candidate.source_id}\n".encode("utf-8"))
        os.chmod(path, 0o600)
    _write_complete_receipt(control_root=control_root, registry=registry)

    with pytest.raises(Oa112BootstrapError, match="OA112_BOOTSTRAP_QUARANTINE_INCOMPLETE"):
        activate_oa112_bootstrap_quarantine(
            registry=registry,
            local_cache_root=cache_root,
            registry_root=control_root,
            registry_relative_path="oa112-active-registry.v1.json",
        )

    assert not (control_root / "oa112-active-registry.v1.json").exists()
    raw = cache_root / "oa-raw"
    assert not raw.exists() or tuple(raw.iterdir()) == ()


def test_bootstrap_activation_ignores_a_prior_failed_receipt_after_a_later_success(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    registry = _registry()
    _write_quarantine(cache_root=cache_root, registry=registry)
    _write_complete_receipt(control_root=control_root, registry=registry)
    _write_failed_receipt(control_root=control_root, registry=registry)

    active = activate_oa112_bootstrap_quarantine(
        registry=registry,
        local_cache_root=cache_root,
        registry_root=control_root,
        registry_relative_path="oa112-active-registry.v1.json",
    )

    assert active.active_source_count == 112


def test_bootstrap_activation_recovers_after_partial_quarantine_promotion(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    registry = _registry()
    quarantine = _write_quarantine(cache_root=cache_root, registry=registry)
    _write_complete_receipt(control_root=control_root, registry=registry)
    raw = cache_root / "oa-raw"
    raw.mkdir(mode=0o700)
    os.chmod(raw, 0o700)
    first = registry.active_entries[0]
    name = oa112_bootstrap_quarantine_filename(first)
    os.rename(quarantine / name, raw / name)

    active = activate_oa112_bootstrap_quarantine(
        registry=registry,
        local_cache_root=cache_root,
        registry_root=control_root,
        registry_relative_path="oa112-active-registry.v1.json",
    )

    assert active.active_source_count == 112
    assert len(tuple(raw.iterdir())) == 112


def test_candidate_registry_requires_exact_ordered_14_by_8_and_all_four_permissions() -> None:
    payload = _registry_payload()
    registry = validate_oa112_bootstrap_candidate_registry(payload)

    assert registry.active_source_count == 112
    assert registry.active_entries[0].source_id == "src_oa_bootstrap_000"
    assert registry.active_entries[-1].source_id == "src_oa_bootstrap_111"

    sources = payload["candidateSources"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    permissions = source["permissions"]
    assert isinstance(permissions, dict)
    permissions["externalGenerationAllowed"] = False
    payload["registryDigest"] = canonical_oa112_bootstrap_candidate_registry_digest(payload)
    with pytest.raises(Oa112BootstrapError, match="OA112_BOOTSTRAP_REGISTRY_RIGHTS_REQUIRED"):
        validate_oa112_bootstrap_candidate_registry(payload)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    cache_root = tmp_path / "cache"
    control_root = tmp_path / "control"
    cache_root.mkdir(mode=0o700)
    control_root.mkdir(mode=0o700)
    os.chmod(cache_root, 0o700)
    os.chmod(control_root, 0o700)
    return cache_root, control_root


def _registry() -> Oa112BootstrapCandidateRegistry:
    return validate_oa112_bootstrap_candidate_registry(_registry_payload())


def _registry_payload() -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        for source_index in range(8):
            ordinal = track_index * 8 + source_index
            candidates.append(
                _candidate_payload(
                    index=ordinal,
                    source_id=f"src_oa_bootstrap_{ordinal:03d}",
                    track_id=track_id,
                )
            )
    payload: dict[str, object] = {
        "automaticReservePromotion": False,
        "candidateSourceCount": 112,
        "candidateSources": candidates,
        "contractId": "rag-v2-oa112-bootstrap-candidate-registry-v1",
        "registryDigest": None,
        "registryId": "oa112-bootstrap-fixture-v1",
        "reserveSourceCount": 0,
        "reserveSources": [],
        "schemaVersion": 1,
    }
    payload["registryDigest"] = canonical_oa112_bootstrap_candidate_registry_digest(payload)
    return payload


def _candidate_payload(*, index: int, source_id: str, track_id: str) -> dict[str, object]:
    canonical_url = f"https://official.example.com/oa/{source_id}.txt"
    return {
        "accessEvidence": {
            "accessCheckedAt": "2026-08-09T00:00:00Z",
            "accessEvidenceDigest": hashlib.sha256(f"access-{index}".encode()).hexdigest(),
            "verificationState": "VERIFIED",
        },
        "authors": [f"Fixture Author {index}"],
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": hashlib.sha256(canonical_url.encode()).hexdigest(),
        "identifier": {"scheme": "ARXIV", "value": f"2608.{index:05d}v1"},
        "languageTags": ["en"],
        "licenseEvidenceDigest": hashlib.sha256(f"license-{index}".encode()).hexdigest(),
        "mimeType": "text/plain",
        "permissions": {
            "externalEmbeddingAllowed": True,
            "externalGenerationAllowed": True,
            "localProcessingAllowed": True,
            "machineFetchAllowed": True,
        },
        "retrievalTopics": ["METHODOLOGY"],
        "revision": f"2608.{index:05d}v1",
        "revisionDate": "2026-08-09",
        "sourceId": source_id,
        "sourceRevisionId": f"srv_{source_id[4:]}",
        "title": f"Fixture OA source {index}",
        "trackId": track_id,
    }


def _packet(registry: Oa112BootstrapCandidateRegistry) -> Oa112DownloadPacket:
    return Oa112DownloadPacket(
        approval_id="oa112-bootstrap-fixture-001",
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        ci_digest="c" * 64,
        security_digest="d" * 64,
        registry_digest=registry.registry_digest,
        source_endpoint_digest=oa112_bootstrap_source_endpoint_digest(registry.active_entries),
        source_ids=registry.active_source_ids,
        provider="OA112_OFFICIAL_HTTPS",
        operation="OA112_CANDIDATE_QUARANTINE_DOWNLOAD",
        query="NONE",
        symbol="NONE",
        date="NONE",
        logical_call_cap=112,
        physical_call_cap=112,
        maximum_source_bytes=1024 * 1024,
        maximum_total_bytes=1024 * 1024 * 112,
        cost_cap_microusd=0,
        retry_count=0,
        tracked_raw_artifact_count=0,
        operator="local-operator",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        nonce="nonce-bootstrap-fixture-0001",
        maximum_pages=500,
    )


def _binding() -> Oa112DownloadBinding:
    return Oa112DownloadBinding(
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        ci_digest="c" * 64,
        security_digest="d" * 64,
    )


def _headers(mime_type: str, body: bytes) -> dict[str, str]:
    return {
        "Content-Encoding": "identity",
        "Content-Length": str(len(body)),
        "Content-Type": mime_type,
    }


def _write_quarantine(
    *,
    cache_root: Path,
    registry: Oa112BootstrapCandidateRegistry,
    skip_source_id: str | None = None,
) -> Path:
    quarantine = cache_root / "oa112-quarantine"
    quarantine.mkdir(mode=0o700)
    os.chmod(quarantine, 0o700)
    for candidate in registry.active_entries:
        if candidate.source_id == skip_source_id:
            continue
        path = quarantine / oa112_bootstrap_quarantine_filename(candidate)
        path.write_bytes(f"{candidate.source_id}\n".encode("utf-8"))
        os.chmod(path, 0o600)
    return quarantine


def _write_complete_receipt(*, control_root: Path, registry: Oa112BootstrapCandidateRegistry) -> None:
    receipts = control_root / "oa112-bootstrap-receipts"
    receipts.mkdir(mode=0o700)
    os.chmod(receipts, 0o700)
    source_receipts = []
    for candidate in registry.active_entries:
        body = f"{candidate.source_id}\n".encode("utf-8")
        source_receipts.append(
            {
                "bytesRead": len(body),
                "rawContentSha256": hashlib.sha256(body).hexdigest(),
                "sourceId": candidate.source_id,
                "sourceRevisionId": candidate.source_revision_id,
                "state": "QUARANTINED",
            }
        )
    payload = {
        "candidateRegistryDigest": registry.registry_digest,
        "code": "OA112_BOOTSTRAP_QUARANTINE_READY",
        "packetDigest": "a" * 64,
        "receipt": {
            "attemptCount": 112,
            "physicalCallCount": 112,
            "quarantinedSourceCount": 112,
            "reusedSourceCount": 0,
            "sources": source_receipts,
        },
        "state": "SUCCEEDED",
    }
    path = receipts / ("a" * 64 + ".json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _write_failed_receipt(*, control_root: Path, registry: Oa112BootstrapCandidateRegistry) -> None:
    path = control_root / "oa112-bootstrap-receipts" / ("b" * 64 + ".json")
    payload = {
        "candidateRegistryDigest": registry.registry_digest,
        "code": "OA112_DOWNLOAD_MIME",
        "packetDigest": "b" * 64,
        "receipt": {
            "attemptCount": 1,
            "physicalCallCount": 1,
            "quarantinedSourceCount": 0,
            "reusedSourceCount": 0,
            "sources": [],
        },
        "state": "FAILED",
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


@dataclass(frozen=True)
class _Request:
    hostname: str
    pinned_ip: str
    target: str
    headers: Mapping[str, str]


@dataclass
class _FixtureResolver:
    calls: int = 0

    def resolve(self, hostname: str, *, timeout_seconds: float) -> list[str]:
        assert hostname == "official.example.com"
        assert timeout_seconds > 0
        self.calls += 1
        return ["8.8.8.8"]


@dataclass(frozen=True)
class _Response:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        midpoint = len(self.body) // 2
        for chunk in (self.body[:midpoint], self.body[midpoint:]):
            if chunk:
                yield chunk

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0


class _FixtureConnection:
    peer_ip = "8.8.8.8"

    def __init__(self, transport: "_FixtureTransport") -> None:
        self._transport = transport

    def __enter__(self) -> "_FixtureConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
        read_timeout_seconds: float,
    ) -> Oa112DownloadResponse:
        assert read_timeout_seconds > 0
        previous = self._transport.requests[-1]
        self._transport.requests[-1] = _Request(
            hostname=previous.hostname,
            pinned_ip=previous.pinned_ip,
            target=target,
            headers=dict(headers),
        )
        if not self._transport.responses:
            raise AssertionError("unexpected transport call")
        return self._transport.responses.pop(0)


class _FixtureTransport:
    def __init__(self, responses: Sequence[Oa112DownloadResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[_Request] = []

    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline: object,
    ) -> _FixtureConnection:
        assert connect_timeout_seconds > 0
        assert read_timeout_seconds > 0
        assert deadline is not None
        self.requests.append(_Request(hostname, pinned_ip, "", {}))
        return _FixtureConnection(self)
