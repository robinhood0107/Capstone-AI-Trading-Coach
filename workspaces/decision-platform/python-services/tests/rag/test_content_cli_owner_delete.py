from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag import content_cli
from app.rag.rag_v2_local_import_control import RagV2OwnerDeleteControl
from app.rag.rag_v2_owner_bge_deletion import RagV2OwnerBgeDeletionReceipt


def test_remove_command_uses_local_control_and_never_echoes_private_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    control = _control()
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "postgresql://private-admin-dsn")
    monkeypatch.setattr(content_cli, "load_pending_owner_delete_control", lambda **_: control)

    class _Repository:
        def __init__(self, *, database_dsn: str) -> None:
            assert database_dsn == "postgresql://private-admin-dsn"

        def delete(self, **kwargs: object) -> RagV2OwnerBgeDeletionReceipt:
            assert kwargs["owner_user_id"] == control.owner_user_id
            assert kwargs["document_id"] == control.document_id
            return RagV2OwnerBgeDeletionReceipt(state="DELETED")

    monkeypatch.setattr(content_cli, "PsycopgRagV2OwnerBgeDeletionRepository", _Repository)

    assert content_cli.main(["remove-document"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {"code": "OWNER_DOCUMENT_DELETED", "state": "DELETED"}
    encoded = json.dumps(value)
    assert "private-admin-dsn" not in encoded
    assert control.owner_user_id not in encoded
    assert control.document_id not in encoded
    assert str(tmp_path) not in encoded


def test_remove_command_rejects_raw_document_argv(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert content_cli.main(["remove-document", "doc_owner_delete_0001"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "CONTENT_COMMAND_INVALID",
        "state": "FAILED",
    }


def _control() -> RagV2OwnerDeleteControl:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    return RagV2OwnerDeleteControl(
        owner_user_id="usr_demo_user",
        document_id="doc_owner_delete_0001",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
