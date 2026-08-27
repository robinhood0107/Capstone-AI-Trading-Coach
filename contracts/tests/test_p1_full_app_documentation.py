from __future__ import annotations

import json
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
    "docs/decision-platform/P1_API_USAGE_MATRIX.md",
    "docs/decision-platform/P1_TEAM_A_B_수신_후_통합_체크리스트.md",
    "docs/decision-platform/P1_OWNER_선행_완료_체크리스트.md",
    "docs/decision-platform/P1_GIT_PULL_동일환경_재현_가이드.md",
    "docs/decision-platform/P1_운영_후속_경계.md",
    "docs/decision-platform/P1_최종_테스트_증거_판정표.md",
    "workspaces/return-engine/README.md",
    "workspaces/experience-dashboard/README.md",
)

TEAM_A_CURRENT_OPERATIONS = frozenset(
    {
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/dashboard/backtests/{runId}"),
        ("GET", "/api/v1/dashboard/model-evaluations/{runId}"),
        ("GET", "/api/v1/dashboard/rag-sources/{answerId}"),
        ("GET", "/api/v1/dashboard/risk-results/{decisionId}"),
        ("GET", "/api/v1/decisions/{decisionId}"),
        ("GET", "/api/v1/principle-presets"),
        ("GET", "/api/v1/principles"),
        ("GET", "/api/v1/principles/{principleId}"),
        ("PUT", "/api/v1/principles/{principleId}"),
        ("POST", "/api/v1/rag/ask"),
        ("GET", "/api/v1/rag/sources"),
        ("GET", "/api/v1/risk/portfolio"),
        ("GET", "/api/v1/system/health"),
        ("GET", "/api/v2/signals/{symbol}"),
    }
)
TEAM_A_REQUIRED_OPERATIONS = frozenset(
    {
        ("GET", "/api/v1/brokerage/mock/accounts/{accountId}/balances"),
        ("GET", "/api/v1/brokerage/mock/accounts/{accountId}/buyable"),
        ("GET", "/api/v1/brokerage/mock/accounts/{accountId}/fills"),
        ("POST", "/api/v1/brokerage/mock/orders"),
        ("GET", "/api/v1/brokerage/orders/{orderId}"),
        ("POST", "/api/v1/brokerage/orders/{orderId}/cancel"),
        ("POST", "/api/v1/consents"),
        ("POST", "/api/v1/rag/answers/{answerId}/feedback"),
        ("POST", "/api/v1/principles"),
        ("POST", "/api/v1/decisions/evaluate-order"),
        ("GET", "/api/v1/risk/kill-switch"),
        ("POST", "/api/v1/risk/kill-switch"),
        ("GET", "/api/v1/automation/status"),
        ("POST", "/api/v1/automation/arm"),
        ("POST", "/api/v1/automation/disarm"),
        ("GET", "/api/v1/automation/runs"),
        ("POST", "/api/v1/journals"),
        ("GET", "/api/v1/journals"),
        ("GET", "/api/v2/automation/status"),
        ("PUT", "/api/v2/automation/policy"),
        ("POST", "/api/v2/automation/arm"),
        ("GET", "/api/v2/automation/runs"),
        ("GET", "/api/v2/automation/positions"),
    }
)
OPTIONAL_PRODUCT_OPERATIONS = frozenset(
    {
        ("GET", "/api/v1/brokerage/paper/accounts/{accountId}/balances"),
        ("GET", "/api/v1/brokerage/paper/accounts/{accountId}/buyable"),
        ("GET", "/api/v1/brokerage/paper/accounts/{accountId}/fills"),
        ("POST", "/api/v1/brokerage/paper/orders"),
        ("GET", "/api/v1/rag/history"),
        ("GET", "/api/v1/rag/history/{answerId}"),
        ("DELETE", "/api/v1/rag/history/{answerId}"),
        ("GET", "/api/v1/principles/{principleId}/versions"),
        ("PATCH", "/api/v1/journals/{journalId}"),
        ("DELETE", "/api/v1/journals/{journalId}"),
    }
)
OPERATOR_ONLY_OPERATIONS = frozenset(
    {
        ("GET", "/api/v1/artifacts/ingest-status"),
        ("GET", "/api/v1/async-jobs"),
        ("GET", "/api/v1/async-jobs/{jobId}"),
        ("POST", "/api/v1/brokerage/orders/{orderId}/reconcile"),
        ("GET", "/api/v1/decisions/{decisionId}/audit"),
        ("GET", "/api/v1/stream-metrics"),
    }
)
NON_PRODUCT_OPERATIONS = frozenset(
    (method, "/error")
    for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
)
EXPECTED_API_CLASSIFICATIONS = {
    "현재 화면": TEAM_A_CURRENT_OPERATIONS,
    "Team A 필수": TEAM_A_REQUIRED_OPERATIONS,
    "선택 기능": OPTIONAL_PRODUCT_OPERATIONS,
    "운영자 전용": OPERATOR_ONLY_OPERATIONS,
    "제품 기능 아님": NON_PRODUCT_OPERATIONS,
}

