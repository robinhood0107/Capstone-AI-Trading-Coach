from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s2_2_contracts import (
    CATALOG_PATH,
    generate_outputs,
    load_catalog,
    load_json_bytes_strict,
)
from contracts.generate_s2_3_contracts import (
    CATALOG_PATH as S23_CATALOG_PATH,
    EXPECTED_CATALOG_SHA256,
    OUTPUTS as S23_OUTPUTS,
    generate_outputs as generate_s23_outputs,
    load_catalog as load_s23_catalog,
    validate_decision_response_semantics,
    validate_request_semantics,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ORDER_FIELDS = {
    "estimatedAmount",
    "estimatedPrice",
    "orderType",
    "quantity",
    "side",
    "strategyId",
    "symbol",
    "timeframe",
}


class S23CiWiringContractTest(unittest.TestCase):


    def test_ci_runs_s23_contract_and_proto_drift_checks(self) -> None:
        contracts_workflow = (
            REPO_ROOT / ".github/workflows/contracts-ci.yml"
        ).read_text(encoding="utf-8")
        python_workflow = (
            REPO_ROOT / ".github/workflows/python-ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "uv run --frozen python contracts/generate_s2_3_contracts.py --check",
            contracts_workflow,
        )
        self.assertIn(
            "uv run --frozen python ../../../contracts/generate_disclosure_proto.py --check",
            python_workflow,
        )

    def test_repo_hygiene_supplies_every_required_source_writer_password(self) -> None:
        workflow = (
            REPO_ROOT / ".github/workflows/repo-hygiene.yml"
        ).read_text(encoding="utf-8")
        for variable in (
            "POSTGRES_MARKET_WRITER_PASSWORD",
            "POSTGRES_PORTFOLIO_WRITER_PASSWORD",
            "POSTGRES_RISK_WRITER_PASSWORD",
            "POSTGRES_RAG_WRITER_PASSWORD",
            "POSTGRES_RAG_ADMIN_PASSWORD",
            "POSTGRES_RAG_QUERY_PASSWORD",
            "POSTGRES_SIGNAL_WRITER_PASSWORD",
            "POSTGRES_SIGNAL_SCHEDULER_PASSWORD",
            "POSTGRES_SIGNAL_ADMIN_PASSWORD",
        ):
            with self.subTest(variable=variable):
                self.assertIn(f"{variable}: validation-dummy-", workflow)


class S23CashOrderContractTest(unittest.TestCase):
    def test_order_intent_schema_uses_exact_positive_integer_krw_fields(self) -> None:
        schema = load_json_bytes_strict(
            (REPO_ROOT / "contracts/schemas/order_intent.schema.json").read_bytes(),
            source="order intent schema",
        )

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(ORDER_FIELDS, set(schema["required"]))
        self.assertEqual(ORDER_FIELDS, set(schema["properties"]))
        for field in ("estimatedPrice", "estimatedAmount"):
            self.assertEqual("integer", schema["properties"][field]["type"])
            self.assertEqual(1, schema["properties"][field]["minimum"])

    def test_s2_2_generated_hash_vector_has_v2_order_identity(self) -> None:
        outputs = generate_outputs(load_catalog(CATALOG_PATH))
        vector = load_json_bytes_strict(
            outputs["contracts/examples/s2-2-hash-vector.valid.json"],
            source="generated S2.2 hash vector",
        )
        order_intent = vector["snapshotArtifact"]["orderIntent"]

        self.assertEqual("HASH-CANONICALIZATION-S22-V2", vector["canonicalizationId"])
        self.assertEqual(
            "s2.2-metric-snapshot-v2",
            vector["snapshotArtifact"]["snapshotSchemaVersion"],
        )
        self.assertEqual(ORDER_FIELDS, set(order_intent))


class S23GeneratedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_s23_catalog(S23_CATALOG_PATH)
        cls.outputs = generate_s23_outputs(cls.catalog)

    def test_catalog_hash_and_generated_output_manifest_are_locked(self) -> None:
        import hashlib

        self.assertEqual(
            EXPECTED_CATALOG_SHA256,
            hashlib.sha256(S23_CATALOG_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(S23_OUTPUTS, frozenset(self.outputs))
        for relative, expected in self.outputs.items():
            with self.subTest(path=relative):
                self.assertEqual(expected, (REPO_ROOT / relative).read_bytes())
                self.assertTrue(expected.endswith(b"\n"))

    def test_review_remediation_amendment_is_part_of_the_canonical_catalog(
        self,
    ) -> None:
        self.assertEqual(
            {
                "corpCodeRequestPolicy": "OPTIONAL_EMPTY_RESOLVES_FROM_REGISTRY",
                "corpRegistryResolution": "SYMBOL_EXACTLY_ONE",
                "eventLookbackDays": 365,
                "physicalAttemptsMax": 1,
                "retryEnabled": False,
                "transparentRetryEnabled": False,
            },
            {
                key: self.catalog["grpc"][key]
                for key in (
                    "corpCodeRequestPolicy",
                    "corpRegistryResolution",
                    "eventLookbackDays",
                    "physicalAttemptsMax",
                    "retryEnabled",
                    "transparentRetryEnabled",
                )
            },
        )
        self.assertEqual(
            {
                "lockOrder": [
                    "IDEMPOTENCY_ADVISORY_LOCK",
                    "PRINCIPLE_FOR_SHARE",
                    "DECISION_GRAPH_INSERT",
                ],
                "principleLock": "FOR SHARE OF principle",
                "updaterFirst": "HTTP_409_ALL_WRITES_ZERO",
                "decisionFirst": "UPDATER_WAITS_FOR_DECISION_COMMIT",
            },
            self.catalog["principleConcurrency"],
        )
        self.assertEqual(
            [
                "decision",
                "violations",
                "trace",
                "artifact",
                "audit",
                "outbox",
                "idempotencyResult",
            ],
            self.catalog["persistence"]["atomicInsertOrder"],
        )
        self.assertFalse(self.catalog["persistence"]["brokerPublish"])
        self.assertEqual(
            "SOURCE_READ_AND_EVALUATION_OUTSIDE",
            self.catalog["persistence"]["transactionReadPolicy"],
        )
        self.assertEqual(
            "FINAL_PERSISTENCE_ONLY",
            self.catalog["persistence"]["transactionWritePolicy"],
        )

    def test_structural_readiness_and_transient_unavailability_are_distinct(
        self,
    ) -> None:
        ownership = self.catalog["sourceOwnership"]
        self.assertEqual(
            "S23_RUNTIME_SOURCE_BLOCKED",
            ownership["structuralMissingPolicy"],
        )
        self.assertEqual(
            "PERSISTED_HOLD",
            ownership["transientUnavailablePolicy"],
        )
        self.assertEqual(
            [
                "canonicalTableOrProjection",
                "productionBeanOrPort",
                "offlineFixtureProducer",
                "leastPrivilegeWriterRole",
                "boundedReader",
                "freshnessCompletenessContract",
                "noFakeRegressionTest",
            ],
            ownership["structuralReadinessRequirements"],
        )
        self.assertEqual(
            {
                "corporation_registry_observations",
                "daily_order_count_observations",
                "deterministic_risk_observations",
                "instrument_catalog_observations",
                "market_quote_observations",
                "portfolio_balance_observations",
                "portfolio_position_observations",
            },
            {row["table"] for row in ownership["observationContracts"]},
        )
        self.assertFalse(ownership["productionSeed"])
        self.assertFalse(ownership["providerHttpFallback"])

    def test_approved_s11_instrument_catalog_source_is_exactly_locked(self) -> None:
        ownership = self.catalog["sourceOwnership"]
        self.assertEqual("S1.1", ownership["instrumentProducer"])
        self.assertEqual(
            "instrument_catalog_observations",
            ownership["instrumentTable"],
        )
        self.assertEqual(
            "latest_instrument_catalog_observations",
            ownership["instrumentProjection"],
        )
        self.assertEqual("decision_market_writer", ownership["instrumentWriterRole"])
        self.assertEqual(1, ownership["instrumentReaderMaxRows"])
        self.assertEqual(
            [
                "symbol",
                "isEtfEtn",
                "isGoldEtfEtn",
                "productRiskScore",
                "catalogVersion",
                "observedAt",
                "receivedAt",
                "sourceRef",
                "artifactHash",
            ],
            ownership["instrumentFields"],
        )
        self.assertTrue(ownership["instrumentProductRiskScoreNullable"])
        self.assertEqual(
            "LATEST_OBSERVED_VERSION_NO_FALLBACK",
            ownership["instrumentVersionPolicy"],
        )
        self.assertEqual(
            "FUTURE_TIMESTAMP_IS_STALE",
            ownership["instrumentTimePolicy"],
        )
        instrument_contract = next(
            row
            for row in ownership["observationContracts"]
            if row["table"] == "instrument_catalog_observations"
        )
        self.assertEqual(
            {
                "producerOwner": "S1.1",
                "projection": "latest_instrument_catalog_observations",
                "table": "instrument_catalog_observations",
                "writerRole": "decision_market_writer",
            },
            instrument_contract,
        )

    def test_database_and_observability_security_amendments_are_locked(self) -> None:
        database = self.catalog["databaseSecurity"]
        self.assertEqual(
            [
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOBYPASSRLS",
            ],
            database["decisionAppFlags"],
        )
        self.assertFalse(database["broadSelectGrant"])
        self.assertFalse(database["futureTableDefaultSelectGrant"])
        self.assertEqual(
            "BOUNDED_SECURITY_DEFINER_FUNCTION",
            database["idempotencyReplayRead"],
        )
        self.assertEqual(
            ["scopeHash", "ownerScopeHash", "expiresAt"],
            database["idempotencyReplayPredicates"],
        )
        observability = self.catalog["observability"]
        self.assertEqual(["GUIDE", "STRICT", "UNPINNED"], observability["modes"])
        self.assertEqual(
            "FIRST_STABLE_SORTED_ISSUE",
            observability["failClosedReason"],
        )
        self.assertTrue(observability["postCommitFaultIsolation"])
        self.assertTrue(observability["normalPathExactOnce"])

    def test_request_rejects_actor_mode_and_inexact_amount(self) -> None:
        schema = load_json_bytes_strict(
            self.outputs["contracts/schemas/s2-3-evaluate-order-request.schema.json"],
            source="generated S2.3 request schema",
        )
        validator = Draft202012Validator(schema)
        valid = load_json_bytes_strict(
            self.outputs["contracts/examples/s2-3-evaluate-order-request.valid.json"],
            source="generated S2.3 request fixture",
        )
        self.assertEqual(
            {"principleId", "portfolioSource", "orderIntent"},
            set(schema["required"]),
        )
        validate_request_semantics(valid)
        for forbidden in ("userId", "accountId", "mode", "corpCode", "auditActor", "time"):
            mutated = deepcopy(valid)
            mutated[forbidden] = "forged"
            with self.subTest(field=forbidden):
                self.assertTrue(list(validator.iter_errors(mutated)))
        mismatched = deepcopy(valid)
        mismatched["orderIntent"]["estimatedAmount"] += 1
        with self.assertRaises(ContractValidationError):
            validate_request_semantics(mismatched)

    def test_persisted_response_requires_identity_times_and_full_outcome_matrix(self) -> None:
        schema = load_json_bytes_strict(
            self.outputs["contracts/schemas/s2-3-decision-response.schema.json"],
            source="generated S2.3 response schema",
        )
        self.assertEqual(
            {
                "decisionId",
                "createdAt",
                "validUntil",
                "principleId",
                "principleVersionId",
                "principleVersion",
                "portfolioSource",
                "mode",
                "enforcementAction",
                "riskDecision",
            },
            set(schema["required"]),
        )
        expected = {
            "allow": ("ALLOW", "GUIDE", "NONE", True),
            "warn": ("WARN", "STRICT", "RECONFIRM_PRINCIPLE", True),
            "hold": ("HOLD", "GUIDE", "RE_EVALUATE", False),
            "block": ("BLOCK", "STRICT", "DO_NOT_SUBMIT", False),
        }
        s22_catalog = load_catalog(CATALOG_PATH)
        for name, row in expected.items():
            payload = load_json_bytes_strict(
                self.outputs[
                    f"contracts/examples/s2-3-decision-response.{name}.valid.json"
                ],
                source=f"generated S2.3 {name} fixture",
            )
            with self.subTest(outcome=name):
                validate_decision_response_semantics(payload, s22_catalog)
                self.assertEqual(row[0], payload["riskDecision"]["decision"])
                self.assertEqual(row[1], payload["mode"])
                self.assertEqual(row[2], payload["enforcementAction"])
                self.assertEqual(row[3], payload["riskDecision"]["canSubmitOrder"])

    def test_proto_is_the_single_stored_observation_business_rpc(self) -> None:
        proto = self.outputs["contracts/proto/disclosure_observation.proto"].decode("utf-8")
        self.assertEqual(1, proto.count("rpc GetDisclosureEvents("))
        self.assertIn("service DisclosureObservationService", proto)
        self.assertIn("repeated string source_refs = 10;", proto)
        self.assertIn("string observed_at = 11;", proto)
        self.assertNotIn("grpc.health", proto)
        self.assertNotIn("reflection", proto.lower())

    def test_tracked_openapi_embeds_the_locked_s23_routes_and_schema_components(
        self,
    ) -> None:
        import hashlib

        openapi = load_json_bytes_strict(
            (REPO_ROOT / "contracts/openapi/openapi.json").read_bytes(),
            source="tracked OpenAPI",
        )
        self.assertEqual(
            "s2-3-decision-contract/v1",
            openapi["x-s2-3-contract-id"],
        )
        self.assertEqual(
            hashlib.sha256(S23_CATALOG_PATH.read_bytes()).hexdigest(),
            openapi["x-s2-3-contract-sha256"],
        )
        expected_methods = {
            "/api/v1/decisions/evaluate-order": {"post"},
            "/api/v1/decisions/{decisionId}": {"get"},
            "/api/v1/decisions/{decisionId}/audit": {"get"},
        }
        actual_methods = {
            path: set(item).intersection(
                {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
            )
            for path, item in openapi["paths"].items()
            if path.startswith("/api/v1/decisions")
        }
        self.assertEqual(expected_methods, actual_methods)

        components = openapi["components"]["schemas"]
        self.assertEqual(
            {
                "S23EvaluateOrderRequest",
                "S23Decision",
                "S23DecisionSuccessResponse",
                "S23DecisionAudit",
                "S23DecisionAuditSuccessResponse",
            },
            {name for name in components if name.startswith("S23")},
        )
        request_schema = load_json_bytes_strict(
            self.outputs["contracts/schemas/s2-3-evaluate-order-request.schema.json"],
            source="generated S2.3 request schema",
        )
        response_schema = load_json_bytes_strict(
            self.outputs["contracts/schemas/s2-3-decision-response.schema.json"],
            source="generated S2.3 response schema",
        )
        self.assertEqual(
            self._normalize_schema_order(request_schema),
            self._normalize_schema_order(components["S23EvaluateOrderRequest"]),
        )
        self.assertEqual(
            self._normalize_schema_order(
                self._openapi_component_refs(response_schema, "S23Decision")
            ),
            self._normalize_schema_order(components["S23Decision"]),
        )
        evaluate = openapi["paths"]["/api/v1/decisions/evaluate-order"]["post"]
        self.assertEqual(
            "#/components/schemas/S23EvaluateOrderRequest",
            evaluate["requestBody"]["content"]["application/json"]["schema"]["$ref"],
        )
        self.assertEqual(
            "#/components/schemas/S23DecisionSuccessResponse",
            evaluate["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
        )

    @classmethod
    def _openapi_component_refs(cls, value: object, component: str) -> object:
        if isinstance(value, dict):
            return {
                key: (
                    item.replace(
                        "#/$defs/",
                        f"#/components/schemas/{component}/$defs/",
                    )
                    if key == "$ref" and isinstance(item, str)
                    else cls._openapi_component_refs(item, component)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._openapi_component_refs(item, component) for item in value]
        return value

    @classmethod
    def _normalize_schema_order(cls, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: (
                    sorted(item)
                    if key == "required"
                    and isinstance(item, list)
                    and all(isinstance(entry, str) for entry in item)
                    else cls._normalize_schema_order(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._normalize_schema_order(item) for item in value]
        return value


if __name__ == "__main__":
    unittest.main()
