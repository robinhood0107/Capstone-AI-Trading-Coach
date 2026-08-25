from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "deploy/p1/full-app-release-manifest.v2.schema.json"
CATALOG_PATH = ROOT / "contracts/catalogs/p1-full-app-release-contract.v2.json"
ZERO_SHA = "0" * 64
ZERO_GIT_SHA = "0" * 40


def _manifest(stage: str = "CANDIDATE") -> dict[str, object]:
    image = {
        "component": "spring-api",
        "reference": f"ghcr.io/example/spring-api@sha256:{ZERO_SHA}",
        "digest": f"sha256:{ZERO_SHA}",
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
        "commitSha": ZERO_GIT_SHA,
        "treeSha": ZERO_GIT_SHA,
        "platform": "linux/amd64",
        "images": [dict(image, component=f"component-{index}") for index in range(8)],
        "publicRagSeed": {
            "schemaVersion": "V87",
            "archiveSha256": ZERO_SHA,
            "sources": 142,
            "chunks": 7871,
            "dimensions": 1024,
            "parts": [{"path": "seed/part-0001", "size": 1024, "sha256": ZERO_SHA}],
        },
        "modelAssets": [
            {
                "component": "bge-m3",
                "revision": "pinned-revision",
                "manifestSha256": ZERO_SHA,
                "licenseEvidenceSha256": ZERO_SHA,
            },
            {
                "component": "paddleocr-vl-1.6",
                "revision": "pinned-revision",
                "manifestSha256": ZERO_SHA,
                "licenseEvidenceSha256": ZERO_SHA,
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
            "receiptSha256": ZERO_SHA,
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
            "sbomSha256": ZERO_SHA,
            "provenanceSha256": ZERO_SHA,
            "signatureBundleSha256": ZERO_SHA,
            "sourceArchiveSha256": ZERO_SHA,
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

    def test_seed_and_license_invariants_are_closed(self) -> None:
        for path, value in (
            (("publicRagSeed", "sources"), 141),
            (("publicRagSeed", "chunks"), 7870),
            (("publicRagSeed", "dimensions"), 4096),
            (("supplyChain", "licenseSha256"), ZERO_SHA),
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
