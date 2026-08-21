from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s5_7a_market_data_contracts import (
    SCHEMA_IDS,
    SCHEMA_PATHS,
    artifacts,
    validate_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> object:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class S57aMarketDataContractTest(unittest.TestCase):
    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = artifacts()
        self.assertEqual(first, artifacts())
        self.assertTrue(all(content.endswith(b"\n") for content in first.values()))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s5_7a_market_data_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S5_7A_MARKET_DATA_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_exact_three_closed_contracts_accept_positive_fixtures(self) -> None:
        self.assertEqual(
            {
                "market-data-seed.v1",
                "market-data-daily-shard.v1",
                "market-data-health.v1",
            },
            set(SCHEMA_IDS),
        )
        for schema_id in SCHEMA_IDS:
            schema = _load(SCHEMA_PATHS[schema_id])
            payload = _load(f"contracts/examples/{schema_id}.valid.json")
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            self.assertEqual([], list(validator.iter_errors(payload)))
            validate_semantics(schema_id, payload)

    def test_all_negative_fixtures_fail_schema_or_semantics(self) -> None:
        fixtures = sorted(
            (ROOT / "contracts/examples/invalid").glob("market-data-*.invalid.json")
        )
        self.assertEqual(8, len(fixtures))
        validators = {
            schema_id: Draft202012Validator(_load(path), format_checker=FormatChecker())
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

    def test_daily_shard_is_one_complete_monthly_fixed_exact31_set(self) -> None:
        payload = _load("contracts/examples/market-data-daily-shard.v1.valid.json")
        self.assertIsInstance(payload, dict)
        self.assertEqual(31, len(payload["membership"]))
        self.assertEqual(
            set(payload["membership"]), {bar["symbol"] for bar in payload["bars"]}
        )
        self.assertEqual(
            {payload["sessionDate"]}, {bar["sessionDate"] for bar in payload["bars"]}
        )
        self.assertEqual(
            {"KOSPI", "KOSDAQ"}, {row["indexId"] for row in payload["indices"]}
        )
        self.assertFalse(payload["forwardFillUsed"])
        self.assertEqual(0, payload["providerCallsOnRead"])

    def test_catalog_locks_reader_retention_and_authority_bounds(self) -> None:
        catalog = _load("contracts/catalogs/s5-7a-market-data-lock.v1.json")
        self.assertIsInstance(catalog, dict)
        self.assertEqual(253, catalog["operationalReader"]["maxCloseSessions"])
        self.assertEqual(1260, catalog["researchReader"]["maxXkrxSessions"])
        self.assertFalse(catalog["researchReader"]["springDecisionAccess"])
        self.assertFalse(catalog["researchReader"]["springRiskAccess"])
        self.assertEqual(365, catalog["retention"]["ecosActiveDaysMax"])
        self.assertEqual(
            ["symbol", "sessionDate", "generation"],
            catalog["normalizedRows"]["bars"]["key"],
        )
        self.assertFalse(
            catalog["normalizedRows"]["universe"]["historicalUnionIsDailyScope"]
        )
        self.assertFalse(catalog["runtimeImplemented"])
        self.assertFalse(catalog["providerAuthorityGranted"])
        self.assertFalse(catalog["publicMarketDataApi"])
        self.assertEqual("ABSTAIN", catalog["lightgbmSignalAuthority"])
        self.assertEqual(38, catalog["dailyShard"]["normalReplayOperationCount"])
        self.assertEqual(41, catalog["dailyShard"]["monthBoundaryReplayOperationCount"])
        self.assertEqual(0, catalog["dailyShard"]["providerPhysicalCallMax"])


if __name__ == "__main__":
    unittest.main()
