from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from app.rag.rag_v2_owner_bge_deletion import (
    OwnerBgeDeletionError,
    PsycopgRagV2OwnerBgeDeletionRepository,
)


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self, *, active_document: bool = False) -> None:
        self.active_document = active_document
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
            return _Cursor(("decision_rag_admin",))
        if "has_table_privilege" in statement:
            return _Cursor((False,))
        if "has_function_privilege" in statement:
            return _Cursor((True,))
        if "replace_and_delete_rag_v2_immutable_owner_document" in statement:
            return _Cursor((True,))
        if "delete_rag_v2_immutable_owner_document" in statement:
            if self.active_document:
                raise psycopg.errors.CheckViolation()
            return _Cursor((True,))
        return _Cursor()


def test_deletion_repository_uses_direct_definer_delete_for_unreferenced_staging_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_owner_bge_deletion.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )

    receipt = PsycopgRagV2OwnerBgeDeletionRepository(
        database_dsn="postgresql://admin"
    ).delete(
        owner_user_id="usr_demo_user",
        document_id="doc_owner_delete_0001",
    )

    assert receipt.state == "DELETED"
    assert sum("delete_rag_v2_immutable_owner_document" in statement for statement, _ in connection.statements) == 1
    assert not any(
        "replace_and_delete_rag_v2_immutable_owner_document" in statement
        for statement, _ in connection.statements
    )
    assert all("rag_v2_immutable_source_revisions" not in statement for statement, _ in connection.statements)


def test_deletion_repository_uses_atomic_replacement_function_when_active_document_blocks_direct_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(active_document=True)
    monkeypatch.setattr(
        "app.rag.rag_v2_owner_bge_deletion.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )

    receipt = PsycopgRagV2OwnerBgeDeletionRepository(
        database_dsn="postgresql://admin"
    ).delete(
        owner_user_id="usr_demo_user",
        document_id="doc_owner_delete_0001",
    )

    assert receipt.state == "DELETED"
    assert sum("delete_rag_v2_immutable_owner_document" in statement for statement, _ in connection.statements) == 2
    assert sum(
        "replace_and_delete_rag_v2_immutable_owner_document" in statement
        for statement, _ in connection.statements
    ) == 1
    assert all("rag_v2_immutable_source_revisions" not in statement for statement, _ in connection.statements)


def test_deletion_repository_rejects_invalid_owner_or_document_before_database_access() -> None:
    repository = PsycopgRagV2OwnerBgeDeletionRepository(database_dsn="postgresql://admin")

    with pytest.raises(OwnerBgeDeletionError, match="OWNER_BGE_DELETE_ARGUMENT"):
        repository.delete(owner_user_id="usr_BAD", document_id="doc_owner_delete_0001")
