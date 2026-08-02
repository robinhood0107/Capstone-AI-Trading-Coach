from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final

import fitz

from app.rag.ocr_benchmark import (
    EvaluationDocument,
    GroundedSpan,
    evaluate_quality,
    quality_receipt_projection,
    reading_order_from_grounded_spans,
    retain_expected_critical_spans,
    sanitize_paddle_ocr_lines,
)


_PADDLE_CANDIDATES: Final = {"PADDLE_STRUCTURED", "PADDLE_VL"}
_NARRATIVE_LABELS: Final = {"paragraph_title", "text"}
_FORMULA_LABELS: Final = {"display_formula", "formula"}
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:%|조원|년|분기|/4)?")
_TEX_COMMAND = re.compile(r"\\[A-Za-z]+")


class _CellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cells: list[str] = []
        self._active = False
        self._buffer: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in {"td", "th"}:
            self._active = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._active:
            value = "".join(self._buffer).strip()
            if value:
                self.cells.append(value)
            self._active = False
            self._buffer = []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> tuple[dict[str, Any], str]:
    path = Path(__file__).with_name("benchmark-manifest.v1.json")
    return json.loads(path.read_text(encoding="utf-8")), _sha256(path)


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime/local-corpus/ocr-benchmark"


def _collapsed_native_text(page: fitz.Page) -> str:
    return " ".join(unicodedata.normalize("NFKC", page.get_text("text", sort=True)).split())


def _rect_300_dpi(value: list[int]) -> fitz.Rect:
    if len(value) != 4 or any(not isinstance(item, int) or item < 0 for item in value):
        raise RuntimeError("OCR_EVALUATION_REGION_INVALID")
    return fitz.Rect(*(item * 72 / 300 for item in value))


def _center_in(block: dict[str, Any], region: list[int]) -> bool:
    bbox = block.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(not isinstance(value, (int, float)) for value in bbox)
    ):
        return False
    x = (float(bbox[0]) + float(bbox[2])) / 2
    y = (float(bbox[1]) + float(bbox[3])) / 2
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def _region_blocks(
    blocks: list[dict[str, Any]],
    region: list[int],
    labels: set[str] | None = None,
) -> list[dict[str, Any]]:
    return [
        block
        for block in blocks
        if _center_in(block, region)
        and (labels is None or block.get("label") in labels)
        and isinstance(block.get("content"), str)
    ]


