from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.validate_p1_full_app_release import semantic_errors


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "deploy/p1/full-app-release-manifest.v2.schema.json"
CATALOG_PATH = ROOT / "contracts/catalogs/p1-full-app-release-contract.v2.json"
SHA_A = "a" * 64
SHA_B = "b" * 64
GIT_SHA = "c" * 40
REQUIRED_IMAGES = (
    "gateway",
    "experience-dashboard",
    "spring-api",
    "python-services",
    "strong-llm",
    "return-engine",
    "searxng",
    "postgres-pgvector",
    "redis",
)


def _manifest(stage: str = "CANDIDATE") -> dict[str, object]:
    image = {
        "component": "spring-api",
        "reference": f"ghcr.io/example/spring-api@sha256:{SHA_A}",
        "digest": f"sha256:{SHA_A}",
        "platform": "linux/amd64",
    }
    gates = {
        "P1_CORE": "PASS",
        "PUBLIC_RAG_SEED": "PASS",
        "OWNER_RAG_BACKEND": "PASS",
        "BGE_OCR_CPU_INTEL": "PASS",
        "PROVIDER_LIVE_READ": "PASS",
        "TEAM_B_REAL_ARTIFACT": "PASS",
        "SECURITY_RELEASE": "PASS",
        "SUPPLY_CHAIN_RELEASE": "PASS",
        "COMPOSE_E2E": "PASS",
    }
    return {
        "contractId": "p1-full-app-release-manifest.v2",
        "releaseVersion": "1.0.0",
        "releaseStage": stage,
        "commitSha": GIT_SHA,
        "treeSha": GIT_SHA,
        "platform": "linux/amd64",
        "images": [dict(image, component=component) for component in REQUIRED_IMAGES],
        "publicRagSeed": {
            "schemaVersion": "p1-public-rag-seed.v1",
            "sourceSchemaVersion": "73",
            "targetSchemaVersion": "87",
            "manifestPath": "deploy/p1/seed/public-rag/public-rag-seed.v1.manifest.json",
            "manifestSha256": SHA_A,
            "archiveSha256": SHA_B,
            "sources": 142,
            "chunks": 7871,
            "dimensions": 1024,
            "parts": [
                {
                    "path": "deploy/p1/seed/public-rag/public-rag-seed.v1.jsonl.gz.part-0001",
                    "size": 1024,
                    "sha256": SHA_A,
                },
                {
                    "path": "deploy/p1/seed/public-rag/public-rag-seed.v1.jsonl.gz.part-0002",
                    "size": 1024,
                    "sha256": SHA_B,
                },
            ],
        },
        "modelAssets": [
            {
                "component": "bge-m3",
                "revision": "5617a9f61b028005a4858fdac845db406aefb181",
                "manifestSha256": SHA_A,
                "licenseEvidenceSha256": SHA_B,
            },
            {
                "component": "paddleocr-vl-1.6",
                "revision": "66317acc4c9fc17bd154591ce650735cd2855f3e",
                "manifestSha256": SHA_A,
                "licenseEvidenceSha256": SHA_B,
            },
        ],
        "hardGates": gates,
        "capabilities": {
            "DASHBOARD_UI": "PARTIAL_TEAM_A_ACTION_REQUIRED",
            "CUDA": "PENDING_USER_HARDWARE_VERIFICATION",
            "SEARXNG": "KNOWN_DEGRADED_NONBLOCKING",
            "LIGHTGBM": "RESEARCH_ONLY_NOT_APPLICABLE",
            "LIVE_ORDER": "FUTURE_NOT_IMPLEMENTED",
        },
        "providerLiveReceipt": {
            "receiptSha256": SHA_A,
            "voyageQueryCalls": 1,
            "googleGroundedVertexCalls": 1,
            "kisTokenCalls": 1,
            "kisDataCalls": 2,
            "ecosCalls": 2,
            "accountCalls": 0,
            "balanceCalls": 0,
            "orderCalls": 0,
            "retries": 0,
        },
        "supplyChain": {
            "sbomSha256": SHA_A,
            "provenanceSha256": SHA_A,
            "signatureBundleSha256": SHA_A,
            "sourceArchiveSha256": SHA_A,
            "licenseSha256": "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
        },
    }


