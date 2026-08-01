from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any, Final


_UPSTREAM_HASHES: Final = {
    "image_processing_paddleocr_vl.py": (
        "9e11ef294ac8d36eedac5fa975ef487c123b07f2c370c55984a0023ae6046a4c"
    ),
    "modeling_paddleocr_vl.py": (
        "26f6bc752a30e8d00a71a056869dca948185811fca8a8c9d0332c18fa3f3ac5e"
    ),
    "ov_paddleocr_vl.py": (
        "b02408445fb9fdd3755794a2e87ce5a16ff54a29a95f27dbe23b1a3f5ee8da4d"
    ),
}
_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_IMAGE_BYTES: Final = 50 * 1024 * 1024
_MAX_IMAGE_PIXELS: Final = 50_000_000
_MAX_RESPONSE_CHARACTERS: Final = 2_000_000
_TABLE_SEPARATOR: Final = re.compile(r"^:?-{3,}:?$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("OPENVINO_ARTIFACT_MISSING") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise RuntimeError("OPENVINO_ARTIFACT_UNSAFE")
    return path


def _assert_sources(root: Path) -> None:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("OPENVINO_SOURCE_ROOT_INVALID")
    for name, expected in _UPSTREAM_HASHES.items():
        path = _regular_file(root / name)
        if _sha256(path) != expected:
            raise RuntimeError(f"OPENVINO_SOURCE_DIGEST_MISMATCH:{name}")


def _tree_digest(root: Path) -> str:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("OPENVINO_MODEL_ROOT_INVALID")
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    if not paths:
        raise RuntimeError("OPENVINO_MODEL_ROOT_INVALID")
    digest = hashlib.sha256()
    for path in paths:
        _regular_file(path)
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative + b"  " + _sha256(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def _execution_devices(compiled: Any) -> list[str]:
    values = [str(value) for value in compiled.get_property("EXECUTION_DEVICES")]
    if not values or any(not value.startswith("GPU") for value in values):
        raise RuntimeError("OPENVINO_GPU_SILENT_FALLBACK")
    return values


def _markdown_blocks(value: str) -> list[dict[str, object]]:
    """VL markdown을 경로 없는 bounded Document IR block projection으로 바꾼다."""

    if not value or len(value) > _MAX_RESPONSE_CHARACTERS or "\x00" in value:
        raise RuntimeError("OPENVINO_RESPONSE_INVALID")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    segments = [segment.strip() for segment in re.split(r"\n\s*\n", normalized) if segment.strip()]
    blocks: list[dict[str, object]] = []
    for index, segment in enumerate(segments):
        lines = [line.strip() for line in segment.splitlines() if line.strip()]
        if not lines:
            continue
        base: dict[str, object] = {
            "confidence": 1.0,
            "targetReadingOrder": index,
        }
        heading = re.fullmatch(r"(#{1,6})\s+(.+)", lines[0])
        if heading is not None and len(lines) == 1:
            blocks.append(
                {
                    **base,
                    "blockType": "HEADING",
                    "level": len(heading.group(1)),
                    "text": heading.group(2).strip(),
                }
            )
            continue
        if all(re.match(r"^[-*+]\s+", line) for line in lines):
            blocks.append(
                {
                    **base,
                    "blockType": "LIST",
                    "items": [re.sub(r"^[-*+]\s+", "", line) for line in lines],
                    "ordered": False,
                }
            )
            continue
        table = _table_block(lines, base)
        if table is not None:
            blocks.append(table)
            continue
        formula = _formula_text(segment)
        if formula is not None:
            blocks.append(
                {
                    **base,
                    "blockType": "FORMULA",
                    "normalizedFormula": formula,
                    "text": segment,
                }
            )
            continue
        blocks.append({**base, "blockType": "PARAGRAPH", "text": segment})
    if not blocks or len(blocks) > 5_000:
        raise RuntimeError("OPENVINO_RESPONSE_INVALID")
    return blocks


def _table_block(
    lines: list[str],
    base: dict[str, object],
) -> dict[str, object] | None:
    if len(lines) < 2 or any("|" not in line for line in lines):
        return None
    rows = [_table_row(line) for line in lines]
    if not rows or len({len(row) for row in rows}) != 1 or not rows[0]:
        return None
    data_rows = [row for row in rows if not all(_TABLE_SEPARATOR.fullmatch(cell) for cell in row)]
    if not data_rows:
        return None
    columns = len(data_rows[0])
    if any(len(row) != columns for row in data_rows):
        return None
    cells = [
        [row_index, column_index, text]
        for row_index, row in enumerate(data_rows)
        for column_index, text in enumerate(row)
    ]
    return {
        **base,
        "blockType": "TABLE",
        "cells": cells,
        "columnCount": columns,
        "rowCount": len(data_rows),
    }


def _table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _formula_text(value: str) -> str | None:
    stripped = value.strip()
    pairs = (("$$", "$$"), (r"\[", r"\]"))
    for prefix, suffix in pairs:
        if stripped.startswith(prefix) and stripped.endswith(suffix):
            formula = stripped[len(prefix) : len(stripped) - len(suffix)].strip()
            return formula or None
    return None


def _run(arguments: argparse.Namespace) -> dict[str, object]:
    source = arguments.source_directory.resolve(strict=True)
    model_root = arguments.model_directory.resolve(strict=True)
    _assert_sources(source)
    if _HASH.fullmatch(arguments.expected_model_sha256) is None or (
        _tree_digest(model_root) != arguments.expected_model_sha256
    ):
        raise RuntimeError("OPENVINO_MODEL_DIGEST_MISMATCH")
    if os.environ.get("CAPSTONE_OCR_NETWORK_DISABLED") != "1":
        raise RuntimeError("OPENVINO_NETWORK_BOUNDARY_MISSING")
    # Transformers가 cache miss를 원격 조회로 바꾸지 못하도록 import 전에 고정한다.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"

    payload = sys.stdin.buffer.read(_MAX_IMAGE_BYTES + 1)
    if (
        len(payload) < 8
        or len(payload) > _MAX_IMAGE_BYTES
        or not payload.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise RuntimeError("OPENVINO_IMAGE_INVALID")
    if arguments.expected_page_sha256 and hashlib.sha256(payload).hexdigest() != (
        arguments.expected_page_sha256
    ):
        raise RuntimeError("OPENVINO_PAGE_DIGEST_MISMATCH")

    sys.path.insert(0, str(source))
    import openvino as ov
    import psutil
    from PIL import Image
    from ov_paddleocr_vl import OVPaddleOCRVLForCausalLM

    core = ov.Core()
    if "GPU" not in core.available_devices:
        raise RuntimeError("OPENVINO_GPU_UNAVAILABLE")
    model = OVPaddleOCRVLForCausalLM(
        core=core,
        ov_model_path=str(model_root),
        device="GPU",
        llm_int8_compress=True,
        llm_int8_quant=True,
        llm_infer_list=[],
        vision_infer=[],
    )
    devices = {
        "embedding": _execution_devices(model.llm_embd_compiled_model),
        "llm": _execution_devices(model.llm_compiled_model),
        "vision": _execution_devices(model.vision_encoder_compiled_model),
    }
    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    image = Image.open(io.BytesIO(payload)).convert("RGB")
    image.load()
    started = time.perf_counter()
    response, _ = model.chat(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": "OCR:"},
                ],
            }
        ],
        generation_config={
            "bos_token_id": model.tokenizer.bos_token_id,
            "do_sample": False,
            "eos_token_id": model.tokenizer.eos_token_id,
            "max_new_tokens": 2048 if arguments.benchmark else 4096,
            "pad_token_id": model.tokenizer.pad_token_id,
        },
    )
    elapsed = time.perf_counter() - started
    if not isinstance(response, str):
        raise RuntimeError("OPENVINO_RESPONSE_INVALID")
    blocks = _markdown_blocks(response)
    if not arguments.benchmark:
        return {"blocks": blocks}
    memory = psutil.Process().memory_info()
    return {
        "candidate": "PADDLE_VL",
        "compileInferVerified": True,
        "devices": devices,
        "elapsedSeconds": elapsed,
        "fixtureId": arguments.fixture_id,
        "lane": "INTEL_GPU",
        "modelSha256": arguments.expected_model_sha256,
        "openvinoDevice": "GPU",
        "openvinoVersion": ov.__version__,
        "peakProcessRssBytes": int(getattr(memory, "peak_wset", memory.rss)),
        "response": response,
        "responseSha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
        "silentFallbackDetected": False,
    }


def main() -> None:
    """고정 OpenVINO VL artifact를 GPU-only로 실행하고 path 없는 JSON만 출력한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-directory", required=True, type=Path)
    parser.add_argument("--model-directory", required=True, type=Path)
    parser.add_argument("--expected-model-sha256", required=True)
    parser.add_argument("--expected-page-sha256")
    parser.add_argument("--fixture-id", default="owner-local-page")
    parser.add_argument("--benchmark", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(_run(arguments), ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
