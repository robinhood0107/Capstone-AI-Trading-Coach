from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import contracts.verify_pre_s5_doc_truth_freeze as truth_freeze
from contracts.generate_pre_s5_rag_news_contracts import FROZEN_EXISTING_HASHES
from contracts.generate_s4_7d_rag_v2_contracts import FROZEN_V1_HASHES
from contracts.verify_pre_s5_doc_truth_freeze import (
    IMMUTABLE_HISTORY_PATH_PREFIXES,
    IMMUTABLE_PRE_S5_FROZEN_PATHS,
    SOLO_OWNERSHIP_MARKERS,
    SOLO_OWNERSHIP_PUBLIC_PATHS,
    SOLO_OWNERSHIP_ROLE_CATALOG,
    collect_markdown_receipt,
    classify_markdown,
    verify_public_truth_freeze,
    verify_solo_ownership_lock,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class PreS5DocumentTruthFreezeTest(unittest.TestCase):
    @staticmethod
    def _commit(root: Path, message: str) -> None:
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)

    def _solo_ownership_fixture(self, root: Path) -> str:
        """base 대비 public 문서와 teammate workspace의 drift를 검사할 최소 Git fixture다."""

        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)

        for relative in SOLO_OWNERSHIP_PUBLIC_PATHS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            marker_block = "\n".join(SOLO_OWNERSHIP_MARKERS)
            if relative == "docs/README.md":
                catalog = "\n".join(SOLO_OWNERSHIP_ROLE_CATALOG)
                text = (
                    "# Active\n\n"
                    "<!-- PRE_S5_SOLO_ROLE_CATALOG_BEGIN -->\n"
                    f"{catalog}\n"
                    "<!-- PRE_S5_SOLO_ROLE_CATALOG_END -->\n"
                )
            else:
                text = f"# Active\n\n{marker_block}\n"
            path.write_text(text, encoding="utf-8")

        for relative in (
            "workspaces/return-engine/README.md",
            "workspaces/experience-dashboard/README.md",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Placeholder\n", encoding="utf-8")

        for relative in (
            "contracts/catalogs/s4-rag-contract.v1.json",
            "contracts/openapi/openapi.json",
            "contracts/proto/rag.descriptor.pb",
            "contracts/proto/rag_v2.descriptor.pb",
            "contracts/schemas/s4-rag-answer.schema.json",
            "docs/adr/ADR-999-existing.md",
            "docs/decision-platform/historical-record.md",
            "contracts/changes/20260801-existing.md",
            "capstone-rag/eval/s4-5-evaluation-60.v1.json",
            "capstone-rag/manifests/completion-manifest.v1.sha256",
            "capstone-rag/ocr/benchmark/receipts/benchmark-summary.v1.json",
            "capstone-rag/reports/completion-receipt.v1.json",
            "capstone-rag/source-cards/README.md",
            "capstone-rag/source-cards/s4-7b/card-001.v1.json",
            "capstone-rag/source-cards/s4-7c-external/card-001.v1.json",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Historical record\n", encoding="utf-8")

        self._commit(root, "fixture base")
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

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

    def test_active_rag_global_news_markers_are_part_of_the_public_truth_gate(self) -> None:
        """OA112 logical-only state와 foreign-news route는 active SSOT에서 함께 검증해야 한다."""

        self.assertIn(
            "PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1",
            truth_freeze.REQUIRED_PUBLIC_MARKERS["docs/README.md"],
        )
        self.assertIn(
            "S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED",
            truth_freeze.REQUIRED_PUBLIC_MARKERS["capstone-rag/README.md"],
        )
        self.assertIn(
            "| S4.7D v2 runtime | `IMPLEMENTED_DRAFT` |",
            truth_freeze.REQUIRED_PUBLIC_MARKERS["docs/README.md"],
        )
        self.assertIn(
            "ACTIVE_V2_RUNTIME=IMPLEMENTED_DRAFT",
            truth_freeze.REQUIRED_PUBLIC_MARKERS["docs/API_명세서.md"],
        )
        self.assertIn(
            "/api/v2/market-evidence/{symbol}/foreign-news-sentiment",
            truth_freeze.REQUIRED_PUBLIC_MARKERS["docs/API_명세서.md"],
        )
        for relative in (
            "docs/README.md",
            "docs/최종_프로젝트_명세서.md",
            "docs/API_명세서.md",
            "contracts/README.md",
        ):
            self.assertIn(
                "S4_8_CORE6_V2=CONTRACT_LOCKED",
                truth_freeze.REQUIRED_PUBLIC_MARKERS[relative],
            )
            self.assertIn(
                "S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT",
                truth_freeze.REQUIRED_PUBLIC_MARKERS[relative],
            )
        for relative in (
            "AGENTS.md",
            "README.md",
            "docs/README.md",
            "docs/최종_프로젝트_명세서.md",
            "docs/API_명세서.md",
            "contracts/README.md",
            "workspaces/decision-platform/README.md",
        ):
            self.assertIn(
                "PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES",
                truth_freeze.REQUIRED_PUBLIC_MARKERS[relative],
            )

    def test_active_vertex_route_is_service_account_oauth_in_the_public_truth_gate(self) -> None:
        for relative in (
            "AGENTS.md",
            "docs/README.md",
            "docs/최종_프로젝트_명세서.md",
            "docs/API_명세서.md",
            "contracts/README.md",
            "docs/RAG_외부_AI_처리_및_개인문서_동의.md",
        ):
            self.assertIn("VERTEX_MODEL_ID", truth_freeze.REQUIRED_PUBLIC_MARKERS[relative])

        self.assertIn(
            "VERTEX_API_KEY",
            truth_freeze.FORBIDDEN_PUBLIC_MARKERS["docs/API_명세서.md"],
        )

    def test_solo_ownership_lock_accepts_exact_catalog_and_clean_teammate_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)

            self.assertEqual([], verify_solo_ownership_lock(root, base))

    def test_solo_ownership_lock_rejects_catalog_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            catalog = root / "docs/README.md"
            prefix, marker, catalog_region = catalog.read_text(encoding="utf-8").partition(
                "<!-- PRE_S5_SOLO_ROLE_CATALOG_BEGIN -->"
            )
            catalog.write_text(
                prefix
                + marker
                + catalog_region.replace("TEAMMATE_WORKSPACE_DIFF=0", "TEAMMATE_WORKSPACE_DIFF=1", 1),
                encoding="utf-8",
            )

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("docs/README.md: solo ownership role catalog differs from the exact catalog", errors)

    def test_solo_ownership_lock_rejects_new_teammate_role_or_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            api = root / "docs/API_명세서.md"
            api.write_text(
                api.read_text(encoding="utf-8")
                + "\n팀원 B의 required artifact, Issue, PR, deadline, live blocker를 새로 만든다.\n",
                encoding="utf-8",
            )
            self._commit(root, "teammate dependency drift")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("docs/API_명세서.md: new teammate dependency was added", errors)

    def test_solo_ownership_lock_allows_exact_s5_component_names_without_new_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            change = root / "contracts/changes/20260815-s5-signal-runtime-transition.md"
            change.write_text(
                "- required component는 `ruleBaseline`, `lstm`, `lightgbm`, `hmmRegime` 정확히 네 개다.\n",
                encoding="utf-8",
            )
            self._commit(root, "approved component vocabulary")

            self.assertEqual([], verify_solo_ownership_lock(root, base))

    def test_solo_ownership_lock_allows_bounded_post_s5_non_assignment_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            status = root / "docs/s8-current-status.md"
            status.write_text(
                "Team A integration은 수행하지 않았다.\n"
                "synthetic fixture는 Team B Return Engine artifact가 아니다.\n"
                "model/backtest는 sanitized projection만 읽는다.\n",
                encoding="utf-8",
            )
            self._commit(root, "bounded post-s5 status")

            self.assertEqual([], verify_solo_ownership_lock(root, base))

    def test_solo_ownership_lock_rejects_conflicting_authority_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            api = root / "docs/API_명세서.md"
            api.write_text(
                api.read_text(encoding="utf-8")
                + "\nPRE_S5_EXECUTION_OWNER=TEAM_B\n"
                + "S1_3G=EXTERNAL_OWNER_HANDOFF\n"
                + "GDELT_OUTBOUND_IMPLEMENTATION=1\n",
                encoding="utf-8",
            )
            self._commit(root, "conflicting authority assignments")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn(
            "docs/API_명세서.md: PRE_S5_EXECUTION_OWNER must have exactly one expected authority assignment",
            errors,
        )
        self.assertIn(
            "docs/API_명세서.md: S1_3G must have exactly one expected authority assignment",
            errors,
        )
        self.assertIn(
            "docs/API_명세서.md: GDELT_OUTBOUND_IMPLEMENTATION must have exactly one expected authority assignment",
            errors,
        )
        self.assertIn(
            "docs/API_명세서.md: forbidden stale solo ownership marker EXTERNAL_OWNER_HANDOFF",
            errors,
        )

    def test_solo_ownership_lock_allows_unrelated_pr_and_live_words(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            api = root / "docs/API_명세서.md"
            api.write_text(
                api.read_text(encoding="utf-8") + "\n현재 PR의 live gate는 별도 packet을 요구한다.\n",
                encoding="utf-8",
            )
            self._commit(root, "unrelated wording")

            self.assertEqual([], verify_solo_ownership_lock(root, base))

    def test_solo_ownership_lock_rejects_tracked_or_changed_teammate_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            extra = root / "workspaces/return-engine/model.py"
            extra.write_text("# out of scope\n", encoding="utf-8")
            self._commit(root, "workspace drift")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("teammate workspace has unexpected tracked paths", errors)
        self.assertIn("teammate workspace changed since base", errors)

    def test_solo_ownership_lock_rejects_ignored_teammate_workspace_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            ignored = root / "workspaces/return-engine/ignored_model.py"
            (root / ".git/info/exclude").write_text(
                "workspaces/return-engine/ignored_model.py\n",
                encoding="utf-8",
            )
            ignored.write_text("# out of scope\n", encoding="utf-8")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("teammate workspace has working-tree drift", errors)

    def test_solo_ownership_lock_rejects_immutable_history_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            for relative in (
                "contracts/catalogs/s4-rag-contract.v1.json",
                "contracts/openapi/openapi.json",
                "contracts/proto/rag.descriptor.pb",
                "contracts/proto/rag_v2.descriptor.pb",
                "contracts/schemas/s4-rag-answer.schema.json",
                "docs/adr/ADR-999-existing.md",
                "docs/decision-platform/historical-record.md",
                "contracts/changes/20260801-existing.md",
                "capstone-rag/eval/s4-5-evaluation-60.v1.json",
                "capstone-rag/manifests/completion-manifest.v1.sha256",
                "capstone-rag/ocr/benchmark/receipts/benchmark-summary.v1.json",
                "capstone-rag/reports/completion-receipt.v1.json",
                "capstone-rag/source-cards/README.md",
                "capstone-rag/source-cards/s4-7b/card-001.v1.json",
                "capstone-rag/source-cards/s4-7c-external/card-001.v1.json",
            ):
                path = root / relative
                path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
            self._commit(root, "historical record drift")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("immutable historical records changed since base", errors)
        self.assertIn("exact-30 source-card tree changed since base", errors)

    def test_solo_ownership_lock_allows_verified_s5_openapi_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            openapi = root / "contracts/openapi/openapi.json"
            openapi.write_text("{\"openapi\":\"3.1.0\"}\n", encoding="utf-8")
            catalog = root / "contracts/catalogs/s5-signal-runtime-transition.v1.json"
            catalog.write_text("{}\n", encoding="utf-8")
            self._commit(root, "verified S5 OpenAPI transition")

            with mock.patch(
                "contracts.verify_s5_signal_runtime_transition.verify_openapi_transition"
            ):
                errors = verify_solo_ownership_lock(root, base)

        self.assertNotIn("immutable historical records changed since base", errors)

    def test_solo_ownership_lock_rejects_added_exact30_source_card(self) -> None:
        """exact-30 tree digest를 바꾸는 A-only card도 base diff에서 거부한다."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            added = root / "capstone-rag/source-cards/s4-7b/card-002.v1.json"
            added.write_text("{\"sourceId\": \"src_added\"}\n", encoding="utf-8")
            self._commit(root, "exact30 source-card addition")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("exact-30 source-card tree changed since base", errors)

    def test_solo_ownership_lock_allows_a_new_historical_manifest_record(self) -> None:
        """새 addendum manifest는 허용하되 병합 뒤에는 다음 base diff에서 immutable이 된다."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            created = root / "capstone-rag/manifests/pre-s5-addendum.v1.json"
            created.write_text("{\"schemaVersion\": 1}\n", encoding="utf-8")
            self._commit(root, "new historical manifest")

            errors = verify_solo_ownership_lock(root, base)

        self.assertEqual([], errors)

    def test_frozen_contract_set_covers_legacy_v1_v2_companions(self) -> None:
        self.assertTrue(
            {
                "contracts/catalogs/s4-rag-contract.v1.json",
                "contracts/catalogs/s4-rag-v2-contract.v1.json",
                "contracts/openapi/openapi.json",
                "contracts/openapi/rag-v2.openapi.json",
                "contracts/proto/rag.proto",
                "contracts/proto/rag.descriptor.pb",
                "contracts/proto/rag.descriptor.sha256",
                "contracts/proto/rag_v2.descriptor.pb",
                "contracts/proto/rag_v2.descriptor.sha256",
                "contracts/proto/rag_v2.proto",
                "contracts/schemas/news_sentiment_summary.v2.schema.json",
                "contracts/schemas/rag-source-card-v1.schema.json",
                "contracts/schemas/rag-source-card-v2.schema.json",
                "contracts/schemas/s4-rag-answer.schema.json",
                "contracts/schemas/s4-rag-ask-request.schema.json",
                "contracts/schemas/s4-rag-history-detail.schema.json",
                "contracts/schemas/s4-rag-history-page.schema.json",
            }.issubset(IMMUTABLE_PRE_S5_FROZEN_PATHS)
        )
        for frozen_paths in (FROZEN_V1_HASHES, FROZEN_EXISTING_HASHES):
            with self.subTest(frozen_paths=frozen_paths):
                self.assertTrue(
                    all(
                        relative in IMMUTABLE_PRE_S5_FROZEN_PATHS
                        or relative.startswith(IMMUTABLE_HISTORY_PATH_PREFIXES)
                        for relative in frozen_paths
                    )
                )

    def test_solo_ownership_lock_rejects_immutable_history_type_change(self) -> None:
        """historical path를 symlink로 바꾸는 T diff도 byte-stable 보존 위반이다."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            historical = root / "contracts/changes/20260801-existing.md"
            target = root / "outside-history.md"
            target.write_text("# Outside\n", encoding="utf-8")
            historical.unlink()
            try:
                os.symlink(target, historical)
            except OSError as error:
                self.skipTest(f"symlink fixture is unavailable: {error}")
            self._commit(root, "historical type drift")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("immutable historical records changed since base", errors)

    def test_solo_ownership_lock_rejects_new_historical_teammate_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            historical = root / "docs/s5-team-dependencies.md"
            historical.write_text(
                "TEAM_B owns a new task\nLSTM output is required for S5 entry\n",
                encoding="utf-8",
            )
            self._commit(root, "new teammate dependency")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn("docs/s5-team-dependencies.md: new teammate role was added outside the exact catalog", errors)
        self.assertIn("docs/s5-team-dependencies.md: new teammate dependency was added", errors)

    def test_solo_ownership_lock_rejects_contiguous_teammate_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            historical = root / "docs/s5-contiguous-teammate-dependency.md"
            historical.write_text(
                "teammate B required artifact for S5 entry\n",
                encoding="utf-8",
            )
            self._commit(root, "contiguous teammate dependency")

            errors = verify_solo_ownership_lock(root, base)

        self.assertIn(
            "docs/s5-contiguous-teammate-dependency.md: new teammate dependency was added",
            errors,
        )

    def test_solo_ownership_lock_rejects_camel_case_catalog_role_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            historical = root / "docs/s5-camel-case-dependencies.md"
            historical.write_text(
                "ReturnEngine output is required for S5 entry\n"
                "ExperienceDashboard live blocker\n",
                encoding="utf-8",
            )
            self._commit(root, "camel case teammate dependency")

            errors = verify_solo_ownership_lock(root, base)

        self.assertEqual(
            2,
            errors.count("docs/s5-camel-case-dependencies.md: new teammate dependency was added"),
        )

    def test_solo_ownership_lock_allows_a_new_decision_only_contract_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = self._solo_ownership_fixture(root)
            change = root / "contracts/changes/20260803-decision-only.md"
            change.write_text("# Decision-only contract change\n", encoding="utf-8")
            self._commit(root, "new decision-only contract change")

            self.assertEqual([], verify_solo_ownership_lock(root, base))

    def test_solo_ownership_lock_fails_closed_for_an_unknown_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._solo_ownership_fixture(root)

            errors = verify_solo_ownership_lock(root, "not-a-commit")

        self.assertIn("solo ownership base cannot be resolved", errors)

    def test_historical_documents_remain_classified_as_historical_superseded(self) -> None:
        self.assertEqual("HISTORICAL_SUPERSEDED", classify_markdown("docs/adr/ADR-999-example.md"))
        self.assertEqual(
            "IMMUTABLE_CONTRACT_HISTORY",
            classify_markdown("contracts/changes/20260803-example.md"),
        )

    def test_s4_9_operations_guide_is_current_public_authority(self) -> None:
        self.assertEqual(
            "ACTIVE_PUBLIC_SSOT",
            classify_markdown("docs/S4_9_MCP_Strong_LLM_운영_가이드.md"),
        )

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
