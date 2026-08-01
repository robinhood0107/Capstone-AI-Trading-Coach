from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s4_7d_rag_v2_contracts import (
    CATALOG_PATH,
    FROZEN_V1_HASHES,
    INVALID_FIXTURE_PATHS,
    OA_TRACK_IDS,
    OCR_CANDIDATES,
    OUTPUTS,
    SCHEMA_PATHS,
    SUPPORTED_MIME_TYPES,
    VALID_FIXTURE_PATHS,
    canonical_tree_digest,
    generate_outputs,
    load_catalog,
    load_json,
    validate_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


class S47dRagV2ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog()
        cls.validators = {
            schema_id: Draft202012Validator(
                load_json(path), format_checker=FormatChecker()
            )
            for schema_id, path in SCHEMA_PATHS.items()
        }

    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = generate_outputs(copy.deepcopy(self.catalog))
        second = generate_outputs(copy.deepcopy(self.catalog))

        self.assertEqual(first, second)
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s4_7d_rag_v2_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S4_7D_RAG_V2_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_v1_and_exact_30_bytes_are_frozen(self) -> None:
        for relative_path, expected_hash in FROZEN_V1_HASHES.items():
            with self.subTest(relative_path=relative_path):
                self.assertEqual(
                    expected_hash,
                    hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest(),
                )

        self.assertEqual(
            "0336148dd05841861fbd3f054ed5eaa72ea511e341bb0ae205223a7da1de95a2",
            canonical_tree_digest(ROOT / "capstone-rag/source-cards/s4-7b"),
        )
        self.assertEqual(
            "84b70aa3bfc1e24bbff939bd1b6c25d6fb9ec50e03eb36929fa9ede232051874",
            canonical_tree_digest(ROOT / "capstone-rag/source-cards/s4-7c-external"),
        )

    def test_catalog_locks_server_selection_tracks_formats_and_active_policy(self) -> None:
        self.assertEqual("s4-rag-v2-contract.v1", self.catalog["contractId"])
        self.assertEqual(
            [
                "PROJECT_SOURCE_CARD",
                "OPEN_ACCESS_DOCUMENT",
                "OWNER_LOCAL_DOCUMENT",
            ],
            self.catalog["sourceKinds"],
        )
        self.assertEqual(list(OA_TRACK_IDS), self.catalog["curriculumTracks"])
        self.assertEqual(14, len(self.catalog["curriculumTracks"]))
        self.assertEqual(list(SUPPORTED_MIME_TYPES), self.catalog["supportedMimeTypes"])
        self.assertEqual(list(OCR_CANDIDATES), self.catalog["ocrResearchCandidates"])
        self.assertEqual(["LOCAL_EPHEMERAL_PARSE"], self.catalog["activeProcessingModes"])
        self.assertEqual(
            ["LICENSED_EPHEMERAL_LOCAL"],
            self.catalog["historicalOnlyProcessingModes"],
        )
        self.assertEqual(60, self.catalog["retrieval"]["rrfK"])
        self.assertEqual(
            ["exact30", "oa", "ownerPrivate"],
            self.catalog["serverSelectedBundle"]["components"],
        )
        self.assertFalse(self.catalog["clientSelection"]["corpusAllowed"])
        self.assertFalse(self.catalog["clientSelection"]["profileAllowed"])
        self.assertFalse(self.catalog["clientSelection"]["topKAllowed"])
        self.assertEqual("NONE", self.catalog["decisionAuthority"])

    def test_all_valid_fixtures_pass_schema_and_semantics(self) -> None:
        for relative_path in sorted(VALID_FIXTURE_PATHS):
            payload = load_json(ROOT / relative_path)
            self.assertIsInstance(payload, dict)
            schema_id = payload["contractId"]
            with self.subTest(relative_path=relative_path):
                self.assertEqual(
                    [], list(self.validators[schema_id].iter_errors(payload))
                )
                validate_semantics(schema_id, payload)

    def test_all_required_invalid_fixtures_fail_closed(self) -> None:
        required_labels = {
            "absolute-path",
            "client-corpus-selector",
            "client-profile-selector",
            "client-top-k-selector",
            "duplicate-reading-order",
            "external-llm-without-local-processing",
            "local-citation-url",
            "oa-machine-fetch-disabled",
            "owner-machine-fetch",
            "owner-redistribution",
            "released-underfilled",
            "status-leaks-hash",
            "unknown-field",
        }
        self.assertEqual(
            required_labels,
            {
                Path(path).name.split(".")[-3]
                for path in INVALID_FIXTURE_PATHS
            },
        )

        for relative_path in sorted(INVALID_FIXTURE_PATHS):
            payload = load_json(ROOT / relative_path)
            self.assertIsInstance(payload, dict)
            schema_id = payload["contractId"]
            schema_errors = list(self.validators[schema_id].iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not schema_errors:
                try:
                    validate_semantics(schema_id, payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            with self.subTest(relative_path=relative_path):
                self.assertTrue(schema_errors or semantic_error is not None)

    def test_source_v3_requires_kind_specific_provenance_and_never_a_path(self) -> None:
        fixtures = {
            load_json(ROOT / path)["sourceKind"]: load_json(ROOT / path)
            for path in VALID_FIXTURE_PATHS
            if Path(path).name.startswith("rag-source-card-v3.")
        }
        self.assertEqual(
            {
                "PROJECT_SOURCE_CARD",
                "OPEN_ACCESS_DOCUMENT",
                "OWNER_LOCAL_DOCUMENT",
            },
            set(fixtures),
        )
        owner = fixtures["OWNER_LOCAL_DOCUMENT"]
        self.assertIn("opaqueLocalDocumentId", owner)
        self.assertNotIn("canonicalUrl", owner)
        self.assertNotIn("downloadUrl", owner)
        serialized = json.dumps(owner, ensure_ascii=False)
        self.assertNotIn("/home/", serialized)
        self.assertNotIn("C:\\", serialized)
        self.assertFalse(owner["machineFetchAllowed"])
        self.assertFalse(owner["redistributionAllowed"])

        oa = fixtures["OPEN_ACCESS_DOCUMENT"]
        self.assertTrue(oa["canonicalUrl"].startswith("https://"))
        self.assertTrue(oa["downloadUrl"].startswith("https://"))
        self.assertTrue(oa["machineFetchAllowed"])
        self.assertTrue(oa["localProcessingAllowed"])

    def test_document_ir_preserves_structure_locator_order_and_ocr_evidence(self) -> None:
        native = load_json(ROOT / "contracts/examples/rag-document-ir-v1.native.valid.json")
        ocr = load_json(ROOT / "contracts/examples/rag-document-ir-v1.ocr.valid.json")

        self.assertEqual(
            ["HEADING", "PARAGRAPH", "TABLE", "FORMULA", "CAPTION"],
            [block["blockType"] for block in native["blocks"]],
        )
        self.assertEqual(
            list(range(len(native["blocks"]))),
            [block["readingOrder"] for block in native["blocks"]],
        )
        self.assertTrue(all(block["ocrConfidence"] is None for block in native["blocks"]))
        self.assertTrue(any(block["ocrConfidence"] is not None for block in ocr["blocks"]))
        self.assertEqual("OCR", ocr["extractionMode"])
        self.assertNotIn("path", json.dumps(native, ensure_ascii=False).lower())

    def test_released_oa_manifest_enforces_track_floor_and_total_bounds(self) -> None:
        draft = load_json(ROOT / "contracts/examples/rag-oa-manifest-v1.draft.valid.json")
        self.assertEqual("DRAFT", draft["releaseStatus"])
        self.assertEqual(list(OA_TRACK_IDS), [track["trackId"] for track in draft["tracks"]])
        validate_semantics("rag-oa-manifest-v1", draft)

        released = copy.deepcopy(draft)
        released["releaseStatus"] = "RELEASED"
        with self.assertRaisesRegex(ContractValidationError, "112..140"):
            validate_semantics("rag-oa-manifest-v1", released)

    def test_v2_request_has_v1_meaning_and_rejects_all_retrieval_controls(self) -> None:
        v1 = load_json(ROOT / "contracts/schemas/s4-rag-ask-request.schema.json")
        v2 = load_json(ROOT / "contracts/schemas/s4-rag-v2-ask-request.schema.json")
        self.assertEqual(v1["required"], v2["required"])
        self.assertEqual(v1["properties"], v2["properties"])

        valid = load_json(ROOT / "contracts/examples/s4-rag-v2-ask-request.valid.json")
        validator = self.validators["s4-rag-v2-ask-request"]
        self.assertEqual([], list(validator.iter_errors(valid)))
        for key in ("corpus", "profile", "topK"):
            candidate = dict(valid)
            candidate[key] = "forbidden" if key != "topK" else 5
            self.assertTrue(list(validator.iter_errors(candidate)), key)

    def test_v2_citation_union_separates_public_web_and_local_document(self) -> None:
        public_answer = load_json(
            ROOT / "contracts/examples/s4-rag-v2-answer.public-web.valid.json"
        )
        local_answer = load_json(
            ROOT / "contracts/examples/s4-rag-v2-answer.local-document.valid.json"
        )
        public_citation = public_answer["citations"][0]
        local_citation = local_answer["citations"][0]

        self.assertEqual("PUBLIC_WEB", public_citation["citationKind"])
        self.assertTrue(public_citation["canonicalUrl"].startswith("https://"))
        self.assertEqual("LOCAL_DOCUMENT", local_citation["citationKind"])
        self.assertIn("documentId", local_citation)
        self.assertNotIn("canonicalUrl", local_citation)
        self.assertNotIn("path", json.dumps(local_citation).lower())

    def test_corpus_status_exposes_only_stable_progress_and_failure_code(self) -> None:
        status = load_json(
            ROOT / "contracts/examples/s4-rag-v2-corpus-status.building.valid.json"
        )
        self.assertEqual("BUILDING", status["state"])
        self.assertIn("progressPercent", status)
        serialized = json.dumps(status, ensure_ascii=False).lower()
        for forbidden in ("filename", "path", "credential", "sha256", "contenthash"):
            self.assertNotIn(forbidden, serialized)

    def test_separate_v2_openapi_locks_exact_routes_without_mutating_v1(self) -> None:
        document = load_json(ROOT / "contracts/openapi/rag-v2.openapi.json")
        self.assertEqual("3.1.1", document["openapi"])
        self.assertEqual(
            {
                "/api/v2/rag/ask",
                "/api/v2/rag/corpus-status",
                "/api/v2/rag/history",
                "/api/v2/rag/history/{answerId}",
            },
            set(document["paths"]),
        )
        self.assertEqual(
            ["get", "delete"],
            sorted(document["paths"]["/api/v2/rag/history/{answerId}"]),
        )
        self.assertEqual(
            "CORPUS_NOT_READY",
            document["components"]["schemas"]["RagV2Error"]["properties"]
            ["code"]["enum"][0],
        )

    def test_catalog_digest_manifest_matches_exact_catalog_bytes(self) -> None:
        manifest = load_json(
            ROOT / "contracts/catalogs/s4-rag-v2-contract.v1.sha256.json"
        )
        self.assertEqual(
            hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(), manifest["sha256"]
        )


if __name__ == "__main__":
    unittest.main()
