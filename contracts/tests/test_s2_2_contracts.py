from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import OUTPUTS as S21_OUTPUTS
from contracts.generate_s2_2_contracts import (
    CATALOG_PATH,
    OUTPUTS as S22_OUTPUTS,
    ContractValidationError,
    generate_outputs,
    hash_canonical_bytes,
    load_catalog,
    load_json_bytes_strict,
    validate_catalog_semantics,
    validate_risk_decision_semantics,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class S22CatalogContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_catalog_has_public_eight_system_six_and_12_1_1_disposition(self) -> None:
        rules = self.catalog["rules"]

        self.assertEqual(14, len(rules))
        self.assertEqual(list(range(1, 15)), [rule["order"] for rule in rules])
        self.assertEqual(14, len({rule["ruleId"] for rule in rules}))
        self.assertEqual(
            {"PUBLIC_PRINCIPLE": 8, "SYSTEM_MANAGED": 6},
            {
                ownership: sum(rule["ownership"] == ownership for rule in rules)
                for ownership in ("PUBLIC_PRINCIPLE", "SYSTEM_MANAGED")
            },
        )
        self.assertEqual(
            {"THRESHOLD": 12, "READINESS": 1, "NOT_APPLICABLE": 1},
            {
                kind: sum(rule["executionKind"] == kind for rule in rules)
                for kind in ("THRESHOLD", "READINESS", "NOT_APPLICABLE")
            },
        )
        self.assertEqual(
            [
                "high_volatility_guard",
                "data_freshness_guard",
                "hmm_risk_off_guard",
                "mean_reversion_warning",
                "etf_etn_risk_check",
                "ad_leading_room_guard",
            ],
            self.catalog["systemManagedRuleIds"],
        )

    def test_catalog_semantics_fail_closed_on_order_ownership_and_disposition_drift(self) -> None:
        mutations = []

        duplicate = copy.deepcopy(self.catalog)
        duplicate["rules"][1]["ruleId"] = duplicate["rules"][0]["ruleId"]
        mutations.append(duplicate)

        reordered = copy.deepcopy(self.catalog)
        reordered["rules"][0], reordered["rules"][1] = (
            reordered["rules"][1],
            reordered["rules"][0],
        )
        mutations.append(reordered)

        public_system_mix = copy.deepcopy(self.catalog)
        public_system_mix["rules"][0]["ownership"] = "SYSTEM_MANAGED"
        mutations.append(public_system_mix)

        wrong_disposition = copy.deepcopy(self.catalog)
        wrong_disposition["rules"][9]["executionKind"] = "THRESHOLD"
        mutations.append(wrong_disposition)

        for mutation in mutations:
            with self.subTest(mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()):
                with self.assertRaises(ContractValidationError):
                    validate_catalog_semantics(mutation)

    def test_s21_and_s22_generator_outputs_are_explicit_and_disjoint(self) -> None:
        generated = generate_outputs(self.catalog)

        self.assertEqual(S22_OUTPUTS, frozenset(generated))
        self.assertFalse(S21_OUTPUTS & S22_OUTPUTS)
        self.assertNotIn("contracts/catalogs/s2-1-principle-contract.v1.json", S22_OUTPUTS)
        self.assertNotIn("contracts/schemas/principle-rule.schema.json", S22_OUTPUTS)

    def test_generated_artifacts_are_deterministic_and_lf_terminated(self) -> None:
        first = generate_outputs(copy.deepcopy(self.catalog))
        second = generate_outputs(copy.deepcopy(self.catalog))

        self.assertEqual(first, second)
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))


class RiskDecisionWireContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)
        cls.outputs = generate_outputs(cls.catalog)
        cls.schema = load_json_bytes_strict(
            cls.outputs["contracts/schemas/risk_decision.schema.json"],
            source="generated risk decision schema",
        )
        cls.validator = Draft202012Validator(cls.schema)

    def test_all_generated_positive_result_fixtures_are_schema_and_semantic_valid(self) -> None:
        paths = sorted(
            path
            for path in self.outputs
            if path.startswith("contracts/examples/risk_decision")
            and path.endswith(".valid.json")
        )
        self.assertGreaterEqual(len(paths), 9)

        for path in paths:
            payload = load_json_bytes_strict(self.outputs[path], source=path)
            with self.subTest(path=path):
                self.validator.validate(payload)
                validate_risk_decision_semantics(payload, self.catalog)

    def test_all_generated_negative_result_fixtures_are_rejected(self) -> None:
        paths = sorted(
            path
            for path in self.outputs
            if path.startswith("contracts/examples/invalid/risk_decision")
            and path.endswith(".invalid.json")
        )
        self.assertGreaterEqual(len(paths), 6)

        for path in paths:
            payload = load_json_bytes_strict(self.outputs[path], source=path)
            schema_errors = list(self.validator.iter_errors(payload))
            semantic_error = None
            if not schema_errors:
                try:
                    validate_risk_decision_semantics(payload, self.catalog)
                except ContractValidationError as caught:
                    semantic_error = caught
            with self.subTest(path=path):
                self.assertTrue(schema_errors or semantic_error is not None)

    def test_result_evidence_values_are_non_null_and_hold_uses_issues(self) -> None:
        violation = self.schema["$defs"]["violation"]
        risk_item = self.schema["$defs"]["riskItem"]

        self.assertEqual("number", violation["properties"]["metricValue"]["type"])
        self.assertEqual("number", violation["properties"]["threshold"]["type"])
        self.assertEqual("number", risk_item["properties"]["value"]["type"])
        violation_rule_ids = violation["properties"]["ruleId"]["enum"]
        self.assertEqual(12, len(violation_rule_ids))
        self.assertNotIn("data_freshness_guard", violation_rule_ids)
        self.assertNotIn("ad_leading_room_guard", violation_rule_ids)

        hold = load_json_bytes_strict(
            self.outputs["contracts/examples/risk_decision.hold.valid.json"],
            source="hold fixture",
        )
        self.assertEqual("HOLD", hold["decision"])
        self.assertFalse(hold["canSubmitOrder"])
        self.assertEqual([], hold["violations"])
        self.assertGreaterEqual(len(hold["issues"]), 1)
        self.assertIn(
            "NEWS_EVIDENCE_UNAVAILABLE",
            self.schema["$defs"]["issue"]["properties"]["code"]["enum"],
        )

    def test_optional_warning_and_abstention_pair_is_bidirectional(self) -> None:
        paths = (
            "contracts/examples/invalid/risk_decision.warning-without-abstention.invalid.json",
            "contracts/examples/invalid/risk_decision.abstention-without-warning.invalid.json",
        )

        for path in paths:
            payload = load_json_bytes_strict(self.outputs[path], source=path)
            with self.subTest(path=path):
                self.validator.validate(payload)
                with self.assertRaises(ContractValidationError):
                    validate_risk_decision_semantics(payload, self.catalog)

    def test_same_rule_and_code_require_source_component_total_order(self) -> None:
        payload = load_json_bytes_strict(
            self.outputs["contracts/examples/risk_decision.allow.valid.json"],
            source="allow fixture",
        )
        payload["decision"] = "WARN"
        payload["warnings"] = [
            {
                "code": "OPTIONAL_EVIDENCE_MISSING",
                "message": "Optional evidence was not used for this evaluation.",
                "source": component,
                "ruleId": "data_freshness_guard",
            }
            for component in ("GBM", "BSM")
        ]
        payload["abstentions"] = [
            {
                "code": "OPTIONAL_EVIDENCE_MISSING",
                "component": component,
                "disposition": "ABSTAIN",
                "message": "The component did not produce threshold evidence.",
                "ruleId": "data_freshness_guard",
            }
            for component in ("BSM", "GBM")
        ]

        self.validator.validate(payload)
        with self.assertRaises(ContractValidationError):
            validate_risk_decision_semantics(payload, self.catalog)

    def test_null_risk_item_is_rejected_by_the_wire_schema(self) -> None:
        path = "contracts/examples/invalid/risk_decision.null-risk-item.invalid.json"
        payload = load_json_bytes_strict(self.outputs[path], source=path)

        self.assertTrue(list(self.validator.iter_errors(payload)))

    def test_pairwise_precedence_fixtures_lock_the_approved_action_order(self) -> None:
        expected = {
            "contracts/examples/risk_decision.precedence-block-hold.valid.json": "BLOCK",
            "contracts/examples/risk_decision.precedence-block-warn.valid.json": "BLOCK",
            "contracts/examples/risk_decision.precedence-hold-warn.valid.json": "HOLD",
            "contracts/examples/risk_decision.precedence-warn-na.valid.json": "WARN",
            "contracts/examples/risk_decision.precedence-pass-na.valid.json": "ALLOW",
        }

        for path, decision in expected.items():
            payload = load_json_bytes_strict(self.outputs[path], source=path)
            with self.subTest(path=path):
                self.assertEqual(decision, payload["decision"])
                validate_risk_decision_semantics(payload, self.catalog)

    def test_wire_schema_rejects_warn_action_with_block_violation(self) -> None:
        payload = load_json_bytes_strict(
            self.outputs[
                "contracts/examples/risk_decision.precedence-block-warn.valid.json"
            ],
            source="block precedence fixture",
        )
        payload["decision"] = "WARN"
        payload["canSubmitOrder"] = True

        self.assertTrue(list(self.validator.iter_errors(payload)))

    def test_wire_schema_locks_system_rule_severity_to_catalog(self) -> None:
        downgrade = load_json_bytes_strict(
            self.outputs["contracts/examples/risk_decision.block.valid.json"],
            source="block fixture",
        )
        downgrade["violations"][0]["ruleId"] = "high_volatility_guard"
        downgrade["violations"][0]["severity"] = "WARN"
        downgrade["decision"] = "WARN"
        downgrade["canSubmitOrder"] = True

        upgrade = load_json_bytes_strict(
            self.outputs["contracts/examples/risk_decision.warn.valid.json"],
            source="warn fixture",
        )
        upgrade["violations"][0]["severity"] = "BLOCK"
        upgrade["decision"] = "BLOCK"
        upgrade["canSubmitOrder"] = False

        for payload in (downgrade, upgrade):
            with self.subTest(
                rule_id=payload["violations"][0]["ruleId"],
                severity=payload["violations"][0]["severity"],
            ):
                self.assertTrue(list(self.validator.iter_errors(payload)))

    def test_disclosure_requiredness_changes_only_missing_evidence_disposition(self) -> None:
        optional = load_json_bytes_strict(
            self.outputs[
                "contracts/examples/risk_decision.optional-disclosure-missing.valid.json"
            ],
            source="optional disclosure fixture",
        )
        required = load_json_bytes_strict(
            self.outputs[
                "contracts/examples/risk_decision.required-disclosure-missing.valid.json"
            ],
            source="required disclosure fixture",
        )

        self.assertEqual("WARN", optional["decision"])
        self.assertEqual([], optional["issues"])
        self.assertEqual(1, len(optional["warnings"]))
        self.assertEqual(1, len(optional["abstentions"]))

        self.assertEqual("HOLD", required["decision"])
        self.assertEqual(1, len(required["issues"]))
        self.assertEqual([], required["warnings"])
        self.assertEqual([], required["abstentions"])


