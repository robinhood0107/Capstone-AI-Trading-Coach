from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from app.rag.bounded_subprocess import (
    BoundedProcessError,
    BoundedProcessLimits,
    run_bounded_process,
)
from app.rag.local_document_parser import (
    BlockType,
    DocumentParseError,
    OcrBlock,
    OcrPageResult,
)

_BACKENDS: Final = {"PADDLE_STRUCTURED", "PADDLE_VL", "UNLIMITED_GGUF"}
_BLOCK_TYPES: Final = {"HEADING", "PARAGRAPH", "LIST", "TABLE", "FORMULA", "CAPTION"}
_MAX_TABLE_CELLS: Final = 50_000
_HASH = re.compile(r"^[0-9a-f]{64}$")
_BLOCK_KEYS: Final = {
    "blockType",
    "cells",
    "columnCount",
    "confidence",
    "items",
    "level",
    "normalizedFormula",
    "ordered",
    "rowCount",
    "targetReadingOrder",
    "text",
}


@dataclass(frozen=True, slots=True)
class SubprocessOcrConfiguration:
    """pinned OCR child와 resource/model 경계를 묶는 내부 설정이다."""

    backend: str
    backend_version: str
    model_sha256: str
    executable: Path
    runner: Path
    working_directory: Path
    model_cache_root: Path
    limits: BoundedProcessLimits


