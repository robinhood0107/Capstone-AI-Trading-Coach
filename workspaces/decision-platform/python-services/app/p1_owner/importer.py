"""Validate, archive, and project a P1 Return Engine v2 artifact bundle.

The importer reads one owner-approved local directory.  It has no provider,
account, or order transport.  Files are validated before an immutable archive
is published and before one bounded SECURITY DEFINER database transaction is
attempted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import psycopg

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.temporal import next_session_evidence_clock
from app.p1_owner.assets import (
    ARTIFACT_NAMES,
    ARTIFACT_SCHEMA_IDS,
    FEATURE_ORDER,
    GOLDEN_MANIFEST,
    P1OwnerAssetError,
    _publish_directory,
    _validate_repository_schema,
)
from app.rag.safe_io import RagSafeIoError, list_approved_regular_files

_EXPECTED_INVENTORY = frozenset((*ARTIFACT_NAMES, GOLDEN_MANIFEST))
_MAX_FILE_BYTES = 128 * 1024 * 1024
_MAX_ENTRIES = 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[0-9]{6}$")
_SCENARIOS = ("BASELINE", "GUIDE", "STRICT")
_SIGNAL_COLUMNS = (
    "confidence",
    "currentClose",
    "expectedReturn",
    "forecastClose",
    "sessionDate",
    "signal",
    "symbol",
)
_TRADE_COLUMNS = (
    "scenario",
    "symbol",
    "entrySession",
    "exitSession",
    "side",
    "quantity",
    "entryPrice",
    "exitPrice",
    "grossReturn",
    "costBps",
    "netReturn",
)
_EQUITY_COLUMNS = ("scenario", "sessionDate", "equityKrw", "drawdown")
_TENSOR_SHAPES: dict[str, tuple[int, ...]] = {
    "weight_ih_l0": (512, 9),
    "weight_hh_l0": (512, 128),
    "bias_ih_l0": (512,),
    "bias_hh_l0": (512,),
    "weight_ih_l1": (512, 128),
    "weight_hh_l1": (512, 128),
    "bias_ih_l1": (512,),
    "bias_hh_l1": (512,),
    "weight_ih_l2": (512, 128),
    "weight_hh_l2": (512, 128),
    "bias_ih_l2": (512,),
    "bias_hh_l2": (512,),
    "head.weight": (1, 128),
    "head.bias": (1,),
}


class P1ArtifactImportError(ValueError):
    """The bundle cannot cross the Owner artifact import boundary."""


@dataclass(frozen=True, slots=True)
class ValidatedArtifactBundle:
    root: Path
    manifest_sha256: str
    bundle_sha256: str
    artifact_id: str
    run_id: str
    input_pack_sha256: str
    evidence_mode: str
    real_team_b: bool
    model_quality: str
    mock_runtime_eligible: bool
    session_date: date
    as_of: datetime
    fresh_until: datetime
    payloads: dict[str, bytes]
    import_packet: dict[str, Any]
    import_packet_sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactImportResult:
    artifact_id: str
    bundle_sha256: str
    run_id: str
    database_outcome: str
    archive_no_op: bool


def validate_artifact_bundle(
    *, bundle_root: Path, expected_manifest_sha256: str
) -> ValidatedArtifactBundle:
    """Validate exact inventory, wire schemas, independent arithmetic, and projections."""

    expected_sha = _sha(expected_manifest_sha256, "manifest")
    try:
        payloads = list_approved_regular_files(
            approved_root=bundle_root.parent,
            relative_directory=bundle_root.name,
            max_entries=_MAX_ENTRIES,
            max_bytes=_MAX_FILE_BYTES,
        )
    except RagSafeIoError as error:
        raise P1ArtifactImportError("bundle root or file metadata is unsafe") from error
    if frozenset(payloads) != _EXPECTED_INVENTORY:
        raise P1ArtifactImportError("bundle must contain the manifest and exact ten artifacts")
    manifest_bytes = payloads[GOLDEN_MANIFEST]
    manifest_sha = _digest(manifest_bytes)
    if manifest_sha != expected_sha:
        raise P1ArtifactImportError("manifest SHA-256 does not match the approved argument")
    manifest = _object(manifest_bytes, "artifact manifest")
    _schema("contracts/schemas/p1-return-engine-artifact-manifest.v2.schema.json", manifest)
    _validate_manifest_truth(manifest)
    artifacts = cast(list[dict[str, Any]], manifest["artifacts"])
    if [item["path"] for item in artifacts] != list(ARTIFACT_NAMES):
        raise P1ArtifactImportError("artifact inventory order drifted")
    for item, schema_id in zip(artifacts, ARTIFACT_SCHEMA_IDS, strict=True):
        name = cast(str, item["path"])
        if item["semanticSchema"] != f"contracts/schemas/{schema_id}.schema.json":
            raise P1ArtifactImportError(f"semantic schema binding drifted: {name}")
        content = payloads[name]
        if item["sizeBytes"] != len(content) or item["sha256"] != _digest(content):
            raise P1ArtifactImportError(f"artifact byte binding mismatch: {name}")

    config = _validate_config(payloads["config.json"])
    scaler_symbols = _validate_scaler(payloads["scaler.json"])
    lstm = _validate_signals(payloads["lstm_signals.parquet"], "LSTM")
    baseline = _validate_signals(payloads["rule_baseline_signals.parquet"], "RULE_BASELINE")
    if set(cast(tuple[str, ...], lstm["symbols"])) != set(scaler_symbols) or set(
        cast(tuple[str, ...], baseline["symbols"])
    ) != set(scaler_symbols):
        raise P1ArtifactImportError("config, scaler, and signal symbol sets differ")
    if lstm["sessionDate"] != baseline["sessionDate"]:
        raise P1ArtifactImportError("LSTM and rule baseline session dates differ")
    session_date = date.fromisoformat(cast(str, lstm["sessionDate"]))
    _validate_xkrx_session(session_date)
    _validate_safetensors(payloads["model.safetensors"], scaler_symbols)
    _validate_golden_output(
        payloads["golden_output.json"],
        manifest=manifest,
        lstm_rows=cast(list[dict[str, Any]], lstm["rows"]),
    )
    scenarios = _validate_backtest(
        backtest_bytes=payloads["backtest_result.json"],
        trade_bytes=payloads["trade_log.parquet"],
        equity_bytes=payloads["equity_log.parquet"],
    )
    _validate_report(payloads["model_report.md"])
    producer = cast(dict[str, Any], manifest["producer"])
    if producer["configSha256"] != _digest(payloads["config.json"]):
        raise P1ArtifactImportError("producer config binding mismatch")
    if producer["goldenOutputSha256"] != _digest(payloads["golden_output.json"]):
        raise P1ArtifactImportError("producer golden output binding mismatch")
    feature_sha = _digest(canonical_json_bytes(list(FEATURE_ORDER)))
    if producer["featureOrderSha256"] != feature_sha:
        raise P1ArtifactImportError("producer feature-order binding mismatch")
    if config["featureOrder"] != list(FEATURE_ORDER):
        raise P1ArtifactImportError("fixed feature order drifted")

    as_of = next_session_evidence_clock(session_date)
    fresh_until = next_session_evidence_clock(session_date, extra_sessions=1)
    bundle_sha = manifest_sha
    artifact_id = f"artifact_p1_{bundle_sha[:24]}"
    packet = _build_import_packet(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        artifact_id=artifact_id,
        session_date=session_date,
        as_of=as_of,
        fresh_until=fresh_until,
        lstm_rows=cast(list[dict[str, Any]], lstm["rows"]),
        baseline_rows=cast(list[dict[str, Any]], baseline["rows"]),
        scenarios=scenarios,
        equity_bytes=payloads["equity_log.parquet"],
    )
    packet_bytes = canonical_json_bytes(packet)
    return ValidatedArtifactBundle(
        root=bundle_root,
        manifest_sha256=manifest_sha,
        bundle_sha256=bundle_sha,
        artifact_id=artifact_id,
        run_id=cast(str, manifest["runId"]),
        input_pack_sha256=cast(str, manifest["inputPackSha256"]),
        evidence_mode=cast(str, manifest["evidenceMode"]),
        real_team_b=cast(bool, manifest["realTeamB"]),
        model_quality=cast(str, manifest["modelQuality"]),
        mock_runtime_eligible=cast(bool, manifest["mockRuntimeEligible"]),
        session_date=session_date,
        as_of=as_of,
        fresh_until=fresh_until,
        payloads=payloads,
        import_packet=packet,
        import_packet_sha256=_digest(packet_bytes),
    )


def import_artifact_bundle(
    *,
    bundle_root: Path,
    expected_manifest_sha256: str,
    archive_parent: Path,
    database_dsn: str,
) -> ArtifactImportResult:
    """Archive validated bytes, then commit every DB projection in one function call."""

    validated = validate_artifact_bundle(
        bundle_root=bundle_root,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    archive_root = archive_parent / validated.bundle_sha256
    archive_payloads = {
        name: body for name, body in validated.payloads.items() if name != GOLDEN_MANIFEST
    }
    archive_no_op = _publish_directory(
        output_root=archive_root,
        payloads=archive_payloads,
        manifest_name=GOLDEN_MANIFEST,
        manifest_bytes=validated.payloads[GOLDEN_MANIFEST],
    )
    packet_text = canonical_json_bytes(validated.import_packet).decode("utf-8")
    try:
        with psycopg.connect(database_dsn, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT outcome,artifact_id,run_id FROM import_p1_return_bundle_v1(%s,%s)",
                    (packet_text, validated.import_packet_sha256),
                )
                row = cursor.fetchone()
                if row is None:
                    raise P1ArtifactImportError("database import returned no receipt")
                outcome, artifact_id, run_id = cast(tuple[str, str, str], row)
                if artifact_id != validated.artifact_id or run_id != validated.run_id:
                    raise P1ArtifactImportError("database import receipt identity mismatch")
            connection.commit()
    except (psycopg.Error, OSError) as error:
        raise P1ArtifactImportError("database import transaction failed") from error
    return ArtifactImportResult(
        artifact_id=validated.artifact_id,
        bundle_sha256=validated.bundle_sha256,
        run_id=validated.run_id,
        database_outcome=outcome,
        archive_no_op=archive_no_op,
    )


def _validate_manifest_truth(manifest: dict[str, Any]) -> None:
    evidence = manifest.get("evidenceMode")
    real = manifest.get("realTeamB")
    quality = manifest.get("modelQuality")
    eligible = manifest.get("mockRuntimeEligible")
    if evidence == "SYNTHETIC_GOLDEN":
        if real is not False or quality != "NOT_EVALUATED_SYNTHETIC" or eligible is not True:
            raise P1ArtifactImportError("synthetic manifest truth markers conflict")
    elif evidence == "REAL_TEAM_B":
        if real is not True or quality not in {"PASS", "BELOW_BASELINE"}:
            raise P1ArtifactImportError("real Team B manifest truth markers conflict")
        if quality != "PASS" and eligible is not False:
            raise P1ArtifactImportError("below-baseline bundle cannot be mock-runtime eligible")
    else:
        raise P1ArtifactImportError("unsupported artifact evidence mode")
    if (
        manifest.get("performanceClaimAllowed") is not False
        or manifest.get("orderAuthority") != "NONE"
    ):
        raise P1ArtifactImportError("artifact attempted performance or order authority")


def _validate_config(content: bytes) -> dict[str, Any]:
    value = _object(content, "config")
    expected = {
        "contractId",
        "deterministicAlgorithms",
        "dropout",
        "featureOrder",
        "hiddenSize",
        "layerCount",
        "learningRate",
        "loss",
        "optimizer",
        "outputSize",
        "perSymbolIndependent",
        "seed",
        "threadCount",
        "windowSize",
    }
    if set(value) != expected or value.get("contractId") != "p1-return-config.v2":
        raise P1ArtifactImportError("config closed field set drifted")
    semantic = {
        key: value[key]
        for key in expected
        if key not in {"contractId", "featureOrder", "perSymbolIndependent"}
    }
    _schema(
        "contracts/schemas/p1-return-config.v2.schema.json",
        _semantic_document("p1-return-config.v2", "config.json", semantic),
    )
    if value["perSymbolIndependent"] is not True or value["featureOrder"] != list(FEATURE_ORDER):
        raise P1ArtifactImportError("config fixed ABI drifted")
    return value


def _validate_scaler(content: bytes) -> tuple[str, ...]:
    value = _object(content, "scaler")
    if set(value) != {"contractId", "featureOrder", "fitScope", "symbols"}:
        raise P1ArtifactImportError("scaler closed field set drifted")
    if value.get("contractId") != "p1-return-scaler.v2" or value.get("fitScope") != "TRAIN_ONLY":
        raise P1ArtifactImportError("scaler contract or fit scope drifted")
    symbols_value = value.get("symbols")
    if not isinstance(symbols_value, dict):
        raise P1ArtifactImportError("scaler symbol map is invalid")
    symbols = tuple(symbols_value)
    _require_symbols(symbols)
    for symbol, parameters in symbols_value.items():
        if not isinstance(parameters, dict) or set(parameters) != {"mean", "scale"}:
            raise P1ArtifactImportError(f"scaler parameters are invalid: {symbol}")
        mean = parameters["mean"]
        scale = parameters["scale"]
        if (
            not isinstance(mean, list)
            or not isinstance(scale, list)
            or len(mean) != len(FEATURE_ORDER)
            or len(scale) != len(FEATURE_ORDER)
            or not all(_finite_number(item) for item in mean)
            or not all(_finite_number(item) and float(item) > 0 for item in scale)
        ):
            raise P1ArtifactImportError(f"scaler values are invalid: {symbol}")
    semantic = {
        "featureOrder": list(FEATURE_ORDER),
        "finite": True,
        "fitScope": "TRAIN_ONLY",
        "symbols": list(symbols),
    }
    _schema(
        "contracts/schemas/p1-return-scaler.v2.schema.json",
        _semantic_document("p1-return-scaler.v2", "scaler.json", semantic),
    )
    return symbols


def _validate_signals(content: bytes, producer: str) -> dict[str, Any]:
    table = _parquet(content, f"{producer} signals")
    if tuple(table.column_names) != _SIGNAL_COLUMNS or table.num_rows != 31:
        raise P1ArtifactImportError(f"{producer} signal table shape drifted")
    rows = cast(list[dict[str, Any]], table.to_pylist())
    symbols = tuple(cast(str, row["symbol"]) for row in rows)
    _require_symbols(symbols)
    session_dates = {row["sessionDate"] for row in rows}
    if len(session_dates) != 1 or not all(isinstance(item, str) for item in session_dates):
        raise P1ArtifactImportError(f"{producer} session date set drifted")
    for row in rows:
        if set(row) != set(_SIGNAL_COLUMNS):
            raise P1ArtifactImportError(f"{producer} signal row fields drifted")
        current = row["currentClose"]
        forecast = row["forecastClose"]
        expected = row["expectedReturn"]
        confidence = row["confidence"]
        if (
            not _finite_number(current)
            or float(current) <= 0
            or not _finite_number(forecast)
            or float(forecast) < 0
            or not _finite_number(expected)
            or not _finite_number(confidence)
            or not 0 <= float(confidence) <= 1
        ):
            raise P1ArtifactImportError(f"{producer} signal row contains invalid numbers")
        recalculated = float(forecast) / float(current) - 1.0
        if not math.isclose(float(expected), recalculated, rel_tol=0.0, abs_tol=1e-12):
            raise P1ArtifactImportError(f"{producer} forecast arithmetic mismatch")
        expected_signal = "BUY" if forecast > current else "SELL" if forecast < current else "HOLD"
        if row["signal"] != expected_signal:
            raise P1ArtifactImportError(f"{producer} signal classification mismatch")
    schema_id = (
        "p1-return-lstm-signals.v2" if producer == "LSTM" else "p1-return-rule-baseline-signals.v2"
    )
    semantic = {
        "finite": True,
        "rowCount": 31,
        "rowSchema": rows[0],
        "symbols": list(symbols),
    }
    _schema(
        f"contracts/schemas/{schema_id}.schema.json",
        _semantic_document(
            schema_id,
            "lstm_signals.parquet" if producer == "LSTM" else "rule_baseline_signals.parquet",
            semantic,
        ),
    )
    return {"rows": rows, "sessionDate": next(iter(session_dates)), "symbols": symbols}


def _validate_safetensors(content: bytes, symbols: tuple[str, ...]) -> None:
    if len(content) < 16:
        raise P1ArtifactImportError("safetensors file is truncated")
    header_length = struct.unpack("<Q", content[:8])[0]
    if header_length < 2 or header_length > 8 * 1024 * 1024 or 8 + header_length >= len(content):
        raise P1ArtifactImportError("safetensors header length is invalid")
    try:
        header = json.loads(content[8 : 8 + header_length])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P1ArtifactImportError("safetensors header JSON is invalid") from error
    if not isinstance(header, dict):
        raise P1ArtifactImportError("safetensors header is not an object")
    metadata = header.pop("__metadata__", None)
    if not isinstance(metadata, dict) or metadata.get("symbolCount") != "31":
        raise P1ArtifactImportError("safetensors metadata is invalid")
    expected_names = {f"{symbol}.{suffix}" for symbol in symbols for suffix in _TENSOR_SHAPES}
    if set(header) != expected_names:
        raise P1ArtifactImportError("safetensors exact tensor inventory drifted")
    data = memoryview(content)[8 + header_length :]
    extents: list[tuple[int, int]] = []
    for name, descriptor in header.items():
        if not isinstance(descriptor, dict) or descriptor.get("dtype") != "F32":
            raise P1ArtifactImportError(f"safetensors dtype drifted: {name}")
        suffix = name.split(".", maxsplit=1)[1]
        shape = descriptor.get("shape")
        offsets = descriptor.get("data_offsets")
        if (
            shape != list(_TENSOR_SHAPES[suffix])
            or not isinstance(offsets, list)
            or len(offsets) != 2
        ):
            raise P1ArtifactImportError(f"safetensors tensor descriptor drifted: {name}")
        start, end = offsets
        expected_bytes = math.prod(_TENSOR_SHAPES[suffix]) * 4
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end - start != expected_bytes
            or end > len(data)
        ):
            raise P1ArtifactImportError(f"safetensors tensor extent drifted: {name}")
        values = np.frombuffer(data[start:end], dtype="<f4")
        if not bool(np.isfinite(values).all()):
            raise P1ArtifactImportError(f"safetensors tensor is non-finite: {name}")
        extents.append((start, end))
    cursor = 0
    for start, end in sorted(extents):
        if start != cursor:
            raise P1ArtifactImportError("safetensors data extents overlap or contain gaps")
        cursor = end
    if cursor != len(data):
        raise P1ArtifactImportError("safetensors data length drifted")
    semantic = {
        "dtype": "FLOAT32",
        "finite": True,
        "format": "SAFETENSORS",
        "pickleFree": True,
        "symbolNamespaces": list(symbols),
        "tensorCount": len(header),
    }
    _schema(
        "contracts/schemas/p1-return-model-safetensors.v2.schema.json",
        _semantic_document("p1-return-model-safetensors.v2", "model.safetensors", semantic),
    )


def _validate_golden_output(
    content: bytes, *, manifest: dict[str, Any], lstm_rows: list[dict[str, Any]]
) -> None:
    value = _object(content, "golden output")
    expected = {
        "contractId",
        "costModelId",
        "evidenceMode",
        "forecastFormula",
        "inputPackSha256",
        "orderAuthority",
        "performanceClaimAllowed",
        "predictions",
    }
    if set(value) != expected or value.get("contractId") != "p1-return-golden-output.v2":
        raise P1ArtifactImportError("golden output closed field set drifted")
    if (
        value["costModelId"] != "CONSERVATIVE_FIXED_35BPS_V1"
        or value["forecastFormula"] != "forecastClose/currentClose-1"
        or value["inputPackSha256"] != manifest["inputPackSha256"]
        or value["orderAuthority"] != "NONE"
        or value["performanceClaimAllowed"] is not False
    ):
        raise P1ArtifactImportError("golden output authority or input binding drifted")
    predictions = value["predictions"]
    if not isinstance(predictions, list) or len(predictions) != 31:
        raise P1ArtifactImportError("golden output must contain exact-31 predictions")
    expected_predictions = [
        {
            "currentClose": row["currentClose"],
            "expectedReturn": row["expectedReturn"],
            "forecastClose": row["forecastClose"],
            "symbol": row["symbol"],
        }
        for row in lstm_rows
    ]
    if predictions != expected_predictions:
        raise P1ArtifactImportError("golden output does not independently match LSTM signals")
    semantic = {
        "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
        "finite": True,
        "forecastFormula": "forecastClose/currentClose-1",
        "goldenHash": _digest(content),
        "symbols": [row["symbol"] for row in predictions],
    }
    _schema(
        "contracts/schemas/p1-return-golden-output.v2.schema.json",
        _semantic_document("p1-return-golden-output.v2", "golden_output.json", semantic),
    )


def _validate_backtest(
    *, backtest_bytes: bytes, trade_bytes: bytes, equity_bytes: bytes
) -> dict[str, dict[str, float | int]]:
    backtest = _object(backtest_bytes, "backtest result")
    if set(backtest) != {
        "contractId",
        "independentlyRecomputed",
        "performanceClaimAllowed",
        "scenarios",
    }:
        raise P1ArtifactImportError("backtest result closed field set drifted")
    if (
        backtest.get("contractId") != "p1-return-backtest-result.v2"
        or backtest.get("independentlyRecomputed") is not True
        or backtest.get("performanceClaimAllowed") is not False
    ):
        raise P1ArtifactImportError("backtest truth markers drifted")
    trade_table = _parquet(trade_bytes, "trade log")
    equity_table = _parquet(equity_bytes, "equity log")
    if (
        tuple(trade_table.column_names) != _TRADE_COLUMNS
        or tuple(equity_table.column_names) != _EQUITY_COLUMNS
    ):
        raise P1ArtifactImportError("backtest Parquet column contract drifted")
    trade_rows = cast(list[dict[str, Any]], trade_table.to_pylist())
    equity_rows = cast(list[dict[str, Any]], equity_table.to_pylist())
    if not equity_rows:
        raise P1ArtifactImportError("equity log is empty")
    for row in trade_rows:
        if (
            row["scenario"] not in _SCENARIOS
            or row["side"] not in {"LONG", "BUY_SELL"}
            or not isinstance(row["quantity"], int)
            or row["quantity"] <= 0
            or not all(
                _finite_number(row[name])
                for name in ("entryPrice", "exitPrice", "grossReturn", "costBps", "netReturn")
            )
            or row["entryPrice"] <= 0
            or row["exitPrice"] < 0
            or row["costBps"] != 35
        ):
            raise P1ArtifactImportError("trade log violates long-only fixed-cost semantics")
        gross = float(row["exitPrice"]) / float(row["entryPrice"]) - 1.0
        net = gross - 0.0035
        if not math.isclose(float(row["grossReturn"]), gross, abs_tol=1e-12) or not math.isclose(
            float(row["netReturn"]), net, abs_tol=1e-12
        ):
            raise P1ArtifactImportError("trade return arithmetic mismatch")
    recomputed: dict[str, dict[str, float | int]] = {}
    for scenario in _SCENARIOS:
        curve = sorted(
            (row for row in equity_rows if row["scenario"] == scenario),
            key=lambda row: cast(date, row["sessionDate"]),
        )
        if not curve or not all(
            _finite_number(row["equityKrw"]) and _finite_number(row["drawdown"]) for row in curve
        ):
            raise P1ArtifactImportError(f"equity curve is missing or non-finite: {scenario}")
        initial = float(curve[0]["equityKrw"])
        final = float(curve[-1]["equityKrw"])
        if initial <= 0 or final < 0:
            raise P1ArtifactImportError(f"equity curve has invalid capital: {scenario}")
        peaks: list[float] = []
        running_peak = 0.0
        returns: list[float] = []
        previous: float | None = None
        for row in curve:
            equity = float(row["equityKrw"])
            running_peak = max(running_peak, equity)
            peaks.append(equity / running_peak - 1.0 if running_peak else 0.0)
            if previous is not None and previous > 0:
                returns.append(equity / previous - 1.0)
            previous = equity
        mdd = min(peaks)
        sharpe = _annualized_sharpe(returns)
        net_return = final / initial - 1.0
        count = sum(1 for row in trade_rows if row["scenario"] == scenario)
        recomputed[scenario] = {
            "netReturn": net_return,
            "mdd": mdd,
            "sharpe": sharpe,
            "tradeCount": count,
        }
    scenarios = backtest.get("scenarios")
    if not isinstance(scenarios, list) or [
        row.get("scenario") for row in scenarios if isinstance(row, dict)
    ] != list(_SCENARIOS):
        raise P1ArtifactImportError("backtest scenario order drifted")
    for row in scenarios:
        if not isinstance(row, dict) or set(row) != {
            "scenario",
            "netReturn",
            "mdd",
            "sharpe",
            "tradeCount",
            "costModelId",
        }:
            raise P1ArtifactImportError("backtest scenario fields drifted")
        scenario = cast(str, row["scenario"])
        if row["costModelId"] != "CONSERVATIVE_FIXED_35BPS_V1":
            raise P1ArtifactImportError("backtest cost model drifted")
        for metric in ("netReturn", "mdd", "sharpe"):
            if not _finite_number(row[metric]) or not math.isclose(
                float(row[metric]), float(recomputed[scenario][metric]), abs_tol=1e-12
            ):
                raise P1ArtifactImportError(f"backtest {metric} independent recomputation mismatch")
        if row["tradeCount"] != recomputed[scenario]["tradeCount"]:
            raise P1ArtifactImportError("backtest trade count independent recomputation mismatch")
    semantic = {"finite": True, "independentlyRecomputed": True, "scenarios": scenarios}
    _schema(
        "contracts/schemas/p1-return-backtest-result.v2.schema.json",
        _semantic_document("p1-return-backtest-result.v2", "backtest_result.json", semantic),
    )
    _schema(
        "contracts/schemas/p1-return-trade-log.v2.schema.json",
        _semantic_document(
            "p1-return-trade-log.v2",
            "trade_log.parquet",
            {
                "columns": list(_TRADE_COLUMNS),
                "finite": True,
                "longOnly": True,
                "rowCount": len(trade_rows),
            },
        ),
    )
    _schema(
        "contracts/schemas/p1-return-equity-log.v2.schema.json",
        _semantic_document(
            "p1-return-equity-log.v2",
            "equity_log.parquet",
            {
                "columns": list(_EQUITY_COLUMNS),
                "finite": True,
                "initialCapitalKrw": int(equity_rows[0]["equityKrw"]),
                "rowCount": len(equity_rows),
            },
        ),
    )
    return recomputed


def _validate_report(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise P1ArtifactImportError("model report is not UTF-8") from error
    sections = ["Data", "Model ABI", "Split", "Reproducibility", "Model quality", "Limitations"]
    if any(f"## {section}" not in text for section in sections):
        raise P1ArtifactImportError("model report required section is missing")
    semantic = {
        "encoding": "UTF-8",
        "orderAuthority": "NONE",
        "performanceClaimAllowed": False,
        "requiredSections": sections,
    }
    _schema(
        "contracts/schemas/p1-return-model-report.v2.schema.json",
        _semantic_document("p1-return-model-report.v2", "model_report.md", semantic),
    )


def _build_import_packet(
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    artifact_id: str,
    session_date: date,
    as_of: datetime,
    fresh_until: datetime,
    lstm_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    scenarios: dict[str, dict[str, float | int]],
    equity_bytes: bytes,
) -> dict[str, Any]:
    run_id = cast(str, manifest["runId"])
    fixture = manifest["evidenceMode"] == "SYNTHETIC_GOLDEN"
    model_version = cast(dict[str, Any], manifest["producer"])["trainingCodeSha256"][:32]
    report_id = f"mrp_p1_{manifest_sha256[:24]}"
    signals: list[dict[str, Any]] = []
    for producer, rows in (("LSTM", lstm_rows), ("RULE_BASELINE", baseline_rows)):
        for row in rows:
            signal = {
                "asOf": _instant(as_of),
                "confidence": row["confidence"],
                "modelReportId": report_id,
                "modelVersion": model_version,
                "predictedReturn": row["expectedReturn"],
                "producer": producer,
                "sessionDate": session_date.isoformat(),
                "signal": row["signal"],
                "symbol": row["symbol"],
            }
            signal["payloadSha256"] = _digest(canonical_json_bytes(signal))
            signals.append(signal)
    evidence_mode = "SYNTHETIC_DEMO" if fixture else "REAL_ARTIFACT"
    fixture_class = "SYNTHETIC_FAKE_E2E" if fixture else "REAL_ARTIFACT"
    request_id = f"req_p1_{manifest_sha256[:24]}"
    metrics_by_model = {
        "BASELINE": scenarios["BASELINE"],
        "LSTM": scenarios["GUIDE"],
    }
    models = []
    for model_id in ("BASELINE", "LSTM", "LIGHTGBM"):
        source = metrics_by_model.get(model_id)
        models.append(
            {
                "metrics": _dashboard_metrics(source),
                "modelId": model_id,
                "status": "ABSTAIN" if source is None else "AVAILABLE",
            }
        )
    equity = _parquet(equity_bytes, "equity projection")
    equity_rows = cast(list[dict[str, Any]], equity.to_pylist())
    timeline = [
        {"at": _date_instant(cast(date, row["sessionDate"])), "value": float(row["equityKrw"])}
        for row in equity_rows
        if row["scenario"] == "GUIDE"
    ][:500]
    model_projection = _envelope(
        request_id=request_id,
        evidence_mode=evidence_mode,
        as_of=as_of,
        fresh_until=fresh_until,
        view={"models": models, "runId": run_id, "sourceRunIds": [run_id], "timeline": timeline},
    )
    strategies = []
    for scenario, label in zip(_SCENARIOS, ("Baseline", "Guide", "Strict"), strict=True):
        curve = [
            {"at": _date_instant(cast(date, row["sessionDate"])), "value": float(row["equityKrw"])}
            for row in equity_rows
            if row["scenario"] == scenario
        ][:2000]
        strategies.append(
            {"curve": curve, "metrics": _dashboard_metrics(scenarios[scenario]), "strategy": label}
        )
    backtest_view = {
        "fixtureClass": fixture_class,
        "heatmap": [],
        "metricCards": [
            {"metric": "netReturn", "value": scenarios["GUIDE"]["netReturn"]},
            {"metric": "mdd", "value": scenarios["GUIDE"]["mdd"]},
            {"metric": "sharpe", "value": scenarios["GUIDE"]["sharpe"]},
            {"metric": "tradeCount", "value": scenarios["GUIDE"]["tradeCount"]},
        ],
        "projectionHash": f"sha256:{manifest_sha256}",
        "runId": run_id,
        "strategies": strategies,
    }
    backtest_projection = _envelope(
        request_id=request_id,
        evidence_mode=evidence_mode,
        as_of=as_of,
        fresh_until=fresh_until,
        view=backtest_view,
    )
    _schema("contracts/schemas/dashboard-model-evaluation.v1.schema.json", model_projection)
    _schema("contracts/schemas/dashboard-backtest.v1.schema.json", backtest_projection)
    packet = {
        "artifactId": artifact_id,
        "backtestProjection": backtest_projection,
        "backtestProjectionSha256": _digest(canonical_json_bytes(backtest_projection)),
        "bundleSha256": manifest_sha256,
        "contractId": "p1-return-artifact-import.v1",
        "evidenceMode": cast(str, manifest["evidenceMode"]),
        "fixtureClass": fixture_class,
        "freshUntil": _instant(fresh_until),
        "inputPackSha256": cast(str, manifest["inputPackSha256"]),
        "manifestFileName": GOLDEN_MANIFEST,
        "manifestSha256": manifest_sha256,
        "mockRuntimeEligible": cast(bool, manifest["mockRuntimeEligible"]),
        "modelProjection": model_projection,
        "modelProjectionSha256": _digest(canonical_json_bytes(model_projection)),
        "modelQuality": cast(str, manifest["modelQuality"]),
        "realTeamB": cast(bool, manifest["realTeamB"]),
        "runId": run_id,
        "sessionDate": session_date.isoformat(),
        "signals": sorted(signals, key=lambda item: (item["symbol"], item["producer"])),
        "sourceWorkspace": "return-engine",
        "asOf": _instant(as_of),
    }
    return packet


def _dashboard_metrics(source: dict[str, float | int] | None) -> dict[str, float | None]:
    if source is None:
        return {key: None for key in ("cagr", "cvar95", "mdd", "sharpe", "sortino", "var95")}
    return {
        "cagr": float(source["netReturn"]),
        "cvar95": None,
        "mdd": float(source["mdd"]),
        "sharpe": float(source["sharpe"]),
        "sortino": None,
        "var95": None,
    }


def _envelope(
    *,
    request_id: str,
    evidence_mode: str,
    as_of: datetime,
    fresh_until: datetime,
    view: dict[str, Any],
) -> dict[str, Any]:
    return {
        "data": {
            "asOf": _instant(as_of),
            "evidenceMode": evidence_mode,
            "freshUntil": _instant(fresh_until),
            "performanceClaimAllowed": False,
            "view": view,
            "viewState": "READY",
        },
        "error": None,
        "requestId": request_id,
        "success": True,
        "warnings": [],
    }


def _validate_xkrx_session(session_date: date) -> None:
    try:
        next_session_evidence_clock(session_date)
    except Exception as error:
        raise P1ArtifactImportError(
            "signal session is not an authoritative XKRX session"
        ) from error


def _require_symbols(symbols: tuple[str, ...]) -> None:
    if len(symbols) != 31 or len(set(symbols)) != 31 or symbols.count("132030") != 1:
        raise P1ArtifactImportError("artifact symbol namespace is not exact-31")
    if any(not _SYMBOL.fullmatch(symbol) for symbol in symbols):
        raise P1ArtifactImportError("artifact symbol format is invalid")


def _annualized_sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    values = np.asarray(returns, dtype=np.float64)
    standard_deviation = float(np.std(values, ddof=1))
    if standard_deviation == 0.0:
        return 0.0
    return float(np.mean(values) / standard_deviation * math.sqrt(253.0))


def _parquet(content: bytes, label: str) -> pa.Table:
    try:
        return pq.read_table(pa.BufferReader(content))  # type: ignore[no-untyped-call]
    except (pa.ArrowException, OSError) as error:
        raise P1ArtifactImportError(f"{label} Parquet is invalid") from error


def _semantic_document(
    contract_id: str, file_name: str, semantic: dict[str, Any]
) -> dict[str, Any]:
    return {"contractId": contract_id, "fileName": file_name, "semantic": semantic}


def _schema(relative: str, payload: object) -> None:
    try:
        _validate_repository_schema(relative, payload)
    except P1OwnerAssetError as error:
        raise P1ArtifactImportError(str(error)) from error


def _object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P1ArtifactImportError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise P1ArtifactImportError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise P1ArtifactImportError(f"{label} must be lowercase SHA-256")
    return value


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise P1ArtifactImportError("projection clock must be timezone aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date_instant(value: date) -> str:
    return (
        datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 Return Engine v2 artifact importer")
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--archive-parent", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            validated = validate_artifact_bundle(
                bundle_root=args.bundle_root,
                expected_manifest_sha256=args.manifest_sha256,
            )
            output = {
                "artifactId": validated.artifact_id,
                "bundleSha256": validated.bundle_sha256,
                "databaseOutcome": "NOT_RUN_VALIDATE_ONLY",
                "providerCalls": 0,
                "runId": validated.run_id,
                "status": "VALIDATED",
            }
        else:
            dsn = os.environ.get("P1_ARTIFACT_IMPORT_DATABASE_DSN", "")
            if not dsn:
                raise P1ArtifactImportError("artifact importer database DSN is unavailable")
            result = import_artifact_bundle(
                bundle_root=args.bundle_root,
                expected_manifest_sha256=args.manifest_sha256,
                archive_parent=args.archive_parent,
                database_dsn=dsn,
            )
            output = {
                "archiveNoOp": result.archive_no_op,
                "artifactId": result.artifact_id,
                "bundleSha256": result.bundle_sha256,
                "databaseOutcome": result.database_outcome,
                "providerCalls": 0,
                "runId": result.run_id,
                "status": "IMPORTED",
            }
    except (P1ArtifactImportError, OSError) as error:
        print(f"P1_ARTIFACT_IMPORT_FAILED: {error}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(output).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
