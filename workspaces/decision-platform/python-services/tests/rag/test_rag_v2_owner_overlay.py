from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.rag.rag_v2_owner_overlay import (
    OwnerOverlayError,
    PsycopgRagV2OwnerOverlayRepository,
)


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

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> _Cursor:
        self.statements.append((statement, parameters))
        if "SELECT current_user" in statement:
            return _Cursor(("decision_rag_admin",))
        if "has_table_privilege" in statement:
            return _Cursor((False,))
        if "has_function_privilege" in statement:
            return _Cursor((True,))
        if "prepare_rag_v2_immutable_owner_overlay" in statement:
            bundle_id = "rgb_" + "a" * 32
            return _Cursor(
                (
                    bundle_id,
                    "rgr_" + "b" * 32,
                    bundle_id if self.already_active else None,
                    1 if self.already_active else 0,
                    2,
                    6,
                )
            )
        if "activate_rag_v2_immutable_owner_bundle" in statement:
            return _Cursor((1,))
        return _Cursor()


def test_overlay_repository_uses_only_admin_definer_functions_and_activates_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "app.rag.rag_v2_owner_overlay.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )

    receipt = PsycopgRagV2OwnerOverlayRepository(database_dsn="postgresql://admin").prepare_and_activate(
        owner_user_id="usr_demo_user"
    )

    assert receipt.state == "READY"
    assert receipt.source_count == 2
    assert receipt.chunk_count == 6
    assert sum("activate_rag_v2_immutable_owner_bundle" in statement for statement, _ in connection.statements) == 1
    assert all("rag_v2_immutable_source_revisions" not in statement for statement, _ in connection.statements)


def test_overlay_repository_reuses_the_already_active_exact_bundle_without_second_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(already_active=True)
    monkeypatch.setattr(
        "app.rag.rag_v2_owner_overlay.psycopg.connect",
        lambda *_args, **_kwargs: connection,
    )

    receipt = PsycopgRagV2OwnerOverlayRepository(database_dsn="postgresql://admin").prepare_and_activate(
        owner_user_id="usr_demo_user"
    )

    assert receipt.state == "READY"
    assert not any("activate_rag_v2_immutable_owner_bundle" in statement for statement, _ in connection.statements)


def test_overlay_repository_rejects_invalid_owner_before_database_access() -> None:
    repository = PsycopgRagV2OwnerOverlayRepository(database_dsn="postgresql://admin")

    with pytest.raises(OwnerOverlayError, match="OWNER_OVERLAY_ARGUMENT"):
        repository.prepare_and_activate(owner_user_id="usr_BAD")
