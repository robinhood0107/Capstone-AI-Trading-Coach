from __future__ import annotations

import copy
import json
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s7_s8_contracts import (
    BASE_TOPICS,
    PUBLIC_PATHS,
    ROOT,
    SCHEMA_IDS,
    _catalog,
    _fixtures,
    _negative_fixtures,
    _openapi,
    build_outputs,
    build_schemas,
    validate_semantics,
)


class S7S8ContractTest(unittest.TestCase):
    def test_generated_artifacts_are_deterministic_and_checked_in(self) -> None:
        for path, expected in build_outputs().items():
            self.assertTrue(path.is_file(), path)
            self.assertEqual(expected, path.read_bytes(), path)

    def test_positive_and_negative_fixtures_are_closed(self) -> None:
        schemas = build_schemas()
        positives = _fixtures()
        negatives = _negative_fixtures(positives)
        self.assertEqual(SCHEMA_IDS, tuple(schemas))
        for schema_id in SCHEMA_IDS:
            validator = Draft202012Validator(schemas[schema_id], format_checker=FormatChecker())
            self.assertEqual([], list(validator.iter_errors(positives[schema_id])))
            validate_semantics(schema_id, positives[schema_id])
            self.assertTrue(list(validator.iter_errors(negatives[schema_id])))

    def test_catalog_materializes_exact_topics_and_no_cross_market(self) -> None:
        catalog = _catalog()
        expected = [topic for base in BASE_TOPICS for topic in (base, base.replace(".v1", ".retry.v1"), base.replace(".v1", ".dlq.v1"))]
        self.assertEqual(expected, catalog["topics"])
        self.assertEqual([], catalog["crossMarketRuntimePaths"])
        self.assertFalse(catalog["performanceClaimAllowed"])

    def test_openapi_has_exact_eight_bearer_paths(self) -> None:
        document = _openapi(build_schemas())
        self.assertEqual(PUBLIC_PATHS, tuple(document["paths"]))
        for path_item in document["paths"].values():
            self.assertEqual([{"bearerAuth": []}], path_item["get"]["security"])
        encoded = json.dumps(document, sort_keys=True)
        self.assertNotIn("cross-market", encoded)
        self.assertNotIn("WARN_ONLY", encoded)

    def test_synthetic_dashboard_namespace_is_semantic(self) -> None:
        fixture = copy.deepcopy(_fixtures()["dashboard-backtest.v1"])
        fixture["data"]["view"]["runId"] = "run_not_demo_00000001"
        with self.assertRaises(ContractValidationError):
            validate_semantics("dashboard-backtest.v1", fixture)

    def test_demo_and_user_test_kit_stay_bounded_and_provider_free(self) -> None:
        seed = _fixtures()["s8-demo-seed.v1"]
        self.assertEqual(["ALLOW", "WARN", "BLOCK", "HOLD"], [item["expectedOutcome"] for item in seed["scenarios"]])
        self.assertEqual(0, seed["providerCalls"])
        self.assertEqual(0, seed["liveAccountCalls"])
        self.assertEqual(0, seed["liveOrderCalls"])
        self.assertEqual("RETIRED_NOT_APPLICABLE", seed["crossMarketCapability"])
        questionnaire = json.loads(
            (ROOT / "docs/decision-platform/s8-user-test-kit/questionnaire.v1.json").read_text(encoding="utf-8")
        )
        self.assertFalse(questionnaire["freeTextEnabled"])
        self.assertNotIn("FREE_TEXT", {task["responseType"] for task in questionnaire["tasks"]})
        manifest = json.loads(
            (ROOT / "docs/decision-platform/s8-user-test-kit/evidence-manifest.v1.json").read_text(encoding="utf-8")
        )
        self.assertFalse(manifest["containsParticipantResults"])
        self.assertFalse(manifest["containsSecretsOrPii"])
        self.assertEqual(7, len(manifest["folders"]))

    def test_contracts_ci_runs_generator_check(self) -> None:
        workflow = (ROOT / ".github/workflows/contracts-ci.yml").read_text(encoding="utf-8")
        self.assertIn("python contracts/generate_s7_s8_contracts.py --check", workflow)
        self.assertIn("python contracts/generate_async_worker_proto.py --check", workflow)


if __name__ == "__main__":
    unittest.main()
