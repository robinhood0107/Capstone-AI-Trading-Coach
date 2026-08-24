from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.rag.pre_s5_provider_control import PreS5VoyageActivation
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageBundleComponent,
    build_pre_s5_voyage_full_bundle,
)
from app.rag.pre_s5_voyage_usage_repository import (
    PreS5VoyageUsageRepositoryError,
    PsycopgPreS5VoyageUsageRepository,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)


def test_voyage_usage_lease_claims_packet_once_and_commits_sanitized_usage(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation, bundle = _activation_and_bundle()
    repository = PsycopgPreS5VoyageUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )

    lease = repository.reserve(activation=activation, bundle=bundle)
    lease.claim_attempt(now=datetime.now(UTC))
    lease.commit(expected_input_tokens=142, total_tokens=143, actual_cost_microusd=143)

    with pytest.raises(PreS5VoyageUsageRepositoryError, match="PRE_S5_VOYAGE_LEASE_CLAIM_REJECTED"):
        lease.claim_attempt(now=datetime.now(UTC))
    with pytest.raises(
        PreS5VoyageUsageRepositoryError, match="PRE_S5_VOYAGE_LEASE_RESERVATION_REJECTED"
    ):
        repository.reserve(activation=activation, bundle=bundle)

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        reservation = connection.execute(
            """
            SELECT packet_sha256, nonce_sha256, bundle_manifest_sha256, rate_evidence_sha256,
                   official_tokenizer_sha256,
                   provider, operation, token_cap, byte_cap, cost_cap_microusd,
                   input_microusd_per_token
            FROM rag_v2_immutable_voyage_usage_reservations
            """
        ).fetchone()
        assert reservation == (
            activation.packet_sha256,
            activation.nonce_sha256,
            bundle.manifest_sha256,
            activation.rate_evidence_sha256,
            activation.tokenizer_sha256,
            "VOYAGE",
            "CONTEXTUALIZED_DOCUMENT_EMBEDDING",
            120_000,
            4_194_304,
            200_000,
            1,
        )
        assert connection.execute(
            """
            SELECT state, expected_input_tokens, provider_total_tokens, actual_cost_microusd
            FROM rag_v2_immutable_voyage_usage_outcomes
            """
        ).fetchone() == ("COMMITTED", 142, 143, 143)
        columns = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'rag_v2_immutable_voyage_usage_reservations'
            ORDER BY column_name
            """
        ).fetchall()
        assert ("nonce",) not in columns

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM rag_v2_immutable_voyage_usage_reservations")


def test_voyage_usage_lease_records_unknown_billing_once_after_claim(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation, bundle = _activation_and_bundle()
    alternate = replace(
        activation,
        packet_sha256="e" * 64,
        nonce_sha256="f" * 64,
    )
    repository = PsycopgPreS5VoyageUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )
    lease = repository.reserve(activation=alternate, bundle=bundle)
    lease.claim_attempt(now=datetime.now(UTC))
    lease.mark_unknown_billing()

    with pytest.raises(
        PreS5VoyageUsageRepositoryError, match="PRE_S5_VOYAGE_LEASE_COMMIT_REJECTED"
    ):
        lease.commit(expected_input_tokens=142, total_tokens=143, actual_cost_microusd=143)
    with pytest.raises(
        PreS5VoyageUsageRepositoryError, match="PRE_S5_VOYAGE_LEASE_UNKNOWN_REJECTED"
    ):
        lease.mark_unknown_billing()

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT state, provider_total_tokens, actual_cost_microusd
            FROM rag_v2_immutable_voyage_usage_outcomes
            WHERE packet_sha256 = %s
            """,
            (alternate.packet_sha256,),
        ).fetchone() == ("UNKNOWN_BILLING", None, None)


def test_voyage_usage_definer_rejects_missing_official_tokenizer_and_preflight_count(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    activation, bundle = _activation_and_bundle()
    expires_at = datetime.now(UTC) + timedelta(minutes=3)
    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.Error) as missing_tokenizer:
            connection.execute(
                """
                SELECT public.reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    "rgr_vou_" + "f" * 32,
                    "f" * 64,
                    "e" * 64,
                    bundle.manifest_sha256,
                    activation.rate_evidence_sha256,
                    None,
                    expires_at,
                    activation.token_cap,
                    activation.byte_cap,
                    activation.cost_cap_microusd,
                    activation.input_microusd_per_token,
                ),
            )
    assert missing_tokenizer.value.sqlstate == "22023"

    repository = PsycopgPreS5VoyageUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )
    lease = repository.reserve(activation=activation, bundle=bundle)
    lease.claim_attempt(now=datetime.now(UTC))
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        usage_event_id = connection.execute(
            "SELECT usage_event_id FROM rag_v2_immutable_voyage_usage_reservations WHERE packet_sha256 = %s",
            (activation.packet_sha256,),
        ).fetchone()
    assert usage_event_id is not None

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.Error) as missing_preflight_count:
            connection.execute(
                "SELECT public.commit_rag_v2_immutable_voyage_usage_with_tokenizer(%s, %s, %s, %s)",
                (usage_event_id[0], None, 1, 1),
            )
    assert missing_preflight_count.value.sqlstate == "22023"

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_voyage_usage_outcomes WHERE usage_event_id = %s",
            (usage_event_id[0],),
        ).fetchone() == (0,)


def _activation_and_bundle():
    bundle = build_pre_s5_voyage_full_bundle(
        components=(
            PreS5VoyageBundleComponent(
                component_scope="EXACT30",
                owner_scope_sha256=None,
                groups=_groups(prefix="exact30", count=30),
            ),
            PreS5VoyageBundleComponent(
                component_scope="OA112",
                owner_scope_sha256=None,
                groups=_groups(prefix="oa112", count=112),
            ),
            PreS5VoyageBundleComponent(
                component_scope="OWNER_PRIVATE",
                owner_scope_sha256="d" * 64,
                groups=_groups(prefix="owner_private", count=1),
            ),
        )
    )
    activation = PreS5VoyageActivation(
        packet_sha256="a" * 64,
        nonce_sha256="b" * 64,
        bundle_manifest_sha256=bundle.manifest_sha256,
        rate_evidence_sha256="c" * 64,
        tokenizer_sha256="d" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=120_000,
        byte_cap=4_194_304,
        cost_cap_microusd=200_000,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )
    return activation, bundle


def _groups(*, prefix: str, count: int) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
    groups: list[VoyagePreChunkedDocumentGroup] = []
    for index in range(count):
        text = f"{prefix} test bundle chunk {index:03d}"
        digest = _sha256(f"{prefix}|{index}")
        groups.append(
            VoyagePreChunkedDocumentGroup(
                source_id=f"src_{prefix}_{index:03d}",
                source_revision_id=f"srv_{prefix}_{index:03d}",
                context_set_hash=_sha256(f"context|{prefix}|{index}"),
                chunks=(
                    VoyagePreChunkedChunk(
                        chunk_id=f"rag_v2_chk_{digest[:32]}",
                        canonical_text=text,
                        canonical_text_sha256=_sha256(text),
                        embedding_input_hash=_sha256(f"input|{prefix}|{index}"),
                        token_count=1,
                    ),
                ),
            )
        )
    return tuple(groups)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
