"""Retired standalone brokerage server configuration.

P1 brokerage execution is intentionally one-shot through
``kis_mock_approval_probe``.  No reusable gRPC server entry point exists.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from pydantic import SecretStr

_SAFE_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,256}$")
_ACCOUNT_ID = re.compile(r"^acct_[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class BrokerageGrpcServerSettings:
    """Historical configuration parser retained for closed-gate compatibility tests."""

    bind_address: str
    shared_secret: str
    bound_account_id: str
    online_enabled: bool
    token_p_physical_cap: int
    brokerage_physical_cap: int
    reference_key: SecretStr
    reference_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "BrokerageGrpcServerSettings":
        enabled = os.environ.get("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("KIS_MOCK_BROKERAGE_ONLINE_ENABLED must be true or false")
        try:
            token_cap = int(os.environ.get("KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP", ""))
            brokerage_cap = int(os.environ.get("KIS_BROKERAGE_PHYSICAL_CAP", ""))
            ttl = int(os.environ.get("KIS_MOCK_ORDER_REFERENCE_TTL_SECONDS", "900"))
        except ValueError:
            raise ValueError("KIS brokerage physical caps and TTL must be integers") from None
        settings = cls(
            bind_address=os.environ.get(
                "PYTHON_BROKERAGE_GRPC_BIND_ADDRESS", "127.0.0.1:50052"
            ).strip(),
            shared_secret=os.environ.get("BROKERAGE_GRPC_SHARED_SECRET", "").strip(),
            bound_account_id=os.environ.get("KIS_MOCK_BOUND_ACCOUNT_ID", "").strip(),
            online_enabled=enabled == "true",
            token_p_physical_cap=token_cap,
            brokerage_physical_cap=brokerage_cap,
            reference_key=SecretStr(
                os.environ.get("KIS_MOCK_ORDER_REFERENCE_KEY", "").strip()
            ),
            reference_ttl_seconds=ttl,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.online_enabled:
            raise ValueError("KIS Mock brokerage online gate is closed")
        if not _is_loopback(self.bind_address):
            raise ValueError("KIS Mock brokerage gRPC must bind to numeric loopback")
        if _SAFE_SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("BROKERAGE_GRPC_SHARED_SECRET is invalid")
        if _ACCOUNT_ID.fullmatch(self.bound_account_id) is None:
            raise ValueError("KIS_MOCK_BOUND_ACCOUNT_ID is invalid")
        if self.token_p_physical_cap not in {0, 1}:
            raise ValueError("KIS brokerage tokenP cap must be 0 or 1")
        if not 1 <= self.brokerage_physical_cap <= 32:
            raise ValueError("KIS brokerage physical cap must be between 1 and 32")
        if not 60 <= self.reference_ttl_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("KIS mock reference TTL is invalid")


def _is_loopback(address: str) -> bool:
    if address.startswith("127.0.0.1:"):
        port = address.removeprefix("127.0.0.1:")
    elif address.startswith("[::1]:"):
        port = address.removeprefix("[::1]:")
    else:
        return False
    return port.isdigit() and 1 <= int(port) <= 65_535
