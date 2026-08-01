from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.ocr_benchmark import (
    BenchmarkError,
    CandidateReceipt,
    EvaluationDocument,
    GroundedSpan,
    OcrLine,
    LaneReceipt,
    QualityReceipt,
    compute_character_error_rate,
    compute_kendall_tau,
    evaluate_quality,
    quality_receipt_projection,
    parse_grounded_ocr_output,
    retain_expected_critical_spans,
    reading_order_from_grounded_spans,
    sanitize_paddle_ocr_lines,
    select_production_backend,
    validate_benchmark_receipt,
)


def _quality(**overrides: object) -> QualityReceipt:
    values: dict[str, object] = {
        "korean_cer": 0.01,
        "english_cer": 0.005,
        "critical_span_errors": 0,
        "table_cell_f1": 0.97,
        "formula_accuracy": 0.96,
        "reading_order_kendall_tau": 0.99,
        "hallucinated_critical_spans": 0,
    }
    values.update(overrides)
    return QualityReceipt(**values)  # type: ignore[arg-type]


def _lane(kind: str, throughput: float, *, openvino: bool = False) -> LaneReceipt:
    return LaneReceipt(
        lane=kind,
        executed=True,
        device_name="fixture-device",
        normalized_pages_per_minute=throughput,
        peak_memory_bytes=1_000_000_000,
        install_bytes=2_000_000_000,
        artifact_sha256="a" * 64,
        openvino_device="GPU" if openvino else None,
        openvino_compile_infer_verified=openvino,
        silent_fallback_detected=False,
    )


def _candidate(name: str, cpu: float, intel: float) -> CandidateReceipt:
    return CandidateReceipt(
        candidate=name,
        candidate_version="pinned-fixture",
        model_sha256="b" * 64,
        quality=_quality(),
        lanes=(_lane("CPU", cpu), _lane("INTEL_GPU", intel, openvino=True)),
    )


def _failed_candidate(name: str, failure_code: str) -> CandidateReceipt:
    return CandidateReceipt(
        candidate=name,
        candidate_version="pinned-fixture",
        model_sha256="b" * 64,
        quality=None,
        lanes=(),
        status="FAILED",
        failure_code=failure_code,
    )


def test_metric_functions_use_deterministic_unicode_and_order_contracts() -> None:
    assert compute_character_error_rate("금리 3.5%", "금리 3.5%") == 0
    assert compute_character_error_rate("abcd", "abxd") == pytest.approx(0.25)
    assert compute_kendall_tau(("a", "b", "c"), ("a", "c", "b")) == pytest.approx(1 / 3)


def test_objective_quality_evaluator_scores_text_cells_formula_order_and_critical_spans() -> None:
    expected = EvaluationDocument(
        korean_text="기준금리는 3.50%다",
        english_text="The option value is 12.5.",
        table_cells=("평균", "113.0", "중위값", "107.3"),
        formulas=(r"U^k-U^{k-1}=0",),
        reading_order=("left-top", "left-table", "right-top"),
        critical_spans=("3.50%", "12.5", "113.0", "107.3"),
    )

    receipt = evaluate_quality(expected, expected)

    assert receipt == _quality(
        korean_cer=0,
        english_cer=0,
        table_cell_f1=1,
        formula_accuracy=1,
        reading_order_kendall_tau=1,
    )
    projection = quality_receipt_projection(receipt)
    assert set(projection) == {
        "criticalSpanErrors",
        "englishCer",
        "formulaAccuracy",
        "hallucinatedCriticalSpans",
        "koreanCer",
        "readingOrderKendallTau",
        "tableCellF1",
    }
    assert "기준금리" not in json.dumps(projection, ensure_ascii=False)


