from __future__ import annotations

import hashlib
import json
import unittest

from jsonschema import Draft202012Validator

from contracts.generate_s6_retirement_contracts import (
    CATALOG_DIR,
    EXAMPLE_DIR,
    HISTORICAL_HASHES,
    HISTORICAL_ONLY_CATALOGS,
    HISTORICAL_ONLY_CONTRACTS,
    INVALID_DIR,
    ROOT,
    SCHEMA_DIR,
    ContractValidationError,
    build_outputs,
    validate_semantics,
)


def _load(path):
    return json.loads(path.read_text())


class S6RetirementContractTest(unittest.TestCase):
    def test_contracts_ci_checks_both_s6_generators(self) -> None:
        workflow = (ROOT / ".github/workflows/contracts-ci.yml").read_text()
        self.assertIn("contracts/generate_s6_contracts.py --check", workflow)
        self.assertIn("contracts/generate_s6_retirement_contracts.py --check", workflow)

    def test_generator_is_deterministic_and_checked_in(self) -> None:
        outputs = build_outputs()
        self.assertEqual(4, len(outputs))
        for path, expected in outputs.items():
            self.assertTrue(path.is_file(), path)
            self.assertEqual(expected, path.read_bytes(), path)

    def test_positive_and_negative_fixtures_are_closed(self) -> None:
        schema = _load(SCHEMA_DIR / "s6-capability-disposition.v1.schema.json")
        valid = _load(EXAMPLE_DIR / "s6-capability-disposition.v1.valid.json")
        invalid = _load(INVALID_DIR / "s6-capability-disposition.v1.contract.invalid.json")
        validator = Draft202012Validator(schema)
        self.assertEqual([], list(validator.iter_errors(valid)))
        validate_semantics("s6-capability-disposition.v1", valid)
        self.assertTrue(list(validator.iter_errors(invalid)))
        with self.assertRaises(ContractValidationError):
            validate_semantics("s6-capability-disposition.v1", invalid)

    def test_v2_lock_retires_only_s6_6_and_s6_7(self) -> None:
        lock = _load(CATALOG_DIR / "s6-contract-lock.v2.json")
        self.assertEqual(["S6.1", "S6.2", "S6.3", "S6.4", "S6.5"], lock["activeSessions"])
        self.assertEqual(["S6.6", "S6.7"], lock["retiredSessions"])
        self.assertEqual("s2-2-system-rule-catalog.v1", lock["runtimeCatalog"])
        self.assertEqual(list(HISTORICAL_ONLY_CATALOGS), lock["historicalOnlyCatalogs"])
        self.assertEqual(list(HISTORICAL_ONLY_CONTRACTS), lock["historicalOnlyContracts"])
        self.assertEqual("NONE", lock["crossMarketRuntimeAuthority"])
        self.assertFalse(lock["providerRuntimeCallsAllowed"])

    def test_historical_contract_bytes_remain_frozen(self) -> None:
        for relative, expected in HISTORICAL_HASHES.items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(expected, actual, relative)


if __name__ == "__main__":
    unittest.main()
