"""Current promotion policy around the preserved S1.4X reference content."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/s1-4x-contract-correctness.yml"
REFERENCE = (
    ROOT
    / "workspaces/decision-platform/research/s1-4x-numeric-parity/contract/reference-lock.v1.json"
)
FIXTURES = ROOT / "contracts/fixtures/s1-4x-reference-runtime"


class S14xPromotionWorkflowTest(unittest.TestCase):
    def test_reference_content_and_promotion_only_gate(self) -> None:
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        workflow = yaml.safe_load(workflow_text)
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events), {"pull_request"})
        self.assertEqual(events["pull_request"]["branches"], ["main"])
        self.assertEqual(
            json.loads(REFERENCE.read_text(encoding="utf-8"))["referenceBaseCommit"],
            "bf8472dfcc5f9d883ca83bd461a62f254332b39f",
        )
        self.assertIn("assert tests == 262", workflow_text)
        self.assertIn("validate_reference_lock(repo, contract)", workflow_text)
        self.assertIn("not test_workflow_runs_both_triggers_and_accounts_for_262_snapshot_tests", workflow_text)
        for filename, expected in {
            "pyproject.toml": "fd114bcabb230368d45142ffe7c68a47ea2ce860021d00abaeaf334d37f928eb",
            "uv.lock": "8cb06fb15858ba9bec5f94951cb89ece38d2ddfe13292d9a82df78f042393459",
        }.items():
            self.assertEqual(hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
