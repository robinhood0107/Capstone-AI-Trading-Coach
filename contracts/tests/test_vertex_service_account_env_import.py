from __future__ import annotations

import base64
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/p1/import_vertex_service_account_to_env.py"
SPEC = importlib.util.spec_from_file_location("vertex_env_import", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VertexServiceAccountEnvImportTest(unittest.TestCase):
    def test_cli_imports_credential_and_model_without_printing_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / ".git").mkdir()
            env_file = repository / ".env"
            env_file.write_text("UNRELATED_SETTING=preserve\n")
            env_file.chmod(0o600)

            credential_path = repository / "credential.json"
            credential_bytes = json.dumps(
                {
                    "type": "service_account",
                    "project_id": "vertex-test-project",
                    "private_key_id": "test-key-id",
                    "private_key": "test-private-key",
                    "client_email": "vertex-test@example.iam.gserviceaccount.com",
                    "client_id": "test-client-id",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/test",
                    "universe_domain": "googleapis.com",
                }
            ).encode()
            credential_path.write_bytes(credential_bytes)
            credential_path.chmod(0o600)

            model_path = repository / "legacy-model.env"
            model_path.write_text('VERTEX_MODEL_ID="gemini-3.5-flash"\n')
            model_path.chmod(0o600)
            command = [
                sys.executable,
                str(SCRIPT),
                str(credential_path),
                "--env-file",
                str(env_file),
                "--model-env-file",
                str(model_path),
            ]

            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, "VERTEX_ENV_IMPORT=UPDATED\n")
            values = dict(
                line.partition("=")[::2]
                for line in env_file.read_text().splitlines()
                if "=" in line
            )
            self.assertEqual(
                base64.b64decode(values[MODULE.ENV_KEY]),
                credential_bytes,
            )
            self.assertEqual(values[MODULE.PROJECT_ENV_KEY], "vertex-test-project")
            self.assertEqual(values[MODULE.MODEL_ENV_KEY], "gemini-3.5-flash")
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

            repeated = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(repeated.stdout, "VERTEX_ENV_IMPORT=ALREADY_CURRENT\n")

    def test_import_is_idempotent_and_preserves_unrelated_root_settings(self) -> None:
        values = {
            MODULE.ENV_KEY: "TEST_BASE64_PLACEHOLDER",
            MODULE.PROJECT_ENV_KEY: "mars-test-project",
            MODULE.MODEL_ENV_KEY: "gemini-test-model",
        }
        original = b"UNRELATED_SETTING=preserve\n"
        updated, changed = MODULE.updated_env_bytes(original, values, replace=False)
        self.assertTrue(changed)
        self.assertTrue(updated.startswith(original))
        repeated, changed_again = MODULE.updated_env_bytes(updated, values, replace=False)
        self.assertFalse(changed_again)
        self.assertEqual(repeated, updated)

    def test_conflicting_values_require_explicit_rotation(self) -> None:
        original = f"{MODULE.ENV_KEY}=old-value\n".encode()
        replacement = {MODULE.ENV_KEY: "new-value"}
        with self.assertRaises(ValueError):
            MODULE.updated_env_bytes(original, replacement, replace=False)
        updated, changed = MODULE.updated_env_bytes(original, replacement, replace=True)
        self.assertTrue(changed)
        self.assertEqual(updated, f"{MODULE.ENV_KEY}=new-value\n".encode())

    def test_duplicate_root_keys_are_rejected(self) -> None:
        original = f"{MODULE.ENV_KEY}=one\n{MODULE.ENV_KEY}=two\n".encode()
        with self.assertRaises(ValueError):
            MODULE.updated_env_bytes(original, {MODULE.ENV_KEY: "three"}, replace=True)

    def test_legacy_model_value_is_migrated_without_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.env"
            path.write_text('OTHER=value\nVERTEX_MODEL_ID="gemini-3.5-flash"\n')
            path.chmod(0o600)
            self.assertEqual(MODULE.read_model_id(path), "gemini-3.5-flash")


if __name__ == "__main__":
    unittest.main()
