from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.rag.authorized_retrieval import (
    AuthorizedRetrievalScope,
    ChannelResult,
    NormalizedRetrievalQuery,
    RetrievalCandidate,
)
from app.rag.source_card_corpus import FrozenSourceCard


class AuthorizedRetrievalAdapterError(RuntimeError):
    """DB channel 또는 immutable card enrichment가 fail-closed했음을 표시한다."""


@dataclass(frozen=True)
class ImmutableCardEvidence:
    """DB에 복제하지 않는 source-card v2 evidence policy metadata."""

    source_id: str
    card_id: str
    evidence_class: str
    model_sensitive: bool
    assumption_keys: tuple[str, ...]
    limitations: tuple[str, ...]
    contradicts_card_ids: tuple[str, ...]


class LocalBgeQueryEmbedder:
    """검증된 local BGE runtime을 domain QueryEmbedder port로 좁힌다."""

    def __init__(self, embedder: Any) -> None:
        if not callable(getattr(embedder, "embed_query", None)):
            raise AuthorizedRetrievalAdapterError("RAG query embedder is invalid.")
        self._embedder = embedder

    @property
    def embedding_profile_id(self) -> str:
        return "bge_m3_local_1024_v1"

    def embed_query(self, question: str) -> Sequence[float]:
        """local CPU inference 결과만 반환하며 provider/network 경계를 만들지 않는다."""

        result = self._embedder.embed_query(question)
        if not hasattr(result, "tolist"):
            raise AuthorizedRetrievalAdapterError("RAG query embedding result is invalid.")
        values = result.tolist()
        if not isinstance(values, list):
            raise AuthorizedRetrievalAdapterError("RAG query embedding result is invalid.")
        return tuple(float(value) for value in values)


