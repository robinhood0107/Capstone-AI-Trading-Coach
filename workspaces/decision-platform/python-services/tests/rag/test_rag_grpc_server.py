from __future__ import annotations

import pytest

from app.rag.rag_grpc_server import RagGrpcServerSettings


_SECRET = "rag-grpc-shared-secret-for-s4-6-tests-0001"


def test_rag_server_settings_default_to_dedicated_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.delenv("RAG_GRPC_BIND_ADDRESS", raising=False)
    monkeypatch.delenv("RAG_GRPC_ENABLE_REFLECTION", raising=False)

    settings = RagGrpcServerSettings.from_env()

    assert settings.bind_address == "127.0.0.1:50053"
    assert settings.shared_secret == _SECRET


@pytest.mark.parametrize(
    "address",
    ["0.0.0.0:50053", "localhost:50053", "[::]:50053"],
)
def test_rag_server_rejects_non_numeric_loopback(address: str) -> None:
    with pytest.raises(ValueError, match="numeric loopback"):
        RagGrpcServerSettings(address, _SECRET)


def test_rag_server_rejects_reflection_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_GRPC_ENABLE_REFLECTION", "true")

    with pytest.raises(ValueError, match="reflection"):
        RagGrpcServerSettings.from_env()
