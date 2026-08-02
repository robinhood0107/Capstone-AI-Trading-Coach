from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import ContractValidationError, load_json_bytes_strict
from contracts.generate_s4_8_core6_v2_contracts import (
    CORE6_SOURCE_FAMILIES,
    FROZEN_V1_HASHES,
    INVALID_FIXTURE_PATHS,
    OUTPUTS,
    SCHEMA_PATHS,
    VALID_FIXTURE_PATHS,
    _unexpected_core6_artifact_paths,
    generate_outputs,
    validate_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(relative_path: str) -> object:
    path = ROOT / relative_path
    return load_json_bytes_strict(
        path.read_bytes(), source=path.relative_to(ROOT).as_posix()
    )


class S48Core6ContractTest(unittest.TestCase):
    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = generate_outputs()
        second = generate_outputs()

        self.assertEqual(first, second)
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s4_8_core6_v2_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S4_8_CORE6_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_generated_check_rejects_extra_core6_named_artifacts(self) -> None:
        # 실제 approval packet은 local-only runner 경계 밖에 두어야 한다. public generated
        # fixture namespace에 추가되면 check가 fail-closed해야 한다.
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            unexpected = (
                temporary_root
                / "contracts/examples/"
                "cross_market_provider_probe_approval.v1.approved.valid.json"
            )
            unexpected.parent.mkdir(parents=True)
            unexpected.write_text("{}", encoding="utf-8")

            self.assertEqual(
                [unexpected.relative_to(temporary_root).as_posix()],
                _unexpected_core6_artifact_paths(temporary_root, OUTPUTS),
            )

    def test_core_six_registry_is_contract_locked_and_never_active(self) -> None:
        registry = _load(
            "contracts/examples/market_source_entitlement.v2.valid.json"
        )
        self.assertIsInstance(registry, dict)
        self.assertEqual("market_source_entitlement.v2", registry["contractId"])
        entries = registry["entitlements"]
        self.assertEqual(set(CORE6_SOURCE_FAMILIES), {entry["sourceFamily"] for entry in entries})
        self.assertEqual(6, len(entries))
        # 공개 fixture는 실제 entitlement 증빙을 운반하지 않는다. 실제 digest는 local-private
        # registry와 승인 packet에서만 쓰므로, fixture에는 scanner-safe sentinel만 허용한다.
        self.assertEqual(
            [f"{index:064x}" for index in range(len(CORE6_SOURCE_FAMILIES))],
            [entry["accessEvidenceDigest"] for entry in entries],
        )
        self.assertEqual(
            "BLOCKED_NO_CREDENTIAL_OR_APPROVAL",
            next(entry for entry in entries if entry["sourceFamily"] == "KOFIA")[
                "activationBlocker"
            ],
        )
        self.assertTrue(
            all(
                entry["activationStatus"] in {"CANDIDATE_DISABLED", "BLOCKED"}
                for entry in entries
            )
        )
        self.assertTrue(all(not entry["providerCallsAllowed"] for entry in entries))
        self.assertTrue(all(not entry["machineFetchAllowed"] for entry in entries))
        self.assertTrue(all(not entry["accountCallsAllowed"] for entry in entries))
        self.assertTrue(all(not entry["orderCallsAllowed"] for entry in entries))
        self.assertTrue(all(not entry["rawStoreAllowed"] for entry in entries))
        self.assertTrue(all(not entry["embeddingAllowed"] for entry in entries))
        self.assertTrue(all(not entry["externalLlmAllowed"] for entry in entries))
        self.assertTrue(all(entry["decisionAuthority"] == "NONE" for entry in entries))
        self.assertTrue(
            all(entry["riskSignalOrderAuthority"] == "NONE" for entry in entries)
        )
        self.assertTrue(all(entry["riskEngineAuthority"] == "NONE" for entry in entries))
        self.assertTrue(all(entry["signalAuthority"] == "NONE" for entry in entries))
        self.assertTrue(all(entry["orderAuthority"] == "NONE" for entry in entries))
        self.assertEqual(
            {"KIS": 18, "SEC_EDGAR": 2, "KRX": 2, "KOFIA": 1},
            {
                entry["sourceFamily"]: entry["endpointSetCount"]
                for entry in entries
                if entry["sourceFamily"] in {"KIS", "SEC_EDGAR", "KRX", "KOFIA"}
            },
        )

    def test_packet_and_receipt_contracts_never_expose_provider_payloads(self) -> None:
        validators = {
            schema_id: Draft202012Validator(_load(path.relative_to(ROOT).as_posix()))
            for schema_id, path in SCHEMA_PATHS.items()
        }
        for relative_path in sorted(VALID_FIXTURE_PATHS):
            payload = _load(relative_path)
            self.assertIsInstance(payload, dict)
            schema_id = payload["contractId"]
            self.assertEqual([], list(validators[schema_id].iter_errors(payload)))
            validate_semantics(schema_id, payload)

        packet = _load(
            "contracts/examples/cross_market_provider_probe_approval.v1.template.valid.json"
        )
        self.assertIsInstance(packet, dict)
        self.assertEqual("TEMPLATE", packet["approvalStatus"])
        self.assertFalse(packet["executionAllowed"])
        self.assertTrue(packet["fixtureOnly"])
        self.assertEqual(0, packet["caps"]["retryCap"])
        self.assertEqual(0, packet["caps"]["artifactCap"])

        receipt = _load(
            "contracts/examples/cross_market_provider_probe_receipt.v1.not-executed.valid.json"
        )
        self.assertIsInstance(receipt, dict)
        self.assertEqual("NOT_EXECUTED", receipt["outcome"])
        self.assertEqual(0, receipt["physicalCalls"])
        self.assertFalse(receipt["rawBodyStored"])
        self.assertFalse(receipt["rawHeaderStored"])
        self.assertFalse(receipt["rawQueryStored"])
        self.assertFalse(receipt["sensitiveMaterialStored"])

        serialized = json.dumps([packet, receipt], ensure_ascii=False)
        for forbidden in (
            "authorization",
            "providerBody",
            "rawResponse",
            "requestQuery",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_required_negative_fixtures_fail_closed(self) -> None:
        self.assertEqual(
            {
                "unknown-source",
                "direct-projection-fanout",
                "active-without-rights",
                "raw-storage",
                "endpoint-count",
                "approval-retry",
                "approval-expiry",
                "approval-request-query",
                "approval-consumed-executable",
                "approval-expired-executable",
                "approval-kofia-executable",
                "approval-endpoint-identity",
                "receipt-over-cap",
                "receipt-raw-storage",
                "receipt-provider-body",
                "receipt-success-zero-calls",
                "receipt-failed-zero-calls",
                "receipt-projection-provider-call",
                "authority-escalation",
            },
            {Path(path).name.split(".")[-3] for path in INVALID_FIXTURE_PATHS},
        )
        validators = {
            schema_id: Draft202012Validator(_load(path.relative_to(ROOT).as_posix()))
            for schema_id, path in SCHEMA_PATHS.items()
        }
        for relative_path in sorted(INVALID_FIXTURE_PATHS):
            payload = _load(relative_path)
            self.assertIsInstance(payload, dict)
            schema_id = payload["contractId"]
            errors = list(validators[schema_id].iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not errors:
                try:
                    validate_semantics(schema_id, payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            self.assertTrue(errors or semantic_error, relative_path)

    def test_failed_receipt_cannot_hide_a_physical_provider_call(self) -> None:
        relative_path = (
            "contracts/examples/invalid/"
            "cross_market_provider_probe_receipt.v1.receipt-failed-zero-calls.invalid.json"
        )
        payload = _load(relative_path)
        self.assertIsInstance(payload, dict)
        schema = _load(
            "contracts/schemas/cross_market_provider_probe_receipt.v1.schema.json"
        )
        self.assertIsInstance(schema, dict)

        self.assertNotEqual([], list(Draft202012Validator(schema).iter_errors(payload)))
        with self.assertRaisesRegex(ContractValidationError, "failed receipt"):
            validate_semantics(payload["contractId"], payload)

    def test_v1_contracts_and_workspace_boundaries_are_byte_stable(self) -> None:
        for relative_path, expected_hash in FROZEN_V1_HASHES.items():
            self.assertEqual(
                expected_hash,
                hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest(),
                relative_path,
            )

        for workspace in ("return-engine", "experience-dashboard"):
            files = sorted(
                path.relative_to(ROOT / "workspaces" / workspace).as_posix()
                for path in (ROOT / "workspaces" / workspace).rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            self.assertEqual(["README.md"], files)

    def test_contract_change_keeps_gdelt_as_decision_platform_offline_only(self) -> None:
        change = (
            ROOT / "contracts/changes/20260802-s4-8-core6-v2-contract-lock.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Decision Platform", change)
        self.assertIn("GDELT_EXISTING_OFFLINE_PRODUCER_UNCHANGED=1", change)
        self.assertIn("GDELT_EXECUTOR_ADDED=0", change)
        self.assertIn("GDELT_OUTBOUND_IMPLEMENTATION=0", change)
        self.assertIn("Return/Experience workspace", change)
        self.assertNotIn("팀원 B", change)
        self.assertNotIn("team member B", change)

    def test_active_status_ledger_keeps_gdelt_decision_owned_and_offline_only(self) -> None:
        ledger = (ROOT / "docs/README.md").read_text(encoding="utf-8")

        self.assertIn("| S1.3G | `OFFLINE_ONLY` |", ledger)
        self.assertIn(
            "Decision Platform existing GDELT offline aggregate producer unchanged", ledger
        )
        self.assertIn("HTTP transport/executor/outbound 0", ledger)
        self.assertNotIn("| S1.3G | `EXTERNAL_OWNER_HANDOFF` |", ledger)
        self.assertNotIn("GDELT producer는 팀원 B", ledger)


if __name__ == "__main__":
    unittest.main()
