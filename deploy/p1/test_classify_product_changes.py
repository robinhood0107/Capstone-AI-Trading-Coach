#!/usr/bin/env python3
"""Executable scope and no-op cases for product-impact path classification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = Path(__file__).with_name("classify_product_changes.py")
SPEC = importlib.util.spec_from_file_location("classify_product_changes", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_demo_only_never_requires_full_image_or_full_version_bump() -> None:
    result = MODULE.classify_paths(["workspaces/mars-demo/src/app/page.tsx"])
    assert result["categories"] == ["DEMO-only"]
    assert result["demoCandidateRequired"] is True
    assert result["fullImageRequired"] is False
    assert result["fullPublishNoOp"] is True
    assert result["demoOnlyFullPublishNoOp"] is True


def test_full_only_starts_an_independent_demo_sync_evaluation() -> None:
    result = MODULE.classify_paths(["workspaces/decision-platform/spring-api/src/Main.kt"])
    assert result["categories"] == ["FULL-only"]
    assert result["fullImageRequired"] is True
    assert result["demoCandidateRequired"] is False
    assert result["demoSyncEvaluationRequired"] is True


def test_shared_ui_builds_each_product_independently() -> None:
    result = MODULE.classify_paths(["workspaces/experience-dashboard/src/shared/ui/Panel.tsx"])
    assert result["categories"] == ["shared-ui/contract"]
    assert result["fullImageRequired"] is True
    assert result["demoCandidateRequired"] is True


def test_reused_full_shell_pages_and_features_trigger_demo_candidate() -> None:
    result = MODULE.classify_paths([
        "workspaces/experience-dashboard/src/shared/ui/AppShell.tsx",
        "workspaces/experience-dashboard/src/app/page.tsx",
        "workspaces/experience-dashboard/src/features/admin/AdminConsole.tsx",
        "workspaces/experience-dashboard/src/features/system/SettingsPageContent.tsx",
    ])
    assert result["categories"] == ["shared-ui/contract"]
    assert result["fullImageRequired"] is True
    assert result["demoCandidateRequired"] is True
    assert result["demoSyncEvaluationRequired"] is True


def test_full_auth_and_brokerage_settings_remain_full_only() -> None:
    result = MODULE.classify_paths([
        "workspaces/experience-dashboard/src/shared/ui/LoginCard.tsx",
        "workspaces/experience-dashboard/src/features/brokerage/MockCredentialView.tsx",
        "workspaces/experience-dashboard/src/features/strong-llm/OwnerVertexCredentialView.tsx",
        "workspaces/experience-dashboard/src/app/admin/page.tsx",
        "workspaces/experience-dashboard/src/app/settings/page.tsx",
        "workspaces/experience-dashboard/src/middleware.ts",
    ])
    assert result["categories"] == ["FULL-only"]
    assert result["fullImageRequired"] is True
    assert result["demoCandidateRequired"] is False
    assert result["demoSyncEvaluationRequired"] is True


def test_workflow_contract_tests_are_deployment_only() -> None:
    result = MODULE.classify_paths(["contracts/tests/test_mars_release_compose.py"])
    assert result["categories"] == ["deployment-only"]
    assert result["fullPublishNoOp"] is True
    assert result["demoCandidateRequired"] is False


def test_deployment_only_does_not_publish_product_images() -> None:
    result = MODULE.classify_paths(["deploy/p1/compose.public-demo.yml"])
    assert result["categories"] == ["deployment-only"]
    assert result["fullPublishNoOp"] is True
    assert result["demoCandidateRequired"] is False


def test_docker_ignore_changes_gate_both_image_contexts() -> None:
    result = MODULE.classify_paths([".dockerignore"])
    assert result["categories"] == ["shared-ui/contract"]
    assert result["fullImageRequired"] is True
    assert result["demoCandidateRequired"] is True


def test_mixed_categories_publish_only_the_affected_products() -> None:
    result = MODULE.classify_paths([
        "workspaces/mars-demo/src/server/overlay.ts",
        "workspaces/decision-platform/spring-api/src/Main.kt",
    ])
    assert result["categories"] == ["DEMO-only", "FULL-only"]
    assert result["fullImageRequired"] is True
    assert result["demoCandidateRequired"] is True
    assert result["demoOnlyFullPublishNoOp"] is False


def test_unknown_path_fails_before_any_publish_decision() -> None:
    try:
        MODULE.classify_paths(["mystery/product/file.txt"])
    except ValueError as error:
        assert "unclassified changed path" in str(error)
    else:
        raise AssertionError("an unclassified path must fail closed")


if __name__ == "__main__":
    test_demo_only_never_requires_full_image_or_full_version_bump()
    test_full_only_starts_an_independent_demo_sync_evaluation()
    test_shared_ui_builds_each_product_independently()
    test_reused_full_shell_pages_and_features_trigger_demo_candidate()
    test_full_auth_and_brokerage_settings_remain_full_only()
    test_workflow_contract_tests_are_deployment_only()
    test_deployment_only_does_not_publish_product_images()
    test_docker_ignore_changes_gate_both_image_contexts()
    test_mixed_categories_publish_only_the_affected_products()
    test_unknown_path_fails_before_any_publish_decision()
    print("PRODUCT_IMPACT_CLASSIFIER_TESTS=PASS")
