from __future__ import annotations

import json
import unittest

from contracts.generate_p1_team_a_acceptance_v3 import (
    CATALOG_PATH,
    CLIENT_PATH,
    EXPECTED_OPERATIONS_V3,
    OPENAPI_PATH,
    build_artifacts,
    main,
)


class P1TeamAAcceptanceV3ContractTest(unittest.TestCase):
    def test_generated_exact_45_artifacts_are_checked_in(self) -> None:
        self.assertEqual(0, main(["--check"]))
        expected = build_artifacts(OPENAPI_PATH.read_bytes())
        self.assertEqual(expected[CATALOG_PATH], CATALOG_PATH.read_bytes())
        self.assertEqual(expected[CLIENT_PATH], CLIENT_PATH.read_bytes())

    def test_catalog_is_exact_45_subset_of_exact_75_and_preserves_v2(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(45, catalog["acceptanceOperationCount"])
        self.assertEqual(75, catalog["rootOpenApi"]["operationCount"])
        self.assertEqual(38, catalog["preservedV2"]["operationCount"])
        self.assertEqual(45, len(EXPECTED_OPERATIONS_V3))
        identities = {(item["method"], item["path"]) for item in catalog["operations"]}
        self.assertEqual(45, len(identities))
        self.assertEqual(
            6,
            sum(item["path"].startswith("/api/v3/automation") for item in catalog["operations"]),
        )
        self.assertIn(
            ("PUT", "/api/v2/strong-llm/settings"),
            identities,
        )

    def test_generated_client_is_same_origin_and_contains_v3_detail(self) -> None:
        client = CLIENT_PATH.read_text(encoding="utf-8")
        self.assertEqual(45, client.count("expectedStatuses:"))
        self.assertIn("getAutomationRunV3", client)
        self.assertIn("same-origin or loopback acceptance only", client)
        self.assertNotIn("console.log", client)


if __name__ == "__main__":
    unittest.main()
