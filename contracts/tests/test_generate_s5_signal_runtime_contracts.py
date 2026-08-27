from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_principle_contracts import (
    ContractValidationError,
    canonical_json_bytes,
)
from contracts.generate_s5_0_signal_v2_contracts import FROZEN_EXISTING_HASHES
from contracts.generate_s5_signal_runtime_contracts import (
    ARTIFACT_PATHS,
    ARTIFACT_SCHEMA,
    RUNTIME_SCHEMA,
    UNKNOWN_FIELDS,
    build_artifacts,
    validate_runtime_semantics,
)
from contracts.verify_s5_signal_runtime_transition import verify_openapi_transition


ROOT = Path(__file__).resolve().parents[2]


class S5SignalRuntimeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.artifacts = build_artifacts()
        self.runtime_validator = Draft202012Validator(
            self.artifacts[RUNTIME_SCHEMA],
            format_checker=FormatChecker(),
        )
        self.artifact_validator = Draft202012Validator(
            self.artifacts[ARTIFACT_SCHEMA],
            format_checker=FormatChecker(),
        )

    def test_old_s5_outputs_and_signal_v1_bytes_remain_frozen(self) -> None:
        for relative, expected in FROZEN_EXISTING_HASHES.items():
            if relative == "contracts/openapi/openapi.json":
                continue
            self.assertEqual(
                expected, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            )
        for relative in (
            "contracts/schemas/signal-v2.schema.json",
            "contracts/examples/signal-v2.available.valid.json",
            "contracts/examples/signal-v2.abstain.valid.json",
        ):
            self.assertNotIn(relative, ARTIFACT_PATHS)

    def test_v1_and_v2_each_have_one_generated_negative_per_unknown_field(self) -> None:
        for field in UNKNOWN_FIELDS:
            slug = []
            for character in field:
                if character.isupper():
                    slug.extend(("-", character.lower()))
                else:
                    slug.append(character)
            suffix = "".join(slug).lstrip("-")
            for prefix in ("signal", "signal-v2"):
                path = (
                    f"contracts/examples/invalid/{prefix}.unknown-{suffix}.invalid.json"
                )
                self.assertIn(path, self.artifacts)
                injected = set(self.artifacts[path]) - set(
                    json.loads(
                        (
                            ROOT / f"contracts/examples/{prefix}.available.valid.json"
                        ).read_text()
                    )
                    if prefix == "signal-v2"
                    else json.loads(
                        (ROOT / "contracts/examples/signal.valid.json").read_text()
                    )
                )
                self.assertEqual({field}, injected)

    def test_runtime_accepts_all_abstain_partial_and_available_hold(self) -> None:
        for name in ("all-abstain", "partial-abstain", "available-hold"):
            payload = self.artifacts[
                f"contracts/examples/signal-v2-runtime-v1.{name}.valid.json"
            ]
            self.assertEqual(
                [], list(self.runtime_validator.iter_errors(payload)), name
            )
            validate_runtime_semantics(payload)
        all_abstain = self.artifacts[
            "contracts/examples/signal-v2-runtime-v1.all-abstain.valid.json"
        ]
        self.assertNotIn("asOf", all_abstain)
        self.assertNotIn("modelReportId", all_abstain)
        available = self.artifacts[
            "contracts/examples/signal-v2-runtime-v1.available-hold.valid.json"
        ]
        self.assertEqual("HOLD", available["composite"]["signal"])

    def test_runtime_rejects_fabricated_abstain_values_and_composite_smuggling(
        self,
    ) -> None:
        base = self.artifacts[
            "contracts/examples/signal-v2-runtime-v1.all-abstain.valid.json"
        ]
        for field, value in (
            ("asOf", "2026-08-14T06:30:00Z"),
            ("signal", "HOLD"),
            ("confidence", 0),
            ("predictedReturn", 0),
            ("state", "SIDEWAYS"),
        ):
            payload = copy.deepcopy(base)
            payload["components"]["hmmRegime"][field] = value
            self.assertTrue(list(self.runtime_validator.iter_errors(payload)), field)
        payload = copy.deepcopy(base)
        payload["composite"] = {
            "status": "AVAILABLE",
            "signal": "HOLD",
            "confidence": 0.0,
        }
        self.assertEqual([], list(self.runtime_validator.iter_errors(payload)))
        with self.assertRaisesRegex(ContractValidationError, "force composite"):
            validate_runtime_semantics(payload)

    def test_internal_artifact_is_closed_and_fake_cannot_claim_production(self) -> None:
        for status in ("available", "abstain"):
            payload = self.artifacts[
                f"contracts/examples/lightgbm-signal-artifact-v1.{status}.valid.json"
            ]
            self.assertEqual(
                [], list(self.artifact_validator.iter_errors(payload)), status
            )
            unknown = copy.deepcopy(payload)
            unknown["modelScore"] = [0.1, 0.2, 0.7]
            self.assertTrue(list(self.artifact_validator.iter_errors(unknown)))
            production = copy.deepcopy(payload)
            production["provenanceClass"] = "PRODUCTION"
            self.assertTrue(list(self.artifact_validator.iter_errors(production)))

    def test_openapi_transition_accepts_historical_and_rejects_unapproved_change(
        self,
    ) -> None:
        verify_openapi_transition()
        document = json.loads((ROOT / "contracts/openapi/openapi.json").read_text())
        document["info"]["title"] = "smuggled"
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "openapi.json"
            path.write_bytes(canonical_json_bytes(document))
            with self.assertRaisesRegex(ContractValidationError, "outside"):
                verify_openapi_transition(path)

        additive = json.loads((ROOT / "contracts/openapi/openapi.json").read_text())
        additive["paths"]["/api/v1/stream-metrics"]["get"]["summary"] = "smuggled"
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "openapi.json"
            path.write_bytes(canonical_json_bytes(additive))
            with self.assertRaisesRegex(
                ContractValidationError,
                "additive OpenAPI fragment drifted"
                "|exact Automation/Journal addition"
                "|approved exact-five V91 addition",
            ):
                verify_openapi_transition(path)


if __name__ == "__main__":
    unittest.main()
