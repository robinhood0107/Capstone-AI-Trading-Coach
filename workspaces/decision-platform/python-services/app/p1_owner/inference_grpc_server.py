"""Loopback-only bounded gRPC transport for the fixed Return inference ABI."""

from __future__ import annotations

import os
import re
from concurrent import futures
from dataclasses import dataclass
from hmac import compare_digest
from pathlib import Path
from typing import Never

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.p1_owner.inference import ReturnInferenceError, ReturnInferenceModel

SERVICE_NAME = "capstone.return_inference.v1.ReturnInferenceService"
METHOD_PATH = f"/{SERVICE_NAME}/Infer"
_AUTH_KEY = "x-return-inference-auth"
_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_CONCURRENCY = 2
_MAX_DEADLINE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ReturnInferenceSettings:
    bind_address: str
    shared_secret: str
    bundle_root: Path | None
    manifest_sha256: str | None
    allow_synthetic: bool

    @classmethod
    def from_env(cls) -> ReturnInferenceSettings:
        root = os.environ.get("RETURN_INFERENCE_BUNDLE_ROOT", "").strip()
        manifest_sha = os.environ.get("RETURN_INFERENCE_MANIFEST_SHA256", "").strip()
        settings = cls(
            bind_address=os.environ.get(
                "RETURN_INFERENCE_GRPC_BIND_ADDRESS", "127.0.0.1:50057"
            ).strip(),
            shared_secret=os.environ.get("RETURN_INFERENCE_GRPC_SHARED_SECRET", "").strip(),
            bundle_root=Path(root) if root else None,
            manifest_sha256=manifest_sha or None,
            allow_synthetic=os.environ.get("RETURN_INFERENCE_ALLOW_SYNTHETIC", "false").lower()
            == "true",
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _is_loopback(self.bind_address):
            raise ValueError("Return inference gRPC must bind to numeric loopback")
        if _SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("Return inference gRPC secret is invalid")
        if (self.bundle_root is None) != (self.manifest_sha256 is None):
            raise ValueError("Return inference bundle root and manifest SHA must be paired")
        if self.manifest_sha256 is not None and _SHA256.fullmatch(self.manifest_sha256) is None:
            raise ValueError("Return inference manifest SHA is invalid")


class ReturnInferenceRpc:
    def __init__(self, model: ReturnInferenceModel | None, shared_secret: str) -> None:
        self._model = model
        self._shared_secret = shared_secret

    def infer(self, request_bytes: bytes, context: grpc.ServicerContext) -> bytes:
        _authenticate(context, self._shared_secret)
        remaining = context.time_remaining()
        if remaining is None or remaining <= 0 or remaining > _MAX_DEADLINE_SECONDS + 0.25:
            _abort(context, grpc.StatusCode.DEADLINE_EXCEEDED, "bounded deadline is required")
        if self._model is None:
            _abort(
                context, grpc.StatusCode.FAILED_PRECONDITION, "verified Return model is unavailable"
            )
        try:
            assert self._model is not None
            return self._model.infer_bytes(request_bytes)
        except ReturnInferenceError:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Return inference request is invalid")


def create_server(settings: ReturnInferenceSettings) -> grpc.Server:
    settings.validate()
    model = None
    if settings.bundle_root is not None and settings.manifest_sha256 is not None:
        model = ReturnInferenceModel.load(
            bundle_root=settings.bundle_root,
            manifest_sha256=settings.manifest_sha256,
            allow_synthetic=settings.allow_synthetic,
        )
    rpc = ReturnInferenceRpc(model, settings.shared_secret)
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY),
        options=(
            ("grpc.max_receive_message_length", _MAX_REQUEST_BYTES),
            ("grpc.max_send_message_length", _MAX_RESPONSE_BYTES),
        ),
        maximum_concurrent_rpcs=_MAX_CONCURRENCY,
    )
    handler = grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {
            "Infer": grpc.unary_unary_rpc_method_handler(
                rpc.infer,
                request_deserializer=lambda value: value,
                response_serializer=lambda value: value,
            )
        },
    )
    server.add_generic_rpc_handlers((handler,))
    health_service = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_service, server)
    health_service.set(SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    if server.add_insecure_port(settings.bind_address) == 0:
        raise RuntimeError("Return inference loopback port could not be bound")
    return server


def serve() -> None:
    server = create_server(ReturnInferenceSettings.from_env())
    try:
        server.start()
        server.wait_for_termination()
    finally:
        server.stop(grace=0).wait(timeout=2)


def _authenticate(context: grpc.ServicerContext, shared_secret: str) -> None:
    values = [value for key, value in context.invocation_metadata() if key == _AUTH_KEY]
    if (
        len(values) != 1
        or not isinstance(values[0], str)
        or not compare_digest(values[0], shared_secret)
    ):
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "Return inference authentication failed")


def _abort(context: grpc.ServicerContext, status: grpc.StatusCode, detail: str) -> Never:
    context.abort(status, detail)
    raise AssertionError("gRPC abort returned unexpectedly")


def _is_loopback(address: str) -> bool:
    if address.startswith("127.0.0.1:"):
        port = address.removeprefix("127.0.0.1:")
    elif address.startswith("[::1]:"):
        port = address.removeprefix("[::1]:")
    else:
        return False
    return port.isdigit() and 1 <= int(port) <= 65_535


if __name__ == "__main__":
    serve()
