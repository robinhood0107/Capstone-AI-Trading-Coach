from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s4_rag_contracts import (
    CATALOG_PATH,
    CATALOG_SHA256_MANIFEST_PATH,
    EXPECTED_CATALOG_SHA256,
    OUTPUTS,
    RAG_SOURCE_CARD_UPSTREAM_SOURCE_IDS,
    generate_outputs,
    load_catalog,
    load_json_bytes_strict,
    validate_admin_policy_selection_semantics,
    validate_catalog_semantics,
    validate_rag_ask_request_semantics,
    validate_rag_source_card_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


class S4RagContractCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_catalog_locks_exact_profiles_policies_and_digest(self) -> None:
        self.assertEqual(EXPECTED_CATALOG_SHA256, hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest())
        manifest = load_json_bytes_strict(
            CATALOG_SHA256_MANIFEST_PATH.read_bytes(),
            source="contracts/catalogs/s4-rag-contract.v1.sha256.json",
        )
        self.assertEqual(
            {
                "catalogPath": "contracts/catalogs/s4-rag-contract.v1.json",
                "contractChangePath": "contracts/changes/20260729-s4-rag-contract-catalog.md",
                "schemaVersion": 1,
                "sha256": EXPECTED_CATALOG_SHA256,
            },
            manifest,
        )
        self.assertEqual(
            ["bge_m3_local_1024_v1", "voyage_context_4_1024_v1"],
            self.catalog["profileIds"],
        )
        self.assertEqual(
            ["bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1"],
            self.catalog["policyIds"],
        )
        self.assertEqual(["voyage_context_3_1024_v1"], self.catalog["forbiddenProfileIds"])
        self.assertNotIn("voyage_context_3_1024_v1", self.catalog["profileIds"])

    def test_source_card_upstream_allowlist_matches_the_runtime_seed(self) -> None:
        seed_path = (
            ROOT
            / "workspaces/decision-platform/python-services/app/rag/rag_source_seed.yaml"
        )
        seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        self.assertEqual(
            RAG_SOURCE_CARD_UPSTREAM_SOURCE_IDS,
            tuple(source["sourceId"] for source in seed["sources"]),
        )
        self.assertEqual(1024, self.catalog["dimension"])
        self.assertEqual(["CONCISE", "DETAILED"], self.catalog["answerModes"])
        self.assertEqual(
            [
                "REGISTERED",
                "PLANNED",
                "MATERIALIZING",
                "MATERIALIZED",
                "EVAL_PASSED",
                "ACTIVE",
                "FAILED_FINAL",
                "DISABLED",
            ],
            self.catalog["generationStatuses"],
        )
        self.assertEqual(0, self.catalog["canonicalChunking"]["overlapPercent"])
        self.assertEqual(
            "ONNX_DATA_ONLY",
            self.catalog["profiles"][0]["artifactFormat"],
        )
        self.assertEqual(
            15,
            self.catalog["profiles"][0]["transientAdjacentContextMaxPercent"],
        )
        self.assertEqual(
            {
                "maximumItems": 30,
                "publicSourceType": "PROJECT_SOURCE_CARD",
                "queryParametersAllowed": False,
                "rawChunkIncluded": False,
                "rawUpstreamBodyIncluded": False,
            },
            self.catalog["sourceMetadata"],
        )

    def test_generator_outputs_are_deterministic_and_complete(self) -> None:
        first = generate_outputs(copy.deepcopy(self.catalog))
        second = generate_outputs(copy.deepcopy(self.catalog))

        self.assertEqual(first, second)
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

    def test_generator_check_passes_against_tracked_artifacts(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s4_rag_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S4_RAG_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_catalog_semantics_reject_forbidden_profile_and_runtime_fallback(self) -> None:
        mutations = []

        with_voyage3 = copy.deepcopy(self.catalog)
        with_voyage3["profiles"][0]["profileId"] = "voyage_context_3_1024_v1"
        mutations.append(with_voyage3)

        profile_as_policy = copy.deepcopy(self.catalog)
        profile_as_policy["policies"][0]["policyId"] = "bge_m3_local_1024_v1"
        mutations.append(profile_as_policy)

        mixed_vectors = copy.deepcopy(self.catalog)
        mixed_vectors["policies"][0]["queryProfileId"] = "voyage_context_4_1024_v1"
        mutations.append(mixed_vectors)

        request_fallback = copy.deepcopy(self.catalog)
        request_fallback["policies"][2]["perRequestFallback"] = True
        mutations.append(request_fallback)

        voyage_downgrade = copy.deepcopy(self.catalog)
        voyage_downgrade["profiles"][1]["model"] = "voyage-context-3"
        mutations.append(voyage_downgrade)

        lifecycle_drift = copy.deepcopy(self.catalog)
        lifecycle_drift["generationStatuses"] = ["MATERIALIZING", "ACTIVE"]
        mutations.append(lifecycle_drift)

        topic_drift = copy.deepcopy(self.catalog)
        topic_drift["topicAllowlist"] = ["RISK", "API"]
        mutations.append(topic_drift)

        idempotency_drift = copy.deepcopy(self.catalog)
        idempotency_drift["askRequest"]["idempotencyKeyPattern"] = "^.*$"
        mutations.append(idempotency_drift)

        for mutation in mutations:
            with self.subTest(mutation=hashlib.sha256(repr(mutation).encode()).hexdigest()):
                with self.assertRaises(ContractValidationError):
                    validate_catalog_semantics(mutation)

    def test_public_ask_schema_rejects_profile_policy_and_topk_controls(self) -> None:
        schema = load_json_bytes_strict(
            (ROOT / "contracts/schemas/s4-rag-ask-request.schema.json").read_bytes(),
            source="contracts/schemas/s4-rag-ask-request.schema.json",
        )
        validator = Draft202012Validator(schema)
        valid = load_json_bytes_strict(
            (ROOT / "contracts/examples/s4-rag-ask-request.valid.json").read_bytes(),
            source="contracts/examples/s4-rag-ask-request.valid.json",
        )
        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_rag_ask_request_semantics(valid, self.catalog)

        for suffix in ("profile-selection", "top-k"):
            invalid = load_json_bytes_strict(
                (
                    ROOT
                    / "contracts/examples/invalid"
                    / f"s4-rag-ask-request.{suffix}.invalid.json"
                ).read_bytes(),
                source=f"s4-rag-ask-request.{suffix}.invalid.json",
            )
            self.assertNotEqual([], list(validator.iter_errors(invalid)), suffix)

        non_nfc = copy.deepcopy(valid)
        non_nfc["question"] = "Cafe\u0301 리스크를 설명해 주세요."
        self.assertEqual([], list(validator.iter_errors(non_nfc)))
        with self.assertRaises(ContractValidationError):
            validate_rag_ask_request_semantics(non_nfc, self.catalog)

        unpaired_surrogate = copy.deepcopy(valid)
        unpaired_surrogate["question"] = "\ud800"
        self.assertEqual([], list(validator.iter_errors(unpaired_surrogate)))
        with self.assertRaises(ContractValidationError):
            validate_rag_ask_request_semantics(unpaired_surrogate, self.catalog)

        for invalid_symbol_list in (
            ["NVDA"],
            ["005930", "000660", "035420", "051910", "068270", "207940"],
        ):
            invalid_symbols = copy.deepcopy(valid)
            invalid_symbols["relatedSymbols"] = invalid_symbol_list
            self.assertNotEqual([], list(validator.iter_errors(invalid_symbols)))

    def test_s4_4_answer_history_feedback_and_consent_schemas_are_closed(self) -> None:
        fixtures = {
            "s4-rag-answer": "provider",
            "s4-rag-history-page": "preview",
            "s4-rag-history-detail": "provider",
            "s4-rag-feedback-request": "comment",
            "s4-rag-consent-request": "actor",
        }
        for name, invalid_suffix in fixtures.items():
            with self.subTest(name=name):
                schema = load_json_bytes_strict(
                    (ROOT / f"contracts/schemas/{name}.schema.json").read_bytes(),
                    source=f"contracts/schemas/{name}.schema.json",
                )
                validator = Draft202012Validator(schema)
                valid = load_json_bytes_strict(
                    (ROOT / f"contracts/examples/{name}.valid.json").read_bytes(),
                    source=f"contracts/examples/{name}.valid.json",
                )
                invalid = load_json_bytes_strict(
                    (
                        ROOT
                        / "contracts/examples/invalid"
                        / f"{name}.{invalid_suffix}.invalid.json"
                    ).read_bytes(),
                    source=f"{name}.{invalid_suffix}.invalid.json",
                )
                self.assertEqual([], list(validator.iter_errors(valid)))
                self.assertNotEqual([], list(validator.iter_errors(invalid)))
                self.assertFalse(schema["additionalProperties"])

        answer_schema = load_json_bytes_strict(
            (ROOT / "contracts/schemas/s4-rag-answer.schema.json").read_bytes(),
            source="contracts/schemas/s4-rag-answer.schema.json",
        )
        answer_validator = Draft202012Validator(answer_schema)
        answered_with_inconsistent_status = {
            "answer": "공개 근거로 답변합니다. [cit_1]",
            "answerId": "rag_ans_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "citationCoverage": 0.5,
            "citations": [
                {
                    "canonicalUrl": "https://example.com/evidence",
                    "citationId": "cit_1",
                    "sectionTitle": "근거",
                    "sourceId": "src_project_example_001",
                    "title": "공개 근거",
                }
            ],
            "generationStatus": "ANSWERED",
            "guardrailFlags": [],
            "requestId": "req_s4_4_consistency",
            "retrievalFailure": True,
        }
        self.assertNotEqual(
            [],
            list(answer_validator.iter_errors(answered_with_inconsistent_status)),
        )

        detail_schema = load_json_bytes_strict(
            (ROOT / "contracts/schemas/s4-rag-history-detail.schema.json").read_bytes(),
            source="contracts/schemas/s4-rag-history-detail.schema.json",
        )
        detail_validator = Draft202012Validator(detail_schema)
        answered_without_answer = load_json_bytes_strict(
            (ROOT / "contracts/examples/s4-rag-history-detail.valid.json").read_bytes(),
            source="contracts/examples/s4-rag-history-detail.valid.json",
        )
        answered_without_answer["generationStatus"] = "ANSWERED"
        self.assertNotEqual(
            [],
            list(detail_validator.iter_errors(answered_without_answer)),
        )

        history_page = load_json_bytes_strict(
            (ROOT / "contracts/schemas/s4-rag-history-page.schema.json").read_bytes(),
            source="contracts/schemas/s4-rag-history-page.schema.json",
        )
        self.assertNotIn(
            "question",
            history_page["properties"]["items"]["items"]["properties"],
        )
        self.assertNotIn(
            "answer",
            history_page["properties"]["items"]["items"]["properties"],
        )

    def test_admin_policy_selection_cannot_accept_profile_ids(self) -> None:
        schema = load_json_bytes_strict(
            (
                ROOT / "contracts/schemas/s4-rag-admin-policy-selection.schema.json"
            ).read_bytes(),
            source="contracts/schemas/s4-rag-admin-policy-selection.schema.json",
        )
        validator = Draft202012Validator(schema)
        valid = load_json_bytes_strict(
            (
                ROOT / "contracts/examples/s4-rag-admin-policy-selection.valid.json"
            ).read_bytes(),
            source="contracts/examples/s4-rag-admin-policy-selection.valid.json",
        )
        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_admin_policy_selection_semantics(valid, self.catalog)

        invalid = load_json_bytes_strict(
            (
                ROOT
                / "contracts/examples/invalid"
                / "s4-rag-admin-policy-selection.profile-as-policy.invalid.json"
            ).read_bytes(),
            source="s4-rag-admin-policy-selection.profile-as-policy.invalid.json",
        )
        self.assertNotEqual([], list(validator.iter_errors(invalid)))

    def test_rag_source_card_schema_and_semantics_reject_control_text(self) -> None:
        schema = load_json_bytes_strict(
            (ROOT / "contracts/schemas/rag-source-card-v1.schema.json").read_bytes(),
            source="contracts/schemas/rag-source-card-v1.schema.json",
        )
        validator = Draft202012Validator(schema)
        valid = load_json_bytes_strict(
            (ROOT / "contracts/examples/rag-source-card-v1.valid.json").read_bytes(),
            source="contracts/examples/rag-source-card-v1.valid.json",
        )
        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_rag_source_card_semantics(valid)

        injection = load_json_bytes_strict(
            (
                ROOT
                / "contracts/examples/invalid"
                / "rag-source-card-v1.injection-like.invalid.json"
            ).read_bytes(),
            source="rag-source-card-v1.injection-like.invalid.json",
        )
        self.assertEqual([], list(validator.iter_errors(injection)))
        with self.assertRaises(ContractValidationError):
            validate_rag_source_card_semantics(injection)

        unsafe_ip = copy.deepcopy(valid)
        unsafe_ip["canonicalUrl"] = "https://127.0.0.1/private"
        unsafe_ip["canonicalUrlSha256"] = hashlib.sha256(
            unsafe_ip["canonicalUrl"].encode("utf-8")
        ).hexdigest()
        with self.assertRaises(ContractValidationError):
            validate_rag_source_card_semantics(unsafe_ip)

        for field, value in (
            ("verifiedAt", "2026-07-30T09:00:00+09:00"),
            ("upstreamSourceIds", ["src_kis_nonexistent_999"]),
        ):
            drifted = copy.deepcopy(valid)
            drifted[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ContractValidationError):
                    validate_rag_source_card_semantics(drifted)

        authority_mismatch = copy.deepcopy(valid)
        authority_mismatch["institution"] = "krx"
        authority_mismatch["upstreamSourceIds"] = ["src_krx_openapi_service_catalog_001"]
        with self.assertRaises(ContractValidationError):
            validate_rag_source_card_semantics(authority_mismatch)

        for unsafe_url in (
            "https://Example.com/private",
            "https://example.com/a/../private",
            "https://example.com/a/./private",
            "https://example.com/a//private",
        ):
            unsafe_shape = copy.deepcopy(valid)
            unsafe_shape["canonicalUrl"] = unsafe_url
            unsafe_shape["canonicalUrlSha256"] = hashlib.sha256(
                unsafe_url.encode("utf-8")
            ).hexdigest()
            with self.subTest(unsafe_url=unsafe_url):
                with self.assertRaises(ContractValidationError):
                    validate_rag_source_card_semantics(unsafe_shape)
