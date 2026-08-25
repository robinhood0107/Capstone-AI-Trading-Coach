from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.rag.bge_acquisition import (
    BgeAcquisitionError,
    acquire_bge_packet,
    resolve_download_redirect,
    verify_bge_completion_manifest,
)
from app.rag.bge_artifact import (
    BgeArtifactFile,
    BgeArtifactSpec,
    verify_bge_packet,
)


def test_bounded_acquisition_uses_pinned_urls_and_publishes_manifest_last(
    posix_tmp_path: Path,
) -> None:
    payloads = {
        "onnx/model.onnx": b"onnx",
        "onnx/model.onnx_data": b"weights",
    }
    spec = _tiny_spec(payloads)
    seen: list[_FixtureRequest] = []

    def handler(request: _FixtureRequest) -> _FixtureResponse:
        seen.append(request)
        if request.hostname == "huggingface.co":
            relative_path = request.target.split(f"/resolve/{spec.revision}/", maxsplit=1)[1]
            relative_path = relative_path.removesuffix("?download=true")
            return _FixtureResponse(
                302,
                {
                    "location": (
                        "https://cas-bridge.xethub.hf.co/xet-bridge-us/"
                        f"{relative_path}?X-Amz-Signature=test"
                    )
                },
                b"",
            )
        relative_path = request.target.split("/xet-bridge-us/", maxsplit=1)[1]
        relative_path = relative_path.split("?", maxsplit=1)[0]
        payload = payloads[relative_path]
        return _FixtureResponse(
            200,
            {
                "content-length": str(len(payload)),
                "content-type": "application/octet-stream",
            },
            payload,
        )

    packet_root = posix_tmp_path / "packet"
    manifest_path = posix_tmp_path / ".packet.approved.json"
    resolver = _FixtureResolver()
    transport = _FixtureTransport(handler)
    receipt = acquire_bge_packet(
        packet_root,
        manifest_path=manifest_path,
        spec=spec,
        resolver=resolver,
        transport=transport,
    )

    assert receipt == verify_bge_packet(packet_root, spec=spec)
    assert [request.hostname for request in seen] == [
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
    ]
    assert resolver.calls == [
        "huggingface.co",
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
        "cas-bridge.xethub.hf.co",
        "huggingface.co",
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
        "cas-bridge.xethub.hf.co",
    ]
    assert all(request.headers["Accept-Encoding"] == "identity" for request in seen)
    assert all(request.headers["Host"] == request.hostname for request in seen)
    assert all(request.pinned_ip == "8.8.8.8" for request in seen)
    assert stat.S_IMODE(packet_root.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE((packet_root / item.relative_path).stat().st_mode) == 0o600
        for item in spec.files
    )
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["complete"] is True
    assert manifest["fileManifestSha256"] == receipt.file_manifest_sha256
    assert manifest["revision"] == spec.revision
    assert (
        verify_bge_completion_manifest(
            packet_root,
            manifest_path=manifest_path,
            spec=spec,
        )
        == receipt
    )
    assert not tuple(posix_tmp_path.glob(".packet.staging-*"))


def test_bounded_acquisition_rejects_unapproved_redirect_without_partial_publish(
    posix_tmp_path: Path,
) -> None:
    payloads = {
        "onnx/model.onnx": b"onnx",
        "onnx/model.onnx_data": b"weights",
    }

    def handler(request: _FixtureRequest) -> _FixtureResponse:
        del request
        return _FixtureResponse(302, {"location": "https://example.com/payload"}, b"")

    packet_root = posix_tmp_path / "packet"
    manifest_path = posix_tmp_path / ".packet.approved.json"
    with pytest.raises(BgeAcquisitionError, match="DOWNLOAD_REDIRECT"):
        acquire_bge_packet(
            packet_root,
            manifest_path=manifest_path,
            spec=_tiny_spec(payloads),
            resolver=_FixtureResolver(),
            transport=_FixtureTransport(handler),
        )

    assert not packet_root.exists()
    assert not manifest_path.exists()
    assert not tuple(posix_tmp_path.glob(".packet.staging-*"))


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.1.1"],
        ["::1"],
        ["fe80::1"],
        ["0.0.0.0"],
        ["224.0.0.1"],
        ["192.0.2.1"],
        ["8.8.8.8", "10.0.0.1"],
    ],
)
def test_bounded_acquisition_rejects_non_global_or_mixed_dns_before_connect(
    posix_tmp_path: Path,
    addresses: list[str],
) -> None:
    payloads = {"onnx/model.onnx": b"onnx"}
    transport = _FixtureTransport(_unexpected_handler)
    packet_root = posix_tmp_path / "packet"
    manifest_path = posix_tmp_path / ".packet.approved.json"

    with pytest.raises(BgeAcquisitionError, match="DOWNLOAD_DNS"):
        acquire_bge_packet(
            packet_root,
            manifest_path=manifest_path,
            spec=_tiny_spec(payloads),
            resolver=_FixtureResolver([addresses, addresses]),
            transport=transport,
        )

    assert transport.connect_calls == 0
    assert not packet_root.exists()
    assert not manifest_path.exists()


