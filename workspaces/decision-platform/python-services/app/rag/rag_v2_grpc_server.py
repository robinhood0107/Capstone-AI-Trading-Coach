"""Immutable RAG v2 profile-selected loopback process entrypoint.

The process receives only a purpose-separated loopback secret and a query-role DSN through
environment variables. It never accepts DB credentials, owner IDs, raw paths, or provider keys
on the command line. BGE stays local; an optional Voyage query profile is assembled only from a
0700/0600 local control+writer-secret boundary, effective consent on the Spring side, and a fresh
question/scope-specific packet for every single permitted physical request.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path

from app.rag.authorized_retrieval import ExactIdentifierExtractor, QueryNormalizer
from app.rag.authorized_retrieval_adapters import LocalBgeQueryEmbedder
from app.rag.bge_runtime import load_bge_onnx_embedder
from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5VoyageQueryRuntimeConfiguration,
    load_optional_pre_s5_voyage_query_runtime_configuration,
    load_pre_s5_voyage_query_writer_database_dsn,
    resolve_voyage_api_key,
)
from app.rag.pre_s5_voyage_query_transport import (
    PacketGatedPreS5VoyageContext4QueryEmbedder,
)
from app.rag.pre_s5_voyage_query_usage_repository import (
    PsycopgPreS5VoyageQueryUsageRepository,
)
from app.rag.pre_s5_voyage_transport import UrllibPreS5VoyageHttpSender
from app.rag.rag_v2_authorized_retrieval import RagV2AuthorizedHybridRetrieval, RagV2RrfFusion
from app.rag.rag_v2_authorized_retrieval_adapter import PsycopgRagV2AuthorizedRetrievalAdapter
from app.rag.rag_v2_rpc import (
    ProfileSelectedRagV2RetrievalOnlyEngine,
    RagV2ScopeReader,
    create_rag_v2_server,
)


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
    """v2 profile-selected retrieval process의 최소 startup surface를 fail-closed하게 검증한다."""

    bind_address: str = "127.0.0.1:50054"
    shared_secret: str = ""
    query_database_dsn: str = ""
    bge_packet_root: Path | None = None
    bge_enabled: bool = True
    voyage_query_runtime: PreS5VoyageQueryRuntimeConfiguration | None = None

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
        if type(self.bge_enabled) is not bool:
            raise ValueError("RAG v2 BGE profile setting is invalid")
        if self.bge_enabled and (
            not isinstance(self.bge_packet_root, Path)
            or not self.bge_packet_root.is_absolute()
        ):
            raise ValueError("CAPSTONE_RAG_BGE_PACKET_ROOT is invalid")
        if self.voyage_query_runtime is not None:
            if not isinstance(self.voyage_query_runtime, PreS5VoyageQueryRuntimeConfiguration):
                raise ValueError("RAG v2 Voyage query runtime setting is invalid")
            if self.voyage_query_runtime.bge_enabled != self.bge_enabled:
                raise ValueError("RAG v2 local profile settings drifted")
        if not self.bge_enabled and self.voyage_query_runtime is None:
            raise ValueError("RAG v2 requires one retrieval profile")

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
        local_root_text = os.environ.get("CAPSTONE_RAG_LOCAL_ROOT", "").strip()
        local_root = Path(local_root_text) if local_root_text else None
        if local_root is not None and not local_root.is_absolute():
            raise ValueError("CAPSTONE_RAG_LOCAL_ROOT is invalid")
        try:
            voyage_query_runtime = (
                load_optional_pre_s5_voyage_query_runtime_configuration(local_root=local_root)
                if local_root is not None
                else None
            )
        except PreS5ProviderActivationError as error:
            raise ValueError("RAG v2 Voyage query runtime control is invalid") from error
        bge_packet_root_text = os.environ.get("CAPSTONE_RAG_BGE_PACKET_ROOT", "").strip()
        return cls(
            bind_address=os.environ.get(
                "RAG_V2_GRPC_BIND_ADDRESS", "127.0.0.1:50054"
            ).strip(),
            shared_secret=secret,
            query_database_dsn=os.environ.get("RAG_V2_QUERY_DATABASE_DSN", "").strip(),
            bge_packet_root=Path(bge_packet_root_text) if bge_packet_root_text else None,
            bge_enabled=(voyage_query_runtime.bge_enabled if voyage_query_runtime is not None else True),
            voyage_query_runtime=voyage_query_runtime,
        )


def serve(settings: RagV2GrpcServerSettings | None = None) -> None:
    """verified local profile map을 별도 v2 service namespace로 실행한다."""

    effective_settings = settings or RagV2GrpcServerSettings.from_env()
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(
        database_dsn=effective_settings.query_database_dsn
    )
    engine = build_rag_v2_engine(
        settings=effective_settings,
        scope_reader=adapter,
        retrieval_adapter=adapter,
        environment=os.environ,
    )
    resources = create_rag_v2_server(
        effective_settings,
        engine,
    )
    try:
        resources.server.start()
        resources.server.wait_for_termination()
    finally:
        resources.server.stop(grace=0).wait(timeout=2)


def build_rag_v2_engine(
    *,
    settings: RagV2GrpcServerSettings,
    scope_reader: RagV2ScopeReader,
    retrieval_adapter: object,
    environment: Mapping[str, object],
) -> ProfileSelectedRagV2RetrievalOnlyEngine:
    """Assemble configured immutable profile adapters without letting a query choose its provider.

    The local runtime control can add Voyage but cannot itself send a request: its adapter validates
    a fresh exact packet, DB writer lease, scope profile, and Spring-provided consent before opening
    the fixed-origin sender. Missing any one condition returns a typed zero-call failure.
    """

    retrievals: dict[str, RagV2AuthorizedHybridRetrieval] = {}
    if settings.bge_enabled:
        bge_packet_root = settings.bge_packet_root
        if bge_packet_root is None:  # Defensive narrowing after frozen settings validation.
            raise ValueError("CAPSTONE_RAG_BGE_PACKET_ROOT is invalid")
        retrievals["bge_m3_local_1024_v1"] = _retrieval(
            retrieval_adapter=retrieval_adapter,
            query_embedder=LocalBgeQueryEmbedder(load_bge_onnx_embedder(bge_packet_root)),
        )
    runtime = settings.voyage_query_runtime
    if runtime is not None:
        try:
            voyage_key = resolve_voyage_api_key(environment)
            writer_dsn = load_pre_s5_voyage_query_writer_database_dsn(
                local_root=runtime.local_root
            )
        except PreS5ProviderActivationError as error:
            raise ValueError("RAG v2 Voyage query credentials are unavailable") from error
        retrievals["voyage_context_4_1024_v1"] = _retrieval(
            retrieval_adapter=retrieval_adapter,
            query_embedder=PacketGatedPreS5VoyageContext4QueryEmbedder(
                local_root=runtime.local_root,
                binding=runtime.binding,
                api_key=voyage_key,
                usage_repository=PsycopgPreS5VoyageQueryUsageRepository(
                    database_dsn=writer_dsn,
                ),
                # The sender creates no socket until a current one-shot packet has been validated and
                # its writer lease has been claimed for the specific request.
                sender=UrllibPreS5VoyageHttpSender(),
            ),
        )
    return ProfileSelectedRagV2RetrievalOnlyEngine(
        scope_reader=scope_reader,
        retrievals=retrievals,
    )


def _retrieval(*, retrieval_adapter: object, query_embedder: object) -> RagV2AuthorizedHybridRetrieval:
    """All profiles share the SQL authorization/RRF code; only their profile-matched query embedding differs."""

    return RagV2AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=query_embedder,  # type: ignore[arg-type]
        exact_retriever=retrieval_adapter,  # type: ignore[arg-type]
        lexical_retriever=retrieval_adapter,  # type: ignore[arg-type]
        dense_retriever=retrieval_adapter,  # type: ignore[arg-type]
        rrf_fusion=RagV2RrfFusion(),
    )


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
