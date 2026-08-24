from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.calibration import fit_ovr_platt
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.features import CORE_FEATURE_COLUMNS
from app.lightgbm.production_db import (
    Connection,
    activate_release_and_batch,
    stage_release_and_batch,
)
from app.lightgbm.production_release import (
    QUALIFICATION_RECEIPT,
    RELEASE_FILES,
    RELEASE_MANIFEST,
    _publish_sealed_release,
    _read_qualification_seal,
    _signal_parquet,
    _write_qualification_reservation,
    _write_qualification_seal,
    qualify_and_write_production_release,
    validate_production_model_release,
    validate_production_signal_batch,
)
from app.lightgbm.production_stage_cli import _manual_action
from app.lightgbm.training import exact_grid, fit_lightgbm_reproducible, raw_margins


def test_manual_stage_action_requires_explicit_valid_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("S5_MANUAL_ACTION", raising=False)
    monkeypatch.delenv("S5_MANUAL_ACTIVATE", raising=False)
    assert _manual_action() == "STAGE"
    monkeypatch.setenv("S5_MANUAL_ACTIVATE", "true")
    assert _manual_action() == "ACTIVATE"
    monkeypatch.setenv("S5_MANUAL_ACTION", "ROLLBACK")
    assert _manual_action() == "ROLLBACK"
    monkeypatch.setenv("S5_MANUAL_ACTION", "AUTO")
    with pytest.raises(ValueError, match=r"manual action"):
        _manual_action()


