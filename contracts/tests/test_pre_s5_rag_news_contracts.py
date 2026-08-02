from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_pre_s5_rag_news_contracts import (
    ARTIFACT_PATHS,
    FROZEN_EXISTING_HASHES,
    INVALID_FIXTURE_PATHS,
    SCHEMA_IDS,
    VALID_FIXTURE_PATHS,
    ContractValidationError,
    generate_outputs,
    validate_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class PreS5RagNewsContractTest(unittest.TestCase):
    """Pre-S5 addendum은 runtime이나 provider 실행 없이 policy bytes만 잠근다."""

    def setUp(self) -> None:
        self.outputs = generate_outputs()
        self.schemas = {
            schema_id: _load(ROOT / f"contracts/schemas/{schema_id}.schema.json")
            for schema_id in SCHEMA_IDS
        }
        self.validators = {
            schema_id: Draft202012Validator(schema)
            for schema_id, schema in self.schemas.items()
        }

    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        self.assertEqual(self.outputs, generate_outputs())
        self.assertEqual(ARTIFACT_PATHS, frozenset(self.outputs))
        self.assertTrue(all(value.endswith(b"\n") for value in self.outputs.values()))

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_pre_s5_rag_news_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("RAG_AND_GLOBAL_NEWS_CONTRACT_LOCKED", completed.stdout)

    def test_existing_history_and_v1_contract_inputs_are_frozen(self) -> None:
        self.assertIn("contracts/openapi/openapi.json", FROZEN_EXISTING_HASHES)
        self.assertIn("contracts/schemas/news_sentiment_summary.v2.schema.json", FROZEN_EXISTING_HASHES)
        self.assertIn(
            "capstone-rag/manifests/s4-7d-oa140-release.v1.json",
            FROZEN_EXISTING_HASHES,
        )
        # generate_outputs() 자체가 expected digest를 확인했으므로 여기서는 의도를 명시한다.
        self.assertEqual(11, len(FROZEN_EXISTING_HASHES))

    def test_oa112_selection_is_logically_active_but_not_materialized(self) -> None:
        catalog = _load(ROOT / "contracts/catalogs/pre-s5-rag-news-contract.v1.json")
        oa = catalog["oa112"]
        self.assertEqual("OA112_ACTIVE_CONTRACT_LOCKED", oa["status"])
        self.assertEqual("NOT_MATERIALIZED", oa["physicalActivation"])
        self.assertEqual(112, oa["sourceCount"])
        self.assertEqual(14, oa["trackCount"])
        self.assertEqual(8, oa["sourcesPerTrack"])
        self.assertEqual(28, oa["reserveMaximumSources"])
        self.assertFalse(oa["reserveAutomaticPromotion"])
        self.assertEqual(
            [
                "machineFetchAllowed",
                "localProcessingAllowed",
                "externalEmbeddingAllowed",
                "externalGenerationAllowed",
            ],
            oa["requiredActivationPermissions"],
        )

        selection = _load(
            ROOT / "contracts/examples/rag-oa112-logical-selection-v1.valid.json"
        )
        self.assertEqual(
            [],
            list(self.validators["rag-oa112-logical-selection-v1"].iter_errors(selection)),
        )
        validate_semantics("rag-oa112-logical-selection-v1", selection)
        self.assertEqual(14, len(selection["tracks"]))
        self.assertTrue(all(track["sourceCount"] == 8 for track in selection["tracks"]))

    def test_v4_source_permissions_require_all_four_for_active_oa(self) -> None:
        source = _load(ROOT / "contracts/examples/rag-source-card-v4.oa-contract.valid.json")
        validator = self.validators["rag-source-card-v4"]
        self.assertEqual([], list(validator.iter_errors(source)))
        validate_semantics("rag-source-card-v4", source)

        forbidden = copy.deepcopy(source)
        forbidden["permissions"]["externalGenerationAllowed"] = False
        self.assertTrue(list(validator.iter_errors(forbidden)))

    def test_rag_v2_surface_locks_consent_ticket_status_ask_and_history(self) -> None:
        document = _load(ROOT / "contracts/openapi/rag-v2-pre-s5-addendum.openapi.json")
        self.assertEqual(
            {
                "/api/v2/rag/consents",
                "/api/v2/rag/consent",
                "/api/v2/rag/import-tickets",
            },
            set(document["paths"]),
        )
        inherited = _load(ROOT / "contracts/catalogs/pre-s5-rag-news-contract.v1.json")[
            "ragV2"
        ]["inheritedSurface"]
        self.assertEqual(
            [
                "/api/v2/rag/ask",
                "/api/v2/rag/corpus-status",
                "/api/v2/rag/history",
                "/api/v2/rag/history/{answerId}",
            ],
            inherited["paths"],
        )
        ticket = _load(ROOT / "contracts/examples/s4-rag-v2-import-ticket.valid.json")
        self.assertEqual(
            [], list(self.validators["s4-rag-v2-import-ticket"].iter_errors(ticket))
        )
        validate_semantics("s4-rag-v2-import-ticket", ticket)
        self.assertEqual(300, ticket["ttlSeconds"])
        self.assertTrue(ticket["singleUse"])
        self.assertFalse(ticket["ownerRawCopyAllowed"])

    def test_voyage_vertex_and_generation_boundaries_are_contract_only(self) -> None:
        catalog = _load(ROOT / "contracts/catalogs/pre-s5-rag-news-contract.v1.json")
        voyage = catalog["ragV2"]["voyage"]
        vertex = catalog["ragV2"]["vertex"]
        self.assertEqual("voyage-context-4", voyage["modelId"])
        self.assertEqual(1024, voyage["dimension"])
        self.assertEqual("VOYAGE_API_KEY", voyage["runtimeEnvironmentVariable"])
        self.assertFalse(voyage["filesApiAllowed"])
        self.assertFalse(voyage["batchApiAllowed"])
        self.assertFalse(voyage["queryUnitFallbackAllowed"])
        self.assertEqual("FULL_BUNDLE_REBUILD_EVALUATE_CAS", voyage["fallback"])
        self.assertEqual("gemini-3.5-flash", vertex["modelId"])
        self.assertEqual(["ADC", "SERVICE_ACCOUNT"], vertex["authentication"])
        self.assertFalse(vertex["developerApiAllowed"])
        self.assertEqual(5, vertex["maximumEvidenceCount"])
        self.assertEqual(1, vertex["maximumGenerateContentCallsPerQuestion"])
        self.assertFalse(vertex["fallbackAllowed"])

    def test_foreign_news_response_has_no_decision_or_raw_authority(self) -> None:
        response = _load(
            ROOT / "contracts/examples/foreign-news-sentiment-v1.abstain.valid.json"
        )
        validator = self.validators["foreign-news-sentiment-v1"]
        self.assertEqual([], list(validator.iter_errors(response)))
        validate_semantics("foreign-news-sentiment-v1", response)
        self.assertEqual("NONE", response["decisionAuthority"])
        self.assertEqual(["EXPLANATION_ONLY"], response["allowedUses"])
        self.assertFalse(response["s5FeatureEligible"])
        self.assertFalse(response["riskDecisionHashIncluded"])
        self.assertFalse(response["rawProviderDataStored"])
        self.assertFalse(response["articleMetadataStored"])

    def test_global_news_and_optional3_are_non_executable_contract_boundaries(self) -> None:
        catalog = _load(ROOT / "contracts/catalogs/pre-s5-rag-news-contract.v1.json")
        lanes = catalog["foreignNews"]["lanes"]
        self.assertEqual(
            ["FINNHUB_PERSONAL_LOCAL", "SEC_OFFICIAL", "FED_OFFICIAL", "GDELT_OFFLINE_REFERENCE"],
            [lane["laneId"] for lane in lanes],
        )
        self.assertTrue(all(lane["providerCallsAllowed"] is False for lane in lanes))
        self.assertEqual("DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY", lanes[-1]["mode"])
        optional3 = catalog["s48Optional3"]
        self.assertEqual(0, optional3["providerCallsAllowed"])
        self.assertEqual(0, optional3["receiptExecutionAllowed"])
        self.assertEqual(
            ["FINNHUB_OPTIONAL3", "TWELVE_DATA", "MASSIVE"],
            optional3["providerFamilies"],
        )

    def test_model_selection_rule_has_no_test_shopping_escape_hatch(self) -> None:
        rule = _load(ROOT / "contracts/examples/foreign-news-model-selection-v1.pending.valid.json")
        validator = self.validators["foreign-news-model-selection-v1"]
        self.assertEqual([], list(validator.iter_errors(rule)))
        validate_semantics("foreign-news-model-selection-v1", rule)
        self.assertEqual(0, rule["testEvaluationCount"])
        self.assertEqual("NOT_SELECTED", rule["selectionStatus"])

    def test_all_required_invalid_fixtures_fail_closed(self) -> None:
        self.assertEqual(13, len(INVALID_FIXTURE_PATHS))
        self.assertEqual(15, len(VALID_FIXTURE_PATHS))
        for relative_path in sorted(INVALID_FIXTURE_PATHS):
            payload = _load(ROOT / relative_path)
            schema_id = Path(relative_path).name.split(".", maxsplit=1)[0]
            validator = self.validators[schema_id]
            errors = list(validator.iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not errors:
                try:
                    validate_semantics(schema_id, payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            self.assertTrue(errors or semantic_error, relative_path)


if __name__ == "__main__":
    unittest.main()
