"""Render the public and Portainer DEMO templates and verify product isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCKER = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"


class PublicDemoComposeTest(unittest.TestCase):
    def config(self, compose: Path, environment: dict[str, str]) -> dict[str, object]:
        result = subprocess.run(
            [DOCKER, "compose", "-f", str(compose), "config", "--format", "json"],
            env={**os.environ, **environment},
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    @staticmethod
    def reference_names(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value)
        return {
            entry.get("source") if isinstance(entry, dict) else entry
            for entry in value
        }

    def test_public_demo_is_one_web_service_with_only_demo_resources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mars-demo-compose-") as directory:
            secrets_dir = Path(directory)
            for name in ("session-signing-key", "vertex-service-account.json"):
                (secrets_dir / name).write_text("test-only-placeholder", encoding="utf-8")
            config = self.config(
                ROOT / "deploy/p1/compose.public-demo.yml",
                {"MARS_DEMO_TAG": "v2.0.0", "MARS_DEMO_SECRETS_DIR": str(secrets_dir)},
            )
        self.assertEqual(set(config["services"]), {"web"})
        service = config["services"]["web"]
        self.assertEqual(service["image"], "pjjpjj111/mars-demo:v2.0.0-web")
        self.assertEqual(service["platform"], "linux/amd64")
        self.assertEqual(self.reference_names(service["secrets"]), {"session-signing-key", "vertex-service-account"})
        self.assertEqual(self.reference_names(service["volumes"]), {"demo-state"})
        self.assertEqual(self.reference_names(service["networks"]), {"demo-egress"})
        self.assertEqual(set(config["volumes"]), {"demo-state"})
        self.assertEqual(config["volumes"]["demo-state"]["name"], "mars-demo_state")
        self.assertEqual(set(config["networks"]), {"demo-egress"})
        self.assertEqual(config["networks"]["demo-egress"]["name"], "mars-demo_web")
        self.assertEqual(service["ports"][0]["host_ip"], "127.0.0.1")
        env = service["environment"]
        self.assertEqual(env["MARS_DEMO_AGENT_PER_SESSION_DAILY_LIMIT"], "5")
        self.assertEqual(env["MARS_DEMO_AGENT_GLOBAL_DAILY_LIMIT"], "50")
        self.assertEqual(env["MARS_DEMO_AGENT_MAX_CONCURRENT"], "1")
        for key in env:
            self.assertFalse(any(term in key.upper() for term in ("KIS", "PGDATA", "POSTGRES", "REDIS", "MARS_PUBLIC_SURFACE_MODE")))
        serialized = json.dumps(config).lower()
        for forbidden in ("mars-full", "mars-full_postgres-data", "brokerage-kek", "actor-client", "postgres", "redis"):
            self.assertNotIn(forbidden, serialized)

    def test_portainer_stack_is_digest_pinned_and_uses_separate_secret_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mars-demo-portainer-") as directory:
            secrets_dir = Path(directory)
            for name in ("session-signing-key", "vertex-service-account.json"):
                (secrets_dir / name).write_text("test-only-placeholder", encoding="utf-8")
            config = self.config(
                ROOT / "deploy/p1/portainer/mars-demo.stack.yml",
                {
                    "MARS_DEMO_IMAGE_DIGEST": "sha256:" + "a" * 64,
                    "MARS_DEMO_SECRETS_DIR": str(secrets_dir),
                },
            )
        self.assertEqual(set(config["services"]), {"web"})
        service = config["services"]["web"]
        self.assertEqual(service["image"], "pjjpjj111/mars-demo@sha256:" + "a" * 64)
        self.assertEqual(self.reference_names(service["secrets"]), {"session-signing-key", "vertex-service-account"})
        self.assertEqual(set(config["volumes"]), {"demo-state"})
        self.assertEqual(config["volumes"]["demo-state"]["name"], "mars-demo_state")


if __name__ == "__main__":
    unittest.main()
