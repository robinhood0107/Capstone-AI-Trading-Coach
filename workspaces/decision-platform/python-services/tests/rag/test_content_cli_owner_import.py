from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag import content_cli
from app.rag.rag_v2_local_import_control import RagV2OwnerImportControl
from app.rag.rag_v2_owner_bge_staging import RagV2OwnerBgeStagingReceipt


def test_windows_import_wrappers_do_not_forward_raw_arguments_to_powershell_or_python() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    windows_tools = repository_root / "capstone-rag" / "tools" / "windows"

    for name in (
        "rag-import-auto.bat",
        "rag-import-cpu.bat",
        "rag-import-intel-gpu.bat",
        "rag-import-nvidia-gpu.bat",
    ):
        script = (windows_tools / name).read_text(encoding="utf-8")
        assert "%*" not in script
        assert "CONTENT_COMMAND_INVALID" in script

    powershell = (windows_tools / "rag-content.ps1").read_text(encoding="utf-8")
    assert "IMPORT_ARGUMENTS_FORBIDDEN" in powershell
    assert "python -m app.rag.content_cli $Command @RemainingArguments" in powershell


def test_import_command_uses_local_control_and_never_echoes_private_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    control = _control(tmp_path)
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://private-dsn")
    monkeypatch.setattr(content_cli, "load_pending_owner_import_control", lambda **_: control)
    monkeypatch.setattr(content_cli, "_materialize_owner_import", lambda **_: object())

    class _Repository:
        def __init__(self, *, database_dsn: str) -> None:
            assert database_dsn == "postgresql://private-dsn"

        def stage(self, **_: object) -> RagV2OwnerBgeStagingReceipt:
            return RagV2OwnerBgeStagingReceipt(
                owner_user_id="usr_demo_user",
                component_generation_id="rgr_11111111111111111111111111111111",
                materialization_run_id="rgr_run_11111111111111111111111111111111",
                component_scope="OWNER_PRIVATE",
                embedding_profile_id="bge_m3_local_1024_v1",
                state="STAGED",
                source_count=1,
                chunk_count=3,
            )

    monkeypatch.setattr(content_cli, "PsycopgRagV2OwnerBgeStagingRepository", _Repository)

    assert content_cli.main(["import-cpu"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "chunkCount": 3,
        "code": "OWNER_DOCUMENT_STAGED",
        "componentGenerationId": "rgr_11111111111111111111111111111111",
        "embeddingProfileId": "bge_m3_local_1024_v1",
        "materializationRunId": "rgr_run_11111111111111111111111111111111",
        "state": "STAGED",
    }
    encoded = json.dumps(value)
    assert "private.pdf" not in encoded
    assert str(tmp_path) not in encoded
    assert "rti_" not in encoded
    assert "usr_" not in encoded
    assert "private-dsn" not in encoded


@pytest.mark.parametrize(
    "arguments",
    [
        ["import-auto", "C:/owner/private.pdf"],
        ["import-cpu", "C:/owner/private.pdf"],
        ["import-intel-gpu", "C:/owner/private.pdf"],
        ["import-nvidia-gpu", "C:/owner/private.pdf"],
    ],
)
def test_import_command_rejects_raw_path_argv(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert content_cli.main(arguments) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "CONTENT_COMMAND_INVALID",
        "state": "FAILED",
    }


def _control(root: Path) -> RagV2OwnerImportControl:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    return RagV2OwnerImportControl(
        owner_user_id="usr_demo_user",
        import_ticket_id="rti_11111111111111111111111111111111",
        approved_root=root,
        relative_path="private.pdf",
        document_id="doc_owner_cli_control_001",
        source_id="src_owner_cli_control_001",
        source_revision_id="srv_owner_cli_control_001",
        language_tags=("en",),
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
