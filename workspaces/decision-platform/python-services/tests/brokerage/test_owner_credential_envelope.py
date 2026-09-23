from __future__ import annotations

import secrets
import struct
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.brokerage.owner_credential_envelope import (
    OwnerCredentialEnvelopeOpener,
    OwnerCredentialUnavailable,
)
from app.generated.brokerage_pb2 import BoundMockCredentialEnvelope


def _sealed(
    directory: Path, owner: str, account_id: str
) -> tuple[BoundMockCredentialEnvelope, tuple[str, str, str]]:
    key = ("K" + secrets.token_urlsafe(24)).replace("=", "")
    secret = "S" + secrets.token_urlsafe(48)
    account_no = "".join(str(secrets.randbelow(10)) for _ in range(10))
    parts = tuple(value.encode("ascii") for value in (key, secret, account_no))
    payload = b"".join(struct.pack(">i", len(part)) + part for part in parts)
    dek = secrets.token_bytes(32)
    kek = (directory / "brokerage-kek-v1.key").read_bytes()

    def aad(field: str) -> bytes:
        return f"mars-brokerage-v1|{owner}|KIS_MOCK|{account_id}|kek-v1|{field}".encode("ascii")

    wrap_nonce = secrets.token_bytes(12)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, aad("wrap"))
    payload_nonce = secrets.token_bytes(12)
    encrypted = AESGCM(dek).encrypt(payload_nonce, payload, aad("payload"))
    return (
        BoundMockCredentialEnvelope(
            owner_user_id=owner,
            account_id=account_id,
            revision=1,
            credential_state="CERTIFIED",
            kek_version="kek-v1",
            wrap_nonce=wrap_nonce,
            wrapped_dek=wrapped[:-16],
            wrap_tag=wrapped[-16:],
            payload_nonce=payload_nonce,
            payload_ciphertext=encrypted[:-16],
            payload_tag=encrypted[-16:],
        ),
        (key, secret, account_no),
    )


def test_owner_envelope_opens_only_matching_account_and_state(tmp_path: Path) -> None:
    directory = tmp_path / "brokerage"
    directory.mkdir(mode=0o700)
    (directory / "brokerage-kek-v1.key").write_bytes(secrets.token_bytes(32))
    (directory / "brokerage-kek-v1.key").chmod(0o600)
    opener = OwnerCredentialEnvelopeOpener(str(directory))
    owner_a = "usr_" + secrets.token_hex(16)
    owner_b = "usr_" + secrets.token_hex(16)
    account_a = "acct_" + secrets.token_hex(16)
    account_b = "acct_" + secrets.token_hex(16)
    envelope_a, values_a = _sealed(directory, owner_a, account_a)
    envelope_b, values_b = _sealed(directory, owner_b, account_b)

    opened_a = opener.open(
        envelope_a, account_id=account_a, allowed_states=frozenset({"CERTIFIED"})
    )
    opened_b = opener.open(
        envelope_b, account_id=account_b, allowed_states=frozenset({"CERTIFIED"})
    )
    assert opened_a.app_key.get_secret_value() == values_a[0]
    assert opened_b.app_secret.get_secret_value() == values_b[1]
    assert opened_a.account_number.get_secret_value() == values_a[2][:8] + "-" + values_a[2][8:]
    with pytest.raises(OwnerCredentialUnavailable):
        opener.open(envelope_a, account_id=account_b, allowed_states=frozenset({"CERTIFIED"}))
    with pytest.raises(OwnerCredentialUnavailable):
        opener.open(envelope_a, account_id=account_a, allowed_states=frozenset({"CONNECTED"}))
    tampered = BoundMockCredentialEnvelope()
    tampered.CopyFrom(envelope_a)
    tampered.owner_user_id = owner_b
    with pytest.raises(OwnerCredentialUnavailable):
        opener.open(tampered, account_id=account_a, allowed_states=frozenset({"CERTIFIED"}))


def test_owner_envelope_rejects_weak_key_file(tmp_path: Path) -> None:
    directory = tmp_path / "brokerage"
    directory.mkdir(mode=0o700)
    (directory / "brokerage-kek-v1.key").write_bytes(secrets.token_bytes(32))
    (directory / "brokerage-kek-v1.key").chmod(0o644)
    with pytest.raises(OwnerCredentialUnavailable):
        OwnerCredentialEnvelopeOpener(str(directory))


def test_full_factory_builds_separate_clients_without_deployment_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.brokerage import brokerage_grpc_server as server

    directory = tmp_path / "brokerage"
    directory.mkdir(mode=0o700)
    (directory / "brokerage-kek-v1.key").write_bytes(secrets.token_bytes(32))
    (directory / "brokerage-kek-v1.key").chmod(0o600)
    opener = OwnerCredentialEnvelopeOpener(str(directory))
    pairs: list[tuple[str, str]] = []
    closed: list[bool] = []

    class FakeClient:
        def __init__(self, *, account_number, credential_provider, **_kwargs) -> None:
            pairs.append(
                (
                    account_number.get_secret_value(),
                    credential_provider().app_key.get_secret_value(),
                )
            )

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(server, "KISMockBrokerageHttpClient", FakeClient)
    settings = server.BrokerageGrpcServerSettings(
        bind_address="127.0.0.1:50052",
        shared_secret="s" * 32,
        bound_account_id="",
        online_enabled=True,
        token_p_physical_cap=1,
        brokerage_physical_cap=1,
        reference_key=server.SecretStr("synthetic-reference-key"),
        reference_ttl_seconds=900,
        product_mode="FULL",
    )
    factory = server.OwnerBoundGatewayFactory(settings, object(), opener)
    expected: list[tuple[str, str]] = []
    for _ in range(2):
        envelope, (key, _, number) = _sealed(
            directory, "usr_" + secrets.token_hex(16), "acct_" + secrets.token_hex(16)
        )
        with factory.open(
            envelope,
            account_id=envelope.account_id,
            allowed_states=frozenset({"CERTIFIED"}),
        ):
            pass
        expected.append((number[:8] + "-" + number[8:], key))
    assert pairs == expected
    assert closed == [True, True]
