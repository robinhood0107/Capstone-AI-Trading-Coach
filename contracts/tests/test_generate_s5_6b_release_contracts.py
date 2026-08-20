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


    def test_automatic_retrain_is_open_but_activation_stays_manual(self) -> None:
        """gate 실패가 종단이면 데이터가 쌓여도 모델이 영원히 나오지 않는다.

        그래서 재학습과 release stage는 자동으로 열되 활성 pointer 전환은 사람이 한다. 서빙
        모델이 승인 없이 바뀌지 않는다.
        """

        payload = self._json("contracts/catalogs/s5-production-release-lock.v1.json")

        self.assertEqual(1, payload["automaticRetrain"])
        self.assertEqual(1, payload["stagedReleaseAwaitingManualActivation"])
        self.assertEqual(0, payload["automaticModelActivation"])
        self.assertEqual("MANUAL_EXPECTED_CURRENT_CAS", payload["activation"])
        self.assertEqual(0, payload["orderWiring"])
        self.assertEqual(0, payload["riskDecisionWiring"])

    def test_requalification_is_bounded_and_last_good_keeps_serving(self) -> None:
        """매 tick마다 다시 학습하면 계산과 seal이 무의미하게 쌓인다."""

        payload = self._json("contracts/catalogs/s5-production-release-lock.v1.json")

        self.assertEqual(
            ["APPEND_SESSION_THRESHOLD", "MONTH_BOUNDARY"],
            payload["requalificationTriggers"],
        )
        self.assertEqual(21, payload["requalificationSessionThreshold"])
        # 무엇을 이미 학습했는지의 권위는 append-only 상태 이력이다.
        self.assertEqual("RUN_STATE_HISTORY", payload["requalificationWatermarkSource"])
        self.assertEqual(1, payload["failedRequalificationLeavesActivePointerUntouched"])
        self.assertEqual(1, payload["lastGoodKeepsServingOnGateFailure"])
        self.assertEqual(1, payload["absentLastGoodServesAbstain"])

if __name__ == "__main__":
    unittest.main()
