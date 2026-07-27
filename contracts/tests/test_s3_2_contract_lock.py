from __future__ import annotations

import copy
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import (
    ContractValidationError,
    load_json_bytes_strict,
)
from contracts.generate_s3_2_contracts import generate, load_catalog
from contracts.validate import validate_s3_2_paper_order_request_semantics


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(relative_path: str) -> object:
    path = REPO_ROOT / relative_path
    return load_json_bytes_strict(path.read_bytes(), source=relative_path)


class S32InternalPaperContractTest(unittest.TestCase):
    def test_catalog_locks_mode_routes_pricing_and_evidence(self) -> None:
        catalog = load_catalog()
        self.assertEqual("s3-2-internal-paper-contract/v1", catalog["contractId"])
        self.assertEqual("INTERNAL_PAPER", catalog["brokerageMode"])
        self.assertEqual(5, catalog["slippageBpsDefault"])
        self.assertEqual(["LAST_QUOTE", "PREVIOUS_CLOSE"], catalog["priceBasis"])
        self.assertEqual(
            {
                "GET /api/v1/brokerage/orders/{orderId}",
                "GET /api/v1/brokerage/paper/accounts/{accountId}/balances",
                "GET /api/v1/brokerage/paper/accounts/{accountId}/buyable",
                "POST /api/v1/brokerage/orders/{orderId}/cancel",
                "POST /api/v1/brokerage/paper/orders",
            },
            set(catalog["routes"].values()),
        )
        self.assertEqual(
            {
                "PAPER_ORDER_ACCEPTED",
                "PAPER_ORDER_CANCELLED",
                "PAPER_ORDER_FILLED",
            },
            set(catalog["evidence"]["auditActions"]),
        )
        self.assertIn("feeModel", catalog["evidence"]["filledExactKeys"])
        self.assertIn("slippageBps", catalog["evidence"]["filledExactKeys"])

    def test_generated_contract_artifacts_have_no_drift(self) -> None:
        self.assertEqual(0, generate(check=True))

    def test_request_rejects_client_mode_account_and_amount_drift(self) -> None:
        schema = _load("contracts/schemas/s3-2-paper-order-request.schema.json")
        validator = Draft202012Validator(schema)
        valid = _load("contracts/examples/s3-2-paper-order-request.valid.json")
        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_s3_2_paper_order_request_semantics(valid)

        for suffix in ("account", "mode", "price", "symbol"):
            invalid = _load(
                f"contracts/examples/invalid/s3-2-paper-order-request.{suffix}.invalid.json"
            )
            self.assertNotEqual([], list(validator.iter_errors(invalid)))

        amount_drift = copy.deepcopy(valid)
        amount_drift["orderIntent"]["estimatedAmount"] += 1
        with self.assertRaises(ContractValidationError):
            validate_s3_2_paper_order_request_semantics(amount_drift)

    def test_order_response_status_and_fill_are_mutually_consistent(self) -> None:
        schema = _load("contracts/schemas/s3-2-paper-order-response.schema.json")
        validator = Draft202012Validator(schema)
        filled = _load("contracts/examples/s3-2-paper-order-response.filled.valid.json")
        accepted = _load(
            "contracts/examples/s3-2-paper-order-response.accepted.valid.json"
        )
        self.assertEqual([], list(validator.iter_errors(filled)))
        self.assertEqual([], list(validator.iter_errors(accepted)))

        invalid_filled = copy.deepcopy(filled)
        invalid_filled["fill"] = None
        self.assertNotEqual([], list(validator.iter_errors(invalid_filled)))
        invalid_accepted = copy.deepcopy(accepted)
        invalid_accepted["fill"] = filled["fill"]
        self.assertNotEqual([], list(validator.iter_errors(invalid_accepted)))

    def test_shared_order_detail_enforces_mode_and_id_prefix_pair(self) -> None:
        schema = _load("contracts/schemas/s3-2-order-detail.schema.json")
        validator = Draft202012Validator(schema)
        paper = _load("contracts/examples/s3-2-order-detail.valid.json")
        self.assertEqual([], list(validator.iter_errors(paper)))

        mixed = copy.deepcopy(paper)
        mixed["orderId"] = "ord_mock_0123456789abcdef0123456789abcdef"
        self.assertNotEqual([], list(validator.iter_errors(mixed)))

    def test_balance_and_buyable_examples_are_paper_only_and_bounded(self) -> None:
        for name in ("s3-2-paper-balance", "s3-2-paper-buyable"):
            schema = _load(f"contracts/schemas/{name}.schema.json")
            example = _load(f"contracts/examples/{name}.valid.json")
            self.assertEqual(
                [], list(Draft202012Validator(schema).iter_errors(example))
            )
            self.assertEqual("INTERNAL_PAPER", example["brokerageMode"])
            self.assertTrue(example["accountId"].startswith("acct_"))
            self.assertNotIn("provider", str(example).lower())
            self.assertNotIn("token", str(example).lower())


if __name__ == "__main__":
    unittest.main()
