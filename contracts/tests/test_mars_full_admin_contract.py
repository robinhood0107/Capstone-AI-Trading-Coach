from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_spec_validator import validate


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "openapi/mars-full-admin.v1.openapi.json"


class MarsFullAdminContractTest(unittest.TestCase):
    def test_admin_surface_is_role_gated_and_exposes_no_provider_secret(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        operations = {
            (method.upper(), path): operation
            for path, item in document["paths"].items()
            for method, operation in item.items()
            if method in {"get", "put"}
        }
        self.assertEqual(
            {
                ("GET", "/api/v1/admin/ai"),
                ("PUT", "/api/v1/admin/ai/operator-fallback"),
                ("GET", "/api/v1/admin/automation"),
                ("GET", "/api/v1/admin/limits"),
                ("PUT", "/api/v1/admin/limits"),
                ("GET", "/api/v1/admin/users"),
                ("PUT", "/api/v1/admin/users/{userId}/access"),
            },
            set(operations),
        )
        for operation in operations.values():
            self.assertEqual("ADMIN", operation["x-required-role"])
            self.assertEqual([{"bearerAuth": []}], operation["security"])
            self.assertTrue({"200", "401", "403"}.issubset(operation["responses"]))

        operator = document["components"]["schemas"]["OperatorVertexStatus"]
        self.assertEqual(
            {"configured", "projectId", "modelId", "reachable"},
            set(operator["properties"]),
        )
        usage = document["components"]["schemas"]["AdminAiUsageRow"]
        self.assertIn("hasOwnKey", usage["properties"])
        self.assertNotIn("privateKey", usage["properties"])
        self.assertNotIn("serviceAccountJson", usage["properties"])


if __name__ == "__main__":
    unittest.main()
