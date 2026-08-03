from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator, Mapping

import fitz
import pytest

from app.rag.oa112_active_registry import Oa112RegistryEntry
from app.rag.oa112_downloader import (
    Oa112DownloadError,
    Oa112DownloadPacket,
    consume_oa112_download_packet,
    download_oa112_local_cache,
)


def test_downloads_hash_verified_identity_encoded_raw_to_local_cache_without_receipt_path(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    body = b"bounded OA source\n"
    source = _source(body)
    transport = _FixtureTransport([_Response(200, _headers("text/plain", body), body)])

    receipt = download_oa112_local_cache(
        entries=(source,),
        registry_digest="a" * 64,
        packet=_packet(source, registry_digest="a" * 64),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=transport,
    )

    assert receipt.physical_call_count == 1
    assert receipt.downloaded_source_count == 1
    assert receipt.reused_source_count == 0
    assert receipt.sources[0].state == "DOWNLOADED"
    assert receipt.sources[0].raw_content_sha256 == source.raw_content_sha256
    assert "path" not in json.dumps(receipt.content_free_projection(), sort_keys=True).lower()
    assert transport.requests[0].headers["Accept-Encoding"] == "identity"
    assert "Range" not in transport.requests[0].headers
    assert (cache_root / "oa-raw" / "src_oa_fixture_001.txt").read_bytes() == body


@pytest.mark.parametrize(
    "response_kind",
    [
        "redirect",
        "mime_spoof",
        "source_drift",
    ],
)
def test_downloader_fails_closed_for_redirect_mime_spoof_and_source_drift(
    tmp_path: Path,
    response_kind: str,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    source = _source(b"expected body\n")
    response = {
        "redirect": _Response(302, {"Location": "https://elsewhere.example/"}, b""),
        "mime_spoof": _Response(200, _headers("application/pdf", b"not a PDF"), b"not a PDF"),
        "source_drift": _Response(200, _headers("text/plain", b"drift\n"), b"drift\n"),
    }[response_kind]
    transport = _FixtureTransport([response])

    with pytest.raises(Oa112DownloadError):
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="b" * 64,
            packet=_packet(source, registry_digest="b" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert len(transport.requests) == 1
    assert not (cache_root / "oa-raw" / "src_oa_fixture_001.txt").exists()


def test_downloader_resumes_only_with_new_packet_and_exact_range_response(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    body = b"first bounded chunk second bounded chunk\n"
    source = _source(body)
    first_transport = _FixtureTransport(
        [_Response(200, _headers("text/plain", body), body, fail_after_chunks=1)]
    )
    first_packet = _packet(source, registry_digest="c" * 64, nonce="nonce-resume-first-0001")

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_TRANSPORT"):
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="c" * 64,
            packet=first_packet,
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=first_transport,
        )

    partial_size = len(body) // 2
    resumed_body = body[partial_size:]
    resumed_transport = _FixtureTransport(
        [
            _Response(
                206,
                {
                    **_headers("text/plain", resumed_body),
                    "Content-Range": f"bytes {partial_size}-{len(body) - 1}/{len(body)}",
                },
                resumed_body,
            )
        ]
    )

    receipt = download_oa112_local_cache(
        entries=(source,),
        registry_digest="c" * 64,
        packet=_packet(source, registry_digest="c" * 64, nonce="nonce-resume-second-002"),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=resumed_transport,
    )

    assert receipt.sources[0].state == "RESUMED"
    assert resumed_transport.requests[0].headers["Range"] == f"bytes={partial_size}-"
    assert (cache_root / "oa-raw" / "src_oa_fixture_001.txt").read_bytes() == body


def test_packet_reuse_and_registry_digest_drift_stop_before_network(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    source = _source(b"body\n")
    packet = _packet(source, registry_digest="d" * 64)
    consume_oa112_download_packet(packet=packet, control_root=control_root)

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_ALREADY_CONSUMED"):
        consume_oa112_download_packet(packet=packet, control_root=control_root)

    transport = _FixtureTransport([])
    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_REGISTRY_DRIFT"):
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="e" * 64,
            packet=_packet(source, registry_digest="d" * 64, nonce="nonce-registry-drift-01"),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )
    assert transport.requests == []


def test_pdf_page_bound_is_checked_before_raw_cache_promotion(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    document = fitz.open()
    document.new_page()
    document.new_page()
    body = document.tobytes()
    document.close()
    source = _source(body, mime_type="application/pdf")
    transport = _FixtureTransport([_Response(200, _headers("application/pdf", body), body)])

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_PAGE_BOUND"):
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="f" * 64,
            packet=_packet(source, registry_digest="f" * 64, max_pages=1),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert not (cache_root / "oa-raw" / "src_oa_fixture_001.pdf").exists()


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    cache_root = tmp_path / "cache"
    control_root = tmp_path / "control"
    cache_root.mkdir(mode=0o700)
    control_root.mkdir(mode=0o700)
    os.chmod(cache_root, 0o700)
    os.chmod(control_root, 0o700)
    return cache_root, control_root


def _source(body: bytes, *, mime_type: str = "text/plain") -> Oa112RegistryEntry:
    canonical_url = "https://official.example.com/oa/fixture.txt"
    return Oa112RegistryEntry(
        source_id="src_oa_fixture_001",
        source_revision_id="srv_oa_fixture_001",
        document_id="doc_oa_fixture_000000000000000000000001",
        track_id="MICRO_GAME_INFO_MARKET_DESIGN",
        language_tags=("en",),
        retrieval_topics=("METHODOLOGY",),
        source_card={},
        title="Fixture OA source",
        canonical_url=canonical_url,
        raw_content_sha256=hashlib.sha256(body).hexdigest(),
        mime_type=mime_type,
        license_evidence_sha256="1" * 64,
        access_evidence_sha256="2" * 64,
        machine_fetch_allowed=True,
        local_processing_allowed=True,
        external_embedding_allowed=True,
        external_generation_allowed=True,
    )


def _packet(
    source: Oa112RegistryEntry,
    *,
    registry_digest: str,
    nonce: str = "nonce-download-fixture-0001",
    max_pages: int = 500,
) -> Oa112DownloadPacket:
    return Oa112DownloadPacket(
        approval_id="oa112-download-fixture-001",
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        ci_digest="c" * 64,
        security_digest="d" * 64,
        registry_digest=registry_digest,
        source_ids=(source.source_id,),
        logical_call_cap=1,
        physical_call_cap=1,
        maximum_source_bytes=1024 * 1024,
        maximum_total_bytes=1024 * 1024,
        cost_cap_microusd=0,
        retry_count=0,
        tracked_raw_artifact_count=0,
        operator="local-operator",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        nonce=nonce,
        maximum_pages=max_pages,
    )


def _headers(mime_type: str, body: bytes) -> dict[str, str]:
    return {
        "Content-Encoding": "identity",
        "Content-Length": str(len(body)),
        "Content-Type": mime_type,
    }


@dataclass(frozen=True)
class _Request:
    hostname: str
    pinned_ip: str
    target: str
    headers: Mapping[str, str]


@dataclass
class _FixtureResolver:
    calls: int = 0

    def resolve(self, hostname: str) -> list[str]:
        assert hostname == "official.example.com"
        self.calls += 1
        return ["8.8.8.8"]


@dataclass(frozen=True)
class _Response:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    fail_after_chunks: int | None = None

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        chunks = [
            self.body[: len(self.body) // 2],
            self.body[len(self.body) // 2 :],
        ]
        for index, chunk in enumerate(chunks, start=1):
            if chunk:
                yield chunk
            if self.fail_after_chunks == index:
                raise TimeoutError


class _FixtureConnection:
    peer_ip = "8.8.8.8"

    def __init__(self, transport: "_FixtureTransport") -> None:
        self._transport = transport

    def __enter__(self) -> "_FixtureConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, *, target: str, headers: Mapping[str, str]) -> _Response:
        self._transport.requests[-1] = _Request(
            hostname=self._transport.requests[-1].hostname,
            pinned_ip=self._transport.requests[-1].pinned_ip,
            target=target,
            headers=dict(headers),
        )
        if not self._transport.responses:
            raise AssertionError("unexpected transport call")
        return self._transport.responses.pop(0)


class _FixtureTransport:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests: list[_Request] = []

    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
    ) -> _FixtureConnection:
        assert connect_timeout_seconds > 0
        assert read_timeout_seconds > 0
        self.requests.append(_Request(hostname, pinned_ip, "", {}))
        return _FixtureConnection(self)