def test_objective_quality_evaluator_detects_missing_and_hallucinated_financial_values() -> None:
    expected = EvaluationDocument(
        korean_text="총자산은 1,053.1조원이다",
        english_text="The rate is -0.25%.",
        table_cells=("1,053.1", "-0.25%"),
        formulas=(r"rU^k=0",),
        reading_order=("a", "b", "c"),
        critical_spans=("1,053.1", "-0.25%"),
    )
    prediction = EvaluationDocument(
        korean_text="총자산은 1,053.7조원이다",
        english_text="The rate is 0.25%.",
        table_cells=("1,053.7", "0.25%"),
        formulas=(r"rU^j=0",),
        reading_order=("a", "c", "b"),
        critical_spans=("1,053.7", "0.25%", "999"),
    )

    receipt = evaluate_quality(expected, prediction)

    assert receipt.critical_span_errors == 2
    assert receipt.hallucinated_critical_spans == 3
    assert receipt.table_cell_f1 == 0
    assert receipt.formula_accuracy < 1
    assert receipt.reading_order_kendall_tau == pytest.approx(1 / 3)


def test_chart_derived_series_do_not_become_printed_critical_span_hallucinations() -> None:
    assert retain_expected_critical_spans(
        expected=("1,053.1", "0.1%", "0.1%", "119.3"),
        prediction=("1,053.1", "0.1%", "2.37", "999", "0.1%", "0.1%"),
    ) == ("1,053.1", "0.1%", "0.1%")


def test_unlimited_grounding_parser_preserves_text_and_normalized_boxes() -> None:
    parsed = parse_grounded_ocr_output(
        "<|det|>title [10, 20, 200, 80]<|/det|>Heading\n"
        "<|ref|>Value 3.5%<|/ref|>"
        "<|det|>[[40, 100, 900, 180]]<|/det|>"
    )

    assert parsed.text == "Heading\nValue 3.5%"
    assert parsed.spans == (
        GroundedSpan(label="title", bbox=(10, 20, 200, 80), text="Heading"),
        GroundedSpan(label="text", bbox=(40, 100, 900, 180), text="Value 3.5%"),
    )


def test_unlimited_grounding_parser_drops_malformed_boxes_but_keeps_bounded_text() -> None:
    parsed = parse_grounded_ocr_output(
        "<|det|>text [-1, 20, 200, 80]<|/det|>Kept text"
    )

    assert parsed.text == "Kept text"
    assert parsed.spans == ()


def test_grounded_spans_project_to_page_reading_order_without_duplicate_regions() -> None:
    spans = (
        GroundedSpan(label="text", bbox=(100, 100, 400, 200), text="left"),
        GroundedSpan(label="text", bbox=(600, 100, 900, 200), text="right"),
        GroundedSpan(label="text", bbox=(620, 120, 880, 180), text="same right"),
    )

    assert reading_order_from_grounded_spans(
        spans=spans,
        image_width=2000,
        image_height=3000,
        regions={"left": (100, 100, 900, 900), "right": (1100, 100, 1900, 900)},
        expected_order=("left", "right"),
    ) == ("left", "right")


def test_paddle_overall_ocr_lines_are_bounded_and_shape_checked() -> None:
    lines = sanitize_paddle_ocr_lines(
        {
            "rec_boxes": [[10, 20, 200, 80], [20, 90, 210, 150]],
            "rec_scores": [0.999, 0.98],
            "rec_texts": ["총자산 1,053.1조원", "증가율 0.1%"],
        }
    )

    assert lines == (
        OcrLine(bbox=(10, 20, 200, 80), confidence=0.999, text="총자산 1,053.1조원"),
        OcrLine(bbox=(20, 90, 210, 150), confidence=0.98, text="증가율 0.1%"),
    )

    with pytest.raises(BenchmarkError, match="OCR_RESULT_SHAPE_INVALID"):
        sanitize_paddle_ocr_lines(
            {"rec_boxes": [[10, 20, 200, 80]], "rec_scores": [], "rec_texts": ["x"]}
        )


