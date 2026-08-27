from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from contracts.generate_principle_contracts import ContractValidationError, canonical_json_bytes
from contracts.verify_p1_automation_journal_openapi_transition import (
    ADDITIVE_PATH,
    OPENAPI_PATH,
    verify_transition,
)


class P1AutomationJournalOpenApiTransitionTest(unittest.TestCase):
    def test_current_exact_56_transition_preserves_exact_48_projection(self) -> None:
        verify_transition()

    def test_existing_schema_mutation_is_rejected(self) -> None:
        document = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        document["components"]["schemas"]["S24KillSwitchState"]["type"] = "string"
        self._assert_rejected(document)

    def test_ninth_route_and_operation_id_drift_are_rejected(self) -> None:
        document = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        document["paths"]["/api/v1/automation/status"]["get"][
            "operationId"
        ] = "driftedAutomationStatus"
        self._assert_rejected(document)

        document = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        document["paths"]["/api/v1/unapproved"] = {
            "get": {"operationId": "unapprovedOperation", "responses": {"200": {"description": "Success"}}}
        }
        self._assert_rejected(document)

    def test_additive_schema_mutation_is_rejected(self) -> None:
        document = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        document["components"]["schemas"]["Journal"]["properties"]["content"][
            "maxLength"
        ] = 8193
        self._assert_rejected(document)

    def _assert_rejected(self, document: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openapi.json"
            path.write_bytes(canonical_json_bytes(copy.deepcopy(document)))
            with self.assertRaises(ContractValidationError):
                verify_transition(path, ADDITIVE_PATH)


if __name__ == "__main__":
    unittest.main()
