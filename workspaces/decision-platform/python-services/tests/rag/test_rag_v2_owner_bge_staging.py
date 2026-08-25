from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import psycopg
import pytest

from app.rag.rag_v2_bge_materializer import (
    RagV2OwnerDocumentRequest,
    materialize_owner_bge_document,
)
from app.rag.rag_v2_owner_bge_staging import (
    OwnerBgeStagingError,
    OwnerBgeStagingMetadata,
    PsycopgRagV2OwnerBgeStagingRepository,
    build_owner_bge_staging_payload,
)
from tests.support.actor_rls_scope import open_actor_rls_scope


class _WhitespaceTokenizer:
    """작은 fixture에서도 deterministic local chunk identity를 만드는 tokenizer다."""

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))

    def take_prefix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[: spans[min(len(spans), maximum_tokens) - 1][1]] if spans else ""

    def take_suffix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[spans[max(0, len(spans) - maximum_tokens)][0] :] if spans else ""


class _FixtureParser:
    def parse_owner_document(self, **_: object) -> dict[str, object]:
        return _document_ir()


class _FixtureEmbedder:
    def embed(self, texts: tuple[str, ...]) -> np.ndarray:
        rows = np.zeros((len(texts), 1024), dtype=np.float32)
        for index in range(len(texts)):
            rows[index, index] = 1.0
        return rows


def test_owner_staging_payload_excludes_local_path_but_keeps_transient_db_input(
    tmp_path: Path,
) -> None:
    materialized = _materialized(tmp_path)

    payload = build_owner_bge_staging_payload(
        materialized,
        metadata=_metadata(),
    )

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert payload["schemaVersion"] == 3
    assert payload["embeddingProfileId"] == "bge_m3_local_1024_v1"
    assert payload["sanitizedDisplayName"] == "Owner fixture"
    assert payload["retrievalTopics"] == ["FINANCIAL_ENGINEERING"]
    assert len(payload["chunks"]) == len(payload["embeddings"]) == 1
    assert "private.pdf" not in encoded
    assert str(tmp_path) not in encoded
    assert "relativePath" not in encoded
    assert "approvedRoot" not in encoded
    assert '"rawContent":' not in encoded


