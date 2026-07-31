from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)


ROOT = _SCRIPT_REPO_ROOT
SIGNALS: Final[list[str]] = ["BUY", "HOLD", "SELL"]
ABSTAIN_REASONS: Final[list[str]] = [
    "ARTIFACT_DRIFT",
    "CALIBRATION_FAILED",
    "MISSING_EVIDENCE",
    "POSTERIOR_BELOW_THRESHOLD",
    "PRODUCER_FAILED",
    "STALE_EVIDENCE",
    "UNIDENTIFIABLE_OUTPUT",
]
REQUIRED_COMPONENTS: Final[tuple[str, ...]] = (
    "ruleBaseline",
    "lstm",
    "lightgbm",
    "hmmRegime",
)
FROZEN_EXISTING_HASHES: Final[dict[str, str]] = {
    "contracts/schemas/signal.schema.json": (
        "ae6b2285d1df7ce608778cd59c332332e8c44ce38a861d23d57dc8f0f9b912c2"
    ),
    "contracts/examples/signal.valid.json": (
        "691cbb7b7f58ae3dbf411e3ff41d173cfdaf927ab3d6af973812dcdea3d9ae35"
    ),
    "contracts/openapi/openapi.json": (
        "94414736f6a1c17b95eafffd53a07a5d33d7a66705890c53dcc971eb5ded3f89"
    ),
}


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _bounded_strings(*, maximum: int = 10) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": maximum,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 256},
    }


def _predictive_component(*, producer: str, workspace: str) -> dict[str, Any]:
    shared = {
        "producer": {"const": producer},
        "sourceWorkspace": {"const": workspace},
        "modelVersion": {"type": "string", "minLength": 1, "maxLength": 128},
        "modelReportId": {"type": "string", "minLength": 1, "maxLength": 128},
    }
    available = _closed(
        ["status", "producer", "sourceWorkspace", "asOf", "signal", "confidence"],
        {
            **shared,
            "status": {"const": "AVAILABLE"},
            "asOf": _timestamp(),
            "signal": {"enum": SIGNALS},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "predictedReturn": {"type": ["number", "null"]},
            "featureSummary": _bounded_strings(maximum=32),
        },
    )
    abstain = _closed(
        ["status", "producer", "sourceWorkspace", "reason"],
        {
            **shared,
            "status": {"const": "ABSTAIN"},
            "reason": {"enum": ABSTAIN_REASONS},
            "warnings": _bounded_strings(),
        },
    )
    return {"oneOf": [available, abstain]}


