from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/p1/assemble_mars_public_secrets.py"
SPEC = importlib.util.spec_from_file_location("assemble_mars_public_secrets", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def private_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def env_lines(keys: tuple[str, ...] | list[str]) -> bytes:
    return "".join(f"{key}={secrets.token_hex(16)}\n" for key in keys).encode()


class MarsPublicSecretAssembleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".git").mkdir()
        self.release = self.root / "release"
        self.release.mkdir(mode=0o700)
        for name in (
            "mars-images.json",
            "mars-public-demo.compose.yml",
            "mars-public-full.compose.yml",
        ):
            (self.release / name).write_text("{}")
        self.vertex = self.root / "vertex.json"
        private_file(
            self.vertex,
            json.dumps({"type": "service_account", "project_id": "test-project"}).encode(),
        )
        self.vertex_b64 = base64.b64encode(self.vertex.read_bytes()).decode("ascii")
        self.demo_operator = self.root / "demo-operator.env"
        private_file(
            self.demo_operator,
            f"MARS_VERTEX_MODEL_ID=test-model\nMARS_VERTEX_SERVICE_ACCOUNT_JSON_B64={self.vertex_b64}\n".encode(),
        )
        self.full_operator = self.root / "full-operator.env"
        private_file(
            self.full_operator,
            (
                f"MARS_VERTEX_MODEL_ID=test-model\nMARS_VERTEX_SERVICE_ACCOUNT_JSON_B64={self.vertex_b64}\n"
                "MARS_VERTEX_PROJECT_ID=test-project\n"
                "GOOGLE_OIDC_CLIENT_ID=example-client\n"
                "GOOGLE_OIDC_CLIENT_SECRET=example-secret\n"
                "KAKAO_OAUTH_CLIENT_ID=example-kakao-client\n"
                "KAKAO_OAUTH_CLIENT_SECRET=example-kakao-secret\n"
                "VOYAGE_API_KEY=example-voyage\n"
            ).encode(),
        )

    def base(self, name: str) -> Path:
        root = self.root / name
        root.mkdir(mode=0o700)
        all_files = set(MODULE.COMMON_FILES) | set(MODULE.FULL_FILES)
        for filename in all_files:
            private_file(root / filename, b"TEST_VALUE=generated-at-test-time\n")
        private_file(
            root / "spring.env",
            env_lines(list(MODULE.SPRING_KEYS) + ["BROKERAGE_GRPC_SHARED_SECRET"]),
        )
        for filename in set(MODULE.FULL_FROM_BASE.values()) - {"spring.env"}:
            keys = [key for key, source in MODULE.FULL_FROM_BASE.items() if source == filename]
            private_file(root / filename, env_lines(keys))
        private_file(
            root / "postgres.env",
            b"POSTGRES_COLLECTOR_PASSWORD=" + secrets.token_hex(16).encode() + b"\n",
        )
        return root

    def assemble(self, product: str, base: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--product",
                product,
                "--base-secrets",
                str(base),
                "--release-dir",
                str(self.release),
                "--operator-env",
                str(self.demo_operator if product == "demo" else self.full_operator),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_both_products_get_distinct_complete_secret_sets(self) -> None:
        self.assertEqual(self.assemble("demo", self.base("demo-base")).returncode, 0)
        self.assertEqual(self.assemble("full", self.base("full-base")).returncode, 0)
        demo_dir = self.release / "demo-secrets"
        full_dir = self.release / "full-secrets"
        self.assertEqual(
            {p.name for p in demo_dir.iterdir()},
            set(MODULE.COMMON_FILES) | {"mars-public-demo.env"},
        )
        self.assertEqual(
            {p.name for p in full_dir.iterdir()},
            set(MODULE.COMMON_FILES)
            | set(MODULE.FULL_FILES)
            | {
                "disclosure-collector.env",
                "mars-public-full.env",
            },
        )
        demo_keys = set(MODULE.env_file(demo_dir / "mars-public-demo.env"))
        full_keys = set(MODULE.env_file(full_dir / "mars-public-full.env"))
        self.assertEqual(
            demo_keys,
            set(MODULE.SPRING_KEYS) | {"STRONG_LLM_GRPC_SHARED_SECRET", MODULE.VERTEX_ACCOUNT_B64},
        )
        self.assertEqual(
            full_keys,
            demo_keys
            | set(MODULE.FULL_FROM_BASE)
            | {
                "GOOGLE_OIDC_CLIENT_ID",
                "GOOGLE_OIDC_CLIENT_SECRET",
                "KAKAO_OAUTH_CLIENT_ID",
                "KAKAO_OAUTH_CLIENT_SECRET",
                "VOYAGE_API_KEY",
                "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256",
            },
        )
        self.assertEqual(
            (self.release / "full.env").stat().st_mode & 0o777,
            0o600,
        )
        compose_values = MODULE.env_file(self.release / "full.env")
        self.assertEqual(
            compose_values["MARS_VERTEX_SERVICE_ACCOUNT_SHA256"],
            hashlib.sha256(self.vertex.read_bytes()).hexdigest(),
        )
        self.assertEqual((full_dir / "mars-public-full.env").stat().st_mode & 0o777, 0o640)
        self.assertEqual(
            (self.release / "full-kek/brokerage-kek-v1.key").stat().st_mode & 0o777, 0o600
        )
        self.assertEqual((self.release / "full-kek/brokerage-kek-v1.key").stat().st_size, 32)
        self.assertNotEqual(
            (demo_dir / "postgres.env").read_bytes(),
            (full_dir / "postgres.env").read_bytes(),
        )

    def test_refuses_to_reuse_one_base_bundle_or_overwrite_secrets(self) -> None:
        base = self.base("shared-base")
        self.assertEqual(self.assemble("demo", base).returncode, 0)
        before = (self.release / "demo-secrets/postgres.env").read_bytes()
        self.assertNotEqual(self.assemble("demo", base).returncode, 0)
        self.assertNotEqual(self.assemble("full", base).returncode, 0)
        self.assertEqual((self.release / "demo-secrets/postgres.env").read_bytes(), before)

    def test_rejects_world_readable_provider_input(self) -> None:
        self.full_operator.chmod(0o644)
        self.assertNotEqual(self.assemble("full", self.base("full-base")).returncode, 0)
        self.assertFalse((self.release / "full-secrets").exists())

    def test_root_env_operator_source_filters_unrelated_credentials(self) -> None:
        root_env = self.root / ".env"
        private_file(
            root_env,
            (
                f"MARS_VERTEX_MODEL_ID=test-model\nMARS_VERTEX_SERVICE_ACCOUNT_JSON_B64={self.vertex_b64}\n"
                "KIS_LIVE_APP_SECRET=must-never-enter-public-bundle\n"
                "OPENAI_API_KEY=must-never-enter-public-bundle\n"
                "unrelated legacy line without an env assignment\n"
            ).encode(),
        )
        previous_root_env = MODULE.ROOT_ENV
        MODULE.ROOT_ENV = root_env
        try:
            selected = MODULE.operator_values(root_env, "demo")
        finally:
            MODULE.ROOT_ENV = previous_root_env
        self.assertEqual(set(selected), MODULE.EXTERNAL_DEMO)
        self.assertNotIn("KIS_LIVE_APP_SECRET", selected)
        self.assertNotIn("OPENAI_API_KEY", selected)

    def test_root_env_rejects_duplicate_allowlisted_settings(self) -> None:
        root_env = self.root / ".env"
        private_file(
            root_env,
            (
                f"MARS_VERTEX_MODEL_ID=test-model\nMARS_VERTEX_MODEL_ID=other-model\n"
                f"MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64={self.vertex_b64}\n"
            ).encode(),
        )
        previous_root_env = MODULE.ROOT_ENV
        MODULE.ROOT_ENV = root_env
        try:
            with self.assertRaises(ValueError):
                MODULE.operator_values(root_env, "demo")
        finally:
            MODULE.ROOT_ENV = previous_root_env

    def test_root_env_rejects_duplicate_blank_optional_settings(self) -> None:
        root_env = self.root / ".env"
        full_values = MODULE.env_file(self.full_operator)
        private_file(
            root_env,
            (
                "".join(f"{key}={value}\n" for key, value in full_values.items())
                + "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256=\n"
                + "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256=\n"
            ).encode(),
        )
        previous_root_env = MODULE.ROOT_ENV
        MODULE.ROOT_ENV = root_env
        try:
            with self.assertRaises(ValueError):
                MODULE.operator_values(root_env, "full")
        finally:
            MODULE.ROOT_ENV = previous_root_env

    def test_root_env_generates_dart_settings_in_scoped_outputs(self) -> None:
        root_env = self.root / ".env"
        full_values = MODULE.env_file(self.full_operator)
        private_file(
            root_env,
            (
                "".join(f"{key}={value}\n" for key, value in full_values.items())
                + "OPENDART_API_KEY=example-dart-key\n"
                + "OPENDART_DAILY_CALL_LIMIT=20000\n"
                + "OPENDART_DAILY_CALL_BUDGET=2000\n"
                + "OPENDART_MAX_CALLS_PER_RUN=600\n"
                + "OPENDART_MAX_SYMBOLS_PER_RUN=31\n"
                + "MARS_FULL_PORT=3302\n"
                + "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256=\n"
            ).encode(),
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--product",
                "full",
                "--base-secrets",
                str(self.base("root-full-base")),
                "--release-dir",
                str(self.release),
                "--operator-env",
                str(root_env),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        disclosure = MODULE.env_file(self.release / "full-secrets/disclosure-collector.env")
        self.assertEqual(disclosure["OPENDART_API_KEY"], "example-dart-key")
        compose = MODULE.env_file(self.release / "full.env")
        self.assertEqual(compose["OPENDART_DAILY_CALL_BUDGET"], "2000")
        self.assertEqual(compose["OPENDART_MAX_SYMBOLS_PER_RUN"], "31")
        self.assertEqual(compose["MARS_FULL_PORT"], "3302")
        full = MODULE.env_file(self.release / "full-secrets/mars-public-full.env")
        self.assertRegex(full["GOOGLE_OIDC_ADMIN_SUBJECT_SHA256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
