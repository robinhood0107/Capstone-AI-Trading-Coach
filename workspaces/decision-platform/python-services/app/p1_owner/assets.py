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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
from jsonschema import Draft202012Validator, FormatChecker

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.model_shape import classify_signal, resolve_shape
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
GOLDEN_MANIFEST = "p1-return-engine-manifest.v3.json"
# SYNTHETIC_GOLDEN 번들의 학습 설정. 실제 Team B 번들은 자기 config.json으로 형상을 선언한다.
_GOLDEN_CONFIG: dict[str, Any] = {
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
CALENDAR_CATALOG = "contracts/catalogs/s5-bootstrap-calendar-recovery-lock.v1.json"
MANIFEST_SCHEMA = "contracts/schemas/p1-return-engine-artifact-manifest.v3.schema.json"
SCENARIO_POLICY = "contracts/examples/p1-scenario-replay-policy.v1.valid.json"
MAX_MANIFEST_BYTES = 1_048_576
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


def build_golden_bundle(
    *, universe_catalog: Path, session_date: str, output_root: Path
) -> AssetBuildResult:
    """Create the exact-ten synthetic bundle with the real wire shape and zero authority.

    입력이 봉인 ZIP 에서 커밋된 유니버스 카탈로그로 바뀌었다. 수집이 yfinance 런타임이라
    사전 봉인할 입력이 없고, 골든 번들에 필요한 것은 종목 집합과 세션 날짜뿐이다.
    inputPackSha256 은 실제로 읽은 카탈로그 바이트의 정직한 해시로 채운다 —
    기대값을 외부에서 주입받지 않는다.
    """

    catalog_bytes = _read_regular(
        universe_catalog.parent, universe_catalog.name, MAX_MANIFEST_BYTES
    )
    catalog = _json_object(catalog_bytes, "universe catalog")
    entries = catalog.get("symbols")
    if not isinstance(entries, list):
        raise P1OwnerAssetError("universe catalog symbols must be a list")
    symbols = [
        _text(cast(dict[str, Any], item).get("symbol"), "universe symbol")
        for item in entries
        if isinstance(item, dict)
    ]
    if len(symbols) != 31 or len(set(symbols)) != 31 or symbols.count("132030") != 1:
        raise P1OwnerAssetError("golden bundle requires the exact-31 input universe")
    input_manifest_sha256 = _digest(catalog_bytes)
    session_date = _golden_session_date(session_date)
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


def _golden_session_date(value: str) -> str:
    """골든 번들은 회귀 기준선이므로 세션 날짜를 호출자가 명시한다. 오늘 날짜로 흔들리면
    두 번 실행 byte determinism 이 깨진다."""

    text = str(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise P1OwnerAssetError("golden session date must be an ISO date") from None
    if parsed.isoformat() != text:
        raise P1OwnerAssetError("golden session date must be an ISO date")
    return text


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


def _golden_payloads(
    *, symbols: list[str], session_date: str, input_pack_sha256: str
) -> dict[str, bytes]:
    lstm_rows: list[dict[str, Any]] = []
    rule_rows: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols):
        current = 10_000 + index * 10
        # ±200원은 최저가 10,300원에서도 1.9%로 signal deadband(±0.5%) 밖이다.
        forecast = current + (200 if index % 3 == 0 else -200 if index % 3 == 1 else 0)
        expected = (forecast / current) - 1
        signal = classify_signal(expected)
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
    config = dict(_GOLDEN_CONFIG)
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
    # 골든 번들은 계약 기준 형상(hidden 128 / 3층)을 유지한다. 일반화 경로의 회귀 기준선이다.
    model_shape = resolve_shape(_GOLDEN_CONFIG, FEATURE_ORDER)
    shapes = {suffix: list(model_shape.shapes[suffix]) for suffix in model_shape.suffixes}
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
    if len(tensor_names) != resolve_shape(_GOLDEN_CONFIG, FEATURE_ORDER).tensor_count(31):
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
    golden_parser = subcommands.add_parser("golden")
    golden_parser.add_argument("--universe-catalog", type=Path, required=True)
    golden_parser.add_argument("--session-date", required=True)
    golden_parser.add_argument("--output-root", type=Path, required=True)
    verify_golden_parser = subcommands.add_parser("verify-golden")
    verify_golden_parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "golden":
            result = build_golden_bundle(
                universe_catalog=args.universe_catalog,
                session_date=args.session_date,
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
        else:
            payload = {
                "manifestSha256": verify_golden_bundle(args.root),
                "providerCalls": 0,
                "realTeamB": False,
                "status": "SYNTHETIC_GOLDEN_VERIFIED",
            }
    except (P1OwnerAssetError, OSError) as error:
        print(f"P1_OWNER_ASSET_FAILED: {error}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(payload).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
