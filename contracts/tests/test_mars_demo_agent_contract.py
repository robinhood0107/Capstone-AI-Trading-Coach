from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_spec_validator import validate


CONTRACT = Path(__file__).resolve().parents[1] / "openapi/mars-demo-agent.v1.openapi.json"


class MarsDemoAgentContractTest(unittest.TestCase):
    def test_anonymous_demo_exposes_only_bounded_ask_without_history_or_account_fields(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        self.assertEqual(set(document["paths"]), {"/api/v1/demo/agent/ask"})
        operation = document["paths"]["/api/v1/demo/agent/ask"]["post"]
        self.assertEqual(operation["security"], [])
        self.assertEqual(set(operation["responses"]), {"200", "400", "422", "429", "503"})
        request = document["components"]["schemas"]["Ask"]
        self.assertEqual(set(request["properties"]), {"questionId"})
        self.assertEqual(len(request["properties"]["questionId"]["enum"]), 3)
        self.assertFalse(request["additionalProperties"])
        answer = document["components"]["schemas"]["Answer"]
        self.assertEqual(set(answer["properties"]), {"answer", "citations", "generationStatus"})
        self.assertFalse(answer["additionalProperties"])
