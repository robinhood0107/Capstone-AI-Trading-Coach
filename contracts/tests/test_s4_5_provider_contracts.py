from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import load_json_bytes_strict
from contracts.generate_s4_5_provider_contracts import (
    GEMINI_SCHEMA_PATH,
    OUTPUTS,
    VOYAGE_SCHEMA_PATH,
    generate_outputs,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> object:
    return load_json_bytes_strict(
        path.read_bytes(), source=path.relative_to(ROOT).as_posix()
    )


class S45ProviderContractTest(unittest.TestCase):
    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = generate_outputs()
        self.assertEqual(first, generate_outputs())
        self.assertEqual(OUTPUTS, frozenset(first))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s4_5_provider_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S4_5_PROVIDER_CONTROL_CONTRACTS_VERIFIED", completed.stdout)

    def test_positive_packets_are_closed_and_zero_retry(self) -> None:
        validators = {
            "voyage": Draft202012Validator(_load(VOYAGE_SCHEMA_PATH)),
            "gemini": Draft202012Validator(_load(GEMINI_SCHEMA_PATH)),
        }
        fixtures = {
            "voyage": _load(
                ROOT / "contracts/examples/s4-2c-voyage-approval.valid.json"
            ),
            "gemini": _load(
                ROOT / "contracts/examples/s4-4g-gemini-approval.valid.json"
            ),
        }
        for provider, payload in fixtures.items():
            self.assertEqual([], list(validators[provider].iter_errors(payload)))
            self.assertEqual(0, payload["retryCount"])
            self.assertEqual("APPROVED", payload["state"])
        self.assertEqual(0, fixtures["voyage"]["paidHardCapUsd"])
        self.assertFalse(fixtures["gemini"]["store"])
        self.assertEqual(60, fixtures["gemini"]["logicalCallCap"])
        self.assertEqual(60, fixtures["gemini"]["physicalCallCap"])

    def test_negative_packets_fail_schema_before_any_executor(self) -> None:
        cases = (
            (
                Draft202012Validator(_load(VOYAGE_SCHEMA_PATH)),
                ROOT
                / "contracts/examples/invalid/s4-2c-voyage-approval.paid.invalid.json",
            ),
            (
                Draft202012Validator(_load(GEMINI_SCHEMA_PATH)),
                ROOT
                / "contracts/examples/invalid/s4-4g-gemini-approval.store.invalid.json",
            ),
        )
        for validator, path in cases:
            self.assertTrue(list(validator.iter_errors(_load(path))), path.name)

    def test_schemas_forbid_unknown_fields_and_cross_purpose_reuse(self) -> None:
        voyage = _load(VOYAGE_SCHEMA_PATH)
        gemini = _load(GEMINI_SCHEMA_PATH)
        self.assertFalse(voyage["additionalProperties"])
        self.assertFalse(gemini["additionalProperties"])
        self.assertEqual(
            ["EVALUATION_ONLY", "SLA_FALLBACK_CANDIDATE"],
            voyage["properties"]["purpose"]["enum"],
        )
        self.assertEqual(
            ["PREFLIGHT", "EVALUATION", "PRODUCTION_ACTIVATION"],
            gemini["properties"]["purpose"]["enum"],
        )


if __name__ == "__main__":
    unittest.main()
