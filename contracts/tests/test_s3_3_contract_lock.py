from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class S33FillContractTest(unittest.TestCase):
    def test_generator_catalog_and_required_schemas_exist(self) -> None:
        self.assertIsNotNone(
            importlib.util.spec_from_file_location(
                "generate_s3_3_contracts",
                ROOT / "contracts/generate_s3_3_contracts.py",
            )
        )
        self.assertTrue(
            (ROOT / "contracts/catalogs/s3-3-fill-contract.v1.json").is_file()
        )
        for name in (
            "s3-3-fill-observation",
            "s3-3-reconcile-response",
            "s3-3-fill-page",
        ):
            self.assertTrue(
                (ROOT / f"contracts/schemas/{name}.schema.json").is_file(),
                name,
            )

    def test_openapi_has_reconcile_and_two_owner_fill_routes_only(self) -> None:
        document = json.loads(
            (ROOT / "contracts/openapi/openapi.json").read_text(encoding="utf-8")
        )
        paths = document["paths"]
        self.assertIn("/api/v1/brokerage/orders/{orderId}/reconcile", paths)
        self.assertIn(
            "/api/v1/brokerage/mock/accounts/{accountId}/fills",
            paths,
        )
        self.assertIn(
            "/api/v1/brokerage/paper/accounts/{accountId}/fills",
            paths,
        )
        self.assertFalse(
            any("report-fill" in path or "fill-observation" in path for path in paths)
        )


if __name__ == "__main__":
    unittest.main()