@pytest.mark.parametrize(
    "quality",
    [
        _quality(korean_cer=0.021),
        _quality(english_cer=0.011),
        _quality(critical_span_errors=1),
        _quality(table_cell_f1=0.949),
        _quality(formula_accuracy=0.949),
        _quality(reading_order_kendall_tau=0.979),
        _quality(hallucinated_critical_spans=1),
    ],
)
def test_every_quality_threshold_is_a_hard_gate(quality: QualityReceipt) -> None:
    receipt = CandidateReceipt(
        candidate="PADDLE_STRUCTURED",
        candidate_version="pinned-fixture",
        model_sha256="b" * 64,
        quality=quality,
        lanes=(_lane("CPU", 2), _lane("INTEL_GPU", 3, openvino=True)),
    )

    with pytest.raises(BenchmarkError, match="OCR_QUALITY_GATE_FAILED"):
        validate_benchmark_receipt(receipt)


def test_cpu_and_real_intel_gpu_receipts_are_required_without_silent_fallback() -> None:
    missing_intel = CandidateReceipt(
        candidate="PADDLE_STRUCTURED",
        candidate_version="pinned-fixture",
        model_sha256="b" * 64,
        quality=_quality(),
        lanes=(_lane("CPU", 2),),
    )
    fallback = CandidateReceipt(
        candidate="PADDLE_STRUCTURED",
        candidate_version="pinned-fixture",
        model_sha256="b" * 64,
        quality=_quality(),
        lanes=(
            _lane("CPU", 2),
            LaneReceipt(
                lane="INTEL_GPU",
                executed=True,
                device_name="Intel Arc fixture",
                normalized_pages_per_minute=3,
                peak_memory_bytes=1,
                install_bytes=1,
                artifact_sha256="c" * 64,
                openvino_device="CPU",
                openvino_compile_infer_verified=False,
                silent_fallback_detected=True,
            ),
        ),
    )

    with pytest.raises(BenchmarkError, match="OCR_REQUIRED_LANE_MISSING"):
        validate_benchmark_receipt(missing_intel)
    with pytest.raises(BenchmarkError, match="OCR_INTEL_GPU_EVIDENCE_INVALID"):
        validate_benchmark_receipt(fallback)


def test_selection_maximizes_slower_lane_then_breaks_tie_by_memory_and_install_size() -> None:
    structured = _candidate("PADDLE_STRUCTURED", cpu=7, intel=9)
    vl = _candidate("PADDLE_VL", cpu=5, intel=20)
    unlimited = _candidate("UNLIMITED_GGUF", cpu=6, intel=6)

    selected = select_production_backend((structured, vl, unlimited))

    assert selected.candidate == "PADDLE_STRUCTURED"
    assert selected.normalized_slowest_lane_throughput == 7


def test_failed_research_candidates_are_retained_but_not_considered_for_production() -> None:
    selected = select_production_backend(
        (
            _candidate("PADDLE_STRUCTURED", cpu=7, intel=9),
            _failed_candidate("PADDLE_VL", "OCR_INTEL_GPU_UNSUPPORTED"),
            _failed_candidate("UNLIMITED_GGUF", "OCR_QUALITY_GATE_FAILED"),
        )
    )

    assert selected.candidate == "PADDLE_STRUCTURED"


def test_release_is_blocked_when_every_benchmarked_candidate_fails() -> None:
    with pytest.raises(BenchmarkError, match="OCR_NO_PRODUCTION_BACKEND"):
        select_production_backend(
            tuple(
                _failed_candidate(name, "OCR_QUALITY_GATE_FAILED")
                for name in ("PADDLE_STRUCTURED", "PADDLE_VL", "UNLIMITED_GGUF")
            )
        )


