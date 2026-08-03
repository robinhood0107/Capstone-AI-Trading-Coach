from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag import rag_v2_public_bge_cli


def test_public_bge_cli_rejects_unknown_or_unconfigured_stage_without_loading_a_model(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rag_v2_public_bge_cli.main(("unknown",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_COMMAND_INVALID",
        "state": "FAILED",
    }

    monkeypatch.delenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", raising=False)
    assert rag_v2_public_bge_cli.main(("exact30-stage",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_STAGE_DATABASE_DSN",
        "state": "FAILED",
    }

    assert rag_v2_public_bge_cli.main(("activate-public-base",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_COMMAND_INVALID",
        "state": "FAILED",
    }


def test_public_bge_cli_emits_only_content_free_exact30_receipts(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialization = SimpleNamespace(
        content_free_receipt=lambda: {
            "chunkCount": 30,
            "componentGenerationId": "rgr_0123456789abcdef0123456789abcdef",
            "componentScope": "EXACT30",
            "embeddingProfileId": "bge_m3_local_1024_v1",
            "manifestHash": "a" * 64,
            "sourceCount": 30,
        }
    )
    monkeypatch.setattr(rag_v2_public_bge_cli, "_materialize_exact30", lambda: materialization)

    assert rag_v2_public_bge_cli.main(("exact30-materialize",)) == 0
    output = json.loads(capsys.readouterr().out)

    assert output == {
        "chunkCount": 30,
        "code": "EXACT30_LOCAL_BGE_MATERIALIZED",
        "componentGenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "componentScope": "EXACT30",
        "embeddingProfileId": "bge_m3_local_1024_v1",
        "manifestHash": "a" * 64,
        "sourceCount": 30,
        "state": "MATERIALIZED",
    }


def test_public_bge_cli_requires_local_control_root_before_oa112_materialization(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAPSTONE_RAG_LOCAL_ROOT", raising=False)

    assert rag_v2_public_bge_cli.main(("oa112-materialize",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "OA112_LOCAL_CONTROL_REQUIRED",
        "state": "FAILED",
    }


def test_public_bge_cli_emits_content_free_oa112_materialization_receipt(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    materialization = SimpleNamespace(
        content_free_receipt=lambda: {
            "chunkCount": 112,
            "componentGenerationId": "rgr_abcdef0123456789abcdef0123456789",
            "componentScope": "OA112",
            "embeddingProfileId": "bge_m3_local_1024_v1",
            "manifestHash": "b" * 64,
            "registryDigest": "c" * 64,
            "sourceCount": 112,
        }
    )
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(rag_v2_public_bge_cli, "_materialize_oa112", lambda: materialization)

    assert rag_v2_public_bge_cli.main(("oa112-materialize",)) == 0
    output = json.loads(capsys.readouterr().out)

    assert output == {
        "chunkCount": 112,
        "code": "OA112_LOCAL_BGE_MATERIALIZED",
        "componentGenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "componentScope": "OA112",
        "embeddingProfileId": "bge_m3_local_1024_v1",
        "manifestHash": "b" * 64,
        "registryDigest": "c" * 64,
        "sourceCount": 112,
        "state": "MATERIALIZED",
    }
