from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