def test_bounded_acquisition_rejects_dns_rebinding_before_connect(
    posix_tmp_path: Path,
) -> None:
    payloads = {"onnx/model.onnx": b"onnx"}
    transport = _FixtureTransport(_unexpected_handler)

    with pytest.raises(BgeAcquisitionError, match="DOWNLOAD_DNS_REBINDING"):
        acquire_bge_packet(
            posix_tmp_path / "packet",
            manifest_path=posix_tmp_path / ".packet.approved.json",
            spec=_tiny_spec(payloads),
            resolver=_FixtureResolver([["8.8.8.8"], ["1.1.1.1"]]),
            transport=transport,
        )

    assert transport.connect_calls == 0


def test_bounded_acquisition_rejects_peer_mismatch_before_get(
    posix_tmp_path: Path,
) -> None:
    payloads = {"onnx/model.onnx": b"onnx"}
    transport = _FixtureTransport(_unexpected_handler, peer_ip="1.1.1.1")

    with pytest.raises(BgeAcquisitionError, match="DOWNLOAD_PEER"):
        acquire_bge_packet(
            posix_tmp_path / "packet",
            manifest_path=posix_tmp_path / ".packet.approved.json",
            spec=_tiny_spec(payloads),
            resolver=_FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=transport,
        )

    assert transport.connect_calls == 1
    assert transport.get_calls == 0


def test_source_cache_redirect_is_exact_revision_path_and_query_bounded() -> None:
    spec = _tiny_spec(
        {
            "onnx/model.onnx": b"onnx",
            "onnx/model.onnx_data": b"weights",
        }
    )
    entry = spec.files[0]
    location = (
        f"/api/resolve-cache/models/BAAI/bge-m3/{spec.revision}/"
        "onnx%2Fmodel.onnx?download=true&etag=%22aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa%22"
    )

    resolved = resolve_download_redirect(location, entry=entry, spec=spec)

    assert resolved.scheme == "https"
    assert resolved.hostname == "huggingface.co"
    assert resolved.path.endswith("/onnx%2Fmodel.onnx")

    for drifted in (
        location.replace(spec.revision, "0" * 40),
        location.replace("onnx%2Fmodel.onnx", "pytorch_model.bin"),
        location + "&unexpected=true",
    ):
        with pytest.raises(BgeAcquisitionError, match="DOWNLOAD_REDIRECT"):
            resolve_download_redirect(drifted, entry=entry, spec=spec)


def _tiny_spec(payloads: dict[str, bytes]) -> BgeArtifactSpec:
    return BgeArtifactSpec(
        repository="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        license_id="MIT",
        artifact_type="ONNX_DATA_ONLY",
        files=tuple(
            BgeArtifactFile(
                relative_path=relative_path,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            for relative_path, payload in payloads.items()
        ),
    )


@dataclass(frozen=True)
class _FixtureRequest:
    hostname: str
    pinned_ip: str
    target: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class _FixtureResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]


class _FixtureResolver:
    def __init__(self, answers: list[list[str]] | None = None) -> None:
        self._answers = answers
        self.calls: list[str] = []

    def resolve(self, hostname: str) -> list[str]:
        index = len(self.calls)
        self.calls.append(hostname)
        if self._answers is None:
            return ["8.8.8.8"]
        return list(self._answers[min(index, len(self._answers) - 1)])


class _FixtureConnection:
    def __init__(
        self,
        *,
        owner: _FixtureTransport,
        hostname: str,
        pinned_ip: str,
        peer_ip: str,
    ) -> None:
        self._owner = owner
        self._hostname = hostname
        self._pinned_ip = pinned_ip
        self.peer_ip = peer_ip

    def __enter__(self) -> _FixtureConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
    ) -> _FixtureResponse:
        self._owner.get_calls += 1
        return self._owner.handler(
            _FixtureRequest(
                hostname=self._hostname,
                pinned_ip=self._pinned_ip,
                target=target,
                headers=headers,
            )
        )


class _FixtureTransport:
    def __init__(
        self,
        handler: Callable[[_FixtureRequest], _FixtureResponse],
        *,
        peer_ip: str = "8.8.8.8",
    ) -> None:
        self.handler = handler
        self.peer_ip = peer_ip
        self.connect_calls = 0
        self.get_calls = 0

    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
    ) -> _FixtureConnection:
        assert connect_timeout_seconds == 10.0
        assert read_timeout_seconds == 60.0
        self.connect_calls += 1
        return _FixtureConnection(
            owner=self,
            hostname=hostname,
            pinned_ip=pinned_ip,
            peer_ip=self.peer_ip,
        )


def _unexpected_handler(request: _FixtureRequest) -> _FixtureResponse:
    raise AssertionError(f"unexpected GET for {request.hostname}")
