from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import onnxruntime as ort  # type: ignore[import-untyped]
import tokenizers

from app.rag.bge_acquisition import (
    DEFAULT_MODEL_MANIFEST,
    DEFAULT_MODEL_ROOT,
    verify_bge_completion_manifest,
)
from app.rag.bge_full_generation import (
    BgeBatchBenchmarkReceipt,
    BgeFullGenerationError,
    execute_bge_full_generation,
    prepare_bge_full_generation,
)
from app.rag.bge_runtime import BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.source_card_corpus import load_frozen_source_card_corpus

_CANDIDATES = (16, 32, 64)
_CHILD_MARKER = "S4_2B_BATCH_CHILD "
_MIN_HOST_HEADROOM_BYTES = 4 * 1024 * 1024 * 1024


class _DiscardingRepository:
    def materialize(
        self,
        *,
        plan: Any,
        rows: tuple[Any, ...],
        aggregate_row_hash: str,
        generation_vector_hash: str,
    ) -> Any:
        from app.rag.bge_full_generation import BgeGenerationDatabaseReceipt

        return BgeGenerationDatabaseReceipt(
            generation_id=plan.generation_id,
            materialization_run_id=plan.materialization_run_id,
            final_row_count=len(rows),
            status="MATERIALIZED",
            aggregate_row_hash=aggregate_row_hash,
            generation_vector_hash=generation_vector_hash,
            active_pointer_changed=False,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """로컬 pinned model만 사용해 16/32/64 memory benchmark receipt를 출력한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-child", type=int, choices=_CANDIDATES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.batch_child is not None:
            report = _run_child(args.batch_child)
            print(_CHILD_MARKER + json.dumps(report, sort_keys=True))
            return 0
        report = _run_parent()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                "S4_2B_BATCH_MEMORY_BENCHMARK "
                + json.dumps(report, sort_keys=True)
            )
        return 0
    except (BgeFullGenerationError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(
            "S4_2B_BATCH_MEMORY_BENCHMARK_FAILED "
            + type(error).__name__,
            file=sys.stderr,
        )
        return 1


def _run_parent() -> dict[str, Any]:
    total_memory_bytes = _total_memory_bytes()
    memory_limit_bytes = min(
        int(total_memory_bytes * 0.70),
        total_memory_bytes - _MIN_HOST_HEADROOM_BYTES,
    )
    if memory_limit_bytes <= 0:
        raise BgeFullGenerationError("BATCH_HOST_MEMORY")
    child_results = tuple(_run_child_process(candidate) for candidate in _CANDIDATES)
    safe_candidates = [
        result["batchSize"]
        for result in child_results
        if result["peakRssBytes"] <= memory_limit_bytes
    ]
    # exact corpus는 30개이므로 한 physical batch에 담는 최소 safe candidate를 선택한다.
    selected = next(
        (candidate for candidate in safe_candidates if candidate >= 30),
        max(safe_candidates, default=0),
    )
    if selected not in _CANDIDATES:
        raise BgeFullGenerationError("BATCH_MEMORY_HEADROOM")
    environment = _environment_payload()
    environment_fingerprint = _canonical_json_hash(environment)
    payload: dict[str, Any] = {
        "schemaVersion": "s4-2b-batch-memory-benchmark/v1",
        "status": "PASS",
        "corpusManifestSha256": (
            load_frozen_source_card_corpus().corpus_manifest_sha256
        ),
        "modelRevision": verify_bge_completion_manifest(
            DEFAULT_MODEL_ROOT,
            manifest_path=DEFAULT_MODEL_MANIFEST,
        ).revision,
        "artifactManifestSha256": verify_bge_completion_manifest(
            DEFAULT_MODEL_ROOT,
            manifest_path=DEFAULT_MODEL_MANIFEST,
        ).file_manifest_sha256,
        "candidateBatchSizes": list(_CANDIDATES),
        "results": list(child_results),
        "selectionPolicy": (
            "LOWEST_SAFE_ONE_PHYSICAL_BATCH_THEN_LARGEST_SAFE"
        ),
        "selectedBatchSize": selected,
        "totalMemoryBytes": total_memory_bytes,
        "memoryLimitBytes": memory_limit_bytes,
        "minimumHostHeadroomBytes": _MIN_HOST_HEADROOM_BYTES,
        "environment": environment,
        "environmentFingerprintSha256": environment_fingerprint,
        "networkCalls": 0,
        "physicalCalls": {"voyage": 0, "gemini": 0, "openai": 0},
    }
    payload["benchmarkSha256"] = _canonical_json_hash(payload)
    return payload


def _run_child_process(batch_size: int) -> dict[str, int | float]:
    environment = os.environ.copy()
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "RAG_SOURCE_REGISTER_TARGET": "offline",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.rag.bge_full_generation_benchmark_cli",
            "--batch-child",
            str(batch_size),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(_CHILD_MARKER)
    ]
    if len(lines) != 1:
        raise BgeFullGenerationError("BATCH_CHILD_RECEIPT")
    value = json.loads(lines[0][len(_CHILD_MARKER) :])
    if (
        not isinstance(value, dict)
        or value.get("batchSize") != batch_size
        or type(value.get("peakRssBytes")) is not int
        or not isinstance(value.get("elapsedMs"), (int, float))
    ):
        raise BgeFullGenerationError("BATCH_CHILD_SHAPE")
    return {
        "batchSize": batch_size,
        "effectiveLargestBatch": min(batch_size, 30),
        "peakRssBytes": int(value["peakRssBytes"]),
        "elapsedMs": float(value["elapsedMs"]),
    }


def _run_child(batch_size: int) -> dict[str, int | float]:
    artifact = verify_bge_completion_manifest(
        DEFAULT_MODEL_ROOT,
        manifest_path=DEFAULT_MODEL_MANIFEST,
    )
    tokenizer = BgeStaticTokenizer.from_file(
        DEFAULT_MODEL_ROOT / "onnx/tokenizer.json"
    )
    placeholder = BgeBatchBenchmarkReceipt(
        selected_batch_size=batch_size,
        candidates=_CANDIDATES,
        peak_rss_bytes=tuple((candidate, 1) for candidate in _CANDIDATES),
        elapsed_ms=tuple((candidate, 1.0) for candidate in _CANDIDATES),
        environment_fingerprint_sha256="1" * 64,
        benchmark_sha256="2" * 64,
    )
    plan = prepare_bge_full_generation(
        corpus=load_frozen_source_card_corpus(),
        tokenizer=tokenizer,
        artifact=artifact,
        batch_benchmark=placeholder,
    )
    embedder = load_bge_onnx_embedder(DEFAULT_MODEL_ROOT)
    started = time.perf_counter_ns()
    execute_bge_full_generation(
        plan=plan,
        embedder=embedder,
        repository=_DiscardingRepository(),
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return {
        "batchSize": batch_size,
        "peakRssBytes": peak_rss_bytes,
        "elapsedMs": elapsed_ms,
    }


def batch_receipt_from_report(report: dict[str, Any]) -> BgeBatchBenchmarkReceipt:
    """tracked benchmark JSON을 generation identity용 typed receipt로 변환한다."""

    results = report.get("results")
    candidates = report.get("candidateBatchSizes")
    if (
        report.get("schemaVersion") != "s4-2b-batch-memory-benchmark/v1"
        or report.get("status") != "PASS"
        or not isinstance(results, list)
        or candidates != list(_CANDIDATES)
    ):
        raise BgeFullGenerationError("BATCH_REPORT_CONTRACT")
    benchmark_sha = report.get("benchmarkSha256")
    without_hash = dict(report)
    without_hash.pop("benchmarkSha256", None)
    if (
        not isinstance(benchmark_sha, str)
        or benchmark_sha != _canonical_json_hash(without_hash)
    ):
        raise BgeFullGenerationError("BATCH_REPORT_HASH")
    return BgeBatchBenchmarkReceipt(
        selected_batch_size=int(report["selectedBatchSize"]),
        candidates=_CANDIDATES,
        peak_rss_bytes=tuple(
            (int(item["batchSize"]), int(item["peakRssBytes"]))
            for item in results
        ),
        elapsed_ms=tuple(
            (int(item["batchSize"]), float(item["elapsedMs"]))
            for item in results
        ),
        environment_fingerprint_sha256=str(
            report["environmentFingerprintSha256"]
        ),
        benchmark_sha256=benchmark_sha,
    )


def _environment_payload() -> dict[str, Any]:
    return {
        "cpuCount": os.cpu_count(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "system": platform.system(),
        "kernelRelease": platform.release(),
        "pythonVersion": platform.python_version(),
        "onnxRuntimeVersion": ort.__version__,
        "tokenizersVersion": tokenizers.__version__,
    }


def _total_memory_bytes() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) == 3 and parts[2] == "kB":
                return int(parts[1]) * 1024
    raise BgeFullGenerationError("BATCH_HOST_MEMORY")


def _canonical_json_hash(value: object) -> str:
    serialized = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
