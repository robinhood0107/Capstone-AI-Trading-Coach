from __future__ import annotations

import json
import unittest

import jsonschema

from contracts.historical_openapi_projection import project_historical_root

from contracts.generate_world_news_v2_contracts import (
    CURRENT,
    NEGATIVE,
    OVERLAY,
    POSITIVE,
    PERFORMANCE_POSITIVE,
    PREVIOUS,
    error_schema,
    item_schema,
    performance_report_schema,
    project_previous,
)


class WorldNewsV2ContractTest(unittest.TestCase):
    def test_world_news_and_performance_are_additive_and_previous_root_is_exact(self) -> None:
        current = json.loads(CURRENT.read_text(encoding="utf-8"))
        # PREVIOUS 는 한 세대의 바이트다. 그 뒤에 내린 제품 결정(매수 마감 09:40->14:30
        # 등)을 되돌린 뒤 비교해야 비교가 성립한다.
        self.assertEqual(
            project_historical_root(project_previous(current)),
            json.loads(PREVIOUS.read_text(encoding="utf-8")),
        )
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

    def test_error_component_resolves_in_overlay_and_root(self) -> None:
        for path in (OVERLAY, CURRENT):
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                error_schema(), document["components"]["schemas"]["WorldNewsV2Error"]
            )

    def test_performance_report_fixture_separates_all_three_sections(self) -> None:
        value = json.loads(PERFORMANCE_POSITIVE.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(performance_report_schema()).validate(value)
        self.assertEqual(
            set(value["report"]["sections"]),
            {"recalculatedBacktest", "fixedDailyForecast", "actualTrading"},
        )
