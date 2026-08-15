"""S5.0 amendment의 closed Signal runtime 및 internal artifact 계약을 생성한다."""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)
from contracts.generate_s5_0_signal_v2_contracts import (  # noqa: E402
    REQUIRED_COMPONENTS,
    SIGNALS,
    build_artifacts as build_s5_0_artifacts,
)


ROOT = _REPO_ROOT
RUNTIME_SCHEMA = "contracts/schemas/signal-v2-runtime-v1.schema.json"
ARTIFACT_SCHEMA = "contracts/schemas/lightgbm-signal-artifact-v1.schema.json"
UNKNOWN_FIELDS: Final[tuple[str, ...]] = (
    "crossMarketScore",
    "crossMarketMode",
    "crossMarketFreshness",
    "crossMarketExposure",
    "analyst",
    "news",
    "cause",
    "rag",
    "llm",
    "riskDecision",
    "orderAuthority",
)
ABSTAIN_REASONS: Final[tuple[str, ...]] = (
    "ARTIFACT_DRIFT",
    "CALIBRATION_FAILED",
    "MISSING_EVIDENCE",
    "POSTERIOR_BELOW_THRESHOLD",
    "PRODUCER_FAILED",
    "STALE_EVIDENCE",
    "UNIDENTIFIABLE_OUTPUT",
)
INTERNAL_REASONS: Final[tuple[str, ...]] = (
    "ARTIFACT_DRIFT",
    "STALE_EVIDENCE",
    "CALIBRATION_FAILED",
    "UNIDENTIFIABLE_OUTPUT",
    "MISSING_EVIDENCE",
    "PRODUCER_FAILED",
)


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _bounded_strings(maximum: int) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": maximum,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 256},
    }


def _predictive_component(producer: str, workspace: str) -> dict[str, Any]:
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
            "featureSummary": _bounded_strings(32),
        },
    )
    abstain = _closed(
        ["status", "producer", "sourceWorkspace", "reason"],
        {
            **shared,
            "status": {"const": "ABSTAIN"},
            "reason": {"enum": list(ABSTAIN_REASONS)},
            "warnings": _bounded_strings(10),
        },
    )
    return {"oneOf": [available, abstain]}


def _regime_component() -> dict[str, Any]:
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
            "reason": {"enum": list(ABSTAIN_REASONS)},
            "warnings": _bounded_strings(10),
        },
    )
    return {"oneOf": [available, abstain]}


def _composite() -> dict[str, Any]:
    return {
        "oneOf": [
            _closed(
                ["status", "signal", "confidence"],
                {
                    "status": {"const": "AVAILABLE"},
                    "signal": {"enum": SIGNALS},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "predictedReturn": {"type": ["number", "null"]},
                },
            ),
            _closed(
                ["status", "reason"],
                {
                    "status": {"const": "ABSTAIN"},
                    "reason": {"const": "REQUIRED_COMPONENT_UNAVAILABLE"},
                },
            ),
        ]
    }


def _runtime_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": RUNTIME_SCHEMA,
        "title": "Signal v2 runtime v1",
        "description": "Closed runtime projection that can honestly represent all required components as ABSTAIN.",
        **_closed(
            ["symbol", "timeframe", "composite", "components", "warnings"],
            {
                "symbol": {"type": "string", "pattern": "^[0-9A-Z._:-]{1,20}$"},
                "asOf": _timestamp(),
                "timeframe": {"const": "1d"},
                "modelReportId": {"type": "string", "minLength": 1, "maxLength": 128},
                "composite": _composite(),
                "components": _closed(
                    list(REQUIRED_COMPONENTS),
                    {
                        "ruleBaseline": _predictive_component(
                            "RULE_BASELINE", "return-engine"
                        ),
                        "lstm": _predictive_component("LSTM", "return-engine"),
                        "lightgbm": _predictive_component(
                            "LIGHTGBM", "decision-platform"
                        ),
                        "hmmRegime": _regime_component(),
                    },
                ),
                "warnings": _bounded_strings(10),
            },
        ),
    }


