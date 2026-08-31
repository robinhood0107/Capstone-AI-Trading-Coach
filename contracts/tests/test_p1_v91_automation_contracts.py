from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_p1_v91_automation_contracts import (
    ADDITIVE_OPENAPI_PATH,
    BLOCKERS,
    PRESETS,
    ROOT,
    SCHEMA_IDS,
    build_outputs,
    build_schemas,
    generate,
    load_catalog,
    validate_policy_semantics,
)
from contracts.generate_principle_contracts import (
    ContractValidationError,
    canonical_json_bytes,
)
from contracts.verify_p1_rag_v2_openapi_transition import (
    project_pre_rag_v2_openapi,
)
from contracts.verify_p1_strong_llm_settings_openapi_transition import (
    ADDITIVE_PATH as STRONG_LLM_ADDITIVE_PATH,
    strip_strong_llm_settings,
)
from contracts.verify_p1_v91_automation_openapi_transition import (
    ADDITIVE_SCHEMA_NAMES,
    HISTORICAL_ROOT_56_SHA256,
    operations,
    project_pre_v91_openapi,
)
from contracts.verify_p1_v3_automation_openapi_transition import project_pre_v3_openapi


class P1V91AutomationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = build_schemas()
        cls.policy = {
            "contractId": "automation-policy.v1",
            "policyId": "auto_pol_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "version": 1,
            "presetId": "balanced",
            "capitalLimitKrw": 1_000_000,
            "stopLossBps": 500,
            "takeProfitBps": 1000,
            "maxOpenPositions": 5,
            "maxNewOrdersPerSession": 1,
            "evaluationTimeKst": "09:30",
            "buyCutoffTimeKst": "09:40",
            "cancelTimeKst": "15:20",
            "createdAt": "2026-08-27T09:00:00+09:00",
            "updatedAt": "2026-08-27T09:00:00+09:00",
        }

    def test_generated_artifacts_are_deterministic_and_checked_in(self) -> None:
        self.assertEqual(build_outputs(), build_outputs())
        self.assertEqual(tuple(self.schemas), SCHEMA_IDS)
        generate(check=True)

    def test_catalog_locks_presets_limits_and_exact_five_operations(self) -> None:
        catalog = load_catalog()
        self.assertEqual(
            PRESETS,
            {
                item["presetId"]: (item["stopLossBps"], item["takeProfitBps"])
                for item in catalog["presets"]
            },
        )
        self.assertEqual(list(BLOCKERS), catalog["blockers"])
        self.assertEqual(5, catalog["execution"]["maxOpenPositions"])
        self.assertEqual(1, catalog["execution"]["maxNewOrdersPerSession"])
        self.assertEqual(5, len(catalog["operations"]))
        self.assertEqual(
            {
                ("GET", "/api/v2/automation/status", "getAutomationStatusV2"),
                ("PUT", "/api/v2/automation/policy", "putAutomationPolicyV2"),
                ("POST", "/api/v2/automation/arm", "armAutomationV2"),
                ("GET", "/api/v2/automation/runs", "listAutomationRunsV2"),
                ("GET", "/api/v2/automation/positions", "listAutomationPositionsV2"),
            },
            {
                (item["method"], item["path"], item["operationId"])
                for item in catalog["operations"]
            },
        )

    def test_closed_schemas_accept_exact_fixtures_and_reject_unknown_fields(
        self,
    ) -> None:
        fixtures = {
            "automation-policy.v1": self.policy,
            "automation-status.v2": {
                "contractId": "automation-status.v2",
                "controlState": "DISARMED",
                "projectionState": "DISARMED",
                "controlVersion": 1,
                "brokerageMode": "KIS_MOCK",
                "accountId": "acct_cccccccccccccccccccccccccccccccc",
                "policy": self.policy,
                "killSwitchActive": False,
                "certificationStatus": "REQUIRED",
                "openPositionCount": 0,
                "unresolvedReconciliation": False,
                "canArm": False,
                "blockers": ["BLOCKED_INCOMPLETE_RISK_BALANCE"],
            },
            "automation-run.v2": {
                "contractId": "automation-run.v2",
                "runId": "auto_run_variable_0001",
                "sessionDate": "2026-08-27",
                "state": "ORDER_SIZING",
                "brokerageMode": "KIS_MOCK",
                "policyId": self.policy["policyId"],
                "policyVersion": 1,
                "selectedSymbol": "005930",
                "selectedSide": "BUY",
                "orderQuantity": 3,
                "filledQuantity": 0,
                "leavesQuantity": 3,
                "limitPriceKrw": 70000,
                "estimatedAmountKrw": 210000,
                "exitReason": None,
                "physicalSubmitCount": 0,
                "providerCalls": 0,
                "startedAt": "2026-08-27T09:30:00+09:00",
                "updatedAt": "2026-08-27T09:30:01+09:00",
            },
            "automation-position.v2": {
                "contractId": "automation-position.v2",
                "positionId": "auto_pos_variable_0001",
                "accountId": "acct_cccccccccccccccccccccccccccccccc",
                "symbol": "005930",
                "quantity": 3,
                "entryAverageFillPriceKrw": 70000,
                "entrySession": "2026-08-27",
                "expirySession": "2026-09-03",
                "policyId": self.policy["policyId"],
                "policyVersion": 1,
                "stopLossBps": 500,
                "takeProfitBps": 1000,
                "status": "OPEN",
                "exitReason": None,
                "exitAverageFillPriceKrw": None,
                "realizedPnlKrw": None,
                "botOwned": True,
                "shortAllowed": False,
                "createdAt": "2026-08-27T09:31:00+09:00",
                "closedAt": None,
            },
        }
        for schema_id, fixture in fixtures.items():
            with self.subTest(schema_id=schema_id):
                validator = Draft202012Validator(
                    self.schemas[schema_id], format_checker=FormatChecker()
                )
                self.assertEqual([], list(validator.iter_errors(fixture)))
                invalid = copy.deepcopy(fixture)
                invalid["unexpected"] = True
                self.assertNotEqual([], list(validator.iter_errors(invalid)))

    def test_policy_cross_field_and_named_preset_semantics_fail_closed(self) -> None:
        validate_policy_semantics(self.policy)
        invalid = copy.deepcopy(self.policy)
        invalid["takeProfitBps"] = invalid["stopLossBps"]
        with self.assertRaises(ContractValidationError):
            validate_policy_semantics(invalid)
        invalid = copy.deepcopy(self.policy)
        invalid["stopLossBps"] = 600
        with self.assertRaises(ContractValidationError):
            validate_policy_semantics(invalid)
        invalid["presetId"] = "custom"
        validate_policy_semantics(invalid)

    def test_exact_five_projection_restores_byte_stable_exact_56(self) -> None:
        root = json.loads(
            (ROOT / "contracts/openapi/openapi.json").read_text(encoding="utf-8")
        )
        additive = json.loads(ADDITIVE_OPENAPI_PATH.read_text(encoding="utf-8"))
        # RAG v2 공개 표면이 앞단에 더해졌다. 그 단계를 먼저 걷어 역사적 exact-61을 복원한다.
        rag_v2_additive = json.loads(
            (ROOT / "contracts/openapi/p1-rag-v2-public.v1.openapi.json").read_text(
                encoding="utf-8"
            )
        )
        # Strong LLM 설정 표면이 맨 앞에 더해졌다. 그 층을 먼저 걷어 exact-68로 내린다.
        strong_llm_additive = json.loads(STRONG_LLM_ADDITIVE_PATH.read_text(encoding="utf-8"))
        v3_additive = json.loads(
            (ROOT / "contracts/openapi/p1-automation-v3.v1.openapi.json").read_text(encoding="utf-8")
        )
        self.assertEqual(75, len(operations(root)))
        root = project_pre_v3_openapi(root, v3_additive)
        self.assertEqual(69, len(operations(root)))
        root = strip_strong_llm_settings(root, strong_llm_additive)
        self.assertEqual(68, len(operations(root)))
        root = project_pre_rag_v2_openapi(root, rag_v2_additive)
        self.assertEqual(61, len(operations(root)))
        projected = project_pre_v91_openapi(root, additive)
        self.assertEqual(56, len(operations(projected)))
        self.assertEqual(
            HISTORICAL_ROOT_56_SHA256,
            hashlib.sha256(canonical_json_bytes(projected)).hexdigest(),
        )
        self.assertEqual(
            ADDITIVE_SCHEMA_NAMES,
            frozenset(additive["components"]["schemas"]),
        )

    def test_v1_team_a_and_automation_contract_bytes_remain_unchanged(self) -> None:
        expected = {
            "contracts/catalogs/p1-team-a-acceptance.v1.json": "75efdec876c2c6b3388ae08f4b478f6564a97b54cb6a1da41766757399173dd8",
            "workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v1.ts": "869b5f6bfb069ca015037461d9706cd8e0a65f5dea688fe83aea022f3281c584",
            "contracts/schemas/automation-control.v1.schema.json": "e649e3265187ec6dbba6aa6db394abc31075f4a4df2f65da1bfdeb9cd64640e5",
            "contracts/schemas/automation-run.v1.schema.json": "8767bea42e403e858c1cdf46784158c39430be324611932e68e4918db3766d99",
            "contracts/schemas/automation-position.v1.schema.json": "5187a4c76fb81a77da39de62f81cf07d2a14c68214ccfc25102adecc720eae23",
        }
        for relative, digest in expected.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    digest, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                )

    def test_spring_status_emits_only_contract_blocker_codes(self) -> None:
        repository = (
            ROOT
            / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/automation/JdbcAutomationRepository.kt"
        ).read_text(encoding="utf-8")
        v2_repository = repository.split("private fun readStatusV3", maxsplit=1)[0]
        emitted = set(re.findall(r'add\("([A-Z][A-Z0-9_]+)"\)', v2_repository))
        self.assertTrue(emitted)
        self.assertEqual(set(), emitted.difference(BLOCKERS))


if __name__ == "__main__":
    unittest.main()
