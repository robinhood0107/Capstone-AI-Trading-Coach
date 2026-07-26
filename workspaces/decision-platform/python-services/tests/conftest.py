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
    Path(__file__).resolve().parents[2]
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
    disclosure_reader_dsn: str
    app_dsn: str
    market_writer_dsn: str
    portfolio_writer_dsn: str
    risk_writer_dsn: str


@pytest.fixture(scope="session")
def postgres_cluster() -> Iterator[PostgresTestCluster]:
    """운영 PostgreSQL 이미지와 실제 migration/role 경계를 Python 통합 테스트 전체에 공유한다."""
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
        disclosure_reader_dsn = (
            f"postgresql://decision_disclosure_reader:disclosure-reader-test@{host}:{port}/decision"
        )
        app_dsn = f"postgresql://decision_app:app-test@{host}:{port}/decision"
        market_writer_dsn = (
            f"postgresql://decision_market_writer:market-writer-test@{host}:{port}/decision"
        )
        portfolio_writer_dsn = (
            f"postgresql://decision_portfolio_writer:portfolio-writer-test@{host}:{port}/decision"
        )
        risk_writer_dsn = (
            f"postgresql://decision_risk_writer:risk-writer-test@{host}:{port}/decision"
        )

        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(
                """
                CREATE ROLE decision_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'app-test';
                CREATE ROLE decision_collector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'collector-test';
                CREATE ROLE decision_disclosure_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'disclosure-reader-test';
                CREATE ROLE decision_market_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'market-writer-test';
                CREATE ROLE decision_portfolio_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'portfolio-writer-test';
                CREATE ROLE decision_risk_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'risk-writer-test';
                CREATE ROLE flyway LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD 'flyway-test';
                GRANT CONNECT ON DATABASE decision TO
                    decision_app,
                    decision_collector,
                    decision_disclosure_reader,
                    decision_market_writer,
                    decision_portfolio_writer,
                    decision_risk_writer,
                    flyway;
                REVOKE CREATE ON SCHEMA public FROM PUBLIC;
                GRANT USAGE ON SCHEMA public TO
                    decision_app,
                    decision_collector,
                    decision_disclosure_reader,
                    decision_market_writer,
                    decision_portfolio_writer,
                    decision_risk_writer,
                    flyway;
                GRANT CREATE ON SCHEMA public TO flyway;
                """
            )
            for migration in sorted(MIGRATION_DIR.glob("V*__*.sql"), key=_migration_version):
                if migration.name.startswith("V8__"):
                    # Java V7 migration은 Python SQL runner 대상이 아니므로 V8 전에 FK용 test identity만 모사한다.
                    connection.execute(
                        """
                        INSERT INTO users (user_id, username, role, password_hash)
                        VALUES
                          ('usr_demo_user', 'python-fixture-user', 'USER', 'test-only-hash'),
                          ('usr_demo_admin', 'python-fixture-admin', 'ADMIN', 'test-only-hash')
                        """
                    )
                connection.execute(migration.read_text(encoding="utf-8"))
                if migration.name.startswith("V4__"):
                    # Python test path도 V5/V6 protection SQL이 참조하는 Flyway history object만 모사한다.
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
            "disclosure_reader_dsn": disclosure_reader_dsn,
            "app_dsn": app_dsn,
            "market_writer_dsn": market_writer_dsn,
            "portfolio_writer_dsn": portfolio_writer_dsn,
            "risk_writer_dsn": risk_writer_dsn,
        }


def _migration_version(path: Path) -> int:
    return int(path.name.split("__", maxsplit=1)[0][1:])
