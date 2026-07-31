from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import (
    ContractValidationError,
    load_json_bytes_strict,
)
from contracts.generate_rag_source_card_v2_contracts import (
    INVALID_JSON_FIXTURE_PATHS,
    OUTPUTS,
    RAG_SOURCE_CARD_V2_COMMON_FIELDS,
    generate_outputs,
    validate_rag_source_card_v2_semantics,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/schemas/rag-source-card-v2.schema.json"
OFFICIAL_FIXTURE_PATH = (
    ROOT / "contracts/examples/rag-source-card-v2.official-migration.valid.json"
)
NAVER_OFFICIAL_FIXTURE_PATH = (
    ROOT / "contracts/examples/rag-source-card-v2.naver-official.valid.json"
)
SCHOLARLY_FIXTURE_PATH = (
    ROOT / "contracts/examples/rag-source-card-v2.scholarly.valid.json"
)


def _load(path: Path) -> object:
    return load_json_bytes_strict(path.read_bytes(), source=path.relative_to(ROOT).as_posix())


class RagSourceCardV2ContractTest(unittest.TestCase):
    def test_v1_artifacts_remain_byte_for_byte_immutable(self) -> None:
        self.assertEqual(
            "89f25e66d8165ceb813045e17c689e1000bb86f710f8d8c0acb22ccc6d0c846c",
            hashlib.sha256(
                (ROOT / "contracts/schemas/rag-source-card-v1.schema.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            "6a77525100c67a9bfcc1a966f1550cfe9bd19f73179544d716a5b8e963fea0c4",
            hashlib.sha256(
                (ROOT / "contracts/examples/rag-source-card-v1.valid.json").read_bytes()
            ).hexdigest(),
        )
        self.assertFalse(any("v1" in path for path in OUTPUTS))

    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = generate_outputs()
        second = generate_outputs()

        self.assertEqual(first, second)
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_rag_source_card_v2_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("RAG_SOURCE_CARD_V2_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_union_accepts_official_migration_naver_and_scholarly_primary(self) -> None:
        schema = _load(SCHEMA_PATH)
        self.assertIsInstance(schema, dict)
        validator = Draft202012Validator(schema)

        official = _load(OFFICIAL_FIXTURE_PATH)
        naver_official = _load(NAVER_OFFICIAL_FIXTURE_PATH)
        scholarly = _load(SCHOLARLY_FIXTURE_PATH)
        self.assertEqual([], list(validator.iter_errors(official)))
        self.assertEqual([], list(validator.iter_errors(naver_official)))
        self.assertEqual([], list(validator.iter_errors(scholarly)))
        validate_rag_source_card_v2_semantics(official)
        validate_rag_source_card_v2_semantics(naver_official)
        validate_rag_source_card_v2_semantics(scholarly)

        self.assertEqual("OFFICIAL_UPSTREAM_CARD", official["cardVariant"])
        self.assertEqual("naver", naver_official["institution"])
        self.assertEqual(
            [
                "src_naver_news_search_001",
                "src_naver_legacy_sunset_001",
            ],
            naver_official["upstreamSourceIds"],
        )
        self.assertEqual("SCHOLARLY_PRIMARY_CARD", scholarly["cardVariant"])
        self.assertEqual([], scholarly["upstreamSourceIds"])
        self.assertEqual(
            {
                "authorityType",
                "locatorType",
                "value",
            },
            set(scholarly["bibliographicLocator"]),
        )
        self.assertEqual(
            {"authors", "editionOrVersion", "title", "venue", "year"},
            set(scholarly["bibliographicMetadata"]),
        )
        self.assertTrue(scholarly["modelSensitive"])
        self.assertGreaterEqual(len(scholarly["modelAssumptions"]), 1)
        self.assertEqual(
            {"key", "statement"},
            set(scholarly["modelAssumptions"][0]),
        )

    def test_existing_official_fixture_has_meaning_preserving_v2_migration(self) -> None:
        v1 = _load(ROOT / "contracts/examples/rag-source-card-v1.valid.json")
        v2 = _load(OFFICIAL_FIXTURE_PATH)

        for field in (
            "sourceId",
            "cardId",
            "title",
            "institution",
            "topic",
            "sourceType",
            "tier",
            "accessLevel",
            "claim",
            "evidenceClass",
            "status",
            "verifiedAt",
            "accessNote",
            "licenseNote",
            "attribution",
            "canonicalUrl",
            "canonicalUrlSha256",
            "evidenceContentSha256",
            "upstreamSourceIds",
            "retentionOwner",
            "retentionDays",
            "externalProcessingAllowed",
            "adoptedSession",
            "contradicts",
            "limitations",
            "allowedUses",
            "forbiddenInferences",
            "representativeQuestions",
        ):
            with self.subTest(field=field):
                self.assertEqual(v1[field], v2[field])
        self.assertEqual("2", v2["schemaVersion"])
        self.assertEqual("PROJECT_AUTHORED_SANITIZED_CARD", v2["contentClass"])
        self.assertEqual("NOT_GRANTED", v2["externalProcessingGate"])
        self.assertFalse(v2["modelSensitive"])
        self.assertEqual([], v2["modelAssumptions"])

    def test_all_required_negative_fixtures_fail_closed(self) -> None:
        required_labels = {
            "scholarly-fake-upstream",
            "official-missing-upstream",
            "missing-locator",
            "missing-bibliographic-title",
            "missing-bibliographic-authors",
            "missing-edition-or-version",
            "secondary-blog",
            "raw-external-true",
            "sanitized-external-without-gate",
            "missing-access",
            "missing-license",
            "missing-retention",
            "missing-attribution",
            "model-assumption-empty",
            "private-path",
            "file-url",
            "localhost-url",
            "ip-url",
            "redirect-url",
            "injection-like",
            "unknown-field",
            "non-nfc",
            "control-character",
            "oversize",
        }
        self.assertTrue(
            required_labels.issubset(
                {
                    Path(path)
                    .name.removeprefix("rag-source-card-v2.")
                    .removesuffix(".invalid.json")
                    for path in INVALID_JSON_FIXTURE_PATHS
                }
            )
        )

        schema = _load(SCHEMA_PATH)
        self.assertIsInstance(schema, dict)
        validator = Draft202012Validator(schema)
        for relative_path in sorted(INVALID_JSON_FIXTURE_PATHS):
            payload = _load(ROOT / relative_path)
            schema_errors = list(validator.iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not schema_errors:
                try:
                    validate_rag_source_card_v2_semantics(payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            with self.subTest(relative_path=relative_path):
                self.assertTrue(schema_errors or semantic_error is not None)

    def test_semantics_reject_variant_gate_and_assumption_drift(self) -> None:
        official = _load(OFFICIAL_FIXTURE_PATH)
        scholarly = _load(SCHOLARLY_FIXTURE_PATH)
        self.assertIsInstance(official, dict)
        self.assertIsInstance(scholarly, dict)

        mutations: list[dict[str, object]] = []

        official_without_authority = copy.deepcopy(official)
        official_without_authority["upstreamSourceIds"] = [
            "src_krx_openapi_service_catalog_001"
        ]
        mutations.append(official_without_authority)

        scholarly_with_upstream = copy.deepcopy(scholarly)
        scholarly_with_upstream["upstreamSourceIds"] = [
            "src_kis_marketdata_daily_001"
        ]
        mutations.append(scholarly_with_upstream)

        external_without_gate = copy.deepcopy(official)
        external_without_gate["externalProcessingAllowed"] = True
        mutations.append(external_without_gate)

        duplicate_assumption_key = copy.deepcopy(scholarly)
        duplicate_assumption_key["modelAssumptions"] = [
            {"key": "ASSUMPTION_KEY", "statement": "첫 번째 bounded model assumption이다."},
            {"key": "ASSUMPTION_KEY", "statement": "두 번째 bounded model assumption이다."},
        ]
        mutations.append(duplicate_assumption_key)

        self.assertEqual(set(RAG_SOURCE_CARD_V2_COMMON_FIELDS), set(official))
        for mutation in mutations:
            with self.subTest(mutation=mutation["sourceId"]):
                with self.assertRaises(ContractValidationError):
                    validate_rag_source_card_v2_semantics(mutation)


if __name__ == "__main__":
    unittest.main()
