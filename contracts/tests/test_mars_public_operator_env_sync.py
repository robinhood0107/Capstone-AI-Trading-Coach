from __future__ import annotations

import base64
import importlib.util
import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/p1"))
SCRIPT = ROOT / "deploy/p1/sync_mars_public_operator_env.py"
SPEC = importlib.util.spec_from_file_location("sync_mars_public_operator_env", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def private_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.write_bytes(data)
    path.chmod(mode)


class MarsPublicOperatorEnvSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".git").mkdir()
        self.env = self.root / ".env"
        self.secrets = self.root / "full-secrets"
        self.secrets.mkdir(mode=0o700)
        identity = {"type": "service_account", "project_id": "mars-test-project"}
        service_account = base64.b64encode(json.dumps(identity).encode()).decode()
        self.values = {
            "MARS_VERTEX_MODEL_ID": "gemini-test-model",
            "MARS_VERTEX_PROJECT_ID": "mars-test-project",
            "MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64": service_account,
            "GOOGLE_OIDC_CLIENT_ID": "example-client-id",
            "GOOGLE_OIDC_CLIENT_SECRET": "example-google-secret",
            "KAKAO_OAUTH_CLIENT_ID": "example-kakao-id",
            "KAKAO_OAUTH_CLIENT_SECRET": "example-kakao-secret",
            "VOYAGE_API_KEY": "example-voyage-key",
            "OPENDART_API_KEY": "example-opendart-key",
            "OPENDART_DAILY_CALL_LIMIT": "20000",
            "OPENDART_DAILY_CALL_BUDGET": "2000",
            "OPENDART_MAX_CALLS_PER_RUN": "600",
            "OPENDART_MAX_SYMBOLS_PER_RUN": "31",
            "MARS_FULL_PORT": "3302",
        }
        private_file(self.env, "".join(f"{key}={value}\n" for key, value in self.values.items()).encode())
        self.runtime_values = {
            **{
                key: value
                for key, value in self.values.items()
                if key in MODULE.RUNTIME_SECRET_KEYS
            },
            "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256": secrets.token_hex(32),
            "JWT_SECRET": secrets.token_hex(32),
        }
        private_file(
            self.secrets / "mars-public-full.env",
            "".join(f"{key}={value}\n" for key, value in self.runtime_values.items()).encode(),
            0o640,
        )
        private_file(
            self.secrets / "disclosure-collector.env",
            b"P1_DISCLOSURE_COLLECTOR_DSN=postgresql://collector:example@postgres:5432/capstone_p1?sslmode=disable\n",
            0o640,
        )
        private_file(
            self.root / "full.env",
            (
                "MARS_FULL_SECRET_GID=1000\n"
                "MARS_FULL_SECRETS_DIR=./full-secrets\n"
                "MARS_FULL_BROKERAGE_KEK_DIR=./full-kek\n"
                f"MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256={secrets.token_hex(32)}\n"
                "MARS_VERTEX_MODEL_ID=old-model\n"
                "MARS_VERTEX_PROJECT_ID=old-project\n"
                f"MARS_VERTEX_SERVICE_ACCOUNT_SHA256={secrets.token_hex(32)}\n"
                "MARS_FULL_PORT=3002\n"
            ).encode(),
        )

    def test_updates_provider_settings_and_preserves_runtime_secrets(self) -> None:
        self.values["GOOGLE_OIDC_CLIENT_SECRET"] = "rotated-google-secret"
        self.env.write_text("".join(f"{key}={value}\n" for key, value in self.values.items()))
        self.env.chmod(0o600)

        self.assertTrue(MODULE.sync_operator_env("full", self.env, self.secrets))
        output = (self.secrets / "mars-public-full.env").read_text()
        self.assertIn("GOOGLE_OIDC_CLIENT_SECRET=rotated-google-secret\n", output)
        self.assertIn(f"GOOGLE_OIDC_ADMIN_SUBJECT_SHA256={self.runtime_values['GOOGLE_OIDC_ADMIN_SUBJECT_SHA256']}\n", output)
        self.assertIn(f"JWT_SECRET={self.runtime_values['JWT_SECRET']}\n", output)
        self.assertIn("OPENDART_API_KEY=example-opendart-key\n", (self.secrets / "disclosure-collector.env").read_text())
        compose = (self.root / "full.env").read_text()
        self.assertIn("MARS_VERTEX_PROJECT_ID=mars-test-project\n", compose)
        self.assertIn("OPENDART_DAILY_CALL_BUDGET=2000\n", compose)
        self.assertIn("MARS_FULL_PORT=3302\n", compose)
        self.assertEqual((self.secrets / "mars-public-full.env").stat().st_mode & 0o777, 0o640)
        self.assertFalse(MODULE.sync_operator_env("full", self.env, self.secrets))

    def test_updates_admin_hash_only_after_it_is_present_in_root_env(self) -> None:
        admin_hash = secrets.token_hex(32)
        with self.env.open("a") as operator_env:
            operator_env.write(f"GOOGLE_OIDC_ADMIN_SUBJECT_SHA256={admin_hash}\n")
        self.env.chmod(0o600)

        self.assertTrue(MODULE.sync_operator_env("full", self.env, self.secrets))
        output = (self.secrets / "mars-public-full.env").read_text()
        self.assertIn(f"GOOGLE_OIDC_ADMIN_SUBJECT_SHA256={admin_hash}\n", output)
        self.assertIn(f"JWT_SECRET={self.runtime_values['JWT_SECRET']}\n", output)

    def test_rejects_invalid_admin_hash_without_mutating_secret_file(self) -> None:
        with self.env.open("a") as operator_env:
            operator_env.write("GOOGLE_OIDC_ADMIN_SUBJECT_SHA256=not-a-hash\n")
        self.env.chmod(0o600)
        target = self.secrets / "mars-public-full.env"
        original = target.read_bytes()

        with self.assertRaisesRegex(ValueError, "admin subject digest"):
            MODULE.sync_operator_env("full", self.env, self.secrets)
        self.assertEqual(target.read_bytes(), original)

    def test_rejects_privileged_host_port_without_mutating_generated_files(self) -> None:
        self.values["MARS_FULL_PORT"] = "443"
        self.env.write_text("".join(f"{key}={value}\n" for key, value in self.values.items()))
        self.env.chmod(0o600)
        target = self.secrets / "mars-public-full.env"
        original = target.read_bytes()

        with self.assertRaisesRegex(ValueError, "host port"):
            MODULE.sync_operator_env("full", self.env, self.secrets)
        self.assertEqual(target.read_bytes(), original)

    def test_removing_optional_dart_settings_from_root_env_disables_them(self) -> None:
        lines = [
            line
            for line in self.env.read_text().splitlines()
            if not line.startswith("OPENDART_")
        ]
        self.env.write_text("\n".join(lines) + "\n")
        self.env.chmod(0o600)

        self.assertTrue(MODULE.sync_operator_env("full", self.env, self.secrets))
        self.assertNotIn("OPENDART_API_KEY=", (self.secrets / "disclosure-collector.env").read_text())
        compose = (self.root / "full.env").read_text()
        self.assertNotIn("OPENDART_DAILY_CALL_BUDGET=", compose)
        self.assertIn("MARS_FULL_PORT=3302\n", compose)
        self.assertFalse(MODULE.sync_operator_env("full", self.env, self.secrets))


if __name__ == "__main__":
    unittest.main()
