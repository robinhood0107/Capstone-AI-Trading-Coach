from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_pre_s5_rag_news_contracts import (
    ARTIFACT_PATHS,
    FROZEN_EXISTING_HASHES,
    INVALID_FIXTURE_PATHS,
    SCHEMA_IDS,
    VALID_FIXTURE_PATHS,
    _check_outputs,
    _unexpected_pre_s5_artifact_paths,
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

    def test_generated_check_rejects_extra_or_linked_pre_s5_artifacts(self) -> None:
        """public Pre-S5 namespace에는 declared output 이외의 packet이나 link를 숨길 수 없다."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            outputs = generate_outputs()
            for relative_path, content in outputs.items():
                path = temporary_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            unexpected = (
                temporary_root
                / "contracts/examples/.local/foreign-news-live-approval.json"
            )
            unexpected.parent.mkdir(parents=True, exist_ok=True)
            unexpected.write_bytes(
                outputs["contracts/examples/foreign-news-sentiment-v1.valid.json"]
            )
            self.assertEqual(
                ["contracts/examples/.local/foreign-news-live-approval.json"],
                _unexpected_pre_s5_artifact_paths(temporary_root, outputs),
            )
            with self.assertRaisesRegex(
                ContractValidationError,
                "unexpected Pre-S5 generated namespace artifacts",
            ):
                _check_outputs(outputs, root=temporary_root)

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            external_examples = temporary_root / "external-examples"
            outputs = generate_outputs()
            for relative_path, content in outputs.items():
                target = temporary_root / relative_path
                if relative_path.startswith("contracts/examples/"):
                    target = external_examples / Path(relative_path).relative_to(
                        "contracts/examples"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            linked_parent = temporary_root / "contracts/examples"
            linked_parent.symlink_to(external_examples, target_is_directory=True)
            with self.assertRaisesRegex(
                ContractValidationError,
                "generated Pre-S5 RAG/news artifacts drifted",
            ):
                _check_outputs(outputs, root=temporary_root)

    def test_existing_history_and_v1_contract_inputs_are_frozen(self) -> None:
        self.assertIn("contracts/openapi/openapi.json", FROZEN_EXISTING_HASHES)
        self.assertIn("contracts/schemas/news_sentiment_summary.v2.schema.json", FROZEN_EXISTING_HASHES)
        self.assertIn("contracts/proto/rag_v2.descriptor.pb", FROZEN_EXISTING_HASHES)
        self.assertIn("contracts/proto/rag_v2.descriptor.sha256", FROZEN_EXISTING_HASHES)
        self.assertIn(
            "capstone-rag/manifests/s4-7d-oa140-release.v1.json",
            FROZEN_EXISTING_HASHES,
        )
        self.assertIn(
            "capstone-rag/manifests/s4-7d-oa140-checksums.sha256",
            FROZEN_EXISTING_HASHES,
        )
        # generate_outputs() 자체가 expected digest를 확인했으므로 여기서는 의도를 명시한다.
        self.assertEqual(14, len(FROZEN_EXISTING_HASHES))
        catalog = _load(ROOT / "contracts/catalogs/pre-s5-rag-news-contract.v1.json")
        self.assertEqual(
            list(FROZEN_EXISTING_HASHES),
            catalog["frozenCompatibility"]["generatorVerifiedInputs"],
        )

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

        duplicated_track = copy.deepcopy(selection)
        duplicated_track["tracks"][-1] = copy.deepcopy(duplicated_track["tracks"][0])
        self.assertTrue(
            list(
                self.validators["rag-oa112-logical-selection-v1"].iter_errors(
                    duplicated_track
                )
            )
        )

    def test_v4_source_permissions_require_all_four_for_active_oa(self) -> None:
        source = _load(ROOT / "contracts/examples/rag-source-card-v4.valid.json")
        validator = self.validators["rag-source-card-v4"]
        self.assertEqual([], list(validator.iter_errors(source)))
        validate_semantics("rag-source-card-v4", source)

        forbidden = copy.deepcopy(source)
        forbidden["permissions"]["externalGenerationAllowed"] = False
        self.assertTrue(list(validator.iter_errors(forbidden)))

        self.assertEqual("DOI", source["identifier"]["scheme"])
        self.assertEqual("2026-08-03", source["revisionDate"])
        self.assertIn("accessEvidenceDigest", source["accessEvidence"])

        unsafe = copy.deepcopy(source)
        unsafe["canonicalUrl"] = "https://127.0.0.1/internal.pdf"
        self.assertEqual([], list(validator.iter_errors(unsafe)))
        with self.assertRaisesRegex(ContractValidationError, "public HTTPS URL"):
            validate_semantics("rag-source-card-v4", unsafe)

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
        ticket = _load(ROOT / "contracts/examples/s4-rag-v2-import-ticket-v1.valid.json")
        self.assertEqual(
            [], list(self.validators["s4-rag-v2-import-ticket-v1"].iter_errors(ticket))
        )
        validate_semantics("s4-rag-v2-import-ticket-v1", ticket)
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
        self.assertFalse(voyage["partialProfileMixAllowed"])
        self.assertTrue(voyage["orderedPrechunkedDocumentGroupsRequired"])
        self.assertEqual("FULL_BUNDLE_REBUILD_EVALUATE_CAS", voyage["generationFallback"])
        self.assertEqual("gemini-3.5-flash", vertex["modelId"])
        self.assertEqual(["ADC", "SERVICE_ACCOUNT"], vertex["authentication"])
        self.assertFalse(vertex["developerApiAllowed"])
        self.assertEqual(5, vertex["maximumEvidenceCount"])
        self.assertEqual(1, vertex["maximumGenerateContentCallsPerQuestion"])
        self.assertFalse(vertex["fallbackAllowed"])
        self.assertFalse(vertex["searchMapsGroundingAllowed"])
        self.assertFalse(vertex["fileUploadAllowed"])
        self.assertFalse(vertex["sessionResumptionAllowed"])
        self.assertFalse(vertex["contextCacheAllowed"])
        self.assertEqual(0, vertex["retryCount"])
        self.assertFalse(vertex["rawResponseStored"])
        self.assertTrue(vertex["sanitizedUsageLedgerOnly"])

    def test_foreign_news_response_has_no_decision_or_raw_authority(self) -> None:
        response = _load(
            ROOT / "contracts/examples/foreign-news-sentiment-v1.valid.json"
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

        unavailable = copy.deepcopy(response)
        unavailable["status"] = "AVAILABLE"
        self.assertTrue(list(validator.iter_errors(unavailable)))

    def test_foreign_news_lane_mapping_locks_transport_and_retention_boundaries(self) -> None:
        entitlement = _load(
            ROOT / "contracts/examples/foreign-news-lane-entitlement-v1.valid.json"
        )
        validator = self.validators["foreign-news-lane-entitlement-v1"]
        self.assertEqual([], list(validator.iter_errors(entitlement)))
        validate_semantics("foreign-news-lane-entitlement-v1", entitlement)

        gdelt = entitlement["lanes"][-1]
        self.assertEqual("GDELT_OFFLINE_REFERENCE", gdelt["laneId"])
        self.assertEqual("DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY", gdelt["mode"])
        self.assertFalse(gdelt["redirectAllowed"])
        self.assertFalse(gdelt["rawForwardedToVertex"])

        forged = copy.deepcopy(entitlement)
        forged["lanes"][-1]["credentialMode"] = "OWNER_PERSONAL_LOCAL_ONLY"
        self.assertTrue(list(validator.iter_errors(forged)))

        duplicated_lane = copy.deepcopy(entitlement)
        duplicated_lane["lanes"][-1] = copy.deepcopy(duplicated_lane["lanes"][0])
        self.assertTrue(list(validator.iter_errors(duplicated_lane)))

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
        rule = _load(ROOT / "contracts/examples/foreign-news-model-selection-v1.valid.json")
        validator = self.validators["foreign-news-model-selection-v1"]
        self.assertEqual([], list(validator.iter_errors(rule)))
        validate_semantics("foreign-news-model-selection-v1", rule)
        self.assertEqual(0, rule["testEvaluationCount"])
        self.assertEqual("NOT_SELECTED", rule["selectionStatus"])
        self.assertEqual(
            [
                "VALIDATION_MACRO_F1_DESC",
                "ECE_ASC",
                "CPU_P95_ASC",
                "FOOTPRINT_ASC",
            ],
            _load(ROOT / "contracts/catalogs/pre-s5-rag-news-contract.v1.json")["foreignNews"][
                "modelSelection"
            ]["selectionOrder"],
        )

        duplicated_result = copy.deepcopy(rule)
        duplicated_result["validationResults"][-1] = copy.deepcopy(
            duplicated_result["validationResults"][0]
        )
        self.assertTrue(list(validator.iter_errors(duplicated_result)))

        pending = copy.deepcopy(rule)
        pending["validationCompleted"] = True
        pending["validationResults"] = [
            {
                "candidateModel": "PROSUSAI_FINBERT",
                "metrics": {
                    "classRecalls": {"NEGATIVE": 0.80, "NEUTRAL": 0.82, "POSITIVE": 0.81},
                    "cpuP95Millis": 10,
                    "criticalNegationNumberUnitErrors": 0,
                    "ece": 0.05,
                    "footprintBytes": 100,
                    "macroF1": 0.84,
                    "neutralF1": 0.82,
                },
            },
            {
                "candidateModel": "YIYANGHKUST_FINBERT_TONE",
                "metrics": {
                    "classRecalls": {"NEGATIVE": 0.80, "NEUTRAL": 0.81, "POSITIVE": 0.80},
                    "cpuP95Millis": 9,
                    "criticalNegationNumberUnitErrors": 0,
                    "ece": 0.04,
                    "footprintBytes": 90,
                    "macroF1": 0.83,
                    "neutralF1": 0.81,
                },
            },
            {
                "candidateModel": "LOUGHRAN_MCDONALD_BASELINE",
                "metrics": {
                    "classRecalls": {"NEGATIVE": 0.76, "NEUTRAL": 0.76, "POSITIVE": 0.77},
                    "cpuP95Millis": 1,
                    "criticalNegationNumberUnitErrors": 0,
                    "ece": 0.09,
                    "footprintBytes": 1,
                    "macroF1": 0.80,
                    "neutralF1": 0.76,
                },
            },
        ]
        pending["selectedModel"] = "PROSUSAI_FINBERT"
        pending["selectionStatus"] = "SELECTED_PENDING_TEST"
        pending["testEvaluationCount"] = 0
        pending["testOutcome"] = "NOT_RUN"
        pending["testTargetModel"] = None
        pending["abstainReason"] = None
        self.assertEqual([], list(validator.iter_errors(pending)))
        validate_semantics("foreign-news-model-selection-v1", pending)

        invalid_test_state = copy.deepcopy(pending)
        invalid_test_state["selectionStatus"] = "TEST_EVALUATED"
        invalid_test_state["testOutcome"] = "PASSED"
        self.assertEqual([], list(validator.iter_errors(invalid_test_state)))
        with self.assertRaisesRegex(ContractValidationError, "test evaluation"):
            validate_semantics("foreign-news-model-selection-v1", invalid_test_state)

        failed_test = copy.deepcopy(pending)
        failed_test["selectionStatus"] = "ABSTAIN"
        failed_test["testEvaluationCount"] = 1
        failed_test["testOutcome"] = "FAILED"
        failed_test["testTargetModel"] = "PROSUSAI_FINBERT"
        failed_test["abstainReason"] = "TEST_FAILED"
        validate_semantics("foreign-news-model-selection-v1", failed_test)

    def test_additive_openapi_operations_require_bearer_authentication(self) -> None:
        rag_document = _load(ROOT / "contracts/openapi/rag-v2-pre-s5-addendum.openapi.json")
        for path_item in rag_document["paths"].values():
            for operation in path_item.values():
                self.assertEqual([{"bearerAuth": []}], operation["security"])

        foreign_document = _load(
            ROOT / "contracts/openapi/foreign-news-sentiment.v1.openapi.json"
        )
        operation = foreign_document["paths"][
            "/api/v2/market-evidence/{symbol}/foreign-news-sentiment"
        ]["get"]
        self.assertEqual([{"bearerAuth": []}], operation["security"])

    def test_owner_deletion_status_requires_replacement_generation_receipt(self) -> None:
        status = _load(ROOT / "contracts/examples/s4-rag-v2-status-activation-v1.valid.json")
        ready = _load(
            ROOT / "contracts/examples/s4-rag-v2-status-activation-v1.ready.valid.json"
        )
        validator = self.validators["s4-rag-v2-status-activation-v1"]
        self.assertEqual([], list(validator.iter_errors(status)))
        validate_semantics("s4-rag-v2-status-activation-v1", status)
        self.assertEqual([], list(validator.iter_errors(ready)))
        validate_semantics("s4-rag-v2-status-activation-v1", ready)

        forged = copy.deepcopy(status)
        forged["state"] = "READY"
        self.assertNotEqual([], list(validator.iter_errors(forged)))
        with self.assertRaisesRegex(ContractValidationError, "hard-delete"):
            validate_semantics("s4-rag-v2-status-activation-v1", forged)

        forged_ready = copy.deepcopy(ready)
        forged_ready["hardDeletedArtifactClasses"] = ["DOCUMENT_IR"]
        self.assertNotEqual([], list(validator.iter_errors(forged_ready)))
        with self.assertRaisesRegex(ContractValidationError, "hard-delete"):
            validate_semantics("s4-rag-v2-status-activation-v1", forged_ready)

    def test_all_required_invalid_fixtures_fail_closed(self) -> None:
        self.assertGreaterEqual(len(INVALID_FIXTURE_PATHS), 16)
        self.assertEqual(16, len(VALID_FIXTURE_PATHS))
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
