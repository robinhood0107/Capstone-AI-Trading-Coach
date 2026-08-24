from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.async_worker.kafka_security import (
    CONTRACT,
    KafkaEnvelopeSecurityError,
    KafkaEnvelopeSigner,
    KafkaEnvelopeVerifier,
    deterministic_partition,
)

TOPIC = "artifact.ingest-requested.v1"
KEY = "hmac-sha256:" + "c" * 64
PAYLOAD_HASH = "sha256:" + "a" * 64


def _encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _keys() -> tuple[KafkaEnvelopeSigner, KafkaEnvelopeVerifier]:
    private = Ed25519PrivateKey.generate()
    signer = KafkaEnvelopeSigner.from_base64url(
        _encoded(private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()))
    )
    verifier = KafkaEnvelopeVerifier.from_base64url(
        _encoded(private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo))
    )
    return signer, verifier


def _envelope(signer: KafkaEnvelopeSigner) -> tuple[dict[str, object], int]:
    partition = deterministic_partition(TOPIC, KEY)
    signature = signer.sign(
        topic=TOPIC,
        key=KEY,
        event_type=TOPIC,
        payload_hash=PAYLOAD_HASH,
        partition=partition,
    )
    return (
        {
            "eventType": TOPIC,
            "payloadHash": PAYLOAD_HASH,
            "transport": {
                "contract": CONTRACT,
                "topic": TOPIC,
                "key": KEY,
                "partition": partition,
                "signature": signature,
            },
        },
        partition,
    )


def test_signature_binds_topic_key_event_payload_and_partition() -> None:
    signer, verifier = _keys()
    envelope, partition = _envelope(signer)
    verifier.verify(envelope, actual_topic=TOPIC, actual_key=KEY, actual_partition=partition)

    for field, value in (
        ("topic", "rag.index-requested.v1"),
        ("key", "hmac-sha256:" + "d" * 64),
        ("partition", (partition + 1) % 3),
    ):
        tampered = {**envelope, "transport": {**envelope["transport"], field: value}}  # type: ignore[dict-item]
        with pytest.raises(KafkaEnvelopeSecurityError):
            verifier.verify(
                tampered, actual_topic=TOPIC, actual_key=KEY, actual_partition=partition
            )

    for field, value in (
        ("eventType", "rag.index-requested.v1"),
        ("payloadHash", "sha256:" + "b" * 64),
    ):
        with pytest.raises(KafkaEnvelopeSecurityError):
            verifier.verify(
                {**envelope, field: value},
                actual_topic=TOPIC,
                actual_key=KEY,
                actual_partition=partition,
            )


def test_wrong_public_key_and_non_deterministic_partition_fail_closed() -> None:
    signer, _ = _keys()
    _, wrong_verifier = _keys()
    envelope, partition = _envelope(signer)
    with pytest.raises(KafkaEnvelopeSecurityError):
        wrong_verifier.verify(
            envelope, actual_topic=TOPIC, actual_key=KEY, actual_partition=partition
        )
    with pytest.raises(KafkaEnvelopeSecurityError):
        signer.sign(
            topic=TOPIC,
            key=KEY,
            event_type=TOPIC,
            payload_hash=PAYLOAD_HASH,
            partition=(partition + 1) % 3,
        )
