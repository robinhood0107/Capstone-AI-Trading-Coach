import pytest

from app.grpc_server import GrpcServerSettings


def test_grpc_defaults_to_loopback_without_reflection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_GRPC_BIND_ADDRESS", raising=False)
    monkeypatch.delenv("PYTHON_GRPC_ENABLE_REFLECTION", raising=False)

    settings = GrpcServerSettings.from_env()

    assert settings.bind_address == "127.0.0.1:50051"
    assert settings.enable_reflection is False


@pytest.mark.parametrize("address", ["[::]:50051", "0.0.0.0:50051", "192.0.2.10:50051"])
def test_grpc_rejects_non_loopback_bind_without_authenticated_transport(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    monkeypatch.setenv("PYTHON_GRPC_BIND_ADDRESS", address)

    with pytest.raises(ValueError, match="loopback"):
        GrpcServerSettings.from_env()


def test_grpc_reflection_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_GRPC_ENABLE_REFLECTION", "true")

    assert GrpcServerSettings.from_env().enable_reflection is True


def test_grpc_rejects_programmatic_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        GrpcServerSettings(bind_address="[::]:50051")
