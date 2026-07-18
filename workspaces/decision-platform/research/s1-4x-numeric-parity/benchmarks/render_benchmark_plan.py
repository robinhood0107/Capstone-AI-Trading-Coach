#!/usr/bin/env python3
"""현재 frozen SSOT bytes에서 benchmark plan과 sidecar를 결정론적으로 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from benchmark_contract import ContractError, build_plan, sha256_file

BENCHMARKS_DIR = Path(__file__).resolve().parent
CONTRACT_DIR = BENCHMARKS_DIR.parent / "contract"
DEFAULT_OUTPUT = BENCHMARKS_DIR / "benchmark-plan.v1.json"


def rendered_plan_bytes() -> bytes:
    """최종 reference/input/result/policy bytes의 digest를 계획에 직접 고정한다."""

    plan = build_plan(
        reference_lock_sha256=sha256_file(CONTRACT_DIR / "reference-lock.v1.json"),
        canonical_inputs_sha256=sha256_file(
            CONTRACT_DIR / "fixtures" / "small" / "canonical-inputs.v1.json"
        ),
        canonical_results_sha256=sha256_file(
            CONTRACT_DIR / "fixtures" / "expected" / "canonical-results.v1.json"
        ),
        scala_source_policy_sha256=sha256_file(
            CONTRACT_DIR / "scala-source-policy.v1.json"
        ),
    )
    return (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()


def sidecar_path(output: Path) -> Path:
    return output.with_suffix(".sha256")


def _sidecar_bytes(plan_bytes: bytes, output: Path) -> bytes:
    digest = hashlib.sha256(plan_bytes).hexdigest()
    return f"{digest}  {output.name}\n".encode()


def write_plan(output: Path) -> None:
    """두 산출물을 임시 파일에서 원자 교체해 서로 다른 세대가 섞이지 않게 한다."""

    plan_bytes = rendered_plan_bytes()
    sidecar = sidecar_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    plan_temporary = output.with_suffix(".json.tmp")
    sidecar_temporary = sidecar.with_suffix(".sha256.tmp")
    plan_temporary.write_bytes(plan_bytes)
    sidecar_temporary.write_bytes(_sidecar_bytes(plan_bytes, output))
    plan_temporary.replace(output)
    sidecar_temporary.replace(sidecar)


def check_plan(output: Path) -> None:
    """tracked plan과 sidecar가 현재 SSOT 최종 bytes에서 재현되는지 확인한다."""

    expected_plan = rendered_plan_bytes()
    expected_sidecar = _sidecar_bytes(expected_plan, output)
    if not output.is_file() or output.read_bytes() != expected_plan:
        raise ContractError("BENCHMARK_PLAN_NOT_REPRODUCIBLE")
    sidecar = sidecar_path(output)
    if not sidecar.is_file() or sidecar.read_bytes() != expected_sidecar:
        raise ContractError("BENCHMARK_PLAN_SIDECAR_NOT_REPRODUCIBLE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        if args.write:
            write_plan(args.output)
        else:
            check_plan(args.output)
    except (ContractError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
