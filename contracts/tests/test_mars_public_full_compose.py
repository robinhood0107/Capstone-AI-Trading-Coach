"""Render the Google-authenticated full product and check account isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/p1/compose.public-full.yml"
DOCKER = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"


class PublicFullComposeTest(unittest.TestCase):
    def test_full_stack_has_its_own_state_and_uses_owner_kis_envelopes(self) -> None:
        environment = {
            **os.environ,
            "MARS_FULL_SECRET_GID": "1000",
            "MARS_FULL_TAG": "candidate",
            "MARS_FULL_SECRETS_DIR": "/tmp/mars-full-contract-secrets",
            "MARS_FULL_BROKERAGE_KEK_DIR": "/tmp/mars-full-contract-kek",
            "MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256": "a" * 64,
            "MARS_VERTEX_SERVICE_ACCOUNT_SHA256": "b" * 64,
            "MARS_VERTEX_MODEL_ID": "test-model",
            "MARS_VERTEX_PROJECT_ID": "test-project",
        }
        result = subprocess.run(
            [DOCKER, "compose", "-f", str(COMPOSE), "config", "--format", "json"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        config = json.loads(result.stdout)
        services = config["services"]
        self.assertEqual(
            set(services),
            {
                "postgres", "redis", "role-bootstrap", "migrate", "seed-import",
                "rag-runtime-seed", "actor-authority", "market-data-daily",
                "world-news-minute", "disclosure-collector", "api", "web",
                "team-b-seed-import", "calendar-offline-seed", "rag-source-register",
            },
        )
        # 개인 스택이 up 마다 돌리는 일회성 적재를 FULL 도 평범한 up 에서 돌린다.
        one_shots = {
            "team-b-seed-import": ("artifact-import", "app.p1_owner.importer", "artifact_import_env"),
            "calendar-offline-seed": (
                "calendar-offline-seed",
                "app.data.calendar.offline_seed_cli",
                "calendar_offline_seed_env",
            ),
            "rag-source-register": (
                "rag-source-register",
                "app.rag.register_sources_cli --register-db --json",
                "rag_source_register_env",
            ),
            "disclosure-collector": (
                "disclosure-collector",
                "app.data.opendart.disclosure_event_collector_cli",
                "disclosure_collector_env",
            ),
        }
        for name, (role, module, secret) in one_shots.items():
            service = services[name]
            self.assertNotIn("profiles", service, name)
            self.assertEqual(service["restart"], "no", name)
            self.assertEqual(service["entrypoint"][-1], role, name)
            self.assertIn(module, " ".join(service["command"]), name)
            self.assertEqual({item["source"] for item in service["secrets"]}, {secret}, name)
            self.assertEqual(
                service["depends_on"]["migrate"]["condition"], "service_completed_successfully", name
            )
        self.assertEqual(services["team-b-seed-import"]["environment"]["P1_OPERATOR_UID"], "65532")
        self.assertIn("/opt/capstone/seed/team-b", services["team-b-seed-import"]["command"])
        self.assertIn("app.data.market_data.yfinance_daily_cli", " ".join(services["market-data-daily"]["command"]))
        self.assertIn("app.data.news.gdelt_collector_cli", " ".join(services["world-news-minute"]["command"]))
        self.assertEqual(services["world-news-minute"]["environment"]["GDELT_WORLD_NEWS_ENABLED"], "true")
        api = services["api"]
        self.assertEqual(
            api["depends_on"]["team-b-seed-import"]["condition"], "service_completed_successfully"
        )
        self.assertEqual(api["environment"]["MARS_PUBLIC_SURFACE_MODE"], "FULL")
        self.assertEqual(api["environment"]["SPRING_PROFILES_ACTIVE"], "mars-full")
        self.assertEqual(api["environment"]["BROKERAGE_GRPC_ENABLED"], "true")
        self.assertEqual(api["environment"]["P1_AUTOMATION_RUNTIME_ENABLED"], "true")
        self.assertEqual(api["environment"]["ASYNC_WORKER_ENABLED"], "true")
        self.assertEqual(api["environment"]["ASYNC_POLLING_ENABLED"], "true")
        self.assertEqual(api["environment"]["ASYNC_WORKER_GRPC_TARGET"], "127.0.0.1:50056")
        self.assertEqual(api["environment"]["WORLD_NEWS_RETENTION_ENABLED"], "true")
        self.assertEqual(api["environment"]["RETURN_INFERENCE_BUNDLE_ROOT"], "/opt/capstone/seed/team-b")
        self.assertEqual(api["environment"]["RETURN_INFERENCE_ALLOW_SYNTHETIC"], "false")
        full_profile = yaml.safe_load(
            (ROOT / "workspaces/decision-platform/spring-api/src/main/resources/application-mars-full.yml").read_text()
        )
        self.assertIs(full_profile["app"]["rag-v2"]["web"]["vertex-google-search"]["enabled"], False)
        self.assertEqual(api["environment"]["RAG_V2_GRPC_ENABLED"], "true")
        self.assertEqual(api["environment"]["RAG_V2_VERTEX_ENABLED"], "true")
        self.assertEqual(api["environment"]["RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED"], "true")
        self.assertEqual(
            services["rag-runtime-seed"]["environment"]["RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED"],
            "true",
        )
        self.assertEqual(services["rag-runtime-seed"]["user"], "0:0")
        self.assertEqual(services["rag-runtime-seed"]["cap_add"], ["CHOWN"])
        self.assertEqual(api["environment"]["MARS_BROKERAGE_KEK_DIRECTORY"], "/run/brokerage-kek")
        self.assertEqual(services["migrate"]["environment"]["MARS_PUBLIC_SURFACE_MODE"], "FULL")
        self.assertEqual(
            {secret["source"] for secret in api["secrets"]},
            {"mars_public_full_env", "rag_history_kek", "actor_client_p12", "actor_tls_ca"},
        )
        self.assertEqual(services["rag-runtime-seed"].get("secrets", []), [])
        self.assertEqual(
            services["rag-runtime-seed"]["environment"]["MARS_VERTEX_SERVICE_ACCOUNT_SHA256"],
            "b" * 64,
        )
        for service in services.values():
            self.assertTrue(service["image"].startswith("pjjpjj111/mars-full:"))
            self.assertFalse({"kis_mock_env", "automation_runtime_env", "mars_public_demo_env"} & {
                secret["source"] for secret in service.get("secrets", [])
            })
        self.assertEqual(set(config["volumes"]), {"full-postgres", "full-redis", "full-rag-runtime"})
        self.assertEqual(services["web"]["environment"]["NEXT_PUBLIC_MARS_PRODUCT"], "full")
        port = services["web"]["ports"][0]
        self.assertEqual(port["host_ip"], "127.0.0.1")
        self.assertEqual(port["published"], "3002")
        self.assertEqual(port["target"], 3000)
        for secret in config["secrets"].values():
            self.assertTrue(secret["file"].startswith("/tmp/mars-full-contract-secrets/"))
        secret_entrypoint = (ROOT / "deploy/p1/docker/secret-entrypoint.sh").read_text()
        self.assertIn(
            'public-full) secret_files=/run/secrets/mars_public_full_env',
            secret_entrypoint,
        )
        self.assertIn("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", secret_entrypoint)
        self.assertIn("KIS_MOCK_ORDER_REFERENCE_KEY) return 0", secret_entrypoint)
        self.assertIn("P1_AUTOMATION_DATABASE_DSN|AUTOMATION_RUNTIME_SHARED_SECRET) return 0", secret_entrypoint)
        self.assertIn("RETURN_INFERENCE_GRPC_SHARED_SECRET|ASYNC_WORKER_DATABASE_DSN) return 0", secret_entrypoint)
        self.assertIn("KIS_*|P1_AUTOMATION_*) return 1", secret_entrypoint)


if __name__ == "__main__":
    unittest.main()
