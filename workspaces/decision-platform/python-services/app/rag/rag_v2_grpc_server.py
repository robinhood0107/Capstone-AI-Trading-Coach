"""Immutable RAG v2 local-BGE loopback process entrypoint.

The process receives only a purpose-separated loopback secret and a query-role DSN through
environment variables. It never accepts DB credentials, owner IDs, raw paths, or provider keys
on the command line; BGE inference remains local and no external provider transport is wired.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path

from app.rag.authorized_retrieval import ExactIdentifierExtractor, QueryNormalizer
from app.rag.authorized_retrieval_adapters import LocalBgeQueryEmbedder
from app.rag.bge_runtime import load_bge_onnx_embedder
from app.rag.rag_v2_authorized_retrieval import RagV2AuthorizedHybridRetrieval, RagV2RrfFusion
from app.rag.rag_v2_authorized_retrieval_adapter import PsycopgRagV2AuthorizedRetrievalAdapter
from app.rag.rag_v2_rpc import BgeRagV2RetrievalOnlyEngine, create_rag_v2_server


_SAFE_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,256}$")
_FORBIDDEN_SHARED_SECRET_ENV_NAMES = (
    "DECISION_GRPC_SHARED_SECRET",
    "RAG_GRPC_SHARED_SECRET",
    "PYTHON_GRPC_SHARED_SECRET",
    "JWT_SECRET",
    "BROKERAGE_DB_CAPABILITY_TOKEN",
    "BROKERAGE_GRPC_SHARED_SECRET",
)


@dataclass(frozen=True, slots=True)
class RagV2GrpcServerSettings:
    """v2 local retrieval process의 최소 startup surface를 fail-closed하게 검증한다."""

    bind_address: str = "127.0.0.1:50054"
    shared_secret: str = ""
    query_database_dsn: str = ""
    bge_packet_root: Path = Path("/")

    def __post_init__(self) -> None:
        if not _is_numeric_loopback(self.bind_address):
            raise ValueError("RAG v2 gRPC must bind to numeric loopback")
        if _SAFE_SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("RAG_V2_GRPC_SHARED_SECRET is invalid")
        if (
            not self.query_database_dsn
            or len(self.query_database_dsn) > 4_096
            or any(character in self.query_database_dsn for character in ("\x00", "\r", "\n"))
        ):
            raise ValueError("RAG_V2_QUERY_DATABASE_DSN is invalid")
        if not self.bge_packet_root.is_absolute():
            raise ValueError("CAPSTONE_RAG_BGE_PACKET_ROOT is invalid")

    @classmethod
    def from_env(cls) -> "RagV2GrpcServerSettings":
        """reflection/secret reuse를 거부하고 local path는 provider URL로 해석하지 않는다."""

        reflection = os.environ.get("RAG_V2_GRPC_ENABLE_REFLECTION", "false").strip().lower()
        if reflection not in {"true", "false"}:
            raise ValueError("RAG_V2_GRPC_ENABLE_REFLECTION must be true or false")
        if reflection == "true":
            raise ValueError("RAG v2 gRPC reflection is disabled by the S4.7D contract")
        secret = os.environ.get("RAG_V2_GRPC_SHARED_SECRET", "").strip()
        for forbidden_name in _FORBIDDEN_SHARED_SECRET_ENV_NAMES:
            forbidden_value = os.environ.get(forbidden_name, "").strip()
            if forbidden_value and compare_digest(secret, forbidden_value):
                raise ValueError(
                    "RAG_V2_GRPC_SHARED_SECRET must be purpose-separated from privileged service secrets"
                )
        return cls(
            bind_address=os.environ.get(
                "RAG_V2_GRPC_BIND_ADDRESS", "127.0.0.1:50054"
            ).strip(),
            shared_secret=secret,
            query_database_dsn=os.environ.get("RAG_V2_QUERY_DATABASE_DSN", "").strip(),
            bge_packet_root=Path(os.environ.get("CAPSTONE_RAG_BGE_PACKET_ROOT", "").strip()),
        )


def serve(settings: RagV2GrpcServerSettings | None = None) -> None:
    """verified packet/local DB retrieval engine을 별도 v2 service namespace로 실행한다."""

    effective_settings = settings or RagV2GrpcServerSettings.from_env()
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(
        database_dsn=effective_settings.query_database_dsn
    )
    retrieval = RagV2AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=LocalBgeQueryEmbedder(
            load_bge_onnx_embedder(effective_settings.bge_packet_root)
        ),
        exact_retriever=adapter,
        lexical_retriever=adapter,
        dense_retriever=adapter,
        rrf_fusion=RagV2RrfFusion(),
    )
    resources = create_rag_v2_server(
        effective_settings,
        BgeRagV2RetrievalOnlyEngine(scope_reader=adapter, retrieval=retrieval),
    )
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
