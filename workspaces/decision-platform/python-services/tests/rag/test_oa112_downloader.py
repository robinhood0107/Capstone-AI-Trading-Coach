from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import fitz
import pytest

from app.rag.oa112_active_registry import Oa112RegistryEntry
from app.rag.oa112_downloader import (
    Oa112DownloadError,
    Oa112DownloadBinding,
    Oa112DownloadPacket,
    Oa112DownloadReceipt,
    Oa112DownloadResponse,
    consume_oa112_download_packet,
    download_oa112_local_cache as _download_oa112_local_cache,
    load_oa112_execution_binding,
    oa112_source_endpoint_digest,
)
from app.rag import oa112_downloader


def download_oa112_local_cache(**kwargs: Any) -> Oa112DownloadReceipt:
    """기존 transport fixture는 fixed current execution binding으로만 downloader를 연다."""

    kwargs.setdefault(
        "execution_binding",
        _binding() if kwargs.get("packet") is not None else None,
    )
    return _download_oa112_local_cache(**kwargs)


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

    assert receipt.attempt_count == 1
    assert receipt.physical_call_count == 1
    assert receipt.downloaded_source_count == 1
    assert receipt.reused_source_count == 0
    assert receipt.sources[0].state == "DOWNLOADED"
    assert receipt.sources[0].raw_content_sha256 == source.raw_content_sha256
    assert "path" not in json.dumps(receipt.content_free_projection(), sort_keys=True).lower()
    assert transport.requests[0].headers["Accept-Encoding"] == "identity"
    assert "Range" not in transport.requests[0].headers
    assert (cache_root / "oa-raw" / "src_oa_fixture_001.txt").read_bytes() == body