TEAM_B_ARTIFACTS = (
    "model.safetensors",
    "scaler.json",
    "config.json",
    "lstm_signals.parquet",
    "rule_baseline_signals.parquet",
    "backtest_result.json",
    "trade_log.parquet",
    "equity_log.parquet",
    "golden_output.json",
    "model_report.md",
)
TEAM_B_VERIFICATION_OPERATIONS = frozenset(
    {
        ("GET", "/api/v2/signals/{symbol}"),
        ("GET", "/api/v1/dashboard/model-evaluations/{runId}"),
        ("GET", "/api/v1/dashboard/backtests/{runId}"),
        ("GET", "/api/v1/artifacts/ingest-status"),
    }
)


class P1FullAppDocumentationTest(unittest.TestCase):
    def test_exact_post_core_catalog_activates_v2_boundary(self) -> None:
        self.assertTrue(post_core_v2_authorized(ROOT))

    def test_current_v3_authority_block_occurs_once_in_each_ssot(self) -> None:
        for relative in AUTHORITY_DOCUMENTS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(
                1, text.count("<!-- P1_FULL_APP_V3_AUTHORITY_BEGIN -->"), relative
            )
            self.assertEqual(
                1, text.count("<!-- P1_FULL_APP_V3_AUTHORITY_END -->"), relative
            )

    def test_tracked_markdown_is_regular_utf8_and_read_to_eof(self) -> None:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        relatives = tuple(
            item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
        )
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

    def test_workspace_index_only_allows_exact_received_preview_binary_inputs(
        self,
    ) -> None:
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
        relatives = tuple(
            item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
        )
        self.assertFalse(
            [
                relative
                for relative in relatives
                if post_core_v2_workspace_path_is_forbidden(relative)
            ]
        )
        for relative in (
            "workspaces/return-engine/artifacts/005930.KS.json",
            "workspaces/return-engine/data/model/005930.KS_lstm.pth",
            "workspaces/return-engine/data/stock/005930.KS.csv",
        ):
            self.assertFalse(
                post_core_v2_workspace_path_is_forbidden(relative), relative
            )
        for relative in (
            "workspaces/return-engine/artifacts/model.safetensors",
            "workspaces/return-engine/raw/provider.jsonl",
            "workspaces/return-engine/output/result.parquet",
            "workspaces/experience-dashboard/cache/runtime.json",
            "workspaces/experience-dashboard/dev/upstream-intake/source.tsx",
            "workspaces/return-engine/src/untrusted.pkl",
        ):
            self.assertTrue(
                post_core_v2_workspace_path_is_forbidden(relative), relative
            )

    def test_api_usage_matrix_classifies_all_openapi_operations_once(self) -> None:
        openapi = json.loads(
            (ROOT / "contracts/openapi/openapi.json").read_text(encoding="utf-8")
        )
        methods = {"get", "post", "put", "delete", "patch", "head", "options"}
        operations = [
            (method.upper(), path, path_item[method].get("operationId"))
            for path, path_item in openapi["paths"].items()
            for method in path_item
            if method in methods
        ]
        self.assertEqual(61, len(operations))
        operation_ids = [operation_id for _, _, operation_id in operations]
        self.assertTrue(
            all(
                isinstance(operation_id, str) and operation_id
                for operation_id in operation_ids
            )
        )
        self.assertEqual(61, len(set(operation_ids)))
        expected = {(method, path) for method, path, _ in operations}

        matrix = (ROOT / "docs/decision-platform/P1_API_USAGE_MATRIX.md").read_text(
            encoding="utf-8"
        )
        rows = re.findall(
            r"^\|\s*(\d+)\s*\|\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s*"
            r"\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|",
            matrix,
            flags=re.MULTILINE,
        )
        self.assertEqual(61, len(rows))
        self.assertEqual(
            list(range(1, 62)), sorted(int(number) for number, _, _, _ in rows)
        )
        self.assertEqual(61, len({(method, path) for _, method, path, _ in rows}))
        self.assertEqual(expected, {(method, path) for _, method, path, _ in rows})

        observed_by_classification = {
            classification: frozenset(
                (method, path)
                for _, method, path, observed_classification in rows
                if observed_classification == classification
            )
            for classification in EXPECTED_API_CLASSIFICATIONS
        }
        self.assertEqual(
            set(EXPECTED_API_CLASSIFICATIONS),
            {classification for _, _, _, classification in rows},
        )
        self.assertEqual(EXPECTED_API_CLASSIFICATIONS, observed_by_classification)

    def test_team_a_request_uses_catalog_reference_and_exact_five_sections(self) -> None:
        request = (
            ROOT / "docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md"
        ).read_text(encoding="utf-8")
        documented = frozenset(
            re.findall(
                r"`(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/api/[^`]+)`",
                request,
            )
        )
        self.assertEqual(frozenset(), documented)
        self.assertIn("p1-team-a-acceptance.v2.json", request)
        self.assertIn("p1-team-a-client.v2.ts", request)
        self.assertIn("기존 source, component, route, test와 `package-lock.json`", request)
        self.assertEqual(
            [
                "건드리지 않으셔도 되는 것",
                "채워 주셔야 하는 흐름",
                "화면 톤",
                "Automation 화면만 조금 자세히",
                "확인은 이렇게",
                "다 되면 알려 주세요",
                "이건 피해 주세요",
            ],
            re.findall(r"(?m)^## (.+)$", request),
        )
        for phrase in (
            "Figma나 v0",
            "WCAG AA",
            "tabular alignment",
            "glassmorphism",
            "BLOCKED_INCOMPLETE_RISK_BALANCE",
            "automation-policy.spec.ts",
        ):
            self.assertIn(phrase, request)

    def test_team_b_request_lists_exact_artifacts_and_owner_verification_apis(
        self,
    ) -> None:
        request = (
            ROOT / "docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md"
        ).read_text(encoding="utf-8")
        self.assertTrue(all(f"`{artifact}`" in request for artifact in TEAM_B_ARTIFACTS))
        self.assertEqual(
            [
                "그대로 두시는 것",
                "새로 붙여 주셔야 하는 것",
                "1.1.0 Automation과의 경계",
                "확인은 이렇게",
                "다 되면 알려 주세요",
                "이건 피해 주세요",
            ],
            re.findall(r"(?m)^## (.+)$", request),
        )
        self.assertIn("LSTM, rule baseline, 데이터 처리, 백테스트 코드와 preview", request)
        self.assertIn("dev/owner-handoff/<inputManifestSha256>/handoff.json", request)
        self.assertIn("mode `0600` 일반 파일", request)
        self.assertNotIn("p1-return-engine-manifest.v1.json", request)
        self.assertIn("p1-return-engine-manifest.v2.json", request)
        self.assertIn("./capstone artifact validate", request)
        self.assertIn("provider·KIS·ECOS·yfinance·Spring·account·order·Vertex·GDELT", request)

    def test_team_handoff_checklist_has_no_stale_preview_or_api_edge_flow(self) -> None:
        checklist = (
            ROOT / "docs/decision-platform/P1_TEAM_A_B_수신_후_통합_체크리스트.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("./capstone preview", checklist)
        self.assertNotIn("api-edge", checklist)

    def test_current_operations_document_separates_runtime_code_from_activation(self) -> None:
        operations = (
            ROOT / "docs/decision-platform/P1_운영_후속_경계.md"
        ).read_text(encoding="utf-8")
        change = (
            ROOT / "contracts/changes/20260827-p1-kis-mock-automation-runtime-v90.md"
        ).read_text(encoding="utf-8")

        for marker in (
            "AUTOMATION_PERSISTENT_RUNTIME=IMPLEMENTED_INACTIVE",
            "NEXT_SESSION_SCHEDULER=IMPLEMENTED_TRANSIENT_SYSTEMD",
            "VERTEX_BUY_VETO_PRODUCTION_TRANSPORT=ABSTAIN_NOT_CONFIGURED",
            "RECURRING_AUTOMATION=DISABLED",
            "KIS_MOCK_CERTIFICATION=NOT_RUN",
        ):
            self.assertIn(marker, operations)
        self.assertIn("nrcvb_buy_qty", change)
        self.assertIn("root OpenAPI exact-56", change)
        self.assertIn("physical calls are zero", change)

    def test_legacy_v1_authority_files_remain_present(self) -> None:
        self.assertTrue((ROOT / "deploy/p1/release-manifest.schema.json").is_file())
        self.assertTrue(
            (ROOT / ".github/workflows/p1-offline-demo-release.yml").is_file()
        )
        self.assertTrue(
            (
                ROOT / "contracts/changes/20260823-p1-security-container-release.md"
            ).is_file()
        )

    def test_full_app_workflow_is_fail_closed_until_release_jobs_exist(self) -> None:
        workflow = (ROOT / ".github/workflows/p1-full-app-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("P1_FULL_APP_RELEASE=BLOCKED_IMPLEMENTATION_INCOMPLETE", workflow)
        self.assertNotIn("gh release create", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_cross_platform_full_app_entrypoints_expose_the_single_compose_flow(
        self,
    ) -> None:
        linux = (ROOT / "capstone").read_text(encoding="utf-8")
        controller = (ROOT / "deploy/p1/full-appctl").read_text(encoding="utf-8")
        windows = (ROOT / "capstone.ps1").read_text(encoding="utf-8")
        compose = (ROOT / "deploy/p1/compose.yml").read_text(encoding="utf-8")
        for command in (
            "up",
            "down",
            "status",
            "logs",
            "smoke",
            "doctor",
            "mock",
            "artifact",
            "team-a",
        ):
            self.assertIn(command, controller)
            self.assertIn(command, windows)
        self.assertIn("full-appctl", linux)
        self.assertIn("CAPSTONE_PERSISTENT_CONTAINERS", controller)
        self.assertIn("KIS_MOCK_NOT_CERTIFIED", controller)
        self.assertIn("mock_certified", controller)
        self.assertIn("mock_certification_guard.py", controller)
        self.assertIn("--repository-root", controller)
        guard = (ROOT / "deploy/p1/mock_certification_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("KIS_MOCK_CERTIFICATION_DIRTY_WORKTREE", guard)
        self.assertIn("KIS_MOCK_CERTIFICATION_SOURCE_DRIFT", guard)
        self.assertIn('"inputSha256"', guard)
        self.assertIn("P1_KIS_MOCK_ONLINE_ENABLED", controller)
        self.assertIn("artifact_validate", controller)
        self.assertIn("--network none", controller)
        self.assertIn("--validate-only", controller)
        self.assertIn("COMPOSE_FILE=$SCRIPT_DIR/compose.yml", controller)
        self.assertIn("DOCKER_BIN=/usr/bin/docker", controller)
        self.assertNotIn("compose.offline", controller)
        self.assertIn("migrate: {condition: service_completed_successfully}", compose)
        self.assertIn(
            "seed-import: {condition: service_completed_successfully}", compose
        )
        self.assertNotIn('PROVIDER_LIVE_CALLS_ENABLED: "true"', compose)
        self.assertNotIn("openapi.koreainvestment.com:9443", compose)
        self.assertIn("BAAI/bge-m3", compose)
        self.assertIn("PaddlePaddle/PaddleOCR-VL-1.6-GGUF", compose)
        self.assertIn("paddleocr-vl-model-fetch", compose)
        self.assertIn("sha256sum -c -", compose)
        self.assertIn("p1-model-fetch", compose)
        self.assertNotIn("lm-kit", compose)

    def test_rag_history_key_is_text_safe_with_raw_state_compatibility(self) -> None:
        p1ctl = (ROOT / "deploy/p1/p1ctl").read_text(encoding="utf-8")
        entrypoint = (ROOT / "deploy/p1/docker/secret-entrypoint.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("openssl rand -hex 32", p1ctl)
        self.assertIn("rag_kek_size == 32", p1ctl)
        self.assertIn('rag_key_size" -eq 32', entrypoint)
        self.assertIn("od -An -v -tx1", entrypoint)

    def test_full_app_compose_has_health_gated_startup_order(self) -> None:
        compose = (ROOT / "deploy/p1/compose.yml").read_text(encoding="utf-8")
        for service in (
            "postgres",
            "redis",
            "actor-authority",
            "decision-platform",
            "experience-dashboard",
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
        self.assertIn("actor-authority: {condition: service_healthy}", compose)
        self.assertIn("decision-platform: {condition: service_healthy}", compose)
        self.assertIn("return-engine-preview-prepare", compose)
        self.assertIn(
            "compose run --rm", (ROOT / "README.md").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
