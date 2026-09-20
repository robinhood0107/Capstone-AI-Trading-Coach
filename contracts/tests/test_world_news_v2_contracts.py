from __future__ import annotations

import json
import unittest

import jsonschema

from contracts.generate_world_news_v2_contracts import (
    CURRENT,
    NEGATIVE,
    POSITIVE,
    PERFORMANCE_POSITIVE,
    PREVIOUS,
    item_schema,
    performance_report_schema,
    project_previous,
)


class WorldNewsV2ContractTest(unittest.TestCase):
    def test_world_news_and_performance_are_additive_and_previous_root_is_exact(self) -> None:
        current = json.loads(CURRENT.read_text(encoding="utf-8"))
        self.assertEqual(project_previous(current), json.loads(PREVIOUS.read_text(encoding="utf-8")))
        self.assertIn("/api/v2/rag/world-news", current["paths"])
        self.assertIn("/api/v1/dashboard/performance-reports/latest", current["paths"])

    def test_world_news_positive_and_negative_fixtures(self) -> None:
        schema = item_schema()
        positive = json.loads(POSITIVE.read_text(encoding="utf-8"))["items"][0]
        jsonschema.Draft202012Validator(schema).validate(positive)
        for path in sorted(NEGATIVE.glob("*.invalid.json")):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.Draft202012Validator(schema).validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )

    def test_performance_report_fixture_separates_all_three_sections(self) -> None:
        value = json.loads(PERFORMANCE_POSITIVE.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(performance_report_schema()).validate(value)
        self.assertEqual(
            set(value["report"]["sections"]),
            {"recalculatedBacktest", "fixedDailyForecast", "actualTrading"},
        )
