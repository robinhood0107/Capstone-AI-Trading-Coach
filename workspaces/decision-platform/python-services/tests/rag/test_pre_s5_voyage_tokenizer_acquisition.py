from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag.pre_s5_provider_control import PreS5ProviderBinding
from app.rag.pre_s5_voyage_tokenizer_acquisition import (
    PreS5VoyageTokenizerAcquisitionError,
    acquire_pre_s5_voyage_tokenizer,
)


_HEAD = "a" * 40
_TREE = "b" * 40
_CI = "c" * 64
_SECURITY = "d" * 64


def test_acquisition_requires_exact_packet_then_publishes_hash_pinned_artifact(
    posix_tmp_path: Path,
) -> None:
    raw = _tokenizer_bytes()
    packet_sha256 = _write_packet(posix_tmp_path)
    fetcher = _FixtureFetcher(raw)

    receipt = acquire_pre_s5_voyage_tokenizer(
        local_root=posix_tmp_path,
        binding=_binding(),
        fetcher=fetcher,
        now=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
    )

    artifact = posix_tmp_path / "artifacts" / "voyage-context-4" / "tokenizer.json"
    assert artifact.read_bytes() == raw
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert receipt.packet_sha256 == packet_sha256
    assert receipt.tokenizer_sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.physical_call_count == 1
    assert fetcher.calls == [
        (
            "https://huggingface.co/voyageai/voyage-context-4/raw/"
            "8ca946072a18e398cd61f2ad0243b56d0350b1db/tokenizer.json",
            8 * 1024 * 1024,
        )
    ]
    completion = json.loads(
        (posix_tmp_path / "artifacts" / "voyage-context-4" / "tokenizer.receipt.json").read_text()
    )
    assert completion["tokenizerSha256"] == receipt.tokenizer_sha256
    assert completion["rawArtifactCount"] == 0
    assert completion["trackedArtifactCount"] == 0


def test_acquisition_rejects_head_drift_before_fetch(posix_tmp_path: Path) -> None:
    _write_packet(posix_tmp_path)
    fetcher = _FixtureFetcher(_tokenizer_bytes())

    with pytest.raises(
        PreS5VoyageTokenizerAcquisitionError,
        match="PRE_S5_VOYAGE_TOKENIZER_PACKET_BINDING",
    ):
        acquire_pre_s5_voyage_tokenizer(
            local_root=posix_tmp_path,
            binding=PreS5ProviderBinding(
                head_commit="e" * 40,
                tree_object=_TREE,
                ci_digest=_CI,
                security_digest=_SECURITY,
            ),
            fetcher=fetcher,
            now=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
        )

    assert fetcher.calls == []
    assert not (posix_tmp_path / "artifacts").exists()


def test_acquisition_consumes_packet_and_leaves_no_partial_artifact_on_invalid_bytes(
    posix_tmp_path: Path,
) -> None:
    packet_sha256 = _write_packet(posix_tmp_path)

    with pytest.raises(
        PreS5VoyageTokenizerAcquisitionError,
        match="PRE_S5_VOYAGE_TOKENIZER_ARTIFACT_INVALID",
    ):
        acquire_pre_s5_voyage_tokenizer(
            local_root=posix_tmp_path,
            binding=_binding(),
            fetcher=_FixtureFetcher(b"not-json"),
            now=datetime(2026, 8, 12, 0, 1, tzinfo=UTC),
        )

    assert (
        posix_tmp_path / "packet-claims" / "voyage-tokenizer" / packet_sha256
    ).is_file()
    assert not (
        posix_tmp_path / "artifacts" / "voyage-context-4" / "tokenizer.json"
    ).exists()


def test_acquisition_refuses_reuse_or_existing_destination_before_fetch(posix_tmp_path: Path) -> None:
    _write_packet(posix_tmp_path)
    fetcher = _FixtureFetcher(_tokenizer_bytes())
    now = datetime(2026, 8, 12, 0, 1, tzinfo=UTC)
    acquire_pre_s5_voyage_tokenizer(
        local_root=posix_tmp_path,
        binding=_binding(),
        fetcher=fetcher,
        now=now,
    )

    with pytest.raises(
        PreS5VoyageTokenizerAcquisitionError,
        match="PRE_S5_VOYAGE_TOKENIZER_ALREADY_PRESENT",
    ):
        acquire_pre_s5_voyage_tokenizer(
            local_root=posix_tmp_path,
            binding=_binding(),
            fetcher=fetcher,
            now=now,
        )

    assert len(fetcher.calls) == 1


@dataclass
class _FixtureResponse:
    status_code: int
    headers: Mapping[str, str]
    payload: bytes

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]


class _FixtureFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, int]] = []

    def fetch(self, *, url: str, byte_cap: int) -> bytes:
        self.calls.append((url, byte_cap))
        if len(self.payload) > byte_cap:
            raise PreS5VoyageTokenizerAcquisitionError(
                "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
            )
        return self.payload


def _binding() -> PreS5ProviderBinding:
    return PreS5ProviderBinding(
        head_commit=_HEAD,
        tree_object=_TREE,
        ci_digest=_CI,
        security_digest=_SECURITY,
    )


def _write_packet(local_root: Path) -> str:
    os.chmod(local_root, 0o700)
    control = local_root / "control"
    control.mkdir(mode=0o700)
    issued_at = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
    payload = {
        "byteCap": 8 * 1024 * 1024,
        "ciDigest": _CI,
        "costCapMicrousd": 0,
        "date": "NONE",
        "endpoint": (
            "/voyageai/voyage-context-4/raw/"
            "8ca946072a18e398cd61f2ad0243b56d0350b1db/tokenizer.json"
        ),
        "expiresAt": (issued_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "headCommit": _HEAD,
        "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "model": "voyage-context-4",
        "nonce": "ps5_tokenizer_fixture_nonce_0001",
        "operation": "ACQUIRE_VOYAGE_CONTEXT_4_TOKENIZER",
        "operator": "local-operator",
        "origin": "https://huggingface.co",
        "physicalCallCap": 1,
        "provider": "HUGGING_FACE_VOYAGEAI",
        "query": "NONE",
        "rawArtifactCount": 0,
        "retryCount": 0,
        "revision": "8ca946072a18e398cd61f2ad0243b56d0350b1db",
        "schemaVersion": 1,
        "securityDigest": _SECURITY,
        "state": "APPROVED",
        "symbol": "NONE",
        "trackedArtifactCount": 0,
        "treeObject": _TREE,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    packet = control / "pre-s5-voyage-tokenizer-acquisition.json"
    packet.write_bytes(raw)
    os.chmod(packet, 0o600)
    return hashlib.sha256(raw).hexdigest()


def _tokenizer_bytes() -> bytes:
    return json.dumps(
        {
            "added_tokens": [],
            "decoder": None,
            "model": {
                "type": "WordLevel",
                "unk_token": "[UNK]",
                "vocab": {"[UNK]": 0, "alpha": 1},
            },
            "normalizer": None,
            "padding": None,
            "post_processor": None,
            "pre_tokenizer": {"type": "Whitespace"},
            "truncation": None,
            "version": "1.0",
        },
        separators=(",", ":"),
    ).encode()