def _sha256() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _artifact_schema() -> dict[str, Any]:
    common = {
        "artifactVersion": {"const": "lightgbm-signal-artifact-v1"},
        "schemaVersion": {"const": "signal-v2-runtime-v1"},
        "producer": {"const": "LIGHTGBM"},
        "sourceWorkspace": {"const": "decision-platform"},
        "symbol": {"type": "string", "pattern": "^[0-9A-Z._:-]{1,20}$"},
        "sessionDate": {"type": "string", "format": "date"},
        "evaluationId": {"type": "string", "minLength": 1, "maxLength": 128},
        "timeframe": {"const": "1d"},
        "modelVersion": {"type": "string", "minLength": 1, "maxLength": 128},
        "modelReportId": {"type": "string", "minLength": 1, "maxLength": 128},
        "fixture": {"type": "boolean"},
        "provenanceClass": {"enum": ["PRODUCTION", "FAKE_CONTRACT"]},
        "datasetSha256": _sha256(),
        "modelSha256": _sha256(),
        "reportSha256": _sha256(),
        "payloadSha256": _sha256(),
        "provenanceSha256": _sha256(),
    }
    required = list(common)
    available = _closed(
        required + ["status", "asOf", "signal", "confidence"],
        {
            **common,
            "status": {"const": "AVAILABLE"},
            "asOf": _timestamp(),
            "signal": {"enum": SIGNALS},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "predictedReturn": {"type": ["number", "null"]},
            "featureSummary": _bounded_strings(32),
        },
    )
    abstain = _closed(
        required + ["status", "reason"],
        {
            **common,
            "status": {"const": "ABSTAIN"},
            "reason": {"enum": list(INTERNAL_REASONS)},
        },
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": ARTIFACT_SCHEMA,
        "title": "LightGBM Signal internal artifact v1",
        "description": "Closed internal evaluation artifact. modelScore is intentionally excluded.",
        "oneOf": [available, abstain],
        "allOf": [
            {
                "if": {
                    "properties": {"fixture": {"const": True}},
                    "required": ["fixture"],
                },
                "then": {"properties": {"provenanceClass": {"const": "FAKE_CONTRACT"}}},
                "else": {"properties": {"provenanceClass": {"const": "PRODUCTION"}}},
            }
        ],
    }


def _all_abstain_fixture() -> dict[str, Any]:
    components = {
        "ruleBaseline": {
            "status": "ABSTAIN",
            "producer": "RULE_BASELINE",
            "sourceWorkspace": "return-engine",
            "reason": "MISSING_EVIDENCE",
        },
        "lstm": {
            "status": "ABSTAIN",
            "producer": "LSTM",
            "sourceWorkspace": "return-engine",
            "reason": "MISSING_EVIDENCE",
        },
        "lightgbm": {
            "status": "ABSTAIN",
            "producer": "LIGHTGBM",
            "sourceWorkspace": "decision-platform",
            "reason": "MISSING_EVIDENCE",
        },
        "hmmRegime": {
            "status": "ABSTAIN",
            "producer": "HMM",
            "sourceWorkspace": "decision-platform",
            "reason": "MISSING_EVIDENCE",
        },
    }
    return {
        "symbol": "005930",
        "timeframe": "1d",
        "composite": {"status": "ABSTAIN", "reason": "REQUIRED_COMPONENT_UNAVAILABLE"},
        "components": components,
        "warnings": ["No verified component evidence is available."],
    }


def _partial_fixture() -> dict[str, Any]:
    fixture = _all_abstain_fixture()
    fixture["asOf"] = "2026-08-14T06:30:00Z"
    fixture["modelReportId"] = "mrp-fixture-runtime-v1"
    fixture["components"]["lightgbm"] = {
        "status": "AVAILABLE",
        "producer": "LIGHTGBM",
        "sourceWorkspace": "decision-platform",
        "asOf": fixture["asOf"],
        "signal": "HOLD",
        "confidence": 0.0,
        "modelVersion": "lgbm-v1-fixture",
        "modelReportId": fixture["modelReportId"],
    }
    return fixture


def _available_hold_fixture() -> dict[str, Any]:
    fixture = copy.deepcopy(
        build_s5_0_artifacts()["contracts/examples/signal-v2.available.valid.json"]
    )
    fixture["timeframe"] = "1d"
    return fixture


def _artifact_fixture(status: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "artifactVersion": "lightgbm-signal-artifact-v1",
        "schemaVersion": "signal-v2-runtime-v1",
        "producer": "LIGHTGBM",
        "sourceWorkspace": "decision-platform",
        "symbol": "005930",
        "sessionDate": "2026-08-14",
        "evaluationId": "eval-fixture-005930-20260814",
        "timeframe": "1d",
        "modelVersion": "lgbm-v1-fixture",
        "modelReportId": "mrp-fixture-runtime-v1",
        "fixture": True,
        "provenanceClass": "FAKE_CONTRACT",
        "datasetSha256": "1" * 64,
        "modelSha256": "2" * 64,
        "reportSha256": "3" * 64,
        "payloadSha256": "4" * 64,
        "provenanceSha256": "5" * 64,
        "status": status,
    }
    if status == "AVAILABLE":
        value.update(
            {"asOf": "2026-08-14T06:30:00Z", "signal": "HOLD", "confidence": 0.0}
        )
    else:
        value["reason"] = "MISSING_EVIDENCE"
    return value


