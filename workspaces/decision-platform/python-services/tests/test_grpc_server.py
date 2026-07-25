import pytest

from app.grpc_server import GrpcServerSettings


def test_grpc_defaults_to_loopback_without_reflection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_GRPC_BIND_ADDRESS", raising=False)
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", _SHARED_SECRET)
    monkeypatch.delenv("PYTHON_GRPC_ENABLE_REFLECTION", raising=False)

    settings = GrpcServerSettings.from_env()

    assert settings.bind_address == "127.0.0.1:50051"
    assert settings.shared_secret == _SHARED_SECRET


@pytest.mark.parametrize("address", ["[::]:50051", "0.0.0.0:50051", "192.0.2.10:50051"])
def test_grpc_rejects_non_loopback_bind_without_authenticated_transport(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    monkeypatch.setenv("PYTHON_GRPC_BIND_ADDRESS", address)
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", _SHARED_SECRET)

    with pytest.raises(ValueError, match="loopback"):
        GrpcServerSettings.from_env()


def test_grpc_reflection_cannot_be_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", _SHARED_SECRET)
    monkeypatch.setenv("PYTHON_GRPC_ENABLE_REFLECTION", "true")

    with pytest.raises(ValueError, match="reflection"):
        GrpcServerSettings.from_env()


@pytest.mark.parametrize("secret", ["", "short", f"with whitespace {'s' * 32}"])
def test_grpc_rejects_missing_or_unsafe_shared_secret(
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
) -> None:
    monkeypatch.setenv("PYTHON_GRPC_SHARED_SECRET", secret)

    with pytest.raises(ValueError, match="SHARED_SECRET"):
        GrpcServerSettings.from_env()


def test_grpc_rejects_programmatic_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        GrpcServerSettings(bind_address="[::]:50051", shared_secret=_SHARED_SECRET)


_SHARED_SECRET = "python-grpc-shared-secret-for-s2-3-tests-0001"
