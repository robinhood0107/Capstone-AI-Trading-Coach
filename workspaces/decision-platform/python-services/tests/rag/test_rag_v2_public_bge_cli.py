from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.rag import rag_v2_public_bge_cli
from app.rag.oa112_downloader import Oa112DownloadError


def test_public_bge_cli_terminally_rejects_every_embedding_command_without_loading_a_model(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_if_called() -> object:
        nonlocal called
        called = True
        raise AssertionError("terminal BGE command loaded a model")

    monkeypatch.setattr(rag_v2_public_bge_cli, "_materialize_exact30", fail_if_called)
    monkeypatch.setattr(rag_v2_public_bge_cli, "_materialize_oa112", fail_if_called)
    for command in (
        "exact30-materialize",
        "exact30-stage",
        "activate-public-base",
        "evaluate-public-base",
        "oa112-materialize",
        "oa112-stage",
    ):
        assert rag_v2_public_bge_cli.main((command,)) == 2
        assert json.loads(capsys.readouterr().out) == {
            "code": "BGE_PUBLIC_EXECUTION_TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN",
            "state": "FAILED",
        }
    assert called is False

    assert rag_v2_public_bge_cli.main(("unknown",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_BGE_COMMAND_INVALID",
        "state": "FAILED",
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
