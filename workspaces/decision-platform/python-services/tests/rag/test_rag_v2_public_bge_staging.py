from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np
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
    build_public_bge_component_context,
    build_public_bge_staging_payload,
)


def test_oa112_component_context_requires_exact_membership_and_builds_path_free_payload() -> None:
    records = tuple(_record("OA112", index) for index in range(112))

    context = build_public_bge_component_context(records)
    payload = build_public_bge_staging_payload(records[0], context=context)

    assert context.component_scope == "OA112"
    assert context.expected_source_count == 112
    assert context.expected_chunk_count == 112
    assert context.embedding_profile_id == "bge_m3_local_1024_v1"
    assert payload["componentGenerationId"] == context.component_generation_id
    assert payload["source"]["oaSourceCard"]["sourceId"] == records[0][0].document.source_id
    assert payload["source"]["oaTrackId"] == "MICRO_GAME_INFO_MARKET_DESIGN"
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "/private/" not in encoded
    assert '"rawPath"' not in encoded
    assert '"absolutePath"' not in encoded


def test_exact30_context_keeps_external_processing_disabled_and_oa_metadata_out() -> None:
    records = tuple(_record("EXACT30", index) for index in range(30))

    context = build_public_bge_component_context(records)
    payload = build_public_bge_staging_payload(records[0], context=context)

    assert context.component_scope == "EXACT30"
    assert context.expected_source_count == 30
    assert payload["source"]["oaSourceCard"] is None
    assert payload["source"]["oaTrackId"] is None
    assert payload["source"]["externalEmbeddingAllowed"] is False
    assert payload["source"]["externalGenerationAllowed"] is False
    assert payload["source"]["externalProcessingEligible"] is False


def test_public_context_rejects_incomplete_mixed_or_source_identity_drift() -> None:
    records = tuple(_record("EXACT30", index) for index in range(30))

    with pytest.raises(RagV2PublicBgeStagingError, match="PUBLIC_BGE_COMPONENT_MEMBERSHIP"):
        build_public_bge_component_context(records[:-1])

    with pytest.raises(RagV2PublicBgeStagingError, match="PUBLIC_BGE_COMPONENT_SCOPE"):
        build_public_bge_component_context((*records[:-1], _record("OA112", 0)))

    materialized, metadata = records[0]
    with pytest.raises(RagV2PublicBgeStagingError, match="PUBLIC_BGE_SOURCE_IDENTITY"):
        build_public_bge_staging_payload(
            (replace(materialized, source_revision_sha256="0" * 64), metadata),
            context=build_public_bge_component_context(records),
        )


def _record(
    scope: str,
    index: int,
) -> tuple[RagV2BgeMaterializedPublicDocument, PublicBgeSourceMetadata]:
    marker = hashlib.sha256(f"{scope}-{index}".encode()).hexdigest()
    source_id = f"src_{scope.lower()}_{index:03d}"
    source_revision_id = f"srv_{scope.lower()}_{index:03d}"
    document_id = f"doc_{scope.lower()}_{index:03d}_fixture"
    chunk_id = f"rag_v2_chk_{marker[:32]}"
    raw_hash = hashlib.sha256(f"raw-{scope}-{index}".encode()).hexdigest()
    normalized_hash = hashlib.sha256(f"normalized-{scope}-{index}".encode()).hexdigest()
    canonical_text = f"{scope} evidence fixture {index}."
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
            json.dumps(
                document_ir, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
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
            canonical_https_url=f"https://example.invalid/exact/{index:03d}",
            source_card_sha256=hashlib.sha256(f"exact-card-{index}".encode()).hexdigest(),
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
