from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_market_bootstrap_run_is_packet_bound_and_uses_a_market_only_secret() -> None:
    control = (ROOT / "deploy/p1/full-appctl").read_text(encoding="utf-8")
    compose = (ROOT / "deploy/p1/compose.yml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "deploy/p1/docker/secret-entrypoint.sh").read_text(encoding="utf-8")
    p1ctl = (ROOT / "deploy/p1/p1ctl").read_text(encoding="utf-8")

    assert "market_data_bootstrap_run" in control
    assert "market_data_universe_refresh" in control
    assert "--approval-packet" in control
    assert "P1_AUTOMATION_MARKET_BOOTSTRAP_ENABLED=true" in control
    assert "enable_strong_llm_by_default" in control
    assert "P1_GOOGLE_BILLING_ACCOUNT_FINGERPRINT" in control
    assert "market_data_provider_env" in compose
    assert "RAG_WEB_GOOGLE_BILLING_ACCOUNT_FINGERPRINT" in compose
    assert "market-data-provider.env" in p1ctl
    market_profile = next(
        line for line in entrypoint.splitlines() if line.strip().startswith("market-data) secret_files=")
    )
    assert "market_data_provider_env" in market_profile
    allowed = next(
        line for line in entrypoint.splitlines() if "market-data:MARKET_DATA_WRITER_DSN" in line
    )
    assert "KIS_MOCK_APP_KEY" in allowed and "KIS_MOCK_APP_SECRET" in allowed
    assert "KRX_OPENAPI_AUTH_KEY" in allowed
    assert "KIS_MOCK_ACCOUNT_NO" not in allowed
    assert "KIS_MOCK_ORDER_REFERENCE_KEY" not in allowed
    assert "/tmp/strong-llm/vertex-service-account.json" in entrypoint


def test_real_automation_evidence_provider_is_distinct_from_fixture_transport() -> None:
    source = (
        ROOT
        / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/grpc/GrpcAutomationEvidenceProvider.kt"
    ).read_text(encoding="utf-8")

    assert ": AutomationEvidenceProvider" in source
    assert '.setGroundingDiscoveryOnly(true)' in source
    assert 'execute(runId, "SCREEN", start, 1' in source
    assert 'execute(runId, "JUDGE", start, 2' in source
    assert "KIS_MOCK_ACCOUNT_NO" not in source
    assert "KIS_MOCK_ORDER_REFERENCE_KEY" not in source
    generic = (
        ROOT
        / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/grpc/GrpcStrongLlmGenerationAdapter.kt"
    ).read_text(encoding="utf-8")
    assert "ObjectProvider<ResearchToolFacade>" in generic
    assert "S4_9_SEARCH_DISABLED" in generic
    assert "command.evidence.any { it.ownerPrivate }" in generic
    assert "command.consent.policyDigest == strongLlmProperties.ownerConsentPolicySha256" in generic


def test_internal_agent_carries_thinking_snapshot_and_discovery_only_bit() -> None:
    proto = (ROOT / "contracts/internal/proto/strong_llm_agent.proto").read_text(encoding="utf-8")

    assert "string thinking_level = 15;" in proto
    assert "bool grounding_discovery_only = 16;" in proto


def test_vertex_service_account_readiness_does_not_require_an_owner_api_key() -> None:
    repository = (
        ROOT
        / "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision/infrastructure/automation/JdbcAutomationRepository.kt"
    ).read_text(encoding="utf-8")

    assert 'aiSettings.provider == "vertex" || primaryCredentialReady' in repository
