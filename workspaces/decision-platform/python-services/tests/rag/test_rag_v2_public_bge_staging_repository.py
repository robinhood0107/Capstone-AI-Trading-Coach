from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import psycopg
import pytest

from app.rag.document_ir_materializer import (
    RagV2CanonicalDocumentChunk,
    RagV2DocumentMaterialization,
)
from app.rag.rag_v2_bge_materializer import (
    RagV2BgeDocumentEmbedding,
    RagV2BgeMaterializedPublicDocument,
)
from app.rag.rag_v2_public_bge_staging import (
    PublicBgeSourceMetadata,
    RagV2PublicBgeStagingError,
    RagV2PublicBgeComponentContext,
    build_public_bge_component_context,
    build_public_bge_staging_payload,
)
from app.rag.rag_v2_public_bge_activation_repository import (
    PublicBgeActivationRequest,
    PsycopgRagV2PublicBgeActivationRepository,
)
from app.rag.rag_v2_public_bge_staging_repository import (
    PublicBgeEvaluationEvidence,
    PublicBgeStagingRepositoryError,
    PsycopgRagV2PublicBgeStagingRepository,
)
from app.rag.source_card_corpus import S4_7B_CORPUS_MANIFEST_PATH


def test_public_writer_stages_exact30_evaluates_and_has_no_raw_table_grant(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    records = tuple(_record("EXACT30", index) for index in range(30))
    context = build_public_bge_component_context(records)
    repository = PsycopgRagV2PublicBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    receipts = repository.stage_component(records=records, context=context)

    assert len(receipts) == 30
    assert all(receipt.component_generation_id == context.component_generation_id for receipt in receipts)
    assert all(receipt.component_scope == "EXACT30" for receipt in receipts)
    assert all(receipt.source_reused is False for receipt in receipts)
    assert receipts[-1].state == "STAGED"
    assert receipts[-1].source_count == 30
    assert receipts[-1].chunk_count == 30

    resumed = repository.stage(record=records[0], context=context)
    assert resumed.source_reused is True
    assert resumed.state == "STAGED"
    assert resumed.source_count == 30
    assert resumed.chunk_count == 30

    fractional_evaluation = _evaluation_evidence("fractional").as_payload()
    fractional_evaluation["providerPhysicalCallCount"] = 0.4
    with pytest.raises(psycopg.Error):
        with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"], autocommit=False) as connection:
            with connection.transaction():
                connection.execute(
                    """
                    SELECT *
                    FROM public.evaluate_rag_v2_immutable_public_bge_component(%s, %s::jsonb)
                    """,
                    (context.component_generation_id, json.dumps(fractional_evaluation, separators=(",", ":"))),
                ).fetchall()

    evidence = _evaluation_evidence("a")
    with pytest.raises(PublicBgeStagingRepositoryError, match="PUBLIC_BGE_EVALUATION_REJECTED"):
        repository.evaluate(
            context=context,
            evidence=replace(evidence, citation_coverage=0.79),
        )
    evaluation = repository.evaluate(context=context, evidence=evidence)
    assert evaluation.component_generation_id == context.component_generation_id
    assert evaluation.state == "EVALUATED"
    assert evaluation.source_count == 30
    assert evaluation.chunk_count == 30
    assert repository.evaluate(context=context, evidence=evidence) == evaluation

    with pytest.raises(PublicBgeStagingRepositoryError, match="PUBLIC_BGE_EVALUATION_REJECTED"):
        repository.evaluate(context=context, evidence=_evaluation_evidence("b"))

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT state, evaluation_status, actual_source_count, actual_chunk_count
            FROM rag_v2_immutable_component_generations
            WHERE component_generation_id = %s
            """,
            (context.component_generation_id,),
        ).fetchone() == ("EVALUATED", "PASSED", 30, 30)
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_embedding_cache
            WHERE owner_user_id IS NULL
              AND source_scope = 'EXACT30'
              AND embedding_profile_id = 'bge_m3_local_1024_v1'
            """
        ).fetchone() == (30,)

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"], autocommit=True) as connection:
        for table in (
            "rag_v2_immutable_source_revisions",
            "rag_v2_immutable_chunks",
            "rag_v2_immutable_component_generations",
            "rag_v2_immutable_public_component_evaluations",
            "rag_v2_immutable_public_component_manifests",
            "rag_v2_immutable_exact30_source_allowlist",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"SELECT * FROM {table}").fetchall()


def test_evaluated_public_bge_pair_activates_through_admin_definers_only(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    exact_records = tuple(_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_record("OA112", index) for index in range(112))
    exact_context = build_public_bge_component_context(exact_records)
    oa_context = build_public_bge_component_context(oa_records)
    writer = PsycopgRagV2PublicBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    assert writer.stage_component(records=exact_records, context=exact_context)[-1].state == "STAGED"
    assert writer.stage_component(records=oa_records, context=oa_context)[-1].state == "STAGED"
    assert writer.evaluate(
        context=exact_context,
        evidence=_evaluation_evidence("activation-exact30"),
    ).state == "EVALUATED"
    assert writer.evaluate(
        context=oa_context,
        evidence=_evaluation_evidence("activation-oa112"),
    ).state == "EVALUATED"

    repository = PsycopgRagV2PublicBgeActivationRepository(
        database_dsn=isolated_postgres_cluster["rag_admin_dsn"],
    )
    request = PublicBgeActivationRequest(exact30=exact_context, oa112=oa_context)
    receipt = repository.activate(request=request)

    assert receipt.state == "ACTIVE"
    assert receipt.exact30_generation_id == exact_context.component_generation_id
    assert receipt.oa112_generation_id == oa_context.component_generation_id
    assert receipt.previous_pointer_version == 1
    assert receipt.new_pointer_version == 2
    assert repository.activate(request=request).new_pointer_version == 2

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT state, exact30_generation_id, oa112_generation_id, embedding_profile_id, pointer_version
            FROM rag_v2_immutable_public_bundle_pointers
            WHERE state_id = 'default'
            """
        ).fetchone() == (
            "ACTIVE",
            exact_context.component_generation_id,
            oa_context.component_generation_id,
            "bge_m3_local_1024_v1",
            2,
        )

    with psycopg.connect(isolated_postgres_cluster["rag_admin_dsn"], autocommit=True) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "SELECT * FROM rag_v2_immutable_public_bundle_pointers"
            ).fetchall()


def test_public_writer_allows_oa112_card_only_through_definer_rls_policy(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    records = tuple(_record("OA112", index) for index in range(112))
    context = build_public_bge_component_context(records)
    repository = PsycopgRagV2PublicBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    receipt = repository.stage(record=records[0], context=context)
    assert receipt.component_scope == "OA112"
    assert receipt.state == "STAGING"
    assert receipt.source_count == receipt.chunk_count == 1

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT active_oa112_eligible, machine_fetch_allowed, local_processing_allowed,
                   external_embedding_allowed, external_generation_allowed
            FROM rag_v2_immutable_oa_source_cards
            WHERE source_revision_id = %s
            """,
            (records[0][0].document.source_revision_id,),
        ).fetchone() == (True, True, True, True, True)

    payload = build_public_bge_staging_payload(records[1], context=context)
    source = payload["source"]
    assert isinstance(source, dict)
    source["machineFetchAllowed"] = False
    with pytest.raises(psycopg.Error):
        with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"], autocommit=False) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL statement_timeout = '60s'")
                connection.execute("SET LOCAL lock_timeout = '10s'")
                connection.execute(
                    "SELECT * FROM public.stage_rag_v2_immutable_public_bge_document(%s::jsonb)",
                    (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
                ).fetchall()
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_source_revisions
            WHERE source_revision_id = %s
            """,
            (records[1][0].document.source_revision_id,),
        ).fetchone() == (0,)


def test_public_writer_rejects_exact30_frozen_card_digest_drift(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    records = tuple(_record("EXACT30", index) for index in range(30))
    context = build_public_bge_component_context(records)
    payload = build_public_bge_staging_payload(records[0], context=context)
    source = payload["source"]
    assert isinstance(source, dict)
    source["sourceCardSha256"] = "0" * 64

    with pytest.raises(psycopg.Error):
        with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"], autocommit=False) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT * FROM public.stage_rag_v2_immutable_public_bge_document(%s::jsonb)",
                    (json.dumps(payload, separators=(",", ":"), sort_keys=True),),
                ).fetchall()

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_source_revisions WHERE source_revision_id = %s",
            (records[0][0].document.source_revision_id,),
        ).fetchone() == (0,)


def test_public_evaluator_rejects_shuffled_member_manifest_after_persisted_recompute(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    records = tuple(_record("EXACT30", index) for index in range(30))
    context = _shuffled_member_context(build_public_bge_component_context(records))
    repository = PsycopgRagV2PublicBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    receipts = repository.stage_component(records=records, context=context)
    assert receipts[-1].state == "STAGED"

    with pytest.raises(PublicBgeStagingRepositoryError, match="PUBLIC_BGE_EVALUATION_REJECTED"):
        repository.evaluate(context=context, evidence=_evaluation_evidence("shuffled"))


def test_same_source_staged_by_concurrent_generations_reuses_one_immutable_graph(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    base_records = tuple(_record("EXACT30", index) for index in range(30))
    refreshed_records = (base_records[0],) + tuple(
        _record("EXACT30", index, revision_variant="refresh")
        for index in range(1, 30)
    )
    first_context = build_public_bge_component_context(base_records)
    second_context = build_public_bge_component_context(refreshed_records)
    assert first_context.component_generation_id != second_context.component_generation_id
    repository = PsycopgRagV2PublicBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(repository.stage, record=base_records[0], context=first_context),
            executor.submit(repository.stage, record=refreshed_records[0], context=second_context),
        )
        receipts = tuple(future.result(timeout=30) for future in futures)

    assert {receipt.component_generation_id for receipt in receipts} == {
        first_context.component_generation_id,
        second_context.component_generation_id,
    }
    assert sorted(receipt.source_reused for receipt in receipts) == [False, True]
    assert all(receipt.source_count == receipt.chunk_count == 1 for receipt in receipts)

    source_revision_id = base_records[0][0].document.source_revision_id
    chunk_id = base_records[0][0].document.chunks[0].chunk_id
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_source_revisions WHERE source_revision_id = %s",
            (source_revision_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_chunks WHERE chunk_id = %s",
            (chunk_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_generation_memberships
            WHERE source_revision_id = %s
            """,
            (source_revision_id,),
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_generation_embeddings
            WHERE chunk_id = %s
            """,
            (chunk_id,),
        ).fetchone() == (2,)
        reuse_counts = connection.execute(
            """
            SELECT source_reused_count, chunk_reused_count, embedding_reused_count
            FROM rag_v2_immutable_materialization_runs
            ORDER BY component_generation_id
            """
        ).fetchall()
        assert sorted(reuse_counts) == [(0, 0, 0), (1, 1, 1)]


def _evaluation_evidence(marker: str) -> PublicBgeEvaluationEvidence:
    return PublicBgeEvaluationEvidence(
        evaluation_digest=hashlib.sha256(f"public-bge-evaluation-{marker}".encode()).hexdigest(),
        exact_top5_hit_rate=1.0,
        track_recall_at5=0.8,
        citation_coverage=0.8,
        direct_advice_block_rate=1.0,
        cross_owner_leak_count=0,
        mixed_profile_row_count=0,
        owner_delete_residual_row_count=0,
        warm_p95_millis=123.0,
        provider_physical_call_count=0,
    )


def _shuffled_member_context(
    context: RagV2PublicBgeComponentContext,
) -> RagV2PublicBgeComponentContext:
    """DB가 source-id 정렬을 독립 재계산하는지 확인할 의도적 비정렬 context를 만든다."""

    member_digests = tuple(reversed(context.member_digests))
    manifest_hash = _canonical_hash(
        {
            "componentScope": context.component_scope,
            "embeddingProfileId": context.embedding_profile_id,
            "members": list(member_digests),
            "schemaVersion": 1,
        }
    )
    generation_hash = _canonical_hash(
        {
            "componentScope": context.component_scope,
            "embeddingProfileId": context.embedding_profile_id,
            "expectedChunkCount": context.expected_chunk_count,
            "expectedSourceCount": context.expected_source_count,
            "manifestHash": manifest_hash,
            "schemaVersion": 1,
        }
    )
    component_generation_id = f"rgr_{generation_hash[:32]}"
    materialization_run_id = "rgr_run_" + hashlib.sha256(
        f"rag-v2-public-bge-run|{component_generation_id}|{manifest_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return RagV2PublicBgeComponentContext(
        component_scope=context.component_scope,
        component_generation_id=component_generation_id,
        materialization_run_id=materialization_run_id,
        generation_hash=generation_hash,
        manifest_hash=manifest_hash,
        expected_source_count=context.expected_source_count,
        expected_chunk_count=context.expected_chunk_count,
        embedding_profile_id=context.embedding_profile_id,
        member_digests=member_digests,
    )


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _record(
    scope: str,
    index: int,
    *,
    revision_variant: str = "",
) -> tuple[RagV2BgeMaterializedPublicDocument, PublicBgeSourceMetadata]:
    exact_card = _exact30_card(index) if scope == "EXACT30" else None
    marker = hashlib.sha256(f"{scope}-{index}-{revision_variant}".encode()).hexdigest()
    source_id = exact_card["sourceId"] if exact_card is not None else f"src_{scope.lower()}_{index:03d}"
    variant_suffix = f"_{revision_variant}" if revision_variant else ""
    source_revision_id = f"srv_{scope.lower()}_{index:03d}{variant_suffix}"
    document_id = f"doc_{scope.lower()}_{index:03d}{variant_suffix}_fixture"
    chunk_id = f"rag_v2_chk_{marker[:32]}"
    raw_hash = exact_card["contentSha256"] if exact_card is not None else hashlib.sha256(
        f"raw-{scope}-{index}".encode()
    ).hexdigest()
    normalized_hash = hashlib.sha256(f"normalized-{scope}-{index}-{revision_variant}".encode()).hexdigest()
    canonical_text = f"{scope} evidence fixture {index} {revision_variant}."
    canonical_hash = hashlib.sha256(canonical_text.encode()).hexdigest()
    document_ir = {
        "blocks": [
            {
                "blockType": "PARAGRAPH",
                "locator": {"section": "document"},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": canonical_text,
            }
        ],
        "contractId": "rag-document-ir-v1",
        "documentIrVersion": 1,
        "extractionMode": "NATIVE",
        "languageTags": ["en"],
        "mimeType": "text/plain",
        "normalizedContentSha256": normalized_hash,
        "parserEvidence": {
            "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
            "parserArtifactSha256": "a" * 64,
            "parserBackend": "fixture",
            "parserVersion": "fixture-v1",
        },
        "rawContentSha256": raw_hash,
        "safetyClassification": {
            "externalLlmEligible": scope == "OA112",
            "piiDetected": False,
            "promptInjectionDetected": False,
            "secretDetected": False,
        },
        "sourceId": source_id,
        "sourceRevisionId": source_revision_id,
    }
    chunk = RagV2CanonicalDocumentChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        sequence=1,
        heading_path=(),
        locator={"section": "document"},
        canonical_text=canonical_text,
        canonical_text_sha256=canonical_hash,
        token_count=4,
        contains_table=False,
    )
    document = RagV2DocumentMaterialization(
        document_id=document_id,
        source_scope=scope,  # type: ignore[arg-type]
        source_id=source_id,
        source_revision_id=source_revision_id,
        raw_content_sha256=raw_hash,
        normalized_content_sha256=normalized_hash,
        external_processing_eligible=scope == "OA112",
        chunks=(chunk,),
    )
    vector = np.zeros(1024, dtype=np.float32)
    vector[index % 1024] = 1.0
    materialized = RagV2BgeMaterializedPublicDocument(
        document=document,
        embeddings=(
            RagV2BgeDocumentEmbedding(
                chunk_id=chunk_id,
                embedding_input_hash=hashlib.sha256(canonical_text.encode()).hexdigest(),
                context_set_hash=None,
                embedding=vector,
            ),
        ),
        source_revision_sha256=hashlib.sha256(
            json.dumps(document_ir, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        document_ir=document_ir,
    )
    if scope == "OA112":
        metadata = PublicBgeSourceMetadata(
            citation_title=f"OA fixture {index}",
            retrieval_topics=("METHODOLOGY",),
            canonical_https_url=f"https://example.invalid/oa/{index:03d}.txt",
            source_card_sha256=None,
            oa_track_id=_oa_track(index),
            source_card=_oa_source_card(
                source_id=source_id,
                title=f"OA fixture {index}",
                url=f"https://example.invalid/oa/{index:03d}.txt",
                raw_hash=raw_hash,
                ordinal=index,
            ),
            license_evidence_sha256=hashlib.sha256(f"license-{index}".encode()).hexdigest(),
            access_evidence_sha256=hashlib.sha256(f"access-{index}".encode()).hexdigest(),
            machine_fetch_allowed=True,
            local_processing_allowed=True,
            external_embedding_allowed=True,
            external_generation_allowed=True,
        )
    else:
        metadata = PublicBgeSourceMetadata(
            citation_title=f"Exact fixture {index}",
            retrieval_topics=("METHODOLOGY",),
            canonical_https_url=exact_card["canonicalUrl"] if exact_card is not None else "",
            source_card_sha256=exact_card["cardSha256"] if exact_card is not None else None,
            oa_track_id=None,
            source_card=None,
            license_evidence_sha256=None,
            access_evidence_sha256=None,
            machine_fetch_allowed=False,
            local_processing_allowed=True,
            external_embedding_allowed=False,
            external_generation_allowed=False,
        )
    return materialized, metadata


def _exact30_card(index: int) -> dict[str, str]:
    """고정 manifest를 test fixture identity로만 읽고 source-card body는 읽지 않는다."""

    payload = json.loads(S4_7B_CORPUS_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    cards = payload.get("cards")
    assert isinstance(cards, list)
    card = cards[index]
    assert isinstance(card, dict)
    required = ("sourceId", "canonicalUrl", "contentSha256", "cardSha256")
    assert all(isinstance(card.get(key), str) for key in required)
    return {key: str(card[key]) for key in required}


def _oa_track(index: int) -> str:
    tracks = (
        "MICRO_GAME_INFO_MARKET_DESIGN",
        "MACRO_MONETARY_INTERNATIONAL",
        "PROBABILITY_STATISTICS_OPTIMIZATION",
        "ECONOMETRICS_CAUSAL_EVENT_STUDY",
        "TIME_SERIES_REGIME_VOLATILITY",
        "ACCOUNTING_CORPORATE_FINANCE_VALUATION",
        "ASSET_PRICING_FACTOR_PORTFOLIO",
        "FIXED_INCOME_RATES_CREDIT",
        "DERIVATIVES_STOCHASTIC_NUMERICS",
        "MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY",
        "RISK_STRESS_BACKTEST_MODEL_RISK",
        "BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING",
        "FINANCIAL_ML_PIT_DATA_PROVENANCE",
        "CROSS_MARKET_COMMODITIES_POLICY_KOREA",
    )
    return tracks[index // 8]


def _oa_source_card(
    *,
    source_id: str,
    title: str,
    url: str,
    raw_hash: str,
    ordinal: int,
) -> dict[str, object]:
    return {
        "accessEvidence": {
            "accessCheckedAt": "2026-08-03T00:00:00Z",
            "accessEvidenceDigest": hashlib.sha256(f"access-{ordinal}".encode()).hexdigest(),
            "verificationState": "VERIFIED",
        },
        "activeOa112Eligible": True,
        "authors": [f"Fixture Author {ordinal}"],
        "canonicalUrl": url,
        "canonicalUrlSha256": hashlib.sha256(url.encode()).hexdigest(),
        "contractId": "rag-source-card-v4",
        "identifier": {"scheme": "DOI", "value": f"10.0000/fixture-{ordinal}"},
        "licenseEvidenceDigest": hashlib.sha256(f"license-{ordinal}".encode()).hexdigest(),
        "mimeType": "text/plain",
        "permissions": {
            "externalEmbeddingAllowed": True,
            "externalGenerationAllowed": True,
            "localProcessingAllowed": True,
            "machineFetchAllowed": True,
        },
        "rawContentSha256": raw_hash,
        "revision": f"r{ordinal}",
        "revisionDate": "2026-08-03",
        "schemaVersion": 4,
        "sourceId": source_id,
        "sourceKind": "OPEN_ACCESS_DOCUMENT",
        "title": title,
    }


def test_public_repository_rejects_component_scope_drift_before_connecting() -> None:
    records = tuple(_record("EXACT30", index) for index in range(30))
    context = build_public_bge_component_context(records)
    repository = PsycopgRagV2PublicBgeStagingRepository(database_dsn="postgresql://invalid")

    with pytest.raises(RagV2PublicBgeStagingError, match="PUBLIC_BGE_COMPONENT_CONTEXT"):
        repository.stage(record=_record("OA112", 0), context=context)
