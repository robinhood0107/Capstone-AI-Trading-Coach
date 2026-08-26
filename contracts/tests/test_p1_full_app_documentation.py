from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

from contracts.verify_pre_s5_doc_truth_freeze import (
    markdown_link_errors,
    post_core_v2_authorized,
    post_core_v2_workspace_path_is_forbidden,
    verify_public_truth_freeze,
)


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_DOCUMENTS = (
    "README.md",
    "docs/README.md",
    "docs/최종_프로젝트_명세서.md",
    "docs/API_명세서.md",
)
FULL_APP_DOCUMENTS = (
    "docs/decision-platform/P1_1_0_0_FULL_APP_V2_권위_및_게이트.md",
    "docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md",
    "docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md",
    "docs/decision-platform/P1_OWNER_선행_완료_체크리스트.md",
    "docs/decision-platform/P1_GIT_PULL_동일환경_재현_가이드.md",
    "docs/decision-platform/P1_운영_후속_경계.md",
    "docs/decision-platform/P1_최종_테스트_증거_판정표.md",
    "workspaces/return-engine/README.md",
    "workspaces/experience-dashboard/README.md",
)


class P1FullAppDocumentationTest(unittest.TestCase):
    def test_exact_post_core_catalog_activates_v2_boundary(self) -> None:
        self.assertTrue(post_core_v2_authorized(ROOT))

    def test_current_authority_block_occurs_once_in_each_ssot(self) -> None:
        for relative in AUTHORITY_DOCUMENTS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(1, text.count("<!-- P1_FULL_APP_V2_AUTHORITY_BEGIN -->"), relative)
            self.assertEqual(1, text.count("<!-- P1_FULL_APP_V2_AUTHORITY_END -->"), relative)

    def test_tracked_markdown_is_regular_utf8_and_read_to_eof(self) -> None:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        relatives = tuple(item.decode("utf-8") for item in completed.stdout.split(b"\0") if item)
        self.assertGreater(len(relatives), 100)
        for relative in relatives:
            path = ROOT / relative
            path.lstat()
            self.assertFalse(path.is_symlink(), relative)
            self.assertTrue(os.path.isfile(path), relative)
            payload = path.read_bytes()
            payload.decode("utf-8")
            self.assertTrue(payload.endswith(b"\n"), relative)

    def test_full_app_document_links_and_truth_markers_do_not_drift(self) -> None:
        for relative in FULL_APP_DOCUMENTS:
            self.assertEqual([], markdown_link_errors(ROOT, relative), relative)
        self.assertEqual([], verify_public_truth_freeze(ROOT))

    def test_workspace_index_never_contains_intake_cache_raw_csv_or_pickle(self) -> None:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "ls-files",
                "-z",
                "--",
                "workspaces/return-engine",
                "workspaces/experience-dashboard",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        relatives = tuple(item.decode("utf-8") for item in completed.stdout.split(b"\0") if item)
        self.assertFalse(
            [relative for relative in relatives if post_core_v2_workspace_path_is_forbidden(relative)]
        )
        for relative in (
            "workspaces/return-engine/artifacts/model.safetensors",
            "workspaces/return-engine/raw/provider.jsonl",
            "workspaces/return-engine/output/result.parquet",
            "workspaces/experience-dashboard/cache/runtime.json",
            "workspaces/experience-dashboard/dev/upstream-intake/source.tsx",
            "workspaces/return-engine/src/untrusted.pkl",
        ):
            self.assertTrue(post_core_v2_workspace_path_is_forbidden(relative), relative)

    def test_legacy_v1_authority_files_remain_present(self) -> None:
        self.assertTrue((ROOT / "deploy/p1/release-manifest.schema.json").is_file())
        self.assertTrue((ROOT / ".github/workflows/p1-offline-demo-release.yml").is_file())
        self.assertTrue((ROOT / "contracts/changes/20260823-p1-security-container-release.md").is_file())

    def test_full_app_workflow_is_fail_closed_until_release_jobs_exist(self) -> None:
        workflow = (ROOT / ".github/workflows/p1-full-app-release.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("P1_FULL_APP_RELEASE=BLOCKED_IMPLEMENTATION_INCOMPLETE", workflow)
        self.assertNotIn("gh release create", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_cross_platform_full_app_entrypoints_are_fail_closed(self) -> None:
        linux = (ROOT / "capstone").read_text(encoding="utf-8")
        controller = (ROOT / "deploy/p1/full-appctl").read_text(encoding="utf-8")
        windows = (ROOT / "capstone.ps1").read_text(encoding="utf-8")
        compose = (ROOT / "deploy/p1/compose.yml").read_text(encoding="utf-8")
        for command in ("install", "start", "stop", "status", "doctor", "backup", "restore", "verify"):
            self.assertIn(command, controller)
            self.assertIn(command, windows)
        self.assertIn("full-appctl", linux)
        self.assertIn("FULL_INSTALL_BLOCKED_REQUIRED_ARTIFACTS", controller)
        self.assertIn("verify_p1_full_app_assets.py", controller)
        self.assertIn("install_model_runtimes", controller)
        self.assertIn("STATIC_ASSET_INTEGRITY_PASS", controller)
        self.assertIn("CAPSTONE_RELEASE_AUTHORITY=NONE", controller)
        self.assertIn("selected_project_name", controller)
        self.assertIn("state_init_resume_inventory", (ROOT / "deploy/p1/p1ctl").read_text(encoding="utf-8"))
        self.assertIn("BLOCKED_G7_ATOMIC_RESTORE_NOT_IMPLEMENTED", controller)
        self.assertIn('FULL_COMPOSE=$SCRIPT_DIR/compose.yml', controller)
        self.assertIn("DOCKER_BIN=/usr/bin/docker", controller)
        self.assertNotIn("FULL_SEED_COMPOSE", controller)
        self.assertNotIn("FULL_MODEL_COMPOSE", controller)
        self.assertIn("migrate: {condition: service_completed_successfully}", compose)
        self.assertIn("seed-import: {condition: service_completed_successfully}", compose)
        self.assertNotIn('PROVIDER_LIVE_CALLS_ENABLED: "true"', compose)
        self.assertIn("BAAI/bge-m3", compose)
        self.assertIn("PaddlePaddle/PaddleOCR-VL-1.6-GGUF", compose)
        self.assertIn("paddleocr-vl-model-fetch", compose)
        self.assertIn("sha256sum -c -", compose)
        self.assertIn("p1-model-fetch", compose)
        self.assertNotIn("lm-kit", compose)

    def test_full_app_compose_has_health_gated_startup_order(self) -> None:
        compose = (ROOT / "deploy/p1/compose.yml").read_text(encoding="utf-8")
        for service in (
            "runtime-netns",
            "api-edge",
            "postgres",
            "redis",
            "actor-capability-authority",
            "python-worker",
            "spring-api",
            "bge-m3",
            "paddleocr-vl",
        ):
            match = re.search(
                rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [A-Za-z0-9-]+:\n|\Z)",
                compose,
            )
            self.assertIsNotNone(match, service)
            block = match.group(1)
            self.assertIn("healthcheck:", block, service)
        self.assertIn("spring-api: {condition: service_healthy}", compose)
        self.assertIn("actor-capability-authority: {condition: service_healthy}", compose)
        self.assertIn("python-worker: {condition: service_healthy}", compose)
        self.assertIn("bge-m3: {condition: service_healthy}", compose)
        self.assertIn("paddleocr-vl: {condition: service_healthy}", compose)


if __name__ == "__main__":
    unittest.main()
