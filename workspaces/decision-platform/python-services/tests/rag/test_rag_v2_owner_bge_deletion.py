from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
            return _Cursor(("decision_rag_admin",))
        if "has_table_privilege" in statement:
            return _Cursor((False,))
        if "has_function_privilege" in statement:
            assert parameters is not None
            signature = str(parameters[0])
            return _Cursor(("with_ticket" in signature,))
        if "delete_rag_v2_immutable_owner_document_with_ticket" in statement:
            return _Cursor((True,))
        return _Cursor()


def test_deletion_repository_uses_one_ticket_bound_atomic_wrapper(
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
        delete_ticket_id="rtd_11111111111111111111111111111111",
    )

    assert receipt.state == "DELETED"
    wrapper_calls = [
        (statement, parameters)
        for statement, parameters in connection.statements
        if "SELECT public.delete_rag_v2_immutable_owner_document_with_ticket(" in statement
    ]
    assert len(wrapper_calls) == 1
    assert wrapper_calls[0][1] is not None
    assert wrapper_calls[0][1][:3] == (
        "usr_demo_user",
        "doc_owner_delete_0001",
        "rtd_11111111111111111111111111111111",
    )
    assert not any(
        "SELECT public.delete_rag_v2_immutable_owner_document(" in statement
        or "SELECT public.replace_and_delete_rag_v2_immutable_owner_document(" in statement
        for statement, _ in connection.statements
    )
    assert all("rag_v2_immutable_source_revisions" not in statement for statement, _ in connection.statements)


def test_deletion_repository_rejects_invalid_owner_document_or_ticket_before_database_access() -> None:
    repository = PsycopgRagV2OwnerBgeDeletionRepository(database_dsn="postgresql://admin")

    with pytest.raises(OwnerBgeDeletionError, match="OWNER_BGE_DELETE_ARGUMENT"):
        repository.delete(
            owner_user_id="usr_BAD",
            document_id="doc_owner_delete_0001",
            delete_ticket_id="rtd_11111111111111111111111111111111",
        )
    with pytest.raises(OwnerBgeDeletionError, match="OWNER_BGE_DELETE_ARGUMENT"):
        repository.delete(
            owner_user_id="usr_demo_user",
            document_id="doc_owner_delete_0001",
            delete_ticket_id="rtd_invalid",
        )
