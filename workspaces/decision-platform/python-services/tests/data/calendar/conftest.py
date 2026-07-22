from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

POSTGRES_IMAGE = (
    "pgvector/pgvector:pg16@sha256:"
    "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)
MIGRATION_DIR = (
    Path(__file__).resolve().parents[4]
    / "spring-api"
    / "src"
    / "main"
    / "resources"
    / "db"
    / "migration"
)


class PostgresTestCluster(TypedDict):
    admin_dsn: str
    collector_dsn: str
    app_dsn: str


@pytest.fixture(scope="session")
def postgres_cluster() -> Iterator[PostgresTestCluster]:
    """운영과 같은 PostgreSQL 16 이미지에 실제 Flyway SQL과 선행 role bootstrap을 적용한다."""
    container = PostgresContainer(
        image=POSTGRES_IMAGE,
        username="decision",
        password="decision",
        dbname="decision",
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        admin_dsn = f"postgresql://decision:decision@{host}:{port}/decision"
        collector_dsn = f"postgresql://decision_collector:collector-test@{host}:{port}/decision"
        app_dsn = f"postgresql://decision_app:app-test@{host}:{port}/decision"

        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                """
                CREATE ROLE decision_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'app-test';
                CREATE ROLE decision_collector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'collector-test';
                GRANT CONNECT ON DATABASE decision TO decision_app, decision_collector;
                REVOKE CREATE ON SCHEMA public FROM PUBLIC;
                GRANT USAGE ON SCHEMA public TO decision_app, decision_collector;
                """
            )
            for migration in sorted(MIGRATION_DIR.glob("V*__*.sql"), key=_migration_version):
                connection.execute(migration.read_text(encoding="utf-8"))
                if migration.name.startswith("V4__"):
                    # Python 통합 테스트도 V5/V6의 실제 권한 SQL을 실행할 수 있게 Flyway 소유 객체만 모사한다.
                    connection.execute(
                        """
                        CREATE TABLE flyway_schema_history (
                            installed_rank integer PRIMARY KEY,
                            version text,
                            success boolean NOT NULL DEFAULT true
                        )
                        """
                    )

        yield {
            "admin_dsn": admin_dsn,
            "collector_dsn": collector_dsn,
            "app_dsn": app_dsn,
        }


def _migration_version(path: Path) -> int:
    return int(path.name.split("__", maxsplit=1)[0][1:])
