from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Iterable, Literal


class BenchmarkError(ValueError):
    """OCR benchmark가 candidate, quality, device evidence 계약을 위반했음을 나타낸다."""


_CANDIDATES: Final[frozenset[str]] = frozenset(
    {"PADDLE_STRUCTURED", "PADDLE_VL", "UNLIMITED_GGUF"}
)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CODE = re.compile(r"^OCR_[A-Z0-9_]{3,96}$")
_GROUNDING_TOKEN = re.compile(
    r"<\|(ref|det)\|>(.*?)<\|/\1\|>",
    flags=re.DOTALL,
)
_GROUNDING_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_MAX_GROUNDED_OUTPUT_CHARACTERS = 16 * 1024 * 1024
_MAX_GROUNDED_SPANS = 2_048
_MAX_GROUNDED_SPAN_CHARACTERS = 4_096
_MAX_OCR_LINES = 10_000


@dataclass(frozen=True, slots=True)
class QualityReceipt:
    """동일 gold fixture에서 계산된 OCR 품질 gate 결과다."""

    korean_cer: float
    english_cer: float
    critical_span_errors: int
    table_cell_f1: float
    formula_accuracy: float
    reading_order_kendall_tau: float
    hallucinated_critical_spans: int


@dataclass(frozen=True, slots=True)
class EvaluationDocument:
    """로컬 gold/prediction의 품질 계산 입력이며 receipt에는 원문을 투영하지 않는다."""

    korean_text: str
    english_text: str
    table_cells: tuple[str, ...]
    formulas: tuple[str, ...]
    reading_order: tuple[str, ...]
    critical_spans: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroundedSpan:
    """Unlimited-OCR가 반환한 0..1000 좌표계의 bounded text span이다."""

    label: str
    bbox: tuple[int, int, int, int]
    text: str


@dataclass(frozen=True, slots=True)
class GroundedOcrOutput:
    """grounding token을 제거한 text와 검증된 locator만 담는 local projection이다."""

    text: str
    spans: tuple[GroundedSpan, ...]


@dataclass(frozen=True, slots=True)
class OcrLine:
    """Paddle overall OCR에서 path/raw image 없이 남기는 bounded line projection이다."""

    bbox: tuple[int, int, int, int]
    confidence: float
    text: str


@dataclass(frozen=True, slots=True)
class LaneReceipt:
    """candidate가 특정 physical lane에서 실제 실행된 bounded evidence다."""

    lane: str
    executed: bool
    device_name: str
    normalized_pages_per_minute: float
    peak_memory_bytes: int
    install_bytes: int
    artifact_sha256: str
    openvino_device: str | None
    openvino_compile_infer_verified: bool
    silent_fallback_detected: bool


@dataclass(frozen=True, slots=True)
class CandidateReceipt:
    """candidate version/model과 quality/device receipts를 하나로 결속한다."""

    candidate: str
    candidate_version: str
    model_sha256: str
    quality: QualityReceipt | None
    lanes: tuple[LaneReceipt, ...]
    status: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED"
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProductionSelection:
    """세 candidate 중 모든 gate를 통과한 단일 production backend 선택 결과다."""

    candidate: str
    candidate_version: str
    model_sha256: str
    normalized_slowest_lane_throughput: float
    receipt: CandidateReceipt


def compute_character_error_rate(reference: str, prediction: str) -> float:
    """NFKC Unicode codepoint 단위 Levenshtein CER을 결정적으로 계산한다."""

    expected = unicodedata.normalize("NFKC", reference)
    actual = unicodedata.normalize("NFKC", prediction)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row_index, expected_character in enumerate(expected, start=1):
        current = [row_index]
        for column_index, actual_character in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column_index] + 1,
                    previous[column_index - 1] + (expected_character != actual_character),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def compute_kendall_tau(reference: tuple[str, ...], prediction: tuple[str, ...]) -> float:
    """동일 unique block ID 집합의 reading-order Kendall tau-a를 계산한다."""

    if len(reference) < 2 or len(set(reference)) != len(reference) or set(reference) != set(prediction):
        raise BenchmarkError("OCR_READING_ORDER_INPUT_INVALID")
    positions = {value: index for index, value in enumerate(prediction)}
    concordant = 0
    discordant = 0
    for left in range(len(reference)):
        for right in range(left + 1, len(reference)):
            if positions[reference[left]] < positions[reference[right]]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / (concordant + discordant)


