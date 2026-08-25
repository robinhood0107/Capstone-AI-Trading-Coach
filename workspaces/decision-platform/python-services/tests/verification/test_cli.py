from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from app.verification import cli


def test_current_clean_head_rejects_untracked_files(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        (
            subprocess.CompletedProcess(
                args=["git"], returncode=0, stdout="a" * 40 + "\n", stderr=""
            ),
            subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="?? unreviewed-file.py\n",
                stderr="",
            ),
        )
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: next(responses))

    with pytest.raises(ValueError, match="clean worktree"):
        cli._current_clean_head()


def test_approval_trust_anchor_uses_protected_policy_not_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_key = tmp_path / "issuer-public.pem"
    public_key.write_bytes(b"pinned-public-key")
    public_key.chmod(0o644)
    policy = tmp_path / "approval-trust-root.json"
    policy.write_text(
        json.dumps(
            {
                "contractId": "p1-approval-trust-root.v1",
                "issuerKeyId": "P1.TEST",
                "publicKeyPath": str(public_key),
                "publicKeySha256": hashlib.sha256(public_key.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    policy.chmod(0o644)
    monkeypatch.setenv("P1_APPROVAL_PUBLIC_KEY_PATH", str(tmp_path / "attacker.pem"))
    monkeypatch.setenv("P1_APPROVAL_ISSUER_KEY_ID", "P1.ATTACKER")
    monkeypatch.setenv("P1_APPROVAL_PUBLIC_KEY_SHA256", "0" * 64)

    assert cli._approval_trust_anchor(policy, expected_owner_uid=os.getuid()) == (
        public_key,
        "P1.TEST",
        hashlib.sha256(public_key.read_bytes()).hexdigest(),
    )


def test_approval_trust_anchor_rejects_writable_and_symlink_policy(tmp_path: Path) -> None:
    public_key = tmp_path / "issuer-public.pem"
    public_key.write_bytes(b"pinned-public-key")
    public_key.chmod(0o644)
    policy = tmp_path / "approval-trust-root.json"
    policy.write_text(
        json.dumps(
            {
                "contractId": "p1-approval-trust-root.v1",
                "issuerKeyId": "P1.TEST",
                "publicKeyPath": str(public_key),
                "publicKeySha256": hashlib.sha256(public_key.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    policy.chmod(0o666)
    with pytest.raises(ValueError, match="boundary"):
        cli._approval_trust_anchor(policy, expected_owner_uid=os.getuid())
    policy.chmod(0o644)
    link = tmp_path / "policy-link.json"
    link.symlink_to(policy)
    with pytest.raises(ValueError, match="unavailable"):
        cli._approval_trust_anchor(link, expected_owner_uid=os.getuid())
