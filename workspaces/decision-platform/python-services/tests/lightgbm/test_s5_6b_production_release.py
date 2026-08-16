from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.calibration import fit_ovr_platt
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.production_db import Connection, activate_release_and_batch, stage_release_and_batch
from app.lightgbm.production_release import (
    QUALIFICATION_RECEIPT,
    RELEASE_FILES,
    RELEASE_MANIFEST,
    _publish_sealed_release,
    _read_qualification_seal,
    _signal_parquet,
    _write_qualification_reservation,
    _write_qualification_seal,
    validate_production_model_release,
    validate_production_signal_batch,
)
from app.lightgbm.training import exact_grid, fit_lightgbm_reproducible, raw_margins


def _private_root(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _write(path: Path, name: str, content: bytes) -> None:
    target = path / name
    target.write_bytes(content)
    target.chmod(0o600)


def _release(root: Path) -> tuple[str, dict[str, object]]:
    random = np.random.default_rng(20260729)
    x_fit = random.normal(size=(300, 17)).astype(np.float32)
    y_fit = np.tile(np.asarray([0, 1, 2]), 100)
    x_early = random.normal(size=(90, 17)).astype(np.float32)
    y_early = np.tile(np.asarray([0, 1, 2]), 30)
    model = fit_lightgbm_reproducible(x_fit, y_fit, x_early, y_early, exact_grid()[0])
    calibrator = fit_ovr_platt(raw_margins(model, x_early), y_early)
    report_without_id = {
        "reportVersion": "s5-production-qualification-v1",
        "finalTest": {"accessCount": 1, "passed": True},
    }
    report_id = f"mrp-{hashlib.sha256(canonical_json_bytes(report_without_id)).hexdigest()[:12]}"
    report = canonical_json_bytes({**report_without_id, "modelReportId": report_id})
    files = {
        "model.txt": model.model_text,
        "calibrator.json": calibrator.canonical_bytes(),
        "report.json": report,
        "gain-importance.json": canonical_json_bytes({"gain": [1.0]}),
        "contribution-report.json": canonical_json_bytes({"rows": []}),
    }
    bindings = {
        "featureManifestSha256": "a" * 64,
        "trainingDatasetSha256": "b" * 64,
        "modelSha256": hashlib.sha256(files["model.txt"]).hexdigest(),
        "calibratorSha256": hashlib.sha256(files["calibrator.json"]).hexdigest(),
        "reportSha256": hashlib.sha256(files["report.json"]).hexdigest(),
        "gainImportanceSha256": hashlib.sha256(files["gain-importance.json"]).hexdigest(),
        "contributionReportSha256": hashlib.sha256(files["contribution-report.json"]).hexdigest(),
    }
    files["qualification.json"] = canonical_json_bytes(
        {
            "qualificationVersion": "s5-production-qualification-receipt-v1",
            **bindings,
            "finalTestAccessCount": 1,
            "selectedGridIndex": 0,
        }
    )
    preimage: dict[str, object] = {
        "releaseVersion": "s5-model-release-v1",
        "modelVersion": f"lgbm-v1-{model.model_sha256[:12]}",
        "modelReportId": report_id,
        "featureManifestSha256": "a" * 64,
        "sourceBundleSetSha256": "c" * 64,
        "sourcePolicySetSha256": "d" * 64,
        "trainingDatasetSha256": "b" * 64,
        "codeHead": "e" * 40,
        "codeTree": "f" * 40,
        "uvLockSha256": "1" * 64,
        "calendarName": "XKRX",
        "calendarVersion": "4.13.2",
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "fixture": False,
        "provenanceClass": "PRODUCTION",
        "status": "QUALIFIED",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    semantic = hashlib.sha256(b"s5-model-release-v1\x00" + canonical_json_bytes(preimage)).hexdigest()
    manifest = {
        **preimage,
        "modelReleaseId": f"lgr-{semantic[:12]}",
        "semanticSha256": semantic,
    }
    for name, content in files.items():
        _write(root, name, content)
    manifest_bytes = canonical_json_bytes(manifest)
    _write(root, "release.json", manifest_bytes)
    return hashlib.sha256(manifest_bytes).hexdigest(), manifest


def _batch(
    root: Path,
    model_release_id: str,
    model_version: str,
    report_id: str,
    *,
    session_date: str = "2026-08-14",
    as_of: str = "2026-08-17T23:10:00Z",
) -> str:
    symbols = tuple(sorted([f"{value:06d}" for value in range(1, 30)] + ["005930", "132030"]))
    rows = [
        {
            "symbol": symbol,
            "status": "AVAILABLE",
            "asOf": as_of,
            "signal": "HOLD",
            "confidence": 0.5,
            "modelVersion": model_version,
            "modelReportId": report_id,
        }
        for symbol in symbols
    ]
    parquet = _signal_parquet(rows)
    membership = hashlib.sha256(
        b"s5-inference-universe-v1\x00" + canonical_json_bytes(list(symbols))
    ).hexdigest()
    preimage = {
        "batchVersion": "s5-signal-batch-v1",
        "modelReleaseId": model_release_id,
        "universeReleaseId": f"sur-{membership[:12]}",
        "membershipSha256": membership,
        "sessionDate": session_date,
        "asOf": as_of,
        "timeframe": "1d",
        "rowCount": 31,
        "parquetFile": "signals.parquet",
        "parquetSha256": hashlib.sha256(parquet).hexdigest(),
        "fixture": False,
        "provenanceClass": "PRODUCTION",
    }
    semantic = hashlib.sha256(b"s5-signal-batch-v1\x00" + canonical_json_bytes(preimage)).hexdigest()
    manifest = canonical_json_bytes(
        {**preimage, "signalBatchId": f"sgb-{semantic[:12]}", "semanticSha256": semantic}
    )
    _write(root, "signals.parquet", parquet)
    _write(root, "batch.json", manifest)
    return hashlib.sha256(manifest).hexdigest()


def test_production_release_and_batch_are_separate_closed_trust_anchors(tmp_path: Path) -> None:
    release_root = _private_root(tmp_path / "release")
    release_sha, release_manifest = _release(release_root)
    release = validate_production_model_release(
        approved_root=release_root, expected_manifest_sha256=release_sha
    )
    batch_root = _private_root(tmp_path / "batch")
    batch_sha = _batch(
        batch_root,
        str(release_manifest["modelReleaseId"]),
        str(release_manifest["modelVersion"]),
        str(release_manifest["modelReportId"]),
    )
    batch = validate_production_signal_batch(
        approved_root=batch_root, expected_manifest_sha256=batch_sha
    )
    assert release.manifest["fixture"] is False
    assert batch.manifest["rowCount"] == 31
    assert len(batch.rows) == 31
    assert all("modelScore" not in row and "predictedReturn" not in row for row in batch.rows)


def test_release_rejects_digest_mutation_fake_and_symlink(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "release")
    digest, _ = _release(root)
    (root / "report.json").write_text("{}\n")
    with pytest.raises(LightGbmContractError, match="digest mismatch"):
        validate_production_model_release(approved_root=root, expected_manifest_sha256=digest)

    other = _private_root(tmp_path / "other")
    other_digest, _ = _release(other)
    model = other / "model.txt"
    model.unlink()
    model.symlink_to(root / "model.txt")
    with pytest.raises(LightGbmContractError):
        validate_production_model_release(
            approved_root=other, expected_manifest_sha256=other_digest
        )


def test_signal_batch_clock_skips_xkrx_substitute_holiday(tmp_path: Path) -> None:
    release_root = _private_root(tmp_path / "release")
    _, manifest = _release(release_root)
    batch_root = _private_root(tmp_path / "batch")
    # 2026-08-17은 대체공휴일이므로 8월 14일 batch의 asOf를 8월 17일 08:10 KST로 둘 수 없다.
    batch_sha = _batch(
        batch_root,
        str(manifest["modelReleaseId"]),
        str(manifest["modelVersion"]),
        str(manifest["modelReportId"]),
        as_of="2026-08-16T23:10:00Z",
    )
    with pytest.raises(LightGbmContractError, match="session clock"):
        validate_production_signal_batch(
            approved_root=batch_root,
            expected_manifest_sha256=batch_sha,
        )


def test_qualification_seal_resumes_release_publish_without_model_refit(tmp_path: Path) -> None:
    origin = _private_root(tmp_path / "origin")
    release_sha, _ = _release(origin)
    names = (*RELEASE_FILES, QUALIFICATION_RECEIPT, RELEASE_MANIFEST)
    files = {name: (origin / name).read_bytes() for name in names}
    parent = _private_root(tmp_path / "resume")
    key = "9" * 64
    _write_qualification_reservation(
        parent=parent,
        qualification_key=key,
        selected_grid_index=0,
    )
    _write_qualification_seal(parent=parent, qualification_key=key, files=files)
    sealed = _read_qualification_seal(parent=parent, qualification_key=key)
    assert sealed == files
    target = parent / "release"
    _publish_sealed_release(release_root=target, files=sealed)
    validated = validate_production_model_release(
        approved_root=target,
        expected_manifest_sha256=release_sha,
    )
    assert validated.manifest_sha256 == release_sha


class _Cursor(AbstractContextManager["_Cursor"]):
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        self.queries.append(query)
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def __exit__(self, *args: object) -> None:
        return None


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.value = _Cursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_db_adapter_calls_only_capability_functions_and_rolls_back_on_missing_receipt(
    tmp_path: Path,
) -> None:
    release_root = _private_root(tmp_path / "release")
    release_sha, manifest = _release(release_root)
    release = validate_production_model_release(
        approved_root=release_root, expected_manifest_sha256=release_sha
    )
    batch_root = _private_root(tmp_path / "batch")
    batch_sha = _batch(
        batch_root,
        str(manifest["modelReleaseId"]),
        str(manifest["modelVersion"]),
        str(manifest["modelReportId"]),
    )
    batch = validate_production_signal_batch(
        approved_root=batch_root, expected_manifest_sha256=batch_sha
    )
    connection = _Connection([("INSERTED",), ("INSERTED",)])
    receipt = stage_release_and_batch(cast(Connection, connection), release=release, batch=batch)
    assert receipt.release_outcome == "INSERTED"
    assert connection.commits == 1 and connection.rollbacks == 0
    assert all("INSERT INTO" not in query for query in connection.value.queries)

    missing = _Connection([])
    with pytest.raises(LightGbmContractError):
        stage_release_and_batch(cast(Connection, missing), release=release, batch=batch)
    assert missing.commits == 0 and missing.rollbacks == 1

    admin = _Connection([(1,)])
    assert (
        activate_release_and_batch(
            cast(Connection, admin),
            model_release_id=str(manifest["modelReleaseId"]),
            signal_batch_id=str(batch.manifest["signalBatchId"]),
            expected_model_release_id=None,
            expected_signal_batch_id=None,
        )
        == 1
    )
