from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.generate_principle_contracts import (
    ContractValidationError,
    load_json_bytes_strict,
)
from contracts.generate_s1_3g_news_contracts import (
    GDELT_SCHEMA_PATH,
    INVALID_FIXTURE_PATHS,
    NEWS_SUMMARY_SCHEMA_PATH,
    OUTPUTS,
    VALID_FIXTURE_PATHS,
    generate_outputs,
    validate_gdelt_observation_semantics,
    validate_news_summary_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> object:
    return load_json_bytes_strict(
        path.read_bytes(), source=path.relative_to(ROOT).as_posix()
    )


class S13gNewsContractTest(unittest.TestCase):
    def test_generator_is_deterministic_complete_and_checked_in(self) -> None:
        first = generate_outputs()
        second = generate_outputs()

        self.assertEqual(first, second)
        self.assertEqual(OUTPUTS, frozenset(first))
        self.assertTrue(all(payload.endswith(b"\n") for payload in first.values()))

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s1_3g_news_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("S1_3G_NEWS_CONTRACT_LOCK_VERIFIED", completed.stdout)

    def test_positive_fixtures_lock_aggregate_only_authority(self) -> None:
        gdelt_schema = _load(GDELT_SCHEMA_PATH)
        summary_schema = _load(NEWS_SUMMARY_SCHEMA_PATH)
        self.assertIsInstance(gdelt_schema, dict)
        self.assertIsInstance(summary_schema, dict)
        gdelt_validator = Draft202012Validator(gdelt_schema)
        summary_validator = Draft202012Validator(summary_schema)

        gdelt_available = _load(
            ROOT
            / "contracts/examples/gdelt_news_tone_observation.v1.available.valid.json"
        )
        gdelt_abstain = _load(
            ROOT
            / "contracts/examples/gdelt_news_tone_observation.v1.abstain.valid.json"
        )
        summary_available = _load(
            ROOT / "contracts/examples/news_sentiment_summary.v2.available.valid.json"
        )
        summary_abstain = _load(
            ROOT / "contracts/examples/news_sentiment_summary.v2.abstain.valid.json"
        )

        for payload in (gdelt_available, gdelt_abstain):
            self.assertEqual([], list(gdelt_validator.iter_errors(payload)))
            validate_gdelt_observation_semantics(payload)
            self.assertEqual("NONE", payload["decisionAuthority"])
            self.assertFalse(payload["rawProviderDataStored"])
            self.assertFalse(payload["articleMetadataStored"])

        for payload in (summary_available, summary_abstain):
            self.assertEqual([], list(summary_validator.iter_errors(payload)))
            validate_news_summary_semantics(payload)
            self.assertEqual("NONE", payload["decisionAuthority"])
            self.assertEqual(["EXPLANATION_ONLY"], payload["allowedUses"])
            self.assertFalse(payload["riskDecisionHashIncluded"])
            self.assertFalse(payload["s5FeatureEligible"])

    def test_required_negative_fixtures_fail_closed(self) -> None:
        self.assertEqual(
            {
                "article-title",
                "article-url",
                "available-at-inversion",
                "fake-zero",
                "future-observed-at",
                "missing-attribution",
                "nan",
                "partial-available",
                "raw-query",
                "unknown-field",
            },
            {
                Path(path).name.split(".")[-3]
                for path in INVALID_FIXTURE_PATHS
            },
        )

        validators = {
            "gdelt_news_tone_observation.v1": Draft202012Validator(
                _load(GDELT_SCHEMA_PATH)
            ),
            "news_sentiment_summary.v2": Draft202012Validator(
                _load(NEWS_SUMMARY_SCHEMA_PATH)
            ),
        }
        for relative_path in sorted(INVALID_FIXTURE_PATHS):
            path = ROOT / relative_path
            schema_id = ".".join(path.name.split(".")[:2])
            try:
                payload = _load(path)
            except ContractValidationError:
                self.assertIn(".nan.invalid.json", path.name)
                continue
            errors = list(validators[schema_id].iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not errors:
                try:
                    if schema_id == "gdelt_news_tone_observation.v1":
                        validate_gdelt_observation_semantics(payload)
                    else:
                        validate_news_summary_semantics(payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            self.assertTrue(errors or semantic_error, relative_path)

    def test_timestamp_order_is_semantic_and_not_clock_dependent(self) -> None:
        available = _load(
            ROOT
            / "contracts/examples/gdelt_news_tone_observation.v1.available.valid.json"
        )
        self.assertIsInstance(available, dict)

        future_observation = copy.deepcopy(available)
        future_observation["observedAt"] = "2026-08-01T00:00:01Z"
        with self.assertRaisesRegex(ContractValidationError, "observedAt"):
            validate_gdelt_observation_semantics(future_observation)

        inversion = copy.deepcopy(available)
        inversion["availableAt"] = "2026-07-30T23:59:59Z"
        with self.assertRaisesRegex(ContractValidationError, "availableAt"):
            validate_gdelt_observation_semantics(inversion)

        # Validator는 현재 wall clock이 아니라 payload 안의 순서만 사용해야 한다.
        self.assertLess(
            datetime.fromisoformat(available["observedAt"].replace("Z", "+00:00")),
            datetime.now(UTC),
        )

    def test_generated_fixture_manifests_are_exact(self) -> None:
        self.assertEqual(4, len(VALID_FIXTURE_PATHS))
        self.assertEqual(10, len(INVALID_FIXTURE_PATHS))
        self.assertEqual(
            {
                "contracts/schemas/gdelt_news_tone_observation.v1.schema.json",
                "contracts/schemas/news_sentiment_summary.v2.schema.json",
                *VALID_FIXTURE_PATHS,
                *INVALID_FIXTURE_PATHS,
            },
            set(OUTPUTS),
        )


if __name__ == "__main__":
    unittest.main()
