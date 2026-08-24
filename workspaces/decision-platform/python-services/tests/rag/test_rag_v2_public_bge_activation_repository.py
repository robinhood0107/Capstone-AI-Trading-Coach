from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from app.rag.rag_v2_public_bge_activation_repository import (
    PsycopgRagV2PublicBgeActivationRepository,
    PublicBgeActivationError,
    PublicBgeActivationRequest,
)
from app.rag.rag_v2_public_bge_staging import RagV2PublicBgeComponentContext


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _Connection:
    def __init__(self, *, already_active: bool = False) -> None:
        self.already_active = already_active
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] | None = None,
    ) -> _Cursor:
        self.statements.append((statement, parameters))
        if "SELECT current_user" in statement:
            return _Cursor(("decision_rag_admin",))
        if "has_table_privilege" in statement:
            return _Cursor((False,))
        if "has_function_privilege" in statement:
            return _Cursor((True,))
        if "prepare_rag_v2_immutable_public_base_activation" in statement:
            return _Cursor((7, not self.already_active))
        if "activate_rag_v2_immutable_public_base" in statement:
            return _Cursor((8,))
        return _Cursor()


def test_public_activation_uses_same_transaction_definer_prepare_and_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_public_bge_activation_repository.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )

    receipt = PsycopgRagV2PublicBgeActivationRepository(
        database_dsn="postgresql://rag-admin"
    ).activate(request=_request())

    assert receipt.state == "ACTIVE"
    assert receipt.previous_pointer_version == 7
    assert receipt.new_pointer_version == 8
    assert (
        sum(
            "prepare_rag_v2_immutable_public_base_activation" in statement
            for statement, _ in connection.statements
        )
        == 1
    )
    assert (
        sum(
            "activate_rag_v2_immutable_public_base" in statement
            for statement, _ in connection.statements
        )
        == 1
    )
    assert all(
        "rag_v2_immutable_source_revisions" not in statement
        for statement, _ in connection.statements
    )


def test_public_activation_is_idempotent_for_the_same_active_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(already_active=True)
    monkeypatch.setattr(
        "app.rag.rag_v2_public_bge_activation_repository.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )

    receipt = PsycopgRagV2PublicBgeActivationRepository(
        database_dsn="postgresql://rag-admin"
    ).activate(request=_request())

    assert receipt.state == "ACTIVE"
    assert receipt.previous_pointer_version == receipt.new_pointer_version == 7
    assert not any(
        "activate_rag_v2_immutable_public_base(" in statement
        for statement, _ in connection.statements
    )


def test_public_activation_rejects_incomplete_or_profile_drifted_context_before_database_access() -> (
    None
):
    request = _request()
    invalid_exact = RagV2PublicBgeComponentContext(
        component_scope="EXACT30",
        component_generation_id=request.exact30.component_generation_id,
        materialization_run_id=request.exact30.materialization_run_id,
        generation_hash=request.exact30.generation_hash,
        manifest_hash=request.exact30.manifest_hash,
        expected_source_count=29,
        expected_chunk_count=request.exact30.expected_chunk_count,
        embedding_profile_id=request.exact30.embedding_profile_id,
        member_digests=request.exact30.member_digests,
    )

    with pytest.raises(PublicBgeActivationError, match="PUBLIC_BGE_ACTIVATION_ARGUMENT"):
        PsycopgRagV2PublicBgeActivationRepository(database_dsn="postgresql://rag-admin").activate(
            request=PublicBgeActivationRequest(exact30=invalid_exact, oa112=request.oa112)
        )


def test_public_activation_maps_pointer_cas_conflict_without_sql_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ConflictConnection(_Connection):
        def execute(
            self,
            statement: str,
            parameters: tuple[object, ...] | None = None,
        ) -> _Cursor:
            if "activate_rag_v2_immutable_public_base" in statement:
                raise psycopg.errors.SerializationFailure()
            return super().execute(statement, parameters)

    monkeypatch.setattr(
        "app.rag.rag_v2_public_bge_activation_repository.psycopg.connect",
        lambda *_args, **_kwargs: _ConflictConnection(),
    )

    with pytest.raises(PublicBgeActivationError, match="PUBLIC_BGE_ACTIVATION_CONFLICT"):
        PsycopgRagV2PublicBgeActivationRepository(database_dsn="postgresql://rag-admin").activate(
            request=_request()
        )


def _request() -> PublicBgeActivationRequest:
    return PublicBgeActivationRequest(
        exact30=_context("EXACT30", 30, "a"),
        oa112=_context("OA112", 112, "b"),
    )


def _context(
    scope: str,
    source_count: int,
    marker: str,
) -> RagV2PublicBgeComponentContext:
    member_digests = tuple(
        hashlib.sha256(f"{scope}|{marker}|{index}".encode()).hexdigest()
        for index in range(source_count)
    )
    manifest_hash = hashlib.sha256(f"{scope}|manifest|{marker}".encode()).hexdigest()
    generation_hash = hashlib.sha256(f"{scope}|generation|{marker}".encode()).hexdigest()
    component_generation_id = f"rgr_{generation_hash[:32]}"
    materialization_run_id = (
        "rgr_run_"
        + hashlib.sha256(
            f"rag-v2-public-bge-run|{component_generation_id}|{manifest_hash}".encode()
        ).hexdigest()[:32]
    )
    return RagV2PublicBgeComponentContext(
        component_scope=scope,  # type: ignore[arg-type]
        component_generation_id=component_generation_id,
        materialization_run_id=materialization_run_id,
        generation_hash=generation_hash,
        manifest_hash=manifest_hash,
        expected_source_count=source_count,
        expected_chunk_count=source_count,
        embedding_profile_id="bge_m3_local_1024_v1",
        member_digests=member_digests,
    )