def test_fully_cached_offline_rebuild_needs_no_packet_or_transport(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    body = b"cached bounded OA source\n"
    source = _source(body)
    first_transport = _FixtureTransport([_Response(200, _headers("text/plain", body), body)])

    download_oa112_local_cache(
        entries=(source,),
        registry_digest="0" * 64,
        packet=_packet(source, registry_digest="0" * 64),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=first_transport,
    )

    offline_transport = _FixtureTransport([])
    receipt = download_oa112_local_cache(
        entries=(source,),
        registry_digest="0" * 64,
        packet=None,
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=offline_transport,
    )

    assert receipt.physical_call_count == 0
    assert receipt.attempt_count == 0
    assert receipt.downloaded_source_count == 0
    assert receipt.reused_source_count == 1
    assert receipt.sources[0].state == "REUSED"
    assert offline_transport.requests == []


def test_missing_cache_requires_packet_before_transport(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    source = _source(b"packet-required body\n")
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_REQUIRED"):
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="9" * 64,
            packet=None,
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert transport.requests == []


def test_pending_download_requires_current_execution_binding_before_packet_consumption(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    source = _source(b"execution evidence required\n")
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_EXECUTION_EVIDENCE_REQUIRED"):
        _download_oa112_local_cache(
            entries=(source,),
            registry_digest="9" * 64,
            packet=_packet(source, registry_digest="9" * 64),
            execution_binding=None,
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert transport.requests == []
    assert not (control_root / "oa112-packet-claims").exists()


def test_packet_binds_current_evidence_and_approved_source_endpoint_set_before_transport(
    tmp_path: Path,
) -> None:
    cache_root, control_root = _roots(tmp_path)
    source = _source(b"binding drift\n")
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_EXECUTION_BINDING"):
        _download_oa112_local_cache(
            entries=(source,),
            registry_digest="9" * 64,
            packet=_packet(source, registry_digest="9" * 64),
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

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_ENDPOINT_SCOPE_DRIFT"):
        _download_oa112_local_cache(
            entries=(source,),
            registry_digest="9" * 64,
            packet=_packet_with_endpoint_digest(
                source,
                registry_digest="9" * 64,
                source_endpoint_digest="e" * 64,
            ),
            execution_binding=_binding(),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )
    assert transport.requests == []


def test_consumed_packet_writes_content_free_success_and_failure_receipts(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    source = _source(b"receipt evidence\n")
    failing_transport = _FixtureTransport(
        [_Response(302, {"Location": "https://elsewhere.example/"}, b"")]
    )

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_REDIRECT_OR_STATUS") as raised:
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="9" * 64,
            packet=_packet(source, registry_digest="9" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=failing_transport,
        )

    assert raised.value.attempt_count == raised.value.physical_call_count == 1
    assert raised.value.failure_receipt_written is True
    receipt_files = tuple((control_root / "oa112-download-receipts").glob("*.json"))
    assert len(receipt_files) == 1
    receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
    assert receipt == {
        "attemptCount": 1,
        "code": "OA112_DOWNLOAD_REDIRECT_OR_STATUS",
        "downloadedSourceCount": 0,
        "packetDigest": receipt["packetDigest"],
        "physicalCallCount": 1,
        "registryDigest": "9" * 64,
        "reusedSourceCount": 0,
        "state": "FAILED",
    }
    receipt_text = json.dumps(receipt, sort_keys=True)
    assert source.canonical_url not in receipt_text
    assert source.raw_content_sha256 not in receipt_text


def test_execution_evidence_loader_requires_current_clean_git_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "local"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    evidence = {
        "ciDigest": "c" * 64,
        "headSha": "a" * 40,
        "securityDigest": "d" * 64,
        "treeSha256": "b" * 64,
    }
    path = root / "oa112-execution-evidence.v1.json"
    path.write_text(json.dumps(evidence, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setattr(
        oa112_downloader,
        "_current_clean_git_identity",
        lambda _repository_root: ("a" * 40, "b" * 64),
    )

    binding = load_oa112_execution_binding(
        approved_root=root,
        relative_path="oa112-execution-evidence.v1.json",
        repository_root=tmp_path,
    )

    assert binding == _binding()
    monkeypatch.setattr(
        oa112_downloader,
        "_current_clean_git_identity",
        lambda _repository_root: ("e" * 40, "b" * 64),
    )
    with pytest.raises(Oa112DownloadError, match="OA112_EXECUTION_EVIDENCE_GIT_DRIFT"):
        load_oa112_execution_binding(
            approved_root=root,
            relative_path="oa112-execution-evidence.v1.json",
            repository_root=tmp_path,
        )


def test_unsafe_cache_layout_stops_before_packet_consumption_or_transport(tmp_path: Path) -> None:
    cache_root, control_root = _roots(tmp_path)
    unsafe_raw_directory = cache_root / "oa-raw"
    unsafe_raw_directory.mkdir(mode=0o755)
    os.chmod(unsafe_raw_directory, 0o755)
    source = _source(b"unsafe layout body\n")
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_CACHE_UNSAFE"):
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="8" * 64,
            packet=_packet(source, registry_digest="8" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert transport.requests == []
    assert not (control_root / "oa112-packet-claims").exists()


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


def test_packet_expiry_during_first_dribbling_source_stops_later_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """첫 source가 approval expiry를 넘기면 같은 packet의 다음 outbound를 만들지 않는다."""

    cache_root, control_root = _roots(tmp_path)
    start = datetime(2026, 8, 3, tzinfo=UTC)
    clock = _MutableUtcClock(start)
    monkeypatch.setattr(oa112_downloader, "_utc_now", clock.now)
    first_body = b"first source dribble\n"
    second_body = b"second source must not connect\n"
    first_source = _source(first_body, source_id="src_oa_fixture_101")
    second_source = _source(second_body, source_id="src_oa_fixture_102")
    packet = _packet_for_sources(
        (first_source, second_source),
        registry_digest="a" * 64,
        expires_at=start + timedelta(seconds=1),
    )
    transport = _FixtureTransport(
        [
            _ExpiringResponse(
                200,
                _headers("text/plain", first_body),
                first_body,
                clock=clock,
                expiry=start + timedelta(seconds=1),
            ),
            _Response(200, _headers("text/plain", second_body), second_body),
        ]
    )

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_EXPIRED") as raised:
        download_oa112_local_cache(
            entries=(first_source, second_source),
            registry_digest="a" * 64,
            packet=packet,
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
            now=start,
        )

    assert raised.value.attempt_count == 1
    assert raised.value.physical_call_count == 1
    assert len(transport.requests) == 1
    assert not (cache_root / "oa-raw" / "src_oa_fixture_101.txt").exists()
    assert not (cache_root / "oa-raw" / "src_oa_fixture_102.txt").exists()


def test_expired_pending_source_is_not_counted_as_a_physical_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """packet이 validate 직후 만료돼도 다음 source의 physical count나 connection을 만들지 않는다."""

    cache_root, control_root = _roots(tmp_path)
    cached_body = b"already cached source\n"
    pending_body = b"expired pending source\n"
    cached_source = _source(cached_body, source_id="src_oa_fixture_201")
    pending_source = _source(pending_body, source_id="src_oa_fixture_202")
    download_oa112_local_cache(
        entries=(cached_source,),
        registry_digest="b" * 64,
        packet=_packet(cached_source, registry_digest="b" * 64),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=_FixtureTransport(
            [_Response(200, _headers("text/plain", cached_body), cached_body)]
        ),
    )
    start = datetime(2026, 8, 3, tzinfo=UTC)
    clock = _SequenceUtcClock(
        values=[start, start + timedelta(seconds=1)]
    )
    monkeypatch.setattr(oa112_downloader, "_utc_now", clock.now)
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_PACKET_EXPIRED") as raised:
        download_oa112_local_cache(
            entries=(cached_source, pending_source),
            registry_digest="c" * 64,
            packet=_packet_for_sources(
                (cached_source, pending_source),
                registry_digest="c" * 64,
                expires_at=start + timedelta(seconds=1),
            ),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert raised.value.attempt_count == 0
    assert raised.value.physical_call_count == 0
    assert transport.requests == []


def test_dns_worker_deadline_kills_hung_resolver_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS worker가 멈춰도 source deadline 뒤 connection 단계로 진행하지 않는다."""

    cache_root, control_root = _roots(tmp_path)
    source = _source(b"DNS deadline body\n")
    monkeypatch.setattr(oa112_downloader, "_MAX_SOURCE_ELAPSED_SECONDS", 0.02)
    resolver = oa112_downloader._SocketOa112DnsResolver(
        worker_command=(sys.executable, "-c", "import time; time.sleep(60)")
    )
    transport = _FixtureTransport([])

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_TIME_BOUND") as raised:
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="b" * 64,
            packet=_packet(source, registry_digest="b" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=resolver,
            transport=transport,
        )

    assert raised.value.attempt_count == 1
    assert raised.value.physical_call_count == 0
    assert transport.requests == []


def test_watchdog_socket_close_removes_zero_byte_state_and_allows_new_packet_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """watchdog가 header 전 socket을 닫아도 empty staging state가 다음 packet을 막지 않는다."""

    cache_root, control_root = _roots(tmp_path)
    body = b"retry after watchdog body\n"
    source = _source(body)
    monkeypatch.setattr(oa112_downloader, "_MAX_SOURCE_ELAPSED_SECONDS", 0.05)
    watchdog_transport = _WatchdogSocketTransport()

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_TIME_BOUND") as raised:
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="c" * 64,
            packet=_packet(source, registry_digest="c" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=watchdog_transport,
        )

    assert raised.value.attempt_count == 1
    assert raised.value.physical_call_count == 1
    assert watchdog_transport.connection_count == 1
    assert not (cache_root / "download-staging" / "src_oa_fixture_001.part").exists()
    assert not (cache_root / "download-staging" / "src_oa_fixture_001.resume.json").exists()

    retry_transport = _FixtureTransport(
        [_Response(200, _headers("text/plain", body), body)]
    )
    receipt = download_oa112_local_cache(
        entries=(source,),
        registry_digest="c" * 64,
        packet=_packet(
            source,
            registry_digest="c" * 64,
            nonce="nonce-watchdog-retry-0002",
        ),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=retry_transport,
    )

    assert receipt.sources[0].state == "DOWNLOADED"
    assert (cache_root / "oa-raw" / "src_oa_fixture_001.txt").read_bytes() == body


def test_header_deadline_stops_before_body_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """header dribble는 socket idle timeout을 갱신해도 source deadline을 넘길 수 없다."""

    cache_root, control_root = _roots(tmp_path)
    body = b"header deadline body\n"
    source = _source(body)
    clock = _MutableMonotonicClock(current=100.0)
    monkeypatch.setattr(oa112_downloader, "_MAX_SOURCE_ELAPSED_SECONDS", 1.0)
    monkeypatch.setattr(oa112_downloader, "_monotonic", clock.now)
    transport = _DeadlineHeaderTransport(
        [_Response(200, _headers("text/plain", body), body)],
        clock=clock,
        advance_seconds=2.0,
    )

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_TIME_BOUND") as raised:
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="c" * 64,
            packet=_packet(source, registry_digest="c" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert raised.value.attempt_count == 1
    assert raised.value.physical_call_count == 1
    assert len(transport.requests) == 1
    assert not (cache_root / "oa-raw" / "src_oa_fixture_001.txt").exists()


def test_body_dribble_deadline_keeps_only_resumable_partial_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """body가 idle timeout보다 자주 오더라도 absolute deadline 후 raw promotion을 막는다."""

    cache_root, control_root = _roots(tmp_path)
    body = b"first bounded chunk second bounded chunk\n"
    source = _source(body)
    clock = _MutableMonotonicClock(current=100.0)
    monkeypatch.setattr(oa112_downloader, "_MAX_SOURCE_ELAPSED_SECONDS", 1.0)
    monkeypatch.setattr(oa112_downloader, "_monotonic", clock.now)
    transport = _FixtureTransport(
        [
            _DeadlineDribblingResponse(
                200,
                _headers("text/plain", body),
                body,
                clock=clock,
                advance_seconds=2.0,
            )
        ]
    )

    with pytest.raises(Oa112DownloadError, match="OA112_DOWNLOAD_TIME_BOUND") as raised:
        download_oa112_local_cache(
            entries=(source,),
            registry_digest="d" * 64,
            packet=_packet(source, registry_digest="d" * 64),
            local_cache_root=cache_root,
            packet_control_root=control_root,
            resolver=_FixtureResolver(),
            transport=transport,
        )

    assert raised.value.attempt_count == 1
    assert raised.value.physical_call_count == 1
    assert len(transport.requests) == 1
    assert (cache_root / "download-staging" / "src_oa_fixture_001.part").exists()
    assert not (cache_root / "oa-raw" / "src_oa_fixture_001.txt").exists()

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
        registry_digest="d" * 64,
        packet=_packet(
            source,
            registry_digest="d" * 64,
            nonce="nonce-time-bound-resume-0002",
        ),
        local_cache_root=cache_root,
        packet_control_root=control_root,
        resolver=_FixtureResolver(),
        transport=resumed_transport,
    )

    assert receipt.sources[0].state == "RESUMED"
    assert (cache_root / "oa-raw" / "src_oa_fixture_001.txt").read_bytes() == body


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    cache_root = tmp_path / "cache"
    control_root = tmp_path / "control"
    cache_root.mkdir(mode=0o700)
    control_root.mkdir(mode=0o700)
    os.chmod(cache_root, 0o700)
    os.chmod(control_root, 0o700)
    return cache_root, control_root


def _source(
    body: bytes,
    *,
    mime_type: str = "text/plain",
    source_id: str = "src_oa_fixture_001",
) -> Oa112RegistryEntry:
    canonical_url = f"https://official.example.com/oa/{source_id}.txt"
    return Oa112RegistryEntry(
        source_id=source_id,
        source_revision_id=f"srv_{source_id[4:]}",
        document_id=f"doc_{source_id[4:]}",
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
        source_endpoint_digest=oa112_source_endpoint_digest((source,)),
        source_ids=(source.source_id,),
        provider="OA112_OFFICIAL_HTTPS",
        operation="OA112_RAW_LOCAL_CACHE_DOWNLOAD",
        query="NONE",
        symbol="NONE",
        date="NONE",
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


def _packet_for_sources(
    sources: tuple[Oa112RegistryEntry, ...],
    *,
    registry_digest: str,
    expires_at: datetime,
) -> Oa112DownloadPacket:
    return Oa112DownloadPacket(
        approval_id="oa112-download-fixture-101",
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        ci_digest="c" * 64,
        security_digest="d" * 64,
        registry_digest=registry_digest,
        source_endpoint_digest=oa112_source_endpoint_digest(sources),
        source_ids=tuple(source.source_id for source in sources),
        provider="OA112_OFFICIAL_HTTPS",
        operation="OA112_RAW_LOCAL_CACHE_DOWNLOAD",
        query="NONE",
        symbol="NONE",
        date="NONE",
        logical_call_cap=len(sources),
        physical_call_cap=len(sources),
        maximum_source_bytes=1024 * 1024,
        maximum_total_bytes=1024 * 1024 * len(sources),
        cost_cap_microusd=0,
        retry_count=0,
        tracked_raw_artifact_count=0,
        operator="local-operator",
        expires_at=expires_at,
        nonce="nonce-download-fixture-0101",
        maximum_pages=500,
    )


def _packet_with_endpoint_digest(
    source: Oa112RegistryEntry,
    *,
    registry_digest: str,
    source_endpoint_digest: str,
) -> Oa112DownloadPacket:
    return replace(
        _packet(source, registry_digest=registry_digest),
        source_endpoint_digest=source_endpoint_digest,
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

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0


@dataclass
class _MutableUtcClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass
class _SequenceUtcClock:
    values: list[datetime]

    def now(self) -> datetime:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


@dataclass
class _MutableMonotonicClock:
    current: float

    def now(self) -> float:
        return self.current


@dataclass(frozen=True)
class _ExpiringResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    clock: _MutableUtcClock
    expiry: datetime

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        midpoint = len(self.body) // 2
        yield self.body[:midpoint]
        self.clock.current = self.expiry
        yield self.body[midpoint:]

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0


@dataclass(frozen=True)
class _DeadlineDribblingResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    clock: _MutableMonotonicClock
    advance_seconds: float

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        midpoint = len(self.body) // 2
        yield self.body[:midpoint]
        self.clock.current += self.advance_seconds
        yield self.body[midpoint:]

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
        read_timeout_seconds: float | None = None,
    ) -> Oa112DownloadResponse:
        if read_timeout_seconds is not None:
            assert read_timeout_seconds > 0
        self._transport.requests[-1] = _Request(
            hostname=self._transport.requests[-1].hostname,
            pinned_ip=self._transport.requests[-1].pinned_ip,
            target=target,
            headers=dict(headers),
        )
        if not self._transport.responses:
            raise AssertionError("unexpected transport call")
        return self._transport.responses.pop(0)


class _DeadlineHeaderConnection(_FixtureConnection):
    def __init__(
        self,
        transport: "_DeadlineHeaderTransport",
        *,
        clock: _MutableMonotonicClock,
        advance_seconds: float,
    ) -> None:
        super().__init__(transport)
        self._clock = clock
        self._advance_seconds = advance_seconds

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
        read_timeout_seconds: float | None = None,
    ) -> Oa112DownloadResponse:
        if read_timeout_seconds is not None:
            assert read_timeout_seconds > 0
        self._clock.current += self._advance_seconds
        return super().get(
            target=target,
            headers=headers,
            read_timeout_seconds=read_timeout_seconds,
        )


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
        deadline: object | None = None,
    ) -> _FixtureConnection:
        assert connect_timeout_seconds > 0
        assert read_timeout_seconds > 0
        self.requests.append(_Request(hostname, pinned_ip, "", {}))
        return _FixtureConnection(self)


class _DeadlineHeaderTransport(_FixtureTransport):
    def __init__(
        self,
        responses: Sequence[Oa112DownloadResponse],
        *,
        clock: _MutableMonotonicClock,
        advance_seconds: float,
    ) -> None:
        super().__init__(responses)
        self._clock = clock
        self._advance_seconds = advance_seconds

    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline: object | None = None,
    ) -> _DeadlineHeaderConnection:
        assert connect_timeout_seconds > 0
        assert read_timeout_seconds > 0
        self.requests.append(_Request(hostname, pinned_ip, "", {}))
        return _DeadlineHeaderConnection(
            self,
            clock=self._clock,
            advance_seconds=self._advance_seconds,
        )


class _WatchdogSocketConnection:
    """deadline callback이 실제 blocking socket을 닫는 downloader 회귀용 transport connection이다."""

    peer_ip = "8.8.8.8"

    def __init__(self, *, deadline: oa112_downloader._Oa112SourceDeadline) -> None:
        self._client, self._peer = socket.socketpair()
        self._client.settimeout(0.5)
        self._unregister = deadline.register_canceller(
            lambda: oa112_downloader._abort_socket(self._client)
        )

    def __enter__(self) -> "_WatchdogSocketConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        self._unregister()
        oa112_downloader._abort_socket(self._client)
        oa112_downloader._abort_socket(self._peer)

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
        read_timeout_seconds: float,
    ) -> Oa112DownloadResponse:
        assert target.startswith("/")
        assert headers["Accept-Encoding"] == "identity"
        assert read_timeout_seconds > 0
        try:
            payload = self._client.recv(1)
        except OSError:
            raise
        if payload == b"":
            raise OSError("watchdog closed header socket")
        raise AssertionError("watchdog test peer must not send a header byte")


class _WatchdogSocketTransport:
    def __init__(self) -> None:
        self.connection_count = 0

    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline: oa112_downloader._Oa112SourceDeadline,
    ) -> _WatchdogSocketConnection:
        assert hostname == "official.example.com"
        assert pinned_ip == "8.8.8.8"
        assert connect_timeout_seconds > 0
        assert read_timeout_seconds > 0
        self.connection_count += 1
        return _WatchdogSocketConnection(deadline=deadline)