class PsycopgAuthorizedRetrievalAdapter:
    """decision_rag_query의 세 bounded definer function만 실행하는 DB adapter."""

    def __init__(
        self,
        *,
        database_dsn: str,
        card_evidence: Mapping[str, ImmutableCardEvidence],
    ) -> None:
        if not database_dsn or len(database_dsn) > 4096:
            raise AuthorizedRetrievalAdapterError("RAG query database DSN is invalid.")
        if not card_evidence:
            raise AuthorizedRetrievalAdapterError("RAG card evidence index is empty.")
        self._database_dsn = database_dsn
        self._card_evidence = dict(card_evidence)

    def retrieve_exact(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        identifiers: tuple[str, ...],
    ) -> ChannelResult:
        """literal identifier array만 exact projection에 전달한다."""

        rows = self._execute(
            """
            SELECT *
            FROM public.search_authorized_rag_exact(%s, %s, %s, %s, %s)
            """,
            (
                scope.claim_id,
                scope.owner_user_id,
                scope.session_id,
                list(_effective_topics(scope, query)),
                list(identifiers),
            ),
        )
        return ChannelResult(
            channel="exact",
            items=self._map_rows(rows),
            complete=True,
        )

    def retrieve_lexical(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
    ) -> ChannelResult:
        """versioned one-pass synonym text만 pg_trgm projection에 전달한다."""

        rows = self._execute(
            """
            SELECT *
            FROM public.search_authorized_rag_lexical(%s, %s, %s, %s, %s)
            """,
            (
                scope.claim_id,
                scope.owner_user_id,
                scope.session_id,
                list(_effective_topics(scope, query)),
                query.lexical_query,
            ),
        )
        return ChannelResult(
            channel="lexical",
            items=self._map_rows(rows),
            complete=True,
        )

    def retrieve_dense(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> ChannelResult:
        """검증된 1024차원 unit vector만 cosine projection에 전달한다."""

        rows = self._execute(
            """
            SELECT *
            FROM public.search_authorized_rag_dense(
              %s, %s, %s, %s, %s::vector
            )
            """,
            (
                scope.claim_id,
                scope.owner_user_id,
                scope.session_id,
                list(_effective_topics(scope, query)),
                _vector_text(query_vector),
            ),
        )
        return ChannelResult(
            channel="dense",
            items=self._map_rows(rows),
            complete=True,
        )

    def _execute(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> list[dict[str, Any]]:
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=1,
                row_factory=dict_row,
            ) as connection:
                connection.execute("SET LOCAL statement_timeout = '1500ms'")
                connection.execute("SET LOCAL lock_timeout = '250ms'")
                rows = connection.execute(statement, parameters).fetchall()
                connection.commit()
        except psycopg.Error as error:
            # DSN, SQL, role과 server error 원문은 application/public 경계로 전달하지 않는다.
            raise AuthorizedRetrievalAdapterError(
                "RAG authorized retrieval channel failed."
            ) from error
        if len(rows) > 30:
            raise AuthorizedRetrievalAdapterError(
                "RAG authorized retrieval channel exceeded its bound."
            )
        return rows

    def _map_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[RetrievalCandidate, ...]:
        candidates: list[RetrievalCandidate] = []
        for expected_rank, row in enumerate(rows, start=1):
            if row.get("rank_no") != expected_rank:
                raise AuthorizedRetrievalAdapterError(
                    "RAG authorized retrieval rank receipt drifted."
                )
            source_id = _required_text(row, "source_id")
            metadata = self._card_evidence.get(source_id)
            if metadata is None or metadata.card_id != _required_text(row, "card_id"):
                raise AuthorizedRetrievalAdapterError(
                    "RAG immutable card evidence identity drifted."
                )
            candidates.append(
                RetrievalCandidate(
                    chunk_revision_id=_required_text(row, "chunk_revision_id"),
                    source_revision_id=_required_text(row, "source_revision_id"),
                    source_id=source_id,
                    card_id=metadata.card_id,
                    title=_required_text(row, "title"),
                    heading_path=_required_text_array(row, "heading_path"),
                    canonical_content=_required_text(row, "canonical_content"),
                    canonical_content_hash=_required_text(
                        row,
                        "canonical_content_hash",
                    ),
                    topic=_required_text(row, "topic"),
                    public_topics=_required_text_array(row, "public_topics"),
                    access_level=_required_text(row, "access_level"),
                    tier=_required_text(row, "tier"),
                    source_status=_required_text(row, "source_status"),
                    evidence_class=metadata.evidence_class,
                    model_sensitive=metadata.model_sensitive,
                    assumption_keys=metadata.assumption_keys,
                    limitations=metadata.limitations,
                    contradicts_card_ids=metadata.contradicts_card_ids,
                    scope_claim_id=_required_text(row, "scope_claim_id"),
                    owner_user_id=_required_text(row, "owner_user_id"),
                    session_id=_required_text(row, "session_id"),
                    generation_id=_required_text(row, "generation_id"),
                    embedding_profile_id=_required_text(
                        row,
                        "embedding_profile_id",
                    ),
                    policy_version=_required_int(row, "policy_version"),
                )
            )
        return tuple(candidates)


def build_immutable_card_evidence(
    cards: Sequence[FrozenSourceCard],
) -> Mapping[str, ImmutableCardEvidence]:
    """검증된 exact corpus를 evidence policy용 immutable index로 투영한다."""

    evidence: dict[str, ImmutableCardEvidence] = {}
    for card in cards:
        payload = card.front_matter
        assumptions = payload.get("modelAssumptions")
        limitations = payload.get("limitations")
        contradicts = payload.get("contradicts")
        if (
            not isinstance(assumptions, list)
            or not isinstance(limitations, list)
            or not isinstance(contradicts, list)
        ):
            raise AuthorizedRetrievalAdapterError("RAG immutable card evidence shape is invalid.")
        assumption_keys: list[str] = []
        for assumption in assumptions:
            if not isinstance(assumption, Mapping):
                raise AuthorizedRetrievalAdapterError("RAG immutable card assumption is invalid.")
            assumption_keys.append(_required_text(assumption, "key"))
        if not all(isinstance(value, str) and value for value in limitations):
            raise AuthorizedRetrievalAdapterError("RAG immutable card limitation is invalid.")
        if not all(isinstance(value, str) and value for value in contradicts):
            raise AuthorizedRetrievalAdapterError("RAG immutable card contradiction is invalid.")
        evidence[card.source_id] = ImmutableCardEvidence(
            source_id=card.source_id,
            card_id=card.card_id,
            evidence_class=_required_text(payload, "evidenceClass"),
            model_sensitive=_required_bool(payload, "modelSensitive"),
            assumption_keys=tuple(assumption_keys),
            limitations=tuple(limitations),
            contradicts_card_ids=tuple(contradicts),
        )
    if len(evidence) != len(cards):
        raise AuthorizedRetrievalAdapterError(
            "RAG immutable card evidence contains duplicate sources."
        )
    return evidence


def _vector_text(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def _effective_topics(
    scope: AuthorizedRetrievalScope,
    query: NormalizedRetrievalQuery,
) -> tuple[str, ...]:
    return query.topics or scope.allowed_topics


def _required_text(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise AuthorizedRetrievalAdapterError(f"RAG authorized retrieval field {field} is invalid.")
    return value


def _required_int(mapping: Mapping[str, Any], field: str) -> int:
    value = mapping.get(field)
    if type(value) is not int:
        raise AuthorizedRetrievalAdapterError(f"RAG authorized retrieval field {field} is invalid.")
    return value


def _required_bool(mapping: Mapping[str, Any], field: str) -> bool:
    value = mapping.get(field)
    if type(value) is not bool:
        raise AuthorizedRetrievalAdapterError(f"RAG authorized retrieval field {field} is invalid.")
    return value


def _required_text_array(
    mapping: Mapping[str, Any],
    field: str,
) -> tuple[str, ...]:
    value = mapping.get(field)
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise AuthorizedRetrievalAdapterError(f"RAG authorized retrieval field {field} is invalid.")
    return tuple(value)
