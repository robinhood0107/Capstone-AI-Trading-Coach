#!/usr/bin/env python3
"""Exercise develop's routing and required job outcomes."""

import runpy
import unittest
from pathlib import Path

CHECKS = runpy.run_path(str(Path(__file__).with_name("develop-checks.py")))
select_checks = CHECKS["select_checks"]
verify = CHECKS["verify"]


class DevelopChecksTest(unittest.TestCase):
    def test_docs_and_other_source_do_not_enable_additional_jobs(self):
        self.assertEqual(select_checks({"README.md", "workspaces/return-engine/src/main.py"}), ([], False))

    def test_changed_manifests_only_route_matching_projects(self):
        paths = {"capstone-rag/ocr/cpu/uv.lock", "workspaces/return-engine/pyproject.toml",
                 "workspaces/return-engine/uv.lock"}
        self.assertEqual(select_checks(paths), (["capstone-rag/ocr/cpu", "workspaces/return-engine"], False))

    def test_backup_changes_run_the_restore_rehearsal(self):
        for path in ("deploy/p1/restore-test", "deploy/p1/compose.yml", "infra/init/01-extensions.sql", "capstone"):
            with self.subTest(path=path):
                self.assertEqual(select_checks({path}), ([], True))

    def test_required_success_and_intentional_skips_pass(self):
        verify(["classify", "true", "success", "full", "true", "success", "demo", "false", "skipped"])

    def test_failure_cancellation_skip_or_missing_result_fail_the_gate(self):
        for result in ("failure", "cancelled", "skipped", ""):
            with self.subTest(result=result), self.assertRaises(SystemExit):
                verify(["backup", "true", result])
        with self.assertRaises(SystemExit):
            verify(["full", "", "skipped"])
        with self.assertRaises(SystemExit):
            verify(["demo", "false", "failure"])


if __name__ == "__main__":
    unittest.main()
