"""decision-platform Python gRPC health 서버. 인증 RPC 전에는 loopback 밖으로 노출하지 않는다."""

import os
from concurrent import futures
from dataclasses import dataclass

import grpc
from grpc_health.v1 import health, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

HEALTH_SERVICE_NAME = "grpc.health.v1.Health"


@dataclass(frozen=True)
class GrpcServerSettings:
    """S1.3 RPC 도입 전 plaintext health는 loopback에만 bind하고 reflection은 명시 opt-in한다."""

    bind_address: str = "127.0.0.1:50051"
    enable_reflection: bool = False

    def __post_init__(self) -> None:
        if not _is_loopback_address(self.bind_address):
            raise ValueError("Python gRPC must bind to loopback until authenticated transport is implemented")

    @classmethod
    def from_env(cls) -> "GrpcServerSettings":
        address = os.environ.get("PYTHON_GRPC_BIND_ADDRESS", "127.0.0.1:50051").strip()
        raw_reflection = os.environ.get("PYTHON_GRPC_ENABLE_REFLECTION", "false").strip().lower()
        if raw_reflection not in {"true", "false"}:
            raise ValueError("PYTHON_GRPC_ENABLE_REFLECTION must be true or false")
        return cls(bind_address=address, enable_reflection=raw_reflection == "true")


def serve(settings: GrpcServerSettings | None = None) -> None:
    settings = settings or GrpcServerSettings.from_env()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    if settings.enable_reflection:
        reflection.enable_server_reflection((HEALTH_SERVICE_NAME, reflection.SERVICE_NAME), server)
    bound_port = server.add_insecure_port(settings.bind_address)
    if bound_port == 0:
        raise RuntimeError("Python gRPC loopback port could not be bound")
    server.start()
    server.wait_for_termination()


def _is_loopback_address(address: str) -> bool:
    if address.startswith("127.0.0.1:"):
        port_text = address.removeprefix("127.0.0.1:")
    elif address.startswith("[::1]:"):
        port_text = address.removeprefix("[::1]:")
    else:
        return False
    return port_text.isdigit() and 1 <= int(port_text) <= 65_535


if __name__ == "__main__":
    serve()
