from __future__ import annotations

import json
import unittest

from contracts.generate_p1_team_a_acceptance import (
    BADGE_PATH,
    CATALOG_PATH,
    CLIENT_PATH,
    EXPECTED_OPERATIONS,
    OPENAPI_PATH,
    ContractError,
    build_artifacts,
    main,
)


class P1TeamAAcceptanceContractTest(unittest.TestCase):
    def test_generated_artifacts_are_deterministic_and_checked_in(self) -> None:
        self.assertEqual(0, main(["--check"]))
        expected = build_artifacts(OPENAPI_PATH.read_bytes())
        self.assertEqual(expected[CATALOG_PATH], CATALOG_PATH.read_bytes())
        self.assertEqual(expected[BADGE_PATH], BADGE_PATH.read_bytes())
        self.assertEqual(expected[CLIENT_PATH], CLIENT_PATH.read_bytes())

    def test_catalog_is_exact_33_subset_of_exact_56(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(33, catalog["acceptanceOperationCount"])
        self.assertEqual(56, catalog["rootOpenApi"]["operationCount"])
        self.assertEqual(
            15, sum(item["category"] == "CURRENT_SCREEN" for item in catalog["operations"])
        )
        self.assertEqual(
            18, sum(item["category"] == "TEAM_A_REQUIRED" for item in catalog["operations"])
        )
        identities = {(item["method"], item["path"]) for item in catalog["operations"]}
        operation_ids = {item["operationId"] for item in catalog["operations"]}
        self.assertEqual(33, len(identities))
        self.assertEqual(33, len(operation_ids))
        self.assertNotIn(("GET", "/error"), identities)
        buyable = next(item for item in catalog["operations"] if item["operationId"] == "getMockBuyable")
        self.assertEqual(
            {("query", "price"), ("query", "symbol")},
            {(item["in"], item["name"]) for item in buyable["clientParameterOverrides"]},
        )
        self.assertEqual("./capstone team-a acceptance", catalog["runner"])
        self.assertFalse(catalog["fixtureBoundary"]["frontendFakeProductionResponse"])

    def test_badges_cannot_promote_synthetic_or_lightgbm(self) -> None:
        badges = json.loads(BADGE_PATH.read_text(encoding="utf-8"))
        synthetic = badges["teamBEvidence"]["SYNTHETIC_GOLDEN"]
        self.assertTrue(synthetic["fixture"])
        self.assertFalse(synthetic["promotableToReal"])
        self.assertFalse(synthetic["performanceClaimAllowed"])
        self.assertEqual("RESEARCH_ONLY", badges["modelBadges"]["LIGHTGBM"]["label"])
        self.assertEqual("NONE", badges["modelBadges"]["LIGHTGBM"]["orderAuthority"])
        self.assertFalse(badges["brokerageModes"]["KIS_MOCK"]["automaticFallbackToInternalPaper"])

    def test_operation_id_drift_fails_closed(self) -> None:
        openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        _, method, path, _, _ = EXPECTED_OPERATIONS[0]
        openapi["paths"][path][method.lower()]["operationId"] = "driftedLogin"
        with self.assertRaises(ContractError):
            build_artifacts((json.dumps(openapi) + "\n").encode())

    def test_generated_client_is_same_origin_and_secret_safe(self) -> None:
        client = CLIENT_PATH.read_text(encoding="utf-8")
        self.assertIn("same-origin or loopback acceptance only", client)
        self.assertIn("export interface TeamARequests", client)
        self.assertIn("export interface TeamAResponses", client)
        self.assertEqual(33, client.count("expectedStatuses:"))
        self.assertNotIn("password =", client.lower())
        self.assertNotIn("console.log", client)


if __name__ == "__main__":
    unittest.main()