def test_ticket_bound_writer_stages_owner_bge_generation_with_no_raw_table_grant(
    isolated_postgres_cluster: dict[str, str],
    tmp_path: Path,
) -> None:
    cluster = isolated_postgres_cluster
    ticket_id = "rti_11111111111111111111111111111111"
    _issue_ticket(
        cluster["app_dsn"],
        cluster["identity_dsn"],
        owner_user_id="usr_demo_user",
        ticket_id=ticket_id,
    )

    repository = PsycopgRagV2OwnerBgeStagingRepository(
        database_dsn=cluster["rag_writer_dsn"],
    )
    receipt = repository.stage(
        owner_user_id="usr_demo_user",
        import_ticket_id=ticket_id,
        materialized=_materialized(tmp_path),
        metadata=_metadata(),
    )

    assert receipt.owner_user_id == "usr_demo_user"
    assert receipt.component_scope == "OWNER_PRIVATE"
    assert receipt.embedding_profile_id == "bge_m3_local_1024_v1"
    assert receipt.state == "STAGED"
    assert receipt.source_count == 1
    assert receipt.chunk_count == 1

    # v2 ticket은 one-use이며 동일 control replay는 source/chunk/vector를 append하기 전에 거부한다.
    with pytest.raises(OwnerBgeStagingError, match="OWNER_BGE_STAGE_REJECTED"):
        repository.stage(
            owner_user_id="usr_demo_user",
            import_ticket_id=ticket_id,
            materialized=_materialized(tmp_path),
            metadata=_metadata(),
        )

    with psycopg.connect(cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT generation.state, generation.evaluation_status,
                   generation.actual_source_count, generation.actual_chunk_count,
                   run.state, run.source_reused_count, run.chunk_reused_count,
                   run.embedding_reused_count
            FROM rag_v2_immutable_component_generations AS generation
            JOIN rag_v2_immutable_materialization_runs AS run
              ON run.component_generation_id = generation.component_generation_id
            WHERE generation.component_generation_id = %s
            """,
            (receipt.component_generation_id,),
        ).fetchone() == ("STAGING", "PENDING", 1, 1, "STAGED", 0, 0, 0)
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_generation_embeddings
            WHERE component_generation_id = %s
              AND owner_user_id = 'usr_demo_user'
              AND component_scope = 'OWNER_PRIVATE'
            """,
            (receipt.component_generation_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT state, count(*)
            FROM rag_v2_immutable_import_tickets
            WHERE owner_user_id = 'usr_demo_user'
            GROUP BY state
            """
        ).fetchone() == ("CONSUMED", 1)
        assert connection.execute(
            """
            SELECT sanitized_display_name, retrieval_topics
            FROM rag_v2_immutable_source_revisions
            WHERE source_revision_id = 'srv_owner_staging_001'
            """
        ).fetchone() == ("Owner fixture", ["FINANCIAL_ENGINEERING"])

    with psycopg.connect(cluster["rag_writer_dsn"]) as connection:
        assert connection.execute("SELECT current_user").fetchone() == ("decision_rag_writer",)
        assert connection.execute(
            """
            SELECT has_function_privilege(
              current_user,
              'public.stage_rag_v2_immutable_owner_bge_document(text,text,jsonb)',
              'EXECUTE'
            )
            """
        ).fetchone() == (False,)
        assert connection.execute(
            """
            SELECT has_function_privilege(
              current_user,
              'public.stage_rag_v2_immutable_owner_bge_document_v2(text,text,jsonb)',
              'EXECUTE'
            )
            """
        ).fetchone() == (False,)
        assert connection.execute(
            """
            SELECT has_function_privilege(
              current_user,
              'public.stage_rag_v2_immutable_owner_document_v3(text,text,jsonb)',
              'EXECUTE'
            )
            """
        ).fetchone() == (True,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM rag_v2_immutable_source_revisions").fetchall()


def test_writer_rejects_other_owner_before_materializing(
    isolated_postgres_cluster: dict[str, str],
    tmp_path: Path,
) -> None:
    cluster = isolated_postgres_cluster
    ticket_id = "rti_22222222222222222222222222222222"
    _issue_ticket(
        cluster["app_dsn"],
        cluster["identity_dsn"],
        owner_user_id="usr_demo_user",
        ticket_id=ticket_id,
    )

    repository = PsycopgRagV2OwnerBgeStagingRepository(
        database_dsn=cluster["rag_writer_dsn"],
    )
    with pytest.raises(OwnerBgeStagingError, match="OWNER_BGE_STAGE_REJECTED"):
        repository.stage(
            owner_user_id="usr_demo_admin",
            import_ticket_id=ticket_id,
            materialized=_materialized(tmp_path),
            metadata=_metadata(),
        )

    with psycopg.connect(cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_component_generations"
        ).fetchone() == (0,)


def _issue_ticket(
    database_dsn: str,
    identity_dsn: str,
    *,
    owner_user_id: str,
    ticket_id: str,
) -> None:
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            open_actor_rls_scope(
                identity_dsn=identity_dsn,
                connection=connection,
                actor_user_id=owner_user_id,
                actor_role="USER",
                operation="ISSUE_RAG_V2_IMPORT",
                target_kind="RAG_TICKET",
                target_id=ticket_id,
            )
            connection.execute(
                """
                SELECT issue_rag_v2_immutable_import_ticket_v2(
                  %s,
                  %s,
                  'OWNER_IMPORT',
                  'RAG_V2_OWNER_DOCUMENT_V2',
                  'bge_m3_local_1024_v1'
                )
                """,
                (owner_user_id, ticket_id),
            ).fetchone()


def _materialized(root: Path):
    return materialize_owner_bge_document(
        parser=_FixtureParser(),
        tokenizer=_WhitespaceTokenizer(),
        embedder=_FixtureEmbedder(),
        request=RagV2OwnerDocumentRequest(
            approved_root=root,
            relative_path="owner/private.pdf",
            document_id="doc_owner_staging_0001",
            source_id="src_owner_staging_001",
            source_revision_id="srv_owner_staging_001",
            language_tags=("en",),
            embedding_profile_id="bge_m3_local_1024_v1",
        ),
    )


def _metadata() -> OwnerBgeStagingMetadata:
    return OwnerBgeStagingMetadata(
        sanitized_display_name="Owner fixture",
        retrieval_topics=("FINANCIAL_ENGINEERING",),
    )


@pytest.mark.parametrize("display_name", ("C:\\owner.pdf", "../owner", "Owner\nfixture"))
def test_owner_staging_metadata_rejects_path_or_control_aliases(display_name: str) -> None:
    with pytest.raises(OwnerBgeStagingError, match="OWNER_BGE_STAGE_METADATA"):
        OwnerBgeStagingMetadata(
            sanitized_display_name=display_name,
            retrieval_topics=("FINANCIAL_ENGINEERING",),
        )


def _document_ir() -> dict[str, object]:
    return {
        "blocks": [
            {
                "blockType": "PARAGRAPH",
                "locator": {"page": 1},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": "Owner material is staged only behind the owner RLS boundary.",
            }
        ],
        "contractId": "rag-document-ir-v1",
        "documentIrVersion": 1,
        "extractionMode": "NATIVE",
        "languageTags": ["en"],
        "mimeType": "application/pdf",
        "normalizedContentSha256": "b" * 64,
        "parserEvidence": {
            "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
            "parserArtifactSha256": "c" * 64,
            "parserBackend": "fixture-safe-parser",
            "parserVersion": "fixture-v1",
        },
        "rawContentSha256": "a" * 64,
        "safetyClassification": {
            "externalLlmEligible": False,
            "piiDetected": False,
            "promptInjectionDetected": False,
            "secretDetected": False,
        },
        "sourceId": "src_owner_staging_001",
        "sourceRevisionId": "srv_owner_staging_001",
    }
