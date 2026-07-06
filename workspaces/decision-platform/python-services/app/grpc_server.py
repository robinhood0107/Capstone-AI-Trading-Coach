"""decision-platform Python gRPC 서버 진입점. S0.1 walking-skeleton 단계에서는 health check만 응답한다.

reflection을 켜두는 이유: grpcurl로 서비스 목록/호출을 즉시 테스트하기 위해서다(작업계획 5.11.4).
  grpcurl -plaintext localhost:50051 list
"""

from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

SERVICE_NAMES = (
    "grpc.health.v1.Health",
    reflection.SERVICE_NAME,
)


def serve() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    reflection.enable_server_reflection(SERVICE_NAMES, server)
    server.add_insecure_port("[::]:50051")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
