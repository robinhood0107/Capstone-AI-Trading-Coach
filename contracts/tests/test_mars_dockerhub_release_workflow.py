from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/mars-dockerhub-release.yml"
IMAGE_WORKFLOW = ROOT / ".github/workflows/mars-product-image-build.yml"
GATE = ROOT / "deploy/p1/mars-release-gate.json"


class MarsDockerHubReleaseWorkflowTest(unittest.TestCase):
    def test_release_is_limited_to_a_merged_same_repository_develop_pr(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("types: [closed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("github.event.pull_request.merged == true", workflow)
        self.assertIn("github.event.pull_request.head.ref == 'develop'", workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("git rev-parse HEAD^2", workflow)
        self.assertIn("refs/heads/main", workflow)

    def test_image_publication_is_separate_from_actual_service_readiness(self) -> None:
        gate = json.loads(GATE.read_text(encoding="utf-8"))
        self.assertEqual(set(gate), {"imagePublicationReady", "serviceReady", "version"})
        self.assertIs(type(gate["imagePublicationReady"]), bool)
        self.assertIs(type(gate["serviceReady"]), bool)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".imagePublicationReady", workflow)
        self.assertIn(".serviceReady", workflow)
        self.assertNotIn(".ready'", workflow)
        self.assertIn("No NAS deployment or N-user capacity test is included.", workflow)
        image_workflow = IMAGE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".imagePublicationReady", image_workflow)
        self.assertIn("jq -r '.version'", image_workflow)
        self.assertNotIn("RELEASE_VERSION=0.1.0-candidate", image_workflow)

    def test_each_promotion_is_one_new_semver_release_with_a_changelog_entry(self) -> None:
        gate = json.loads(GATE.read_text(encoding="utf-8"))
        self.assertRegex(gate["version"], r"^[0-9]+\.[0-9]+\.[0-9]+$")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"(?m)^## \[{gate['version']}\] - \d{{4}}-\d{{2}}-\d{{2}}$")
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('tag="v${version}"', workflow)
        self.assertNotIn("MERGE_SHA:0:12", workflow)
        self.assertIn("MARS_RELEASE_VERSION_NOT_BUMPED", workflow)
        image_workflow = IMAGE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('gh release view "v${version}"', image_workflow)
        self.assertIn("CHANGELOG.md", image_workflow)

    def test_only_release_publishing_job_has_github_contents_write(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        build_job = workflow.split("  build-scan-push:\n", 1)[1].split("  publish-release:\n", 1)[0]
        release_job = workflow.split("  publish-release:\n", 1)[1]
        self.assertIn("permissions: {}", workflow)
        self.assertIn("contents: read", build_job)
        self.assertNotIn("contents: write", build_job)
        self.assertIn("contents: write", release_job)
        self.assertLess(build_job.index("Scan full web image"), build_job.index("Log in to Docker Hub"))
        self.assertIn("secrets.DOCKERHUB_TOKEN", build_job)
        self.assertNotIn("secrets.DOCKERHUB_TOKEN", release_job)

    def test_release_contains_digest_manifest_pinned_compose_and_sboms(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for artifact in (
            "mars-images.json",
            "mars-public-demo.compose.yml",
            "mars-public-full.compose.yml",
            "mars-api.spdx.json",
            "mars-postgres.spdx.json",
            "mars-redis.spdx.json",
            "mars-demo-web.spdx.json",
            "mars-full-web.spdx.json",
        ):
            self.assertIn(artifact, workflow)
        self.assertIn("@sha256:", workflow)
        self.assertIn("docker buildx imagetools inspect", workflow)
        self.assertIn("org.opencontainers.image.revision", workflow)
        self.assertIn("cmp \"release-assets/$asset\" \"release-readback/$asset\"", workflow)

    def test_candidate_build_scans_all_images_before_artifact_without_registry_credentials(self) -> None:
        image_workflow = IMAGE_WORKFLOW.read_text(encoding="utf-8")
        for image in (
            "mars-candidate:api",
            "mars-candidate:postgres",
            "mars-candidate:redis",
            "mars-candidate:demo-web",
            "mars-candidate:full-web",
        ):
            self.assertIn(f"image-ref: {image}", image_workflow)
        self.assertEqual(image_workflow.count("severity: HIGH,CRITICAL"), 5)
        self.assertIn("scanners: vuln,secret", image_workflow)
        self.assertIn("Upload candidate image identities", image_workflow)
        self.assertLess(
            image_workflow.index("Scan full web candidate image"),
            image_workflow.index("Upload candidate image identities"),
        )
        self.assertNotIn("DOCKERHUB_TOKEN", image_workflow)


if __name__ == "__main__":
    unittest.main()
