from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.content_cli import main


def test_status_before_corpus_runtime_is_stable_and_contains_no_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))

    assert main(["status"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "code": "CONTENT_SETUP_REQUIRED",
        "progressPercent": 0,
        "state": "BUILDING",
    }
    assert str(tmp_path) not in json.dumps(value)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        (["setup"], "CONTENT_RELEASE_NOT_INSTALLED"),
        (["import-cpu", "C:/Users/owner/private.pdf"], "OCR_PRODUCTION_BACKEND_NOT_SELECTED"),
        (["import-intel-gpu", "C:/Users/owner/private.pdf"], "OCR_PRODUCTION_BACKEND_NOT_SELECTED"),
        (["import-nvidia-gpu", "C:/Users/owner/private.pdf"], "OCR_PRODUCTION_BACKEND_NOT_SELECTED"),
        (["import-auto", "C:/Users/owner/private.pdf"], "OCR_PRODUCTION_BACKEND_NOT_SELECTED"),
        (["remove-document", "doc_owner_fixture_001"], "CORPUS_RUNTIME_NOT_INSTALLED"),
        (["cache-clean"], "CORPUS_RUNTIME_NOT_INSTALLED"),
    ],
)
def test_pre_runtime_commands_fail_closed_without_echoing_private_arguments(
    arguments: list[str],
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(arguments) == 2

    value = json.loads(capsys.readouterr().out)
    assert value == {"code": code, "state": "FAILED"}
    assert "private.pdf" not in json.dumps(value)
