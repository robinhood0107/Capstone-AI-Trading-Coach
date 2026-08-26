from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.verify_p1_full_app_assets import RETURN_ARTIFACTS, verify_assets


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _file_record(root: Path, relative: str, payload: bytes) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": relative,
        "sizeBytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_return_manifest(root: Path) -> Path:
    artifacts = [
        _file_record(root, f"files/{name}", f"return-{name}".encode())
        for name in sorted(RETURN_ARTIFACTS)
    ]
    manifest = {
        "contractId": "p1-return-engine-artifact-manifest.v1",
        "evidenceMode": "REAL_TEAM_B",
        "producer": {
            "commitSha256": SHA,
            "dependencyLockSha256": SHA,
            "dockerfileSha256": SHA,
            "sourceSnapshotSha256": SHA,
            "trainingCodeSha256": SHA,
            "featureOrderSha256": SHA,
            "splitSha256": SHA,
            "configSha256": SHA,
            "goldenOutputSha256": SHA,
            "seed": 20260826,
            "windowSessions": 253,
        },
        "forecast": {
            "nextXkrxSession": "2026-08-27",
            "currentClose": 100.0,
            "forecastClose": 101.0,
            "expectedReturn": 0.01,
        },
        "strategies": {
            "BASELINE": {"transactionCostBps": 0.0, "taxBps": 0.0, "slippageBps": 0.0},
            "GUIDE": {"transactionCostBps": 5.0, "taxBps": 15.0, "slippageBps": 5.0},
            "STRICT": {"transactionCostBps": 10.0, "taxBps": 15.0, "slippageBps": 10.0},
        },
        "artifacts": artifacts,
    }
    path = root / "p1-return-engine-manifest.v1.json"
    _write_json(path, manifest)
    return path


class P1FullAppAssetVerifierTest(unittest.TestCase):
    def test_return_engine_schema_accepts_only_closed_real_manifest_shape(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/schemas/p1-return-engine-artifact-manifest.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_return_manifest(Path(temporary))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([], list(validator.iter_errors(payload)))
            payload["producer"]["dependencyLockSha256"] = "0" * 64
            self.assertNotEqual([], list(validator.iter_errors(payload)))

    def test_catalog_pins_official_container_runtimes_and_model_revisions(self) -> None:
        catalog = json.loads(
            (ROOT / "contracts/catalogs/p1-full-app-release-contract.v2.json").read_text(encoding="utf-8")
        )
        bge = catalog["modelAssets"]["bge-m3"]
        self.assertEqual("BAAI/bge-m3", bge["repository"])
        self.assertEqual("5617a9f61b028005a4858fdac845db406aefb181", bge["revision"])
        self.assertEqual("HUGGINGFACE_TEXT_EMBEDDINGS_INFERENCE_CPU_1_9", bge["runtime"])
        self.assertRegex(bge["imageReference"], r"^ghcr\.io/huggingface/.+@sha256:[0-9a-f]{64}$")

        paddle = catalog["modelAssets"]["paddleocr-vl-1.6"]
        self.assertEqual("PaddlePaddle/PaddleOCR-VL-1.6-GGUF", paddle["repository"])
        self.assertEqual("511b09642bb324401f15f97cc23bc67e8f0a291d", paddle["revision"])
        self.assertEqual("LLAMA_CPP_SERVER_B10524", paddle["runtime"])
        self.assertEqual(935769056, paddle["modelFile"]["sizeBytes"])
        self.assertEqual(881770560, paddle["mmprojFile"]["sizeBytes"])

    def test_official_model_compose_has_no_host_port_or_community_bge(self) -> None:
        compose = (ROOT / "deploy/p1/compose.full.models.yml").read_text(encoding="utf-8")
        self.assertIn("ghcr.io/huggingface/text-embeddings-inference:cpu-1.9@sha256:", compose)
        self.assertIn("BAAI/bge-m3", compose)
        self.assertIn("ghcr.io/ggml-org/llama.cpp:server-b10524@sha256:", compose)
        self.assertIn("PaddlePaddle/PaddleOCR-VL-1.6-GGUF", compose)
        self.assertIn("paddleocr-vl-model-fetch", compose)
        self.assertIn("--model", compose)
        self.assertIn("--mmproj", compose)
        self.assertIn("sha256sum -c -", compose)
        self.assertIn("p1-model-fetch", compose)
        self.assertNotIn("lm-kit", compose)
        self.assertNotIn("ports:", compose)

    def test_real_return_artifact_passes_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _write_return_manifest(root)
            self.assertEqual([], verify_assets(manifest, ROOT))

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["forecast"]["expectedReturn"] = 0.2
            _write_json(manifest, payload)
            self.assertIn("RETURN:FORECAST:FORMULA", verify_assets(manifest, ROOT))

    def test_empty_return_marker_never_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "p1-return-engine-manifest.v1.json"
            _write_json(path, {})
            errors = verify_assets(path, ROOT)
            self.assertIn("RETURN:EVIDENCE_MODE", errors)
            self.assertIn("RETURN:ARTIFACT_SET", errors)


if __name__ == "__main__":
    unittest.main()
