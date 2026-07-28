from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]


class S33FillContractTest(unittest.TestCase):
    def test_generator_catalog_and_required_schemas_exist(self) -> None:
        self.assertTrue(
            (ROOT / "contracts/generate_s3_3_contracts.py").is_file()
        )
        catalog = self._load(
            "contracts/catalogs/s3-3-fill-contract.v1.json"
        )
        self.assertEqual("s3-3-fill-contract/v1", catalog["contractId"])
        self.assertEqual(200, catalog["reconcileObservationMaximum"])
        self.assertEqual(31, catalog["cursor"]["dateRangeDaysMaximum"])
        self.assertEqual(50, catalog["cursor"]["pageSizeMaximum"])
        self.assertEqual(
            {
                "GET /api/v1/brokerage/mock/accounts/{accountId}/fills",
                "GET /api/v1/brokerage/paper/accounts/{accountId}/fills",
                "POST /api/v1/brokerage/orders/{orderId}/reconcile",
            },
            set(catalog["routes"].values()),
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

    def test_generator_check_uses_only_the_python_standard_library(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(ROOT / "contracts/generate_s3_3_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_fill_observation_schema_rejects_raw_or_inconsistent_reports(self) -> None:
        validator = self._validator("s3-3-fill-observation")
        valid = self._load("contracts/examples/s3-3-fill-observation.valid.json")
        self.assertEqual([], list(validator.iter_errors(valid)))
        for suffix in ("raw-ref", "terminal-quantity", "unknown"):
            invalid = self._load(
                f"contracts/examples/invalid/s3-3-fill-observation.{suffix}.invalid.json"
            )
            self.assertNotEqual([], list(validator.iter_errors(invalid)), suffix)

    def test_reconcile_and_fill_page_schemas_are_sanitized_and_bounded(self) -> None:
        reconcile = self._validator("s3-3-reconcile-response")
        fill_page = self._validator("s3-3-fill-page")
        self.assertEqual(
            [],
            list(
                reconcile.iter_errors(
                    self._load(
                        "contracts/examples/s3-3-reconcile-response.valid.json"
                    )
                )
            ),
        )
        self.assertEqual(
            [],
            list(
                fill_page.iter_errors(
                    self._load("contracts/examples/s3-3-fill-page.valid.json")
                )
            ),
        )
        self.assertNotEqual(
            [],
            list(
                reconcile.iter_errors(
                    self._load(
                        "contracts/examples/invalid/"
                        "s3-3-reconcile-response.account.invalid.json"
                    )
                )
            ),
        )
        self.assertNotEqual(
            [],
            list(
                fill_page.iter_errors(
                    self._load(
                        "contracts/examples/invalid/"
                        "s3-3-fill-page.raw-ref.invalid.json"
                    )
                )
            ),
        )
        self.assertEqual(
            50,
            self._load("contracts/schemas/s3-3-fill-page.schema.json")
            ["properties"]["items"]["maxItems"],
        )

        pending_mock = copy.deepcopy(
            self._load("contracts/examples/s3-3-reconcile-response.valid.json")
        )
        pending_mock["status"] = "PENDING_RECONCILIATION"
        self.assertEqual([], list(reconcile.iter_errors(pending_mock)))

        pending_paper = copy.deepcopy(pending_mock)
        pending_paper["brokerageMode"] = "INTERNAL_PAPER"
        pending_paper["orderId"] = "ord_paper_0123456789abcdef0123456789abcdef"
        self.assertNotEqual([], list(reconcile.iter_errors(pending_paper)))

    def test_openapi_has_exact_s33_routes_components_and_digest(self) -> None:
        document = self._load("contracts/openapi/openapi.json")
        catalog_path = ROOT / "contracts/catalogs/s3-3-fill-contract.v1.json"
        digest = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        self.assertEqual(
            "s3-3-fill-contract/v1",
            document["x-s3-3-contract-id"],
        )
        self.assertEqual(digest, document["x-s3-3-contract-sha256"])
        paths = document["paths"]
        expected = {
            "/api/v1/brokerage/orders/{orderId}/reconcile": {"post"},
            "/api/v1/brokerage/mock/accounts/{accountId}/fills": {"get"},
            "/api/v1/brokerage/paper/accounts/{accountId}/fills": {"get"},
        }
        actual = {
            path: set(paths[path]).intersection(
                {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
            )
            for path in expected
        }
        self.assertEqual(expected, actual)
        self.assertEqual(
            {
                "S33FillObservation",
                "S33Reconcile",
                "S33FillPage",
                "S33ReconcileSuccessResponse",
                "S33FillPageSuccessResponse",
            },
            {
                name
                for name in document["components"]["schemas"]
                if name.startswith("S33")
            },
        )
        self.assertFalse(
            any("report-fill" in path or "fill-observation" in path for path in paths)
        )

    def test_offline_roundtrip_covers_mock_and_paper_without_embedded_secrets(
        self,
    ) -> None:
        text = (
            ROOT
            / "workspaces/decision-platform/spring-api/http/"
            "s3-3-offline-roundtrip.http"
        ).read_text(encoding="utf-8")
        for fragment in (
            "/api/v1/principles",
            "/api/v1/decisions/evaluate-order",
            "/api/v1/brokerage/mock/orders",
            "app.brokerage.cli.write_fill_observations",
            "/api/v1/brokerage/orders/{{mock_order_id}}/reconcile",
            "/api/v1/brokerage/mock/accounts/{{mock_account_id}}/fills",
            "/api/v1/brokerage/mock/accounts/{{mock_account_id}}/balances",
            "/api/v1/brokerage/paper/orders",
            "/api/v1/brokerage/orders/{{paper_order_id}}/reconcile",
            "/api/v1/brokerage/paper/accounts/{{paper_account_id}}/fills",
            "/api/v1/brokerage/paper/accounts/{{paper_account_id}}/balances",
        ):
            self.assertIn(fragment, text)
        self.assertNotIn("Bearer eyJ", text)
        self.assertNotIn("password\": \"demo", text)
        self.assertNotIn("providerExecRef\":", text)

    def test_public_docs_lock_digest_routes_bounds_and_fill_writer_role(self) -> None:
        digest = hashlib.sha256(
            (
                ROOT / "contracts/catalogs/s3-3-fill-contract.v1.json"
            ).read_bytes()
        ).hexdigest()
        change = (
            ROOT
            / "contracts/changes/"
            "20260727-s3-3-fill-events-reconciliation-contract.md"
        ).read_text(encoding="utf-8")
        contracts_readme = (ROOT / "contracts/README.md").read_text(
            encoding="utf-8"
        )
        api = (ROOT / "docs/API_명세서.md").read_text(encoding="utf-8")
        root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for document in (change, contracts_readme):
            self.assertIn(digest, document)
            self.assertIn("200", document)
            self.assertIn("50", document)
            self.assertIn("31", document)
        for route in (
            "POST /api/v1/brokerage/orders/{orderId}/reconcile",
            "GET /api/v1/brokerage/mock/accounts/{accountId}/fills",
            "GET /api/v1/brokerage/paper/accounts/{accountId}/fills",
        ):
            self.assertIn(route, change)
            self.assertIn(route, api)
        self.assertIn("decision_fill_writer", root_readme)
        self.assertIn("V6/V9/V14", root_readme)

    def _validator(self, name: str) -> Draft202012Validator:
        schema = self._load(f"contracts/schemas/{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())

    @staticmethod
    def _load(relative: str) -> object:
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
