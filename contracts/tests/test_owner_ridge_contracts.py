"""새 API surface만 바뀌며 과거 OpenAPI는 원형 복원이 가능해야 한다."""
import copy
import json
import unittest
from contracts.generate_owner_ridge_contracts import PREVIOUS, ROOT, project_previous
from contracts.historical_openapi_projection import project_historical_root


class OwnerRidgeContractTest(unittest.TestCase):
    def test_connected_kis_readiness_and_failure_stop_are_in_the_current_contract(self):
        from contracts.generate_owner_ridge_contracts import build

        components = build()["components"]["schemas"]
        status = components["AutomationStatusV3"]
        self.assertEqual(
            {"type": "integer", "minimum": 1, "maximum": 20},
            components["AutomationPolicyV3"]["properties"]["maxOpenPositions"],
        )
        self.assertEqual(
            {
                "ownerConnectionReady",
                "orderPathVerified",
                "orderFailureCode",
                "unlinkedOpenPositionCount",
                "unresolvedUnlinkedOrderCount",
                "unresolvedUnlinkedRunCount",
                "quarantinedPositionCount",
                "historicalPaperOpenPositionCount",
                "historicalPaperClosedPositionCount",
                "historicalPaperRunCount",
            },
            {
                name
                for name in (
                    "ownerConnectionReady",
                    "orderPathVerified",
                    "orderFailureCode",
                    "unlinkedOpenPositionCount",
                    "unresolvedUnlinkedOrderCount",
                    "unresolvedUnlinkedRunCount",
                    "quarantinedPositionCount",
                    "historicalPaperOpenPositionCount",
                    "historicalPaperClosedPositionCount",
                    "historicalPaperRunCount",
                )
                if name in status["properties"] and name in status["required"]
            },
        )
        self.assertIn(
            "ACCOUNT_HISTORY_UNLINKED",
            status["properties"]["blockers"]["items"]["enum"],
        )
        self.assertNotIn("maximum", status["properties"]["openPositionCount"])
        self.assertEqual(
            {"KIS_ORDER_REJECTED", "KIS_ORDER_RESULT_UNCERTAIN", None},
            set(status["properties"]["orderFailureCode"]["enum"]),
        )
        owner = components["OwnerKillSwitchDto"]
        self.assertIn(
            "BROKERAGE_FAILURE_STOP", owner["properties"]["reasonClass"]["enum"]
        )
        login = components["LoginRequest"]["properties"]["username"]
        self.assertEqual(254, login["maxLength"])

    def test_previous_root_is_preserved_exactly(self):
        current = json.loads((ROOT / 'contracts/openapi/openapi.json').read_text())
        # PREVIOUS 는 한 세대의 바이트다. 그 뒤에 내린 제품 결정(매수 마감 09:40->14:30
        # 등)을 되돌린 뒤 비교해야 비교가 성립한다.
        self.assertEqual(
            json.loads(PREVIOUS.read_text()),
            project_historical_root(project_previous(current)),
        )

    def test_new_owner_surface_cannot_hide_behind_historical_projection(self):
        current = json.loads((ROOT / 'contracts/openapi/openapi.json').read_text())
        changed = copy.deepcopy(current)
        changed['paths']['/api/v1/risk/kill-switch']['post']['x-required-role'] = 'USER'
        with self.assertRaises(ValueError):
            project_previous(changed)