def _slug(field: str) -> str:
    output = []
    for character in field:
        if character.isupper():
            output.extend(("-", character.lower()))
        else:
            output.append(character)
    return "".join(output).lstrip("-")


def _unknown_value(field: str) -> object:
    if field == "riskDecision":
        return "HOLD"
    if field == "orderAuthority":
        return "NONE"
    if field.startswith("crossMarket"):
        return 1
    return {}


def _negative_corpus() -> dict[str, dict[str, Any]]:
    v1 = load_json_bytes_strict(
        (ROOT / "contracts/examples/signal.valid.json").read_bytes(),
        source="contracts/examples/signal.valid.json",
    )
    v2 = build_s5_0_artifacts()["contracts/examples/signal-v2.available.valid.json"]
    if not isinstance(v1, dict):
        raise ContractValidationError("Signal v1 fixture must be an object.")
    artifacts: dict[str, dict[str, Any]] = {}
    for field in UNKNOWN_FIELDS:
        for prefix, base in (("signal", v1), ("signal-v2", v2)):
            mutated = copy.deepcopy(base)
            mutated[field] = _unknown_value(field)
            artifacts[
                f"contracts/examples/invalid/{prefix}.unknown-{_slug(field)}.invalid.json"
            ] = mutated

    base_abstain = copy.deepcopy(
        build_s5_0_artifacts()["contracts/examples/signal-v2.abstain.valid.json"]
    )
    for field, value in (
        ("state", "SIDEWAYS"),
        ("asOf", "2026-07-31T00:00:00Z"),
        ("signal", "HOLD"),
        ("confidence", 0),
        ("predictedReturn", 0),
    ):
        mutated = copy.deepcopy(base_abstain)
        mutated["components"]["hmmRegime"][field] = value
        artifacts[
            f"contracts/examples/invalid/signal-v2.abstain-{_slug(field)}.invalid.json"
        ] = mutated
    smuggled = copy.deepcopy(base_abstain)
    smuggled["composite"] = copy.deepcopy(v2["composite"])
    artifacts[
        "contracts/examples/invalid/signal-v2.required-component-smuggling.invalid.json"
    ] = smuggled
    return artifacts


def _transition_catalog() -> dict[str, Any]:
    return {
        "contractId": "s5-signal-runtime-transition.v1",
        "historicalOpenApiRawSha256": "94414736f6a1c17b95eafffd53a07a5d33d7a66705890c53dcc971eb5ded3f89",
        "historicalPreservedProjectionSha256": "94414736f6a1c17b95eafffd53a07a5d33d7a66705890c53dcc971eb5ded3f89",
        "allowedPath": "/api/v2/signals/{symbol}",
        "allowedMethods": ["get"],
        "allowedSchemaNames": [
            "SignalV2RuntimeComponentResponse",
            "SignalV2RuntimeComponentsResponse",
            "SignalV2RuntimeCompositeResponse",
            "SignalV2RuntimeResponse",
            "SignalV2RuntimeSuccessResponse",
        ],
        "allowedRootTags": ["Signal v2"],
    }


def _policy_catalog() -> dict[str, Any]:
    return {
        "contractId": "s5-lightgbm-implementation-lock.v1",
        "classOrder": {"SELL": 0, "HOLD": 1, "BUY": 2},
        "objective": {"name": "multiclass", "numClass": 3},
        "labelBoundary": {
            "sell": "r < -0.006",
            "hold": "-0.006 <= r <= 0.006",
            "buy": "r > 0.006",
        },
        "probabilityTieOrder": ["HOLD", "SELL", "BUY"],
        "gridOrder": [
            [15, "NONE"],
            [15, "CAPPED_BALANCED"],
            [31, "NONE"],
            [31, "CAPPED_BALANCED"],
        ],
        "candidateSelection": [
            "meanCalibratedLogLoss",
            "meanBrier",
            "meanEce",
            "gridOrder",
        ],
        "classWeight": {
            "formula": "N/(3*N_c)",
            "cap": 5.0,
            "normalizeMean": 1.0,
            "scope": "FIT_ONLY",
        },
        "metrics": {
            "brier": "mean_sum_squared_error_unscaled",
            "logarithm": "natural",
            "ece": "top_label_10_equal_width",
        },
        "missingActualData": {"status": "DATASET_UNAVAILABLE", "productionPointer": 0},
        "runtime": {
            "externalCalls": 0,
            "riskDecisionWiring": 0,
            "orderWiring": 0,
            "firstCrossMarketJoin": "S6.6",
        },
    }


