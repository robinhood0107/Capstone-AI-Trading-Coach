from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_s5_6_production_contracts import (
    CATALOG,
    FEATURE_SCHEMA,
    SOURCE_SCHEMA,
    build_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]


class S56ProductionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.artifacts = build_artifacts()

    def test_closed_positive_and_negative_fixtures(self) -> None:
        pairs = (
            (SOURCE_SCHEMA, "s5-pit-source-bundle-v1"),
            (FEATURE_SCHEMA, "s5-feature-bundle-v2"),
        )
        for schema_path, name in pairs:
            validator = Draft202012Validator(
                self.artifacts[schema_path], format_checker=FormatChecker()
            )
            valid = self.artifacts[f"contracts/examples/{name}.valid.json"]
            self.assertEqual([], list(validator.iter_errors(valid)))
            invalid_paths = sorted(
                path
                for path in self.artifacts
                if path.startswith(f"contracts/examples/invalid/{name}.")
            )
            self.assertTrue(invalid_paths)
            for invalid_path in invalid_paths:
                with self.subTest(invalid_path=invalid_path):
                    self.assertTrue(list(validator.iter_errors(self.artifacts[invalid_path])))

    def test_source_receipt_provider_is_bound_to_chunk_provider(self) -> None:
        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-pit-source-bundle-v1.valid.json"]
        )
        payload["chunks"][0]["receipt"]["sourceId"] = "ECOS"
        validator = Draft202012Validator(self.artifacts[SOURCE_SCHEMA])
        self.assertTrue(list(validator.iter_errors(payload)))

    def test_feature_column_order_and_names_are_exact(self) -> None:
        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-feature-bundle-v2.valid.json"]
        )
        payload["featureColumns"][0] = "cross_market_score"
        validator = Draft202012Validator(self.artifacts[FEATURE_SCHEMA])
        self.assertTrue(list(validator.iter_errors(payload)))

    def test_corrected_calendar_feature_fixture_is_separate_and_exact(self) -> None:
        payload = self.artifacts[
            "contracts/examples/s5-feature-bundle-v2.corrected-calendar.valid.json"
        ]
        validator = Draft202012Validator(
            self.artifacts[FEATURE_SCHEMA], format_checker=FormatChecker()
        )
        self.assertEqual([], list(validator.iter_errors(payload)))
        provenance = payload["provenance"]
        self.assertEqual("2022-03-29", provenance["rawSessionStart"])
        self.assertEqual("2026-08-13", provenance["rawSessionEnd"])
        self.assertEqual("2022-06-23", provenance["eligibleSessionStart"])
        self.assertEqual("2026-08-05", provenance["eligibleSessionEnd"])

    def test_temporal_receipt_does_not_accept_fabricated_provider_revision(self) -> None:
        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-pit-source-bundle-v1.valid.json"]
        )
        payload["chunks"][0]["receipt"]["providerRevision"] = "sha-is-not-a-revision"
        validator = Draft202012Validator(self.artifacts[SOURCE_SCHEMA])
        self.assertTrue(list(validator.iter_errors(payload)))

    def test_temporal_receipt_clocks_are_mutually_exclusive(self) -> None:
        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-pit-source-bundle-v1.valid.json"]
        )
        payload["chunks"][0]["receipt"]["providerAvailableAt"] = "2026-08-16T00:00:00Z"
        validator = Draft202012Validator(self.artifacts[SOURCE_SCHEMA])
        self.assertTrue(list(validator.iter_errors(payload)))

    def test_source_operation_is_provider_bound(self) -> None:
        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-pit-source-bundle-v1.valid.json"]
        )
        payload["chunks"][0]["operationId"] = "account-balance"
        payload["chunks"][0]["receipt"]["operationId"] = "account-balance"
        validator = Draft202012Validator(self.artifacts[SOURCE_SCHEMA])
        self.assertTrue(list(validator.iter_errors(payload)))

        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-pit-source-bundle-v1.valid.json"]
        )
        payload["chunks"][0]["sourceId"] = "KRX"
        payload["chunks"][0]["operationId"] = "stk_bydd_trd"
        payload["chunks"][0]["receipt"]["sourceId"] = "KRX"
        payload["chunks"][0]["receipt"]["operationId"] = "kospi_dd_trd"
        self.assertTrue(list(validator.iter_errors(payload)))

    def test_policy_caps_and_no_go_boundaries_are_exact(self) -> None:
        catalog = self.artifacts[CATALOG]
        self.assertEqual(6446, catalog["bootstrap"]["totalMaxPhysicalCalls"])
        self.assertEqual(0, catalog["bootstrap"]["retry"])
        self.assertEqual(0, catalog["runtime"]["riskDecisionWiring"])
        self.assertEqual(0, catalog["runtime"]["orderWiring"])
        self.assertFalse(catalog["strictProviderPITClaim"])
        self.assertEqual(16 * 1024 * 1024, catalog["sourceBundle"]["manifestMaxBytes"])

    def test_existing_s5_contracts_remain_byte_stable(self) -> None:
        paths = (
            "contracts/catalogs/s5-lightgbm-implementation-lock.v1.json",
            "contracts/schemas/signal-v2-runtime-v1.schema.json",
            "contracts/schemas/lightgbm-signal-artifact-v1.schema.json",
        )
        expected = {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
        }
        build_artifacts()
        self.assertEqual(
            expected,
            {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths},
        )


if __name__ == "__main__":
    unittest.main()
