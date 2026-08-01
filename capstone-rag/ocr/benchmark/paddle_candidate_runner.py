from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Final

import psutil


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(
    0,
    str(_REPOSITORY_ROOT / "workspaces/decision-platform/python-services"),
)
from app.rag.benchmark_receipt_io import write_benchmark_receipt  # noqa: E402
from app.rag.ocr_benchmark import sanitize_paddle_ocr_lines  # noqa: E402


_SUPPORTED: Final = {"PADDLE_STRUCTURED", "PADDLE_VL"}


def _repository_root() -> Path:
    return _REPOSITORY_ROOT


def _manifest() -> dict[str, Any]:
    path = Path(__file__).with_name("benchmark-manifest.v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _page_rows(manifest: dict[str, Any]) -> list[tuple[str, Path, str]]:
    root = _repository_root()
    runtime = root / "capstone-rag/runtime/local-corpus/ocr-benchmark/pages"
    rows: list[tuple[str, Path, str]] = []
    for source in manifest["sources"]:
        language = source["language"]
        for page in source["pages"]:
            image = runtime / page["imageFile"]
            if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != page[
                "imageSha256"
            ]:
                raise RuntimeError("OCR_BENCHMARK_PAGE_DIGEST_MISMATCH")
            rows.append((page["fixtureId"], image, language))
    return rows


def _sanitize(result: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = getattr(result, "json", None)
    if not isinstance(payload, dict) or not isinstance(payload.get("res"), dict):
        raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
    blocks = payload["res"].get("parsing_res_list")
    if not isinstance(blocks, list):
        raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
    safe: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
        safe.append(
            {
                "bbox": block.get("block_bbox"),
                "content": block.get("block_content"),
                "label": block.get("block_label"),
                "order": block.get("block_order"),
            }
        )
    overall_ocr = payload["res"].get("overall_ocr_res")
    lines = () if overall_ocr is None else sanitize_paddle_ocr_lines(overall_ocr)
    return safe, [
        {
            "bbox": list(line.bbox),
            "confidence": line.confidence,
            "text": line.text,
        }
        for line in lines
    ]


def _structured(language: str) -> object:
    from paddleocr import PPStructureV3

    recognition = (
        "korean_PP-OCRv5_mobile_rec" if language == "ko" else "PP-OCRv6_small_rec"
    )
    return PPStructureV3(
        layout_detection_model_name="PP-DocLayout-M",
        layout_threshold=0.1,
        layout_nms=True,
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name=recognition,
        wired_table_structure_recognition_model_name="SLANeXt_wired",
        wireless_table_structure_recognition_model_name="SLANet_plus",
        formula_recognition_model_name="PP-FormulaNet_plus-S",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_seal_recognition=False,
        use_table_recognition=True,
        use_formula_recognition=True,
        # 차트의 인쇄 숫자는 overall OCR line으로 평가한다. 생성형 chart-to-table은
        # 금융 숫자를 새로 만들어낼 수 있고 구조형 경량 후보의 범위도 아니다.
        use_chart_recognition=False,
        use_region_detection=True,
        device="cpu",
        enable_mkldnn=False,
    )


def _vl() -> object:
    from paddleocr import PaddleOCRVL

    return PaddleOCRVL(
        pipeline_version="v1.6",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_layout_detection=True,
        # 생성형 chart reconstruction은 금융 숫자를 만들 수 있으므로 사용하지 않는다.
        # chart/image block의 인쇄 문자열은 pinned OCR model로만 읽는다.
        use_chart_recognition=False,
        use_seal_recognition=False,
        use_ocr_for_image_block=True,
        vl_rec_backend="native",
        device="cpu",
        enable_mkldnn=False,
    )


def _auxiliary_ocr(language: str) -> object:
    """VL이 구조를 읽은 뒤 chart/image의 인쇄 문자열만 deterministic OCR로 보강한다."""

    from paddleocr import PaddleOCR

    recognition = (
        "korean_PP-OCRv5_mobile_rec" if language == "ko" else "PP-OCRv6_small_rec"
    )
    return PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name=recognition,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
        enable_mkldnn=False,
    )


def _sanitize_auxiliary_ocr(result: object) -> list[dict[str, Any]]:
    payload = getattr(result, "json", None)
    if not isinstance(payload, dict) or not isinstance(payload.get("res"), dict):
        raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
    lines = sanitize_paddle_ocr_lines(payload["res"])
    return [
        {
            "bbox": list(line.bbox),
            "confidence": line.confidence,
            "text": line.text,
        }
        for line in lines
    ]


def _run(candidate: str, output_directory: Path, fixture: str | None) -> None:
    manifest = _manifest()
    candidate_manifest = manifest["candidates"][candidate]
    expected = candidate_manifest["modelSha256"]
    runtime_root = _repository_root() / "capstone-rag/runtime/local-corpus"
    expected_output = runtime_root / "ocr-benchmark/results" / candidate / "CPU"
    if Path(os.path.abspath(output_directory)) != expected_output:
        raise RuntimeError("OCR_BENCHMARK_OUTPUT_INVALID")
    cache = Path(os.environ.get("PADDLE_PDX_CACHE_HOME", ""))
    if not cache.is_absolute() or _tree_digest(
        cache / "official_models", tuple(candidate_manifest["modelDirectories"])
    ) != expected:
        raise RuntimeError("OCR_MODEL_DIGEST_MISMATCH")
    process = psutil.Process()
    rows = [row for row in _page_rows(manifest) if fixture is None or row[0] == fixture]
    if not rows:
        raise RuntimeError("OCR_BENCHMARK_FIXTURE_UNKNOWN")
    initialized: dict[str, object] = {}
    auxiliary_initialized: dict[str, object] = {}
    for fixture_id, image, language in rows:
        key = language if candidate == "PADDLE_STRUCTURED" else "vl"
        pipeline = initialized.get(key)
        if pipeline is None:
            pipeline = _structured(language) if candidate == "PADDLE_STRUCTURED" else _vl()
            initialized[key] = pipeline
        started = time.perf_counter()
        # PP-Chart2Table의 pinned generation config와 같은 상한을 사용해 chart loop가
        # 무제한으로 콘텐츠 설정 worker를 점유하지 못하게 한다.
        results = list(  # type: ignore[attr-defined]
            pipeline.predict(str(image), max_new_tokens=2048)
        )
        elapsed = time.perf_counter() - started
        if len(results) != 1:
            raise RuntimeError("OCR_RESULT_COUNT_INVALID")
        blocks, ocr_lines = _sanitize(results[0])
        if candidate == "PADDLE_VL":
            auxiliary = auxiliary_initialized.get(language)
            if auxiliary is None:
                auxiliary = _auxiliary_ocr(language)
                auxiliary_initialized[language] = auxiliary
            auxiliary_results = list(auxiliary.predict(str(image)))  # type: ignore[attr-defined]
            if len(auxiliary_results) != 1:
                raise RuntimeError("OCR_RESULT_COUNT_INVALID")
            ocr_lines = _sanitize_auxiliary_ocr(auxiliary_results[0])
        receipt = {
            "candidate": candidate,
            "elapsedSeconds": elapsed,
            "fixtureId": fixture_id,
            "hardware": platform.platform(),
            "lane": "CPU",
            "modelSha256": expected,
            "ocrLines": ocr_lines,
            "peakProcessRssBytes": process.memory_info().rss,
            "result": blocks,
        }
        payload = (
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        write_benchmark_receipt(
            approved_root=runtime_root,
            relative_directory=f"ocr-benchmark/results/{candidate}/CPU",
            filename=f"{fixture_id}.json",
            payload=payload,
        )


def _tree_digest(root: Path, directories: tuple[str, ...]) -> str:
    if not root.is_dir():
        raise RuntimeError("OCR_MODEL_CACHE_MISSING")
    digest = hashlib.sha256()
    paths = [
        path
        for directory in directories
        for path in (root / directory).rglob("*")
        if path.is_file()
    ]
    if not paths or any(not (root / directory).is_dir() for directory in directories):
        raise RuntimeError("OCR_MODEL_CACHE_MISSING")
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
        digest.update(relative + b"  " + file_digest + b"\n")
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", choices=sorted(_SUPPORTED), required=True)
    parser.add_argument("--fixture")
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    _run(
        arguments.candidate,
        Path(os.path.abspath(arguments.output_directory)),
        arguments.fixture,
    )


if __name__ == "__main__":
    main()
