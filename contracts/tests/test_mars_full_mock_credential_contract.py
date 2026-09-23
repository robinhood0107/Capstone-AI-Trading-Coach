from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_spec_validator import validate


CONTRACT = Path(__file__).resolve().parents[1] / "openapi/mars-full-mock-credential.v1.openapi.json"


class MarsFullMockCredentialContractTest(unittest.TestCase):
    def test_only_mock_owner_input_is_writeable_and_reads_are_masked(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        operations = document["paths"]["/api/v1/brokerage/mock/credential"]
        request = document["components"]["schemas"]["PutMockCredential"]
        self.assertEqual(set(operations), {"get", "put"})
        self.assertEqual(set(request["properties"]), {"appKey", "appSecret", "accountNo"})
        self.assertFalse(request["additionalProperties"])
        self.assertTrue(all(value["writeOnly"] for value in request["properties"].values()))
        summary = document["components"]["schemas"]["MockCredentialSummary"]
        self.assertEqual(
            set(summary["properties"]),
            {"accountId", "state", "revision", "appKeyLast4", "accountNoLast4", "connected", "certified"},
        )
        self.assertEqual(set(operations["put"]["responses"]), {"204", "400", "401", "409"})
        connect = document["paths"]["/api/v1/brokerage/mock/credential/connect"]
        self.assertEqual(set(connect), {"post"})
        self.assertNotIn("requestBody", connect["post"])
