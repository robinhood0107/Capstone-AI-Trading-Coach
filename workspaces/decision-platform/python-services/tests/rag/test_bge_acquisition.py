from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import httpx
import pytest

from app.rag.bge_acquisition import BgeAcquisitionError, acquire_bge_packet
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
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "huggingface.co":
            relative_path = request.url.path.split(f"/resolve/{spec.revision}/", maxsplit=1)[1]
            return httpx.Response(
                302,
                headers={
                    "location": (
                        "https://cas-bridge.xethub.hf.co/xet-bridge-us/"
                        f"{relative_path}?X-Amz-Signature=test"
                    )
                },
            )
        relative_path = request.url.path.split("/xet-bridge-us/", maxsplit=1)[1]
        payload = payloads[relative_path]
        return httpx.Response(
            200,
            headers={
                "content-length": str(len(payload)),
                "content-type": "application/octet-stream",
            },
            content=payload,
        )

    packet_root = posix_tmp_path / "packet"
    manifest_path = posix_tmp_path / ".packet.approved.json"
    receipt = acquire_bge_packet(
        packet_root,
        manifest_path=manifest_path,
        spec=spec,
        transport=httpx.MockTransport(handler),
    )

    assert receipt == verify_bge_packet(packet_root, spec=spec)
    assert [request.url.host for request in seen] == [
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
        "huggingface.co",
        "cas-bridge.xethub.hf.co",
    ]
    assert all(request.headers["accept-encoding"] == "identity" for request in seen)
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
    assert not tuple(posix_tmp_path.glob(".packet.staging-*"))


def test_bounded_acquisition_rejects_unapproved_redirect_without_partial_publish(
    posix_tmp_path: Path,
) -> None:
    payloads = {
        "onnx/model.onnx": b"onnx",
        "onnx/model.onnx_data": b"weights",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(302, headers={"location": "https://example.com/payload"})

    packet_root = posix_tmp_path / "packet"
    manifest_path = posix_tmp_path / ".packet.approved.json"
    with pytest.raises(BgeAcquisitionError, match="DOWNLOAD_REDIRECT"):
        acquire_bge_packet(
            packet_root,
            manifest_path=manifest_path,
            spec=_tiny_spec(payloads),
            transport=httpx.MockTransport(handler),
        )

    assert not packet_root.exists()
    assert not manifest_path.exists()
    assert not tuple(posix_tmp_path.glob(".packet.staging-*"))


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
