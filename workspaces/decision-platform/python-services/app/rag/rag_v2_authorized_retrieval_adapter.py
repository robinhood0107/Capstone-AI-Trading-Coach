from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.rag.authorized_retrieval import (
    ALLOWED_RAG_TOPICS,
    EMBEDDING_DIMENSION,
    INTERNAL_CHANNEL_LIMIT,
    NormalizedRetrievalQuery,
)
from app.rag.rag_v2_authorized_retrieval import (
    RagV2BundleScope,
    RagV2ChannelResult,
    RagV2RetrievalCandidate,
)

_QUERY_ROLE = "decision_rag_query"
_SOURCE_SCOPES = frozenset({"EXACT30", "OA112", "OWNER_PRIVATE"})
_DIRECT_READ_TABLES = (
    "public.rag_v2_immutable_source_revisions",
    "public.rag_v2_immutable_chunks",
    "public.rag_v2_immutable_component_generations",
    "public.rag_v2_immutable_generation_memberships",
    "public.rag_v2_immutable_generation_embeddings",
    "public.rag_v2_immutable_public_bundle_pointers",
    "public.rag_v2_immutable_bundles",
    "public.rag_v2_immutable_owner_bundle_pointers",
    "public.rag_v2_retrieval_scope_claims",
)
_REQUIRED_FUNCTIONS = (
    "public.read_rag_v2_retrieval_scope(text,text,text)",
    "public.read_rag_v2_retrieval_scope_by_claim(text,text)",
    "public.search_authorized_rag_v2_exact(text,text,text,text[],text[])",
    "public.search_authorized_rag_v2_lexical(text,text,text,text[],text)",
    "public.search_authorized_rag_v2_dense(text,text,text,text[],vector)",
)


class RagV2AuthorizedRetrievalAdapterError(RuntimeError):
    """v2 retrieval DB capability receipt가 fail-closed했음을 나타낸다."""


