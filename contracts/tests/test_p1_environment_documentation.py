from __future__ import annotations

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
P1CTL = ROOT / "deploy" / "p1" / "p1ctl"
ENTRYPOINT = ROOT / "deploy" / "p1" / "docker" / "secret-entrypoint.sh"
P1_COMPOSE = ROOT / "deploy" / "p1" / "compose.db.yml"
SOURCE_COMPOSE = ROOT / "infra" / "docker-compose.infra.yml"
APPLICATION = (
    ROOT
    / "workspaces"
    / "decision-platform"
    / "spring-api"
    / "src"
    / "main"
    / "resources"
    / "application.yml"
)
S8_RUNNER = ROOT / "workspaces" / "decision-platform" / "demo" / "s8" / "run-demo.sh"
S8_GENERATOR = (
    ROOT
    / "workspaces"
    / "decision-platform"
    / "spring-api"
    / "src"
    / "test"
    / "kotlin"
    / "com"
    / "capstone"
    / "decision"
    / "OpenApiFixtureEnvironmentWriter.kt"
)


def _readme_keys(name: str) -> set[str]:
    text = README.read_text(encoding="utf-8")
    match = re.search(
        rf"<!-- P1_{name}_KEYS_BEGIN -->\s*```text\n(.*?)\n```\s*"
        rf"<!-- P1_{name}_KEYS_END -->",
        text,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing README key block: {name}")
    return {line.split("=", 1)[0] for line in match.group(1).splitlines()}


def _required_profile_keys(entrypoint: str, profile: str) -> set[str]:
    match = re.search(
        rf"^    {profile}\) printf '%s\\n' '([^']+)' ;;&?$",
        entrypoint,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"missing required profile: {profile}")
    return set(match.group(1).split())


class P1EnvironmentDocumentationTest(unittest.TestCase):
    def test_readme_inventory_matches_p1ctl_and_secret_entrypoint(self) -> None:
        p1ctl = P1CTL.read_text(encoding="utf-8")
        entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
        runtime = {
            "P1_SECRETS_DIR",
            "P1_SECRET_GID",
            "P1_API_PORT",
            "P1_SPRING_IMAGE",
            "P1_PYTHON_IMAGE",
            "P1_POSTGRES_IMAGE",
        }
        release = {"P1_SPRING_IMAGE", "P1_PYTHON_IMAGE", "P1_POSTGRES_IMAGE"}
        self.assertEqual(_readme_keys("RUNTIME_ENV"), runtime)
        self.assertEqual(_readme_keys("RELEASE_ENV"), release)
        for key in runtime:
            self.assertIn(f"{key}=", p1ctl)
        for key in release:
            self.assertIn(key, p1ctl)

        for profile, documented_name in (("postgres", "POSTGRES"), ("role-bootstrap", "ROLE_BOOTSTRAP"), ("spring", "SPRING"), ("migration", "MIGRATION"), ("bootstrap", "BOOTSTRAP"), ("python", "PYTHON"), ("demo", "DEMO"), ("redis", "REDIS")):
            documented = _readme_keys(f"{documented_name}_ENV")
            required = _required_profile_keys(entrypoint, profile)
            allowed = set(re.findall(rf"(?<![A-Za-z0-9-]){re.escape(profile)}:([A-Z][A-Z0-9_]*)", entrypoint))
            self.assertEqual(documented, required, profile)
            self.assertEqual(allowed, required, profile)

    def test_env_example_covers_source_required_union(self) -> None:
        example_keys = {
            match.group(1)
            for match in re.finditer(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), re.MULTILINE)
        }
        compose_required = set(
            re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", SOURCE_COMPOSE.read_text(encoding="utf-8"))
        )
        spring_required = set(
            re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", APPLICATION.read_text(encoding="utf-8"))
        )
        python_required = {"ASYNC_WORKER_DATABASE_DSN", "ASYNC_WORKER_GRPC_BIND_ADDRESS"}
        host_port_keys = {"REDIS_HOST_PORT", "SEARXNG_HOST_PORT"}
        self.assertEqual((compose_required | spring_required | python_required | host_port_keys) - example_keys, set())

    def test_s8_runner_key_contract_matches_generator(self) -> None:
        runner = S8_RUNNER.read_text(encoding="utf-8")
        generator = S8_GENERATOR.read_text(encoding="utf-8")
        key_block = re.search(r"local keys=\(\n(.*?)\n  \)", runner, re.DOTALL)
        self.assertIsNotNone(key_block)
        runner_keys = set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", key_block.group(1)))
        generator_keys = set(re.findall(r'"([A-Z][A-Z0-9_]*)"\s+to\b', generator))
        self.assertEqual(runner_keys, generator_keys)

    def test_p1_compose_keeps_provider_account_and_order_authority_closed(self) -> None:
        compose = P1_COMPOSE.read_text(encoding="utf-8")
        expected_fixed = {
            'PROVIDER_LIVE_CALLS_ENABLED: "false"',
            "KIS_MODE: mock",
            'KIS_OFFLINE: "1"',
            'KIS_MOCK_BROKERAGE_ONLINE_ENABLED: "false"',
            'BROKERAGE_GRPC_ENABLED: "false"',
            'RAG_GRPC_ENABLED: "false"',
            'RAG_V2_GRPC_ENABLED: "false"',
            'RAG_V2_VERTEX_ENABLED: "false"',
            'RAG_WEB_ENABLED: "false"',
            'S4_9_STRONG_LLM_ENABLED: "false"',
            'S4_9_MCP_ENABLED: "false"',
            'FINANCIAL_ENGINEERING_GRPC_ENABLED: "false"',
        }
        for setting in expected_fixed:
            self.assertIn(setting, compose)
        for forbidden in (
            "KIS_MOCK_APP_KEY",
            "KIS_MOCK_APP_SECRET",
            "KIS_MOCK_ACCOUNT_NO",
            "KIS_LIVE_APP_KEY",
            "KIS_LIVE_APP_SECRET",
            "S3_KIS_MOCK_EXACT_APPROVAL",
        ):
            self.assertNotIn(forbidden, compose)

    def test_ghcr_docs_cover_all_three_digest_pinned_packages(self) -> None:
        documents = (
            README.read_text(encoding="utf-8"),
            (ROOT / "docs" / "decision-platform" / "P1_OFFLINE_DEMO_배포_및_검증.md").read_text(encoding="utf-8"),
        )
        packages = (
            "capstone-spring-api",
            "capstone-python-services",
            "capstone-postgres-pgvector",
        )
        for document in documents:
            self.assertIn('--password-stdin', document)
            self.assertNotIn("GHCR_READ_TOKEN=", document)
            for package in packages:
                self.assertIn(f"ghcr.io/robinhood0107/{package}@sha256:<DIGEST>", document)


if __name__ == "__main__":
    unittest.main()
