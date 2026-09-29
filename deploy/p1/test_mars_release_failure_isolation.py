#!/usr/bin/env python3
"""Failure-injection contract for independently released MARS products."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL = (ROOT / ".github/workflows/mars-dockerhub-release.yml").read_text(encoding="utf-8")
DEMO = (ROOT / ".github/workflows/mars-demo-sync.yml").read_text(encoding="utf-8")
DEMO_BUILD = (ROOT / ".github/workflows/mars-demo-candidate-build.yml").read_text(encoding="utf-8")


def simulate(full_required: bool, demo_candidate: bool, full_status: str, demo_status: str) -> dict[str, object]:
    full_result = "NOOP" if not full_required else "PUBLISHED" if full_status == "success" else "FAILED"
    demo_result = "NO_DELTA" if not demo_candidate else "PUBLISHED" if demo_status == "success" else "FAILED"
    # Workflow releases never promote a Portainer stack. Registry candidate state cannot change
    # the digest recorded as active in the NAS stack.
    return {
        "fullRelease": full_result,
        "demoRelease": demo_result,
        "activeDemoDigest": "sha256:existing-demo-digest",
        "activeDemoStackRevision": "existing-demo-stack-revision",
    }


def test_workflows_have_no_cross_product_needs_edges() -> None:
    assert "needs: [classify-full-merge, build-scan-push-full]" in FULL
    assert "mars-demo-sync" not in FULL
    assert "mars-demo-candidate" not in FULL
    assert "needs: evaluate" in DEMO
    assert "needs.classify-full" not in DEMO
    assert "needs.build-scan-push-full" not in DEMO
    assert "pull_request:" in FULL and "merge_commit_sha" in FULL
    assert "pull_request:" in DEMO and "merge_commit_sha" in DEMO


def test_demo_failures_do_not_change_a_successful_full_release() -> None:
    for stage in ("typecheck", "build", "contract", "scan", "publish", "manifest"):
        outcome = simulate(True, True, "success", f"{stage}-failed")
        assert outcome["fullRelease"] == "PUBLISHED", stage
        assert outcome["demoRelease"] == "FAILED", stage
        assert outcome["activeDemoDigest"] == "sha256:existing-demo-digest", stage
        assert outcome["activeDemoStackRevision"] == "existing-demo-stack-revision", stage


def test_full_failures_leave_active_demo_digest_and_stack_unchanged() -> None:
    for stage in ("typecheck", "build", "scan", "publish", "release"):
        outcome = simulate(True, True, f"{stage}-failed", "success")
        assert outcome["fullRelease"] == "FAILED", stage
        assert outcome["activeDemoDigest"] == "sha256:existing-demo-digest", stage
        assert outcome["activeDemoStackRevision"] == "existing-demo-stack-revision", stage


def test_demo_only_merge_is_a_full_publish_and_version_noop() -> None:
    outcome = simulate(False, True, "success", "success")
    assert outcome["fullRelease"] == "NOOP"
    assert outcome["demoRelease"] == "PUBLISHED"
    assert "pjjpjj111/mars-full" not in DEMO
    assert "MARS_RELEASE_VERSION_NOT_BUMPED" not in DEMO


def test_full_only_sync_writes_no_demo_delta_and_shared_ui_tracks_same_sha() -> None:
    assert '"NO_DEMO_DELTA"' in DEMO
    assert '"sourceSha": sys.argv[1]' in DEMO
    assert "needs.evaluate.outputs.sha" in DEMO
    assert "needs.evaluate.outputs.demo_candidate_required == 'true'" in DEMO
    assert 'Rule("workspaces/experience-dashboard/src/shared/ui/Panel.tsx", "shared-ui/contract"' in (
        ROOT / "deploy/p1/classify_product_changes.py"
    ).read_text(encoding="utf-8")


def test_candidate_and_release_manifests_are_product_only_and_no_nas_promotion() -> None:
    assert '"demo-web"' in DEMO
    assert "mars-demo-web.spdx.json" in DEMO
    assert "mars-api.spdx.json" not in DEMO
    assert "MARS_DEMO_IMAGE_DIGEST" in (ROOT / "deploy/p1/portainer/mars-demo.stack.yml").read_text(encoding="utf-8")
    assert "docker compose up" not in DEMO.lower()
    assert "portainer" not in DEMO.lower()
    assert "docker push" in DEMO
    assert "docker rmi" not in DEMO.lower()
    assert "tag delete" not in DEMO.lower()
    assert "DOCKERHUB_TOKEN" not in DEMO_BUILD


def test_demo_publish_failure_does_not_delete_old_registry_tags() -> None:
    assert "curl" in DEMO
    assert "docker push" in DEMO
    assert "docker manifest rm" not in DEMO
    assert "DELETE FROM" not in DEMO
    assert "DELETE" not in DEMO.upper().replace("DELETE FROM", "")


def main() -> int:
    tests = (
        test_workflows_have_no_cross_product_needs_edges,
        test_demo_failures_do_not_change_a_successful_full_release,
        test_full_failures_leave_active_demo_digest_and_stack_unchanged,
        test_demo_only_merge_is_a_full_publish_and_version_noop,
        test_full_only_sync_writes_no_demo_delta_and_shared_ui_tracks_same_sha,
        test_candidate_and_release_manifests_are_product_only_and_no_nas_promotion,
        test_demo_publish_failure_does_not_delete_old_registry_tags,
    )
    results = []
    for test in tests:
        test()
        results.append({"case": test.__name__, "result": "PASS"})
    print("MARS_RELEASE_FAILURE_INJECTION=" + json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
