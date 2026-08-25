from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.rag.bounded_subprocess import BoundedProcessLimits
from app.rag.local_document_parser import DocumentParseError
from app.rag.subprocess_ocr_backend import SubprocessOcrBackend, SubprocessOcrConfiguration


def _runner(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "fixture_runner.py"
    encoded = json.dumps(payload, separators=(",", ":"))
    path.write_text(
        (
            "import os, sys\n"
            "assert os.environ.get('OWNER_PRIVATE_SECRET') is None\n"
            "assert os.environ['CAPSTONE_OCR_NETWORK_DISABLED'] == '1'\n"
            "assert sys.stdin.buffer.read().startswith(b'\\x89PNG\\r\\n\\x1a\\n')\n"
            f"print({encoded!r})\n"
        ),
        encoding="utf-8",
    )
    return path


def _configuration(tmp_path: Path, runner: Path) -> SubprocessOcrConfiguration:
    cache = tmp_path / "models"
    cache.mkdir(exist_ok=True)
    return SubprocessOcrConfiguration(
        backend="PADDLE_STRUCTURED",
        backend_version="3.7.0",
        model_sha256="7" * 64,
        executable=Path(sys.executable).resolve(),
        runner=runner,
        working_directory=tmp_path,
        model_cache_root=cache,
        limits=BoundedProcessLimits(
            timeout_seconds=2,
            max_stdin_bytes=1024,
            max_stdout_bytes=4096,
            max_stderr_bytes=4096,
            max_memory_bytes=256 * 1024 * 1024,
            max_cpu_seconds=2,
        ),
    )


def test_subprocess_backend_maps_strict_json_to_document_ocr_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNER_PRIVATE_SECRET", "must-not-reach-ocr")
    runner = _runner(
        tmp_path,
        {
            "blocks": [
                {
                    "blockType": "PARAGRAPH",
                    "confidence": 0.997,
                    "text": "Option pricing evidence",
                },
                {
                    "blockType": "TABLE",
                    "cells": [[0, 0, "Metric"], [0, 1, "Value"]],
                    "columnCount": 2,
                    "confidence": 0.98,
                    "rowCount": 1,
                },
            ]
        },
    )
    backend = SubprocessOcrBackend(_configuration(tmp_path, runner))

    result = backend.parse_page(png_bytes=b"\x89PNG\r\n\x1a\nfixture", page_number=3)

    assert backend.backend == "PADDLE_STRUCTURED"
    assert result.blocks[0].text == "Option pricing evidence"
    assert result.blocks[1].cells == ((0, 0, "Metric"), (0, 1, "Value"))
    assert result.blocks[1].row_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"blocks": []},
        {"blocks": [{"blockType": "UNKNOWN", "confidence": 1, "text": "x"}]},
        {"blocks": [{"blockType": "PARAGRAPH", "confidence": 2, "text": "x"}]},
        {
            "blocks": [
                {"blockType": "PARAGRAPH", "confidence": 1, "text": "x", "path": "C:/private"}
            ]
        },
    ],
)
def test_subprocess_backend_rejects_empty_unknown_unbounded_or_path_bearing_output(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    backend = SubprocessOcrBackend(_configuration(tmp_path, _runner(tmp_path, payload)))

    with pytest.raises(DocumentParseError, match="OCR_RESULT_INVALID"):
        backend.parse_page(png_bytes=b"\x89PNG\r\n\x1a\nfixture", page_number=1)


def test_subprocess_backend_rejects_table_shape_that_exceeds_dense_parser_budget(
    tmp_path: Path,
) -> None:
    runner = _runner(
        tmp_path,
        {
            "blocks": [
                {
                    "blockType": "TABLE",
                    "cells": [[0, 0, "Metric"]],
                    "columnCount": 10_000,
                    "confidence": 0.98,
                    "rowCount": 10_000,
                }
            ]
        },
    )
    backend = SubprocessOcrBackend(_configuration(tmp_path, runner))

    with pytest.raises(DocumentParseError, match="OCR_RESULT_INVALID"):
        backend.parse_page(png_bytes=b"\x89PNG\r\n\x1a\nfixture", page_number=1)


def test_subprocess_backend_validates_png_page_and_pinned_configuration(tmp_path: Path) -> None:
    runner = _runner(
        tmp_path,
        {"blocks": [{"blockType": "PARAGRAPH", "confidence": 1, "text": "x"}]},
    )
    backend = SubprocessOcrBackend(_configuration(tmp_path, runner))

    with pytest.raises(DocumentParseError, match="OCR_INPUT_INVALID"):
        backend.parse_page(png_bytes=b"not-png", page_number=0)

    invalid = _configuration(tmp_path, runner)
    object.__setattr__(invalid, "model_sha256", "unpinned")
    with pytest.raises(DocumentParseError, match="OCR_CONFIGURATION_INVALID"):
        SubprocessOcrBackend(invalid)
