from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.verify_p1_full_app_assets import RETURN_ARTIFACTS, _inventory_sha256, verify_assets


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
MODEL_CONTRACTS = {
    "bge-m3": {
        "repository": "BAAI/bge-m3",
        "revision": "5617a9f61b028005a4858fdac845db406aefb181",
        "licenseSpdxId": "MIT",
        "fileCount": 10,
    },
    "paddleocr-vl-1.6": {
        "repository": "PaddlePaddle/PaddleOCR-VL-1.6",
        "revision": "66317acc4c9fc17bd154591ce650735cd2855f3e",
        "licenseSpdxId": "Apache-2.0",
        "fileCount": 5,
        "qualityCandidate": "PADDLE_VL",
        "qualityEvidenceSha256": "f43abfc2eaab0d6f958b8eac3369fc3f2f00c4d95569ae977e527bef3fc39f1a",
    },
}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _file_record(root: Path, relative: str, payload: bytes, size_key: str) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": relative,
        size_key: len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_model(model_root: Path, component: str) -> None:
    contract = MODEL_CONTRACTS[component]
    root = model_root / component
    files = [
        _file_record(root, f"files/asset-{index:02d}.bin", f"{component}-{index}".encode(), "bytes")
        for index in range(contract["fileCount"])
    ]
    manifest: dict[str, object] = {
        "schemaVersion": "model-artifact-manifest/v1",
        "repository": contract["repository"],
        "revision": contract["revision"],
        "license": {
            "spdxId": contract["licenseSpdxId"],
            "locator": f"https://example.invalid/{component}/{contract['revision']}",
        },
        "fileCount": len(files),
        "totalBytes": sum(item["bytes"] for item in files),
        "files": files,
    }
    if component == "bge-m3":
        manifest["graphContract"] = {"outputDimension": 1024}
    else:
        manifest["qualityCandidate"] = contract["qualityCandidate"]
        manifest["qualityEvidenceSha256"] = contract["qualityEvidenceSha256"]
    _write_json(root / "model-asset-manifest.v1.json", manifest)


def _write_test_catalog(root: Path, model_root: Path) -> Path:
    contracts: dict[str, object] = {}
    for component, base in MODEL_CONTRACTS.items():
        manifest_path = model_root / component / "model-asset-manifest.v1.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest["files"]
            inventory_sha256 = _inventory_sha256(files, "bytes")
            file_count = len(files)
            total_bytes = sum(item["bytes"] for item in files)
        except (OSError, KeyError, TypeError):
            inventory_sha256 = SHA
            file_count = 1
            total_bytes = 1
        contract = dict(base)
        contract.update(
            {
                "inventoryStatus": "MATERIALIZED",
                "inventorySha256": inventory_sha256,
                "fileCount": file_count,
                "totalBytes": total_bytes,
            }
        )
        contracts[component] = contract
    _write_json(root / "contracts/catalogs/p1-full-app-release-contract.v2.json", {"modelAssets": contracts})
    return root


def _write_return_manifest(root: Path) -> Path:
    artifacts = [
        _file_record(root, f"files/{name}", f"return-{name}".encode(), "sizeBytes")
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

    def test_tracked_bge_inventory_digest_matches_current_catalog(self) -> None:
        catalog = json.loads(
            (ROOT / "contracts/catalogs/p1-full-app-release-contract.v2.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (
                ROOT
                / "huggingface_model/manifests/bge-m3-onnx-5617a9f61b028005a4858fdac845db406aefb181.v1.json"
            ).read_text(encoding="utf-8")
        )
        contract = catalog["modelAssets"]["bge-m3"]
        self.assertEqual(contract["inventorySha256"], _inventory_sha256(manifest["files"], "bytes"))
        self.assertEqual(contract["fileCount"], manifest["fileCount"])
        self.assertEqual(contract["totalBytes"], manifest["totalBytes"])

    def test_exact_model_and_real_team_b_asset_inventory_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_root = root / "models"
            _write_model(model_root, "bge-m3")
            _write_model(model_root, "paddleocr-vl-1.6")
            return_manifest = _write_return_manifest(root / "return")
            contract_root = _write_test_catalog(root / "contract", model_root)

            self.assertEqual([], verify_assets(model_root, return_manifest, contract_root))

    def test_empty_json_markers_cannot_pass_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_root = root / "models"
            for component in MODEL_CONTRACTS:
                _write_json(model_root / component / "model-asset-manifest.v1.json", {})
            return_manifest = root / "return/p1-return-engine-manifest.v1.json"
            _write_json(return_manifest, {})
            contract_root = _write_test_catalog(root / "contract", model_root)

            errors = verify_assets(model_root, return_manifest, contract_root)
            self.assertIn("MODEL:bge-m3:SCHEMA", errors)
            self.assertIn("MODEL:paddleocr-vl-1.6:QUALITY_EVIDENCE", errors)
            self.assertIn("RETURN:EVIDENCE_MODE", errors)
            self.assertIn("RETURN:ARTIFACT_SET", errors)

    def test_hash_swap_symlink_and_wrong_return_formula_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_root = root / "models"
            _write_model(model_root, "bge-m3")
            _write_model(model_root, "paddleocr-vl-1.6")
            return_manifest = _write_return_manifest(root / "return")
            contract_root = _write_test_catalog(root / "contract", model_root)

            bge_asset = model_root / "bge-m3/files/asset-00.bin"
            bge_asset.write_bytes(b"changed")
            return_payload = json.loads(return_manifest.read_text(encoding="utf-8"))
            return_payload["forecast"]["expectedReturn"] = 0.2
            _write_json(return_manifest, return_payload)
            errors = verify_assets(model_root, return_manifest, contract_root)
            self.assertTrue(any(error.startswith("MODEL:bge-m3_FILE_INTEGRITY") for error in errors))
            self.assertIn("RETURN:FORECAST:FORMULA", errors)

            paddle_asset = model_root / "paddleocr-vl-1.6/files/asset-00.bin"
            target = root / "outside.bin"
            target.write_bytes(paddle_asset.read_bytes())
            paddle_asset.unlink()
            paddle_asset.symlink_to(target)
            errors = verify_assets(model_root, return_manifest, contract_root)
            self.assertTrue(any(error.startswith("MODEL:paddleocr-vl-1.6_FILE_BOUNDARY") for error in errors))

    def test_symlinked_model_component_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_root = root / "models"
            outside = root / "outside"
            _write_model(outside, "bge-m3")
            model_root.mkdir()
            (model_root / "bge-m3").symlink_to(outside / "bge-m3", target_is_directory=True)
            _write_model(model_root, "paddleocr-vl-1.6")
            return_manifest = _write_return_manifest(root / "return")
            contract_root = _write_test_catalog(root / "contract", model_root)

            self.assertIn(
                "MODEL:bge-m3:ROOT_BOUNDARY",
                verify_assets(model_root, return_manifest, contract_root),
            )

    def test_current_catalog_keeps_paddle_inventory_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_root = root / "models"
            _write_model(model_root, "bge-m3")
            _write_model(model_root, "paddleocr-vl-1.6")
            return_manifest = _write_return_manifest(root / "return")

            self.assertIn(
                "MODEL:paddleocr-vl-1.6:INVENTORY_NOT_MATERIALIZED",
                verify_assets(model_root, return_manifest, ROOT),
            )


if __name__ == "__main__":
    unittest.main()
