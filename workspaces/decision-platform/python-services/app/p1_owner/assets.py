"""Build and verify the sealed Owner input pack and synthetic Team B golden bundle.

이 모듈은 검증된 local market-data archive만 읽는다. provider, account, order client를 import하지
않으며 output은 새 owner-private directory에 manifest-last로 게시한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import struct
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from jsonschema import Draft202012Validator, FormatChecker

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.market_data.archive import (
    MarketDataArchive,
    MarketDataArchiveError,
    read_artifact_table,
    read_market_data_archive,
)
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file

FEATURE_ORDER = (
    "open",
    "high",
    "low",
    "raw_close",
    "volume",
    "return_1d",
    "ma5",
    "ma20",
    "rsi14",
)
ARTIFACT_NAMES = (
    "model.safetensors",
    "scaler.json",
    "config.json",
    "lstm_signals.parquet",
    "rule_baseline_signals.parquet",
    "backtest_result.json",
    "trade_log.parquet",
    "equity_log.parquet",
    "golden_output.json",
    "model_report.md",
)
ARTIFACT_SCHEMA_IDS = (
    "p1-return-model-safetensors.v2",
    "p1-return-scaler.v2",
    "p1-return-config.v2",
    "p1-return-lstm-signals.v3",
    "p1-return-rule-baseline-signals.v3",
    "p1-return-backtest-result.v2",
    "p1-return-trade-log.v2",
    "p1-return-equity-log.v2",
    "p1-return-golden-output.v2",
    "p1-return-model-report.v2",
)
INPUT_MANIFEST = "manifest.json"
GOLDEN_MANIFEST = "p1-return-engine-manifest.v3.json"
CALENDAR_CATALOG = "contracts/catalogs/s5-bootstrap-calendar-recovery-lock.v1.json"
INPUT_SCHEMA = "contracts/schemas/p1-return-engine-input-pack.v1.schema.json"
MANIFEST_SCHEMA = "contracts/schemas/p1-return-engine-artifact-manifest.v3.schema.json"
SCENARIO_POLICY = "contracts/examples/p1-scenario-replay-policy.v1.valid.json"
MAX_MANIFEST_BYTES = 1_048_576
MAX_ARCHIVE_ARTIFACT_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[0-9]{6}$")


class P1OwnerAssetError(ValueError):
    """Owner input/golden asset did not satisfy the closed contract."""


@dataclass(frozen=True, slots=True)
class AssetBuildResult:
    output_root: Path
    manifest_sha256: str
    file_count: int
    no_op: bool


def build_input_pack(
    *, archive_root: Path, expected_archive_manifest_sha256: str, output_root: Path
) -> AssetBuildResult:
    """검증된 S5.7 archive를 exact-31 Team B 입력 pack으로 provider 0에서 변환한다."""

    archive = read_market_data_archive(archive_root)
    if archive.manifest_sha256 != _sha(expected_archive_manifest_sha256, "archive manifest"):
        raise P1OwnerAssetError("archive manifest SHA-256 does not match the approved input")
    bars = read_artifact_table(archive, "BARS")
    macro = read_artifact_table(archive, "MACRO")
    universes = read_artifact_table(archive, "UNIVERSES")
    indices = read_artifact_table(archive, "INDICES")
    tables, metadata = _materialize_input_tables(
        bars=bars,
        macro=macro,
        universes=universes,
        indices=indices,
    )
    payloads: dict[str, bytes] = {
        "daily_ohlcv.parquet": _parquet_bytes(tables["bars"]),
        "ecos_macro.parquet": _parquet_bytes(tables["macro"]),
        "universe.parquet": _parquet_bytes(tables["universe"]),
        "xkrx_sessions.json": canonical_json_bytes(metadata["sessions"]),
        "scenario_policy.json": _read_repository_json_bytes(SCENARIO_POLICY),
        "corporate_action_exclusions.json": canonical_json_bytes(
            {
                "basis": "NORMALIZED_ARCHIVE_NO_DECLARED_EXCLUSION_ROWS",
                "contractId": "p1-corporate-action-exclusions.v1",
                "exclusions": [],
                "performanceClaimAllowed": False,
                "priceBasis": "RAW_CLOSE",
                "sourceArchiveManifestSha256": archive.manifest_sha256,
            }
        ),
    }
    manifest = _input_manifest(
        archive=archive,
        metadata=metadata,
        payloads=payloads,
    )
    _validate_repository_schema(INPUT_SCHEMA, manifest)
    manifest_bytes = canonical_json_bytes(manifest)
    no_op = _publish_directory(
        output_root=output_root,
        payloads=payloads,
        manifest_name=INPUT_MANIFEST,
        manifest_bytes=manifest_bytes,
    )
    verified = verify_input_pack(output_root)
    return AssetBuildResult(output_root, verified, len(payloads), no_op)


def verify_input_pack(root: Path) -> str:
    """Rebind every input-pack file to the closed manifest and return manifest SHA-256."""

    manifest_bytes = _read_regular(root, INPUT_MANIFEST, MAX_MANIFEST_BYTES)
    manifest = _json_object(manifest_bytes, "input pack manifest")
    _validate_repository_schema(INPUT_SCHEMA, manifest)
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 6:
        raise P1OwnerAssetError("input pack must bind exactly six payload files")
    expected = {
        "daily_ohlcv.parquet",
        "ecos_macro.parquet",
        "universe.parquet",
        "xkrx_sessions.json",
        "scenario_policy.json",
        "corporate_action_exclusions.json",
    }
    if {cast(str, item.get("path")) for item in files if isinstance(item, dict)} != expected:
        raise P1OwnerAssetError("input pack file inventory drifted")
    for item in files:
        if not isinstance(item, dict):
            raise P1OwnerAssetError("input pack file receipt is invalid")
        relative = _text(item.get("path"), "input file path")
        content = _read_regular(root, relative, MAX_ARCHIVE_ARTIFACT_BYTES)
        if len(content) != item.get("sizeBytes") or _digest(content) != item.get("sha256"):
            raise P1OwnerAssetError(f"input pack file binding mismatch: {relative}")
    _verify_input_pack_tables(root, manifest)
    return _digest(manifest_bytes)


def write_input_pack_zip(root: Path, output_zip: Path) -> str:
    """Write one deterministic ZIP after the directory pack has been fully verified."""

    verify_input_pack(root)
    if not output_zip.is_absolute() or output_zip.exists() or output_zip.is_symlink():
        raise P1OwnerAssetError("input pack ZIP target must be a new absolute path")
    parent = output_zip.parent
    if not parent.is_dir() or parent.is_symlink():
        raise P1OwnerAssetError("input pack ZIP parent is unsafe")
    names = (
        "corporate_action_exclusions.json",
        "daily_ohlcv.parquet",
        "ecos_macro.parquet",
        INPUT_MANIFEST,
        "scenario_policy.json",
        "universe.parquet",
        "xkrx_sessions.json",
    )
    try:
        with (
            output_zip.open("xb") as raw,
            zipfile.ZipFile(
                raw, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive,
        ):
            for name in names:
                content = _read_regular(root, name, MAX_ARCHIVE_ARTIFACT_BYTES)
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                archive.writestr(info, content)
        os.chmod(output_zip, 0o600)
        return _digest(output_zip.read_bytes())
    except Exception:
        if output_zip.is_file() and not output_zip.is_symlink():
            output_zip.unlink()
        raise


def build_golden_bundle(*, input_pack_manifest: Path, output_root: Path) -> AssetBuildResult:
    """Create the exact-ten synthetic bundle with the real wire shape and zero authority."""

    input_root = input_pack_manifest.parent
    input_manifest_sha256 = verify_input_pack(input_root)
    if input_pack_manifest.name != INPUT_MANIFEST or input_pack_manifest.is_symlink():
        raise P1OwnerAssetError("golden input must be the canonical input-pack manifest")
    input_manifest = _json_object(
        _read_regular(input_root, INPUT_MANIFEST, MAX_MANIFEST_BYTES),
        "input pack manifest",
    )
    symbols = cast(list[str], cast(dict[str, Any], input_manifest["universe"])["symbols"])
    if len(symbols) != 31 or symbols.count("132030") != 1:
        raise P1OwnerAssetError("golden bundle requires the exact-31 input universe")
    sessions = _json_list(
        _read_regular(input_root, "xkrx_sessions.json", MAX_MANIFEST_BYTES),
        "XKRX sessions",
    )
    session_date = _text(sessions[-1], "last XKRX session")
    payloads = _golden_payloads(
        symbols=symbols,
        session_date=session_date,
        input_pack_sha256=input_manifest_sha256,
    )
    manifest = _golden_manifest(
        input_pack_sha256=input_manifest_sha256,
        payloads=payloads,
    )
    _validate_repository_schema(MANIFEST_SCHEMA, manifest)
    no_op = _publish_directory(
        output_root=output_root,
        payloads=payloads,
        manifest_name=GOLDEN_MANIFEST,
        manifest_bytes=canonical_json_bytes(manifest),
    )
    verified = verify_golden_bundle(output_root)
    return AssetBuildResult(output_root, verified, len(payloads), no_op)


def verify_golden_bundle(root: Path) -> str:
    """Verify the synthetic truth markers, exact-ten files, hashes and safe file semantics."""

    manifest_bytes = _read_regular(root, GOLDEN_MANIFEST, MAX_MANIFEST_BYTES)
    manifest = _json_object(manifest_bytes, "golden manifest")
    _validate_repository_schema(MANIFEST_SCHEMA, manifest)
    if (
        manifest.get("evidenceMode") != "SYNTHETIC_GOLDEN"
        or manifest.get("realTeamB") is not False
        or manifest.get("performanceClaimAllowed") is not False
        or manifest.get("orderAuthority") != "NONE"
    ):
        raise P1OwnerAssetError("golden manifest attempted to claim real or order authority")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or [item.get("path") for item in artifacts] != list(
        ARTIFACT_NAMES
    ):
        raise P1OwnerAssetError("golden bundle does not contain the exact ordered ten files")
    for item in artifacts:
        relative = _text(item.get("path"), "golden artifact path")
        content = _read_regular(root, relative, 128 * 1024 * 1024)
        if len(content) != item.get("sizeBytes") or _digest(content) != item.get("sha256"):
            raise P1OwnerAssetError(f"golden artifact binding mismatch: {relative}")
    _verify_safetensors_header(_read_regular(root, "model.safetensors", 128 * 1024 * 1024))
    _verify_golden_json_and_parquet(root)
    return _digest(manifest_bytes)


def _materialize_input_tables(
    *, bars: pa.Table, macro: pa.Table, universes: pa.Table, indices: pa.Table
) -> tuple[dict[str, pa.Table], dict[str, Any]]:
    months = sorted(set(cast(list[str], universes["membershipMonth"].to_pylist())))
    if not months:
        raise P1OwnerAssetError("market-data archive has no monthly universe")
    membership_month = months[-1]
    current = universes.filter(
        pc.equal(  # type: ignore[attr-defined]
            universes["membershipMonth"], membership_month
        )
    ).sort_by([("rank", "ascending")])
    symbols = cast(list[str], current["symbol"].to_pylist())
    if (
        current.num_rows != 31
        or len(set(symbols)) != 31
        or symbols.count("132030") != 1
        or symbols[-1] != "132030"
    ):
        raise P1OwnerAssetError("latest universe is not exact-31 with fixed 132030")
    filtered_bars = bars.filter(
        pc.is_in(  # type: ignore[attr-defined]
            bars["symbol"], value_set=pa.array(symbols)
        )
    ).sort_by([("symbol", "ascending"), ("sessionDate", "ascending")])
    bar_projection = pa.table(
        {
            "symbol": filtered_bars["symbol"],
            "sessionDate": filtered_bars["sessionDate"],
            "open": filtered_bars["open"],
            "high": filtered_bars["high"],
            "low": filtered_bars["low"],
            "raw_close": filtered_bars["close"],
            "volume": filtered_bars["volume"],
        }
    )
    session_dates = sorted(set(cast(list[date], indices["sessionDate"].to_pylist())))
    if len(session_dates) < 756:
        raise P1OwnerAssetError("input pack does not contain a minimum three-year session window")
    coverage = _coverage(symbols=symbols, bars=bar_projection, sessions=session_dates)
    split_train = max(1, int(len(session_dates) * 0.60))
    split_validation = max(split_train + 1, int(len(session_dates) * 0.80))
    series = sorted(set(cast(list[str], macro["seriesId"].to_pylist())))
    if len(series) > 2:
        raise P1OwnerAssetError("input pack ECOS snapshot exceeds two series")
    return (
        {"bars": bar_projection, "macro": macro, "universe": current},
        {
            "membershipMonth": membership_month,
            "symbols": symbols,
            "coverage": coverage,
            "sessions": [value.isoformat() for value in session_dates],
            "firstSession": session_dates[0].isoformat(),
            "lastSession": session_dates[-1].isoformat(),
            "minimumYears": round((session_dates[-1] - session_dates[0]).days / 365.25, 6),
            "trainEnd": session_dates[split_train - 1].isoformat(),
            "validationEnd": session_dates[split_validation - 1].isoformat(),
            "testStart": session_dates[split_validation].isoformat(),
            "seriesCount": len(series),
        },
    )


def _coverage(*, symbols: list[str], bars: pa.Table, sessions: list[date]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    session_index = {value: index for index, value in enumerate(sessions)}
    for symbol in symbols:
        table = bars.filter(pc.equal(bars["symbol"], symbol))  # type: ignore[attr-defined]
        dates = cast(list[date], table["sessionDate"].to_pylist())
        if not dates:
            raise P1OwnerAssetError(f"input pack symbol has no bars: {symbol}")
        unique_dates = sorted(set(dates))
        if len(unique_dates) != len(dates):
            raise P1OwnerAssetError(f"input pack symbol has duplicate bars: {symbol}")
        first = unique_dates[0]
        last = unique_dates[-1]
        expected = sessions[session_index[first] : session_index[last] + 1]
        missing_middle = len(set(expected) - set(unique_dates))
        if missing_middle:
            raise P1OwnerAssetError(f"input pack symbol has middle-session gaps: {symbol}")
        status = "COMPLETE" if first == sessions[0] and last == sessions[-1] else "EDGE_TRUNCATED"
        result.append(
            {
                "firstSession": first.isoformat(),
                "lastSession": last.isoformat(),
                "missingMiddleSessions": 0,
                "status": status,
                "symbol": symbol,
            }
        )
    return result


def _input_manifest(
    *, archive: MarketDataArchive, metadata: dict[str, Any], payloads: dict[str, bytes]
) -> dict[str, Any]:
    sessions_sha = _digest(payloads["xkrx_sessions.json"])
    macro_sha = _digest(payloads["ecos_macro.parquet"])
    calendar = _repository_json(CALENDAR_CATALOG)
    correction_sha = _text(
        cast(dict[str, Any], calendar["calendar"])["correctionSetSha256"],
        "calendar correction SHA-256",
    )
    files = [
        {
            "contentType": "PARQUET" if name.endswith(".parquet") else "JSON",
            "path": name,
            "sha256": _digest(payload),
            "sizeBytes": len(payload),
        }
        for name, payload in sorted(payloads.items())
    ]
    preimage: dict[str, Any] = {
        "calendar": {
            "calendarVersion": "exchange-calendars-4.13.2",
            "correctionGenerationSha256": correction_sha,
            "mic": "XKRX",
            "sessionsSha256": sessions_sha,
            "timezone": "Asia/Seoul",
        },
        "contractId": "p1-return-engine-input-pack.v1",
        "costModel": {
            "actualKisFeeClaim": False,
            "appliesIdenticallyToScenarios": True,
            "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
            "roundTripCostBps": 35,
        },
        "coverage": metadata["coverage"],
        "dataPolicy": {
            "accountOrderDataIncluded": False,
            "corporateActionExclusionsSha256": _digest(
                payloads["corporate_action_exclusions.json"]
            ),
            "gdeltInputs": 0,
            "globalSplitSha256": _digest(
                canonical_json_bytes(
                    {
                        "testStart": metadata["testStart"],
                        "trainEnd": metadata["trainEnd"],
                        "validationEnd": metadata["validationEnd"],
                    }
                )
            ),
            "intradayFeatures": 0,
            "minimumDailyYears": 3,
            "newsFeatures": 0,
            "priceBasis": "RAW_CLOSE",
            "providerCredentialsIncluded": False,
        },
        "featureOrder": list(FEATURE_ORDER),
        "files": files,
        "macroSnapshot": {
            "availableAtBound": True,
            "contractId": "ecos_macro_snapshot",
            "manifestSha256": macro_sha,
            "seriesCount": metadata["seriesCount"],
        },
        "modelConfig": {
            "cpuDeterministic": True,
            "dropout": 0.2,
            "finalTestReviewCount": 0,
            "hiddenSize": 128,
            "hyperparameterSearchCount": 0,
            "layerCount": 3,
            "learningRate": 0.0005,
            "loss": "SmoothL1",
            "optimizer": "Adam",
            "outputSize": 1,
            "perSymbolIndependent": True,
            "seed": 0,
            "threadCount": 1,
            "windowSize": 20,
        },
        "ownerRiskEvaluator": {
            "contractId": "s2-2-system-rule-catalog/v1",
            "orderAuthority": "NONE",
            "providerCalls": 0,
        },
        "period": {
            "firstSession": metadata["firstSession"],
            "lastSession": metadata["lastSession"],
            "minimumYears": metadata["minimumYears"],
            "testStart": metadata["testStart"],
            "trainEnd": metadata["trainEnd"],
            "validationEnd": metadata["validationEnd"],
        },
        "universe": {
            "domesticStockCount": 30,
            "goldEtfSymbol": "132030",
            "symbols": metadata["symbols"],
            "universeId": "P1_EXACT_31_V1",
        },
    }
    preimage["canonicalManifestSha256"] = _digest(canonical_json_bytes(preimage))
    # source archive identity is intentionally reflected only by file hashes and the binding preimage.
    if (
        archive.manifest_sha256
        != "e3f26485c93d5e8bd9cdbd7f9ea7cc46cf3f446cf42e9d65b28f1f5b89bd9a5c"
    ):
        raise P1OwnerAssetError("unexpected market-data archive generation")
    return preimage


def _golden_payloads(
    *, symbols: list[str], session_date: str, input_pack_sha256: str
) -> dict[str, bytes]:
    lstm_rows: list[dict[str, Any]] = []
    rule_rows: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols):
        current = 10_000 + index * 10
        forecast = current + (1 if index % 3 == 0 else -1 if index % 3 == 1 else 0)
        expected = (forecast / current) - 1
        signal = "BUY" if forecast > current else "SELL" if forecast < current else "HOLD"
        lstm_rows.append(
            {
                "currentClose": current,
                "expectedReturn": expected,
                "forecastClose": forecast,
                "sessionDate": session_date,
                "signal": signal,
                "symbol": symbol,
            }
        )
        rule_rows.append(
            {
                "currentClose": current,
                "expectedReturn": 0.0,
                "forecastClose": current,
                "sessionDate": session_date,
                "signal": "HOLD",
                "symbol": symbol,
            }
        )
        predictions.append(
            {
                "currentClose": current,
                "expectedReturn": expected,
                "forecastClose": forecast,
                "symbol": symbol,
            }
        )
    config = {
        "contractId": "p1-return-config.v2",
        "deterministicAlgorithms": True,
        "dropout": 0.2,
        "featureOrder": list(FEATURE_ORDER),
        "hiddenSize": 128,
        "layerCount": 3,
        "learningRate": 0.0005,
        "loss": "SmoothL1",
        "optimizer": "Adam",
        "outputSize": 1,
        "perSymbolIndependent": True,
        "seed": 0,
        "threadCount": 1,
        "windowSize": 20,
    }
    scaler = {
        "contractId": "p1-return-scaler.v2",
        "featureOrder": list(FEATURE_ORDER),
        "fitScope": "TRAIN_ONLY",
        "symbols": {
            symbol: {
                "mean": [0.0 for _ in FEATURE_ORDER],
                "scale": [1.0 for _ in FEATURE_ORDER],
            }
            for symbol in symbols
        },
    }
    scenarios = [
        {
            "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
            "mdd": 0.0,
            "netReturn": 0.0,
            "scenario": scenario,
            "sharpe": 0.0,
            "tradeCount": 0,
        }
        for scenario in ("BASELINE", "GUIDE", "STRICT")
    ]
    equity = pa.Table.from_pylist(
        [
            {
                "drawdown": 0.0,
                "equityKrw": 10_000_000,
                "scenario": scenario,
                "sessionDate": date.fromisoformat(session_date),
            }
            for scenario in ("BASELINE", "GUIDE", "STRICT")
        ],
        schema=pa.schema(
            [
                ("scenario", pa.string()),
                ("sessionDate", pa.date32()),
                ("equityKrw", pa.int64()),
                ("drawdown", pa.float64()),
            ]
        ),
    )
    trade_schema = pa.schema(
        [
            ("scenario", pa.string()),
            ("symbol", pa.string()),
            ("entrySession", pa.date32()),
            ("exitSession", pa.date32()),
            ("side", pa.string()),
            ("quantity", pa.int64()),
            ("entryPrice", pa.int64()),
            ("exitPrice", pa.int64()),
            ("grossReturn", pa.float64()),
            ("costBps", pa.int64()),
            ("netReturn", pa.float64()),
        ]
    )
    golden = {
        "contractId": "p1-return-golden-output.v2",
        "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
        "evidenceMode": "SYNTHETIC_GOLDEN",
        "forecastFormula": "forecastClose/currentClose-1",
        "inputPackSha256": input_pack_sha256,
        "orderAuthority": "NONE",
        "performanceClaimAllowed": False,
        "predictions": predictions,
    }
    report = """# Synthetic Golden Model Report

