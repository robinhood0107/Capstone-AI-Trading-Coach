from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag import rag_v2_local_import_control as local_import_control

from app.rag.rag_v2_local_import_control import (
    RagV2LocalDeleteControlError,
    RagV2LocalImportControlError,
    RagV2OwnerDeleteControl,
    RagV2OwnerImportControl,
    load_pending_owner_delete_control,
    load_pending_owner_import_control,
    write_pending_owner_delete_control,
    write_pending_owner_import_control,
)


def test_local_import_control_round_trip_is_private_and_argv_free(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    control = _control(tmp_path)

    write_pending_owner_import_control(local_root=tmp_path, control=control)
    loaded = load_pending_owner_import_control(
        local_root=tmp_path,
        now=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
    )

    assert loaded == control
    assert loaded.embedding_profile_id == "voyage_context_4_1024_v1"
    record = tmp_path / "control" / "owner-import.json"
    assert record.stat().st_mode & 0o777 == 0o600
    assert "private.pdf" not in json.dumps(loaded.content_free_summary())
    assert "Owner fixture" not in json.dumps(loaded.content_free_summary())
    assert "rti_" not in json.dumps(loaded.content_free_summary())
    assert "usr_" not in json.dumps(loaded.content_free_summary())


def test_local_import_control_allows_access_time_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_root(tmp_path)
    control = _control(tmp_path)
    write_pending_owner_import_control(local_root=tmp_path, control=control)
    original_boundary = local_import_control._assert_control_record_boundary
    boundary_calls = 0

    def boundary_with_read_atime_change(local_root: Path, *, filename: str) -> object:
        nonlocal boundary_calls
        metadata = original_boundary(local_root, filename=filename)
        boundary_calls += 1
        if boundary_calls != 2:
            return metadata
        values = {
            field: getattr(metadata, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_gid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values["st_atime_ns"] = metadata.st_atime_ns + 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(
        local_import_control,
        "_assert_control_record_boundary",
        boundary_with_read_atime_change,
    )

    loaded = load_pending_owner_import_control(
        local_root=tmp_path,
        now=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
    )

    assert loaded == control


def test_local_import_control_rejects_expiry_and_shared_record_mode(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    write_pending_owner_import_control(local_root=tmp_path, control=_control(tmp_path))
    record = tmp_path / "control" / "owner-import.json"

    with pytest.raises(RagV2LocalImportControlError, match="LOCAL_IMPORT_CONTROL_EXPIRED"):
        load_pending_owner_import_control(
            local_root=tmp_path,
            now=datetime(2026, 8, 3, 0, 6, tzinfo=UTC),
        )

    os.chmod(record, 0o640)
    with pytest.raises(RagV2LocalImportControlError, match="LOCAL_IMPORT_CONTROL_BOUNDARY"):
        load_pending_owner_import_control(
            local_root=tmp_path,
            now=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
        )


def test_local_import_control_closed_shape_rejects_direct_database_or_path_aliases(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    write_pending_owner_import_control(local_root=tmp_path, control=_control(tmp_path))
    record = tmp_path / "control" / "owner-import.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["databaseDsn"] = "postgresql://must-not-be-here"
    record.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(record, 0o600)

    with pytest.raises(RagV2LocalImportControlError, match="LOCAL_IMPORT_CONTROL_INVALID"):
        load_pending_owner_import_control(
            local_root=tmp_path,
            now=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
        )


def test_local_import_control_requires_one_explicit_supported_library_profile(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    control = _control(tmp_path)

    with pytest.raises(RagV2LocalImportControlError, match="LOCAL_IMPORT_CONTROL_INVALID"):
        write_pending_owner_import_control(
            local_root=tmp_path,
            control=replace(control, embedding_profile_id="arbitrary_profile"),
        )


def test_local_delete_control_round_trip_is_private_and_short_lived(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, tzinfo=UTC)
    control = RagV2OwnerDeleteControl(
        owner_user_id="usr_demo_user",
        document_id="doc_owner_delete_0001",
        delete_ticket_id="rtd_11111111111111111111111111111111",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    write_pending_owner_delete_control(local_root=tmp_path, control=control)
    loaded = load_pending_owner_delete_control(
        local_root=tmp_path,
        now=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
    )

    assert loaded == control
    record = tmp_path / "control" / "owner-delete.json"
    assert record.stat().st_mode & 0o777 == 0o600
    summary = json.dumps(loaded.content_free_summary())
    assert "usr_" not in summary
    assert "doc_" not in summary
    assert "rtd_" not in summary


def test_local_delete_control_rejects_database_alias_and_expiry(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, tzinfo=UTC)
    control = RagV2OwnerDeleteControl(
        owner_user_id="usr_demo_user",
        document_id="doc_owner_delete_0001",
        delete_ticket_id="rtd_11111111111111111111111111111111",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    write_pending_owner_delete_control(local_root=tmp_path, control=control)
    record = tmp_path / "control" / "owner-delete.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["databaseDsn"] = "postgresql://must-not-be-here"
    record.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(record, 0o600)

    with pytest.raises(RagV2LocalDeleteControlError, match="LOCAL_DELETE_CONTROL_INVALID"):
        load_pending_owner_delete_control(
            local_root=tmp_path,
            now=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
        )

    write_pending_owner_delete_control(local_root=tmp_path, control=control)
    with pytest.raises(RagV2LocalDeleteControlError, match="LOCAL_DELETE_CONTROL_EXPIRED"):
        load_pending_owner_delete_control(
            local_root=tmp_path,
            now=datetime(2026, 8, 3, 0, 6, tzinfo=UTC),
        )


def _secure_root(root: Path) -> None:
    os.chmod(root, 0o700)


def _control(root: Path) -> RagV2OwnerImportControl:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    source_root = root / "documents"
    source_root.mkdir(mode=0o700)
    return RagV2OwnerImportControl(
        owner_user_id="usr_demo_user",
        import_ticket_id="rti_11111111111111111111111111111111",
        approved_root=source_root,
        relative_path="private.pdf",
        document_id="doc_owner_control_0001",
        source_id="src_owner_control_001",
        source_revision_id="srv_owner_control_001",
        language_tags=("en",),
        sanitized_display_name="Owner fixture",
        retrieval_topics=("FINANCIAL_ENGINEERING",),
        embedding_profile_id="voyage_context_4_1024_v1",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