def evaluate_quality(
    expected: EvaluationDocument,
    prediction: EvaluationDocument,
) -> QualityReceipt:
    """gold와 candidate output을 비교해 원문 없는 deterministic quality receipt를 만든다."""

    expected_critical = Counter(_normalize_span(value) for value in expected.critical_spans)
    predicted_critical = Counter(_normalize_span(value) for value in prediction.critical_spans)
    missing_critical = sum((expected_critical - predicted_critical).values())
    hallucinated_critical = sum((predicted_critical - expected_critical).values())
    try:
        reading_order = compute_kendall_tau(expected.reading_order, prediction.reading_order)
    except BenchmarkError:
        reading_order = -1.0
    return QualityReceipt(
        korean_cer=compute_character_error_rate(
            _normalize_text(expected.korean_text),
            _normalize_text(prediction.korean_text),
        ),
        english_cer=compute_character_error_rate(
            _normalize_text(expected.english_text),
            _normalize_text(prediction.english_text),
        ),
        critical_span_errors=missing_critical,
        table_cell_f1=_multiset_f1(expected.table_cells, prediction.table_cells),
        formula_accuracy=_formula_accuracy(expected.formulas, prediction.formulas),
        reading_order_kendall_tau=reading_order,
        hallucinated_critical_spans=hallucinated_critical,
    )


def quality_receipt_projection(value: QualityReceipt) -> dict[str, float | int]:
    """평가 원문·경로 없이 공개 가능한 quality metric만 projection한다."""

    return {
        "criticalSpanErrors": value.critical_span_errors,
        "englishCer": value.english_cer,
        "formulaAccuracy": value.formula_accuracy,
        "hallucinatedCriticalSpans": value.hallucinated_critical_spans,
        "koreanCer": value.korean_cer,
        "readingOrderKendallTau": value.reading_order_kendall_tau,
        "tableCellF1": value.table_cell_f1,
    }


def retain_expected_critical_spans(
    expected: tuple[str, ...],
    prediction: tuple[str, ...],
) -> tuple[str, ...]:
    """chart parser의 파생 series 중 원문에 인쇄된 critical span만 평가 대상으로 남긴다.

    chart-to-table은 plot 좌표에서 추가 값을 유도할 수 있으므로 그 값을 원문 OCR hallucination으로
    세지 않는다. 대신 원문에 실제 인쇄된 숫자의 누락·중복 상한은 multiset으로 보존한다.
    """

    remaining = Counter(_normalize_span(value) for value in expected)
    retained: list[str] = []
    for value in prediction:
        normalized = _normalize_span(value)
        if remaining[normalized] <= 0:
            continue
        remaining[normalized] -= 1
        retained.append(value)
    return tuple(retained)


def parse_grounded_ocr_output(value: str) -> GroundedOcrOutput:
    """두 Unlimited-OCR grounding 표기를 bounded text와 normalized bbox로 정규화한다.

    GGUF runtime은 `det → text`와 `ref text → det`를 모두 출력할 수 있다. 잘못된 bbox는
    locator에서 제외하되 OCR text 자체는 보존하여 품질 실패가 숨겨지지 않게 한다.
    """

    if not isinstance(value, str) or len(value) > _MAX_GROUNDED_OUTPUT_CHARACTERS:
        raise BenchmarkError("OCR_GROUNDING_OUTPUT_INVALID")
    matches = list(_GROUNDING_TOKEN.finditer(value))
    spans: list[GroundedSpan] = []
    pending_reference: str | None = None
    for index, match in enumerate(matches):
        kind = match.group(1)
        body = match.group(2)
        if kind == "ref":
            pending_reference = _bounded_grounding_text(body)
            continue
        bbox = _grounding_bbox(body)
        if bbox is None:
            pending_reference = None
            continue
        if pending_reference:
            content = pending_reference
            pending_reference = None
        else:
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(value)
            content = _bounded_grounding_text(value[match.end() : next_start])
        if not content:
            continue
        prefix = body.split("[", maxsplit=1)[0].strip()
        label = prefix if _GROUNDING_LABEL.fullmatch(prefix) else "text"
        spans.append(GroundedSpan(label=label, bbox=bbox, text=content))
        if len(spans) >= _MAX_GROUNDED_SPANS:
            break

    def replace_token(match: re.Match[str]) -> str:
        return match.group(2) if match.group(1) == "ref" else ""

    text = _GROUNDING_TOKEN.sub(replace_token, value).strip()
    return GroundedOcrOutput(text=text, spans=tuple(spans))


