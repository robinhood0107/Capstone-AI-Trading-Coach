from __future__ import annotations

import pytest

from app.rag.rag_grpc_server import RagGrpcServerSettings


_SECRET = "rag-grpc-shared-secret-for-s4-6-tests-0001"
_PYTHON_SECRET = "python-grpc-shared-secret-for-s2-3-tests-0001"
_JWT_SECRET = "jwt-signing-secret-for-s0-3-tests-00000001"


def test_rag_server_settings_default_to_dedicated_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", _PYTHON_SECRET)
    monkeypatch.delenv("RAG_GRPC_BIND_ADDRESS", raising=False)
    monkeypatch.delenv("RAG_GRPC_ENABLE_REFLECTION", raising=False)

    settings = RagGrpcServerSettings.from_env()

    assert settings.bind_address == "127.0.0.1:50053"
    assert settings.shared_secret == _SECRET


def test_rag_server_requires_its_dedicated_secret_instead_of_python_grpc_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAG_GRPC_SHARED_SECRET", raising=False)
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", _PYTHON_SECRET)

    with pytest.raises(ValueError, match="RAG_GRPC_SHARED_SECRET"):
        RagGrpcServerSettings.from_env()


def test_rag_server_rejects_reusing_python_grpc_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", _SECRET)

    with pytest.raises(ValueError, match="must differ"):
        RagGrpcServerSettings.from_env()


def test_rag_server_rejects_reusing_jwt_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("JWT_SECRET", _SECRET)

    with pytest.raises(ValueError, match="must differ"):
        RagGrpcServerSettings.from_env()


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
