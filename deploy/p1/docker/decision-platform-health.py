"""통합 decision-platform의 Spring 및 Python worker health를 함께 확인한다."""

from __future__ import annotations

import json
import os
import urllib.request

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from app.generated import async_worker_pb2
from app.p1_owner.inference_grpc_server import SERVICE_NAME as RETURN_INFERENCE_SERVICE


def main() -> None:
    """제품 프로필이 실제로 시작한 runtime만 모두 준비되면 정상 종료한다."""

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
        channel = grpc.insecure_channel(target)
        try:
            result = health_pb2_grpc.HealthStub(channel).Check(
                health_pb2.HealthCheckRequest(service=service),
                timeout=2,
            )
            if result.status != health_pb2.HealthCheckResponse.SERVING:
                raise RuntimeError("Python runtime is not healthy")
        finally:
            channel.close()
    if os.environ.get("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "false").lower() == "true":
        brokerage = grpc.insecure_channel("127.0.0.1:50052")
        try:
            result = health_pb2_grpc.HealthStub(brokerage).Check(
                health_pb2.HealthCheckRequest(service=""),
                timeout=2,
            )
            if result.status != health_pb2.HealthCheckResponse.SERVING:
                raise RuntimeError("KIS Mock brokerage is not healthy")
        finally:
            brokerage.close()


if __name__ == "__main__":
    main()