def reading_order_from_grounded_spans(
    *,
    spans: tuple[GroundedSpan, ...],
    image_width: int,
    image_height: int,
    regions: Mapping[str, Sequence[int]],
    expected_order: tuple[str, ...],
) -> tuple[str, ...]:
    """0..1000 grounding bbox 중심을 benchmark page region 순서로 투영한다."""

    if image_width <= 0 or image_height <= 0 or len(set(expected_order)) != len(expected_order):
        raise BenchmarkError("OCR_GROUNDING_REGION_INVALID")
    output: list[str] = []
    for span in spans:
        x = ((span.bbox[0] + span.bbox[2]) / 2_000) * image_width
        y = ((span.bbox[1] + span.bbox[3]) / 2_000) * image_height
        for name in expected_order:
            region = regions.get(name)
            if region is None or len(region) != 4:
                raise BenchmarkError("OCR_GROUNDING_REGION_INVALID")
            x0, y0, x1, y1 = region
            if name not in output and x0 <= x <= x1 and y0 <= y <= y1:
                output.append(name)
                break
    return tuple(output)


def sanitize_paddle_ocr_lines(value: object) -> tuple[OcrLine, ...]:
    """Paddle overall OCR result의 세 병렬 배열을 strict bounded line tuple로 바꾼다."""

    if not isinstance(value, dict):
        raise BenchmarkError("OCR_RESULT_SHAPE_INVALID")
    boxes = value.get("rec_boxes")
    scores = value.get("rec_scores")
    texts = value.get("rec_texts")
    if (
        not isinstance(boxes, list)
        or not isinstance(scores, list)
        or not isinstance(texts, list)
        or not len(boxes) == len(scores) == len(texts)
        or len(boxes) > _MAX_OCR_LINES
    ):
        raise BenchmarkError("OCR_RESULT_SHAPE_INVALID")
    output: list[OcrLine] = []
    for bbox, score, text in zip(boxes, scores, texts, strict=True):
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                for item in bbox
            )
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not 0 <= float(score) <= 1
            or not isinstance(text, str)
            or "\x00" in text
            or len(text) > _MAX_GROUNDED_SPAN_CHARACTERS
        ):
            raise BenchmarkError("OCR_RESULT_SHAPE_INVALID")
        if not text:
            # Paddle는 검출 confidence와 무관하게 recognition이 제거한 line을 빈 text로
            # 남길 수 있다. 내용 없는 line은 Document IR에 기여하지 않으므로 폐기한다.
            continue
        raw_x0, raw_y0, raw_x1, raw_y1 = (float(item) for item in bbox)
        if not (0 <= raw_x0 < raw_x1 and 0 <= raw_y0 < raw_y1):
            raise BenchmarkError("OCR_RESULT_SHAPE_INVALID")
        x0 = math.floor(raw_x0)
        y0 = math.floor(raw_y0)
        x1 = math.ceil(raw_x1)
        y1 = math.ceil(raw_y1)
        output.append(
            OcrLine(
                bbox=(x0, y0, x1, y1),
                confidence=float(score),
                text=text,
            )
        )
    return tuple(output)


def _grounding_bbox(value: str) -> tuple[int, int, int, int] | None:
    coordinates = tuple(int(item) for item in re.findall(r"-?\d+", value))
    if len(coordinates) < 4:
        return None
    x0, y0, x1, y1 = coordinates[:4]
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    return x0, y0, x1, y1


def _bounded_grounding_text(value: str) -> str:
    normalized = value.replace("\x00", "").strip()
    return normalized[:_MAX_GROUNDED_SPAN_CHARACTERS]


def _normalize_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _normalize_span(value: str) -> str:
    return _normalize_text(value).casefold()


