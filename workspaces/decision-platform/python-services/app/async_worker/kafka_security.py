from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import re
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    load_der_private_key,
    load_der_public_key,
)


CONTRACT = "p1-kafka-envelope.v1"
PARTITION_COUNT = 3
_TOPIC = re.compile(r"^[a-z][a-z0-9.-]{2,127}$")
_KEY = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9.-]{2,127}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")


class KafkaEnvelopeSecurityError(ValueError):
    """The Kafka transport identity or signature is invalid."""


def deterministic_partition(topic: str, key: str) -> int:
    _validate_identity(topic, key)
    digest = hashlib.sha256(
        b"p1-kafka-partition-v1\0" + topic.encode("ascii") + b"\0" + key.encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big") % PARTITION_COUNT


def canonical_signature_input(
    *,
    topic: str,
    key: str,
    event_type: str,
    payload_hash: str,
    partition: int,
) -> bytes:
    _validate_identity(topic, key)
    if (
        _EVENT_TYPE.fullmatch(event_type) is None
        or _HASH.fullmatch(payload_hash) is None
        or partition not in range(PARTITION_COUNT)
        or partition != deterministic_partition(topic, key)
    ):
        raise KafkaEnvelopeSecurityError
    return "\n".join((CONTRACT, topic, key, event_type, payload_hash, str(partition))).encode(
        "ascii"
    )


@dataclass(frozen=True, slots=True)
class KafkaEnvelopeSigner:
    _signer: Ed25519PrivateKey

    @classmethod
    def from_base64url(cls, encoded: str) -> "KafkaEnvelopeSigner":
        key = load_der_private_key(_decode_key(encoded), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise KafkaEnvelopeSecurityError
        return cls(key)

    def sign(
        self,
        *,
        topic: str,
        key: str,
        event_type: str,
        payload_hash: str,
        partition: int,
    ) -> str:
        signature = self._signer.sign(
            canonical_signature_input(
                topic=topic,
                key=key,
                event_type=event_type,
                payload_hash=payload_hash,
                partition=partition,
            )
        )
        return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class KafkaEnvelopeVerifier:
    public_key: Ed25519PublicKey

    @classmethod
    def from_base64url(cls, encoded: str) -> "KafkaEnvelopeVerifier":
        key = load_der_public_key(_decode_key(encoded))
        if not isinstance(key, Ed25519PublicKey):
            raise KafkaEnvelopeSecurityError
        return cls(key)

    def verify(
        self, envelope: dict[str, Any], *, actual_topic: str, actual_key: str, actual_partition: int
    ) -> None:
        transport = envelope.get("transport")
        if not isinstance(transport, dict) or set(transport) != {
            "contract",
            "topic",
            "key",
            "partition",
            "signature",
        }:
            raise KafkaEnvelopeSecurityError
        signature = transport.get("signature")
        event_type = envelope.get("eventType")
        payload_hash = envelope.get("payloadHash")
        if (
            transport.get("contract") != CONTRACT
            or transport.get("topic") != actual_topic
            or transport.get("key") != actual_key
            or transport.get("partition") != actual_partition
            or not isinstance(signature, str)
            or _SIGNATURE.fullmatch(signature) is None
            or not isinstance(event_type, str)
            or not isinstance(payload_hash, str)
        ):
            raise KafkaEnvelopeSecurityError
        try:
            self.public_key.verify(
                _decode_signature(signature),
                canonical_signature_input(
                    topic=actual_topic,
                    key=actual_key,
                    event_type=event_type,
                    payload_hash=payload_hash,
                    partition=actual_partition,
                ),
            )
        except Exception as error:
            raise KafkaEnvelopeSecurityError from error


def _validate_identity(topic: str, key: str) -> None:
    if _TOPIC.fullmatch(topic) is None or _KEY.fullmatch(key) is None:
        raise KafkaEnvelopeSecurityError


def _decode_key(encoded: str) -> bytes:
    if not encoded or len(encoded) > 256 or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        raise KafkaEnvelopeSecurityError
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError as error:
        raise KafkaEnvelopeSecurityError from error


def _decode_signature(encoded: str) -> bytes:
    raw = _decode_key(encoded)
    if len(raw) != 64:
        raise KafkaEnvelopeSecurityError
    return raw
