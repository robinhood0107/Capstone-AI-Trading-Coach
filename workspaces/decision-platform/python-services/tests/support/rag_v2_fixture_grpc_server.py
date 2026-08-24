"""Production-shaped RAG v2 gRPC fixture process for Spring/Python E2E tests.

The fixture deliberately reuses the runtime settings, query-role adapter, authorization-aware
three-channel retrieval, RRF, and loopback server factory.  It swaps only local BGE inference for
a deterministic 1024-dimensional unit vector so an E2E test neither opens a BGE artifact nor
creates any provider transport.  Startup still reads the exact production environment surface;
``CAPSTONE_RAG_BGE_PACKET_ROOT`` is validated by the shared settings but is never opened here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from app.rag.authorized_retrieval import (
    EMBEDDING_DIMENSION,
    ExactIdentifierExtractor,
    QueryNormalizer,
)
from app.rag.rag_v2_authorized_retrieval import RagV2AuthorizedHybridRetrieval, RagV2RrfFusion
from app.rag.rag_v2_authorized_retrieval_adapter import PsycopgRagV2AuthorizedRetrievalAdapter
from app.rag.rag_v2_grpc_server import RagV2GrpcServerSettings
from app.rag.rag_v2_rpc import (
    BgeRagV2RetrievalOnlyEngine,
    RagV2ServerResources,
    create_rag_v2_server,
)

_BGE_PROFILE = "bge_m3_local_1024_v1"


class DeterministicRagV2FixtureQueryEmbedder:
    """E2E fixture query text를 repeatable local unit vector로만 바꾼다.

    This is test support, never a fallback or production embedding implementation.  A one-hot
    vector makes the norm exact and lets an E2E seed matching pgvector rows without loading a
    model artifact, transmitting question text, or creating a provider client.
    """

    @property
    def embedding_profile_id(self) -> str:
        """The fixture keeps the active local BGE profile identity to exercise profile guards."""

        return _BGE_PROFILE

    def embed_query(self, question: str) -> Sequence[float]:
        """Return one deterministic 1024-dimensional unit vector without I/O or provider calls."""

        if not isinstance(question, str):
            raise TypeError("RAG fixture question must be text")
        digest = hashlib.sha256(question.encode("utf-8")).digest()
        position = int.from_bytes(digest[:2], byteorder="big") % EMBEDDING_DIMENSION
        return tuple(1.0 if index == position else 0.0 for index in range(EMBEDDING_DIMENSION))


def create_fixture_rag_v2_server(
    settings: RagV2GrpcServerSettings | None = None,
) -> RagV2ServerResources:
    """Build the production retrieval path with only its local query embedder replaced for tests."""

    effective_settings = settings or RagV2GrpcServerSettings.from_env()
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(
        database_dsn=effective_settings.query_database_dsn
    )
    retrieval = RagV2AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=DeterministicRagV2FixtureQueryEmbedder(),
        exact_retriever=adapter,
        lexical_retriever=adapter,
        dense_retriever=adapter,
        rrf_fusion=RagV2RrfFusion(),
    )
    return create_rag_v2_server(
        effective_settings,
        BgeRagV2RetrievalOnlyEngine(scope_reader=adapter, retrieval=retrieval),
    )


def serve(settings: RagV2GrpcServerSettings | None = None) -> None:
    """Start the fixture server with production env names and no command-line secret surface."""

    resources = create_fixture_rag_v2_server(settings)
    try:
        resources.server.start()
        resources.server.wait_for_termination()
    finally:
        resources.server.stop(grace=0).wait(timeout=2)


if __name__ == "__main__":
    serve()
