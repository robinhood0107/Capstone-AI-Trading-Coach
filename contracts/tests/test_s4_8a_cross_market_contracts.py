from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import (
    ContractValidationError,
    load_json_bytes_strict,
)
from contracts.generate_s4_8a_cross_market_contracts import (
    FROZEN_EXISTING_PAYLOAD_HASHES,
    INVALID_FIXTURE_PATHS,
    KIS_ENDPOINT_IDENTITY_HASHES,
    OUTPUTS,
    SCHEMA_PATHS,
    VALID_FIXTURE_PATHS,
    generate_outputs,
    validate_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(relative_path: str) -> object:
    path = ROOT / relative_path
    return load_json_bytes_strict(
        path.read_bytes(), source=path.relative_to(ROOT).as_posix()
    )


class S48aCrossMarketContractTest(unittest.TestCase):
    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = generate_outputs()
        second = generate_outputs()

        self.assertEqual(first, second)
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s4_8a_cross_market_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S4_8A_CROSS_MARKET_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_exact_seven_ssot_schemas_and_positive_fixtures(self) -> None:
        self.assertEqual(
            {
                "market_source_entitlement.v1",
                "cross_market_exposure_catalog.v1",
                "cross_market_observation.v1",
                "analyst_revision_evidence.v1",
                "market_cause_evidence.v1",
                "cross_market_risk_snapshot.v1",
                "cross_market_policy_evaluation.v1",
            },
            set(SCHEMA_PATHS),
        )

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

    def test_required_negative_fixtures_fail_closed(self) -> None:
        required_cases = {
            "derived-right-missing",
            "embedding-right-missing",
            "expired-entitlement",
            "external-llm-right-missing",
            "fake-zero",
            "future-available-at",
            "gdelt-article-metadata",
            "gdelt-reported-as-cause",
            "incomplete-available",
            "raw-right-missing",
            "risk-authority-escalation",
            "unknown-endpoint",
        }
        self.assertEqual(
            required_cases,
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

    def test_gdelt_causal_authority_restriction_rejects_each_upgrade(self) -> None:
        schema = _load("contracts/schemas/market_cause_evidence.v1.schema.json")
        self.assertIsInstance(schema, dict)
        validator = Draft202012Validator(schema)
        valid = _load("contracts/examples/market_cause_evidence.v1.valid.json")
        self.assertIsInstance(valid, dict)
        self.assertEqual("GDELT_AGGREGATE", valid["sourceFamily"])
        self.assertEqual("CO_MOVES_WITH", valid["relation"])
        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_semantics("market_cause_evidence.v1", valid)

        for field, forbidden_value in (
            ("classification", "CONFIRMED_FACT"),
            ("relation", "REPORTED_AS_CAUSE"),
        ):
            candidate = dict(valid)
            candidate[field] = forbidden_value
            self.assertTrue(
                list(validator.iter_errors(candidate)),
                f"GDELT aggregate {field}={forbidden_value} must fail the schema",
            )
            with self.assertRaisesRegex(
                ContractValidationError,
                "GDELT aggregate cannot assert a confirmed fact or reported cause",
            ):
                validate_semantics("market_cause_evidence.v1", candidate)

    def test_kis_exact_18_are_opaque_disabled_and_gdelt_is_separate(self) -> None:
        registry = _load("contracts/examples/market_source_entitlement.v1.valid.json")
        self.assertIsInstance(registry, dict)
        entries = registry["entitlements"]
        kis_entries = [entry for entry in entries if entry["sourceFamily"] == "KIS"]
        gdelt_entries = [
            entry for entry in entries if entry["sourceFamily"] == "GDELT_AGGREGATE"
        ]

        self.assertEqual(18, len(kis_entries))
        self.assertEqual(1, len(gdelt_entries))
        self.assertEqual(
            set(KIS_ENDPOINT_IDENTITY_HASHES),
            {entry["endpointIdentityHash"] for entry in kis_entries},
        )
        self.assertEqual(
            {"ANALYST": 3, "OVERSEAS_LEAD": 4, "DOMESTIC_AMPLIFICATION": 11},
            {
                category: sum(entry["category"] == category for entry in kis_entries)
                for category in {entry["category"] for entry in kis_entries}
            },
        )
        self.assertTrue(
            all(entry["activationStatus"] == "CANDIDATE_DISABLED" for entry in entries)
        )
        self.assertTrue(all(not entry["providerCallsAllowed"] for entry in entries))
        self.assertEqual("NONE", gdelt_entries[0]["decisionAuthority"])

        serialized = json.dumps(registry, ensure_ascii=False)
        for forbidden in (
            "invest-opinion",
            "inquire-price",
            "daily-short-sale",
            "articleUrl",
            "articleText",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_queryless_get_and_v3_compatibility_are_exact(self) -> None:
        get_contract = _load("contracts/catalogs/s4-8a-cross-market-get.v1.json")
        self.assertEqual("GET", get_contract["method"])
        self.assertEqual("/api/v1/risk/cross-market", get_contract["path"])
        self.assertEqual([], get_contract["queryParameters"])
        self.assertEqual("LATEST_OWNER_ONLY", get_contract["projection"])
        self.assertFalse(get_contract["providerFanoutAllowed"])

        v1 = _load("contracts/catalogs/s2-2-system-rule-catalog.v1.json")
        v2 = _load("contracts/catalogs/s2-2-system-rule-catalog.v2.json")
        self.assertEqual(v1["rules"], v2["rules"][:14])
        self.assertEqual("cross_market_new_buy_guard", v2["rules"][14]["ruleId"])
        self.assertEqual(2, v2["catalogVersion"])
        self.assertEqual("HASH-CANONICALIZATION-S22-V3", v2["canonicalization"]["id"])

        vector = _load("contracts/examples/s2-2-hash-vector.v3.valid.json")
        self.assertEqual("s2.2-metric-snapshot-v3", vector["metricSnapshotVersion"])
        self.assertEqual("HASH-CANONICALIZATION-S22-V3", vector["canonicalizationId"])
        canonical = json.dumps(
            vector["semanticInput"],
            default=float,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(), vector["semanticInputHash"]
        )
        self.assertEqual(
            [
                "analystEvidence",
                "artifactHash",
                "causeEvidence",
                "evidenceMode",
                "newsEvidence",
                "performanceClaimAllowed",
                "ragOutput",
                "snapshotAsOf",
                "snapshotId",
            ],
            vector["excludedFields"],
        )

    def test_existing_payload_bytes_are_frozen_and_post_core_intake_is_untracked(self) -> None:
        for relative_path, expected_hash in FROZEN_EXISTING_PAYLOAD_HASHES.items():
            actual_hash = hashlib.sha256(
                (ROOT / relative_path).read_bytes()
            ).hexdigest()
            self.assertEqual(expected_hash, actual_hash, relative_path)

        tracked = subprocess.run(
            ["git", "ls-files", "--", "workspaces/return-engine", "workspaces/experience-dashboard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertFalse([path for path in tracked if "/dev/" in path])
        for workspace in ("return-engine", "experience-dashboard"):
            self.assertTrue((ROOT / "workspaces" / workspace / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
