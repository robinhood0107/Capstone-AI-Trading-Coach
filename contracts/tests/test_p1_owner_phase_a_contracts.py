from __future__ import annotations

import copy
import hashlib
import json
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_p1_owner_phase_a_contracts import (
    ARTIFACT_NAMES,
    ARTIFACT_SCHEMA_IDS,
    FEATURE_ORDER,
    FROZEN_SHA256,
    RELEASE_V3_HARD_GATES,
    ROOT,
    SCHEMA_IDS,
    SCHEMA_PATHS,
    _fixtures,
    _release_manifest_schema_v3,
    build_outputs,
    build_schemas,
    generate,
    validate_semantics,
)
from contracts.generate_principle_contracts import ContractValidationError


class P1OwnerPhaseAContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = build_schemas()
        cls.fixtures = _fixtures()

    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = build_outputs()
        second = build_outputs()
        self.assertEqual(first, second)
        self.assertEqual(tuple(self.schemas), SCHEMA_IDS)
        generate(check=True)

    def test_all_positive_and_unknown_field_negative_fixtures_are_closed(self) -> None:
        for schema_id in SCHEMA_IDS:
            with self.subTest(schema_id=schema_id):
                validator = Draft202012Validator(
                    self.schemas[schema_id], format_checker=FormatChecker()
                )
                self.assertEqual(
                    [], list(validator.iter_errors(self.fixtures[schema_id]))
                )
                invalid = copy.deepcopy(self.fixtures[schema_id])
                invalid["unexpected"] = True
                self.assertNotEqual([], list(validator.iter_errors(invalid)))

    def test_input_pack_locks_exact31_model_abi_cost_and_no_news(self) -> None:
        payload = self.fixtures["p1-return-engine-input-pack.v1"]
        self.assertEqual(31, len(payload["universe"]["symbols"]))
        self.assertEqual(1, payload["universe"]["symbols"].count("132030"))
        self.assertEqual(list(FEATURE_ORDER), payload["featureOrder"])
        self.assertEqual(35, payload["costModel"]["roundTripCostBps"])
        self.assertEqual(0, payload["modelConfig"]["hyperparameterSearchCount"])
        self.assertFalse(payload["costModel"]["actualKisFeeClaim"])
        self.assertEqual(0, payload["dataPolicy"]["newsFeatures"])
        self.assertEqual(0, payload["dataPolicy"]["gdeltInputs"])

        invalid = copy.deepcopy(payload)
        invalid["universe"]["symbols"][-1] = invalid["universe"]["symbols"][0]
        with self.assertRaises(ContractValidationError):
            validate_semantics("p1-return-engine-input-pack.v1", invalid)

    def test_manifest_binds_exact_ten_ordered_files_and_synthetic_truth(self) -> None:
        payload = self.fixtures["p1-return-engine-artifact-manifest.v2"]
        self.assertEqual(
            list(ARTIFACT_NAMES), [item["path"] for item in payload["artifacts"]]
        )
        self.assertEqual(
            [SCHEMA_PATHS[item] for item in ARTIFACT_SCHEMA_IDS],
            [item["semanticSchema"] for item in payload["artifacts"]],
        )
        self.assertFalse(payload["performanceClaimAllowed"])
        self.assertEqual("NONE", payload["orderAuthority"])

        synthetic = copy.deepcopy(payload)
        synthetic["evidenceMode"] = "SYNTHETIC_GOLDEN"
        synthetic["realTeamB"] = True
        synthetic["modelQuality"] = "NOT_EVALUATED_SYNTHETIC"
        with self.assertRaises(ContractValidationError):
            validate_semantics("p1-return-engine-artifact-manifest.v2", synthetic)

    def test_vertex_veto_is_a_closed_union_without_order_fields(self) -> None:
        schema = self.schemas["vertex-news-veto.v1"]
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        available = self.fixtures["vertex-news-veto.v1"]
        self.assertEqual([], list(validator.iter_errors(available)))
        for forbidden in (
            "side",
            "quantity",
            "price",
            "orderType",
            "userId",
            "accountId",
        ):
            invalid = copy.deepcopy(available)
            invalid[forbidden] = "forbidden"
            self.assertNotEqual([], list(validator.iter_errors(invalid)), forbidden)

        abstain = {
            "status": "ABSTAIN",
            "reason": "NO_GROUNDING",
            "inputSha256": "a" * 64,
            "modelId": "gemini-3.5-flash",
            "promptVersion": "vertex-news-veto-v1",
            "orderAuthority": "NONE",
        }
        self.assertEqual([], list(validator.iter_errors(abstain)))

    def test_automation_and_journal_contracts_are_owner_safe_and_bounded(self) -> None:
        control = self.fixtures["automation-control.v1"]
        invalid = copy.deepcopy(control)
        invalid["controlState"] = "HALTED"
        invalid["projectionState"] = "RUNNING"
        with self.assertRaises(ContractValidationError):
            validate_semantics("automation-control.v1", invalid)

        position = self.fixtures["automation-position.v1"]
        self.assertEqual(1, position["quantity"])
        self.assertTrue(position["botOwned"])
        self.assertFalse(position["shortAllowed"])

        journal = self.schemas["journal.v1"]
        self.assertEqual(8192, journal["properties"]["content"]["maxLength"])
        self.assertFalse(journal["additionalProperties"])

    def test_additive_openapi_locks_eight_routes_and_v1_surface_stays_exact_56(
        self,
    ) -> None:
        additive = json.loads(
            (
                ROOT / "contracts/openapi/p1-automation-journal.v1.openapi.json"
            ).read_text(encoding="utf-8")
        )
        methods = {
            (method.upper(), path)
            for path, path_item in additive["paths"].items()
            for method in path_item
            if method != "parameters"
        }
        self.assertEqual(
            {
                ("GET", "/api/v1/automation/status"),
                ("POST", "/api/v1/automation/arm"),
                ("POST", "/api/v1/automation/disarm"),
                ("GET", "/api/v1/automation/runs"),
                ("POST", "/api/v1/journals"),
                ("GET", "/api/v1/journals"),
                ("PATCH", "/api/v1/journals/{journalId}"),
                ("DELETE", "/api/v1/journals/{journalId}"),
            },
            methods,
        )
        root = json.loads(
            (ROOT / "contracts/openapi/openapi.json").read_text(encoding="utf-8")
        )
        root_operations = {
            (path, method)
            for path, path_item in root["paths"].items()
            for method in path_item
            if method != "parameters"
        }
        self.assertEqual(69, len(root_operations))
        # Strong LLM 설정 표면 하나도 이 검사의 대상이 아니다. 가장 새 층부터 덜어 낸다.
        strong_llm_additive = json.loads(
            (ROOT / "contracts/openapi/p1-strong-llm-settings.v1.openapi.json").read_text(
                encoding="utf-8"
            )
        )
        strong_llm_operations = {
            (path, method)
            for path, path_item in strong_llm_additive["paths"].items()
            for method in path_item
            if method != "parameters"
        }
        self.assertEqual(1, len(strong_llm_operations))
        self.assertTrue(strong_llm_operations <= root_operations)
        root_operations -= strong_llm_operations
        self.assertEqual(68, len(root_operations))
        # RAG v2 공개 표면 일곱 개는 이 검사의 대상이 아니다. 먼저 덜어 내고 exact-61을 본다.
        rag_v2_additive = json.loads(
            (ROOT / "contracts/openapi/p1-rag-v2-public.v1.openapi.json").read_text(
                encoding="utf-8"
            )
        )
        rag_v2_operations = {
            (path, method)
            for path, path_item in rag_v2_additive["paths"].items()
            for method in path_item
            if method != "parameters"
        }
        self.assertEqual(7, len(rag_v2_operations))
        self.assertTrue(rag_v2_operations <= root_operations)
        root_operations -= rag_v2_operations
        self.assertEqual(61, len(root_operations))

        v2_additive = json.loads(
            (
                ROOT / "contracts/openapi/p1-automation-v2.v1.openapi.json"
            ).read_text(encoding="utf-8")
        )
        v2_operations = {
            (path, method)
            for path, path_item in v2_additive["paths"].items()
            for method in path_item
            if method != "parameters"
        }
        self.assertEqual(5, len(v2_operations))
        self.assertTrue(v2_operations <= root_operations)
        self.assertEqual(56, len(root_operations - v2_operations))
        self.assertFalse(
            {path for path, _ in root_operations - v2_operations}
            & {path for path, _ in v2_operations}
        )

    def test_release_v3_requires_exact_sixteen_gates_and_keeps_live_closed(
        self,
    ) -> None:
        catalog = json.loads(
            (
                ROOT / "contracts/catalogs/p1-full-app-release-contract.v3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(list(RELEASE_V3_HARD_GATES), catalog["hardGates"])
        self.assertEqual(16, len(catalog["hardGates"]))
        self.assertEqual(33, catalog["teamARequiredOperationCount"])
        self.assertEqual(56, catalog["openApiOperationCount"])
        self.assertEqual(0, catalog["kisLiveOrderCalls"])
        self.assertEqual(0, catalog["gdeltCalls"])

        schema = _release_manifest_schema_v3()
        validator = Draft202012Validator(schema)
        candidate = {
            "contractId": "p1-full-app-release-manifest.v3",
            "stage": "CANDIDATE",
            "releaseVersion": "1.0.0",
            "commitSha": "a" * 40,
            "treeSha": "b" * 40,
            "hardGates": {gate: "NOT_RUN" for gate in RELEASE_V3_HARD_GATES},
            "teamBManifestSha256": "a" * 64,
            "teamAImageDigest": f"sha256:{'b' * 64}",
            "providerReceipt": {
                "vertexGroundedCalls": 0,
                "kisTokenCalls": 0,
                "kisDailyCalls": 0,
                "ecosCalls": 0,
                "kisQuoteCalls": 0,
                "kisBrokerageCalls": 0,
                "orderSubmitCalls": 0,
                "cancelCalls": 0,
                "retries": 0,
            },
            "lightgbm": "RESEARCH_ONLY_NO_SIGNAL_OR_ORDER_AUTHORITY",
            "kisLiveOrderCalls": 0,
            "gdeltCalls": 0,
            "released": False,
        }
        self.assertEqual([], list(validator.iter_errors(candidate)))
        final = copy.deepcopy(candidate)
        final["stage"] = "FINAL"
        final["released"] = True
        final["hardGates"] = {gate: "PASS" for gate in RELEASE_V3_HARD_GATES}
        self.assertEqual([], list(validator.iter_errors(final)))
        final["hardGates"]["THREE_XKRX_SESSION_SOAK"] = "BLOCKED"
        self.assertNotEqual([], list(validator.iter_errors(final)))

    def test_historical_contracts_license_and_news_summary_are_byte_stable(
        self,
    ) -> None:
        for relative, expected in FROZEN_SHA256.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    expected,
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                )
        self.assertFalse(
            (
                ROOT / "contracts/schemas/return-engine-news-feature.v1.schema.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