def test_production_qualification_no_pass_never_projects_final_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.lightgbm.production_release as release_module
    import app.lightgbm.training as training_module

    rows = release_module._TrainingRows(
        keys=(("005930", date(2026, 8, 14)),),
        features=np.zeros((1, len(CORE_FEATURE_COLUMNS)), dtype=np.float32),
        labels=np.asarray([1], dtype=np.int64),
        forward_returns=np.asarray([0.0], dtype=np.float64),
    )
    manifest_sha = "a" * 64
    bundle = SimpleNamespace(
        manifest_sha256=manifest_sha,
        provenance=SimpleNamespace(
            base=SimpleNamespace(dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC))
        ),
        artifact=SimpleNamespace(table=object()),
    )
    materialization = SimpleNamespace(
        feature_bundle=SimpleNamespace(manifest_sha256=manifest_sha),
        acquisition=SimpleNamespace(prices=(), krx_raw_prices={}),
    )
    final = SimpleNamespace(
        fit_sessions=(), early_sessions=(), calibration_sessions=(), evaluation_sessions=()
    )
    monkeypatch.setattr(release_module, "read_production_feature_bundle", lambda **_: bundle)
    monkeypatch.setattr(
        release_module, "build_production_exact_labels", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(release_module, "_training_rows", lambda *_args: rows)
    monkeypatch.setattr(
        release_module,
        "build_walk_forward_plan",
        lambda *_args: SimpleNamespace(folds=(), final=final),
    )
    monkeypatch.setattr(release_module, "_final_arrays", lambda *_args: object())
    monkeypatch.setattr(release_module, "build_production_feature_table", lambda **_: object())
    monkeypatch.setattr(release_module, "_training_rows_from_table", lambda *_args: rows)
    monkeypatch.setattr(release_module, "_corporate_sensitivity", lambda **_: (set(), True))
    monkeypatch.setattr(training_module, "run_exact_four_grid", lambda _blocks: [])
    monkeypatch.setattr(training_module, "select_candidate", lambda _evaluations: None)
    result = qualify_and_write_production_release(
        packet=cast(object, SimpleNamespace()),  # type: ignore[arg-type]
        materialization=cast(object, materialization),  # type: ignore[arg-type]
        feature_root=_private_root(tmp_path / "feature"),
        expected_feature_manifest_sha256=manifest_sha,
        release_root=tmp_path / "release",
        code_head="b" * 40,
        code_tree="c" * 40,
        uv_lock_sha256="d" * 64,
    )
    assert result.reason == "CALIBRATION_FAILED"  # type: ignore[union-attr]
    assert result.final_test_access_count == 0  # type: ignore[union-attr]


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
    metrics = {"ece": 0.03, "brier": 0.5, "logLoss": 0.7}
    baseline = {
        "costSensitivityBps": {"25": 0.0, "30": 0.0, "35": 0.0},
        "meanEdge35Bps": 0.0,
        "logLoss": 0.7,
        "brier": 0.5,
        "ece": 0.03,
        "macroF1": 0.5,
        "confusionMatrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    }
    report_without_id: dict[str, object] = {
        "reportVersion": "s5-production-qualification-v1",
        "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "strictProviderPITClaim": False,
        "selectedCandidate": {"gridIndex": 0, "numLeaves": 15, "classWeight": "NONE"},
        "primaryEvaluation": [
            {
                "gridIndex": grid_index,
                "folds": [
                    {
                        "fold": f"fold-{fold_index}",
                        "raw": metrics,
                        "calibrated": metrics,
                        "passed": True,
                    }
                    for fold_index in range(1, 4)
                ],
            }
            for grid_index in range(4)
        ],
        "sensitivity": [
            {
                "gridIndex": grid_index,
                "folds": [
                    {
                        "fold": f"fold-{fold_index}",
                        "corporateActionPass": True,
                        "macroTimingPass": True,
                        "rowCount": 100,
                        "macroRowCount": 98,
                        "eventFreeRowCount": 100,
                    }
                    for fold_index in range(1, 4)
                ],
            }
            for grid_index in range(4)
        ],
        "corporateActionGlobalPass": True,
        "finalTest": {
            "accessCount": 1,
            "rowCount": 126,
            "raw": metrics,
            "calibrated": metrics,
            "passed": True,
        },
        "costReport": {
            "directionalEdgeOnly": True,
            **baseline,
            "alwaysHold": baseline,
            "trainOnlyPrior": {"probabilities": [1 / 3, 1 / 3, 1 / 3], **baseline},
            "fakeArtifactsIncluded": False,
        },
    }
    report_id = f"mrp-{hashlib.sha256(canonical_json_bytes(report_without_id)).hexdigest()[:12]}"
    report = canonical_json_bytes({**report_without_id, "modelReportId": report_id})
    files = {
        "model.txt": model.model_text,
        "calibrator.json": calibrator.canonical_bytes(),
        "report.json": report,
        "gain-importance.json": canonical_json_bytes(
            {
                "reportVersion": "s5-gain-importance-v1",
                "featureNames": list(CORE_FEATURE_COLUMNS),
                "gain": dict.fromkeys(CORE_FEATURE_COLUMNS, 1.0),
            }
        ),
        "contribution-report.json": canonical_json_bytes(
            {
                "reportVersion": "s5-pred-contrib-v1",
                "reportOnly": True,
                "featureNames": list(CORE_FEATURE_COLUMNS),
                "rowCount": 1,
                "rows": [
                    {
                        "rowKeyHash": "9" * 64,
                        "classes": [
                            {
                                "classIndex": class_index,
                                "bias": 0.0,
                                "contributions": [0.0] * len(CORE_FEATURE_COLUMNS),
                                "rawMargin": 0.0,
                            }
                            for class_index in range(3)
                        ],
                    }
                ],
            }
        ),
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
    semantic = hashlib.sha256(
        b"s5-model-release-v1\x00" + canonical_json_bytes(preimage)
    ).hexdigest()
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
        "batchPurpose": "DAILY",
        "modelReleaseId": model_release_id,
        "universeReleaseId": f"sur-{membership[:12]}",
        "membershipSha256": membership,
        "sessionDate": session_date,
        "asOf": as_of,
        "timeframe": "1d",
        "rowCount": 31,
        "membersSha256": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
        "parquetFile": "signals.parquet",
        "parquetSha256": hashlib.sha256(parquet).hexdigest(),
        "fixture": False,
        "provenanceClass": "PRODUCTION",
    }
    semantic = hashlib.sha256(
        b"s5-signal-batch-v1\x00" + canonical_json_bytes(preimage)
    ).hexdigest()
    manifest = canonical_json_bytes(
        {**preimage, "signalBatchId": f"sgb-{semantic[:12]}", "semanticSha256": semantic}
    )
    _write(root, "signals.parquet", parquet)
    _write(root, "batch.json", manifest)
    return hashlib.sha256(manifest).hexdigest()


def _rewrite_report(root: Path, mutate: Callable[[dict[str, object]], None]) -> str:
    report = json.loads((root / "report.json").read_bytes())
    report.pop("modelReportId")
    mutate(report)
    report_id = f"mrp-{hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12]}"
    report["modelReportId"] = report_id
    _write(root, "report.json", canonical_json_bytes(report))
    qualification = json.loads((root / "qualification.json").read_bytes())
    qualification["reportSha256"] = hashlib.sha256((root / "report.json").read_bytes()).hexdigest()
    _write(root, "qualification.json", canonical_json_bytes(qualification))
    manifest = json.loads((root / "release.json").read_bytes())
    manifest["modelReportId"] = report_id
    manifest["files"]["report.json"] = hashlib.sha256(
        (root / "report.json").read_bytes()
    ).hexdigest()
    manifest["files"]["qualification.json"] = hashlib.sha256(
        (root / "qualification.json").read_bytes()
    ).hexdigest()
    manifest.pop("modelReleaseId")
    manifest.pop("semanticSha256")
    semantic = hashlib.sha256(
        b"s5-model-release-v1\x00" + canonical_json_bytes(manifest)
    ).hexdigest()
    manifest["modelReleaseId"] = f"lgr-{semantic[:12]}"
    manifest["semanticSha256"] = semantic
    content = canonical_json_bytes(manifest)
    _write(root, "release.json", content)
    return hashlib.sha256(content).hexdigest()


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
    with pytest.raises(LightGbmContractError, match=r"digest mismatch"):
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


def test_release_rejects_self_consistent_but_failed_qualification(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "release")
    _release(root)

    def fail_final(report: dict[str, object]) -> None:
        final_test = cast(dict[str, object], report["finalTest"])
        final_test["passed"] = False

    digest = _rewrite_report(root, fail_final)
    with pytest.raises(LightGbmContractError, match=r"final test qualification"):
        validate_production_model_release(
            approved_root=root,
            expected_manifest_sha256=digest,
        )


def test_release_rejects_a_passing_candidate_that_violates_locked_ranking(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "release")
    _release(root)

    def make_second_candidate_better(report: dict[str, object]) -> None:
        primary = cast(list[dict[str, object]], report["primaryEvaluation"])
        for fold in cast(list[dict[str, object]], primary[0]["folds"]):
            cast(dict[str, object], fold["calibrated"])["logLoss"] = 0.71
        for fold in cast(list[dict[str, object]], primary[1]["folds"]):
            cast(dict[str, object], fold["calibrated"])["logLoss"] = 0.69

    digest = _rewrite_report(root, make_second_candidate_better)
    with pytest.raises(LightGbmContractError, match=r"locked ranking"):
        validate_production_model_release(
            approved_root=root,
            expected_manifest_sha256=digest,
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
    with pytest.raises(LightGbmContractError, match=r"session clock"):
        validate_production_signal_batch(
            approved_root=batch_root,
            expected_manifest_sha256=batch_sha,
        )


def test_signal_batch_rejects_decoded_parquet_amplification_before_full_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.lightgbm.production_release as release_module

    release_root = _private_root(tmp_path / "release")
    _, manifest = _release(release_root)
    batch_root = _private_root(tmp_path / "batch")
    _batch(
        batch_root,
        str(manifest["modelReleaseId"]),
        str(manifest["modelVersion"]),
        str(manifest["modelReportId"]),
    )
    symbols = tuple(sorted([f"{value:06d}" for value in range(1, 30)] + ["005930", "132030"]))
    large_as_of = "2026-08-18T00:00:00Z" + ("A" * 1_500_000)
    rows = [
        {
            "symbol": symbol,
            "status": "AVAILABLE",
            "asOf": large_as_of,
            "signal": "HOLD",
            "confidence": 0.5,
            "modelVersion": str(manifest["modelVersion"]),
            "modelReportId": str(manifest["modelReportId"]),
        }
        for symbol in symbols
    ]
    sink = BytesIO()
    pq.write_table(
        pa.Table.from_pylist(rows, schema=release_module.SIGNAL_BATCH_SCHEMA),
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
    )
    amplified = sink.getvalue()
    assert 0 < len(amplified) <= release_module.BATCH_MAX_BYTES
    batch_manifest = json.loads((batch_root / "batch.json").read_bytes())
    batch_manifest["parquetSha256"] = hashlib.sha256(amplified).hexdigest()
    _write(batch_root, "signals.parquet", amplified)
    manifest_bytes = canonical_json_bytes(batch_manifest)
    _write(batch_root, "batch.json", manifest_bytes)

    original_parquet_file = release_module.pq.ParquetFile

    class _ParquetFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._delegate = original_parquet_file(*args, **kwargs)
            self.metadata = self._delegate.metadata
            self.schema_arrow = self._delegate.schema_arrow

        def read(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("production signal batch validation must not use full read")

        def iter_batches(self, *args: object, **kwargs: object) -> object:
            return self._delegate.iter_batches(*args, **kwargs)

    monkeypatch.setattr(release_module.pq, "ParquetFile", _ParquetFile)
    with pytest.raises(LightGbmContractError, match=r"declared decoded|actual decoded"):
        validate_production_signal_batch(
            approved_root=batch_root,
            expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
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
        self.params: list[tuple[object, ...]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        self.queries.append(query)
        self.params.append(params)
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
            release_manifest_sha256=release.manifest_sha256,
            batch_manifest_sha256=batch.manifest_sha256,
        )
        == 1
    )
    rollback = _Connection([(2,)])
    assert (
        activate_release_and_batch(
            cast(Connection, rollback),
            model_release_id=str(manifest["modelReleaseId"]),
            signal_batch_id=str(batch.manifest["signalBatchId"]),
            expected_model_release_id="lgr-999999999999",
            expected_signal_batch_id="sgb-999999999999",
            release_manifest_sha256=release.manifest_sha256,
            batch_manifest_sha256=batch.manifest_sha256,
            rollback=True,
        )
        == 2
    )
    assert rollback.value.params[0][-1] == "MANUAL_ROLLBACK"
