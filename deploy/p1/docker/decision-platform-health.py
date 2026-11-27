"""통합 decision-platform의 Spring 및 Python worker health를 함께 확인한다."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from app.generated import async_worker_pb2, rag_v2_pb2
from app.p1_owner.inference_grpc_server import SERVICE_NAME as RETURN_INFERENCE_SERVICE


def _grpc_serving(target: str, service: str, *, timeout: float = 2) -> bool:
    with grpc.insecure_channel(target) as channel:
        result = health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(service=service),
            timeout=timeout,
        )
    return result.status == health_pb2.HealthCheckResponse.SERVING


def main() -> None:
    """필수 프로세스가 준비되면 성공하고, 부가 프로세스는 개별 상태를 출력한다."""

    with urllib.request.urlopen("http://127.0.0.1:8080/actuator/health", timeout=2) as response:
        body = json.load(response)
        if response.status != 200 or body.get("status") != "UP":
            raise RuntimeError("Spring API is not healthy")
    demo = os.environ.get("MARS_PUBLIC_SURFACE_MODE", "LOCAL") == "DEMO"
    if demo:
        # Public DEMO deliberately omits owner async and inference workers. Its
        # only Python RPC capability is the constrained Agent provider.
        services = (
            (
                os.environ.get("STRONG_LLM_GRPC_BIND_ADDRESS", "127.0.0.1:50055"),
                "capstone.decision.internal.s49.StrongLlmAgentService",
            ),
        )
    else:
        services = []
        if os.environ.get("ASYNC_WORKER_ENABLED", "true").lower() == "true":
            services.append(
                (
                    "127.0.0.1:50056",
                    async_worker_pb2.DESCRIPTOR.services_by_name["AsyncWorkerService"].full_name,
                )
            )
        services.append(("127.0.0.1:50057", RETURN_INFERENCE_SERVICE))
    for target, service in services:
        if not _grpc_serving(target, service):
            raise RuntimeError("Python runtime is not healthy")
    if os.environ.get("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "false").lower() == "true":
        if not _grpc_serving("127.0.0.1:50052", ""):
            raise RuntimeError("KIS Mock brokerage is not healthy")
    if os.environ.get("RAG_V2_GRPC_ENABLED", "false").lower() == "true":
        try:
            rag_ready = _grpc_serving(
                os.environ.get("RAG_V2_GRPC_BIND_ADDRESS", "127.0.0.1:50054"),
                rag_v2_pb2.DESCRIPTOR.services_by_name["RagService"].full_name,
                timeout=0.5,
            )
        except grpc.RpcError:
            rag_ready = False
        print("RAG_V2=READY" if rag_ready else "RAG_V2=DEGRADED")
    if os.environ.get("WORLD_NEWS_RETENTION_ENABLED", "false").lower() == "true":
        running = Path("/tmp/decision-platform-retention-running").exists()
        print("WORLD_NEWS_RETENTION=RUNNING" if running else "WORLD_NEWS_RETENTION=DEGRADED")


if __name__ == "__main__":
    main()
