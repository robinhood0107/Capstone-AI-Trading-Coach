from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s4_rag_contracts import (
    CATALOG_PATH,
    EXPECTED_CATALOG_SHA256,
    OUTPUTS,
    generate_outputs,
    load_catalog,
    load_json_bytes_strict,
    validate_admin_policy_selection_semantics,
    validate_catalog_semantics,
    validate_rag_ask_request_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


class S4RagContractCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_catalog_locks_exact_profiles_policies_and_digest(self) -> None:
        self.assertEqual(EXPECTED_CATALOG_SHA256, hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest())
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
        self.assertEqual(1024, self.catalog["dimension"])
        self.assertEqual(["CONCISE", "DETAILED"], self.catalog["answerModes"])
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