def _normalize_formula(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\\label\{[^{}]+\}", "", normalized)
    normalized = re.sub(r"\\(?:begin|end)\{[^{}]+\}", "", normalized)
    for token in ("\\left", "\\right", "\\[", "\\]", "$$", "$"):
        normalized = normalized.replace(token, "")
    normalized = re.sub(r"\\(?:textrm|text|mathrm)\{and\}", "and", normalized)
    normalized = normalized.replace("\\ldots", "\\dots").replace("\\cdots", "\\dots")
    normalized = normalized.replace("\\Vert", "\\norm").replace("\\|", "\\norm")
    normalized = normalized.replace("\\ud", "d").replace("\\mathrm{d}", "d")
    normalized = normalized.replace("&", "").replace("\\\\", "")
    normalized = normalized.replace("{", "").replace("}", "")
    return "".join(normalized.split())


def _multiset_f1(expected: tuple[str, ...], prediction: tuple[str, ...]) -> float:
    expected_values = Counter(_normalize_span(value) for value in expected)
    predicted_values = Counter(_normalize_span(value) for value in prediction)
    if not expected_values:
        return 1.0 if not predicted_values else 0.0
    true_positive = sum((expected_values & predicted_values).values())
    if true_positive == 0:
        return 0.0
    precision = true_positive / sum(predicted_values.values())
    recall = true_positive / sum(expected_values.values())
    return 2 * precision * recall / (precision + recall)


def _formula_accuracy(expected: tuple[str, ...], prediction: tuple[str, ...]) -> float:
    if not expected:
        return 1.0 if not prediction else 0.0
    maximum = max(len(expected), len(prediction))
    scores = [
        1.0
        - min(
            1.0,
            compute_character_error_rate(
                _normalize_formula(expected[index]),
                _normalize_formula(prediction[index]),
            ),
        )
        if index < len(expected) and index < len(prediction)
        else 0.0
        for index in range(maximum)
    ]
    return sum(scores) / maximum


def validate_benchmark_receipt(value: CandidateReceipt) -> None:
    """quality와 CPU/Intel GPU physical evidence가 모두 맞는 candidate만 승인한다."""

    if (
        value.candidate not in _CANDIDATES
        or not value.candidate_version
        or _HASH.fullmatch(value.model_sha256) is None
    ):
        raise BenchmarkError("OCR_CANDIDATE_INVALID")
    if value.status == "FAILED":
        if value.failure_code is None or _FAILURE_CODE.fullmatch(value.failure_code) is None:
            raise BenchmarkError("OCR_FAILED_RECEIPT_INVALID")
        return
    if value.status != "SUCCEEDED" or value.failure_code is not None or value.quality is None:
        raise BenchmarkError("OCR_CANDIDATE_INVALID")
    quality = value.quality
    if (
        not 0 <= quality.korean_cer <= 0.02
        or not 0 <= quality.english_cer <= 0.01
        or quality.critical_span_errors != 0
        or not 0.95 <= quality.table_cell_f1 <= 1
        or not 0.95 <= quality.formula_accuracy <= 1
        or not 0.98 <= quality.reading_order_kendall_tau <= 1
        or quality.hallucinated_critical_spans != 0
    ):
        raise BenchmarkError("OCR_QUALITY_GATE_FAILED")
    lanes = {lane.lane: lane for lane in value.lanes}
    if set(lanes) != {"CPU", "INTEL_GPU"}:
        raise BenchmarkError("OCR_REQUIRED_LANE_MISSING")
    intel = lanes["INTEL_GPU"]
    if (
        intel.openvino_device != "GPU"
        or not intel.openvino_compile_infer_verified
        or intel.silent_fallback_detected
    ):
        raise BenchmarkError("OCR_INTEL_GPU_EVIDENCE_INVALID")
    for lane in lanes.values():
        if (
            not lane.executed
            or not lane.device_name
            or lane.normalized_pages_per_minute <= 0
            or lane.peak_memory_bytes <= 0
            or lane.install_bytes <= 0
            or _HASH.fullmatch(lane.artifact_sha256) is None
            or (lane.lane != "INTEL_GPU" and lane.silent_fallback_detected)
        ):
            raise BenchmarkError("OCR_LANE_EVIDENCE_INVALID")


def select_production_backend(values: Iterable[CandidateReceipt]) -> ProductionSelection:
    """세 후보의 slower-lane throughput을 최대화하고 memory/install size로 동률을 깬다."""

    receipts = tuple(values)
    if len(receipts) != 3 or {receipt.candidate for receipt in receipts} != _CANDIDATES:
        raise BenchmarkError("OCR_CANDIDATE_SET_INCOMPLETE")
    for receipt in receipts:
        validate_benchmark_receipt(receipt)
    eligible = tuple(receipt for receipt in receipts if receipt.status == "SUCCEEDED")
    if not eligible:
        raise BenchmarkError("OCR_NO_PRODUCTION_BACKEND")

    def rank(receipt: CandidateReceipt) -> tuple[float, int, int, str]:
        slowest = min(lane.normalized_pages_per_minute for lane in receipt.lanes)
        peak = max(lane.peak_memory_bytes for lane in receipt.lanes)
        install = max(lane.install_bytes for lane in receipt.lanes)
        return (slowest, -peak, -install, receipt.candidate)

    selected = max(eligible, key=rank)
    return ProductionSelection(
        candidate=selected.candidate,
        candidate_version=selected.candidate_version,
        model_sha256=selected.model_sha256,
        normalized_slowest_lane_throughput=min(
            lane.normalized_pages_per_minute for lane in selected.lanes
        ),
        receipt=selected,
    )
