"""Render the public demo as deployed, then check its isolation boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/p1/compose.public-demo.yml"
DOCKER = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"


class PublicDemoComposeTest(unittest.TestCase):
    def test_rendered_demo_has_only_bounded_services_and_secrets(self) -> None:
        environment = {
            **os.environ,
            "MARS_DEMO_SECRET_GID": "1000",
            "MARS_DEMO_TAG": "test-source-api",
            "MARS_VERTEX_MODEL_ID": "test-model",
            "MARS_DEMO_SECRETS_DIR": "/tmp/mars-demo-contract-secrets",
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
            {"postgres", "redis", "role-bootstrap", "migrate", "actor-authority", "api", "web"},
        )
        self.assertEqual(services["api"]["environment"]["MARS_PUBLIC_SURFACE_MODE"], "DEMO")
        self.assertEqual(services["api"]["environment"]["KIS_MOCK_BROKERAGE_ONLINE_ENABLED"], "false")
        self.assertEqual(services["api"]["environment"]["P1_AUTOMATION_RUNTIME_ENABLED"], "false")
        self.assertEqual(services["api"]["environment"]["ASYNC_WORKER_ENABLED"], "false")
        self.assertEqual(services["api"]["environment"]["S4_9_STRONG_LLM_ENABLED"], "true")
        self.assertEqual(
            services["api"]["environment"]["CAPSTONE_RAG_LOCAL_ROOT"],
            "/opt/capstone/rag-runtime-default",
        )
        self.assertEqual(services["migrate"]["environment"]["MARS_PUBLIC_SURFACE_MODE"], "DEMO")
        self.assertEqual(
            {secret["source"] for secret in services["api"]["secrets"]},
            {"mars_public_demo_env", "rag_history_kek", "vertex_service_account", "actor_client_p12", "actor_tls_ca"},
        )
        for service in services.values():
            self.assertTrue(service["image"].startswith("pjjpjj111/mars-demo:"))
            self.assertFalse({"kis_mock_env", "automation_runtime_env", "bootstrap_env"} & {
                secret["source"] for secret in service.get("secrets", [])
            })
        for secret in config["secrets"].values():
            self.assertTrue(secret["file"].startswith("/tmp/mars-demo-contract-secrets/"))
        self.assertEqual(set(config["volumes"]), {"demo-postgres", "demo-redis"})


if __name__ == "__main__":
    unittest.main()
