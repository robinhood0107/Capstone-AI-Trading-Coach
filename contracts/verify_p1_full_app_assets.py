from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
SHA256_LENGTH = 64
RETURN_ARTIFACTS = frozenset(
    {
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
    }
)
RETURN_STRATEGIES = frozenset({"BASELINE", "GUIDE", "STRICT"})


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and value != "0" * SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_json(path: Path, code: str, errors: list[str]) -> Mapping[str, Any] | None:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_MANIFEST_BYTES:
            raise OSError
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(code)
        return None
    if not isinstance(payload, Mapping):
        errors.append(code)
        return None
    return payload


def _safe_relative_path(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _regular_descendant(root: Path, relative: PurePosixPath) -> Path | None:
    try:
        root_resolved = root.resolve(strict=True)
        candidate = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root_resolved)
        metadata = candidate.lstat()
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    return resolved


def _safe_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return not path.is_symlink() and stat.S_ISDIR(metadata.st_mode)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_sha256(files: object, size_key: str) -> str | None:
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
        return None
    normalized: list[dict[str, object]] = []
    for item in files:
        if not isinstance(item, Mapping):
            return None
        path = item.get("path")
        size = item.get(size_key)
        sha256 = item.get("sha256")
        if not isinstance(path, str) or not isinstance(size, int) or isinstance(size, bool) or not isinstance(sha256, str):
            return None
        normalized.append({"path": path, "bytes": size, "sha256": sha256})
    normalized.sort(key=lambda item: str(item["path"]))
    encoded = (json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_file_inventory(
    root: Path,
    files: object,
    *,
    size_key: str,
    code_prefix: str,
    errors: list[str],
) -> tuple[set[str], int]:
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)) or not files:
        errors.append(f"{code_prefix}_FILES")
        return set(), 0
    observed: set[str] = set()
    total_bytes = 0
    for item in files:
        if not isinstance(item, Mapping):
            errors.append(f"{code_prefix}_FILE_RECORD")
            continue
        relative = _safe_relative_path(item.get("path"))
        if relative is None:
            errors.append(f"{code_prefix}_FILE_PATH")
            continue
        path_text = relative.as_posix()
        if path_text in observed:
            errors.append(f"{code_prefix}_FILE_DUPLICATE:{path_text}")
            continue
        observed.add(path_text)
        expected_size = item.get(size_key)
        expected_hash = item.get("sha256")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 1 or not _is_sha256(expected_hash):
            errors.append(f"{code_prefix}_FILE_METADATA:{path_text}")
            continue
        candidate = _regular_descendant(root, relative)
        if candidate is None:
            errors.append(f"{code_prefix}_FILE_BOUNDARY:{path_text}")
            continue
        if candidate.stat().st_size != expected_size or _sha256(candidate) != expected_hash:
            errors.append(f"{code_prefix}_FILE_INTEGRITY:{path_text}")
            continue
        total_bytes += expected_size
    return observed, total_bytes


