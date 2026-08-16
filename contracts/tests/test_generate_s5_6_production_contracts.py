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
            invalid_path = next(
                path for path in self.artifacts if path.startswith(
                    f"contracts/examples/invalid/{name}."
                )
            )
            self.assertTrue(list(validator.iter_errors(self.artifacts[invalid_path])))

    def test_temporal_receipt_does_not_accept_fabricated_provider_revision(self) -> None:
        payload = copy.deepcopy(
            self.artifacts["contracts/examples/s5-pit-source-bundle-v1.valid.json"]
        )
        payload["chunks"][0]["receipt"]["providerRevision"] = "sha-is-not-a-revision"
        validator = Draft202012Validator(self.artifacts[SOURCE_SCHEMA])
        self.assertTrue(list(validator.iter_errors(payload)))

    def test_policy_caps_and_no_go_boundaries_are_exact(self) -> None:
        catalog = self.artifacts[CATALOG]
        self.assertEqual(6446, catalog["bootstrap"]["totalMaxPhysicalCalls"])
        self.assertEqual(0, catalog["bootstrap"]["retry"])
        self.assertEqual(0, catalog["runtime"]["riskDecisionWiring"])
        self.assertEqual(0, catalog["runtime"]["orderWiring"])
        self.assertFalse(catalog["strictProviderPITClaim"])

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
