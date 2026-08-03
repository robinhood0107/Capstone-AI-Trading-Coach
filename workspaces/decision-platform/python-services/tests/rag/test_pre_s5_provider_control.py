from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5ProviderBinding,
    load_pre_s5_voyage_activation,
    resolve_voyage_api_key,
)


def test_voyage_activation_packet_is_local_only_bound_and_content_free(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    _write_packet(tmp_path, _packet(now=now))

    activation = load_pre_s5_voyage_activation(
        local_root=tmp_path,
        binding=_binding(),
        now=now,
    )

    assert activation.provider == "VOYAGE"
    assert activation.operation == "CONTEXTUALIZED_DOCUMENT_EMBEDDING"
    assert activation.origin == "https://api.voyageai.com"
    assert activation.endpoint == "/v1/contextualizedembeddings"
    assert activation.logical_call_cap == activation.physical_call_cap == 1
    assert activation.retry_count == 0
    assert activation.raw_artifact_count == 0
    summary = json.dumps(activation.content_free_summary(), ensure_ascii=False, sort_keys=True)
    assert "operator" not in summary
    assert "nonce" not in summary
    assert "evidence" not in summary
    assert "query" not in summary


def test_voyage_activation_packet_rejects_shared_mode_expiry_and_binding_drift(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    _write_packet(tmp_path, _packet(now=now))
    packet_path = tmp_path / "control" / "pre-s5-voyage-activation.json"

    os.chmod(packet_path, 0o640)
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BOUNDARY"):
        load_pre_s5_voyage_activation(local_root=tmp_path, binding=_binding(), now=now)

    os.chmod(packet_path, 0o600)
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_EXPIRED"):
        load_pre_s5_voyage_activation(
            local_root=tmp_path,
            binding=_binding(),
            now=now + timedelta(minutes=6),
        )
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BINDING"):
        load_pre_s5_voyage_activation(
            local_root=tmp_path,
            binding=PreS5ProviderBinding(
                head_commit="f" * 40,
                tree_object="b" * 40,
                ci_digest="c" * 64,
                security_digest="d" * 64,
            ),
            now=now,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("retryCount", 1),
        ("rawArtifactCount", 1),
        ("origin", "https://attacker.invalid"),
        ("endpoint", "/v1/files"),
        ("physicalCallCap", 2),
        ("operation", "QUERY_FALLBACK"),
    ),
)
def test_voyage_activation_packet_rejects_scope_expansion(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    packet = _packet(now=now)
    packet[field] = value
    _write_packet(tmp_path, packet)

    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_activation(local_root=tmp_path, binding=_binding(), now=now)


def test_voyage_key_reader_uses_only_standard_environment_variable() -> None:
    assert resolve_voyage_api_key({"VOYAGE_API_KEY": "test-key", "VOYAGE_TOKEN": "legacy-key"}) == "test-key"
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_VOYAGE_API_KEY_REQUIRED"):
        resolve_voyage_api_key({"VOYAGE_TOKEN": "legacy-key"})


def _packet(*, now: datetime) -> dict[str, object]:
    return {
        "bundleManifestSha256": "e" * 64,
        "ciDigest": "c" * 64,
        "costCapMicrousd": 100_000,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (now + timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "headCommit": "a" * 40,
        "issuedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "operation": "CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "FULL_BUNDLE_ORDERED_PRECHUNKED_DOCUMENTS",
        "rawArtifactCount": 0,
        "retryCount": 0,
        "schemaVersion": "pre-s5-provider-activation/v1",
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenCap": 120_000,
        "treeObject": "b" * 40,
        "nonce": "ps5_voyage_activation_0001",
    }


def _binding() -> PreS5ProviderBinding:
    return PreS5ProviderBinding(
        head_commit="a" * 40,
        tree_object="b" * 40,
        ci_digest="c" * 64,
        security_digest="d" * 64,
    )


def _secure_root(root: Path) -> None:
    os.chmod(root, 0o700)
    (root / "control").mkdir(mode=0o700)


def _write_packet(root: Path, packet: dict[str, object]) -> None:
    path = root / "control" / "pre-s5-voyage-activation.json"
    path.write_text(json.dumps(packet, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)
