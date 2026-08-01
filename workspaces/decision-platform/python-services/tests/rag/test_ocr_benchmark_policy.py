from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.ocr_benchmark import (
    BenchmarkError,
    CandidateReceipt,
    LaneReceipt,
    QualityReceipt,
    compute_character_error_rate,
    compute_kendall_tau,
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
