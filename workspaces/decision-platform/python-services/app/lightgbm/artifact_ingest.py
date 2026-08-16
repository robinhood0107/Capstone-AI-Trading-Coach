"""S5.5 server-defined artifact bundle의 bounded safe ingest validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
from typing import Any, Mapping, cast

import lightgbm as lgb
import pyarrow.parquet as pq

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.fake_artifacts import (
    SIGNAL_ROW_SCHEMA,
    signal_row_payload_sha256,
    signal_row_provenance_sha256,
)
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file


FILE_LIMITS = {
    "manifest.json": 1 * 1024 * 1024,
    "lightgbm-signal.json": 1 * 1024 * 1024,
    "calibrator.json": 1 * 1024 * 1024,
    "report.json": 16 * 1024 * 1024,
    "model.txt": 64 * 1024 * 1024,
    "signals.parquet": 256 * 1024 * 1024,
}
MAX_ROWS = 250_000
MAX_COLUMNS = 128
MAX_DECODED_BYTES = 256 * 1024 * 1024
JSON_LIMITS = BoundedJsonLimits(1 * 1024 * 1024, 16, 128, 64, 4096, 16_384, 64)
REPORT_JSON_LIMITS = BoundedJsonLimits(16 * 1024 * 1024, 16, 128, 64, 4096, 16_384, 64)


@dataclass(frozen=True)
class ValidatedSignalBundle:
    """모든 file/hash/schema 검증이 끝나 DB transaction에 넘길 수 있는 immutable projection."""

    manifest_sha256: str
    artifact_sha256: str
    fixture: bool
    provenance_class: str
    rows: tuple[dict[str, object], ...]
    lightgbm_signal: dict[str, object]


def validate_signal_bundle(*, approved_root: Path) -> ValidatedSignalBundle:
    """사용자 식별자를 경로에 넣지 않고 server-defined 네 파일만 descriptor로 읽어 검증한다."""

    manifest_bytes = _read(approved_root, "manifest.json")
    manifest = _parse_object(manifest_bytes, "manifest")
    if set(manifest) != {"manifestVersion", "schemaVersion", "fixture", "provenanceClass", "files"}:
        raise LightGbmContractError("signal artifact manifest fields drifted")
    if (
        manifest["manifestVersion"] != "s5-signal-fake-bundle-v1"
        or manifest["schemaVersion"] != "signal-v2-runtime-v1"
        or manifest["fixture"] is not True
        or manifest["provenanceClass"] != "FAKE_CONTRACT"
    ):
        raise LightGbmContractError("signal artifact manifest version or provenance is invalid")
    file_entries = manifest["files"]
    if not isinstance(file_entries, list) or len(file_entries) != 5:
        raise LightGbmContractError("signal artifact file inventory is invalid")
    inventory: dict[str, tuple[str, int]] = {}
    for entry in file_entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "sha256", "size"}:
            raise LightGbmContractError("signal artifact file entry is invalid")
        name, digest, size = entry["name"], entry["sha256"], entry["size"]
        if name not in FILE_LIMITS or name == "manifest.json" or name in inventory:
            raise LightGbmContractError("signal artifact filename is not server-defined")
        if (
            not _is_sha256(digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or size > FILE_LIMITS[name]
        ):
            raise LightGbmContractError("signal artifact file binding is invalid")
        inventory[name] = (digest, size)
    if set(inventory) != {
        "signals.parquet",
        "model.txt",
        "calibrator.json",
        "report.json",
        "lightgbm-signal.json",
    }:
        raise LightGbmContractError("signal artifact exact file set is missing")

    contents: dict[str, bytes] = {}
    for name, (expected_hash, expected_size) in inventory.items():
        content = _read(approved_root, name)
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            raise LightGbmContractError("signal artifact file hash or size mismatch")
        contents[name] = content

    _validate_text_model(contents["model.txt"])
    _validate_calibrator(contents["calibrator.json"])
    _validate_report(contents["report.json"])
    lightgbm_signal = _validate_lightgbm_signal(contents["lightgbm-signal.json"])
    try:
        rows = _validate_signal_parquet(contents["signals.parquet"])
    except LightGbmContractError:
        raise
    except Exception as error:
        raise LightGbmContractError("signal Parquet is invalid") from error
    lightgbm_row = next((row for row in rows if row["producer"] == "LIGHTGBM"), None)
    if lightgbm_row is None or (
        lightgbm_signal["modelSha256"] != hashlib.sha256(contents["model.txt"]).hexdigest()
        or lightgbm_signal["reportSha256"] != hashlib.sha256(contents["report.json"]).hexdigest()
        or lightgbm_signal["payloadSha256"] != lightgbm_row["payloadSha256"]
        or lightgbm_signal["provenanceSha256"] != lightgbm_row["provenanceSha256"]
        or lightgbm_signal["symbol"] != lightgbm_row["symbol"]
        or lightgbm_signal["sessionDate"] != cast(date, lightgbm_row["sessionDate"]).isoformat()
        or lightgbm_signal["evaluationId"] != lightgbm_row["evaluationId"]
        or lightgbm_signal["timeframe"] != lightgbm_row["timeframe"]
        or lightgbm_signal["modelVersion"] != f"lgbm-v1-{lightgbm_signal['modelSha256'][:12]}"
        or lightgbm_signal["modelReportId"] != lightgbm_row["modelReportId"]
        or lightgbm_signal["datasetSha256"]
        != hashlib.sha256(b"s5-fake-contract-dataset-unavailable-v1").hexdigest()
    ):
        raise LightGbmContractError("LightGBM signal file/provenance binding is invalid")
    artifact_preimage = b"s5-signal-artifact-bundle-v1\x00" + b"".join(
        name.encode() + b"\x00" + bytes.fromhex(inventory[name][0]) for name in sorted(inventory)
    )
    return ValidatedSignalBundle(
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        artifact_sha256=hashlib.sha256(artifact_preimage).hexdigest(),
        fixture=True,
        provenance_class="FAKE_CONTRACT",
        rows=rows,
        lightgbm_signal=lightgbm_signal,
    )


def _read(root: Path, name: str) -> bytes:
    try:
        return read_approved_regular_file(
            approved_root=root,
            relative_path=name,
            max_bytes=FILE_LIMITS[name],
        ).content
    except (RagSafeIoError, OSError, KeyError) as error:
        raise LightGbmContractError(
            "signal artifact path, symlink, or size boundary is invalid"
        ) from error


def _parse_object(
    content: bytes,
    label: str,
    *,
    limits: BoundedJsonLimits = JSON_LIMITS,
) -> dict[str, Any]:
    try:
        value = parse_bounded_json_bytes(content, limits=limits)
    except BoundedJsonError as error:
        raise LightGbmContractError(f"{label} JSON is invalid") from error
    if not isinstance(value, dict):
        raise LightGbmContractError(f"{label} must be an object")
    return value


def _validate_text_model(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeError as error:
        raise LightGbmContractError("LightGBM model text is not UTF-8") from error
    if "objective=multiclass" not in text or "num_class=3" not in text:
        raise LightGbmContractError("LightGBM text model contract is invalid")
    try:
        lgb.Booster(model_str=text)
    except Exception as error:
        raise LightGbmContractError("LightGBM text model cannot be loaded safely") from error


def _validate_calibrator(content: bytes) -> None:
    value = _parse_object(content, "calibrator")
    if set(value) != {"calibratorVersion", "classOrder", "fixture", "parameters"}:
        raise LightGbmContractError("calibrator contains an unknown field")
    if (
        value["calibratorVersion"] != "ovr-platt-v1"
        or value["classOrder"] != ["SELL", "HOLD", "BUY"]
        or value["fixture"] is not True
        or not isinstance(value["parameters"], list)
        or len(value["parameters"]) != 3
    ):
        raise LightGbmContractError("calibrator contract is invalid")
    for index, parameter in enumerate(value["parameters"]):
        if (
            not isinstance(parameter, dict)
            or set(parameter) != {"a", "b", "classIndex"}
            or parameter["classIndex"] != index
            or not _finite_number(parameter["a"])
            or not _finite_number(parameter["b"])
        ):
            raise LightGbmContractError("calibrator numeric parameters are invalid")


def _validate_report(content: bytes) -> None:
    value = _parse_object(content, "report", limits=REPORT_JSON_LIMITS)
    if set(value) != {
        "fixture",
        "performanceEvidence",
        "provenanceClass",
        "reportVersion",
        "rowCount",
        "status",
    }:
        raise LightGbmContractError("report contains an unknown field")
    if value != {
        "fixture": True,
        "performanceEvidence": False,
        "provenanceClass": "FAKE_CONTRACT",
        "reportVersion": "s5-fake-contract-report-v1",
        "rowCount": 4,
        "status": "DATASET_UNAVAILABLE",
    }:
        raise LightGbmContractError("fake report must not claim performance evidence")


def _validate_lightgbm_signal(content: bytes) -> dict[str, object]:
    value = _parse_object(content, "LightGBM signal")
    expected = {
        "artifactVersion",
        "schemaVersion",
        "producer",
        "sourceWorkspace",
        "symbol",
        "sessionDate",
        "evaluationId",
        "timeframe",
        "modelVersion",
        "modelReportId",
        "fixture",
        "provenanceClass",
        "datasetSha256",
        "modelSha256",
        "reportSha256",
        "payloadSha256",
        "provenanceSha256",
        "status",
        "reason",
    }
    if set(value) != expected:
        raise LightGbmContractError("LightGBM signal artifact contains an unknown field")
    if (
        value["artifactVersion"] != "lightgbm-signal-artifact-v1"
        or value["schemaVersion"] != "signal-v2-runtime-v1"
        or value["producer"] != "LIGHTGBM"
        or value["sourceWorkspace"] != "decision-platform"
        or value["fixture"] is not True
        or value["provenanceClass"] != "FAKE_CONTRACT"
        or value["status"] != "ABSTAIN"
        or value["reason"] != "MISSING_EVIDENCE"
    ):
        raise LightGbmContractError("LightGBM fake signal union or provenance is invalid")
    for field in (
        "datasetSha256",
        "modelSha256",
        "reportSha256",
        "payloadSha256",
        "provenanceSha256",
    ):
        if not _is_sha256(value[field]):
            raise LightGbmContractError("LightGBM signal digest is invalid")
    return value


def _validate_signal_parquet(content: bytes) -> tuple[dict[str, object], ...]:
    parquet = pq.ParquetFile(  # type: ignore[no-untyped-call]
        BytesIO(content),
        thrift_string_size_limit=1 * 1024 * 1024,
        thrift_container_size_limit=300_000,
        page_checksum_verification=True,
    )
    metadata = parquet.metadata
    if metadata.num_rows > MAX_ROWS or metadata.num_columns > MAX_COLUMNS:
        raise LightGbmContractError("signal Parquet row or column cap exceeded")
    if parquet.schema_arrow != SIGNAL_ROW_SCHEMA:
        raise LightGbmContractError("signal Parquet schema, type, or unknown column is invalid")
    declared = sum(
        metadata.row_group(group).column(column).total_uncompressed_size
        for group in range(metadata.num_row_groups)
        for column in range(metadata.num_columns)
    )
    if declared > MAX_DECODED_BYTES:
        raise LightGbmContractError("signal Parquet declared decoded size exceeded")
    rows: list[dict[str, object]] = []
    decoded = 0
    for batch in parquet.iter_batches(batch_size=8_192, use_threads=False):  # type: ignore[no-untyped-call]
        decoded += batch.nbytes
        if decoded > MAX_DECODED_BYTES or len(rows) + batch.num_rows > MAX_ROWS:
            raise LightGbmContractError("signal Parquet actual decoded bound exceeded")
        rows.extend(batch.to_pylist())
    keys: set[tuple[str, str, str]] = set()
    ordered_keys: list[tuple[str, str, str]] = []
    for row in rows:
        _validate_row(row)
        key = (str(row["producer"]), str(row["symbol"]), str(row["evaluationId"]))
        if key in keys:
            raise LightGbmContractError("signal Parquet logical identity is duplicated")
        keys.add(key)
        ordered_keys.append((str(row["symbol"]), str(row["producer"]), str(row["evaluationId"])))
    if ordered_keys != sorted(ordered_keys):
        raise LightGbmContractError("signal Parquet rows are not canonically sorted")
    return tuple(rows)


def _validate_row(row: Mapping[str, object]) -> None:
    producer = row["producer"]
    workspace = row["sourceWorkspace"]
    expected_workspace = (
        "return-engine" if producer in {"RULE_BASELINE", "LSTM"} else "decision-platform"
    )
    if (
        producer not in {"RULE_BASELINE", "LSTM", "LIGHTGBM", "HMM"}
        or workspace != expected_workspace
    ):
        raise LightGbmContractError("signal producer/workspace mapping is invalid")
    if row["fixture"] is not True or row["provenanceClass"] != "FAKE_CONTRACT":
        raise LightGbmContractError("fake signal row provenance is invalid")
    if (
        not isinstance(row["symbol"], str)
        or re.fullmatch(r"[0-9A-Z._:-]{1,20}", row["symbol"]) is None
        or not isinstance(row["sessionDate"], date)
        or row["timeframe"] != "1d"
        or not isinstance(row["evaluationId"], str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", row["evaluationId"]) is None
    ):
        raise LightGbmContractError("signal row identity is invalid")
    if row["status"] == "ABSTAIN":
        if row["reason"] not in {
            "MISSING_EVIDENCE",
            "PRODUCER_FAILED",
            "STALE_EVIDENCE",
            "ARTIFACT_DRIFT",
            "CALIBRATION_FAILED",
            "UNIDENTIFIABLE_OUTPUT",
        }:
            raise LightGbmContractError("signal ABSTAIN reason is invalid")
        if any(
            row[field] is not None for field in ("asOf", "signal", "confidence", "predictedReturn")
        ):
            raise LightGbmContractError("signal ABSTAIN row fabricates prediction fields")
    elif row["status"] == "AVAILABLE":
        if (
            row["asOf"] is None
            or row["signal"] not in {"BUY", "HOLD", "SELL"}
            or row["confidence"] is None
            or row["reason"] is not None
            or not _finite_number(row["confidence"])
            or not 0.0 <= float(cast(float, row["confidence"])) <= 1.0
            or (row["predictedReturn"] is not None and not _finite_number(row["predictedReturn"]))
        ):
            raise LightGbmContractError("signal AVAILABLE row is incomplete")
    else:
        raise LightGbmContractError("signal row status is invalid")
    summary = row["featureSummary"]
    if (
        not isinstance(summary, list)
        or len(summary) > 32
        or not all(isinstance(item, str) and 0 < len(item) <= 256 for item in summary)
    ):
        raise LightGbmContractError("signal featureSummary is invalid")
    if (
        not _is_sha256(row["payloadSha256"])
        or not _is_sha256(row["provenanceSha256"])
        or row["payloadSha256"] != signal_row_payload_sha256(dict(row))
        or row["provenanceSha256"] != signal_row_provenance_sha256(dict(row))
    ):
        raise LightGbmContractError("signal row digest is invalid")


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
