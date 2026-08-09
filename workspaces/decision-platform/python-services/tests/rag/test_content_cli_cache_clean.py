from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag import content_cli
from app.rag.rag_v2_local_cache import RagV2LocalCacheReceipt


def test_cache_clean_emits_only_sanitized_removed_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(
        content_cli,
        "clean_local_rag_cache",
        lambda **_: RagV2LocalCacheReceipt(removed_entries=7),
    )

    assert content_cli.main(["cache-clean"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {"code": "LOCAL_CACHE_CLEARED", "removedEntries": 7, "state": "READY"}
    assert str(tmp_path) not in json.dumps(value)


def test_cache_clean_rejects_any_raw_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert content_cli.main(["cache-clean", "C:/private/cache"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "CONTENT_COMMAND_INVALID",
        "state": "FAILED",
    }