def _normalize_korean_narrative(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(re.findall(r"[가-힣A-Za-z0-9.%(),/\-]", normalized))


def _normalize_english_narrative(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\\(?:mathbf|mathrm|mathsf|text)", "", normalized)
    normalized = _TEX_COMMAND.sub("", normalized)
    return "".join(re.findall(r"[A-Za-z]", normalized)).casefold()


def _gold_table_cells(page: fitz.Page, points: list[int]) -> tuple[str, ...]:
    if len(points) != 4:
        raise RuntimeError("OCR_TABLE_GOLD_INVALID")
    area = fitz.Rect(*points)
    rows: dict[float, list[str]] = {}
    for word in page.get_text("words", sort=True):
        x0, y0, x1, y1, text = word[:5]
        if area.contains(fitz.Rect(x0, y0, x1, y1)):
            rows.setdefault(round(y0, 1), []).append(str(text))
    cells: list[str] = []
    for y in sorted(rows):
        values = rows[y]
        if values[:2] == ["평", "균"]:
            values = ["평균", *values[2:]]
        cells.extend(values)
    if not cells:
        raise RuntimeError("OCR_TABLE_GOLD_INVALID")
    return tuple(cells)


def _prediction_table_cells(blocks: list[dict[str, Any]], region: list[int]) -> tuple[str, ...]:
    parser = _CellParser()
    for block in _region_blocks(blocks, region, {"table"}):
        parser.feed(str(block["content"]))
    return tuple(parser.cells)


def _formula_gold(tex: str) -> tuple[str, ...]:
    def labelled(label: str) -> str:
        marker = f"\\label{{{label}}}"
        index = tex.index(marker)
        start = tex.rfind("\\begin{equation}", 0, index)
        end = tex.index("\\end{equation}", index) + len("\\end{equation}")
        return tex[start:end]

    def display_after(marker: str) -> str:
        index = tex.index(marker) + len(marker)
        start = tex.index("\\[", index)
        end = tex.index("\\]", start) + 2
        return tex[start:end]

    return (
        labelled("eq:coefficients"),
        display_after("following time discretization scheme"),
        labelled("eq:discretization"),
        labelled("eq:discretization2"),
        display_after("if and only if $U^k$ minimizes"),
    )


def _prediction_formulae(
    blocks: list[dict[str, Any]],
    page: dict[str, Any],
) -> tuple[str, ...]:
    names = (
        "formula-coefficients",
        "formula-discretization-1",
        "formula-discretization-2",
        "formula-variational",
        "formula-energy",
    )
    values: list[str] = []
    regions = page["evaluationRegions300Dpi"]
    for name in names:
        matches = _region_blocks(blocks, regions[name], _FORMULA_LABELS)
        if matches:
            values.append(str(matches[0]["content"]))
    return tuple(values)


def _reading_order(blocks: list[dict[str, Any]], page: dict[str, Any]) -> tuple[str, ...]:
    regions = page["evaluationRegions300Dpi"]
    expected = page["readingOrder"]
    output: list[str] = []
    for block in blocks:
        for name in expected:
            if name not in output and _center_in(block, regions[name]):
                output.append(name)
                break
    return tuple(output)


def _numeric_spans(value: str) -> tuple[str, ...]:
    output: list[str] = []
    normalized = unicodedata.normalize("NFKC", value).replace("−", "-")
    for match in _NUMBER.finditer(normalized):
        token = match.group(0).rstrip(",")
        token = re.sub(r"(?:조원|년|분기)$", "", token)
        if token:
            output.append(token)
    return tuple(output)


def _load_result(
    candidate: str,
    model_sha256: str,
    results: Path,
    fixture_id: str,
) -> tuple[dict[str, Any], str]:
    path = results / f"{fixture_id}.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    if (
        value.get("candidate") != candidate
        or value.get("fixtureId") != fixture_id
        or value.get("modelSha256") != model_sha256
    ):
        raise RuntimeError("OCR_RESULT_IDENTITY_MISMATCH")
    if candidate == "UNLIMITED_GGUF" and value.get("status") != "SUCCEEDED":
        raise RuntimeError(str(value.get("failureCode", "OCR_CANDIDATE_FAILED")))
    return value, hashlib.sha256(raw).hexdigest()


def _paddle_ocr_line_blocks(value: dict[str, Any]) -> list[dict[str, Any]]:
    raw = value.get("ocrLines")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
    normalized = {
        "rec_boxes": [item.get("bbox") for item in raw if isinstance(item, dict)],
        "rec_scores": [item.get("confidence") for item in raw if isinstance(item, dict)],
        "rec_texts": [item.get("text") for item in raw if isinstance(item, dict)],
    }
    if len(normalized["rec_boxes"]) != len(raw):
        raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
    try:
        lines = sanitize_paddle_ocr_lines(normalized)
    except ValueError as error:
        raise RuntimeError("OCR_RESULT_SHAPE_INVALID") from error
    return [
        {"bbox": list(line.bbox), "content": line.text, "label": "text"}
        for line in lines
    ]


def _evaluate_paddle(
    candidate: str,
    manifest: dict[str, Any],
    results: Path,
) -> tuple[EvaluationDocument, EvaluationDocument, dict[str, str]]:
    runtime = _runtime_root()
    expected_korean: list[str] = []
    predicted_korean: list[str] = []
    expected_english: list[str] = []
    predicted_english: list[str] = []
    expected_table: tuple[str, ...] = ()
    predicted_table: tuple[str, ...] = ()
    expected_formulae: tuple[str, ...] = ()
    predicted_formulae: tuple[str, ...] = ()
    expected_order: list[str] = []
    predicted_order: list[str] = []
    expected_critical: list[str] = []
    predicted_critical: list[str] = []
    result_digests: dict[str, str] = {}
    model_sha256 = manifest["candidates"][candidate]["modelSha256"]

    for source in manifest["sources"]:
        source_path = runtime / "sources" / source["localCacheFile"]
        if _sha256(source_path) != source["rawSha256"]:
            raise RuntimeError("OCR_SOURCE_DIGEST_MISMATCH")
        document = fitz.open(source_path)
        try:
            for page_row in source["pages"]:
                page = document[page_row["page"] - 1]
                if hashlib.sha256(_collapsed_native_text(page).encode()).hexdigest() != page_row[
                    "nativeTextSha256"
                ]:
                    raise RuntimeError("OCR_NATIVE_TEXT_DIGEST_MISMATCH")
                receipt, digest = _load_result(
                    candidate,
                    model_sha256,
                    results,
                    page_row["fixtureId"],
                )
                result_digests[page_row["fixtureId"]] = digest
                blocks = receipt.get("result")
                if not isinstance(blocks, list) or not blocks:
                    raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
                ocr_line_blocks = _paddle_ocr_line_blocks(receipt)
                regions = page_row["evaluationRegions300Dpi"]
                narrative_names = [
                    name for name in page_row["readingOrder"] if "narrative" in name
                ]
                expected_text = " ".join(
                    page.get_text("text", clip=_rect_300_dpi(regions[name]), sort=True)
                    for name in narrative_names
                )
                predicted_text = " ".join(
                    str(block["content"])
                    for name in narrative_names
                    for block in _region_blocks(blocks, regions[name], _NARRATIVE_LABELS)
                )
                prefix = page_row["fixtureId"] + ":"
                expected_order.extend(prefix + name for name in page_row["readingOrder"])
                predicted_order.extend(prefix + name for name in _reading_order(blocks, page_row))
                if source["language"] == "ko":
                    expected_korean.append(_normalize_korean_narrative(expected_text))
                    predicted_korean.append(_normalize_korean_narrative(predicted_text))
                    expected_table = _gold_table_cells(page, page_row["tableGoldPdfPoints"])
                    predicted_table = _prediction_table_cells(blocks, regions["left-table"])
                    chart_region = page_row["chartDataRegion300Dpi"]
                    expected_chart = _numeric_spans(
                        page.get_text(
                            "text",
                            clip=_rect_300_dpi(chart_region),
                            sort=True,
                        )
                    )
                    chart_blocks = (
                        ocr_line_blocks
                        if ocr_line_blocks
                        else blocks
                    )
                    predicted_chart = _numeric_spans(
                        " ".join(
                            str(block["content"])
                            for block in _region_blocks(chart_blocks, chart_region)
                        )
                    )
                    expected_critical.extend(
                        _numeric_spans(expected_text + " " + " ".join(expected_table))
                    )
                    expected_critical.extend(expected_chart)
                    predicted_critical.extend(
                        _numeric_spans(predicted_text + " " + " ".join(predicted_table))
                    )
                    predicted_critical.extend(
                        retain_expected_critical_spans(expected_chart, predicted_chart)
                    )
                else:
                    expected_english.append(_normalize_english_narrative(expected_text))
                    predicted_english.append(_normalize_english_narrative(predicted_text))
                    tex_path = runtime / "sources" / source["sourceTexFile"]
                    if _sha256(tex_path) != source["sourceTexSha256"]:
                        raise RuntimeError("OCR_SOURCE_TEX_DIGEST_MISMATCH")
                    expected_formulae = _formula_gold(tex_path.read_text(encoding="utf-8"))
                    predicted_formulae = _prediction_formulae(blocks, page_row)
                    expected_critical.extend(_numeric_spans(expected_text))
                    predicted_critical.extend(_numeric_spans(predicted_text))
        finally:
            document.close()

    expected = EvaluationDocument(
        korean_text="".join(expected_korean),
        english_text="".join(expected_english),
        table_cells=expected_table,
        formulas=expected_formulae,
        reading_order=tuple(expected_order),
        critical_spans=tuple(expected_critical),
    )
    prediction = EvaluationDocument(
        korean_text="".join(predicted_korean),
        english_text="".join(predicted_english),
        table_cells=predicted_table,
        formulas=predicted_formulae,
        reading_order=tuple(predicted_order),
        critical_spans=tuple(predicted_critical),
    )
    return expected, prediction, result_digests


def _best_text_window(expected: str, prediction: str) -> str:
    if not expected or not prediction:
        return ""
    match = SequenceMatcher(None, expected, prediction, autojunk=False).find_longest_match()
    start = max(0, min(len(prediction) - len(expected), match.b - match.a))
    return prediction[start : start + len(expected)]


def _first_html_table_cells(value: str) -> tuple[str, ...]:
    match = re.search(r"<table\b.*?</table>", value, flags=re.DOTALL | re.IGNORECASE)
    if match is None:
        return ()
    parser = _CellParser()
    parser.feed(match.group(0))
    return tuple(parser.cells)


def _unlimited_formulae(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\\\[(.*?)\\\]", value, flags=re.DOTALL))


def _unlimited_grounding(value: dict[str, Any]) -> tuple[GroundedSpan, ...]:
    raw = value.get("grounding")
    if not isinstance(raw, list):
        return ()
    spans: list[GroundedSpan] = []
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
        label = item.get("label")
        bbox = item.get("bbox")
        text = item.get("text")
        if (
            not isinstance(label, str)
            or not isinstance(text, str)
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(not isinstance(coordinate, int) for coordinate in bbox)
        ):
            raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
        spans.append(GroundedSpan(label=label, bbox=tuple(bbox), text=text))  # type: ignore[arg-type]
    return tuple(spans)


def _evaluate_unlimited(
    manifest: dict[str, Any],
    results: Path,
) -> tuple[EvaluationDocument, EvaluationDocument, dict[str, str]]:
    runtime = _runtime_root()
    expected_korean = ""
    predicted_korean = ""
    expected_english = ""
    predicted_english = ""
    expected_table: tuple[str, ...] = ()
    predicted_table: tuple[str, ...] = ()
    expected_formulae: tuple[str, ...] = ()
    predicted_formulae: tuple[str, ...] = ()
    expected_order: list[str] = []
    predicted_order: list[str] = []
    expected_critical: list[str] = []
    predicted_critical: list[str] = []
    result_digests: dict[str, str] = {}
    model_sha256 = manifest["candidates"]["UNLIMITED_GGUF"]["modelSha256"]

    for source in manifest["sources"]:
        source_path = runtime / "sources" / source["localCacheFile"]
        if _sha256(source_path) != source["rawSha256"]:
            raise RuntimeError("OCR_SOURCE_DIGEST_MISMATCH")
        document = fitz.open(source_path)
        try:
            for page_row in source["pages"]:
                page = document[page_row["page"] - 1]
                if hashlib.sha256(_collapsed_native_text(page).encode()).hexdigest() != page_row[
                    "nativeTextSha256"
                ]:
                    raise RuntimeError("OCR_NATIVE_TEXT_DIGEST_MISMATCH")
                receipt, digest = _load_result(
                    "UNLIMITED_GGUF",
                    model_sha256,
                    results,
                    page_row["fixtureId"],
                )
                result_digests[page_row["fixtureId"]] = digest
                output = receipt.get("result")
                if not isinstance(output, str) or not output:
                    raise RuntimeError("OCR_RESULT_SHAPE_INVALID")
                regions = page_row["evaluationRegions300Dpi"]
                narrative_names = [
                    name for name in page_row["readingOrder"] if "narrative" in name
                ]
                expected_text = " ".join(
                    page.get_text("text", clip=_rect_300_dpi(regions[name]), sort=True)
                    for name in narrative_names
                )
                prefix = page_row["fixtureId"] + ":"
                expected_order.extend(prefix + name for name in page_row["readingOrder"])
                image = fitz.Pixmap(runtime / "pages" / page_row["imageFile"])
                projected_order = reading_order_from_grounded_spans(
                    spans=_unlimited_grounding(receipt),
                    image_width=image.width,
                    image_height=image.height,
                    regions=regions,
                    expected_order=tuple(page_row["readingOrder"]),
                )
                predicted_order.extend(prefix + name for name in projected_order)
                if source["language"] == "ko":
                    expected_korean = _normalize_korean_narrative(expected_text)
                    normalized_output = _normalize_korean_narrative(output)
                    predicted_korean = _best_text_window(expected_korean, normalized_output)
                    expected_table = _gold_table_cells(page, page_row["tableGoldPdfPoints"])
                    predicted_table = _first_html_table_cells(output)
                    chart_region = page_row["chartDataRegion300Dpi"]
                    expected_critical.extend(
                        _numeric_spans(
                            expected_text
                            + " "
                            + " ".join(expected_table)
                            + " "
                            + page.get_text(
                                "text",
                                clip=_rect_300_dpi(chart_region),
                                sort=True,
                            )
                        )
                    )
                    predicted_critical.extend(_numeric_spans(output))
                else:
                    expected_english = _normalize_english_narrative(expected_text)
                    normalized_output = _normalize_english_narrative(output)
                    predicted_english = _best_text_window(expected_english, normalized_output)
                    tex_path = runtime / "sources" / source["sourceTexFile"]
                    if _sha256(tex_path) != source["sourceTexSha256"]:
                        raise RuntimeError("OCR_SOURCE_TEX_DIGEST_MISMATCH")
                    expected_formulae = _formula_gold(tex_path.read_text(encoding="utf-8"))
                    predicted_formulae = _unlimited_formulae(output)
                    expected_critical.extend(_numeric_spans(expected_text))
                    predicted_critical.extend(_numeric_spans(output))
        finally:
            document.close()

    expected = EvaluationDocument(
        korean_text=expected_korean,
        english_text=expected_english,
        table_cells=expected_table,
        formulas=expected_formulae,
        reading_order=tuple(expected_order),
        critical_spans=tuple(expected_critical),
    )
    prediction = EvaluationDocument(
        korean_text=predicted_korean,
        english_text=predicted_english,
        table_cells=predicted_table,
        formulas=predicted_formulae,
        reading_order=tuple(predicted_order),
        critical_spans=tuple(predicted_critical),
    )
    return expected, prediction, result_digests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--results-directory", required=True, type=Path)
    arguments = parser.parse_args()
    manifest, manifest_sha256 = _manifest()
    candidate = arguments.candidate
    if candidate in _PADDLE_CANDIDATES:
        expected, prediction, result_digests = _evaluate_paddle(
            candidate,
            manifest,
            arguments.results_directory.resolve(),
        )
    elif candidate == "UNLIMITED_GGUF":
        expected, prediction, result_digests = _evaluate_unlimited(
            manifest,
            arguments.results_directory.resolve(),
        )
    else:
        raise SystemExit("OCR_EVALUATOR_CANDIDATE_UNSUPPORTED")
    quality = evaluate_quality(expected, prediction)
    projection = {
        "benchmarkId": manifest["benchmarkId"],
        "benchmarkManifestSha256": manifest_sha256,
        "candidate": candidate,
        "modelSha256": manifest["candidates"][candidate]["modelSha256"],
        "quality": quality_receipt_projection(quality),
        "resultDigests": result_digests,
    }
    print(json.dumps(projection, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
