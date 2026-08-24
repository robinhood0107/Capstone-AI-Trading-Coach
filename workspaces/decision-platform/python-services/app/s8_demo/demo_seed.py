from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from app.s8_demo.synthetic_bundle import _write_idempotent as _write_synthetic_idempotent
from app.s8_demo.synthetic_bundle import build_synthetic_bundle

_SEED: dict[str, Any] = {
    "schemaVersion": "1.0.0",
    "demoProject": "capstone-s8-demo",
    "namespace": "demo_s8_offline_0001",
    "fixtureClass": "SYNTHETIC_FAKE_E2E",
    "brokerageMode": "INTERNAL_PAPER",
    "providerCalls": 0,
    "liveAccountCalls": 0,
    "liveOrderCalls": 0,
    "ragAnswerCache": "OFF",
    "crossMarketCapability": "RETIRED_NOT_APPLICABLE",
    "scenarios": [
        {
            "scenarioId": "demo_allow",
            "expectedOutcome": "ALLOW",
            "authority": "DETERMINISTIC_RISK_ENGINE",
        },
        {
            "scenarioId": "demo_warn",
            "expectedOutcome": "WARN",
            "authority": "DETERMINISTIC_RISK_ENGINE",
        },
        {
            "scenarioId": "demo_block",
            "expectedOutcome": "BLOCK",
            "authority": "KILL_SWITCH",
        },
        {
            "scenarioId": "demo_hold",
            "expectedOutcome": "HOLD",
            "authority": "MISSING_EVIDENCE",
        },
    ],
    "ragQuestions": [
        {
            "questionId": "demo_rag_q1",
            "classification": "INTERNAL_PAPER",
            "text": "손실 한도 원칙은 무엇인가요?",
        },
        {
            "questionId": "demo_rag_q2",
            "classification": "INTERNAL_PAPER",
            "text": "경고와 차단은 어떻게 다른가요?",
        },
        {
            "questionId": "demo_rag_q3",
            "classification": "INTERNAL_PAPER",
            "text": "근거가 없으면 왜 보류하나요?",
        },
    ],
    "killSwitchScenario": {
        "scenarioId": "demo_kill_switch",
        "expectedState": "ACTIVE",
        "orderAuthority": "BLOCKED",
    },
    "asyncAdapters": ["db", "kafka"],
    "performanceClaimAllowed": False,
}


def build_demo_seed(*, brokerage_mode: str) -> dict[str, Any]:
    if brokerage_mode != "INTERNAL_PAPER":
        raise ValueError("demo_mode_must_be_explicit_internal_paper")
    return copy.deepcopy(_SEED)


def materialize_demo(*, config_path: Path, output_dir: Path, brokerage_mode: str) -> str:
    seed = build_demo_seed(brokerage_mode=brokerage_mode)
    bundle = build_synthetic_bundle(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink():
        raise ValueError("demo_output_symlink_rejected")
    bundle.write(output_dir)
    seed_text = _canonical_json(seed) + "\n"
    _write_idempotent(output_dir / "demo-seed.json", seed_text)
    receipt = {
        "schemaVersion": "1.0.0",
        "namespace": seed["namespace"],
        "seedHash": _sha256(seed_text.rstrip("\n")),
        "artifactContentHash": bundle.content_hash,
        "modelProjectionHash": bundle.model_projection_hash,
        "backtestProjectionHash": bundle.backtest_projection_hash,
        "providerCalls": 0,
        "liveAccountCalls": 0,
        "liveOrderCalls": 0,
        "performanceClaimAllowed": False,
        "status": "S8_3_OFFLINE_DEMO_SEED_READY",
    }
    receipt_text = _canonical_json(receipt) + "\n"
    _write_idempotent(output_dir / "demo-receipt.json", receipt_text)
    return _sha256(receipt_text.rstrip("\n"))


def _write_idempotent(path: Path, content: str) -> None:
    try:
        _write_synthetic_idempotent(path, content)
    except ValueError as error:
        if str(error) == "synthetic_output_conflict":
            raise ValueError("demo_seed_conflict") from error
        raise


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize the isolated offline S8 demo seed.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--brokerage-mode", required=True)
    args = parser.parse_args()
    try:
        receipt_hash = materialize_demo(
            config_path=args.config,
            output_dir=args.output,
            brokerage_mode=args.brokerage_mode,
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"S8_3_OFFLINE_DEMO_SEED_FAILED: {error}")
        return 1
    print(f"S8_3_OFFLINE_DEMO_SEED_READY receiptHash={receipt_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
