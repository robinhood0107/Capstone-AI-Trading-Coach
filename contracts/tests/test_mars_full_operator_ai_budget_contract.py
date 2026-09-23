from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_spec_validator import validate


CONTRACT = Path(__file__).resolve().parents[1] / "openapi/mars-full-operator-ai-budget.v1.openapi.json"


class MarsFullOperatorAiBudgetContractTest(unittest.TestCase):
    def test_only_admin_policy_endpoint_writes_integer_cents_below_private_ceiling(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        operations = document["paths"]["/api/v1/admin/ai-budget"]
        self.assertEqual(set(operations), {"get", "put"})
        self.assertIn("403", operations["get"]["responses"])
        self.assertIn("403", operations["put"]["responses"])
        request = document["components"]["schemas"]["PutPolicy"]
        self.assertEqual(set(request["properties"]), {"dailySoftCapCents", "expectedRevision"})
        self.assertFalse(request["additionalProperties"])