def validate_runtime_semantics(payload: object) -> None:
    """root asOf와 composite availability를 schema 이후에 검증한다."""

    if not isinstance(payload, Mapping):
        raise ContractValidationError("Signal runtime payload must be an object.")
    components = payload.get("components")
    composite = payload.get("composite")
    if not isinstance(components, Mapping) or not isinstance(composite, Mapping):
        raise ContractValidationError(
            "Signal runtime components and composite are required."
        )
    if set(components) != set(REQUIRED_COMPONENTS):
        raise ContractValidationError("Signal runtime required component set drifted.")
    available_times: list[datetime] = []
    for name in REQUIRED_COMPONENTS:
        component = components[name]
        if not isinstance(component, Mapping):
            raise ContractValidationError("Signal runtime component must be an object.")
        if component.get("status") == "AVAILABLE":
            value = component.get("asOf")
            if not isinstance(value, str):
                raise ContractValidationError("AVAILABLE component requires asOf.")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ContractValidationError(
                    "Signal runtime timestamp is invalid."
                ) from error
            if parsed.tzinfo is None:
                raise ContractValidationError(
                    "Signal runtime timestamp must be timezone aware."
                )
            available_times.append(parsed)
    if len(available_times) == len(REQUIRED_COMPONENTS):
        if composite.get("status") != "AVAILABLE":
            raise ContractValidationError(
                "Complete runtime components require AVAILABLE composite."
            )
    elif composite != {"status": "ABSTAIN", "reason": "REQUIRED_COMPONENT_UNAVAILABLE"}:
        raise ContractValidationError(
            "Unavailable runtime component must force composite ABSTAIN."
        )
    root_as_of = payload.get("asOf")
    if available_times:
        if not isinstance(root_as_of, str):
            raise ContractValidationError(
                "Runtime payload with AVAILABLE evidence requires root asOf."
            )
        parsed_root = datetime.fromisoformat(root_as_of.replace("Z", "+00:00"))
        if parsed_root != max(available_times):
            raise ContractValidationError(
                "Runtime root asOf must equal latest AVAILABLE component."
            )
    elif "asOf" in payload or "modelReportId" in payload:
        raise ContractValidationError(
            "All-ABSTAIN runtime payload must omit asOf and modelReportId."
        )


def build_artifacts() -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {
        "contracts/catalogs/s5-lightgbm-implementation-lock.v1.json": _policy_catalog(),
        "contracts/catalogs/s5-signal-runtime-transition.v1.json": _transition_catalog(),
        RUNTIME_SCHEMA: _runtime_schema(),
        ARTIFACT_SCHEMA: _artifact_schema(),
        "contracts/examples/signal-v2-runtime-v1.all-abstain.valid.json": _all_abstain_fixture(),
        "contracts/examples/signal-v2-runtime-v1.partial-abstain.valid.json": _partial_fixture(),
        "contracts/examples/signal-v2-runtime-v1.available-hold.valid.json": _available_hold_fixture(),
        "contracts/examples/lightgbm-signal-artifact-v1.available.valid.json": _artifact_fixture(
            "AVAILABLE"
        ),
        "contracts/examples/lightgbm-signal-artifact-v1.abstain.valid.json": _artifact_fixture(
            "ABSTAIN"
        ),
        **_negative_corpus(),
    }
    Draft202012Validator.check_schema(artifacts[RUNTIME_SCHEMA])
    Draft202012Validator.check_schema(artifacts[ARTIFACT_SCHEMA])
    return artifacts


ARTIFACT_PATHS: Final[frozenset[str]] = frozenset(build_artifacts())


def generate_outputs() -> dict[str, bytes]:
    return {
        path: canonical_json_bytes(value) for path, value in build_artifacts().items()
    }


def _write(outputs: Mapping[str, bytes]) -> None:
    for relative, payload in sorted(outputs.items()):
        write_generated_artifact(ROOT, relative, payload)


def _check(outputs: Mapping[str, bytes]) -> None:
    drifted = [
        relative
        for relative, expected in sorted(outputs.items())
        if not (ROOT / relative).is_file()
        or (ROOT / relative).is_symlink()
        or (ROOT / relative).read_bytes() != expected
    ]
    if drifted:
        raise ContractValidationError(
            "generated S5 Signal runtime artifacts drifted:\n"
            + "\n".join(f"- {path}" for path in drifted)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        outputs = generate_outputs()
        _write(outputs) if args.write else _check(outputs)
    except (ContractValidationError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("S5_SIGNAL_RUNTIME_TRANSITION_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
