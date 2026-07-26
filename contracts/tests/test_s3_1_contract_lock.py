from __future__ import annotations

import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import ContractValidationError, load_json_bytes_strict
from contracts.validate import validate_s3_1_mock_order_request_semantics


REPO_ROOT = Path(__file__).resolve().parents[2]


class S31BrokerageMockContractTest(unittest.TestCase):
    def test_mock_order_request_rejects_raw_account_fields_and_amount_drift(self) -> None:
        schema = load_json_bytes_strict(
            (REPO_ROOT / "contracts/schemas/s3-1-mock-order-request.schema.json").read_bytes(),
            source="contracts/schemas/s3-1-mock-order-request.schema.json",
        )
        validator = Draft202012Validator(schema)
        valid = load_json_bytes_strict(
            (REPO_ROOT / "contracts/examples/s3-1-mock-order-request.valid.json").read_bytes(),
            source="contracts/examples/s3-1-mock-order-request.valid.json",
        )
        account_forgery = load_json_bytes_strict(
            (
                REPO_ROOT
                / "contracts/examples/invalid/s3-1-mock-order-request.account.invalid.json"
            ).read_bytes(),
            source="contracts/examples/invalid/s3-1-mock-order-request.account.invalid.json",
        )
        amount_drift = load_json_bytes_strict(
            (
                REPO_ROOT
                / "contracts/examples/invalid/s3-1-mock-order-request.amount.invalid.json"
            ).read_bytes(),
            source="contracts/examples/invalid/s3-1-mock-order-request.amount.invalid.json",
        )

        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_s3_1_mock_order_request_semantics(valid)
        self.assertNotEqual([], list(validator.iter_errors(account_forgery)))
        with self.assertRaises(ContractValidationError):
            validate_s3_1_mock_order_request_semantics(amount_drift)

    def test_mock_balance_and_buyable_examples_are_bounded_owner_scoped_shapes(self) -> None:
        for name in ("s3-1-mock-balance", "s3-1-mock-buyable"):
            schema = load_json_bytes_strict(
                (REPO_ROOT / f"contracts/schemas/{name}.schema.json").read_bytes(),
                source=f"contracts/schemas/{name}.schema.json",
            )
            example = load_json_bytes_strict(
                (REPO_ROOT / f"contracts/examples/{name}.valid.json").read_bytes(),
                source=f"contracts/examples/{name}.valid.json",
            )
            self.assertEqual([], list(Draft202012Validator(schema).iter_errors(example)))
            self.assertTrue(example["accountId"].startswith("acct_"))
            self.assertNotIn("provider", str(example).lower())
            self.assertNotIn("token", str(example).lower())

    def test_v11_locks_order_ledger_one_use_and_sanitized_projection(self) -> None:
        migration = (
            REPO_ROOT
            / "workspaces/decision-platform/spring-api/src/main/resources/db/migration/"
            / "V11__s3_1_brokerage_mock_orders.sql"
        )
        sql = migration.read_text(encoding="utf-8")
        for required in (
            "S3.1 V11 precondition failed",
            "orders_decision_id_unique",
            "orders_idempotency_scope_unique",
            "ALTER TABLE orders FORCE ROW LEVEL SECURITY",
            "CREATE FUNCTION read_mock_order_decision",
            "CREATE FUNCTION find_mock_order_idempotency_result",
            "CREATE VIEW mock_order_owner_projection",
            "portfolio_owner_scope_hash",
            "MOCK_ORDER_CANCEL_REQUESTED",
            "WHEN 'MOCK_ORDER_CANCEL_REQUESTED' THEN 20",
            "order_events_definer_select_policy",
        ):
            self.assertIn(required, sql)
        for forbidden in ("TTTC0012U", "TTTC0011U", "KIS_LIVE", "provider_payload", "raw_account"):
            self.assertNotIn(forbidden, sql)

    def test_openapi_lists_s3_1_submit_cancel_balance_and_buyable_paths(self) -> None:
        openapi = load_json_bytes_strict(
            (REPO_ROOT / "contracts/openapi/openapi.json").read_bytes(),
            source="contracts/openapi/openapi.json",
        )
        paths = openapi["paths"]
        self.assertIn("/api/v1/brokerage/mock/orders", paths)
        self.assertIn("/api/v1/brokerage/orders/{orderId}", paths)
        self.assertIn("post", paths["/api/v1/brokerage/orders/{orderId}/cancel"])
        self.assertIn("get", paths["/api/v1/brokerage/mock/accounts/{accountId}/balances"])
        self.assertIn("get", paths["/api/v1/brokerage/mock/accounts/{accountId}/buyable"])

    def test_brokerage_proto_is_mock_only_and_codegen_is_ci_locked(self) -> None:
        proto = (REPO_ROOT / "contracts/proto/brokerage.proto").read_text(encoding="utf-8")
        workflow = (REPO_ROOT / ".github/workflows/python-ci.yml").read_text(encoding="utf-8")
        for required in (
            "service BrokerageService",
            "rpc SubmitMockCashOrder(",
            "rpc CancelMockCashOrder(",
            "rpc GetMockBalance(",
            "rpc GetMockBuyable(",
            "provider_order_ref_hash",
        ):
            self.assertIn(required, proto)
        for forbidden in ("appkey", "secret", "token", "tttc0011u", "tttc0012u", "account_number"):
            self.assertNotIn(forbidden, proto.lower())
        self.assertIn("generate_brokerage_proto.py --check", workflow)


if __name__ == "__main__":
    unittest.main()
