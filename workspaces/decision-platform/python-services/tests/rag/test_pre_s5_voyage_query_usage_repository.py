from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.rag.pre_s5_provider_control import PreS5VoyageQueryActivation
from app.rag.pre_s5_voyage_query_usage_repository import (
    PreS5VoyageQueryUsageRepositoryError,
    PsycopgPreS5VoyageQueryUsageRepository,
)


def test_voyage_query_usage_lease_claims_exact_packet_once_without_persisting_query_or_scope_text(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation = _activation()
    repository = PsycopgPreS5VoyageQueryUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )

    lease = repository.reserve(activation=activation)
    lease.claim_attempt(now=datetime.now(UTC))
    lease.commit(expected_input_tokens=3, total_tokens=7, actual_cost_microusd=7)

    with pytest.raises(PreS5VoyageQueryUsageRepositoryError, match="PRE_S5_VOYAGE_QUERY_LEASE_CLAIM_REJECTED"):
        lease.claim_attempt(now=datetime.now(UTC))
    with pytest.raises(PreS5VoyageQueryUsageRepositoryError, match="PRE_S5_VOYAGE_QUERY_LEASE_RESERVATION_REJECTED"):
        repository.reserve(activation=activation)

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        reservation = connection.execute(
            """
            SELECT packet_sha256, nonce_sha256, query_sha256, scope_claim_sha256,
                   rate_evidence_sha256, official_tokenizer_sha256, evaluation_component_scope, provider, operation,
                   token_cap, byte_cap,
                   cost_cap_microusd, input_microusd_per_token
            FROM rag_v2_immutable_voyage_query_usage_reservations
            """
        ).fetchone()
        assert reservation == (
            activation.packet_sha256,
            activation.nonce_sha256,
            activation.query_sha256,
            activation.scope_claim_sha256,
            activation.rate_evidence_sha256,
            activation.tokenizer_sha256,
            "RUNTIME",
            "VOYAGE",
            "CONTEXTUALIZED_QUERY_EMBEDDING",
            8_192,
            1_048_576,
            8_192,
            1,
        )
        assert connection.execute(
            """
            SELECT state, expected_input_tokens, provider_total_tokens, actual_cost_microusd
            FROM rag_v2_immutable_voyage_query_usage_outcomes
            """
        ).fetchone() == ("COMMITTED", 3, 7, 7)
        columns = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'rag_v2_immutable_voyage_query_usage_reservations'
            ORDER BY column_name
            """
        ).fetchall()
        assert ("question",) not in columns
        assert ("scope_claim",) not in columns
        assert ("nonce",) not in columns


def test_voyage_query_usage_lease_keeps_public_evaluation_component_label_without_query_content(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation = replace(_activation(), packet_sha256="d" * 64, nonce_sha256="e" * 64)
    repository = PsycopgPreS5VoyageQueryUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )

    lease = repository.reserve(activation=activation, evaluation_component_scope="OA112")
    lease.claim_attempt(now=datetime.now(UTC))
    lease.commit(expected_input_tokens=3, total_tokens=7, actual_cost_microusd=7)

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT evaluation_component_scope, query_sha256, scope_claim_sha256
            FROM rag_v2_immutable_voyage_query_usage_reservations
            WHERE packet_sha256 = %s
            """,
            (activation.packet_sha256,),
        ).fetchone() == ("OA112", activation.query_sha256, activation.scope_claim_sha256)

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM rag_v2_immutable_voyage_query_usage_reservations")


def test_voyage_query_usage_lease_records_unknown_billing_once_after_claim(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation = replace(_activation(), packet_sha256="e" * 64, nonce_sha256="f" * 64)
    repository = PsycopgPreS5VoyageQueryUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )

    lease = repository.reserve(activation=activation)
    lease.claim_attempt(now=datetime.now(UTC))
    lease.mark_unknown_billing()

    with pytest.raises(PreS5VoyageQueryUsageRepositoryError, match="PRE_S5_VOYAGE_QUERY_LEASE_COMMIT_REJECTED"):
        lease.commit(expected_input_tokens=3, total_tokens=7, actual_cost_microusd=7)
    with pytest.raises(PreS5VoyageQueryUsageRepositoryError, match="PRE_S5_VOYAGE_QUERY_LEASE_UNKNOWN_REJECTED"):
        lease.mark_unknown_billing()

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT state, provider_total_tokens, actual_cost_microusd
            FROM rag_v2_immutable_voyage_query_usage_outcomes
            WHERE packet_sha256 = %s
            """,
            (activation.packet_sha256,),
        ).fetchone() == ("UNKNOWN_BILLING", None, None)


def test_voyage_query_usage_definer_rejects_missing_official_tokenizer_and_preflight_count(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation = _activation()
    expires_at = datetime.now(UTC) + timedelta(minutes=3)
    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.Error) as missing_tokenizer:
            connection.execute(
                """
                SELECT public.reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    "rgr_vqu_" + "f" * 32,
                    "f" * 64,
                    "e" * 64,
                    activation.query_sha256,
                    activation.scope_claim_sha256,
                    activation.rate_evidence_sha256,
                    None,
                    "RUNTIME",
                    expires_at,
                    activation.token_cap,
                    activation.byte_cap,
                    activation.cost_cap_microusd,
                    activation.input_microusd_per_token,
                ),
            )
    assert missing_tokenizer.value.sqlstate == "22023"

    repository = PsycopgPreS5VoyageQueryUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )
    lease = repository.reserve(activation=activation)
    lease.claim_attempt(now=datetime.now(UTC))
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        usage_event_id = connection.execute(
            "SELECT usage_event_id FROM rag_v2_immutable_voyage_query_usage_reservations WHERE packet_sha256 = %s",
            (activation.packet_sha256,),
        ).fetchone()
    assert usage_event_id is not None

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.Error) as missing_preflight_count:
            connection.execute(
                "SELECT public.commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(%s, %s, %s, %s)",
                (usage_event_id[0], None, 1, 1),
            )
    assert missing_preflight_count.value.sqlstate == "22023"

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_voyage_query_usage_outcomes WHERE usage_event_id = %s",
            (usage_event_id[0],),
        ).fetchone() == (0,)


def _activation() -> PreS5VoyageQueryActivation:
    question = "public evidence question"
    scope = "rvs_" + "a" * 32
    return PreS5VoyageQueryActivation(
        packet_sha256="a" * 64,
        nonce_sha256="b" * 64,
        query_sha256=hashlib.sha256(question.encode()).hexdigest(),
        scope_claim_sha256=hashlib.sha256(scope.encode()).hexdigest(),
        rate_evidence_sha256="c" * 64,
        tokenizer_sha256="d" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_QUERY_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=8_192,
        byte_cap=1_048_576,
        cost_cap_microusd=8_192,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )
