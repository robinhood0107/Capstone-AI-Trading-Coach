from __future__ import annotations

import copy
import hashlib
import json
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_p1_v3_automation_contracts import (
    ADDITIVE_OPENAPI_PATH,
    BLOCKERS,
    HISTORICAL_BYTES,
    PRESETS,
    ROOT,
    SCHEMA_IDS,
    build_outputs,
    build_schemas,
    generate,
    validate_bootstrap_semantics,
    validate_policy_semantics,
    validate_screen_semantics,
)
from contracts.generate_principle_contracts import ContractValidationError
from contracts.verify_p1_v3_automation_openapi_transition import (
    CURRENT_ROOT_69_SHA256,
    merge_v3_openapi,
    operations,
    project_pre_v3_openapi,
)


class P1V3AutomationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = build_schemas()
        cls.policy = {
            "contractId": "automation-policy.v2",
            "policyId": "auto_pol_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "version": 1,
            "presetId": "balanced",
            "capitalLimitKrw": 10_000_000,
            "stopLossBps": 500,
            "takeProfitBps": 1_000,
            "maxHoldingSessions": 60,
            "atrPeriod": 22,
            "atrMultiplierMilli": 3_000,
            "modelSellEnabled": True,
            "maxOpenPositions": 5,
            "maxNewOrdersPerSession": 1,
            "evaluationTimeKst": "09:30",
            "buyCutoffTimeKst": "09:40",
            "cancelTimeKst": "15:20",
            "createdAt": "2026-08-31T18:00:00+09:00",
            "updatedAt": "2026-08-31T18:00:00+09:00",
        }
        cls.evidence = {
            "symbol": "005930",
            "citationId": "cit_005930_1",
            "sourceId": "src_official_kind",
            "sourceType": "OFFICIAL_PRIMARY",
            "sourceEventDate": "2026-08-20",
            "ageWarning": True,
            "uriSha256": "1" * 64,
            "boundedQuote": "삼성전자 관련 공시의 검증된 근거 문장입니다.",
            "quoteSha256": "2" * 64,
            "verified": True,
        }

    def test_generated_artifacts_are_deterministic_and_checked_in(self) -> None:
        self.assertEqual(build_outputs(), build_outputs())
        self.assertEqual(tuple(self.schemas), SCHEMA_IDS)
        generate(check=True)

    def test_catalog_locks_exact_six_operations_presets_and_boundaries(self) -> None:
        catalog = json.loads(
            (ROOT / "contracts/catalogs/p1-automation-policy.v2.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            PRESETS,
            {
                item["presetId"]: (
                    item["stopLossBps"],
                    item["takeProfitBps"],
                    item["maxHoldingSessions"],
                    item["atrPeriod"],
                    item["atrMultiplierMilli"],
                    item["modelSellEnabled"],
                )
                for item in catalog["presets"]
            },
        )
        self.assertEqual(list(BLOCKERS), catalog["blockers"])
        self.assertEqual(6, len(catalog["operations"]))
        self.assertEqual(
            {
                ("GET", "/api/v3/automation/status", "getAutomationStatusV3"),
                ("PUT", "/api/v3/automation/policy", "putAutomationPolicyV3"),
                ("POST", "/api/v3/automation/arm", "armAutomationV3"),
                ("GET", "/api/v3/automation/runs", "listAutomationRunsV3"),
                ("GET", "/api/v3/automation/runs/{runId}", "getAutomationRunV3"),
                ("GET", "/api/v3/automation/positions", "listAutomationPositionsV3"),
            },
            {
                (item["method"], item["path"], item["operationId"])
                for item in catalog["operations"]
            },
        )

    def test_closed_schemas_accept_exact_fixtures_and_reject_unknown_fields(
        self,
    ) -> None:
        screening = {
            "symbol": "005930",
            "status": "AVAILABLE",
            "verdict": "VETO_BUY",
            "score": 0.2,
            "reason": "검증된 공시 근거가 매수 차단을 지지합니다.",
            "evidence": [self.evidence],
        }
        fixtures = {
            "automation-policy.v2": self.policy,
            "automation-candidate-evidence.v1": self.evidence,
            "vertex-news-screen.v2": {
                "contractId": "vertex-news-screen.v2",
                "status": "AVAILABLE",
                "failureReason": None,
                "candidateSetSha256": "3" * 64,
                "inputSha256": "4" * 64,
                "outputSha256": "5" * 64,
                "modelId": "gemini-3.5-flash",
                "promptVersion": "vertex-news-screen-v2",
                "providerCallCount": 1,
                "groundingQueryCount": 1,
                "orderAuthority": "NONE",
                "candidates": [screening],
            },
            "strong-llm-owner-settings.v2": {
                "contractId": "strong-llm-owner-settings.v2",
                "aiJudgementEnabled": False,
                "thinkingLevel": "low",
            },
            "p1-automation-market-bootstrap.v1": {
                "contractId": "p1-automation-market-bootstrap.v1",
                "complete": True,
                "createdAt": "2026-08-31T18:00:00+09:00",
                "membershipMonth": "2026-08",
                "membership": [f"{value:06d}" for value in range(1, 31)] + ["132030"],
                "requestedSessionCount": 1260,
                "firstSessionDate": "2021-07-01",
                "lastSessionDate": "2026-08-31",
                "adjustmentMode": "ADJUSTED",
                "bars": {
                    "relativePath": "bars/automation-bars-v1.parquet",
                    "sha256": "6" * 64,
                    "rowCount": 38_000,
                },
                "providerCaps": {
                    "kisDaily": 403,
                    "kisToken": 1,
                    "krxMembership": 5,
                    "retry": 0,
                },
                "providerPhysicalCalls": {
                    "kisDaily": 400,
                    "kisToken": 1,
                    "krxMembership": 1,
                },
                "rawProviderResponseStored": False,
                "sourcePathPersisted": False,
                "performanceClaimAllowed": False,
                "accountCalls": 0,
                "orderCalls": 0,
                "manifestSha256": "7" * 64,
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

    def test_policy_semantics_lock_presets_custom_and_cross_field_rules(self) -> None:
        validate_policy_semantics(self.policy)
        for field, value in (
            ("takeProfitBps", 500),
            ("atrMultiplierMilli", 3_050),
            ("maxHoldingSessions", 1_261),
        ):
            invalid = copy.deepcopy(self.policy)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ContractValidationError):
                validate_policy_semantics(invalid)
        custom = copy.deepcopy(self.policy)
        custom["maxHoldingSessions"] = 0
        custom["presetId"] = "custom"
        validate_policy_semantics(custom)

    def test_screening_semantics_require_verified_evidence_for_non_neutral_effect(
        self,
    ) -> None:
        available = {
            "contractId": "vertex-news-screen.v2",
            "status": "AVAILABLE",
            "failureReason": None,
            "candidateSetSha256": "3" * 64,
            "inputSha256": "4" * 64,
            "outputSha256": "5" * 64,
            "modelId": "gemini-3.5-flash",
            "promptVersion": "vertex-news-screen-v2",
            "providerCallCount": 1,
            "groundingQueryCount": 1,
            "orderAuthority": "NONE",
            "candidates": [
                {
                    "symbol": "005930",
                    "status": "AVAILABLE",
                    "verdict": "NO_VETO",
                    "score": 0.5,
                    "reason": "검증 근거가 없어 중립으로 통과합니다.",
                    "evidence": [],
                }
            ],
        }
        validate_screen_semantics(available)
        unsupported = copy.deepcopy(available)
        unsupported["candidates"][0]["score"] = 0.71
        with self.assertRaises(ContractValidationError):
            validate_screen_semantics(unsupported)
        unsupported = copy.deepcopy(available)
        unsupported["candidates"][0]["verdict"] = "VETO_BUY"
        with self.assertRaises(ContractValidationError):
            validate_screen_semantics(unsupported)

    def test_bootstrap_caps_and_call_accounting_are_fail_closed(self) -> None:
        fixture = {
            "providerCaps": {
                "kisDaily": 403,
                "kisToken": 1,
                "krxMembership": 5,
                "retry": 0,
            },
            "providerPhysicalCalls": {
                "kisDaily": 403,
                "kisToken": 1,
                "krxMembership": 5,
            },
            "membership": [f"{value:06d}" for value in range(1, 31)] + ["132030"],
            "accountCalls": 0,
            "orderCalls": 0,
        }
        validate_bootstrap_semantics(fixture)
        invalid = copy.deepcopy(fixture)
        invalid["providerPhysicalCalls"]["kisDaily"] = 404
        with self.assertRaises(ContractValidationError):
            validate_bootstrap_semantics(invalid)

    def test_additive_six_merge_reaches_75_and_projects_to_exact_current_root(
        self,
    ) -> None:
        root_path = ROOT / "contracts/openapi/openapi.json"
        root = json.loads(root_path.read_text(encoding="utf-8"))
        additive = json.loads(ADDITIVE_OPENAPI_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            CURRENT_ROOT_69_SHA256, hashlib.sha256(root_path.read_bytes()).hexdigest()
        )
        self.assertEqual(69, len(operations(root)))
        merged = merge_v3_openapi(root, additive)
        self.assertEqual(75, len(operations(merged)))
        projected = project_pre_v3_openapi(merged, additive)
        self.assertEqual(root, projected)

    def test_historical_v1_v2_bytes_are_unchanged(self) -> None:
        for relative, digest in HISTORICAL_BYTES.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    digest,
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                )


if __name__ == "__main__":
    unittest.main()
