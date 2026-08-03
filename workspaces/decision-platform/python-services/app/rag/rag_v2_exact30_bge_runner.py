from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.rag.exact30_source_card_parser import (
    EXACT30_LANGUAGE_TAGS,
    Exact30SourceCardDocumentParser,
    Exact30SourceCardParserError,
    exact30_document_id,
    exact30_source_revision_id,
)
from app.rag.ingest_pipeline import RagTokenizer
from app.rag.rag_v2_bge_materializer import (
    BgeDocumentEmbedder,
    RagV2BgeMaterializationError,
    RagV2PublicDocumentRequest,
    materialize_public_bge_document,
)
from app.rag.rag_v2_public_bge_staging import (
    PublicBgeSourceMetadata,
    RagV2PublicBgeComponentContext,
    RagV2PublicBgeStagingError,
    build_public_bge_component_context,
)
from app.rag.rag_v2_public_bge_staging_repository import PublicBgeRecord
from app.rag.source_card_corpus import (
    PUBLIC_TOPICS_BY_SOURCE_ID,
    S4_7B_SOURCE_CARD_ROOT,
    FrozenSourceCard,
    FrozenSourceCardCorpus,
    RagSourceCardCorpusError,
    load_frozen_source_card_corpus,
)

_BGE_PROFILE_ID = "bge_m3_local_1024_v1"


class RagV2Exact30BgeRunnerError(ValueError):
    """exact-30 local BGE component materialization이 closed source-card contract를 벗어났다."""


@dataclass(frozen=True, slots=True)
class Exact30PublicBgeMaterialization:
    """in-memory exact-30 public component와 DB에 안전한 immutable context다.

    `records`에는 canonical text/vector가 process-local로만 존재한다. caller는 이를 writer
    function에 즉시 전달하거나 폐기해야 하며, receipt·CLI·history에 직렬화하면 안 된다.
    """

    records: tuple[PublicBgeRecord, ...]
    context: RagV2PublicBgeComponentContext
    source_card_corpus_manifest_sha256: str

    def content_free_receipt(self) -> dict[str, object]:
        """raw card text, source path, vector를 제외한 local materialization receipt다."""

        return {
            "chunkCount": self.context.expected_chunk_count,
            "componentGenerationId": self.context.component_generation_id,
            "componentScope": self.context.component_scope,
            "embeddingProfileId": self.context.embedding_profile_id,
            "generationHash": self.context.generation_hash,
            "manifestHash": self.context.manifest_hash,
            "materializationRunId": self.context.materialization_run_id,
            "sourceCardCorpusManifestSha256": self.source_card_corpus_manifest_sha256,
            "sourceCount": self.context.expected_source_count,
        }


def materialize_exact30_public_bge_component(
    *,
    tokenizer: RagTokenizer,
    embedder: BgeDocumentEmbedder,
    corpus: FrozenSourceCardCorpus | None = None,
) -> Exact30PublicBgeMaterialization:
    """all frozen exact-30 cards를 local safe parser→BGE vector component로 materialize한다.

    이 함수는 network/provider transport, database connection, active bundle pointer 변경을 만들지
    않는다. exact card manifest를 먼저 load하고 30개 전체가 local BGE를 통과할 때만 stage-ready
    component를 반환한다.
    """

    try:
        selected_corpus = corpus or load_frozen_source_card_corpus()
    except RagSourceCardCorpusError as error:
        raise RagV2Exact30BgeRunnerError("EXACT30_SOURCE_CARD_CORPUS") from error
    cards = tuple(selected_corpus.cards)
    if len(cards) != 30 or len({card.source_id for card in cards}) != 30:
        raise RagV2Exact30BgeRunnerError("EXACT30_SOURCE_CARD_MEMBERSHIP")

    parser = Exact30SourceCardDocumentParser(corpus=selected_corpus)
    records: list[PublicBgeRecord] = []
    for card in cards:
        try:
            materialized = materialize_public_bge_document(
                parser=parser,
                tokenizer=tokenizer,
                embedder=embedder,
                request=_document_request(card),
            )
            records.append((materialized, _source_metadata(card)))
        except (Exact30SourceCardParserError, RagV2BgeMaterializationError) as error:
            raise RagV2Exact30BgeRunnerError("EXACT30_BGE_MATERIALIZATION") from error

    try:
        context = build_public_bge_component_context(tuple(records))
    except RagV2PublicBgeStagingError as error:
        raise RagV2Exact30BgeRunnerError("EXACT30_BGE_COMPONENT_CONTEXT") from error
    if context.component_scope != "EXACT30" or context.expected_source_count != 30:
        raise RagV2Exact30BgeRunnerError("EXACT30_BGE_COMPONENT_CONTEXT")
    return Exact30PublicBgeMaterialization(
        records=tuple(records),
        context=context,
        source_card_corpus_manifest_sha256=selected_corpus.corpus_manifest_sha256,
    )


def _document_request(card: FrozenSourceCard) -> RagV2PublicDocumentRequest:
    """frozen card metadata에서만 exact-30 parser request를 만들고 caller selector를 받지 않는다."""

    return RagV2PublicDocumentRequest(
        approved_root=S4_7B_SOURCE_CARD_ROOT,
        relative_path=Path(card.relative_path).name,
        document_id=exact30_document_id(card),
        source_scope="EXACT30",
        source_id=card.source_id,
        source_revision_id=exact30_source_revision_id(card),
        language_tags=EXACT30_LANGUAGE_TAGS,
        expected_raw_content_sha256=card.content_sha256,
        expected_mime_type="text/markdown",
        local_processing_allowed=True,
        external_embedding_allowed=False,
        external_generation_allowed=False,
        embedding_profile_id=_BGE_PROFILE_ID,
    )


def _source_metadata(card: FrozenSourceCard) -> PublicBgeSourceMetadata:
    """frozen card의 citation/rights projection만 stage payload로 넘긴다."""

    title = card.front_matter.get("title")
    canonical_url = card.front_matter.get("canonicalUrl")
    topics = PUBLIC_TOPICS_BY_SOURCE_ID.get(card.source_id)
    if not isinstance(title, str) or not isinstance(canonical_url, str) or not topics:
        raise RagV2Exact30BgeRunnerError("EXACT30_SOURCE_CARD_METADATA")
    return PublicBgeSourceMetadata(
        citation_title=title,
        retrieval_topics=tuple(topics),
        canonical_https_url=canonical_url,
        source_card_sha256=card.card_sha256,
        oa_track_id=None,
        source_card=None,
        license_evidence_sha256=None,
        access_evidence_sha256=None,
        machine_fetch_allowed=False,
        local_processing_allowed=True,
        external_embedding_allowed=False,
        external_generation_allowed=False,
    )
