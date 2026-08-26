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
    """두 runtime이 모두 준비된 경우에만 정상 종료한다."""

    with urllib.request.urlopen("http://127.0.0.1:8080/actuator/health", timeout=2) as response:
        body = json.load(response)
        if response.status != 200 or body.get("status") != "UP":
            raise RuntimeError("Spring API is not healthy")
    channel = grpc.insecure_channel("127.0.0.1:50056")
    try:
        service = async_worker_pb2.DESCRIPTOR.services_by_name["AsyncWorkerService"].full_name
        result = health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(service=service),
            timeout=2,
        )
        if result.status != health_pb2.HealthCheckResponse.SERVING:
            raise RuntimeError("Python worker is not healthy")
    finally:
        channel.close()
    inference = grpc.insecure_channel("127.0.0.1:50057")
    try:
        result = health_pb2_grpc.HealthStub(inference).Check(
            health_pb2.HealthCheckRequest(service=RETURN_INFERENCE_SERVICE),
            timeout=2,
        )
        if result.status != health_pb2.HealthCheckResponse.SERVING:
            raise RuntimeError("Return inference is not healthy")
    finally:
        inference.close()
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