def _hmm_component() -> dict[str, Any]:
    shared = {
        "producer": {"const": "HMM"},
        "sourceWorkspace": {"const": "decision-platform"},
        "modelVersion": {"type": "string", "minLength": 1, "maxLength": 128},
        "modelReportId": {"type": "string", "minLength": 1, "maxLength": 128},
    }
    available = _closed(
        ["status", "producer", "sourceWorkspace", "asOf", "state", "confidence"],
        {
            **shared,
            "status": {"const": "AVAILABLE"},
            "asOf": _timestamp(),
            "state": {
                "enum": ["NORMAL", "SIDEWAYS", "HIGH_VOLATILITY", "RISK_OFF", "RISK_ON"]
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    )
    abstain = _closed(
        ["status", "producer", "sourceWorkspace", "reason"],
        {
            **shared,
            "status": {"const": "ABSTAIN"},
            "reason": {"enum": ABSTAIN_REASONS},
            "warnings": _bounded_strings(),
        },
    )
    return {"oneOf": [available, abstain]}


def _composite() -> dict[str, Any]:
    available = _closed(
        ["status", "signal", "confidence"],
        {
            "status": {"const": "AVAILABLE"},
            "signal": {"enum": SIGNALS},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "predictedReturn": {"type": ["number", "null"]},
        },
    )
    abstain = _closed(
        ["status", "reason"],
        {
            "status": {"const": "ABSTAIN"},
            "reason": {"const": "REQUIRED_COMPONENT_UNAVAILABLE"},
        },
    )
    return {"oneOf": [available, abstain]}


def _schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/signal-v2.schema.json",
        "title": "Signal v2",
        "description": (
            "Closed Signal v2 component union. It has no RiskDecision, order, cross-market, "
            "analyst, news, cause, RAG, or LLM authority."
        ),
        **_closed(
            ["symbol", "asOf", "timeframe", "composite", "components", "warnings"],
            {
                "symbol": {"type": "string", "pattern": "^[0-9A-Z._:-]{1,20}$"},
                "asOf": _timestamp(),
                "timeframe": {"enum": ["1d", "60m"]},
                "modelReportId": {"type": "string", "minLength": 1, "maxLength": 128},
                "composite": _composite(),
                "components": _closed(
                    list(REQUIRED_COMPONENTS),
                    {
                        "ruleBaseline": _predictive_component(
                            producer="RULE_BASELINE",
                            workspace="return-engine",
                        ),
                        "lstm": _predictive_component(
                            producer="LSTM",
                            workspace="return-engine",
                        ),
                        "lightgbm": _predictive_component(
                            producer="LIGHTGBM",
                            workspace="decision-platform",
                        ),
                        "hmmRegime": _hmm_component(),
                    },
                ),
                "warnings": _bounded_strings(),
            },
        ),
    }


def _available_fixture() -> dict[str, Any]:
    timestamp = "2026-07-31T00:00:00Z"
    return {
        "symbol": "005930",
        "asOf": timestamp,
        "timeframe": "1d",
        "modelReportId": "model_report_fixture_20260731",
        "composite": {"status": "AVAILABLE", "signal": "HOLD", "confidence": 0.62},
        "components": {
            "ruleBaseline": {
                "status": "AVAILABLE",
                "producer": "RULE_BASELINE",
                "sourceWorkspace": "return-engine",
                "asOf": timestamp,
                "signal": "HOLD",
                "confidence": 0.51,
                "predictedReturn": 0.001,
            },
            "lstm": {
                "status": "AVAILABLE",
                "producer": "LSTM",
                "sourceWorkspace": "return-engine",
                "asOf": timestamp,
                "signal": "BUY",
                "confidence": 0.57,
                "predictedReturn": 0.008,
            },
            "lightgbm": {
                "status": "AVAILABLE",
                "producer": "LIGHTGBM",
                "sourceWorkspace": "decision-platform",
                "asOf": timestamp,
                "signal": "HOLD",
                "confidence": 0.66,
                "predictedReturn": 0.003,
            },
            "hmmRegime": {
                "status": "AVAILABLE",
                "producer": "HMM",
                "sourceWorkspace": "decision-platform",
                "asOf": timestamp,
                "state": "SIDEWAYS",
                "confidence": 0.72,
            },
        },
        "warnings": [],
    }


def _abstain_fixture() -> dict[str, Any]:
    fixture = copy.deepcopy(_available_fixture())
    fixture["composite"] = {
        "status": "ABSTAIN",
        "reason": "REQUIRED_COMPONENT_UNAVAILABLE",
    }
    fixture["components"]["hmmRegime"] = {
        "status": "ABSTAIN",
        "producer": "HMM",
        "sourceWorkspace": "decision-platform",
        "reason": "POSTERIOR_BELOW_THRESHOLD",
        "warnings": ["HMM evidence was excluded from the composite."],
    }
    fixture["warnings"] = ["One or more required components are unavailable."]
    return fixture


def _invalid_fixtures() -> dict[str, dict[str, Any]]:
    fabrication = _abstain_fixture()
    fabrication["components"]["hmmRegime"].update(
        {
            "asOf": fabrication["asOf"],
            "confidence": 0,
            "signal": "HOLD",
            "state": "SIDEWAYS",
        }
    )
    smuggling = _abstain_fixture()
    smuggling["composite"] = copy.deepcopy(_available_fixture()["composite"])
    cross_market = _available_fixture()
    cross_market.update(
        {
            "crossMarketExposure": "SEMICONDUCTOR",
            "crossMarketFreshness": "FRESH",
            "crossMarketMode": "WARN_ONLY",
            "crossMarketScore": 99,
        }
    )
    adjacent = _available_fixture()
    adjacent.update(
        {
            "analyst": {},
            "cause": {},
            "llm": {},
            "news": {},
            "orderAuthority": "NONE",
            "rag": {},
            "riskDecision": "HOLD",
        }
    )
    return {
        "contracts/examples/invalid/signal-v2.abstain-fabrication.invalid.json": fabrication,
        "contracts/examples/invalid/signal-v2.composite-smuggling.invalid.json": smuggling,
        "contracts/examples/invalid/signal-v2.unknown-authority.invalid.json": adjacent,
        "contracts/examples/invalid/signal-v2.unknown-cross-market.invalid.json": cross_market,
    }


def _catalog() -> dict[str, Any]:
    return {
        "contractId": "s5-0-signal-v2-contract.v1",
        "status": "CONTRACT_LOCKED_RUNTIME_NOT_PUBLISHED",
        "schemaId": "contracts/schemas/signal-v2.schema.json",
        "requiredComponents": list(REQUIRED_COMPONENTS),
        "componentUnion": {
            "AVAILABLE": {
                "predictionFieldsAllowed": ["signal", "confidence", "predictedReturn"],
                "neutralSignal": "HOLD",
            },
            "ABSTAIN": {
                "required": ["producer", "reason", "sourceWorkspace", "status"],
                "forbidden": ["asOf", "confidence", "predictedReturn", "signal", "state"],
            },
        },
        "datasetModelIsolation": {
            "crossMarketReaderCalls": 0,
            "firstJoinSession": "S6.6",
            "forbiddenFeaturePrefixes": ["analyst_", "cause_", "cross_market_", "llm_", "news_", "rag_"],
            "invariantHashes": ["datasetHash", "modelHash", "signalV2Hash"],
            "sampleWeightInjection": 0,
        },
        "forbiddenUnknownFields": [
            "analyst",
            "cause",
            "crossMarketExposure",
            "crossMarketFreshness",
            "crossMarketMode",
            "crossMarketScore",
            "llm",
            "news",
            "orderAuthority",
            "rag",
            "riskDecision",
        ],
        "runtimePublication": {
            "activeEndpoint": "NO_GO",
            "externalCalls": 0,
            "orderWiring": "NO_GO",
            "riskDecisionWiring": "NO_GO",
        },
        "compatibility": {
            "currentOpenApiSha256": FROZEN_EXISTING_HASHES["contracts/openapi/openapi.json"],
            "signalV1ExampleSha256": FROZEN_EXISTING_HASHES["contracts/examples/signal.valid.json"],
            "signalV1SchemaSha256": FROZEN_EXISTING_HASHES["contracts/schemas/signal.schema.json"],
            "signalV1Unchanged": True,
        },
    }


def build_artifacts() -> dict[str, dict[str, Any]]:
    artifacts = {
        "contracts/catalogs/s5-0-signal-v2-contract.v1.json": _catalog(),
        "contracts/examples/signal-v2.abstain.valid.json": _abstain_fixture(),
        "contracts/examples/signal-v2.available.valid.json": _available_fixture(),
        "contracts/schemas/signal-v2.schema.json": _schema(),
        **_invalid_fixtures(),
    }
    Draft202012Validator.check_schema(artifacts["contracts/schemas/signal-v2.schema.json"])
    return artifacts


ARTIFACT_PATHS: Final[frozenset[str]] = frozenset(build_artifacts())


def validate_signal_v2_semantics(payload: object) -> None:
    """schema를 통과한 payload의 required-component와 top-level asOf 불변식을 검증한다."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("Signal v2 payload must be an object.")
    components = payload.get("components")
    composite = payload.get("composite")
    if not isinstance(components, Mapping) or not isinstance(composite, Mapping):
        raise ContractValidationError("Signal v2 components and composite are required.")
    if tuple(components) != REQUIRED_COMPONENTS and set(components) != set(REQUIRED_COMPONENTS):
        raise ContractValidationError("Signal v2 required component set drifted.")
    unavailable = [name for name in REQUIRED_COMPONENTS if components[name].get("status") == "ABSTAIN"]
    if unavailable:
        if (
            composite.get("status") != "ABSTAIN"
            or composite.get("reason") != "REQUIRED_COMPONENT_UNAVAILABLE"
        ):
            raise ContractValidationError("Signal v2 required component must force composite ABSTAIN.")
    elif composite.get("status") != "AVAILABLE":
        raise ContractValidationError("Signal v2 complete components require AVAILABLE composite.")

    available_times: list[datetime] = []
    for name in REQUIRED_COMPONENTS:
        component = components[name]
        if component.get("status") == "AVAILABLE":
            as_of = component.get("asOf")
            if not isinstance(as_of, str):
                raise ContractValidationError("Signal v2 AVAILABLE component requires asOf.")
            available_times.append(_parse_timestamp(as_of))
    top_as_of = payload.get("asOf")
    if not isinstance(top_as_of, str) or not available_times or _parse_timestamp(top_as_of) != max(available_times):
        raise ContractValidationError("Signal v2 top-level asOf must equal latest AVAILABLE component.")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractValidationError("Signal v2 timestamp is invalid.") from error
    if parsed.tzinfo is None:
        raise ContractValidationError("Signal v2 timestamp must be timezone aware.")
    return parsed


def generate_outputs() -> dict[str, bytes]:
    _verify_frozen_existing_files()
    return {path: canonical_json_bytes(value) for path, value in build_artifacts().items()}


def _verify_frozen_existing_files() -> None:
    for relative, expected in FROZEN_EXISTING_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise ContractValidationError(f"frozen Signal/OpenAPI input is unavailable: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ContractValidationError(f"frozen Signal/OpenAPI input drifted: {relative}")


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ContractValidationError(f"refusing symlink output: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o644)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_outputs(outputs: Mapping[str, bytes]) -> None:
    for relative, payload in sorted(outputs.items()):
        _write_atomic(ROOT / relative, payload)


def _check_outputs(outputs: Mapping[str, bytes]) -> None:
    drifted = [
        relative
        for relative, expected in sorted(outputs.items())
        if not (ROOT / relative).is_file()
        or (ROOT / relative).is_symlink()
        or (ROOT / relative).read_bytes() != expected
    ]
    if drifted:
        raise ContractValidationError(
            "generated S5.0 Signal v2 artifacts drifted:\n"
            + "\n".join(f"- {relative}" for relative in drifted)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        outputs = generate_outputs()
        if arguments.write:
            _write_outputs(outputs)
        else:
            _check_outputs(outputs)
    except (ContractValidationError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("S5_0_SIGNAL_V2_CONTRACT_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
