from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from contracts.generate_principle_contracts import CATALOG_PATH, canonical_json_bytes
from contracts.normalize_openapi import (
    CANONICAL_GENERATED_SERVER,
    normalize_generated_openapi,
)


ROOT = Path(__file__).resolve().parents[1]


class FullOpenApiRootProjectionTest(unittest.TestCase):
    def test_full_auth_and_admin_paths_stay_in_their_product_contracts(self) -> None:
        expected_path = ROOT / "openapi/openapi.json"
        expected_bytes = expected_path.read_bytes()
        expected = json.loads(expected_bytes)
        generated = copy.deepcopy(expected)
        generated["openapi"] = "3.1.0"
        generated["servers"] = [copy.deepcopy(CANONICAL_GENERATED_SERVER)]

        auth = json.loads((ROOT / "openapi/mars-full-auth.v1.openapi.json").read_text())
        admin = json.loads((ROOT / "openapi/mars-full-admin.v1.openapi.json").read_text())
        inputs = json.loads((ROOT / "openapi/mars-full-decision-inputs.v1.openapi.json").read_text())
        for document in (auth, admin, inputs):
            for path, item in document["paths"].items():
                for method, operation in item.items():
                    if method not in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}:
                        continue
                    if path in generated["paths"] and method in generated["paths"][path]:
                        continue
                    generated["paths"].setdefault(path, {})[method] = copy.deepcopy(operation)
            for name, schema in document.get("components", {}).get("schemas", {}).items():
                generated["components"]["schemas"].setdefault(name, copy.deepcopy(schema))

        normalized = normalize_generated_openapi(
            canonical_json_bytes(generated),
            CATALOG_PATH.read_bytes(),
            amendment=False,
            expected_bytes=expected_bytes,
        )
        self.assertEqual(expected_bytes, normalized)


if __name__ == "__main__":
    unittest.main()
