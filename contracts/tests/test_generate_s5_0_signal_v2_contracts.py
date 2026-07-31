from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_principle_contracts import ContractValidationError
from contracts.generate_s5_0_signal_v2_contracts import (
    ARTIFACT_PATHS,
    build_artifacts,
    validate_signal_v2_semantics,
)


ROOT = Path(__file__).resolve().parents[2]


class SignalV2ContractGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.artifacts = build_artifacts()
        self.schema = self.artifacts["contracts/schemas/signal-v2.schema.json"]
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())
        self.available = self.artifacts["contracts/examples/signal-v2.available.valid.json"]
        self.abstain = self.artifacts["contracts/examples/signal-v2.abstain.valid.json"]

    def test_generator_owns_exact_schema_catalog_and_fixture_set(self) -> None:
        self.assertEqual(set(ARTIFACT_PATHS), set(self.artifacts))
        self.assertEqual(
            {
                "contracts/catalogs/s5-0-signal-v2-contract.v1.json",
                "contracts/examples/invalid/signal-v2.abstain-fabrication.invalid.json",
                "contracts/examples/invalid/signal-v2.composite-smuggling.invalid.json",
                "contracts/examples/invalid/signal-v2.unknown-authority.invalid.json",
                "contracts/examples/invalid/signal-v2.unknown-cross-market.invalid.json",
                "contracts/examples/signal-v2.abstain.valid.json",
                "contracts/examples/signal-v2.available.valid.json",
                "contracts/schemas/signal-v2.schema.json",
            },
            set(self.artifacts),
        )

    def test_available_and_abstain_truth_table_is_closed(self) -> None:
        for payload in (self.available, self.abstain):
            self.assertEqual([], list(self.validator.iter_errors(payload)))
            validate_signal_v2_semantics(payload)

        self.assertEqual("HOLD", self.available["composite"]["signal"])
        self.assertGreater(self.available["composite"]["confidence"], 0)
        self.assertEqual("ABSTAIN", self.abstain["composite"]["status"])
        self.assertNotIn("signal", self.abstain["composite"])
        self.assertNotIn("confidence", self.abstain["composite"])
        self.assertNotIn("asOf", self.abstain["components"]["hmmRegime"])
        self.assertNotIn("state", self.abstain["components"]["hmmRegime"])

    def test_every_forbidden_adjacent_authority_field_is_rejected(self) -> None:
        forbidden = (
            "crossMarketScore",
            "crossMarketMode",
            "crossMarketFreshness",
            "crossMarketExposure",
            "analyst",
            "news",
            "cause",
            "rag",
            "llm",
            "riskDecision",
            "orderAuthority",
        )
        for field in forbidden:
            mutated = copy.deepcopy(self.available)
            mutated[field] = "FORBIDDEN"
            self.assertTrue(list(self.validator.iter_errors(mutated)), field)

    def test_abstain_cannot_smuggle_prediction_or_fabricated_time_and_state(self) -> None:
        for field, value in (
            ("signal", "HOLD"),
            ("confidence", 0),
            ("predictedReturn", 0),
            ("asOf", "2026-07-31T00:00:00Z"),
            ("state", "SIDEWAYS"),
        ):
            mutated = copy.deepcopy(self.abstain)
            mutated["components"]["hmmRegime"][field] = value
            self.assertTrue(list(self.validator.iter_errors(mutated)), field)

    def test_required_component_abstain_forces_composite_abstain(self) -> None:
        smuggled = copy.deepcopy(self.abstain)
        smuggled["composite"] = copy.deepcopy(self.available["composite"])
        self.assertEqual([], list(self.validator.iter_errors(smuggled)))
        with self.assertRaisesRegex(ContractValidationError, "required component"):
            validate_signal_v2_semantics(smuggled)

    def test_catalog_freezes_v1_openapi_and_cross_market_isolation(self) -> None:
        catalog = self.artifacts["contracts/catalogs/s5-0-signal-v2-contract.v1.json"]
        self.assertEqual("NO_GO", catalog["runtimePublication"]["activeEndpoint"])
        self.assertEqual("NO_GO", catalog["runtimePublication"]["riskDecisionWiring"])
        self.assertEqual("NO_GO", catalog["runtimePublication"]["orderWiring"])
        self.assertEqual(0, catalog["runtimePublication"]["externalCalls"])
        self.assertEqual(0, catalog["datasetModelIsolation"]["crossMarketReaderCalls"])
        self.assertEqual(
            ["datasetHash", "modelHash", "signalV2Hash"],
            catalog["datasetModelIsolation"]["invariantHashes"],
        )
        self.assertEqual(
            "ae6b2285d1df7ce608778cd59c332332e8c44ce38a861d23d57dc8f0f9b912c2",
            catalog["compatibility"]["signalV1SchemaSha256"],
        )
        self.assertEqual(
            "94414736f6a1c17b95eafffd53a07a5d33d7a66705890c53dcc971eb5ded3f89",
            catalog["compatibility"]["currentOpenApiSha256"],
        )

    def test_checked_in_artifacts_are_deterministic(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "contracts/generate_s5_0_signal_v2_contracts.py"),
                "--check",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        for relative, expected in self.artifacts.items():
            actual = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            self.assertEqual(expected, actual, relative)

    def test_contracts_ci_runs_new_generator_checks(self) -> None:
        workflow = (ROOT / ".github/workflows/contracts-ci.yml").read_text(encoding="utf-8")
        self.assertIn("contracts/generate_rag_proto.py --check", workflow)
        self.assertIn("contracts/generate_s5_0_signal_v2_contracts.py --check", workflow)


if __name__ == "__main__":
    unittest.main()
