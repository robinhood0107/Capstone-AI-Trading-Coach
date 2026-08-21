from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_p1_verification_contracts import (
    LIVE_OPERATIONS,
    SCHEMA_IDS,
    SCHEMA_PATHS,
    artifacts,
    validate_semantics,
)
from contracts.generate_principle_contracts import ContractValidationError


ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> object:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class P1VerificationContractTest(unittest.TestCase):
    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        self.assertEqual(artifacts(), artifacts())
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_p1_verification_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("P1_VERIFICATION_CONTRACTS_VERIFIED", completed.stdout)

    def test_exact_closed_contracts_accept_positive_fixtures(self) -> None:
        self.assertEqual(
            {"p1-verification-packet.v1", "p1-verification-report.v1"}, set(SCHEMA_IDS)
        )
        for schema_id in SCHEMA_IDS:
            schema = _load(SCHEMA_PATHS[schema_id])
            payload = _load(f"contracts/examples/{schema_id}.valid.json")
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            self.assertEqual([], list(validator.iter_errors(payload)))
            validate_semantics(schema_id, payload)

    def test_negative_fixtures_fail_schema_or_semantics(self) -> None:
        fixtures = sorted(
            (ROOT / "contracts/examples/invalid").glob("p1-verification-*.invalid.json")
        )
        self.assertEqual(4, len(fixtures))
        validators = {
            schema_id: Draft202012Validator(
                _load(path), format_checker=FormatChecker()
            )
            for schema_id, path in SCHEMA_PATHS.items()
        }
        for path in fixtures:
            payload = json.loads(path.read_text(encoding="utf-8"))
            schema_id = payload["contractId"]
            errors = list(validators[schema_id].iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not errors:
                try:
                    validate_semantics(schema_id, payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            self.assertTrue(errors or semantic_error, path.name)

    def test_catalog_separates_replay_provider_and_future_profiles(self) -> None:
        catalog = _load("contracts/catalogs/p1-verification-catalog.v1.json")
        self.assertIsInstance(catalog, dict)
        self.assertEqual(38, catalog["offlineReplay"]["normalOperationCount"])
        self.assertEqual(41, catalog["offlineReplay"]["monthBoundaryOperationCount"])
        self.assertEqual(0, catalog["offlineReplay"]["providerPhysicalCalls"])
        self.assertEqual(list(LIVE_OPERATIONS), catalog["providerReadSmoke"]["operations"])
        self.assertEqual(6, catalog["providerReadSmoke"]["dataPhysicalCallCap"])
        self.assertEqual(0, catalog["providerReadSmoke"]["liveOrderCallCap"])
        states = {
            row["profile"]: row["currentImplementationState"]
            for row in catalog["profiles"]
        }
        self.assertEqual("IMPLEMENTED", states["S0_S5_CURRENT"])
        self.assertEqual("IMPLEMENTED", states["PROVIDER_READ_SMOKE"])
        self.assertEqual("NOT_IMPLEMENTED", states["S6_OFFLINE"])
        self.assertEqual("EXTERNAL_PLACEHOLDER", states["P1_FULL"])


if __name__ == "__main__":
    unittest.main()
