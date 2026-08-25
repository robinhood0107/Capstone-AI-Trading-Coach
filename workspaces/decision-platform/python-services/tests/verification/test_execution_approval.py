from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.data._shared.canonical_json import canonical_json_bytes
from app.verification import cli
from app.verification.execution_approval import (
    ZERO_SCOPE_SHA256,
    ExecutionApprovalError,
    author_execution_approval,
    load_and_verify_execution_approval,
    scope_digest,
)


def _keys(tmp_path: Path) -> tuple[Path, Path, str]:
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    private.chmod(0o600)
    public.chmod(0o644)
    return private, public, hashlib.sha256(public.read_bytes()).hexdigest()


def test_common_approval_binds_exact_scope_and_verifies_public_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public, public_digest = _keys(tmp_path)
    now = datetime(2030, 1, 2, 3, tzinfo=UTC)
    owner_digest = scope_digest("owner-7")
    approval = author_execution_approval(
        approval_id="P1.TEST-EXECUTION-01",
        issuer_key_id="P1.TEST",
        private_key_path=private,
        provider_family="OPTIONAL3",
        exact_operations=("FINNHUB_QUOTE",),
        payload_sha256="a" * 64,
        repository_digest="b" * 64,
        evidence_digest="c" * 64,
        owner_scope_digest=owner_digest,
        account_scope_digest=ZERO_SCOPE_SHA256,
        credential_scope_digest=scope_digest("FINNHUB_API_KEY"),
        physical_call_cap=1,
        cost_cap_microusd=0,
        now=now,
    )
    packet_path = tmp_path / "p1-approval-packet.v2.json"
    packet_path.write_bytes(canonical_json_bytes(approval.to_dict()))
    packet_path.chmod(0o600)
    monkeypatch.setattr(cli, "_approval_trust_anchor", lambda: (public, "P1.TEST", public_digest))

    verified = load_and_verify_execution_approval(
        packet_path.absolute(),
        provider_family="OPTIONAL3",
        exact_operations=("FINNHUB_QUOTE",),
        payload_sha256="a" * 64,
        repository_digest="b" * 64,
        evidence_digest="c" * 64,
        owner_scope_digest=owner_digest,
        credential_scope_digest=scope_digest("FINNHUB_API_KEY"),
        physical_call_cap=1,
        cost_cap_microusd=0,
        now=now + timedelta(minutes=1),
    )

    assert verified == approval


def test_common_approval_rejects_scope_drift_and_signature_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public, public_digest = _keys(tmp_path)
    now = datetime(2030, 1, 2, 3, tzinfo=UTC)
    approval = author_execution_approval(
        approval_id="P1.TEST-EXECUTION-02",
        issuer_key_id="P1.TEST",
        private_key_path=private,
        provider_family="CORE6",
        exact_operations=("SEC_EDGAR_SUBMISSIONS",),
        payload_sha256="a" * 64,
        repository_digest="b" * 64,
        evidence_digest="c" * 64,
        owner_scope_digest=ZERO_SCOPE_SHA256,
        account_scope_digest=ZERO_SCOPE_SHA256,
        credential_scope_digest=scope_digest("SEC_EDGAR:SEC_EDGAR_SUBMISSIONS"),
        physical_call_cap=1,
        cost_cap_microusd=0,
        now=now,
    )
    packet_path = tmp_path / "p1-approval-packet.v2.json"
    packet_path.write_bytes(canonical_json_bytes(approval.to_dict()))
    packet_path.chmod(0o600)
    monkeypatch.setattr(cli, "_approval_trust_anchor", lambda: (public, "P1.TEST", public_digest))
    common = {
        "approval_path": packet_path.absolute(),
        "provider_family": "CORE6",
        "exact_operations": ("SEC_EDGAR_SUBMISSIONS",),
        "payload_sha256": "a" * 64,
        "repository_digest": "b" * 64,
        "evidence_digest": "c" * 64,
        "credential_scope_digest": scope_digest("SEC_EDGAR:SEC_EDGAR_SUBMISSIONS"),
        "physical_call_cap": 1,
        "cost_cap_microusd": 0,
        "now": now + timedelta(minutes=1),
    }
    with pytest.raises(ExecutionApprovalError, match="SCOPE_MISMATCH"):
        load_and_verify_execution_approval(**{**common, "payload_sha256": "d" * 64})

    tampered = approval.to_dict()
    tampered["payloadSha256"] = "d" * 64
    packet_path.write_bytes(canonical_json_bytes(tampered))
    with pytest.raises(ExecutionApprovalError, match="SIGNATURE_INVALID"):
        load_and_verify_execution_approval(**{**common, "payload_sha256": "d" * 64})
