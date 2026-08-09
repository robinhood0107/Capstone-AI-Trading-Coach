from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag import rag_v2_public_bge_cli
from app.rag.oa112_downloader import Oa112DownloadError


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

    monkeypatch.delenv("CAPSTONE_RAG_ADMIN_DATABASE_DSN", raising=False)
    assert rag_v2_public_bge_cli.main(("activate-public-base",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_ACTIVATION_DATABASE_DSN",
        "state": "FAILED",
    }

    assert rag_v2_public_bge_cli.main(("evaluate-public-base",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_EVALUATION_DATABASE_DSN",
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


def test_public_bge_cli_activates_only_a_pre_evaluated_local_pair_without_operator_ids(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_context = object()
    oa_context = object()
    received: dict[str, object] = {}

    class _ActivationRepository:
        def __init__(self, *, database_dsn: str) -> None:
            received["database_dsn"] = database_dsn

        def activate(self, *, request: object) -> SimpleNamespace:
            received["request"] = request
            return SimpleNamespace(
                embedding_profile_id="bge_m3_local_1024_v1",
                exact30_generation_id="rgr_0123456789abcdef0123456789abcdef",
                oa112_generation_id="rgr_abcdef0123456789abcdef0123456789",
                previous_pointer_version=1,
                new_pointer_version=2,
                state="ACTIVE",
            )

    monkeypatch.setenv("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "postgresql://rag-admin")
    monkeypatch.setattr(
        rag_v2_public_bge_cli,
        "_materialize_exact30",
        lambda: SimpleNamespace(context=exact_context),
    )
    monkeypatch.setattr(
        rag_v2_public_bge_cli,
        "_materialize_oa112",
        lambda: SimpleNamespace(context=oa_context),
    )
    monkeypatch.setattr(
        rag_v2_public_bge_cli,
        "PublicBgeActivationRequest",
        lambda **values: values,
    )
    monkeypatch.setattr(
        rag_v2_public_bge_cli,
        "PsycopgRagV2PublicBgeActivationRepository",
        _ActivationRepository,
    )

    assert rag_v2_public_bge_cli.main(("activate-public-base",)) == 0

    assert received == {
        "database_dsn": "postgresql://rag-admin",
        "request": {"exact30": exact_context, "oa112": oa_context},
    }
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_BASE_ACTIVE",
        "embeddingProfileId": "bge_m3_local_1024_v1",
        "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "newPointerVersion": 2,
        "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "previousPointerVersion": 1,
        "state": "ACTIVE",
    }


def test_public_bge_cli_evaluates_or_reuses_only_content_free_pair_summary(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://rag-writer")
    received: dict[str, object] = {}

    def evaluate(*, database_dsn: str) -> tuple[dict[str, object], bool]:
        received["database_dsn"] = database_dsn
        return (
            {
                "embeddingProfileId": "bge_m3_local_1024_v1",
                "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
                "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
            },
            True,
        )

    monkeypatch.setattr(rag_v2_public_bge_cli, "_evaluate_public_base", evaluate)

    assert rag_v2_public_bge_cli.main(("evaluate-public-base",)) == 0
    assert received == {"database_dsn": "postgresql://rag-writer"}
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_PAIR_EVALUATION_REUSED",
        "embeddingProfileId": "bge_m3_local_1024_v1",
        "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "state": "EVALUATED",
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


def test_public_bge_cli_projects_oa112_physical_counts_and_failure_receipt_state(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rag_v2_public_bge_cli,
        "_download_oa112",
        lambda: SimpleNamespace(
            attempt_count=2,
            downloaded_source_count=1,
            physical_call_count=1,
            reused_source_count=111,
        ),
    )

    assert rag_v2_public_bge_cli.main(("oa112-download",)) == 0
    assert json.loads(capsys.readouterr().out) == {
        "attemptCount": 2,
        "code": "OA112_LOCAL_CACHE_READY",
        "downloadedSourceCount": 1,
        "physicalCallCount": 1,
        "reusedSourceCount": 111,
        "state": "DOWNLOADED",
    }

    def fail() -> object:
        raise Oa112DownloadError(
            "OA112_DOWNLOAD_DNS",
            attempt_count=1,
            physical_call_count=0,
            failure_receipt_written=True,
        )

    monkeypatch.setattr(rag_v2_public_bge_cli, "_download_oa112", fail)
    assert rag_v2_public_bge_cli.main(("oa112-download",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "attemptCount": 1,
        "code": "OA112_DOWNLOAD_DNS",
        "failureReceiptWritten": True,
        "physicalCallCount": 0,
        "state": "FAILED",
    }
