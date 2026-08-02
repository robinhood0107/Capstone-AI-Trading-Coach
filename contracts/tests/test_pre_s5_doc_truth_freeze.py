from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import contracts.verify_pre_s5_doc_truth_freeze as truth_freeze
from contracts.verify_pre_s5_doc_truth_freeze import (
    collect_markdown_receipt,
    verify_public_truth_freeze,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class PreS5DocumentTruthFreezeTest(unittest.TestCase):
    def test_receipt_reads_regular_markdown_to_eof_without_following_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docs = root / "docs"
            docs.mkdir()
            (docs / "active.md").write_text("# Active\n\n본문\n", encoding="utf-8")
            (docs / "historical.md").write_text("# Historical\n", encoding="utf-8")
            generated = root / "build"
            generated.mkdir()
            (generated / "ignored.md").write_text("# Generated\n", encoding="utf-8")

            try:
                os.symlink(docs / "active.md", docs / "link.md")
            except OSError as error:
                self.skipTest(f"symlink fixture is unavailable: {error}")

            receipt = collect_markdown_receipt(root)

        regular_paths = [item["path"] for item in receipt["regularFiles"]]
        self.assertEqual(["docs/active.md", "docs/historical.md"], regular_paths)
        self.assertEqual(["docs/link.md"], receipt["skippedSymlinks"])
        self.assertTrue(all(item["eofNewline"] for item in receipt["regularFiles"]))
        self.assertTrue(all(item["sha256"] for item in receipt["regularFiles"]))

    def test_repository_public_ssot_has_the_pre_s5_truth_freeze_markers(self) -> None:
        errors = verify_public_truth_freeze(REPO_ROOT)
        self.assertEqual([], errors)

    def test_public_truth_freeze_rejects_required_and_linked_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            root = temporary_root / "repository"
            docs = root / "docs"
            workspace = root / "workspaces" / "decision-platform"
            docs.mkdir(parents=True)
            workspace.mkdir(parents=True)
            (workspace / "README.md").write_text("# Decision\n", encoding="utf-8")
            outside = temporary_root / "outside.md"
            outside.write_text("# outside-anchor\nrequired marker\n", encoding="utf-8")

            try:
                os.symlink(outside, docs / "README.md")
            except OSError as error:
                self.skipTest(f"symlink fixture is unavailable: {error}")

            with (
                mock.patch.object(truth_freeze, "V1_FROZEN_SHA256", {}),
                mock.patch.object(truth_freeze, "IMMUTABLE_WORKSPACE_SHA256", {}),
                mock.patch.object(truth_freeze, "EXACT30_SOURCE_TREE_SHA256", "synthetic"),
                mock.patch.object(truth_freeze, "tree_digest", return_value="synthetic"),
                mock.patch.object(truth_freeze, "tracked_local_reference_error", return_value=None),
                mock.patch.object(
                    truth_freeze,
                    "REQUIRED_PUBLIC_MARKERS",
                    {"docs/README.md": ("required marker",)},
                ),
                mock.patch.object(truth_freeze, "FORBIDDEN_PUBLIC_MARKERS", {}),
            ):
                required_symlink_errors = truth_freeze.verify_public_truth_freeze(root)
                self.assertIn(
                    "docs/README.md: required active SSOT is missing or unsafe",
                    required_symlink_errors,
                )

                (docs / "README.md").unlink()
                (docs / "README.md").write_text(
                    "# Active\n\nrequired marker\n\n[local](link.md#outside-anchor)\n",
                    encoding="utf-8",
                )
                os.symlink(outside, docs / "link.md")

                linked_symlink_errors = truth_freeze.verify_public_truth_freeze(root)
                self.assertIn(
                    "docs/README.md: missing local Markdown target 'link.md#outside-anchor'",
                    linked_symlink_errors,
                )

                (docs / "link.md").unlink()
                (docs / "link.md").write_text("# outside-anchor\n", encoding="utf-8")
                self.assertEqual([], truth_freeze.verify_public_truth_freeze(root))


if __name__ == "__main__":
    unittest.main()
