"""Render the Google-authenticated full product and check account isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/p1/compose.public-full.yml"
DOCKER = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"


class PublicFullComposeTest(unittest.TestCase):
    def test_full_stack_has_its_own_state_and_uses_owner_kis_envelopes(self) -> None:
        environment = {
            **os.environ,
            "MARS_FULL_SECRET_GID": "1000",
            "MARS_FULL_TAG": "candidate",
            "MARS_AI_DAILY_HARD_CAP_USD": "1.00",
            "MARS_FULL_SECRETS_DIR": "/tmp/mars-full-contract-secrets",
            "MARS_FULL_BROKERAGE_KEK_DIR": "/tmp/mars-full-contract-kek",
            "MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256": "a" * 64,
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
            {"postgres", "redis", "role-bootstrap", "migrate", "seed-import", "rag-runtime-seed", "actor-authority", "api", "web"},
        )
        api = services["api"]
        self.assertEqual(api["environment"]["MARS_PUBLIC_SURFACE_MODE"], "FULL")
        self.assertEqual(api["environment"]["SPRING_PROFILES_ACTIVE"], "mars-full")
        self.assertEqual(api["environment"]["BROKERAGE_GRPC_ENABLED"], "true")
        self.assertEqual(api["environment"]["RAG_V2_GRPC_ENABLED"], "true")
        self.assertEqual(api["environment"]["MARS_BROKERAGE_KEK_DIRECTORY"], "/run/brokerage-kek")
        self.assertEqual(services["migrate"]["environment"]["MARS_PUBLIC_SURFACE_MODE"], "FULL")
        self.assertEqual(
            {secret["source"] for secret in api["secrets"]},
            {"mars_public_full_env", "rag_history_kek", "vertex_service_account", "actor_client_p12", "actor_tls_ca"},
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
        self.assertIn("public-full) secret_files=/run/secrets/mars_public_full_env", secret_entrypoint)
        self.assertIn("KIS_MOCK_ORDER_REFERENCE_KEY) return 0", secret_entrypoint)
        self.assertIn("KIS_*|P1_AUTOMATION_*) return 1", secret_entrypoint)


if __name__ == "__main__":
    unittest.main()
