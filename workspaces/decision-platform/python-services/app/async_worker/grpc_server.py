from __future__ import annotations

from concurrent import futures
from dataclasses import dataclass
from hmac import compare_digest
import os
import re
from typing import Never

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.async_worker.core import AsyncWork, AsyncWorkProcessor
from app.async_worker.postgres import PostgresAsyncWorkRepository, is_decision_worker_dsn
from app.generated import async_worker_pb2, async_worker_pb2_grpc


_AUTH_KEY = "x-async-worker-auth"
_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,128}$")
_MAX_REQUEST_BYTES = 65_536
_MAX_RESPONSE_BYTES = 4_096
_MAX_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class AsyncWorkerSettings:
    bind_address: str
    shared_secret: str
    database_dsn: str
    partition_hmac_key: bytes

    @classmethod
    def from_env(cls) -> "AsyncWorkerSettings":
        settings = cls(
            bind_address=os.environ.get(
                "ASYNC_WORKER_GRPC_BIND_ADDRESS", "127.0.0.1:50056"
            ).strip(),
            shared_secret=os.environ.get("ASYNC_WORKER_GRPC_SHARED_SECRET", "").strip(),
            database_dsn=os.environ.get("ASYNC_WORKER_DATABASE_DSN", "").strip(),
            partition_hmac_key=os.environ.get(
                "ASYNC_PARTITION_HMAC_KEY", ""
            ).encode(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _is_loopback(self.bind_address):
            raise ValueError("async worker gRPC must bind to numeric loopback")
        if _SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("async worker gRPC secret is invalid")
        if not is_decision_worker_dsn(self.database_dsn):
            raise ValueError("async worker must use the decision_worker DSN")
        if not 32 <= len(self.partition_hmac_key) <= 128:
            raise ValueError("async worker partition key is invalid")
        if compare_digest(self.shared_secret.encode(), self.partition_hmac_key):
            raise ValueError("async worker secrets must be purpose-separated")


class AsyncWorkerServicer(async_worker_pb2_grpc.AsyncWorkerServiceServicer):
    def __init__(self, processor: AsyncWorkProcessor, shared_secret: str) -> None:
        self._processor = processor
        self._shared_secret = shared_secret

    def Process(
        self,
        request: async_worker_pb2.AsyncWorkRequest,
        context: grpc.ServicerContext,
    ) -> async_worker_pb2.AsyncWorkResponse:
        _authenticate(context, self._shared_secret)
        transport = {
            async_worker_pb2.ASYNC_TRANSPORT_DB: "DB",
            async_worker_pb2.ASYNC_TRANSPORT_KAFKA: "KAFKA",
        }.get(request.transport, "UNSPECIFIED")
        result = self._processor.process(
            AsyncWork(
                event_id=request.event_id,
                event_type=request.event_type,
                schema_version=request.schema_version,
                payload_hash=request.payload_hash,
                job_id=request.job_id,
                job_type=request.job_type,
                payload_json=bytes(request.payload_json),
                claim_token=request.claim_token or None,
                transport=transport,
                attempt=request.attempt,
                source_topic=request.event_type,
                partition_key=request.partition_key or None,
            )
        )
        outcome = {
            "COMPLETED": async_worker_pb2.ASYNC_WORK_COMPLETED,
            "DUPLICATE": async_worker_pb2.ASYNC_WORK_DUPLICATE,
            "FAILED": async_worker_pb2.ASYNC_WORK_FAILED,
            "NEEDS_REVIEW": async_worker_pb2.ASYNC_WORK_NEEDS_REVIEW,
        }[result.outcome]
        return async_worker_pb2.AsyncWorkResponse(
            job_id=request.job_id,
            outcome=outcome,
            result_ref=result.result_ref or "",
            failure_code=result.failure_code or "",
        )


def create_server(settings: AsyncWorkerSettings) -> grpc.Server:
    settings.validate()
    repository = PostgresAsyncWorkRepository(
        settings.database_dsn, settings.partition_hmac_key
    )
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY),
        options=(
            ("grpc.max_receive_message_length", _MAX_REQUEST_BYTES),
            ("grpc.max_send_message_length", _MAX_RESPONSE_BYTES),
        ),
        maximum_concurrent_rpcs=_MAX_CONCURRENCY,
    )
    async_worker_pb2_grpc.add_AsyncWorkerServiceServicer_to_server(  # type: ignore[no-untyped-call]
        AsyncWorkerServicer(AsyncWorkProcessor(repository), settings.shared_secret), server
    )
    health_service = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_service, server)
    health_service.set(
        async_worker_pb2.DESCRIPTOR.services_by_name["AsyncWorkerService"].full_name,
        health_pb2.HealthCheckResponse.SERVING,
    )
    if server.add_insecure_port(settings.bind_address) == 0:
        raise RuntimeError("async worker gRPC loopback port could not be bound")
    return server


def serve() -> None:
    server = create_server(AsyncWorkerSettings.from_env())
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
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "async worker authentication failed")


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
