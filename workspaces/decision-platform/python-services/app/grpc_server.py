"""decision-platform Python gRPC 서버 진입점. S0.1 walking-skeleton 단계에서는 health check만 응답한다."""

from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2_grpc


def serve() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    server.add_insecure_port("[::]:50051")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
