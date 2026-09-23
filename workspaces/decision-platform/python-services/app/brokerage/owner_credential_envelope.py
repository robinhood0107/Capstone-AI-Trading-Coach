"""Open only a Spring owner/account-bound KIS_MOCK envelope inside the existing broker."""

from __future__ import annotations

import os
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.generated.brokerage_pb2 import BoundMockCredentialEnvelope

_OWNER = re.compile(r"^usr_[A-Za-z0-9_-]{4,96}$")
_ACCOUNT = re.compile(r"^acct_[0-9a-f]{32}$")
_APP_KEY = re.compile(r"^[A-Za-z0-9._-]{8,256}$")
_APP_SECRET = re.compile(r"^[!-~]{8,512}$")
_ACCOUNT_NO = re.compile(r"^[0-9]{10}$")
_KEK_VERSION = "kek-v1"


def _zero(value: bytearray) -> None:
    value[:] = b"\x00" * len(value)
    value.clear()


class OwnerCredentialUnavailable(RuntimeError):
    """The envelope, key file, or account binding failed without exposing values."""


@dataclass(frozen=True, repr=False)
class OpenedMockCredential:
    owner_user_id: str
    account_id: str
    revision: int
    state: str
    app_key: SecretStr
    app_secret: SecretStr
    account_number: SecretStr


class OwnerCredentialEnvelopeOpener:
    """Shares the exact AES-GCM AAD and 0700/0600 KEK contract with Spring."""

    def __init__(self, directory_name: str) -> None:
        self._directory = Path(directory_name)
        _zero(self._load_kek())

    def open(
        self,
        envelope: BoundMockCredentialEnvelope,
        *,
        account_id: str,
        allowed_states: frozenset[str],
    ) -> OpenedMockCredential:
        if (
            _OWNER.fullmatch(envelope.owner_user_id) is None
            or _ACCOUNT.fullmatch(account_id) is None
            or envelope.account_id != account_id
            or envelope.revision < 1
            or envelope.credential_state not in allowed_states
            or envelope.kek_version != _KEK_VERSION
            or len(envelope.wrap_nonce) != 12
            or len(envelope.wrap_tag) != 16
            or len(envelope.wrapped_dek) != 32
            or len(envelope.payload_nonce) != 12
            or len(envelope.payload_tag) != 16
            or not 1 <= len(envelope.payload_ciphertext) <= 8192
        ):
            raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
        kek = self._load_kek()
        dek = bytearray()
        plaintext = bytearray()
        try:
            dek = bytearray(
                AESGCM(bytes(kek)).decrypt(
                    envelope.wrap_nonce,
                    envelope.wrapped_dek + envelope.wrap_tag,
                    self._aad(envelope, "wrap"),
                )
            )
            if len(dek) != 32:
                raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
            plaintext = bytearray(
                AESGCM(bytes(dek)).decrypt(
                    envelope.payload_nonce,
                    envelope.payload_ciphertext + envelope.payload_tag,
                    self._aad(envelope, "payload"),
                )
            )
            app_key, app_secret, account_no = self._parse_payload(plaintext)
            return OpenedMockCredential(
                owner_user_id=envelope.owner_user_id,
                account_id=account_id,
                revision=envelope.revision,
                state=envelope.credential_state,
                app_key=SecretStr(app_key),
                app_secret=SecretStr(app_secret),
                account_number=SecretStr(f"{account_no[:8]}-{account_no[8:]}"),
            )
        except OwnerCredentialUnavailable:
            raise
        except Exception:
            raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE") from None
        finally:
            _zero(kek)
            _zero(dek)
            _zero(plaintext)

    def _load_kek(self) -> bytearray:
        directory = self._directory
        try:
            if not directory.is_absolute() or directory != Path(os.path.normpath(directory)):
                raise ValueError
            directory_stat = directory.lstat()
            if (
                not stat.S_ISDIR(directory_stat.st_mode)
                or stat.S_IMODE(directory_stat.st_mode) != 0o700
                or directory_stat.st_uid != os.geteuid()
            ):
                raise ValueError
            path = directory / "brokerage-kek-v1.key"
            before = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != directory_stat.st_uid
                or before.st_nlink != 1
                or before.st_size != 32
            ):
                raise ValueError
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                value = os.read(descriptor, 33)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if len(value) != 32 or before.st_ino != after.st_ino or before.st_dev != after.st_dev:
                raise ValueError
            return bytearray(value)
        except (OSError, ValueError):
            raise OwnerCredentialUnavailable("BROKERAGE_KEK_UNAVAILABLE") from None

    @staticmethod
    def _aad(envelope: BoundMockCredentialEnvelope, field: str) -> bytes:
        return (
            f"mars-brokerage-v1|{envelope.owner_user_id}|KIS_MOCK|"
            f"{envelope.account_id}|{_KEK_VERSION}|{field}"
        ).encode("ascii")

    @staticmethod
    def _parse_payload(payload: bytearray) -> tuple[str, str, str]:
        offset = 0
        parts: list[str] = []
        for minimum, maximum in ((8, 256), (8, 512), (10, 10)):
            if len(payload) - offset < 4:
                raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
            length = struct.unpack_from(">i", payload, offset)[0]
            offset += 4
            if length < minimum or length > maximum or len(payload) - offset < length:
                raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
            parts.append(bytes(payload[offset : offset + length]).decode("ascii"))
            offset += length
        if offset != len(payload):
            raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
        key, secret, account = parts
        if (
            _APP_KEY.fullmatch(key) is None
            or _APP_SECRET.fullmatch(secret) is None
            or _ACCOUNT_NO.fullmatch(account) is None
        ):
            raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
        return key, secret, account