def validate_model_asset(component: str, model_root: Path, contract: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if contract.get("inventoryStatus") != "MATERIALIZED":
        return [f"MODEL:{component}:INVENTORY_NOT_MATERIALIZED"]
    root = model_root / component
    if not _safe_directory(model_root) or not _safe_directory(root):
        return [f"MODEL:{component}:ROOT_BOUNDARY"]
    manifest = _load_json(root / "model-asset-manifest.v1.json", f"MODEL_MANIFEST:{component}", errors)
    if manifest is None:
        return errors
    prefix = f"MODEL:{component}"
    if manifest.get("schemaVersion") != "model-artifact-manifest/v1":
        errors.append(f"{prefix}:SCHEMA")
    for field in ("repository", "revision"):
        if manifest.get(field) != contract.get(field):
            errors.append(f"{prefix}:{field.upper()}")
    license_record = manifest.get("license")
    locator = license_record.get("locator") if isinstance(license_record, Mapping) else None
    if (
        not isinstance(license_record, Mapping)
        or license_record.get("spdxId") != contract.get("licenseSpdxId")
        or not isinstance(locator, str)
        or not locator.startswith("https://")
        or str(contract.get("revision")) not in locator
    ):
        errors.append(f"{prefix}:LICENSE")
    observed, total_bytes = _validate_file_inventory(
        root,
        manifest.get("files"),
        size_key="bytes",
        code_prefix=prefix,
        errors=errors,
    )
    if contract.get("fileCount") != len(observed) or manifest.get("fileCount") != len(observed):
        errors.append(f"{prefix}:FILE_COUNT")
    if contract.get("totalBytes") != total_bytes or manifest.get("totalBytes") != total_bytes:
        errors.append(f"{prefix}:TOTAL_BYTES")
    if _inventory_sha256(manifest.get("files"), "bytes") != contract.get("inventorySha256"):
        errors.append(f"{prefix}:INVENTORY_HASH")
    if component == "bge-m3":
        graph = manifest.get("graphContract")
        if not isinstance(graph, Mapping) or graph.get("outputDimension") != 1024:
            errors.append(f"{prefix}:OUTPUT_DIMENSION")
    if component == "paddleocr-vl-1.6":
        if (
            manifest.get("qualityCandidate") != contract.get("qualityCandidate")
            or manifest.get("qualityEvidenceSha256") != contract.get("qualityEvidenceSha256")
        ):
            errors.append(f"{prefix}:QUALITY_EVIDENCE")
    return errors


def validate_return_manifest(path: Path) -> list[str]:
    errors: list[str] = []
    if not _safe_directory(path.parent):
        return ["RETURN:ROOT_BOUNDARY"]
    manifest = _load_json(path, "RETURN_MANIFEST", errors)
    if manifest is None:
        return errors
    if manifest.get("contractId") != "p1-return-engine-artifact-manifest.v1":
        errors.append("RETURN:CONTRACT")
    if manifest.get("evidenceMode") != "REAL_TEAM_B":
        errors.append("RETURN:EVIDENCE_MODE")
    producer = manifest.get("producer")
    if not isinstance(producer, Mapping):
        errors.append("RETURN:PRODUCER")
    else:
        for field in (
            "commitSha256",
            "dependencyLockSha256",
            "dockerfileSha256",
            "sourceSnapshotSha256",
            "trainingCodeSha256",
            "featureOrderSha256",
            "splitSha256",
            "configSha256",
            "goldenOutputSha256",
        ):
            if not _is_sha256(producer.get(field)):
                errors.append(f"RETURN:PRODUCER:{field}")
        if not isinstance(producer.get("seed"), int) or isinstance(producer.get("seed"), bool):
            errors.append("RETURN:PRODUCER:seed")
        if not isinstance(producer.get("windowSessions"), int) or producer.get("windowSessions", 0) < 2:
            errors.append("RETURN:PRODUCER:windowSessions")
    forecast = manifest.get("forecast")
    if not isinstance(forecast, Mapping):
        errors.append("RETURN:FORECAST")
    else:
        current = forecast.get("currentClose")
        predicted = forecast.get("forecastClose")
        expected = forecast.get("expectedReturn")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in (current, predicted, expected)) or current <= 0:
            errors.append("RETURN:FORECAST:VALUES")
        elif not math.isclose(expected, predicted / current - 1.0, rel_tol=1e-12, abs_tol=1e-12):
            errors.append("RETURN:FORECAST:FORMULA")
        session = forecast.get("nextXkrxSession")
        try:
            parsed_session = dt.date.fromisoformat(session) if isinstance(session, str) else None
        except ValueError:
            parsed_session = None
        if parsed_session is None or parsed_session.isoformat() != session:
            errors.append("RETURN:FORECAST:XKRX_SESSION")
    strategies = manifest.get("strategies")
    if not isinstance(strategies, Mapping) or set(strategies) != RETURN_STRATEGIES:
        errors.append("RETURN:STRATEGY_SET")
    else:
        for strategy, policy in strategies.items():
            if not isinstance(policy, Mapping) or set(policy) != {
                "transactionCostBps",
                "taxBps",
                "slippageBps",
            }:
                errors.append(f"RETURN:STRATEGY:{strategy}")
                continue
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
                for value in policy.values()
            ):
                errors.append(f"RETURN:STRATEGY:{strategy}")
    observed, _ = _validate_file_inventory(
        path.parent,
        manifest.get("artifacts"),
        size_key="sizeBytes",
        code_prefix="RETURN",
        errors=errors,
    )
    basenames = {PurePosixPath(item).name for item in observed}
    if basenames != RETURN_ARTIFACTS or len(observed) != len(RETURN_ARTIFACTS):
        errors.append("RETURN:ARTIFACT_SET")
    return errors


def verify_assets(model_root: Path, return_manifest: Path, repository_root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    catalog = _load_json(
        repository_root / "contracts/catalogs/p1-full-app-release-contract.v2.json",
        "RELEASE_CATALOG",
        errors,
    )
    if catalog is None:
        return errors
    model_contracts = catalog.get("modelAssets")
    if not isinstance(model_contracts, Mapping):
        return ["RELEASE_CATALOG_MODELS"]
    for component in ("bge-m3", "paddleocr-vl-1.6"):
        contract = model_contracts.get(component)
        if not isinstance(contract, Mapping):
            errors.append(f"RELEASE_CATALOG_MODEL:{component}")
            continue
        errors.extend(validate_model_asset(component, model_root, contract))
    errors.extend(validate_return_manifest(return_manifest))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify P1 full-app local model and Team B artifact assets.")
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--return-manifest", required=True, type=Path)
    parser.add_argument("--repository-root", default=ROOT, type=Path)
    arguments = parser.parse_args()
    errors = verify_assets(
        arguments.model_root.absolute(),
        arguments.return_manifest.absolute(),
        arguments.repository_root.absolute(),
    )
    if errors:
        for error in errors:
            print(f"CAPSTONE_FULL_ASSET_ERROR={error}", file=sys.stderr)
        return 1
    print("CAPSTONE_FULL_ASSETS=INTEGRITY_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
