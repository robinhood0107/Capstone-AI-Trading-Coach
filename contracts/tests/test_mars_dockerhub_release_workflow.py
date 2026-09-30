from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FULL_RELEASE = ROOT / ".github/workflows/mars-dockerhub-release.yml"
FULL_CANDIDATE = ROOT / ".github/workflows/mars-product-image-build.yml"
DEMO_SYNC = ROOT / ".github/workflows/mars-demo-sync.yml"
DEMO_CANDIDATE = ROOT / ".github/workflows/mars-demo-candidate-build.yml"
IMPACT = ROOT / ".github/workflows/mars-product-impact.yml"
FULL_GATE = ROOT / "deploy/p1/mars-release-gate.json"
DEMO_GATE = ROOT / "deploy/p1/mars-demo-release.json"


class MarsDockerHubReleaseWorkflowTest(unittest.TestCase):
    def test_full_release_is_limited_to_a_merged_same_repository_develop_pr(self) -> None:
        workflow = FULL_RELEASE.read_text(encoding="utf-8")
        self.assertIn("types: [closed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("github.event.pull_request.merged == true", workflow)
        self.assertIn("github.event.pull_request.head.ref == 'develop'", workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("git rev-parse HEAD^2", workflow)
        self.assertIn('git cat-file -e "$MERGE_SHA^{commit}"', workflow)
        self.assertNotIn("ls-remote origin refs/heads/main", workflow)
        self.assertIn("full_image_required == 'true'", workflow)

    def test_demo_only_change_is_a_full_publish_and_version_noop(self) -> None:
        full = FULL_RELEASE.read_text(encoding="utf-8")
        candidate = FULL_CANDIDATE.read_text(encoding="utf-8")
        self.assertIn("full_publish_noop:", full)
        self.assertIn("full_image_required", full)
        self.assertIn("if: steps.impact.outputs.full_image_required == 'true'", candidate)
        self.assertIn("if: needs.classify.outputs.full_image_required == 'true'", candidate)
        self.assertIn("FULL_PUBLISH_NOOP=true", full)
        self.assertIn("FULL_PUBLISH_NOOP", full)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as paths:
            paths.write("workspaces/mars-demo/tests/runtime-boundaries.test.ts\n")
            paths.flush()
            impact = json.loads(subprocess.check_output([
                sys.executable,
                str(ROOT / "deploy/p1/classify_product_changes.py"),
                "--paths-file",
                paths.name,
            ], text=True))
        self.assertEqual(impact["categories"], ["DEMO-only"])
        self.assertFalse(impact["fullImageRequired"])
        self.assertTrue(impact["fullPublishNoOp"])
        self.assertTrue(impact["demoOnlyFullPublishNoOp"])

    def test_full_release_manifest_artifacts_and_tags_are_full_only(self) -> None:
        workflow = FULL_RELEASE.read_text(encoding="utf-8")
        release_build = workflow.split("  build-scan-push-full:\n", 1)[1].split("  publish-full-release:\n", 1)[0]
        for image in ("mars-full-build:api", "mars-full-build:postgres", "mars-full-build:redis", "mars-full-build:web"):
            self.assertIn(image, release_build)
        self.assertNotIn("repo=pjjpjj111/mars-demo", release_build)
        self.assertNotIn('images[f"demo-', release_build)
        self.assertIn('images[f"full-{part}"]', release_build)
        self.assertIn('assert set(images) == {"full-api", "full-web", "full-postgres", "full-redis"}', release_build)
        release_job = workflow.split("  publish-full-release:\n", 1)[1]
        for asset in ("mars-images.json", "mars-public-full.compose.yml", "mars-web.spdx.json"):
            self.assertIn(asset, release_job)
        self.assertNotIn("mars-public-demo.compose.yml", release_job)
        self.assertNotIn("mars-demo-web.spdx.json", release_job)
        self.assertIn('tag="v${version}"', workflow)
        self.assertIn("MARS_RELEASE_VERSION_NOT_BUMPED", workflow)

    def test_full_release_does_not_depend_on_demo_status_or_artifacts(self) -> None:
        workflow = FULL_RELEASE.read_text(encoding="utf-8")
        self.assertNotIn("needs.demo", workflow)
        self.assertNotIn("needs.mars-demo", workflow)
        self.assertNotIn("mars-demo-images.json", workflow)
        self.assertNotIn("mars-demo-candidate", workflow)
        self.assertIn("independent DEMO sync workflow is not a dependency", workflow)

    def test_main_merge_triggers_demo_evaluation_on_the_same_immutable_sha(self) -> None:
        workflow = DEMO_SYNC.read_text(encoding="utf-8")
        self.assertIn("types: [closed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("MERGE_SHA: ${{ github.event.pull_request.merge_commit_sha }}", workflow)
        self.assertIn('git cat-file -e "$MERGE_SHA^{commit}"', workflow)
        self.assertNotIn("ls-remote origin refs/heads/main", workflow)
        self.assertIn("needs.evaluate.outputs.sha", workflow)
        self.assertIn('"NO_DEMO_DELTA"', workflow)
        self.assertIn('"sourceSha": sys.argv[1]', workflow)
        self.assertIn("candidateCreated", workflow)

    def test_demo_sync_publishes_one_demo_image_without_nas_promotion_or_tag_delete(self) -> None:
        workflow = DEMO_SYNC.read_text(encoding="utf-8")
        candidate = workflow.split("  build-scan-publish:\n", 1)[1]
        for expected in ("pjjpjj111/mars-demo", "demo-web", "mars-demo-web.spdx.json", "mars-public-demo.compose.yml"):
            self.assertIn(expected, candidate)
        self.assertNotIn("pjjpjj111/mars-full", candidate)
        self.assertNotIn("docker compose up", candidate.lower())
        self.assertNotIn("portainer", candidate.lower())
        self.assertNotIn("docker manifest rm", candidate.lower())
        self.assertIn("RegistryPublished", candidate) if "RegistryPublished" in candidate else self.assertIn("REGISTRY_PUBLISHED", candidate)
        self.assertIn("DOCKERHUB_TOKEN", candidate)
        self.assertNotIn("DOCKERHUB_TOKEN", DEMO_CANDIDATE.read_text(encoding="utf-8"))

    def test_product_versions_are_independent_and_full_baseline_is_preserved(self) -> None:
        full = json.loads(FULL_GATE.read_text(encoding="utf-8"))
        demo = json.loads(DEMO_GATE.read_text(encoding="utf-8"))
        self.assertEqual(full["version"], "1.0.15")
        self.assertEqual(demo["version"], "2.0.0")
        self.assertEqual(demo["imageParts"], ["web"])
        self.assertTrue(full["imagePublicationReady"])
        self.assertFalse(demo["serviceReady"])
        self.assertFalse(demo["nasPromotionReady"])

    def test_release_failure_injection_matrix_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "deploy/p1/test_mars_release_failure_isolation.py")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MARS_RELEASE_FAILURE_INJECTION=", result.stdout)

    def test_release_candidate_builds_are_scoped_and_scan_before_registry_credentials(self) -> None:
        full = FULL_CANDIDATE.read_text(encoding="utf-8")
        demo = DEMO_CANDIDATE.read_text(encoding="utf-8")
        self.assertIn("branches: [develop, main]", full)
        self.assertIn("head.repo.full_name == github.repository", full)
        self.assertNotIn("head.ref == 'develop'", full)
        self.assertIn("--file workspaces/experience-dashboard/Dockerfile", full)
        self.assertIn("--build-arg MARS_PRODUCT=full", full)
        self.assertNotIn("Build both product web images", full)
        self.assertIn("demo_candidate_required", demo)
        self.assertIn("--file workspaces/mars-demo/Dockerfile", demo)
        self.assertIn("astral-sh/setup-uv@d31148d669074a8d0a63714ba94f3201e7020bc3", demo)
        demo_sync = DEMO_SYNC.read_text(encoding="utf-8")
        self.assertIn("astral-sh/setup-uv@d31148d669074a8d0a63714ba94f3201e7020bc3", demo_sync)
        self.assertIn("scan-type: image", demo)
        self.assertLess(demo.index("Scan standalone DEMO image"), demo.index("Upload DEMO candidate-only artifacts"))
        self.assertLess(demo.index("Prepare candidate artifact directory"), demo.index("Generate DEMO SBOM"))
        self.assertNotIn("DOCKERHUB_TOKEN", demo)

    def test_product_impact_check_fails_closed_on_unclassified_paths(self) -> None:
        workflow = IMPACT.read_text(encoding="utf-8")
        classifier = (ROOT / "deploy/p1/classify_product_changes.py").read_text(encoding="utf-8")
        self.assertIn("Product Impact Classification", workflow)
        self.assertIn("unclassified changed path(s)", classifier)
        self.assertIn("if: steps.impact.outputs.full_image_required == 'true'", FULL_CANDIDATE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
