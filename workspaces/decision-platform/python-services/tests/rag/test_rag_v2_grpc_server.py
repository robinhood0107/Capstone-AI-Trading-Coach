from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.rag_v2_grpc_server import RagV2GrpcServerSettings


_SECRET = "rag-v2-grpc-shared-secret-for-s4-7d-settings-0001"


def test_v2_server_settings_require_dedicated_loopback_query_dsn_and_absolute_bge_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_V2_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_V2_QUERY_DATABASE_DSN", "postgresql://decision_rag_query@localhost/rag")
    monkeypatch.setenv("CAPSTONE_RAG_BGE_PACKET_ROOT", "/var/lib/capstone/bge-packet")
    monkeypatch.delenv("RAG_V2_GRPC_ENABLE_REFLECTION", raising=False)

    settings = RagV2GrpcServerSettings.from_env()

    assert settings.bind_address == "127.0.0.1:50054"
    assert settings.shared_secret == _SECRET
    assert settings.query_database_dsn == "postgresql://decision_rag_query@localhost/rag"
    assert settings.bge_packet_root == Path("/var/lib/capstone/bge-packet")


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("RAG_V2_GRPC_BIND_ADDRESS", "0.0.0.0:50054", "numeric loopback"),
        ("RAG_V2_QUERY_DATABASE_DSN", "", "QUERY_DATABASE_DSN"),
        ("CAPSTONE_RAG_BGE_PACKET_ROOT", "relative/packet", "BGE_PACKET_ROOT"),
    ],
)
def test_v2_server_settings_fail_closed_for_invalid_runtime_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    expected: str,
) -> None:
    monkeypatch.setenv("RAG_V2_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_V2_QUERY_DATABASE_DSN", "postgresql://decision_rag_query@localhost/rag")
    monkeypatch.setenv("CAPSTONE_RAG_BGE_PACKET_ROOT", "/var/lib/capstone/bge-packet")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=expected):
        RagV2GrpcServerSettings.from_env()


def test_v2_server_settings_reject_reflection_and_reused_privileged_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_V2_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_V2_QUERY_DATABASE_DSN", "postgresql://decision_rag_query@localhost/rag")
    monkeypatch.setenv("CAPSTONE_RAG_BGE_PACKET_ROOT", "/var/lib/capstone/bge-packet")
    monkeypatch.setenv("RAG_V2_GRPC_ENABLE_REFLECTION", "true")

    with pytest.raises(ValueError, match="reflection"):
        RagV2GrpcServerSettings.from_env()

    monkeypatch.setenv("RAG_V2_GRPC_ENABLE_REFLECTION", "false")
    monkeypatch.setenv("DECISION_GRPC_SHARED_SECRET", _SECRET)
    with pytest.raises(ValueError, match="purpose-separated"):
        RagV2GrpcServerSettings.from_env()
