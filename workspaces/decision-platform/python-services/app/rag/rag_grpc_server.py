"""S4.6 fixture/retrieval-only RagService process entrypoint."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.rag.rag_rpc import S45FixtureRagEngine, create_rag_server


_SAFE_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,256}$")


@dataclass(frozen=True, slots=True)
class RagGrpcServerSettings:
    """RAG business RPC는 mTLS 전 numeric loopback과 reflection false로만 시작한다."""

    bind_address: str = "127.0.0.1:50053"
    shared_secret: str = ""

    def __post_init__(self) -> None:
        if not _is_numeric_loopback(self.bind_address):
            raise ValueError("RAG gRPC must bind to numeric loopback")
        if _SAFE_SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("RAG_GRPC_SHARED_SECRET is invalid")

    @classmethod
    def from_env(cls) -> "RagGrpcServerSettings":
        reflection = os.environ.get("RAG_GRPC_ENABLE_REFLECTION", "false").strip().lower()
        if reflection not in {"true", "false"}:
            raise ValueError("RAG_GRPC_ENABLE_REFLECTION must be true or false")
        if reflection == "true":
            raise ValueError("RAG gRPC reflection is disabled by the S4.6 contract")
        secret = os.environ.get("RAG_GRPC_SHARED_SECRET", "").strip()
        if not secret:
            secret = os.environ.get("PYTHON_GRPC_SHARED_SECRET", "").strip()
        return cls(
            bind_address=os.environ.get(
                "RAG_GRPC_BIND_ADDRESS", "127.0.0.1:50053"
            ).strip(),
            shared_secret=secret,
        )


def serve(settings: RagGrpcServerSettings | None = None) -> None:
    """provider transport가 없는 S4.5 fixture engine을 주입해 RagService를 실행한다."""

    resources = create_rag_server(settings or RagGrpcServerSettings.from_env(), S45FixtureRagEngine())
    try:
        resources.server.start()
        resources.server.wait_for_termination()
    finally:
        resources.server.stop(grace=0).wait(timeout=2)


def _is_numeric_loopback(value: str) -> bool:
    if value.startswith("127.0.0.1:"):
        port = value.removeprefix("127.0.0.1:")
    elif value.startswith("[::1]:"):
        port = value.removeprefix("[::1]:")
    else:
        return False
    return port.isdigit() and 1 <= int(port) <= 65_535


if __name__ == "__main__":
    serve()
