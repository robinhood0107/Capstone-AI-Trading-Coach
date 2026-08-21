from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from contracts.generate_principle_contracts import (
    CATALOG_PATH,
    EXPECTED_LEGACY_EVIDENCE_INFERENCE,
    OUTPUTS,
    ContractValidationError,
    canonical_json_bytes,
    generate_outputs,
    load_catalog,
    load_json_bytes_strict,
    validate_catalog_semantics,
)
from contracts.generate_s2_3_contracts import (
    CATALOG_PATH as S23_CATALOG_PATH,
)
from contracts.normalize_openapi import (
    S32_CATALOG_PATH,
    OpenApiNormalizationError,
    check_normalized_openapi,
    normalize_generated_openapi,
)
from contracts.openapi_env import OpenApiEnvironmentError, parse_openapi_environment
from contracts.run_openapi_gate import (
    OpenApiGateError,
    _explicit_process_environment,
    run_gate,
)


class StrictContractJsonTest(unittest.TestCase):
    def test_duplicate_keys_and_non_finite_constants_are_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            load_json_bytes_strict(b'{"ruleId":"one","ruleId":"two"}', source="duplicate")
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(token=token):
                with self.assertRaises(ContractValidationError):
                    load_json_bytes_strict(b'{"threshold":' + token + b"}", source="constant")

    def test_decimal_spellings_have_one_canonical_byte_representation(self) -> None:
        variants = (
            load_json_bytes_strict(b'{"threshold":0.1500}', source="fixed"),
            load_json_bytes_strict(b'{"threshold":0.15}', source="short"),
            load_json_bytes_strict(b'{"threshold":1.5e-1}', source="exponent"),
        )

        self.assertEqual(
            [b'{\n  "threshold": 0.15\n}\n'] * 3,
            [canonical_json_bytes(value) for value in variants],
        )

    def test_canonical_writer_sorts_objects_but_preserves_array_order(self) -> None:
        value = {"z": [3, 2, 1], "a": {"b": Decimal("2.00"), "a": 1}}

        self.assertEqual(
            b'{\n  "a": {\n    "a": 1,\n    "b": 2\n  },\n  "z": [\n    3,\n    2,\n    1\n  ]\n}\n',
            canonical_json_bytes(value),
        )


class PrincipleCatalogGenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_catalog_and_generated_artifacts_are_deterministic(self) -> None:
        first = generate_outputs(copy.deepcopy(self.catalog))
        second = generate_outputs(copy.deepcopy(self.catalog))

        self.assertEqual(first, second)
        self.assertEqual(
            frozenset(
                {
                "contracts/schemas/s2-1-principle-catalog.schema.json",
                "contracts/schemas/principle-rule.schema.json",
                "contracts/schemas/principle.schema.json",
                "contracts/schemas/principle-preset-list.schema.json",
                "contracts/schemas/principle-create-request.schema.json",
                "contracts/schemas/principle-update-request.schema.json",
                "contracts/schemas/principle-list-response.schema.json",
                "contracts/schemas/principle-history-response.schema.json",
                "contracts/schemas/principle-error.schema.json",
                "contracts/examples/principle.valid.json",
                "contracts/examples/principle-presets.valid.json",
                "contracts/examples/principle-create.valid.json",
                "contracts/examples/principle-create-custom-rules.valid.json",
                "contracts/examples/principle-update.valid.json",
                "contracts/examples/principle-update-no-op.valid.json",
                "contracts/examples/principle-list.valid.json",
                "contracts/examples/principle-list-next-page.valid.json",
                "contracts/examples/principle-list-empty.valid.json",
                "contracts/examples/principle-history.valid.json",
                "contracts/examples/principle-history-next-page.valid.json",
                "contracts/examples/principle-history-empty.valid.json",
                "contracts/examples/principle-error-validation.valid.json",
                "contracts/examples/principle-error-cursor.valid.json",
                "contracts/examples/principle-error-unauthorized.valid.json",
                "contracts/examples/principle-error-forbidden.valid.json",
                "contracts/examples/principle-error-not-found.valid.json",
                "contracts/examples/principle-error-conflict.valid.json",
                "contracts/examples/principle-error-version-exhausted.valid.json",
                "contracts/examples/principle-error-payload-too-large.valid.json",
                "contracts/examples/invalid/principle.invalid.json",
                "contracts/examples/invalid/principle.duplicate-rule.invalid.json",
                "contracts/examples/invalid/principle.invalid-tuple.invalid.json",
                "contracts/examples/invalid/principle.threshold-range.invalid.json",
                "contracts/examples/invalid/principle.threshold-scale.invalid.json",
                "contracts/examples/invalid/principle.threshold-null.invalid.json",
                "contracts/examples/invalid/principle.threshold-string.invalid.json",
                "contracts/examples/invalid/principle.unknown-property.invalid.json",
                "contracts/examples/invalid/principle.enabled-allow.invalid.json",
                "contracts/examples/invalid/principle.disabled-block.invalid.json",
                "contracts/examples/invalid/principle.evidence-missing.invalid.json",
                "contracts/examples/invalid/principle.evidence-optional-hard.invalid.json",
                "contracts/examples/invalid/principle.too-many-rules.invalid.json",
                "contracts/examples/invalid/principle-update.empty-rules.invalid.json",
                "contracts/examples/invalid/principle-update.invalid-status.invalid.json",
                "contracts/examples/invalid/principle-update.missing-field.invalid.json",
                "contracts/examples/invalid/principle-create.missing-title.invalid.json",
                },
            ),
            OUTPUTS,
        )
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

    def test_catalog_semantics_reject_tuple_duplicate_scale_and_matrix_drift(self) -> None:
        mutations = []

        invalid_tuple = copy.deepcopy(self.catalog)
        invalid_tuple["ruleDefinitions"][0]["metric"] = "attacker_controlled_metric"
        mutations.append(invalid_tuple)

        duplicate_rule = copy.deepcopy(self.catalog)
        duplicate_rule["ruleDefinitions"][1]["ruleId"] = duplicate_rule["ruleDefinitions"][0]["ruleId"]
        mutations.append(duplicate_rule)

        invalid_scale = copy.deepcopy(self.catalog)
        invalid_scale["presets"][0]["defaultRules"][0]["threshold"] = Decimal("0.12345")
        mutations.append(invalid_scale)

        invalid_cross_field = copy.deepcopy(self.catalog)
        invalid_cross_field["presets"][0]["defaultRules"][0]["severity"] = "ALLOW"
        mutations.append(invalid_cross_field)

        reordered_matrix = copy.deepcopy(self.catalog)
        reordered_matrix["presets"][0]["defaultRules"].reverse()
        mutations.append(reordered_matrix)

        hard_optional = copy.deepcopy(self.catalog)
        hard_optional["presets"][0]["defaultRules"][0]["evidenceRequirement"] = "OPTIONAL"
        mutations.append(hard_optional)

        for mutation in mutations:
            with self.subTest(mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()):
                with self.assertRaises(ContractValidationError):
                    validate_catalog_semantics(mutation)

    def test_legacy_evidence_inference_is_versioned_and_fail_closed(self) -> None:
        self.assertEqual(
            EXPECTED_LEGACY_EVIDENCE_INFERENCE,
            self.catalog["legacyEvidenceInference"],
        )
        generated = generate_outputs(self.catalog)
        schema = load_json_bytes_strict(
            generated["contracts/schemas/s2-1-principle-catalog.schema.json"],
            source="generated S2.1 catalog schema",
        )
        self.assertIn("legacyEvidenceInference", schema["required"])
        policy_schema = schema["properties"]["legacyEvidenceInference"]
        self.assertFalse(policy_schema["additionalProperties"])
        self.assertEqual(
            sorted(EXPECTED_LEGACY_EVIDENCE_INFERENCE),
            policy_schema["required"],
        )

        invalid_values = {
            "disabledMissingField": "REQUIRED",
            "enabledMissingField": "OPTIONAL",
            "policyVersion": "s2-1-legacy-evidence-inference/v2",
            "rewriteHistoricalRows": True,
            "unknownTuple": "INFER",
        }
        for field, value in invalid_values.items():
            mutation = copy.deepcopy(self.catalog)
            mutation["legacyEvidenceInference"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ContractValidationError):
                    validate_catalog_semantics(mutation)