## Data
Synthetic exact-31 fixture only.

## Model ABI
Fixed independent per-symbol LSTM ABI.

## Split
Global time split; scaler fit scope is TRAIN_ONLY.

## Reproducibility
Seed 0, CPU deterministic algorithms, one thread.

## Model quality
NOT_EVALUATED_SYNTHETIC.

## Limitations
No performance claim and no order authority.
""".encode()
    return {
        "model.safetensors": _safetensors_bytes(symbols),
        "scaler.json": canonical_json_bytes(scaler),
        "config.json": canonical_json_bytes(config),
        "lstm_signals.parquet": _parquet_bytes(pa.Table.from_pylist(lstm_rows)),
        "rule_baseline_signals.parquet": _parquet_bytes(pa.Table.from_pylist(rule_rows)),
        "backtest_result.json": canonical_json_bytes(
            {
                "contractId": "p1-return-backtest-result.v2",
                "independentlyRecomputed": True,
                "performanceClaimAllowed": False,
                "scenarios": scenarios,
            }
        ),
        "trade_log.parquet": _parquet_bytes(pa.Table.from_pylist([], schema=trade_schema)),
        "equity_log.parquet": _parquet_bytes(equity),
        "golden_output.json": canonical_json_bytes(golden),
        "model_report.md": report,
    }


def _golden_manifest(*, input_pack_sha256: str, payloads: dict[str, bytes]) -> dict[str, Any]:
    code_sha = _repository_file_sha(
        "workspaces/decision-platform/python-services/app/p1_owner/assets.py"
    )
    lock_sha = _repository_file_sha("workspaces/decision-platform/python-services/uv.lock")
    docker_sha = _repository_file_sha("workspaces/return-engine/Dockerfile")
    return {
        "artifacts": [
            {
                "path": name,
                "semanticSchema": f"contracts/schemas/{schema_id}.schema.json",
                "sha256": _digest(payloads[name]),
                "sizeBytes": len(payloads[name]),
            }
            for name, schema_id in zip(ARTIFACT_NAMES, ARTIFACT_SCHEMA_IDS, strict=True)
        ],
        "contractId": "p1-return-engine-artifact-manifest.v3",
        "evidenceMode": "SYNTHETIC_GOLDEN",
        "furtherTuningRequired": False,
        "inputPackSha256": input_pack_sha256,
        "mockRuntimeEligible": True,
        "modelQuality": "NOT_EVALUATED_SYNTHETIC",
        "orderAuthority": "NONE",
        "performanceClaimAllowed": False,
        "producer": {
            "accountCalls": 0,
            "commitSha256": code_sha,
            "configSha256": _digest(payloads["config.json"]),
            "dependencyLockSha256": lock_sha,
            "dockerfileSha256": docker_sha,
            "featureOrderSha256": _digest(canonical_json_bytes(list(FEATURE_ORDER))),
            "goldenOutputSha256": _digest(payloads["golden_output.json"]),
            "networkCalls": 0,
            "orderCalls": 0,
            "seed": 0,
            "splitSha256": _digest(b"P1_GLOBAL_TIME_SPLIT_60_20_20_V1"),
            "springCalls": 0,
            "trainingCodeSha256": code_sha,
        },
        "realTeamB": False,
        "runId": "run_synthetic_golden_exact31_v1",
    }


def _safetensors_bytes(symbols: list[str]) -> bytes:
    tensors: dict[str, dict[str, Any]] = {}
    offset = 0
    shapes = {
        "weight_ih_l0": [512, 9],
        "weight_hh_l0": [512, 128],
        "bias_ih_l0": [512],
        "bias_hh_l0": [512],
        "weight_ih_l1": [512, 128],
        "weight_hh_l1": [512, 128],
        "bias_ih_l1": [512],
        "bias_hh_l1": [512],
        "weight_ih_l2": [512, 128],
        "weight_hh_l2": [512, 128],
        "bias_ih_l2": [512],
        "bias_hh_l2": [512],
        "head.weight": [1, 128],
        "head.bias": [1],
    }
    for symbol in symbols:
        for suffix, shape in shapes.items():
            length = 4
            for dimension in shape:
                length *= dimension
            tensors[f"{symbol}.{suffix}"] = {
                "data_offsets": [offset, offset + length],
                "dtype": "F32",
                "shape": shape,
            }
            offset += length
    tensors["__metadata__"] = {
        "contractId": "p1-return-model-safetensors.v2",
        "featureOrder": ",".join(FEATURE_ORDER),
        "fixture": "SYNTHETIC_GOLDEN",
        "symbolCount": "31",
    }
    header = json.dumps(tensors, sort_keys=True, separators=(",", ":")).encode()
    padding = (-len(header)) % 8
    padded = header + b" " * padding
    return struct.pack("<Q", len(padded)) + padded + bytes(offset)


def _verify_safetensors_header(content: bytes) -> None:
    if len(content) < 16:
        raise P1OwnerAssetError("safetensors file is truncated")
    header_length = struct.unpack("<Q", content[:8])[0]
    if header_length < 2 or header_length > 8 * 1024 * 1024 or 8 + header_length > len(content):
        raise P1OwnerAssetError("safetensors header length is invalid")
    try:
        header = json.loads(content[8 : 8 + header_length])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P1OwnerAssetError("safetensors header is invalid") from error
    if not isinstance(header, dict):
        raise P1OwnerAssetError("safetensors header must be an object")
    metadata = header.get("__metadata__")
    if not isinstance(metadata, dict) or metadata.get("symbolCount") != "31":
        raise P1OwnerAssetError("safetensors metadata is not exact-31")
    tensor_names = [name for name in header if name != "__metadata__"]
    if len(tensor_names) != 31 * 14:
        raise P1OwnerAssetError("safetensors tensor inventory is incomplete")
    end = 0
    symbols: set[str] = set()
    for name in sorted(tensor_names):
        value = header[name]
        if not isinstance(value, dict) or value.get("dtype") != "F32":
            raise P1OwnerAssetError("safetensors tensor descriptor is invalid")
        offsets = value.get("data_offsets")
        if not isinstance(offsets, list) or len(offsets) != 2:
            raise P1OwnerAssetError("safetensors tensor offsets are invalid")
        if not all(isinstance(item, int) and item >= 0 for item in offsets):
            raise P1OwnerAssetError("safetensors tensor offsets are invalid")
        end = max(end, offsets[1])
        symbols.add(name.split(".", maxsplit=1)[0])
    if len(symbols) != 31 or 8 + header_length + end != len(content):
        raise P1OwnerAssetError("safetensors data extent is invalid")


def _verify_input_pack_tables(root: Path, manifest: dict[str, Any]) -> None:
    bars = pq.read_table(root / "daily_ohlcv.parquet")  # type: ignore[no-untyped-call]
    expected_columns = ["symbol", "sessionDate", "open", "high", "low", "raw_close", "volume"]
    if bars.column_names != expected_columns:
        raise P1OwnerAssetError("input OHLCV columns drifted")
    symbols = cast(list[str], cast(dict[str, Any], manifest["universe"])["symbols"])
    if sorted(set(cast(list[str], bars["symbol"].to_pylist()))) != sorted(symbols):
        raise P1OwnerAssetError("input OHLCV symbol set drifted")
    macro = pq.read_table(root / "ecos_macro.parquet")  # type: ignore[no-untyped-call]
    if len(set(cast(list[str], macro["seriesId"].to_pylist()))) > 2:
        raise P1OwnerAssetError("input macro series set drifted")


def _verify_golden_json_and_parquet(root: Path) -> None:
    config = _json_object(_read_regular(root, "config.json", MAX_MANIFEST_BYTES), "config")
    scaler = _json_object(_read_regular(root, "scaler.json", 4 * 1024 * 1024), "scaler")
    if config.get("featureOrder") != list(FEATURE_ORDER) or scaler.get("featureOrder") != list(
        FEATURE_ORDER
    ):
        raise P1OwnerAssetError("golden feature order drifted")
    if scaler.get("fitScope") != "TRAIN_ONLY" or len(cast(dict[str, Any], scaler["symbols"])) != 31:
        raise P1OwnerAssetError("golden scaler is not exact-31 train-only")
    for name in ("lstm_signals.parquet", "rule_baseline_signals.parquet"):
        table = pq.read_table(root / name)  # type: ignore[no-untyped-call]
        if table.num_rows != 31 or len(set(table["symbol"].to_pylist())) != 31:
            raise P1OwnerAssetError(f"golden signal file is not exact-31: {name}")
    equity = pq.read_table(root / "equity_log.parquet")  # type: ignore[no-untyped-call]
    if equity.num_rows != 3:
        raise P1OwnerAssetError("golden equity log must cover three scenarios")
    report = _read_regular(root, "model_report.md", MAX_MANIFEST_BYTES).decode()
    for heading in (
        "Data",
        "Model ABI",
        "Split",
        "Reproducibility",
        "Model quality",
        "Limitations",
    ):
        if f"## {heading}" not in report:
            raise P1OwnerAssetError(f"golden model report is missing section: {heading}")


def _publish_directory(
    *, output_root: Path, payloads: Mapping[str, bytes], manifest_name: str, manifest_bytes: bytes
) -> bool:
    parent = output_root.parent
    parent_fd = _open_owned_directory(parent)
    staging_name = f".{output_root.name}.{secrets.token_hex(8)}.tmp"
    staging = parent / staging_name
    staging_fd = -1
    try:
        try:
            output_metadata = os.stat(output_root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            output_metadata = None
        if output_metadata is not None:
            if not stat.S_ISDIR(output_metadata.st_mode):
                raise P1OwnerAssetError("existing output root is unsafe")
            existing = _read_regular(output_root, manifest_name, MAX_MANIFEST_BYTES)
            if existing == manifest_bytes:
                return True
            raise P1OwnerAssetError("existing output root has a different manifest")
        os.mkdir(staging_name, mode=0o700, dir_fd=parent_fd)
        staging_fd = os.open(
            staging_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        os.fchmod(staging_fd, 0o700)
        staging_metadata = os.fstat(staging_fd)
        if (
            staging_metadata.st_uid != os.getuid()
            or stat.S_IMODE(staging_metadata.st_mode) != 0o700
        ):
            raise P1OwnerAssetError("staging directory metadata is unsafe")
        for relative, payload in payloads.items():
            _write_new_file(staging_fd, relative, payload)
        _write_new_file(staging_fd, manifest_name, manifest_bytes)
        os.fsync(staging_fd)
        os.close(staging_fd)
        staging_fd = -1
        os.rename(
            staging_name,
            output_root.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    except Exception:
        try:
            if staging_fd >= 0:
                os.close(staging_fd)
        except OSError:
            pass
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        os.close(parent_fd)
    return False


def _write_new_file(root_fd: int, relative: str, content: bytes) -> None:
    if not relative or "/" in relative or "\\" in relative or relative in {".", ".."}:
        raise P1OwnerAssetError("output file name is invalid")
    file_fd = os.open(
        relative,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=root_fd,
    )
    try:
        os.fchmod(file_fd, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise P1OwnerAssetError("output write did not progress")
            view = view[written:]
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _read_regular(root: Path, relative: str, max_bytes: int) -> bytes:
    try:
        result = read_approved_regular_file(
            approved_root=root,
            relative_path=relative,
            max_bytes=max_bytes,
        )
    except RagSafeIoError as error:
        raise P1OwnerAssetError(f"unsafe or missing file: {relative}") from error
    return result.content


def _open_owned_directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts or path.anchor != "/":
        raise P1OwnerAssetError("output parent must be an absolute clean path")
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        metadata = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise P1OwnerAssetError("output parent ownership or mode is unsafe")
        return current_fd
    except OSError as error:
        os.close(current_fd)
        raise P1OwnerAssetError("output parent could not be opened safely") from error
    except Exception:
        os.close(current_fd)
        raise


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(  # type: ignore[no-untyped-call]
        table,
        sink,
        compression="zstd",
        row_group_size=8192,
        write_statistics=True,
    )
    return cast(bytes, sink.getvalue().to_pybytes())


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P1OwnerAssetError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise P1OwnerAssetError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _json_list(content: bytes, label: str) -> list[Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P1OwnerAssetError(f"{label} is invalid JSON") from error
    if not isinstance(value, list) or not value:
        raise P1OwnerAssetError(f"{label} must be a non-empty array")
    return value


def _validate_repository_schema(relative: str, payload: object) -> None:
    schema = _repository_json(relative)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda error: list(error.path),
    )
    if errors:
        raise P1OwnerAssetError(f"schema validation failed for {relative}: {errors[0].message}")


def _repository_root() -> Path:
    module = Path(__file__).resolve()
    for candidate in module.parents:
        if (candidate / "contracts").is_dir():
            return candidate
    raise P1OwnerAssetError("repository contract root is unavailable")


def _repository_json(relative: str) -> dict[str, Any]:
    path = _repository_root() / relative
    if path.is_symlink() or not path.is_file():
        raise P1OwnerAssetError(f"repository contract is unavailable: {relative}")
    return _json_object(path.read_bytes(), relative)


def _read_repository_json_bytes(relative: str) -> bytes:
    path = _repository_root() / relative
    if path.is_symlink() or not path.is_file():
        raise P1OwnerAssetError(f"repository fixture is unavailable: {relative}")
    content = path.read_bytes()
    _json_object(content, relative)
    return content


def _repository_file_sha(relative: str) -> str:
    path = _repository_root() / relative
    if path.is_symlink() or not path.is_file():
        raise P1OwnerAssetError(f"repository file is unavailable: {relative}")
    return _digest(path.read_bytes())


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise P1OwnerAssetError(f"{label} must be lowercase SHA-256")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise P1OwnerAssetError(f"{label} must be non-empty text")
    return value


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 Owner provider-free input/golden asset tool")
    subcommands = parser.add_subparsers(dest="command", required=True)
    input_parser = subcommands.add_parser("input-pack")
    input_parser.add_argument("--archive-root", type=Path, required=True)
    input_parser.add_argument("--archive-manifest-sha256", required=True)
    input_parser.add_argument("--output-root", type=Path, required=True)
    input_parser.add_argument("--zip-output", type=Path)
    golden_parser = subcommands.add_parser("golden")
    golden_parser.add_argument("--input-pack-manifest", type=Path, required=True)
    golden_parser.add_argument("--output-root", type=Path, required=True)
    verify_input_parser = subcommands.add_parser("verify-input-pack")
    verify_input_parser.add_argument("--root", type=Path, required=True)
    verify_golden_parser = subcommands.add_parser("verify-golden")
    verify_golden_parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "input-pack":
            result = build_input_pack(
                archive_root=args.archive_root,
                expected_archive_manifest_sha256=args.archive_manifest_sha256,
                output_root=args.output_root,
            )
            payload = {
                "fileCount": result.file_count,
                "manifestSha256": result.manifest_sha256,
                "noOp": result.no_op,
                "providerCalls": 0,
                "status": "INPUT_PACK_VERIFIED",
            }
            if args.zip_output is not None:
                payload["zipPath"] = args.zip_output.name
                payload["zipSha256"] = write_input_pack_zip(args.output_root, args.zip_output)
        elif args.command == "golden":
            result = build_golden_bundle(
                input_pack_manifest=args.input_pack_manifest,
                output_root=args.output_root,
            )
            payload = {
                "fileCount": result.file_count,
                "manifestSha256": result.manifest_sha256,
                "noOp": result.no_op,
                "providerCalls": 0,
                "realTeamB": False,
                "status": "SYNTHETIC_GOLDEN_VERIFIED",
            }
        elif args.command == "verify-input-pack":
            payload = {
                "manifestSha256": verify_input_pack(args.root),
                "providerCalls": 0,
                "status": "INPUT_PACK_VERIFIED",
            }
        else:
            payload = {
                "manifestSha256": verify_golden_bundle(args.root),
                "providerCalls": 0,
                "realTeamB": False,
                "status": "SYNTHETIC_GOLDEN_VERIFIED",
            }
    except (MarketDataArchiveError, P1OwnerAssetError, OSError) as error:
        print(f"P1_OWNER_ASSET_FAILED: {error}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(payload).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