class S22HashAndBuildContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)
        cls.outputs = generate_outputs(cls.catalog)

    def test_hash_vector_is_exact_and_uses_separate_semantic_and_artifact_hashes(self) -> None:
        vector = load_json_bytes_strict(
            self.outputs["contracts/examples/s2-2-hash-vector.valid.json"],
            source="hash vector",
        )

        self.assertEqual("HASH-CANONICALIZATION-S22-V1", vector["canonicalizationId"])
        self.assertEqual(
            "0bcb5986ed326a7dbf08010c503e2c895a39e66c970544375feb4812c7321e5d",
            vector["semanticInputHash"],
        )
        self.assertEqual(
            "da2773a7d012377d241fc68b107417666a6002c008c83202a7b11119095078b7",
            vector["snapshotArtifactHash"],
        )
        self.assertEqual(
            vector["semanticInputCanonicalJson"].encode("utf-8"),
            hash_canonical_bytes(vector["semanticInput"]),
        )
        self.assertEqual(
            vector["snapshotArtifactCanonicalJson"].encode("utf-8"),
            hash_canonical_bytes(vector["snapshotArtifact"]),
        )

    def test_hash_vector_covers_full_snapshot_and_every_decision_changing_input(self) -> None:
        vector = load_json_bytes_strict(
            self.outputs["contracts/examples/s2-2-hash-vector.valid.json"],
            source="hash vector",
        )
        semantic = vector["semanticInput"]
        artifact = vector["snapshotArtifact"]

        self.assertEqual(
            {
                "actorUserId",
                "disclosureEvidence",
                "evaluationAsOf",
                "metrics",
                "observedOptionalComponentEvidence",
                "orderIntent",
                "portfolio",
                "principle",
                "provenanceRefs",
                "readinessPolicyVersion",
                "requestedOptionalComponents",
                "snapshotSchemaVersion",
                "systemRuleCatalogVersion",
            },
            set(semantic),
        )
        self.assertEqual(
            {"evaluationId", "retrievedAt"},
            set(artifact) - set(semantic),
        )
        self.assertEqual("LIMIT", semantic["orderIntent"]["orderType"])
        self.assertEqual("50000", semantic["orderIntent"]["limitPrice"])
        asset_weight = next(
            metric for metric in semantic["metrics"] if metric["metric"] == "asset_weight"
        )
        self.assertEqual(
            {
                "availability",
                "declaredScale",
                "freshUntil",
                "metric",
                "observedAt",
                "source",
                "sourceRef",
                "sourceVersion",
                "unit",
                "value",
            },
            set(asset_weight),
        )
        self.assertEqual(
            {
                "available",
                "completeness",
                "componentId",
                "evidenceVersion",
                "reasonCode",
                "sourceRefs",
            },
            set(semantic["observedOptionalComponentEvidence"][0]),
        )
        self.assertEqual(
            "s1.2-v1",
            semantic["observedOptionalComponentEvidence"][0]["evidenceVersion"],
        )
        self.assertEqual(
            "COMPLETE",
            semantic["observedOptionalComponentEvidence"][0]["completeness"],
        )

    def test_ci_checks_s21_before_s22_and_gradle_packages_exact_catalog(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/contracts-ci.yml").read_text(encoding="utf-8")
        s21 = "python contracts/generate_principle_contracts.py --check"
        s22 = "python contracts/generate_s2_2_contracts.py --check"
        self.assertIn(s21, workflow)
        self.assertIn(s22, workflow)
        self.assertLess(workflow.index(s21), workflow.index(s22))

        build = (
            REPO_ROOT / "workspaces/decision-platform/spring-api/build.gradle.kts"
        ).read_text(encoding="utf-8")
        self.assertIn("s2-2-system-rule-catalog.v1.json", build)
        self.assertIn('into("contracts")', build)


if __name__ == "__main__":
    unittest.main()