class P1FullAppReleaseContractV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_catalog_has_exact_release_authority_and_hard_gates(self) -> None:
        self.assertEqual("p1-full-app-release-contract.v2", self.catalog["contractId"])
        self.assertEqual("1.0.0", self.catalog["releaseVersion"])
        self.assertEqual(".github/workflows/p1-full-app-release.yml", self.catalog["releaseAuthorityWorkflow"])
        self.assertEqual(list(REQUIRED_IMAGES), self.catalog["requiredImageComponents"])
        self.assertEqual(["kafka"], self.catalog["optionalImageComponents"])
        self.assertEqual(
            "contracts/schemas/p1-return-engine-artifact-manifest.v1.schema.json",
            self.catalog["returnEngineArtifactSchema"],
        )
        self.assertEqual(
            "5617a9f61b028005a4858fdac845db406aefb181",
            self.catalog["modelAssets"]["bge-m3"]["revision"],
        )
        self.assertEqual("MATERIALIZED", self.catalog["modelAssets"]["bge-m3"]["inventoryStatus"])
        self.assertEqual(
            "NOT_MATERIALIZED",
            self.catalog["modelAssets"]["paddleocr-vl-1.6"]["inventoryStatus"],
        )
        self.assertEqual(
            {
                "P1_CORE",
                "PUBLIC_RAG_SEED",
                "OWNER_RAG_BACKEND",
                "BGE_OCR_CPU_INTEL",
                "PROVIDER_LIVE_READ",
                "TEAM_B_REAL_ARTIFACT",
                "SECURITY_RELEASE",
                "SUPPLY_CHAIN_RELEASE",
                "COMPOSE_E2E",
            },
            set(self.catalog["hardGates"]),
        )

    def test_candidate_and_final_accept_complete_manifest(self) -> None:
        for stage in ("CANDIDATE", "FINAL"):
            self.assertEqual([], list(self.validator.iter_errors(_manifest(stage))))

    def test_final_rejects_any_non_pass_hard_gate(self) -> None:
        payload = _manifest("FINAL")
        payload["hardGates"]["TEAM_B_REAL_ARTIFACT"] = "BLOCKED"
        self.assertNotEqual([], list(self.validator.iter_errors(payload)))

    def test_candidate_may_report_blocked_gate_without_claiming_release(self) -> None:
        payload = _manifest("CANDIDATE")
        payload["hardGates"]["TEAM_B_REAL_ARTIFACT"] = "BLOCKED"
        self.assertEqual([], list(self.validator.iter_errors(payload)))

    def test_live_receipt_rejects_order_balance_retry_and_cap_expansion(self) -> None:
        for field, value in (("orderCalls", 1), ("balanceCalls", 1), ("retries", 1), ("kisDataCalls", 3)):
            payload = copy.deepcopy(_manifest())
            payload["providerLiveReceipt"][field] = value
            self.assertNotEqual([], list(self.validator.iter_errors(payload)), field)

    def test_final_requires_the_exact_live_read_receipt_counts(self) -> None:
        for field in ("voyageQueryCalls", "googleGroundedVertexCalls", "kisDataCalls", "ecosCalls"):
            payload = copy.deepcopy(_manifest("FINAL"))
            payload["providerLiveReceipt"][field] = 0
            self.assertNotEqual([], list(self.validator.iter_errors(payload)), field)

    def test_schema_rejects_missing_or_duplicate_release_assets(self) -> None:
        missing_image = copy.deepcopy(_manifest())
        missing_image["images"].pop()
        self.assertNotEqual([], list(self.validator.iter_errors(missing_image)))

        duplicate_model = copy.deepcopy(_manifest())
        duplicate_model["modelAssets"][1] = copy.deepcopy(duplicate_model["modelAssets"][0])
        self.assertNotEqual([], list(self.validator.iter_errors(duplicate_model)))

        wrong_revision = copy.deepcopy(_manifest())
        wrong_revision["modelAssets"][0]["revision"] = "unpinned"
        self.assertNotEqual([], list(self.validator.iter_errors(wrong_revision)))

        zero_digest = copy.deepcopy(_manifest())
        zero_digest["images"][0]["digest"] = f"sha256:{'0' * 64}"
        self.assertNotEqual([], list(self.validator.iter_errors(zero_digest)))

    def test_semantic_validation_binds_image_seed_license_and_git_identity(self) -> None:
        payload = copy.deepcopy(_manifest())
        payload["commitSha"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        payload["treeSha"] = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        seed_manifest_path = ROOT / payload["publicRagSeed"]["manifestPath"]
        seed_manifest = json.loads(seed_manifest_path.read_text(encoding="utf-8"))
        payload["publicRagSeed"]["manifestSha256"] = hashlib.sha256(seed_manifest_path.read_bytes()).hexdigest()
        payload["publicRagSeed"]["archiveSha256"] = seed_manifest["archiveSha256"]
        for release_part, manifest_part in zip(
            payload["publicRagSeed"]["parts"], seed_manifest["parts"], strict=True
        ):
            release_part["size"] = manifest_part["sizeBytes"]
            release_part["sha256"] = manifest_part["sha256"]

        self.assertEqual([], semantic_errors(payload, ROOT))

        payload["images"][0]["reference"] = payload["images"][0]["reference"].replace(SHA_A, SHA_B)
        self.assertIn(
            "IMAGE_REFERENCE_DIGEST_MISMATCH:gateway",
            semantic_errors(payload, ROOT),
        )

    def test_seed_and_license_invariants_are_closed(self) -> None:
        for path, value in (
            (("publicRagSeed", "sources"), 141),
            (("publicRagSeed", "chunks"), 7870),
            (("publicRagSeed", "dimensions"), 4096),
            (("supplyChain", "licenseSha256"), "0" * 64),
        ):
            payload = copy.deepcopy(_manifest())
            payload[path[0]][path[1]] = value
            self.assertNotEqual([], list(self.validator.iter_errors(payload)), path)

    def test_v1_release_contract_bytes_remain_frozen(self) -> None:
        import hashlib

        digest = hashlib.sha256((ROOT / "deploy/p1/release-manifest.schema.json").read_bytes()).hexdigest()
        self.assertEqual("c5cc34f796205b7fc5dc6b80d838b2f5ba0ff2008e5e8ff499301a799d40f57e", digest)


if __name__ == "__main__":
    unittest.main()
