from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.rag.authorized_retrieval import QueryNormalizer
from app.rag.rag_v2_authorized_retrieval_adapter import (
    PsycopgRagV2AuthorizedRetrievalAdapter,
    RagV2AuthorizedRetrievalAdapterError,
)


class _Cursor:
    def __init__(self, *, one: tuple[object, ...] | None = None, rows: list[dict[str, object]] | None = None) -> None:
        self._one = one
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> _Cursor:
        self.statements.append((statement, parameters))
        if "SELECT current_user" in statement:
            return _Cursor(one=("decision_rag_query",))
        if "has_table_privilege" in statement:
            return _Cursor(one=(False,))
        if "has_function_privilege" in statement:
            return _Cursor(one=(True,))
        if "read_rag_v2_retrieval_scope" in statement:
            return _Cursor(rows=[_scope_row()])
        if "search_authorized_rag_v2_exact" in statement:
            return _Cursor(rows=[_candidate_row(rank=1, source_scope="EXACT30")])
        if "search_authorized_rag_v2_lexical" in statement:
            return _Cursor(rows=[_candidate_row(rank=1, source_scope="OA112")])
        if "search_authorized_rag_v2_dense" in statement:
            return _Cursor(rows=[_candidate_row(rank=1, source_scope="OWNER_PRIVATE")])
        return _Cursor()


def test_adapter_reads_only_query_role_definer_functions_and_maps_tagged_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_authorized_retrieval_adapter.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(database_dsn="postgresql://query")

    scope = adapter.read_scope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_demo_owner",
        session_id="req_v2_retrieval_000000000001",
    )
    query = QueryNormalizer().normalize(
        {"question": "금융공학 근거", "answerMode": "CONCISE", "topics": ["FINANCIAL_ENGINEERING"]}
    )
    exact = adapter.retrieve_exact(scope=scope, query=query, identifiers=("src_v2_fixture_001",))
    lexical = adapter.retrieve_lexical(scope=scope, query=query)
    dense = adapter.retrieve_dense(
        scope=scope,
        query=query,
        query_vector=(1.0,) + (0.0,) * 1023,
        owner_query_vector=(0.0, 1.0) + (0.0,) * 1022,
    )

    assert scope.embedding_profile_id == "voyage_context_4_1024_v1"
    assert scope.owner_embedding_profile_id == "bge_m3_local_1024_v1"
    assert exact.items[0].canonical_https_url == "https://public.example.com/evidence"
    assert lexical.items[0].title == "OA fixture"
    assert dense.items[0].document_id == "doc_owner_document_0001"
    assert dense.items[0].sanitized_display_name == "Owner fixture"
    assert dense.items[0].canonical_https_url is None
    assert all("rag_v2_immutable_source_revisions" not in statement for statement, _ in connection.statements)
    dense_calls = [
        parameters
        for statement, parameters in connection.statements
        if "search_authorized_rag_v2_dense_v2" in statement
    ]
    assert len(dense_calls) == 1
    assert dense_calls[0] is not None
    assert dense_calls[0][-2:] == (
        "[1,0" + ",0" * 1022 + "]",
        "[0,1" + ",0" * 1022 + "]",
    )


def test_adapter_resolves_the_opaque_scope_without_putting_owner_id_in_the_python_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_authorized_retrieval_adapter.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(database_dsn="postgresql://query")

    scope = adapter.read_scope_by_claim(
        claim_id="rvs_" + "a" * 32,
        session_id="req_v2_retrieval_000000000001",
    )

    opaque_calls = [
        parameters
        for statement, parameters in connection.statements
        if "read_rag_v2_retrieval_scope_by_claim" in statement
    ]
    assert scope.owner_user_id == "usr_demo_owner"
    assert opaque_calls == [
        ("rvs_" + "a" * 32, "req_v2_retrieval_000000000001")
    ]
    assert all("rag_v2_retrieval_scope_claims" not in statement for statement, _ in connection.statements)


