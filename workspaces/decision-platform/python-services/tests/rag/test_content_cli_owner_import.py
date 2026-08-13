from __future__ import annotations

import json
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag import content_cli
from app.rag.pre_s5_provider_control import PreS5ProviderBinding
from app.rag.rag_v2_local_import_control import RagV2OwnerImportControl
from app.rag.rag_v2_owner_bge_staging import (
    OwnerBgeStagingMetadata,
    RagV2OwnerBgeStagingReceipt,
)
from app.rag.rag_v2_owner_overlay import RagV2OwnerOverlayReceipt
from app.rag.rag_v2_owner_voyage_import import OwnerVoyageImportError


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
    monkeypatch.setenv("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "postgresql://private-admin-dsn")
    monkeypatch.setattr(content_cli, "load_pending_owner_import_control", lambda **_: control)
    monkeypatch.setattr(content_cli, "_materialize_owner_import", lambda **_: object())
    monkeypatch.setattr(
        content_cli,
        "_execute_owner_voyage_import",
        lambda **_values: pytest.fail("BGE selection must not call Voyage"),
        raising=False,
    )

    class _Repository:
        def __init__(self, *, database_dsn: str) -> None:
            assert database_dsn == "postgresql://private-dsn"

        def stage(self, **values: object) -> RagV2OwnerBgeStagingReceipt:
            metadata = values["metadata"]
            assert isinstance(metadata, OwnerBgeStagingMetadata)
            assert metadata.sanitized_display_name == "Owner fixture"
            assert metadata.retrieval_topics == ("FINANCIAL_ENGINEERING",)
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

    class _OverlayRepository:
        def __init__(self, *, database_dsn: str) -> None:
            assert database_dsn == "postgresql://private-admin-dsn"

        def prepare_and_activate(self, **values: object) -> RagV2OwnerOverlayReceipt:
            assert values == {"owner_user_id": "usr_demo_user"}
            return RagV2OwnerOverlayReceipt(
                bundle_id="rgb_22222222222222222222222222222222",
                component_generation_id="rgr_22222222222222222222222222222222",
                source_count=1,
                chunk_count=3,
                state="READY",
            )

    monkeypatch.setattr(content_cli, "PsycopgRagV2OwnerOverlayRepository", _OverlayRepository)

    assert content_cli.main(["import-cpu"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "bundleId": "rgb_22222222222222222222222222222222",
        "chunkCount": 3,
        "code": "OWNER_DOCUMENT_READY",
        "componentGenerationId": "rgr_22222222222222222222222222222222",
        "embeddingProfileId": "bge_m3_local_1024_v1",
        "state": "READY",
    }
    encoded = json.dumps(value)
    assert "private.pdf" not in encoded
    assert str(tmp_path) not in encoded
    assert "rti_" not in encoded
    assert "usr_" not in encoded
    assert "private-dsn" not in encoded


def test_voyage_selected_import_never_loads_bge_or_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    control = replace(
        _control(tmp_path),
        embedding_profile_id="voyage_context_4_1024_v1",
    )
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://private-dsn")
    monkeypatch.setenv("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "postgresql://private-admin-dsn")
    monkeypatch.setattr(content_cli, "load_pending_owner_import_control", lambda **_: control)
    monkeypatch.setattr(
        content_cli,
        "load_bge_onnx_embedder",
        lambda *_args, **_kwargs: pytest.fail("Voyage selection must not load BGE"),
    )

    def execute_voyage(**values: object) -> tuple[RagV2OwnerBgeStagingReceipt, RagV2OwnerOverlayReceipt]:
        assert values["control"] == control
        return (
            RagV2OwnerBgeStagingReceipt(
                owner_user_id="usr_demo_user",
                component_generation_id="rgr_11111111111111111111111111111111",
                materialization_run_id="rgr_run_11111111111111111111111111111111",
                component_scope="OWNER_PRIVATE",
                embedding_profile_id="voyage_context_4_1024_v1",
                state="STAGED",
                source_count=1,
                chunk_count=1,
            ),
            RagV2OwnerOverlayReceipt(
                bundle_id="rgb_22222222222222222222222222222222",
                component_generation_id="rgr_22222222222222222222222222222222",
                source_count=1,
                chunk_count=1,
                state="READY",
            ),
        )

    monkeypatch.setattr(content_cli, "_execute_owner_voyage_import", execute_voyage)

    assert content_cli.main(["import-cpu"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["embeddingProfileId"] == "voyage_context_4_1024_v1"
    assert value["state"] == "READY"


def test_owner_voyage_manifest_binds_exact_content_free_plan_and_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _owner_voyage_plan()
    binding = _owner_voyage_binding()
    manifest = _owner_voyage_manifest(plan=plan, binding=binding)
    encoded = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    _write_owner_voyage_manifest(tmp_path, encoded)
    approval = hashlib.sha256(encoded).hexdigest()
    monkeypatch.setenv("PRE_S5_OWNER_VOYAGE_SYNTHETIC_MANIFEST_SHA256", approval)

    observed, decoded = content_cli._load_owner_voyage_manifest(
        local_root=tmp_path,
        plan=plan,
        binding=binding,
    )

    assert observed == approval
    assert decoded == manifest
    assert "usr_" not in encoded.decode()
    assert "rti_" not in encoded.decode()


def test_owner_voyage_manifest_rejects_non_object_without_leaking_parser_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = b"[]"
    _write_owner_voyage_manifest(tmp_path, encoded)
    monkeypatch.setenv(
        "PRE_S5_OWNER_VOYAGE_SYNTHETIC_MANIFEST_SHA256",
        hashlib.sha256(encoded).hexdigest(),
    )

    with pytest.raises(OwnerVoyageImportError, match="OWNER_VOYAGE_MANIFEST_INVALID"):
        content_cli._load_owner_voyage_manifest(
            local_root=tmp_path,
            plan=_owner_voyage_plan(),
            binding=_owner_voyage_binding(),
        )


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
        sanitized_display_name="Owner fixture",
        retrieval_topics=("FINANCIAL_ENGINEERING",),
        embedding_profile_id="bge_m3_local_1024_v1",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _owner_voyage_plan() -> SimpleNamespace:
    return SimpleNamespace(
        plan_sha256="a" * 64,
        owner_scope_sha256="b" * 64,
        ticket_set_sha256="c" * 64,
        tokenizer_sha256="d" * 64,
        items=tuple(object() for _ in range(9)),
        batch=SimpleNamespace(
            batch_manifest_sha256="e" * 64,
            chunk_count=9,
            token_count=90,
        ),
    )


def _owner_voyage_binding() -> PreS5ProviderBinding:
    return PreS5ProviderBinding(
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
    )


def _owner_voyage_manifest(
    *,
    plan: SimpleNamespace,
    binding: PreS5ProviderBinding,
) -> dict[str, object]:
    return {
        "approvalScope": "PRE_S5_OWNER_VOYAGE_SYNTHETIC_ONE_SHOT",
        "batchManifestSha256": plan.batch.batch_manifest_sha256,
        "binding": {
            "ciDigest": binding.ci_digest,
            "headCommit": binding.head_commit,
            "securityDigest": binding.security_digest,
            "treeObject": binding.tree_object,
        },
        "chunkCount": plan.batch.chunk_count,
        "documentCount": len(plan.items),
        "embeddingProfileId": "voyage_context_4_1024_v1",
        "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "operation": "OWNER_VOYAGE_DOCUMENT_IMPORT",
        "ownerScopeSha256": plan.owner_scope_sha256,
        "packetSha256": "f" * 64,
        "physicalCallCap": 1,
        "planSha256": plan.plan_sha256,
        "rawArtifactCount": 0,
        "retryCount": 0,
        "schemaVersion": 1,
        "ticketSetSha256": plan.ticket_set_sha256,
        "tokenCount": plan.batch.token_count,
        "tokenizerSha256": plan.tokenizer_sha256,
    }


def _write_owner_voyage_manifest(root: Path, content: bytes) -> None:
    control = root / "control"
    control.mkdir(mode=0o700)
    os.chmod(control, 0o700)
    path = control / "owner-voyage-manifest.v1.json"
    path.write_bytes(content)
    os.chmod(path, 0o600)
