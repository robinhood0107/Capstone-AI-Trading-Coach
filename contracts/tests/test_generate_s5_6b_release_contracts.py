from __future__ import annotations

import json
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_s5_6b_release_contracts import artifacts


class S56BReleaseContractGenerationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generated = artifacts()

    def _json(self, path: str) -> object:
        return json.loads(self.generated[path])

    def test_positive_release_and_batch_validate(self) -> None:
        pairs = (
            (
                "contracts/schemas/s5-model-release-v1.schema.json",
                "contracts/examples/s5-model-release-v1.valid.json",
            ),
            (
                "contracts/schemas/s5-signal-batch-v1.schema.json",
                "contracts/examples/s5-signal-batch-v1.valid.json",
            ),
        )
        for schema_path, fixture_path in pairs:
            validator = Draft202012Validator(
                self._json(schema_path), format_checker=FormatChecker()
            )
            self.assertEqual([], list(validator.iter_errors(self._json(fixture_path))))

    def test_negative_corpus_is_rejected(self) -> None:
        cases = (
            (
                "contracts/schemas/s5-model-release-v1.schema.json",
                "contracts/examples/invalid/s5-model-release-v1.unknown-field.invalid.json",
            ),
            (
                "contracts/schemas/s5-model-release-v1.schema.json",
                "contracts/examples/invalid/s5-model-release-v1.fake.invalid.json",
            ),
            (
                "contracts/schemas/s5-signal-batch-v1.schema.json",
                "contracts/examples/invalid/s5-signal-batch-v1.row-count.invalid.json",
            ),
            (
                "contracts/schemas/s5-signal-batch-v1.schema.json",
                "contracts/examples/invalid/s5-signal-batch-v1.artifact-path.invalid.json",
            ),
        )
        for schema_path, fixture_path in cases:
            validator = Draft202012Validator(
                self._json(schema_path), format_checker=FormatChecker()
            )
            self.assertTrue(list(validator.iter_errors(self._json(fixture_path))))

    def test_every_object_branch_is_closed(self) -> None:
        def visit(value: object) -> None:
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self._json("contracts/schemas/s5-model-release-v1.schema.json"))
        visit(self._json("contracts/schemas/s5-signal-batch-v1.schema.json"))

    def test_batch_fixture_locks_xkrx_substitute_holiday_clock(self) -> None:
        batch = self._json("contracts/examples/s5-signal-batch-v1.valid.json")
        catalog = self._json("contracts/catalogs/s5-production-release-lock.v1.json")
        self.assertEqual("2026-08-14", batch["sessionDate"])
        self.assertEqual("2026-08-17T23:10:00Z", batch["asOf"])
        self.assertEqual("2026-08-18", catalog["sessionClock"]["regression"]["nextSession"])
        self.assertFalse(catalog["sessionClock"]["calendarDatePlusOneAllowed"])


if __name__ == "__main__":
    unittest.main()