def test_adapter_allows_the_bounded_five_second_cold_dense_search_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7,871-vector cold scan은 one-shot provider 성공 뒤 1.5초에 잘리면 안 된다."""

    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_authorized_retrieval_adapter.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(database_dsn="postgresql://query")

    adapter.read_scope_by_claim(
        claim_id="rvs_" + "a" * 32,
        session_id="req_v2_retrieval_000000000001",
    )

    assert ("SET LOCAL statement_timeout = '5s'", None) in connection.statements


def test_adapter_rejects_an_oversized_or_scope_drifting_row(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_authorized_retrieval_adapter.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )
    adapter = PsycopgRagV2AuthorizedRetrievalAdapter(database_dsn="postgresql://query")
    scope = adapter.read_scope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_demo_owner",
        session_id="req_v2_retrieval_000000000001",
    )
    query = QueryNormalizer().normalize({"question": "근거", "answerMode": "CONCISE"})
    connection.execute = lambda statement, parameters=None: _bad_cursor(statement)  # type: ignore[method-assign]

    with pytest.raises(RagV2AuthorizedRetrievalAdapterError, match="RAG_V2_QUERY_RECEIPT"):
        adapter.retrieve_exact(scope=scope, query=query, identifiers=("src_v2_fixture_001",))


def _bad_cursor(statement: str) -> _Cursor:
    if "SELECT current_user" in statement:
        return _Cursor(one=("decision_rag_query",))
    if "has_table_privilege" in statement:
        return _Cursor(one=(False,))
    if "has_function_privilege" in statement:
        return _Cursor(one=(True,))
    if "search_authorized_rag_v2_exact" in statement:
        return _Cursor(rows=[_candidate_row(rank=2, source_scope="EXACT30")])
    return _Cursor()


def _scope_row() -> dict[str, object]:
    return {
        "scope_claim_id": "rvs_" + "a" * 32,
        "owner_user_id": "usr_demo_owner",
        "session_id": "req_v2_retrieval_000000000001",
        "allowed_topics": ["FINANCIAL_ENGINEERING", "RISK"],
        "exact30_generation_id": "rgr_" + "1" * 32,
        "oa112_generation_id": "rgr_" + "2" * 32,
        "owner_private_generation_id": "rgr_" + "3" * 32,
        "embedding_profile_id": "voyage_context_4_1024_v1",
        "owner_embedding_profile_id": "bge_m3_local_1024_v1",
        "policy_version": 1,
    }


def _candidate_row(*, rank: int, source_scope: str) -> dict[str, object]:
    content = "Canonical evidence text."
    public = source_scope != "OWNER_PRIVATE"
    return {
        "rank_no": rank,
        "canonical_content": content,
        "canonical_content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "canonical_https_url": "https://public.example.com/evidence" if public else None,
        "chunk_id": "rag_v2_chk_" + "c" * 32,
        "document_id": None if public else "doc_owner_document_0001",
        "embedding_profile_id": (
            "bge_m3_local_1024_v1"
            if source_scope == "OWNER_PRIVATE"
            else "voyage_context_4_1024_v1"
        ),
        "external_processing_eligible": public,
        "generation_id": "rgr_" + ({"EXACT30": "1", "OA112": "2", "OWNER_PRIVATE": "3"}[source_scope] * 32),
        "heading_path": ["Evidence"],
        "locator": {"section": "Evidence"},
        "candidate_owner_user_id": None if public else "usr_demo_owner",
        "policy_version": 1,
        "sanitized_display_name": None if public else "Owner fixture",
        "scope_claim_id": "rvs_" + "a" * 32,
        "session_id": "req_v2_retrieval_000000000001",
        "source_id": "src_v2_fixture_001",
        "source_revision_id": "srv_v2_fixture_001",
        "source_scope": source_scope,
        "citation_title": "OA fixture" if source_scope == "OA112" else ("Exact fixture" if public else None),
        "retrieval_topics": ["FINANCIAL_ENGINEERING"],
    }
