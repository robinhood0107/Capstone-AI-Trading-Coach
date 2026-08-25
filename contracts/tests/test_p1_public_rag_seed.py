from __future__ import annotations

import hashlib
import json
import stat
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/schemas/p1-public-rag-seed-manifest.v1.schema.json"
SEED_ROOT = ROOT / "deploy/p1/seed/public-rag"
MANIFEST = SEED_ROOT / "public-rag-seed.v1.manifest.json"


class P1PublicRagSeedContractTest(unittest.TestCase):
    def test_manifest_and_parts_are_closed_and_hash_bound(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(manifest)

        archive_hash = hashlib.sha256()
        archive_size = 0
        for expected_ordinal, part in enumerate(manifest["parts"], start=1):
            self.assertEqual(expected_ordinal, part["ordinal"])
            path = SEED_ROOT / part["file"]
            metadata = path.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertFalse(stat.S_ISLNK(metadata.st_mode))
            payload = path.read_bytes()
            self.assertEqual(part["sizeBytes"], len(payload))
            self.assertEqual(part["sha256"], hashlib.sha256(payload).hexdigest())
            archive_hash.update(payload)
            archive_size += len(payload)

        self.assertEqual(manifest["archiveSizeBytes"], archive_size)
        self.assertEqual(manifest["archiveSha256"], archive_hash.hexdigest())

    def test_seed_assets_have_no_unlisted_regular_files(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected = {MANIFEST.name, *(part["file"] for part in manifest["parts"])}
        observed = {path.name for path in SEED_ROOT.iterdir() if path.is_file()}
        self.assertEqual(expected, observed)


if __name__ == "__main__":
    unittest.main()