def test_failed_candidate_requires_a_stable_failure_code() -> None:
    invalid = CandidateReceipt(
        candidate="PADDLE_STRUCTURED",
        candidate_version="pinned-fixture",
        model_sha256="b" * 64,
        quality=None,
        lanes=(),
        status="FAILED",
        failure_code=None,
    )

    with pytest.raises(BenchmarkError, match="OCR_FAILED_RECEIPT_INVALID"):
        select_production_backend(
            (
                invalid,
                _failed_candidate("PADDLE_VL", "OCR_QUALITY_GATE_FAILED"),
                _failed_candidate("UNLIMITED_GGUF", "OCR_QUALITY_GATE_FAILED"),
            )
        )


def test_unapproved_candidate_or_partial_candidate_set_cannot_be_selected() -> None:
    with pytest.raises(BenchmarkError, match="OCR_CANDIDATE_SET_INCOMPLETE"):
        select_production_backend((_candidate("PADDLE_STRUCTURED", 2, 2),))

    invalid = CandidateReceipt(
        candidate="UNRELATED_OCR",
        candidate_version="fixture",
        model_sha256="b" * 64,
        quality=_quality(),
        lanes=(_lane("CPU", 2), _lane("INTEL_GPU", 2, openvino=True)),
    )
    with pytest.raises(BenchmarkError, match="OCR_CANDIDATE_INVALID"):
        validate_benchmark_receipt(invalid)


def test_benchmark_manifest_pins_sources_pages_dpi_models_and_runtime_artifacts() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    manifest = json.loads(
        (repository_root / "capstone-rag/ocr/benchmark/benchmark-manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["dpi"] == 300
    assert [source["sourceId"] for source in manifest["sources"]] == [
        "bok-financial-stability-report-2026",
        "arxiv-2403.00746",
    ]
    assert all(source["canonicalUrl"].startswith("https://") for source in manifest["sources"])
    assert all(source["downloadUrl"].startswith("https://") for source in manifest["sources"])
    assert all(len(source["rawSha256"]) == 64 for source in manifest["sources"])
    assert all(source["pages"] for source in manifest["sources"])
    assert set(manifest["candidates"]) == {
        "PADDLE_STRUCTURED",
        "PADDLE_VL",
        "UNLIMITED_GGUF",
    }
    assert all(len(value["modelSha256"]) == 64 for value in manifest["candidates"].values())
    assert len(manifest["unlimitedGguf"]["llamaCppCommit"]) == 40
    assert len(manifest["unlimitedGguf"]["containerImageDigest"].removeprefix("sha256:")) == 64


def test_unlimited_runner_uses_the_pinned_official_deterministic_ocr_prompt() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    runner = (
        repository_root / "capstone-rag/ocr/benchmark/unlimited_candidate_runner.py"
    ).read_text(encoding="utf-8")

    assert "<|grounding|>Convert the document to markdown." in runner
    assert "--temp 0" in runner
    assert "--repeat-penalty 1.05" in runner
    assert "--dry-multiplier" not in runner


def test_paddle_runner_bounds_each_region_generation_to_the_official_chart_limit() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    runner = (
        repository_root / "capstone-rag/ocr/benchmark/paddle_candidate_runner.py"
    ).read_text(encoding="utf-8")

    assert "max_new_tokens=2048" in runner


def test_candidate_runners_publish_only_through_the_safe_receipt_writer() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    for filename in ("paddle_candidate_runner.py", "unlimited_candidate_runner.py"):
        runner = (repository_root / "capstone-rag/ocr/benchmark" / filename).read_text(
            encoding="utf-8"
        )

        assert "write_benchmark_receipt" in runner
        assert ".write_text(" not in runner
        assert "os.path.abspath" in runner

    unlimited = (
        repository_root / "capstone-rag/ocr/benchmark/unlimited_candidate_runner.py"
    ).read_text(encoding="utf-8")
    assert "parse_grounded_ocr_output" in unlimited
    paddle = (
        repository_root / "capstone-rag/ocr/benchmark/paddle_candidate_runner.py"
    ).read_text(encoding="utf-8")
    assert "sanitize_paddle_ocr_lines" in paddle
