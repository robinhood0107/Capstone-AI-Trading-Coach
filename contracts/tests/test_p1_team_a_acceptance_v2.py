from __future__ import annotations

import json
import unittest

from contracts.generate_p1_team_a_acceptance_v2 import (
    CATALOG_PATH,
    CLIENT_PATH,
    EXPECTED_OPERATIONS_V2,
    OPENAPI_PATH,
    build_artifacts,
    main,
)


class P1TeamAAcceptanceV2ContractTest(unittest.TestCase):
    def test_generated_exact_38_artifacts_are_checked_in(self) -> None:
        self.assertEqual(0, main(["--check"]))
        expected = build_artifacts(OPENAPI_PATH.read_bytes())
        self.assertEqual(expected[CATALOG_PATH], CATALOG_PATH.read_bytes())
        self.assertEqual(expected[CLIENT_PATH], CLIENT_PATH.read_bytes())

    def test_catalog_is_exact_38_subset_of_exact_68(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(38, catalog["acceptanceOperationCount"])
        self.assertEqual(68, catalog["rootOpenApi"]["operationCount"])
        self.assertEqual(33, catalog["preservedV1"]["operationCount"])
        self.assertEqual(
            15,
            sum(item["category"] == "CURRENT_SCREEN" for item in catalog["operations"]),
        )
        self.assertEqual(
            23,
            sum(item["category"] == "TEAM_A_REQUIRED" for item in catalog["operations"]),
        )
        identities = {(item["method"], item["path"]) for item in catalog["operations"]}
        self.assertEqual(38, len(identities))
        arm = next(item for item in catalog["operations"] if item["operationId"] == "armAutomationV2")
        self.assertEqual([409], arm["expectedStatuses"])
        self.assertEqual(38, len(EXPECTED_OPERATIONS_V2))

    def test_generated_client_is_same_origin_and_accepts_expected_arm_blocker(self) -> None:
        client = CLIENT_PATH.read_text(encoding="utf-8")
        self.assertEqual(38, client.count("expectedStatuses:"))
        self.assertIn(
            'armAutomationV2: { method: "POST", path: "/api/v2/automation/arm", expectedStatuses: [409] }',
            client,
        )
        self.assertIn("same-origin or loopback acceptance only", client)
        self.assertNotIn("console.log", client)


if __name__ == "__main__":
    unittest.main()
