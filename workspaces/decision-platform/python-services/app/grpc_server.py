"""decision-platform stored-observation gRPC 서버. loopback 밖 plaintext는 허용하지 않는다."""

import os
import re
from dataclasses import dataclass

from app.disclosure_repository import PostgresStoredDisclosureRepository
from app.disclosure_rpc import create_disclosure_server


@dataclass(frozen=True)
class GrpcServerSettings:
    """plaintext business RPC는 같은 namespace의 loopback에만 bind하고 reflection은 금지한다."""

    bind_address: str = "127.0.0.1:50051"
    shared_secret: str = ""

    def __post_init__(self) -> None:
        if not _is_loopback_address(self.bind_address):
            raise ValueError(
                "Python gRPC must bind to loopback until authenticated transport is implemented"
            )
        if _SHARED_SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("PYTHON_GRPC_SHARED_SECRET must be 32..256 safe ASCII characters")

    @classmethod
    def from_env(cls) -> "GrpcServerSettings":
        address = os.environ.get("PYTHON_GRPC_BIND_ADDRESS", "127.0.0.1:50051").strip()
        shared_secret = os.environ.get("PYTHON_GRPC_SHARED_SECRET", "").strip()
        raw_reflection = os.environ.get("PYTHON_GRPC_ENABLE_REFLECTION", "false").strip().lower()
        if raw_reflection not in {"true", "false"}:
            raise ValueError("PYTHON_GRPC_ENABLE_REFLECTION must be true or false")
        if raw_reflection == "true":
            raise ValueError("Python gRPC reflection is disabled by the S2.3 contract")
        return cls(bind_address=address, shared_secret=shared_secret)


def serve(settings: GrpcServerSettings | None = None) -> None:
    """저장 observation reader를 주입해 health와 business RPC를 함께 시작한다."""
    settings = settings or GrpcServerSettings.from_env()
    repository = PostgresStoredDisclosureRepository.from_env()
    server = create_disclosure_server(settings, repository)
    try:
        server.start()
        server.wait_for_termination()
    finally:
        server.stop(grace=0).wait(timeout=2)
        repository.close()


def _is_loopback_address(address: str) -> bool:
    if address.startswith("127.0.0.1:"):
        port_text = address.removeprefix("127.0.0.1:")
    elif address.startswith("[::1]:"):
        port_text = address.removeprefix("[::1]:")
    else:
        return False
    return port_text.isdigit() and 1 <= int(port_text) <= 65_535


_SHARED_SECRET = re.compile(r"[A-Za-z0-9._~:-]{32,256}")


if __name__ == "__main__":
    serve()