class PsycopgRagV2AuthorizedRetrievalAdapter:
    """v2 opaque scope와 bounded definer function만 쓰는 PostgreSQL retrieval adapter다.

    입력은 Spring이 발급한 scope와 scope profile에 맞는 query vector뿐이며, 직접 table read,
    raw artifact 저장, provider/network call을 만들지 않는다. 매 connection에서 role과 grant를
    다시 검증해 잘못된 DSN 또는 과도한 database grant를 capability로 승격하지 않는다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not database_dsn or len(database_dsn) > 4096:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_DSN")
        self._database_dsn = database_dsn

    def read_scope(
        self,
        *,
        claim_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> RagV2BundleScope:
        """query role이 현재 active immutable pointer와 claim을 재검증한 scope를 읽는다."""

        rows = self._execute(
            """
            SELECT *
            FROM public.read_rag_v2_retrieval_scope(%s, %s, %s)
            """,
            (claim_id, owner_user_id, session_id),
        )
        scope = self._scope_from_rows(rows)
        if (
            scope.claim_id != claim_id
            or scope.owner_user_id != owner_user_id
            or scope.session_id != session_id
        ):
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
        return scope

    def read_scope_by_claim(
        self,
        *,
        claim_id: str,
        session_id: str,
    ) -> RagV2BundleScope:
        """opaque claim/session만으로 owner-bound scope를 읽어 owner ID wire 전달을 막는다."""

        rows = self._execute(
            """
            SELECT *
            FROM public.read_rag_v2_retrieval_scope_by_claim(%s, %s)
            """,
            (claim_id, session_id),
        )
        scope = self._scope_from_rows(rows)
        if scope.claim_id != claim_id or scope.session_id != session_id:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
        return scope

    def _scope_from_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> RagV2BundleScope:
        """definer receipt의 exact shape를 one place에서 검증해 scope swap을 닫는다."""

        if len(rows) != 1:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
        row = rows[0]
        try:
            return RagV2BundleScope(
                claim_id=_required_text(row, "scope_claim_id"),
                owner_user_id=_required_text(row, "owner_user_id"),
                session_id=_required_text(row, "session_id"),
                exact30_generation_id=_required_text(row, "exact30_generation_id"),
                oa112_generation_id=_required_text(row, "oa112_generation_id"),
                owner_private_generation_id=_optional_text(
                    row, "owner_private_generation_id"
                ),
                embedding_profile_id=_required_text(row, "embedding_profile_id"),
                policy_version=_required_int(row, "policy_version"),
                allowed_topics=_required_text_array(row, "allowed_topics"),
            )
        except (TypeError, ValueError) as error:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT") from error

    def retrieve_exact(
        self,
        *,
        scope: RagV2BundleScope,
        query: NormalizedRetrievalQuery,
        identifiers: tuple[str, ...],
    ) -> RagV2ChannelResult:
        """식별자가 있을 때만 exact channel을 top-30 bounded definer function으로 읽는다."""

        if not identifiers:
            # Symbol/ID가 없는 일반 질문은 exact channel의 정상 empty receipt다.
            return RagV2ChannelResult(channel="exact", items=(), complete=True)
        if (
            len(identifiers) > 20
            or len(set(identifiers)) != len(identifiers)
            or any(
                not isinstance(identifier, str)
                or not 1 <= len(identifier) <= 256
                or any(character.isspace() and character in "\r\n" for character in identifier)
                or "\x00" in identifier
                for identifier in identifiers
            )
        ):
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ARGUMENT")
        rows = self._execute(
            """
            SELECT *
            FROM public.search_authorized_rag_v2_exact(%s, %s, %s, %s, %s)
            """,
            (
                scope.claim_id,
                scope.owner_user_id,
                scope.session_id,
                list(_effective_topics(scope, query)),
                list(identifiers),
            ),
        )
        return RagV2ChannelResult(
            channel="exact", items=self._map_rows(rows, scope=scope), complete=True
        )

    def retrieve_lexical(
        self,
        *,
        scope: RagV2BundleScope,
        query: NormalizedRetrievalQuery,
    ) -> RagV2ChannelResult:
        """정규화된 lexical query만 pg_trgm channel에 보내며 source scope는 DB가 먼저 거른다."""

        rows = self._execute(
            """
            SELECT *
            FROM public.search_authorized_rag_v2_lexical(%s, %s, %s, %s, %s)
            """,
            (
                scope.claim_id,
                scope.owner_user_id,
                scope.session_id,
                list(_effective_topics(scope, query)),
                query.lexical_query,
            ),
        )
        return RagV2ChannelResult(
            channel="lexical", items=self._map_rows(rows, scope=scope), complete=True
        )

    def retrieve_dense(
        self,
        *,
        scope: RagV2BundleScope,
        query: NormalizedRetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> RagV2ChannelResult:
        """1024차원 unit local vector만 pgvector cosine channel에 전달한다."""

        vector = _validated_vector_text(query_vector)
        rows = self._execute(
            """
            SELECT *
            FROM public.search_authorized_rag_v2_dense(%s, %s, %s, %s, %s::vector)
            """,
            (
                scope.claim_id,
                scope.owner_user_id,
                scope.session_id,
                list(_effective_topics(scope, query)),
                vector,
            ),
        )
        return RagV2ChannelResult(
            channel="dense", items=self._map_rows(rows, scope=scope), complete=True
        )

    def _execute(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> Sequence[Mapping[str, Any]]:
        """짧은 transaction에서 role/grant를 attest한 뒤 named function만 실행한다."""

        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=1,
                row_factory=dict_row,
            ) as connection:
                with connection.transaction():
                    self._attest_query_connection(connection)
                    # 7,871개 public vector의 첫 exact cosine scan은 cold cache에서 1.5초를 넘을 수 있다.
                    # claim/row cap은 그대로 두고 query usage lease와 같은 5초 안에서만 완료를 허용한다.
                    connection.execute("SET LOCAL statement_timeout = '5s'")
                    connection.execute("SET LOCAL lock_timeout = '250ms'")
                    connection.execute(
                        "SET LOCAL idle_in_transaction_session_timeout = '5s'"
                    )
                    rows = connection.execute(statement, parameters).fetchall()
        except RagV2AuthorizedRetrievalAdapterError:
            raise
        except (OSError, TimeoutError, psycopg.Error) as error:
            # DSN, SQL, role, provider payload와 PostgreSQL 원문은 API/log 경계로 전달하지 않는다.
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_UNAVAILABLE") from error
        if not isinstance(rows, list) or len(rows) > INTERNAL_CHANNEL_LIMIT:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
        if not all(isinstance(row, Mapping) for row in rows):
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
        return rows

    def _attest_query_connection(self, connection: Any) -> None:
        """definer function 외 direct table capability가 없음을 매 call 전에 확인한다."""

        role_row = connection.execute("SELECT current_user").fetchone()
        if _scalar_text(role_row) != _QUERY_ROLE:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ROLE")
        direct_read_row = connection.execute(
            """
            SELECT bool_or(has_table_privilege(current_user, table_name::regclass, 'SELECT'))
            FROM unnest(%s::text[]) AS blocked_tables(table_name)
            """,
            (list(_DIRECT_READ_TABLES),),
        ).fetchone()
        if _scalar_bool(direct_read_row) is not False:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_PRIVILEGE")
        function_grant_row = connection.execute(
            """
            SELECT bool_and(
              has_function_privilege(current_user, function_name::regprocedure, 'EXECUTE')
            )
            FROM unnest(%s::text[]) AS required_functions(function_name)
            """,
            (list(_REQUIRED_FUNCTIONS),),
        ).fetchone()
        if _scalar_bool(function_grant_row) is not True:
            raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_PRIVILEGE")

    def _map_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        scope: RagV2BundleScope,
    ) -> tuple[RagV2RetrievalCandidate, ...]:
        candidates: list[RagV2RetrievalCandidate] = []
        for expected_rank, row in enumerate(rows, start=1):
            if _required_int(row, "rank_no") != expected_rank:
                raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
            try:
                candidate = RagV2RetrievalCandidate(
                    canonical_content=_required_text(row, "canonical_content"),
                    canonical_content_sha256=_required_text(
                        row, "canonical_content_sha256"
                    ),
                    canonical_https_url=_optional_text(row, "canonical_https_url"),
                    chunk_id=_required_text(row, "chunk_id"),
                    document_id=_optional_text(row, "document_id"),
                    embedding_profile_id=_required_text(row, "embedding_profile_id"),
                    external_processing_eligible=_required_bool(
                        row, "external_processing_eligible"
                    ),
                    generation_id=_required_text(row, "generation_id"),
                    heading_path=_required_text_array(row, "heading_path"),
                    locator=_required_mapping(row, "locator"),
                    owner_user_id=_optional_text(row, "candidate_owner_user_id"),
                    policy_version=_required_int(row, "policy_version"),
                    sanitized_display_name=_optional_text(
                        row, "sanitized_display_name"
                    ),
                    scope_claim_id=_required_text(row, "scope_claim_id"),
                    session_id=_required_text(row, "session_id"),
                    source_id=_required_text(row, "source_id"),
                    source_revision_id=_required_text(row, "source_revision_id"),
                    source_scope=_required_text(row, "source_scope"),
                    title=_optional_text(row, "citation_title"),
                    topics=_required_text_array(row, "retrieval_topics"),
                )
            except (TypeError, ValueError) as error:
                raise RagV2AuthorizedRetrievalAdapterError(
                    "RAG_V2_QUERY_RECEIPT"
                ) from error
            if not _candidate_receipt_matches(scope, candidate):
                raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
            candidates.append(candidate)
        return tuple(candidates)


def _effective_topics(
    scope: RagV2BundleScope,
    query: NormalizedRetrievalQuery,
) -> tuple[str, ...]:
    topics = query.topics or scope.allowed_topics
    if (
        not topics
        or len(set(topics)) != len(topics)
        or not set(topics) <= set(scope.allowed_topics)
    ):
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ARGUMENT")
    return topics


def _validated_vector_text(vector: Sequence[float]) -> str:
    if isinstance(vector, (bytes, str)) or len(vector) != EMBEDDING_DIMENSION:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ARGUMENT")
    try:
        values = tuple(float(value) for value in vector)
    except (TypeError, ValueError) as error:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ARGUMENT") from error
    if not all(math.isfinite(value) for value in values):
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ARGUMENT")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.00001:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_ARGUMENT")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _candidate_receipt_matches(
    scope: RagV2BundleScope,
    candidate: RagV2RetrievalCandidate,
) -> bool:
    expected_generation_id = {
        "EXACT30": scope.exact30_generation_id,
        "OA112": scope.oa112_generation_id,
        "OWNER_PRIVATE": scope.owner_private_generation_id,
    }.get(candidate.source_scope)
    if (
        candidate.source_scope not in _SOURCE_SCOPES
        or expected_generation_id is None
        or candidate.scope_claim_id != scope.claim_id
        or candidate.session_id != scope.session_id
        or candidate.generation_id != expected_generation_id
        or candidate.embedding_profile_id != scope.embedding_profile_id
        or candidate.policy_version != scope.policy_version
        or not candidate.topics
        or len(set(candidate.topics)) != len(candidate.topics)
        or not set(candidate.topics) <= ALLOWED_RAG_TOPICS
        or not set(candidate.topics).intersection(scope.allowed_topics)
    ):
        return False
    if candidate.source_scope in {"EXACT30", "OA112"}:
        return candidate.owner_user_id is None
    return candidate.owner_user_id == scope.owner_user_id


def _required_text(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
    return value


def _optional_text(mapping: Mapping[str, Any], field: str) -> str | None:
    value = mapping.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
    return value


def _required_int(mapping: Mapping[str, Any], field: str) -> int:
    value = mapping.get(field)
    if type(value) is not int:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
    return value


def _required_bool(mapping: Mapping[str, Any], field: str) -> bool:
    value = mapping.get(field)
    if type(value) is not bool:
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
    return value


def _required_text_array(mapping: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = mapping.get(field)
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
    return tuple(value)


def _required_mapping(mapping: Mapping[str, Any], field: str) -> Mapping[str, object]:
    value = mapping.get(field)
    if not isinstance(value, Mapping):
        raise RagV2AuthorizedRetrievalAdapterError("RAG_V2_QUERY_RECEIPT")
    return dict(value)


def _scalar_text(row: object) -> str | None:
    if isinstance(row, Mapping):
        values = tuple(row.values())
    elif isinstance(row, tuple):
        values = row
    else:
        return None
    return values[0] if len(values) == 1 and isinstance(values[0], str) else None


def _scalar_bool(row: object) -> bool | None:
    if isinstance(row, Mapping):
        values = tuple(row.values())
    elif isinstance(row, tuple):
        values = row
    else:
        return None
    return values[0] if len(values) == 1 and type(values[0]) is bool else None
