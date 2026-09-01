from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class P1AutomationV3LiveReadinessTest(unittest.TestCase):
    def test_market_bootstrap_run_is_packet_bound_and_uses_a_market_only_secret(
        self,
    ) -> None:
        control = (ROOT / "deploy/p1/full-appctl").read_text(encoding="utf-8")
        compose = (ROOT / "deploy/p1/compose.yml").read_text(encoding="utf-8")
        entrypoint = (ROOT / "deploy/p1/docker/secret-entrypoint.sh").read_text(
            encoding="utf-8"
        )
        p1ctl = (ROOT / "deploy/p1/p1ctl").read_text(encoding="utf-8")

        self.assertIn("market_data_bootstrap_run", control)
        self.assertIn("market_data_universe_refresh", control)
        self.assertIn("--approval-packet", control)
        self.assertIn("P1_AUTOMATION_MARKET_BOOTSTRAP_ENABLED=true", control)
        self.assertIn("enable_strong_llm_by_default", control)
        self.assertIn("P1_GOOGLE_BILLING_ACCOUNT_FINGERPRINT", control)
        self.assertIn("market_data_provider_env", compose)
        self.assertIn("RAG_WEB_GOOGLE_BILLING_ACCOUNT_FINGERPRINT", compose)
        self.assertIn("market-data-provider.env", p1ctl)
        market_profile = next(
            line
            for line in entrypoint.splitlines()
            if line.strip().startswith("market-data) secret_files=")
        )
        self.assertIn("market_data_provider_env", market_profile)
        allowed = next(
            line
            for line in entrypoint.splitlines()
            if "market-data:MARKET_DATA_WRITER_DSN" in line
        )
        self.assertIn("KIS_MOCK_APP_KEY", allowed)
        self.assertIn("KIS_MOCK_APP_SECRET", allowed)
        self.assertIn("KRX_OPENAPI_AUTH_KEY", allowed)
        self.assertNotIn("KIS_MOCK_ACCOUNT_NO", allowed)
        self.assertNotIn("KIS_MOCK_ORDER_REFERENCE_KEY", allowed)
        self.assertIn("/tmp/strong-llm/vertex-service-account.json", entrypoint)

    def test_real_automation_evidence_provider_is_distinct_from_fixture_transport(
        self,
    ) -> None:
        source = (
            ROOT
            / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/grpc/GrpcAutomationEvidenceProvider.kt"
        ).read_text(encoding="utf-8")

        self.assertIn(": AutomationEvidenceProvider", source)
        self.assertIn(".setGroundingDiscoveryOnly(true)", source)
        self.assertIn('execute(runId, "SCREEN", start, 1', source)
        self.assertIn('execute(runId, "JUDGE", start, 2', source)
        self.assertNotIn("KIS_MOCK_ACCOUNT_NO", source)
        self.assertNotIn("KIS_MOCK_ORDER_REFERENCE_KEY", source)
        generic = (
            ROOT
            / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/grpc/GrpcStrongLlmGenerationAdapter.kt"
        ).read_text(encoding="utf-8")
        self.assertIn("ObjectProvider<ResearchToolFacade>", generic)
        self.assertIn("S4_9_SEARCH_DISABLED", generic)
        self.assertIn("command.evidence.any { it.ownerPrivate }", generic)
        self.assertIn(
            "command.consent.policyDigest == strongLlmProperties.ownerConsentPolicySha256",
            generic,
        )

    def test_internal_agent_carries_thinking_snapshot_and_discovery_only_bit(
        self,
    ) -> None:
        proto = (ROOT / "contracts/internal/proto/strong_llm_agent.proto").read_text(
            encoding="utf-8"
        )

        self.assertIn("string thinking_level = 15;", proto)
        self.assertIn("bool grounding_discovery_only = 16;", proto)

    def test_vertex_service_account_readiness_does_not_require_an_owner_api_key(
        self,
    ) -> None:
        repository = (
            ROOT
            / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/automation/JdbcAutomationRepository.kt"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'aiSettings.provider == "vertex" || primaryCredentialReady', repository
        )

    def test_after_hours_brokerage_e2e_is_provider_free_and_restores_runtime_mode(
        self,
    ) -> None:
        runner = (
            ROOT
            / "workspaces/decision-platform/python-services/tests/e2e/brokerage_e2e.py"
        ).read_text(encoding="utf-8")

        self.assertIn("offline_brokerage=True", runner)
        self.assertIn("_start_offline_brokerage(", runner)
        self.assertIn("_stop_offline_brokerage()", runner)
        self.assertIn('_compose("up", "-d", "--no-deps", "decision-platform")', runner)
        fixture = (
            ROOT
            / "workspaces/decision-platform/python-services/tests/e2e/offline_brokerage.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class OfflineReferenceStore", fixture)
        self.assertIn("if path == ORDER_CANCEL_PATH:", fixture)
        self.assertIn("reference_store=reference_store", fixture)

    def test_v114_removes_private_checkpoint_acl_dependency_from_owner_run_reads(
        self,
    ) -> None:
        migration = (
            ROOT
            / "workspaces/decision-platform/spring-api/src/main/resources/db/migration/V114__p1_automation_v3_owner_read_scope.sql"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "CREATE FUNCTION public.p1_automation_ai_judgement_runtime_scope_v1",
            migration,
        )
        self.assertIn("DROP POLICY automation_ai_judgements_scope_v106", migration)
        self.assertIn("CREATE POLICY automation_ai_judgements_runtime_v114", migration)
        self.assertNotIn(
            "GRANT SELECT ON public.automation_runtime_checkpoint", migration
        )
        role_bootstrap = (ROOT / "infra/init/02-application-roles.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "public.p1_automation_ai_judgement_runtime_scope_v1(text)", role_bootstrap
        )
        self.assertIn("TO decision_app, decision_automation_runtime;", role_bootstrap)

    def test_v115_allows_first_v3_policy_after_legacy_history_with_expected_zero(
        self,
    ) -> None:
        migration = (
            ROOT
            / "workspaces/decision-platform/spring-api/src/main/resources/db/migration/V115__p1_automation_v3_policy_upgrade_cas.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("current_v3_version IS NULL AND p_expected_version<>0", migration)
        self.assertIn(
            "WHEN current_v3_version IS NULL THEN COALESCE(latest_historical_version,0)",
            migration,
        )
        self.assertIn(
            "effective_expected_version,p_scope_hash,p_request_hash", migration
        )
