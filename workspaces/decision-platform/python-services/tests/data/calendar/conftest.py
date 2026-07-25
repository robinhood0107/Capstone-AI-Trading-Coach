"""Calendar tests가 공유하는 PostgreSQL fixture type의 호환 import 경계."""

from tests.conftest import PostgresTestCluster

__all__ = ["PostgresTestCluster"]