class SubprocessOcrBackend:
    """owner page를 stdin으로만 넘기고 strict JSON block만 받는 OCR adapter다."""

    def __init__(self, configuration: SubprocessOcrConfiguration) -> None:
        _validate_configuration(configuration)
        self._configuration = configuration
        self.backend = configuration.backend
        self.backend_version = configuration.backend_version
        self.model_sha256 = configuration.model_sha256

    def parse_page(self, *, png_bytes: bytes, page_number: int) -> OcrPageResult:
        """PNG 한 page를 network-disabled child로 파싱하며 원본 경로는 전달하지 않는다."""

        if page_number < 1 or len(png_bytes) < 8 or not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DocumentParseError("OCR_INPUT_INVALID")
        configuration = self._configuration
        try:
            result = run_bounded_process(
                executable=configuration.executable,
                arguments=(
                    str(configuration.runner),
                    "--backend",
                    configuration.backend,
                    "--model-sha256",
                    configuration.model_sha256,
                    "--page-number",
                    str(page_number),
                ),
                working_directory=configuration.working_directory,
                environment={
                    "CAPSTONE_OCR_NETWORK_DISABLED": "1",
                    "PADDLE_PDX_CACHE_HOME": str(configuration.model_cache_root),
                },
                limits=configuration.limits,
                stdin=png_bytes,
            )
        except BoundedProcessError as error:
            raise DocumentParseError("OCR_BACKEND_FAILED") from error
        try:
            value = json.loads(result.stdout)
            return _decode_result(value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as error:
            raise DocumentParseError("OCR_RESULT_INVALID") from error


def _validate_configuration(value: SubprocessOcrConfiguration) -> None:
    try:
        runner_metadata = value.runner.lstat()
    except OSError as error:
        raise DocumentParseError("OCR_CONFIGURATION_INVALID") from error
    if (
        value.backend not in _BACKENDS
        or not value.backend_version
        or len(value.backend_version) > 128
        or any(character in value.backend_version for character in ("\x00", "\r", "\n"))
        or _HASH.fullmatch(value.model_sha256) is None
        or not value.executable.is_absolute()
        or not value.executable.is_file()
        or not value.runner.is_absolute()
        or not stat.S_ISREG(runner_metadata.st_mode)
        or runner_metadata.st_nlink != 1
        or not value.working_directory.is_absolute()
        or not value.working_directory.is_dir()
        or not value.model_cache_root.is_absolute()
        or not value.model_cache_root.is_dir()
    ):
        raise DocumentParseError("OCR_CONFIGURATION_INVALID")
    try:
        value.limits.validate()
    except BoundedProcessError as error:
        raise DocumentParseError("OCR_CONFIGURATION_INVALID") from error


def _decode_result(value: object) -> OcrPageResult:
    if not isinstance(value, dict) or set(value) != {"blocks"}:
        raise ValueError("OCR_RESULT_INVALID")
    raw_blocks = value["blocks"]
    if not isinstance(raw_blocks, list) or not 1 <= len(raw_blocks) <= 5_000:
        raise ValueError("OCR_RESULT_INVALID")
    return OcrPageResult(blocks=tuple(_decode_block(block) for block in raw_blocks))


def _decode_block(value: object) -> OcrBlock:
    if (
        not isinstance(value, dict)
        or not {"blockType", "confidence"} <= set(value)
        or not set(value) <= _BLOCK_KEYS
    ):
        raise ValueError("OCR_RESULT_INVALID")
    block_type = value["blockType"]
    confidence = value["confidence"]
    if (
        not isinstance(block_type, str)
        or block_type not in _BLOCK_TYPES
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ValueError("OCR_RESULT_INVALID")
    text = _optional_text(value.get("text"), 100_000)
    level = _optional_integer(value.get("level"), minimum=1, maximum=6)
    items = _text_tuple(value.get("items", []), maximum_items=10_000)
    ordered = value.get("ordered", False)
    if not isinstance(ordered, bool):
        raise ValueError("OCR_RESULT_INVALID")
    cells = _cells(value.get("cells", []))
    row_count = _optional_integer(value.get("rowCount"), minimum=1, maximum=10_000)
    column_count = _optional_integer(value.get("columnCount"), minimum=1, maximum=10_000)
    normalized_formula = _optional_text(value.get("normalizedFormula"), 100_000)
    target_order = _optional_integer(
        value.get("targetReadingOrder"),
        minimum=0,
        maximum=50_000,
    )
    if block_type == "TABLE" and (not cells or row_count is None or column_count is None):
        raise ValueError("OCR_RESULT_INVALID")
    # child payload의 declared shape가 parser의 dense matrix 예산을 넘으면 전달 자체를 거부한다.
    if (
        block_type == "TABLE"
        and row_count is not None
        and column_count is not None
        and row_count > _MAX_TABLE_CELLS // column_count
    ):
        raise ValueError("OCR_RESULT_INVALID")
    if block_type == "FORMULA" and normalized_formula is None:
        raise ValueError("OCR_RESULT_INVALID")
    if block_type not in {"TABLE"} and cells:
        raise ValueError("OCR_RESULT_INVALID")
    return OcrBlock(
        block_type=cast(BlockType, block_type),
        confidence=float(confidence),
        text=text,
        level=level,
        items=items,
        ordered=ordered,
        cells=cells,
        row_count=row_count,
        column_count=column_count,
        normalized_formula=normalized_formula,
        target_reading_order=target_order,
    )


def _optional_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ValueError("OCR_RESULT_INVALID")
    return value


def _optional_integer(value: object, *, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("OCR_RESULT_INVALID")
    return value


def _text_tuple(value: object, *, maximum_items: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError("OCR_RESULT_INVALID")
    return tuple(_required_cell_text(item) for item in value)


def _cells(value: object) -> tuple[tuple[int, int, str], ...]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise ValueError("OCR_RESULT_INVALID")
    output: list[tuple[int, int, str]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 3
            or isinstance(item[0], bool)
            or isinstance(item[1], bool)
            or not isinstance(item[0], int)
            or not isinstance(item[1], int)
            or not 0 <= item[0] <= 9_999
            or not 0 <= item[1] <= 9_999
        ):
            raise ValueError("OCR_RESULT_INVALID")
        output.append((item[0], item[1], _required_cell_text(item[2])))
    return tuple(output)


def _required_cell_text(value: object) -> str:
    if not isinstance(value, str) or len(value) > 100_000 or "\x00" in value:
        raise ValueError("OCR_RESULT_INVALID")
    return value