class OpenApiEnvironmentParserTest(unittest.TestCase):
    def test_safe_current_auth_bundle_environment_is_accepted(self) -> None:
        with self.environment_file(self.valid_environment_text()) as path:
            parsed = parse_openapi_environment(path)

        self.assertEqual("55432", parsed["POSTGRES_HOST_PORT"])
        self.assertEqual("55432", parsed["POSTGRES_PORT"])
        for name in (
            "POSTGRES_DISCLOSURE_READER_PASSWORD",
            "POSTGRES_MARKET_WRITER_PASSWORD",
            "POSTGRES_PORTFOLIO_WRITER_PASSWORD",
            "POSTGRES_RISK_WRITER_PASSWORD",
            "POSTGRES_FILL_WRITER_PASSWORD",
            "POSTGRES_RAG_WRITER_PASSWORD",
            "POSTGRES_RAG_ADMIN_PASSWORD",
            "POSTGRES_RAG_QUERY_PASSWORD",
            "POSTGRES_SIGNAL_WRITER_PASSWORD",
            "POSTGRES_SIGNAL_SCHEDULER_PASSWORD",
            "POSTGRES_SIGNAL_ADMIN_PASSWORD",
            "POSTGRES_WORKER_PASSWORD",
            "KAFKA_UI_PASSWORD",
            "ASYNC_CURSOR_HMAC_KEY",
            "ASYNC_PARTITION_HMAC_KEY",
            "ASYNC_WORKER_GRPC_SHARED_SECRET",
            "SEARXNG_SECRET",
            "MCP_SEARXNG_AUTH_TOKEN",
            "DECISION_GRPC_SHARED_SECRET",
            "RAG_GRPC_SHARED_SECRET",
            "PYTHON_GRPC_SHARED_SECRET",
            "DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY",
            "BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY",
            "BROKERAGE_DB_CAPABILITY_TOKEN",
            "BROKERAGE_DB_CAPABILITY_TOKEN_SHA256",
            "RAG_HISTORY_SECRET_DIRECTORY",
            "RAG_HISTORY_CURRENT_KEK_VERSION",
            "RAG_IDEMPOTENCY_SCOPE_HMAC_KEY",
            "RAG_REQUEST_FINGERPRINT_HMAC_KEY",
            "RAG_PROVIDER_USAGE_HMAC_KEY",
            "RAG_RATE_LIMIT_HMAC_KEY",
            "RAG_HISTORY_CURSOR_HMAC_KEY",
        ):
            self.assertIn(name, parsed)
        self.assertEqual(
            hashlib.sha256(parsed["BROKERAGE_DB_CAPABILITY_TOKEN"].encode("utf-8")).hexdigest(),
            parsed["BROKERAGE_DB_CAPABILITY_TOKEN_SHA256"],
        )
        self.assertEqual(
            parsed["DECISION_GRPC_SHARED_SECRET"],
            parsed["PYTHON_GRPC_SHARED_SECRET"],
        )
        self.assertNotEqual(
            parsed["RAG_GRPC_SHARED_SECRET"],
            parsed["DECISION_GRPC_SHARED_SECRET"],
        )
        self.assertTrue(parsed["DEMO_USER_CREDENTIAL_BUNDLE"].startswith("s21-v1:usr_demo_user:"))
        self.assertNotIn("KIS_MODE", parsed)

    def test_unsafe_or_ambiguous_environment_inputs_are_rejected(self) -> None:
        valid = self.valid_environment_text()
        cases = {
            "unquoted": valid.replace("POSTGRES_DB='trading'", "POSTGRES_DB=trading"),
            "duplicate": valid + "POSTGRES_DB='trading'\n",
            "unknown": valid + "KIS_MODE='live'\n",
            "interpolation": valid.replace(
                "JWT_ISSUER='s21-openapi-local'",
                "JWT_ISSUER='$(touch-should-never-run)'",
            ),
            "single-quote": valid.replace(
                "JWT_AUDIENCE='s21-openapi-client'",
                "JWT_AUDIENCE='unsafe'quote'",
            ),
            "port-mismatch": valid.replace("POSTGRES_PORT='55432'", "POSTGRES_PORT='55433'"),
            "rag-grpc-reused-as-decision": valid.replace(
                f"RAG_GRPC_SHARED_SECRET='{'5' * 43}'",
                f"RAG_GRPC_SHARED_SECRET='{'S' * 43}'",
            ),
            "rag-grpc-reused-as-jwt": valid.replace(
                f"RAG_GRPC_SHARED_SECRET='{'5' * 43}'",
                f"RAG_GRPC_SHARED_SECRET='{'F' * 43}'",
            ),
            "rag-grpc-reused-as-brokerage-capability": valid.replace(
                f"RAG_GRPC_SHARED_SECRET='{'5' * 43}'",
                f"RAG_GRPC_SHARED_SECRET='{'U' * 43}'",
            ),
            "capability-digest-mismatch": valid.replace(
                f"BROKERAGE_DB_CAPABILITY_TOKEN_SHA256='{hashlib.sha256(('U' * 43).encode()).hexdigest()}'",
                f"BROKERAGE_DB_CAPABILITY_TOKEN_SHA256='{'0' * 64}'",
            ),
            "crlf": valid.replace("\n", "\r\n"),
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.environment_file(content) as path:
                with self.assertRaises(OpenApiEnvironmentError):
                    parse_openapi_environment(path)

    def test_symlink_and_broad_permissions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            target = root / "target.env"
            target.write_text(self.valid_environment_text(), encoding="utf-8")
            target.chmod(0o600)
            symlink = root / "linked.env"
            symlink.symlink_to(target)
            with self.assertRaises(OpenApiEnvironmentError):
                parse_openapi_environment(symlink)

            target.chmod(0o644)
            with self.assertRaises(OpenApiEnvironmentError):
                parse_openapi_environment(target)

    def test_subprocess_environment_replaces_inherited_wslenv_with_exact_fixture_names(
        self,
    ) -> None:
        values = {
            line.split("=", 1)[0]: line.split("=", 1)[1][1:-1]
            for line in self.valid_environment_text().splitlines()
        }
        with patch.dict(
            os.environ,
            {"PATH": "/usr/bin", "WSLENV": "UNSAFE_INHERITED_NAME", "UNSAFE_INHERITED_NAME": "x"},
            clear=True,
        ):
            environment = _explicit_process_environment(values)

        self.assertNotIn("UNSAFE_INHERITED_NAME", environment)
        self.assertEqual(
            ":".join((*values, "COMPOSE_DISABLE_ENV_FILE")),
            environment["WSLENV"],
        )

    @staticmethod
    def valid_environment_text() -> str:
        base64_a = "A" * 43
        base64_b = "B" * 43
        base64_c = "C" * 43
        base64_d = "D" * 43
        bcrypt_user = "$2b$12$" + "u" * 53
        bcrypt_admin = "$2b$12$" + "a" * 53
        values = {
            "POSTGRES_DB": "trading",
            "POSTGRES_ADMIN_USER": "postgres",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_HOST_PORT": "55432",
            "POSTGRES_PORT": "55432",
            "POSTGRES_ADMIN_PASSWORD": base64_a,
            "POSTGRES_APP_PASSWORD": base64_b,
            "POSTGRES_MIGRATION_PASSWORD": base64_c,
            "POSTGRES_COLLECTOR_PASSWORD": base64_d,
            "POSTGRES_DISCLOSURE_READER_PASSWORD": "R" * 43,
            "POSTGRES_MARKET_WRITER_PASSWORD": "N" * 43,
            "POSTGRES_PORTFOLIO_WRITER_PASSWORD": "O" * 43,
            "POSTGRES_RISK_WRITER_PASSWORD": "P" * 43,
            "POSTGRES_FILL_WRITER_PASSWORD": "V" * 43,
            "POSTGRES_RAG_WRITER_PASSWORD": "W" * 43,
            "POSTGRES_RAG_ADMIN_PASSWORD": "X" * 43,
            "POSTGRES_RAG_QUERY_PASSWORD": "Y" * 43,
            "POSTGRES_SIGNAL_WRITER_PASSWORD": "7" * 43,
            "POSTGRES_SIGNAL_SCHEDULER_PASSWORD": "8" * 43,
            "POSTGRES_SIGNAL_ADMIN_PASSWORD": "9" * 43,
            "POSTGRES_WORKER_PASSWORD": "w" * 43,
            "KAFKA_UI_USERNAME": "admin",
            "KAFKA_UI_PASSWORD": "f" * 43,
            "ASYNC_POLLING_ENABLED": "false",
            "ASYNC_WORKER_ENABLED": "false",
            "ASYNC_CURSOR_HMAC_KEY": "c" * 43,
            "ASYNC_PARTITION_HMAC_KEY": "d" * 43,
            "ASYNC_WORKER_GRPC_SHARED_SECRET": "e" * 43,
            "DECISION_GRPC_SHARED_SECRET": "S" * 43,
            "RAG_GRPC_SHARED_SECRET": "5" * 43,
            "PYTHON_GRPC_SHARED_SECRET": "S" * 43,
            "REDIS_PASSWORD": "E" * 43,
            "SEARXNG_SECRET": "4" * 43,
            "MCP_SEARXNG_AUTH_TOKEN": "6" * 43,
            "JWT_SECRET": "F" * 43,
            "JWT_ISSUER": "s21-openapi-local",
            "JWT_AUDIENCE": "s21-openapi-client",
            "LOGIN_SCOPE_HMAC_KEY": "G" * 43,
            "PRINCIPLE_CURSOR_HMAC_KEY": "H" * 43,
            "DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY": "Q" * 43,
            "BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY": "T" * 43,
            "BROKERAGE_DB_CAPABILITY_TOKEN": "U" * 43,
            "BROKERAGE_DB_CAPABILITY_TOKEN_SHA256": hashlib.sha256(("U" * 43).encode()).hexdigest(),
            "RAG_HISTORY_SECRET_DIRECTORY": "/tmp/capstone-openapi-rag-secrets",
            "RAG_HISTORY_CURRENT_KEK_VERSION": "kek-v1",
            "RAG_IDEMPOTENCY_SCOPE_HMAC_KEY": "Z" * 43,
            "RAG_REQUEST_FINGERPRINT_HMAC_KEY": "0" * 43,
            "RAG_PROVIDER_USAGE_HMAC_KEY": "1" * 43,
            "RAG_RATE_LIMIT_HMAC_KEY": "2" * 43,
            "RAG_HISTORY_CURSOR_HMAC_KEY": "3" * 43,
            "DEMO_CREDENTIAL_SEPARATION_KEY": "I" * 43,
            "DEMO_USER_CREDENTIAL_BUNDLE": (
                f"s21-v1:usr_demo_user:{'J' * 43}:{bcrypt_user}:{'K' * 43}"
            ),
            "DEMO_ADMIN_CREDENTIAL_BUNDLE": (
                f"s21-v1:usr_demo_admin:{'L' * 43}:{bcrypt_admin}:{'M' * 43}"
            ),
        }
        return "".join(f"{name}='{value}'\n" for name, value in values.items())

    class environment_file:
        def __init__(self, content: str) -> None:
            self.content = content
            self.directory: tempfile.TemporaryDirectory[str] | None = None
            self.path: Path | None = None

        def __enter__(self) -> Path:
            self.directory = tempfile.TemporaryDirectory(dir="/tmp")
            self.path = Path(self.directory.name) / "openapi.env"
            self.path.write_text(self.content, encoding="utf-8", newline="")
            self.path.chmod(0o600)
            return self.path

        def __exit__(self, *_: object) -> None:
            assert self.directory is not None
            self.directory.cleanup()


class OpenApiGateCleanupTest(unittest.TestCase):
    def test_partial_compose_start_is_cleaned_up(self) -> None:
        commands: list[list[str]] = []

        def fail_start_then_allow_cleanup(
            command: list[str],
            **_: object,
        ) -> None:
            commands.append(command)
            if len(commands) == 1:
                raise OpenApiGateError("simulated compose health failure")

        with (
            patch("contracts.run_openapi_gate.parse_openapi_environment", return_value={}),
            patch("contracts.run_openapi_gate._require_fixture_port_available"),
            patch(
                "contracts.run_openapi_gate._run",
                side_effect=fail_start_then_allow_cleanup,
            ),
        ):
            with self.assertRaisesRegex(
                OpenApiGateError,
                "simulated compose health failure",
            ):
                run_gate(Path("/unused/openapi.env"), write=False)

        self.assertEqual(2, len(commands))
        self.assertIn("up", commands[0])
        self.assertIn("down", commands[1])
        self.assertIn("--volumes", commands[1])

    def test_runtime_gate_selects_implementation_normalization(self) -> None:
        commands: list[list[str]] = []
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            generated = Path(directory) / "openapi.json"
            generated.write_text("{}\n", encoding="utf-8")
            with (
                patch("contracts.run_openapi_gate.parse_openapi_environment", return_value={}),
                patch("contracts.run_openapi_gate._require_fixture_port_available"),
                patch("contracts.run_openapi_gate.GENERATED_OPENAPI", generated),
                patch(
                    "contracts.run_openapi_gate._run",
                    side_effect=lambda command, **_: commands.append(list(command)),
                ),
            ):
                run_gate(Path("/unused/openapi.env"), write=False)

        normalizer = next(
            command
            for command in commands
            if "contracts/normalize_openapi.py" in command
        )
        self.assertIn("--implementation", normalizer)


class ContractsCiWorkflowTest(unittest.TestCase):
    def test_openapi_fixture_task_uses_checked_in_gradle_wrapper(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        workflow = (repo_root / ".github/workflows/contracts-ci.yml").read_text(encoding="utf-8")
        expected_command = (
            "run: workspaces/decision-platform/spring-api/gradlew "
            "-p workspaces/decision-platform/spring-api prepareOpenApiFixtureEnv"
        )

        # GitHub runner의 저장소 루트에는 gradlew가 없으므로 workspace wrapper를 직접 호출한다.
        self.assertIn(expected_command, workflow)
        self.assertNotIn(
            "run: ./gradlew -p workspaces/decision-platform/spring-api prepareOpenApiFixtureEnv",
            workflow,
        )


class OpenApiNormalizerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog_bytes = canonical_json_bytes(load_catalog(CATALOG_PATH))
        self.digest = hashlib.sha256(self.catalog_bytes).hexdigest()
        self.s23_digest = hashlib.sha256(S23_CATALOG_PATH.read_bytes()).hexdigest()
        self.s32_digest = hashlib.sha256(S32_CATALOG_PATH.read_bytes()).hexdigest()
        self.s33_digest = hashlib.sha256(
            (
                Path(__file__).resolve().parents[2]
                / "contracts/catalogs/s3-3-fill-contract.v1.json"
            ).read_bytes()
        ).hexdigest()
        self.generated = {
            "openapi": "3.1.0",
            "jsonSchemaDialect": "https://spec.openapis.org/oas/3.1/dialect/base",
            "x-s2-1-contract-id": "s2-1-principle-contract/v1",
            "x-s2-1-contract-sha256": self.digest,
            "x-s2-3-contract-id": "s2-3-decision-contract/v1",
            "x-s2-3-contract-sha256": self.s23_digest,
            "x-s3-2-contract-id": "s3-2-internal-paper-contract/v1",
            "x-s3-2-contract-sha256": self.s32_digest,
            "x-s3-3-contract-id": "s3-3-fill-contract/v1",
            "x-s3-3-contract-sha256": self.s33_digest,
            "info": {"title": "Decision Platform API", "version": "0"},
            "paths": {
                "/api/v1/auth/login": {
                    "post": {"responses": {"200": {"description": "Successful login"}}}
                }
            },
            "components": {"schemas": {"LoginRequest": {"type": "object"}}},
        }
        self.expected = copy.deepcopy(self.generated)
        self.expected["openapi"] = "3.1.1"

    def test_only_root_patch_and_deterministic_formatting_are_accepted(self) -> None:
        normalized = check_normalized_openapi(
            canonical_json_bytes(self.generated),
            canonical_json_bytes(self.expected),
            self.catalog_bytes,
            amendment=True,
        )

        self.assertEqual(canonical_json_bytes(self.expected), normalized)

    def test_generated_document_must_pass_the_oas_31_schema(self) -> None:
        invalid_oas = copy.deepcopy(self.generated)
        invalid_oas["info"] = {"title": "Missing required version"}

        with self.assertRaises(OpenApiNormalizationError):
            normalize_generated_openapi(
                canonical_json_bytes(invalid_oas),
                self.catalog_bytes,
                amendment=True,
            )

    def test_implementation_mode_accepts_real_principle_paths(self) -> None:
        implementation = copy.deepcopy(self.generated)
        implementation["paths"]["/api/v1/principles"] = {
            "get": {"responses": {"200": {"description": "Owned Principle page"}}}
        }
        implementation["paths"].update(self._decision_paths())
        implementation["components"]["schemas"].update(self._decision_components())
        implementation["paths"].update(self._s31_paths())
        implementation["paths"].update(self._s32_paths())
        implementation["components"]["schemas"].update(self._s32_components())
        implementation["paths"].update(self._s33_paths())
        implementation["components"]["schemas"].update(self._s33_components())

        normalized = normalize_generated_openapi(
            canonical_json_bytes(implementation),
            self.catalog_bytes,
            amendment=False,
        )

        self.assertIn(b"/api/v1/principles", normalized)

    def test_implementation_mode_accepts_only_the_exact_decision_paths_and_methods(
        self,
    ) -> None:
        implementation = copy.deepcopy(self.generated)
        implementation["paths"].update(self._decision_paths())
        implementation["components"]["schemas"].update(self._decision_components())
        implementation["paths"].update(self._s31_paths())
        implementation["paths"].update(self._s32_paths())
        implementation["components"]["schemas"].update(self._s32_components())
        implementation["paths"].update(self._s33_paths())
        implementation["components"]["schemas"].update(self._s33_components())

        normalized = normalize_generated_openapi(
            canonical_json_bytes(implementation),
            self.catalog_bytes,
            amendment=False,
        )
        self.assertIn(b"/api/v1/decisions/evaluate-order", normalized)

        mutations = []
        missing = copy.deepcopy(implementation)
        del missing["paths"]["/api/v1/decisions/{decisionId}/audit"]
        mutations.append(missing)
        extra = copy.deepcopy(implementation)
        extra["paths"]["/api/v1/decisions"] = {
            "get": {"responses": {"200": {"description": "Unapproved collection"}}}
        }
        mutations.append(extra)
        wrong_method = copy.deepcopy(implementation)
        wrong_method["paths"]["/api/v1/decisions/evaluate-order"]["get"] = {
            "responses": {"200": {"description": "Unapproved method"}}
        }
        mutations.append(wrong_method)
        missing_component = copy.deepcopy(implementation)
        del missing_component["components"]["schemas"]["S23Decision"]
        mutations.append(missing_component)
        extra_component = copy.deepcopy(implementation)
        extra_component["components"]["schemas"]["S23Unapproved"] = {
            "type": "object"
        }
        mutations.append(extra_component)

        for mutation in mutations:
            with self.subTest(mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()):
                with self.assertRaises(OpenApiNormalizationError):
                    normalize_generated_openapi(
                        canonical_json_bytes(mutation),
                        self.catalog_bytes,
                        amendment=False,
                    )

    @staticmethod
    def _decision_paths() -> dict[str, object]:
        return {
            "/api/v1/decisions/evaluate-order": {
                "post": {"responses": {"200": {"description": "Decision result"}}}
            },
            "/api/v1/decisions/{decisionId}": {
                "parameters": [
                    {
                        "name": "decisionId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {"responses": {"200": {"description": "Owned Decision"}}}
            },
            "/api/v1/decisions/{decisionId}/audit": {
                "parameters": [
                    {
                        "name": "decisionId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {"responses": {"200": {"description": "Sanitized audit"}}}
            },
        }

    @staticmethod
    def _decision_components() -> dict[str, object]:
        return {
            "S23EvaluateOrderRequest": {"type": "object"},
            "S23Decision": {"type": "object"},
            "S23DecisionSuccessResponse": {"type": "object"},
            "S23DecisionAudit": {"type": "object"},
            "S23DecisionAuditSuccessResponse": {"type": "object"},
        }

    def test_implementation_mode_accepts_only_the_exact_s32_paths_and_components(
        self,
    ) -> None:
        implementation = copy.deepcopy(self.generated)
        implementation["paths"].update(self._decision_paths())
        implementation["components"]["schemas"].update(self._decision_components())
        implementation["paths"].update(self._s31_paths())
        implementation["paths"].update(self._s32_paths())
        implementation["components"]["schemas"].update(self._s32_components())
        implementation["paths"].update(self._s33_paths())
        implementation["components"]["schemas"].update(self._s33_components())

        normalized = normalize_generated_openapi(
            canonical_json_bytes(implementation),
            self.catalog_bytes,
            amendment=False,
        )
        self.assertIn(b"/api/v1/brokerage/paper/orders", normalized)

        mutations = []
        missing = copy.deepcopy(implementation)
        del missing["paths"]["/api/v1/brokerage/paper/accounts/{accountId}/balances"]
        mutations.append(missing)
        extra = copy.deepcopy(implementation)
        extra["paths"]["/api/v1/brokerage/paper/accounts/{accountId}/positions"] = {
            "get": {"responses": {"200": {"description": "Unapproved paper route"}}}
        }
        mutations.append(extra)
        wrong_method = copy.deepcopy(implementation)
        wrong_method["paths"]["/api/v1/brokerage/paper/orders"]["get"] = {
            "responses": {"200": {"description": "Unapproved method"}}
        }
        mutations.append(wrong_method)
        missing_component = copy.deepcopy(implementation)
        del missing_component["components"]["schemas"]["S32PaperOrder"]
        mutations.append(missing_component)
        extra_component = copy.deepcopy(implementation)
        extra_component["components"]["schemas"]["S32Unapproved"] = {
            "type": "object"
        }
        mutations.append(extra_component)
        wrong_digest = copy.deepcopy(implementation)
        wrong_digest["x-s3-2-contract-sha256"] = "0" * 64
        mutations.append(wrong_digest)

        for mutation in mutations:
            with self.subTest(mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()):
                with self.assertRaises(OpenApiNormalizationError):
                    normalize_generated_openapi(
                        canonical_json_bytes(mutation),
                        self.catalog_bytes,
                        amendment=False,
                    )

    @staticmethod
    def _s31_paths() -> dict[str, object]:
        account_id_parameter = {
            "name": "accountId",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        return {
            "/api/v1/brokerage/mock/orders": {
                "post": {"responses": {"200": {"description": "Mock order"}}}
            },
            "/api/v1/brokerage/mock/accounts/{accountId}/balances": {
                "parameters": [account_id_parameter],
                "get": {"responses": {"200": {"description": "Mock balance"}}},
            },
            "/api/v1/brokerage/mock/accounts/{accountId}/buyable": {
                "parameters": [account_id_parameter],
                "get": {"responses": {"200": {"description": "Mock buyable"}}},
            },
        }

    @staticmethod
    def _s32_paths() -> dict[str, object]:
        order_id_parameter = {
            "name": "orderId",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        account_id_parameter = {
            "name": "accountId",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        return {
            "/api/v1/brokerage/paper/orders": {
                "post": {"responses": {"200": {"description": "Paper order"}}}
            },
            "/api/v1/brokerage/paper/accounts/{accountId}/balances": {
                "parameters": [account_id_parameter],
                "get": {"responses": {"200": {"description": "Paper balance"}}},
            },
            "/api/v1/brokerage/paper/accounts/{accountId}/buyable": {
                "parameters": [account_id_parameter],
                "get": {"responses": {"200": {"description": "Paper buyable"}}},
            },
            "/api/v1/brokerage/orders/{orderId}": {
                "parameters": [order_id_parameter],
                "get": {"responses": {"200": {"description": "Order detail"}}},
            },
            "/api/v1/brokerage/orders/{orderId}/cancel": {
                "parameters": [order_id_parameter],
                "post": {"responses": {"200": {"description": "Cancelled order"}}},
            },
        }

    @staticmethod
    def _s32_components() -> dict[str, object]:
        return {
            "S32PaperOrderRequest": {"type": "object"},
            "S32PaperOrder": {"type": "object"},
            "S32OrderDetail": {"type": "object"},
            "S32PaperBalance": {"type": "object"},
            "S32PaperBuyable": {"type": "object"},
            "S32PaperOrderSuccessResponse": {"type": "object"},
            "S32OrderDetailSuccessResponse": {"type": "object"},
            "S32PaperBalanceSuccessResponse": {"type": "object"},
            "S32PaperBuyableSuccessResponse": {"type": "object"},
        }

    def test_implementation_mode_accepts_only_exact_s33_paths_components_and_digest(
        self,
    ) -> None:
        implementation = copy.deepcopy(self.generated)
        implementation["paths"].update(self._decision_paths())
        implementation["components"]["schemas"].update(self._decision_components())
        implementation["paths"].update(self._s31_paths())
        implementation["paths"].update(self._s32_paths())
        implementation["components"]["schemas"].update(self._s32_components())
        implementation["paths"].update(self._s33_paths())
        implementation["components"]["schemas"].update(self._s33_components())

        normalized = normalize_generated_openapi(
            canonical_json_bytes(implementation),
            self.catalog_bytes,
            amendment=False,
        )
        self.assertIn(b"/api/v1/brokerage/orders/{orderId}/reconcile", normalized)

        mutations = []
        missing = copy.deepcopy(implementation)
        del missing["paths"]["/api/v1/brokerage/mock/accounts/{accountId}/fills"]
        mutations.append(missing)
        public_report = copy.deepcopy(implementation)
        public_report["paths"]["/api/v1/brokerage/orders/{orderId}/report-fill"] = {
            "post": {"responses": {"200": {"description": "Unapproved fill claim"}}}
        }
        mutations.append(public_report)
        executions = copy.deepcopy(implementation)
        executions["paths"]["/api/v1/brokerage/orders/{orderId}/executions"] = {
            "parameters": copy.deepcopy(
                implementation["paths"][
                    "/api/v1/brokerage/orders/{orderId}/reconcile"
                ]["parameters"]
            ),
            "post": {"responses": {"200": {"description": "Unapproved execution claim"}}}
        }
        mutations.append(executions)
        wrong_method = copy.deepcopy(implementation)
        wrong_method["paths"]["/api/v1/brokerage/orders/{orderId}/reconcile"]["get"] = {
            "responses": {"200": {"description": "Unapproved method"}}
        }
        mutations.append(wrong_method)
        missing_component = copy.deepcopy(implementation)
        del missing_component["components"]["schemas"]["S33FillPage"]
        mutations.append(missing_component)
        extra_component = copy.deepcopy(implementation)
        extra_component["components"]["schemas"]["S33RawObservation"] = {
            "type": "object"
        }
        mutations.append(extra_component)
        wrong_digest = copy.deepcopy(implementation)
        wrong_digest["x-s3-3-contract-sha256"] = "0" * 64
        mutations.append(wrong_digest)

        for mutation in mutations:
            with self.subTest(
                mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()
            ):
                with self.assertRaises(OpenApiNormalizationError):
                    normalize_generated_openapi(
                        canonical_json_bytes(mutation),
                        self.catalog_bytes,
                        amendment=False,
                    )

    @staticmethod
    def _s33_paths() -> dict[str, object]:
        order_id_parameter = {
            "name": "orderId",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        account_id_parameter = {
            "name": "accountId",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        return {
            "/api/v1/brokerage/orders/{orderId}/reconcile": {
                "parameters": [order_id_parameter],
                "post": {"responses": {"200": {"description": "Reconciliation"}}},
            },
            "/api/v1/brokerage/mock/accounts/{accountId}/fills": {
                "parameters": [account_id_parameter],
                "get": {"responses": {"200": {"description": "Mock fills"}}},
            },
            "/api/v1/brokerage/paper/accounts/{accountId}/fills": {
                "parameters": [account_id_parameter],
                "get": {"responses": {"200": {"description": "Paper fills"}}},
            },
        }

    @staticmethod
    def _s33_components() -> dict[str, object]:
        return {
            "S33FillObservation": {"type": "object"},
            "S33Reconcile": {"type": "object"},
            "S33FillPage": {"type": "object"},
            "S33ReconcileSuccessResponse": {"type": "object"},
            "S33FillPageSuccessResponse": {"type": "object"},
        }

    def test_dialect_paths_components_and_digest_mutations_fail_closed(self) -> None:
        mutations = []
        wrong_dialect = copy.deepcopy(self.generated)
        wrong_dialect["jsonSchemaDialect"] = "https://json-schema.org/draft/2020-12/schema"
        mutations.append(wrong_dialect)

        premature_path = copy.deepcopy(self.generated)
        premature_path["paths"]["/api/v1/principles"] = {"get": {"responses": {"200": {}}}}
        mutations.append(premature_path)

        drifted_component = copy.deepcopy(self.generated)
        drifted_component["components"]["schemas"]["Injected"] = {"type": "string"}
        mutations.append(drifted_component)

        wrong_digest = copy.deepcopy(self.generated)
        wrong_digest["x-s2-1-contract-sha256"] = "0" * 64
        mutations.append(wrong_digest)

        invalid_oas = copy.deepcopy(self.generated)
        invalid_oas["info"] = {"title": "Missing required version"}
        mutations.append(invalid_oas)

        for mutation in mutations:
            with self.subTest(mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()):
                with self.assertRaises(OpenApiNormalizationError):
                    check_normalized_openapi(
                        canonical_json_bytes(mutation),
                        canonical_json_bytes(self.expected),
                        self.catalog_bytes,
                        amendment=True,
                    )


if __name__ == "__main__":
    unittest.main()
