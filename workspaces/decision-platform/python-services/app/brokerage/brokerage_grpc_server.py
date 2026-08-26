"""certification 이후 같은 기능 컨테이너에서만 여는 KIS Mock gRPC server."""

from __future__ import annotations

import os
import re
from concurrent import futures
from dataclasses import dataclass

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from pydantic import SecretStr

from app.brokerage.brokerage_rpc import BrokerageServicer
from app.brokerage.kis_mock_online_client import KISBrokerageCallBudget, KISMockBrokerageHttpClient
from app.brokerage.kis_mock_online_runtime import KISMockOnlineBalanceReader
from app.brokerage.kis_mock_order_gateway import KISMockOrderGateway
from app.brokerage.mock_order_reference_store import EncryptedRedisOrderReferenceStore
from app.data.kis._credential_transport import _build_redis_client
from app.data.kis.settings import KISSettings
from app.generated import brokerage_pb2_grpc

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
    def from_env(cls) -> BrokerageGrpcServerSettings:
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
            reference_key=SecretStr(os.environ.get("KIS_MOCK_ORDER_REFERENCE_KEY", "").strip()),
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


def main() -> None:
    """수동 `--mock` 모드에서만 retry 없는 mock brokerage RPC를 제공한다."""

    settings = BrokerageGrpcServerSettings.from_env()
    redis_client = _build_redis_client()
    client: KISMockBrokerageHttpClient | None = None
    server: grpc.Server | None = None
    try:
        reference_store = EncryptedRedisOrderReferenceStore(
            redis_client,
            encryption_key=settings.reference_key,
            ttl_seconds=settings.reference_ttl_seconds,
        )
        budget = KISBrokerageCallBudget(
            token_p_cap=settings.token_p_physical_cap,
            brokerage_cap=settings.brokerage_physical_cap,
        )
        client = KISMockBrokerageHttpClient(
            settings=KISSettings(kis_mode="mock", kis_offline=False, kis_retry_attempts=1),
            budget=budget,
        )
        gateway = KISMockOrderGateway(client, mode="mock", reference_store=reference_store)
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=4),
            options=(
                ("grpc.max_receive_message_length", 262_144),
                ("grpc.max_send_message_length", 1_048_576),
                ("grpc.enable_retries", 0),
            ),
        )
        brokerage_pb2_grpc.add_BrokerageServiceServicer_to_server(  # type: ignore[no-untyped-call]
            BrokerageServicer(
                gateway,
                settings.shared_secret,
                bound_account_id=settings.bound_account_id,
                balance_reader=KISMockOnlineBalanceReader(client),
            ),
            server,
        )
        health_service = health.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(health_service, server)
        health_service.set("", health_pb2.HealthCheckResponse.SERVING)
        if server.add_insecure_port(settings.bind_address) != int(
            settings.bind_address.rsplit(":", 1)[1]
        ):
            raise RuntimeError("KIS Mock brokerage gRPC bind failed")
        server.start()
        server.wait_for_termination()
    finally:
        if server is not None:
            server.stop(grace=5).wait()
        if client is not None:
            client.close()
        redis_client.close()


if __name__ == "__main__":
    main()
