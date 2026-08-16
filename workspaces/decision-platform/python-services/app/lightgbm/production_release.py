"""S5.6B production qualification, immutable model release와 exact signal batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.bootstrap_executor import (
    BootstrapMaterialization,
    build_production_feature_table,
)
from app.lightgbm.bootstrap_packet import BootstrapPacket
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.feature_artifact import (
    ProductionFeatureBundle,
    logical_training_dataset_hash,
    read_production_feature_bundle,
)
from app.lightgbm.features import CORE_FEATURE_COLUMNS, ProductionPriceEvidence
from app.lightgbm.labels import LabelRow, build_production_exact_labels, zero_fill_features
from app.lightgbm.private_root import require_private_regular_file, require_private_root
from app.lightgbm.production_policy import (
    corporate_action_sensitivity_pass,
    macro_timing_sensitivity_pass,
)
from app.lightgbm.temporal import next_xkrx_evidence_clock
from app.lightgbm.universe import MonthlyUniverse
from app.lightgbm.walk_forward import UntouchedTestLoader, build_walk_forward_plan
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file, write_approved_new_file


RELEASE_FILES = (
    "model.txt",
    "calibrator.json",
    "report.json",
    "gain-importance.json",
    "contribution-report.json",
)
RELEASE_MANIFEST = "release.json"
QUALIFICATION_RECEIPT = "qualification.json"
QUALIFICATION_RESERVATION_VERSION = "s5-final-test-reservation-v1"
QUALIFICATION_SEAL_MAGIC = b"S5Q1\n"
QUALIFICATION_SEAL_MAX_BYTES = 192 * 1024 * 1024
BATCH_MANIFEST = "batch.json"
BATCH_PARQUET = "signals.parquet"
MODEL_MAX_BYTES = 64 * 1024 * 1024
JSON_MAX_BYTES = 16 * 1024 * 1024
SMALL_JSON_MAX_BYTES = 1 * 1024 * 1024
BATCH_MAX_BYTES = 4 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=JSON_MAX_BYTES,
    max_depth=10,
    max_list_items=20_000,
    max_object_keys=256,
    max_text_codepoints=1_000_000,
    max_text_bytes=4_000_000,
    max_number_characters=64,
)


@dataclass(frozen=True)
class QualificationFailure:
    """final test를 열지 않았거나 qualification이 실패한 typed ABSTAIN 결과."""

    reason: str
    final_test_access_count: int


@dataclass(frozen=True)
class QualifiedProductionRelease:
    """검증·봉인된 model release와 inference에 필요한 in-memory model receipt."""

    model_release_id: str
    model_version: str
    model_report_id: str
    release_manifest_sha256: str
    release_manifest_bytes: bytes
    feature_bundle: ProductionFeatureBundle
    model: object
    calibrator: object


@dataclass(frozen=True)
class ValidatedProductionRelease:
    """production/fake 분리 validator가 반환하는 immutable model release bytes."""

    manifest: Mapping[str, object]
    manifest_sha256: str
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class ValidatedSignalBatch:
    """exact 31-row production batch와 closed manifest validation receipt."""

    manifest: Mapping[str, object]
    manifest_sha256: str
    rows: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class _TrainingRows:
    keys: tuple[tuple[str, date], ...]
    features: np.ndarray
    labels: np.ndarray
    forward_returns: np.ndarray


def qualify_and_write_production_release(
    *,
    packet: BootstrapPacket,
    materialization: BootstrapMaterialization,
    feature_root: Path,
    expected_feature_manifest_sha256: str,
    release_root: Path,
    code_head: str,
    code_tree: str,
    uv_lock_sha256: str,
) -> QualifiedProductionRelease | QualificationFailure:
    """feature bundle v2 trust anchor 검증 뒤에만 exact grid를 실행하고 release를 manifest-last 봉인한다."""

    bundle = read_production_feature_bundle(
        approved_root=feature_root,
        expected_manifest_sha256=expected_feature_manifest_sha256,
    )
    if bundle.manifest_sha256 != materialization.feature_bundle.manifest_sha256:
        raise LightGbmContractError("production feature bundle handoff digest drifted")
    _require_git_sha(code_head, "code HEAD")
    _require_git_sha(code_tree, "code tree")
    _require_sha(uv_lock_sha256, "uv.lock")
    qualification_key = _qualification_key(
        feature_manifest_sha256=bundle.manifest_sha256,
        code_head=code_head,
        code_tree=code_tree,
        uv_lock_sha256=uv_lock_sha256,
    )
    resumed = _resume_sealed_qualification(
        parent=release_root.parent,
        release_root=release_root,
        qualification_key=qualification_key,
        feature_root=feature_root,
        feature_manifest_sha256=bundle.manifest_sha256,
    )
    if resumed is not None:
        return resumed

    # Heavy training imports intentionally occur only after the production feature trust anchor passes.
    from app.lightgbm.export import curated_contribution_report, gain_importance, report_id
    from app.lightgbm.training import (
        CandidateRun,
        calibrated_probabilities,
        evaluate_calibration_gate,
        research_cost_report,
        run_exact_four_grid,
        run_final_candidate,
        select_candidate,
    )

    cutoff = bundle.provenance.base.dataset_cutoff
    labels = build_production_exact_labels(
        materialization.acquisition.prices,
        dataset_cutoff=cutoff,
    )
    rows = _training_rows(bundle, labels)
    sessions = tuple(sorted({session for _, session in rows.keys}))
    plan = build_walk_forward_plan(sessions, labels)
    primary_blocks: tuple[Any, ...] = tuple(_fold_arrays(rows, split) for split in plan.folds)
    final_rows = _rows_for_sessions(rows, plan.final.evaluation_sessions)
    final_blocks = _final_arrays(rows, plan.final)
    loader = UntouchedTestLoader((final_rows.features, final_rows.labels))

    delayed_table = build_production_feature_table(
        packet=packet,
        acquisition=materialization.acquisition,
        macro_delay_sessions=1,
    )
    delayed_rows = _training_rows_from_table(delayed_table, labels)
    _require_same_keys(rows, delayed_rows, "macro sensitivity")
    event_free_keys, corporate_pass = _corporate_sensitivity(
        prices=materialization.acquisition.prices,
        krx_raw_prices=materialization.acquisition.krx_raw_prices,
        feature_keys=set(rows.keys),
    )
    if not corporate_pass:
        return QualificationFailure("UNIDENTIFIABLE_OUTPUT", loader.access_count)

    candidate_runs = run_exact_four_grid(primary_blocks)
    sensitivity_report: list[dict[str, object]] = []
    # Candidate별 세 fold sensitivity를 원래 grid 순서 그대로 평가한다.
    production_runs: list[CandidateRun] = []
    for run in candidate_runs:
        updated_folds = []
        candidate_sensitivity: list[dict[str, object]] = []
        for fold_run, split in zip(run.folds, plan.folds, strict=True):
            evaluation_rows = _rows_for_sessions(rows, split.evaluation_sessions)
            delayed_evaluation = _rows_for_sessions(delayed_rows, split.evaluation_sessions)
            event_indices = [
                index for index, key in enumerate(evaluation_rows.keys) if key in event_free_keys
            ]
            if not event_indices:
                corporate_fold_pass = False
            else:
                event_x = evaluation_rows.features[event_indices]
                event_y = evaluation_rows.labels[event_indices]
                event_raw, event_calibrated = calibrated_probabilities(
                    fold_run.model, fold_run.calibrator, event_x
                )
                corporate_fold_pass = evaluate_calibration_gate(
                    event_y, event_raw, event_calibrated
                ).passed
            _, primary_probabilities = calibrated_probabilities(
                fold_run.model, fold_run.calibrator, evaluation_rows.features
            )
            _, delayed_probabilities = calibrated_probabilities(
                fold_run.model, fold_run.calibrator, delayed_evaluation.features
            )
            macro_pass = macro_timing_sensitivity_pass(
                primary_probabilities=primary_probabilities,
                delayed_probabilities=delayed_probabilities,
                labels=evaluation_rows.labels.tolist(),
                primary_row_count=len(evaluation_rows.labels),
            )
            passed = fold_run.evaluation.passed and corporate_fold_pass and macro_pass
            updated_evaluation = replace(fold_run.evaluation, passed=passed)
            updated_folds.append(replace(fold_run, evaluation=updated_evaluation))
            candidate_sensitivity.append(
                {
                    "fold": split.name,
                    "corporateActionPass": corporate_fold_pass,
                    "macroTimingPass": macro_pass,
                    "rowCount": len(evaluation_rows.labels),
                    "eventFreeRowCount": len(event_indices),
                }
            )
        evaluations = tuple(item.evaluation for item in updated_folds)
        production_runs.append(
            CandidateRun(
                replace(run.evaluation, folds=(evaluations[0], evaluations[1], evaluations[2])),
                (updated_folds[0], updated_folds[1], updated_folds[2]),
            )
        )
        sensitivity_report.append(
            {
                "gridIndex": run.evaluation.candidate.grid_index,
                "folds": candidate_sensitivity,
            }
        )

    selected = select_candidate([item.evaluation for item in production_runs])
    if selected is None:
        return QualificationFailure("CALIBRATION_FAILED", loader.access_count)
    _write_qualification_reservation(
        parent=release_root.parent,
        qualification_key=qualification_key,
        selected_grid_index=selected.candidate.grid_index,
    )
    final_run = run_final_candidate(production_runs, final_blocks, loader)
    if final_run is None:
        raise LightGbmContractError("qualification candidate selection drifted")
    if loader.access_count != 1 or not final_run.evaluation.passed:
        return QualificationFailure("CALIBRATION_FAILED", loader.access_count)

    calibrator_bytes = final_run.calibrator.canonical_bytes()
    calibrator_sha = hashlib.sha256(calibrator_bytes).hexdigest()
    model_bytes = final_run.model.model_text
    model_sha = hashlib.sha256(model_bytes).hexdigest()
    model_version = f"lgbm-v1-{model_sha[:12]}"
    feature_names = list(CORE_FEATURE_COLUMNS)
    gain_bytes = canonical_json_bytes(
        {
            "reportVersion": "s5-gain-importance-v1",
            "featureNames": feature_names,
            "gain": gain_importance(final_run.model),
        }
    )
    contribution_bytes = curated_contribution_report(
        final_run.model.booster,  # type: ignore[arg-type]
        final_rows.features,
        row_keys=final_rows.keys,
        dataset_hash=logical_training_dataset_hash(bundle.artifact.table, rows.labels.tolist()),
        feature_names=feature_names,
        best_iteration=final_run.model.best_iteration,
    )
    _, final_probabilities = calibrated_probabilities(
        final_run.model, final_run.calibrator, final_rows.features
    )
    report_payload: dict[str, object] = {
        "reportVersion": "s5-production-qualification-v1",
        "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "strictProviderPITClaim": False,
        "selectedCandidate": {
            "gridIndex": final_run.candidate.grid_index,
            "numLeaves": final_run.candidate.num_leaves,
            "classWeight": final_run.candidate.class_weight,
        },
        "sensitivity": sensitivity_report,
        "corporateActionGlobalPass": corporate_pass,
        "finalTest": {
            "accessCount": loader.access_count,
            "rowCount": len(final_rows.labels),
            "raw": _metrics(final_run.evaluation.raw),
            "calibrated": _metrics(final_run.evaluation.calibrated),
            "passed": final_run.evaluation.passed,
        },
        "costReport": research_cost_report(
            final_rows.labels,
            final_probabilities,
            final_rows.forward_returns,
            np.bincount(final_blocks.y_fit, minlength=3),
        ),
    }
    model_report_id = report_id(report_payload)
    report_payload["modelReportId"] = model_report_id
    report_bytes = canonical_json_bytes(report_payload)
    qualification = {
        "qualificationVersion": "s5-production-qualification-receipt-v1",
        "featureManifestSha256": bundle.manifest_sha256,
        "trainingDatasetSha256": logical_training_dataset_hash(
            bundle.artifact.table, rows.labels.tolist()
        ),
        "finalTestAccessCount": loader.access_count,
        "selectedGridIndex": final_run.candidate.grid_index,
        "modelSha256": model_sha,
        "calibratorSha256": calibrator_sha,
        "reportSha256": hashlib.sha256(report_bytes).hexdigest(),
        "gainImportanceSha256": hashlib.sha256(gain_bytes).hexdigest(),
        "contributionReportSha256": hashlib.sha256(contribution_bytes).hexdigest(),
    }
    qualification_bytes = canonical_json_bytes(qualification)
    files = {
        "model.txt": model_bytes,
        "calibrator.json": calibrator_bytes,
        "report.json": report_bytes,
        "gain-importance.json": gain_bytes,
        "contribution-report.json": contribution_bytes,
        QUALIFICATION_RECEIPT: qualification_bytes,
    }
    release_preimage = {
        "releaseVersion": "s5-model-release-v1",
        "modelVersion": model_version,
        "modelReportId": model_report_id,
        "featureManifestSha256": bundle.manifest_sha256,
        "sourceBundleSetSha256": bundle.provenance.source_bundle_set_sha256,
        "sourcePolicySetSha256": bundle.provenance.source_policy_set_sha256,
        "trainingDatasetSha256": qualification["trainingDatasetSha256"],
        "codeHead": code_head,
        "codeTree": code_tree,
        "uvLockSha256": uv_lock_sha256,
        "calendarName": "XKRX",
        "calendarVersion": "4.13.2",
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "fixture": False,
        "provenanceClass": "PRODUCTION",
        "status": "QUALIFIED",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    semantic_sha = hashlib.sha256(
        b"s5-model-release-v1\x00" + canonical_json_bytes(release_preimage)
    ).hexdigest()
    model_release_id = f"lgr-{semantic_sha[:12]}"
    manifest_bytes = canonical_json_bytes(
        {**release_preimage, "modelReleaseId": model_release_id, "semanticSha256": semantic_sha}
    )
    sealed_files = {**files, RELEASE_MANIFEST: manifest_bytes}
    _write_qualification_seal(
        parent=release_root.parent,
        qualification_key=qualification_key,
        files=sealed_files,
    )
    _publish_sealed_release(release_root=release_root, files=sealed_files)
    validated = validate_production_model_release(
        approved_root=release_root,
        expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
    return QualifiedProductionRelease(
        model_release_id=model_release_id,
        model_version=model_version,
        model_report_id=model_report_id,
        release_manifest_sha256=validated.manifest_sha256,
        release_manifest_bytes=manifest_bytes,
        feature_bundle=bundle,
        model=final_run.model,
        calibrator=final_run.calibrator,
    )


def write_production_signal_batch(
    *,
    release: QualifiedProductionRelease,
    inference_universe: MonthlyUniverse,
    inference_table: pa.Table,
    session_date: date,
    as_of: datetime,
    batch_root: Path,
) -> ValidatedSignalBatch:
    """활성 release로 exact 31 membership의 AVAILABLE-only immutable daily batch를 만든다."""

    if as_of.tzinfo is None:
        raise LightGbmContractError("signal batch asOf must be timezone aware")
    symbols = tuple(sorted(inference_universe.symbols))
    if len(symbols) != 31 or len(set(symbols)) != 31 or "132030" not in symbols:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: inference universe must be exact 31")
    table_rows = inference_table.to_pylist()
    feature_by_symbol = {
        str(row["symbol"]): row
        for row in table_rows
        if row["sessionDate"] == session_date and str(row["symbol"]) in symbols
    }
    if set(feature_by_symbol) != set(symbols):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: current 31-row feature evidence is incomplete")
    matrix = np.asarray(
        [
            list(zero_fill_features({name: feature_by_symbol[symbol][name] for name in CORE_FEATURE_COLUMNS}).values())
            for symbol in symbols
        ],
        dtype=np.float32,
    )
    from app.lightgbm.training import calibrated_probabilities, raw_margins
    from app.lightgbm.metrics import tie_aware_argmax

    _, probabilities = calibrated_probabilities(release.model, release.calibrator, matrix)  # type: ignore[arg-type]
    margins = raw_margins(release.model, matrix)  # type: ignore[arg-type]
    if margins.shape != probabilities.shape:
        raise LightGbmContractError("signal batch model output is invalid")
    predicted = tie_aware_argmax(probabilities)
    labels = ("SELL", "HOLD", "BUY")
    as_of_text = as_of.astimezone(UTC).isoformat().replace("+00:00", "Z")
    rows = [
        {
            "symbol": symbol,
            "status": "AVAILABLE",
            "asOf": as_of_text,
            "signal": labels[int(predicted[index])],
            "confidence": float(probabilities[index, predicted[index]]),
            "modelVersion": release.model_version,
            "modelReportId": release.model_report_id,
        }
        for index, symbol in enumerate(symbols)
    ]
    parquet = _signal_parquet(rows)
    membership_sha = hashlib.sha256(
        b"s5-inference-universe-v1\x00" + canonical_json_bytes(list(symbols))
    ).hexdigest()
    universe_release_id = f"sur-{membership_sha[:12]}"
    preimage = {
        "batchVersion": "s5-signal-batch-v1",
        "modelReleaseId": release.model_release_id,
        "universeReleaseId": universe_release_id,
        "membershipSha256": membership_sha,
        "sessionDate": session_date.isoformat(),
        "asOf": as_of_text,
        "timeframe": "1d",
        "rowCount": 31,
        "parquetFile": BATCH_PARQUET,
        "parquetSha256": hashlib.sha256(parquet).hexdigest(),
        "fixture": False,
        "provenanceClass": "PRODUCTION",
    }
    semantic_sha = hashlib.sha256(
        b"s5-signal-batch-v1\x00" + canonical_json_bytes(preimage)
    ).hexdigest()
    manifest = canonical_json_bytes(
        {**preimage, "signalBatchId": f"sgb-{semantic_sha[:12]}", "semanticSha256": semantic_sha}
    )
    _prepare_bundle_root(batch_root, allowed={BATCH_PARQUET, BATCH_MANIFEST})
    _write_private_exact(batch_root, BATCH_PARQUET, parquet, BATCH_MAX_BYTES)
    _write_private_exact(batch_root, BATCH_MANIFEST, manifest, SMALL_JSON_MAX_BYTES)
    return validate_production_signal_batch(
        approved_root=batch_root,
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )


def validate_production_model_release(
    *, approved_root: Path, expected_manifest_sha256: str
) -> ValidatedProductionRelease:
    """fake artifact validator와 분리해 fixed filenames와 production provenance만 허용한다."""

    _require_sha(expected_manifest_sha256, "release manifest")
    require_private_root(approved_root)
    manifest_bytes = _read_private(approved_root, RELEASE_MANIFEST, SMALL_JSON_MAX_BYTES)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise LightGbmContractError("release manifest trust anchor mismatch")
    manifest = _parse_canonical_object(manifest_bytes)
    expected_fields = {
        "releaseVersion", "modelReleaseId", "modelVersion", "modelReportId",
        "featureManifestSha256", "sourceBundleSetSha256", "sourcePolicySetSha256",
        "trainingDatasetSha256", "codeHead", "codeTree", "uvLockSha256", "calendarName",
        "calendarVersion", "temporalQuality", "fixture", "provenanceClass", "status",
        "files", "semanticSha256",
    }
    if set(manifest) != expected_fields:
        raise LightGbmContractError("release manifest field set is not closed")
    if (
        manifest["releaseVersion"] != "s5-model-release-v1"
        or manifest["fixture"] is not False
        or manifest["provenanceClass"] != "PRODUCTION"
        or manifest["status"] != "QUALIFIED"
        or manifest["temporalQuality"] != "RECONSTRUCTED_FIXED_LAG"
        or manifest["calendarName"] != "XKRX"
        or manifest["calendarVersion"] != "4.13.2"
    ):
        raise LightGbmContractError("release manifest production authority is invalid")
    files_field = manifest["files"]
    if not isinstance(files_field, dict) or set(files_field) != {*RELEASE_FILES, QUALIFICATION_RECEIPT}:
        raise LightGbmContractError("release file set is not exact")
    files: dict[str, bytes] = {}
    for name in (*RELEASE_FILES, QUALIFICATION_RECEIPT):
        content = _read_private(approved_root, name, _file_cap(name))
        digest = hashlib.sha256(content).hexdigest()
        if files_field.get(name) != digest:
            raise LightGbmContractError("release file digest mismatch")
        if name.endswith(".json"):
            _parse_canonical_object(content)
        files[name] = content
    preimage = {key: value for key, value in manifest.items() if key not in {"modelReleaseId", "semanticSha256"}}
    semantic = hashlib.sha256(
        b"s5-model-release-v1\x00" + canonical_json_bytes(preimage)
    ).hexdigest()
    if manifest["semanticSha256"] != semantic or manifest["modelReleaseId"] != f"lgr-{semantic[:12]}":
        raise LightGbmContractError("release content-derived identity mismatch")
    for field in (
        "featureManifestSha256", "sourceBundleSetSha256", "sourcePolicySetSha256",
        "trainingDatasetSha256", "uvLockSha256", "semanticSha256",
    ):
        _require_sha(str(manifest[field]), field)
    for field in ("codeHead", "codeTree"):
        _require_git_sha(str(manifest[field]), field)
    _validate_release_semantics(manifest, files)
    return ValidatedProductionRelease(manifest, expected_manifest_sha256, files)


def load_qualified_production_release(
    *,
    release_root: Path,
    expected_release_manifest_sha256: str,
    feature_root: Path,
    expected_feature_manifest_sha256: str,
) -> QualifiedProductionRelease:
    """daily inference가 release와 feature trust anchor를 재검증한 뒤 text model만 복원한다."""

    validated = validate_production_model_release(
        approved_root=release_root,
        expected_manifest_sha256=expected_release_manifest_sha256,
    )
    bundle = read_production_feature_bundle(
        approved_root=feature_root,
        expected_manifest_sha256=expected_feature_manifest_sha256,
    )
    manifest = validated.manifest
    if manifest["featureManifestSha256"] != bundle.manifest_sha256:
        raise LightGbmContractError("release is bound to a different feature bundle")
    # 검증된 bounded UTF-8 text model 외 pickle/joblib/remote code 경로는 존재하지 않는다.
    import lightgbm as lgb

    from app.lightgbm.calibration import calibrator_from_mapping
    from app.lightgbm.training import TrainedBooster

    try:
        model_text = validated.files["model.txt"]
        model_string = model_text.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LightGbmContractError("production model text is not UTF-8") from error
    booster = lgb.Booster(model_str=model_string)
    best_iteration = booster.current_iteration()
    if best_iteration <= 0 or best_iteration > 500:
        raise LightGbmContractError("production model iteration is invalid")
    model = TrainedBooster(
        booster=booster,
        model_text=model_text,
        model_sha256=hashlib.sha256(model_text).hexdigest(),
        best_iteration=best_iteration,
        num_threads=4,
    )
    calibrator = calibrator_from_mapping(_parse_canonical_object(validated.files["calibrator.json"]))
    return QualifiedProductionRelease(
        model_release_id=str(manifest["modelReleaseId"]),
        model_version=str(manifest["modelVersion"]),
        model_report_id=str(manifest["modelReportId"]),
        release_manifest_sha256=validated.manifest_sha256,
        release_manifest_bytes=_read_private(
            release_root, RELEASE_MANIFEST, SMALL_JSON_MAX_BYTES
        ),
        feature_bundle=bundle,
        model=model,
        calibrator=calibrator,
    )


def validate_production_signal_batch(
    *, approved_root: Path, expected_manifest_sha256: str
) -> ValidatedSignalBatch:
    """manifest trust anchor와 exact 31-row AVAILABLE Parquet을 모두 검증한다."""

    _require_sha(expected_manifest_sha256, "batch manifest")
    require_private_root(approved_root)
    manifest_bytes = _read_private(approved_root, BATCH_MANIFEST, SMALL_JSON_MAX_BYTES)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise LightGbmContractError("batch manifest trust anchor mismatch")
    manifest = _parse_canonical_object(manifest_bytes)
    expected = {
        "batchVersion", "signalBatchId", "modelReleaseId", "universeReleaseId",
        "membershipSha256", "sessionDate", "asOf", "timeframe", "rowCount",
        "parquetFile", "parquetSha256", "fixture", "provenanceClass", "semanticSha256",
    }
    if set(manifest) != expected:
        raise LightGbmContractError("batch manifest field set is not closed")
    if (
        manifest["batchVersion"] != "s5-signal-batch-v1"
        or manifest["timeframe"] != "1d"
        or manifest["rowCount"] != 31
        or manifest["parquetFile"] != BATCH_PARQUET
        or manifest["fixture"] is not False
        or manifest["provenanceClass"] != "PRODUCTION"
    ):
        raise LightGbmContractError("batch production authority is invalid")
    parquet = _read_private(approved_root, BATCH_PARQUET, BATCH_MAX_BYTES)
    if hashlib.sha256(parquet).hexdigest() != manifest["parquetSha256"]:
        raise LightGbmContractError("batch Parquet digest mismatch")
    rows = _validate_signal_parquet(parquet)
    try:
        session_date = date.fromisoformat(str(manifest["sessionDate"]))
        as_of_text = str(manifest["asOf"])
        as_of = datetime.fromisoformat(
            as_of_text[:-1] + "+00:00" if as_of_text.endswith("Z") else as_of_text
        )
    except ValueError:
        raise LightGbmContractError("batch session clock is invalid") from None
    expected_as_of = next_xkrx_evidence_clock(session_date).astimezone(UTC)
    if as_of.tzinfo is None or as_of.astimezone(UTC) != expected_as_of:
        raise LightGbmContractError("batch session clock is invalid")
    if any(row["asOf"] != as_of_text for row in rows):
        raise LightGbmContractError("batch rows do not bind the manifest")
    membership = [str(row["symbol"]) for row in rows]
    membership_sha = hashlib.sha256(
        b"s5-inference-universe-v1\x00" + canonical_json_bytes(membership)
    ).hexdigest()
    if manifest["membershipSha256"] != membership_sha or manifest["universeReleaseId"] != f"sur-{membership_sha[:12]}":
        raise LightGbmContractError("batch membership identity mismatch")
    preimage = {key: value for key, value in manifest.items() if key not in {"signalBatchId", "semanticSha256"}}
    semantic = hashlib.sha256(
        b"s5-signal-batch-v1\x00" + canonical_json_bytes(preimage)
    ).hexdigest()
    if manifest["semanticSha256"] != semantic or manifest["signalBatchId"] != f"sgb-{semantic[:12]}":
        raise LightGbmContractError("batch content-derived identity mismatch")
    return ValidatedSignalBatch(manifest, expected_manifest_sha256, rows)


def _training_rows(bundle: ProductionFeatureBundle, labels: Sequence[LabelRow]) -> _TrainingRows:
    return _training_rows_from_table(bundle.artifact.table, labels)


def _training_rows_from_table(table: pa.Table, labels: Sequence[LabelRow]) -> _TrainingRows:
    label_map = {(row.symbol, row.session_date): row for row in labels}
    if len(label_map) != len(labels):
        raise LightGbmContractError("production labels contain duplicate keys")
    keys: list[tuple[str, date]] = []
    features: list[list[np.float32]] = []
    y: list[int] = []
    returns: list[float] = []
    for raw in table.to_pylist():
        key = (str(raw["symbol"]), raw["sessionDate"])
        label = label_map.get(key)
        if label is None:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: feature row label is missing")
        values = zero_fill_features({name: raw[name] for name in CORE_FEATURE_COLUMNS})
        keys.append(key)
        features.append(list(values.values()))
        y.append(label.label)
        returns.append(label.forward_return)
    if not keys:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: training rows are absent")
    return _TrainingRows(
        tuple(keys),
        np.asarray(features, dtype=np.float32),
        np.asarray(y, dtype=np.int64),
        np.asarray(returns, dtype=np.float64),
    )


def _rows_for_sessions(rows: _TrainingRows, sessions: Sequence[date]) -> _TrainingRows:
    allowed = set(sessions)
    indices = [index for index, (_, day) in enumerate(rows.keys) if day in allowed]
    if not indices:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: training block rows are absent")
    return _TrainingRows(
        tuple(rows.keys[index] for index in indices),
        rows.features[indices],
        rows.labels[indices],
        rows.forward_returns[indices],
    )


def _fold_arrays(rows: _TrainingRows, split: object) -> Any:
    from app.lightgbm.training import FoldArrays

    fit = _rows_for_sessions(rows, split.fit_sessions)  # type: ignore[attr-defined]
    early = _rows_for_sessions(rows, split.early_sessions)  # type: ignore[attr-defined]
    calibration = _rows_for_sessions(rows, split.calibration_sessions)  # type: ignore[attr-defined]
    evaluation = _rows_for_sessions(rows, split.evaluation_sessions)  # type: ignore[attr-defined]
    return FoldArrays(
        fit.features, fit.labels, early.features, early.labels,
        calibration.features, calibration.labels, evaluation.features, evaluation.labels,
    )


def _final_arrays(rows: _TrainingRows, split: object) -> Any:
    from app.lightgbm.training import FinalFitArrays

    fit = _rows_for_sessions(rows, split.fit_sessions)  # type: ignore[attr-defined]
    early = _rows_for_sessions(rows, split.early_sessions)  # type: ignore[attr-defined]
    calibration = _rows_for_sessions(rows, split.calibration_sessions)  # type: ignore[attr-defined]
    return FinalFitArrays(
        fit.features, fit.labels, early.features, early.labels,
        calibration.features, calibration.labels,
    )


def _corporate_sensitivity(
    *,
    prices: Sequence[ProductionPriceEvidence],
    krx_raw_prices: Mapping[tuple[str, date], tuple[float, float]] | None,
    feature_keys: set[tuple[str, date]],
) -> tuple[set[tuple[str, date]], bool]:
    if not krx_raw_prices:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: KRX raw sensitivity evidence is absent")
    by_symbol: dict[str, list[ProductionPriceEvidence]] = {}
    for row in prices:
        by_symbol.setdefault(row.symbol, []).append(row)
    kis_close: list[float] = []
    krx_close: list[float] = []
    kis_label: list[float] = []
    krx_label: list[float] = []
    event_free_keys: set[tuple[str, date]] = set()
    for symbol, values in by_symbol.items():
        ordered = sorted(values, key=lambda item: item.session_date)
        for index in range(59, len(ordered) - 6):
            current = ordered[index]
            key = (symbol, current.session_date)
            if key not in feature_keys:
                continue
            window = ordered[index - 59 : index + 7]
            if any(_has_corporate_action(row) for row in window):
                continue
            prior = ordered[index - 1]
            t1 = ordered[index + 1]
            t6 = ordered[index + 6]
            raw_prior = krx_raw_prices.get((symbol, prior.session_date))
            raw_current = krx_raw_prices.get((symbol, current.session_date))
            raw_t1 = krx_raw_prices.get((symbol, t1.session_date))
            raw_t6 = krx_raw_prices.get((symbol, t6.session_date))
            if None in {raw_prior, raw_current, raw_t1, raw_t6}:
                continue
            assert raw_prior is not None and raw_current is not None
            assert raw_t1 is not None and raw_t6 is not None
            if t1.adjusted_open is None or t6.adjusted_open is None:
                continue
            event_free_keys.add(key)
            kis_close.append(current.adjusted_close / prior.adjusted_close - 1.0)
            krx_close.append(raw_current[1] / raw_prior[1] - 1.0)
            kis_label.append(t6.adjusted_open / t1.adjusted_open - 1.0)
            krx_label.append(raw_t6[0] / raw_t1[0] - 1.0)
    passed = corporate_action_sensitivity_pass(kis_close, krx_close, kis_label, krx_label)
    return event_free_keys, passed


def _has_corporate_action(row: ProductionPriceEvidence) -> bool:
    return (
        row.mod_yn != "N"
        or row.flng_cls_code not in {"", "00"}
        or row.prtt_rate > 0
        or bool(row.revl_issu_reas)
    )


def _require_same_keys(left: _TrainingRows, right: _TrainingRows, name: str) -> None:
    if left.keys != right.keys:
        raise DatasetUnavailable(f"UNIDENTIFIABLE_OUTPUT: {name} coverage is below 100%")


def _metrics(value: object) -> dict[str, float]:
    return {
        "ece": float(value.ece),  # type: ignore[attr-defined]
        "brier": float(value.brier),  # type: ignore[attr-defined]
        "logLoss": float(value.log_loss),  # type: ignore[attr-defined]
    }


def _signal_parquet(rows: Sequence[Mapping[str, object]]) -> bytes:
    schema = pa.schema(
        [
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("asOf", pa.string(), nullable=False),
            pa.field("signal", pa.string(), nullable=False),
            pa.field("confidence", pa.float64(), nullable=False),
            pa.field("modelVersion", pa.string(), nullable=False),
            pa.field("modelReportId", pa.string(), nullable=False),
        ]
    )
    sink = BytesIO()
    pq.write_table(  # type: ignore[no-untyped-call]
        pa.Table.from_pylist(list(rows), schema=schema),
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        write_statistics=True,
    )
    payload = sink.getvalue()
    if not payload or len(payload) > BATCH_MAX_BYTES:
        raise LightGbmContractError("signal batch Parquet exceeds bound")
    return payload


def _validate_signal_parquet(content: bytes) -> tuple[Mapping[str, object], ...]:
    parquet = pq.ParquetFile(  # type: ignore[no-untyped-call]
        BytesIO(content), page_checksum_verification=True
    )
    expected = ["symbol", "status", "asOf", "signal", "confidence", "modelVersion", "modelReportId"]
    if parquet.schema_arrow.names != expected or parquet.metadata.num_rows != 31:
        raise LightGbmContractError("signal batch schema or row count is invalid")
    table = parquet.read(use_threads=False)  # type: ignore[no-untyped-call]
    if table.num_rows != 31 or table.nbytes > BATCH_MAX_BYTES:
        raise LightGbmContractError("signal batch decoded bound is invalid")
    rows = tuple(table.to_pylist())
    symbols = tuple(str(row["symbol"]) for row in rows)
    if symbols != tuple(sorted(set(symbols))) or "132030" not in symbols:
        raise LightGbmContractError("signal batch membership is not sorted unique exact-31")
    for row in rows:
        confidence = row["confidence"]
        if (
            set(row) != set(expected)
            or row["status"] != "AVAILABLE"
            or row["signal"] not in {"SELL", "HOLD", "BUY"}
            or not isinstance(confidence, float)
            or not np.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
            or not IDENTIFIER.fullmatch(str(row["modelVersion"]))
            or not IDENTIFIER.fullmatch(str(row["modelReportId"]))
        ):
            raise LightGbmContractError("signal batch row violates AVAILABLE union")
    return rows


def _parse_canonical_object(content: bytes) -> dict[str, object]:
    try:
        value = parse_bounded_json_bytes(content, limits=_JSON_LIMITS)
    except BoundedJsonError as error:
        raise LightGbmContractError("production JSON is invalid") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise LightGbmContractError("production JSON must be a canonical object")
    return value


def _validate_release_semantics(
    manifest: Mapping[str, object], files: Mapping[str, bytes]
) -> None:
    try:
        model_text = files["model.txt"].decode("utf-8")
    except UnicodeDecodeError as error:
        raise LightGbmContractError("production model text is not UTF-8") from error
    if (
        "\x00" in model_text
        or not model_text.startswith("tree\n")
        or "objective=multiclass num_class:3" not in model_text
        or max((len(line) for line in model_text.splitlines()), default=0) > 4 * 1024 * 1024
    ):
        raise LightGbmContractError("production LightGBM text model is invalid")
    model_sha = hashlib.sha256(files["model.txt"]).hexdigest()
    if manifest["modelVersion"] != f"lgbm-v1-{model_sha[:12]}":
        raise LightGbmContractError("production modelVersion does not bind model text")
    from app.lightgbm.calibration import calibrator_from_mapping

    calibrator_from_mapping(_parse_canonical_object(files["calibrator.json"]))
    report = _parse_canonical_object(files["report.json"])
    report_without_id = dict(report)
    model_report_id = report_without_id.pop("modelReportId", None)
    report_semantic = hashlib.sha256(canonical_json_bytes(report_without_id)).hexdigest()
    if model_report_id != f"mrp-{report_semantic[:12]}" or manifest["modelReportId"] != model_report_id:
        raise LightGbmContractError("production modelReportId does not bind report semantics")
    qualification = _parse_canonical_object(files[QUALIFICATION_RECEIPT])
    expected_qualification = {
        "qualificationVersion", "featureManifestSha256", "trainingDatasetSha256",
        "finalTestAccessCount", "selectedGridIndex", "modelSha256", "calibratorSha256",
        "reportSha256", "gainImportanceSha256", "contributionReportSha256",
    }
    if set(qualification) != expected_qualification or qualification["qualificationVersion"] != "s5-production-qualification-receipt-v1":
        raise LightGbmContractError("production qualification receipt is invalid")
    bindings = {
        "featureManifestSha256": manifest["featureManifestSha256"],
        "trainingDatasetSha256": manifest["trainingDatasetSha256"],
        "modelSha256": model_sha,
        "calibratorSha256": hashlib.sha256(files["calibrator.json"]).hexdigest(),
        "reportSha256": hashlib.sha256(files["report.json"]).hexdigest(),
        "gainImportanceSha256": hashlib.sha256(files["gain-importance.json"]).hexdigest(),
        "contributionReportSha256": hashlib.sha256(files["contribution-report.json"]).hexdigest(),
    }
    if any(qualification.get(key) != value for key, value in bindings.items()):
        raise LightGbmContractError("production qualification file binding mismatch")
    if qualification.get("finalTestAccessCount") != 1:
        raise LightGbmContractError("production final test was not consumed exactly once")


def _qualification_key(
    *,
    feature_manifest_sha256: str,
    code_head: str,
    code_tree: str,
    uv_lock_sha256: str,
) -> str:
    preimage = {
        "qualificationInputVersion": "s5-production-qualification-input-v1",
        "featureManifestSha256": feature_manifest_sha256,
        "codeHead": code_head,
        "codeTree": code_tree,
        "uvLockSha256": uv_lock_sha256,
    }
    return hashlib.sha256(
        b"s5-production-qualification-input-v1\x00" + canonical_json_bytes(preimage)
    ).hexdigest()


def _write_qualification_reservation(
    *, parent: Path, qualification_key: str, selected_grid_index: int
) -> None:
    """untouched test를 열기 전에 one-way reservation을 owner-private 파일로 봉인한다."""

    require_private_root(parent)
    content = canonical_json_bytes(
        {
            "reservationVersion": QUALIFICATION_RESERVATION_VERSION,
            "qualificationKey": qualification_key,
            "selectedGridIndex": selected_grid_index,
            "finalTestMaxAccessCount": 1,
        }
    )
    _write_private_exact(
        parent,
        f"qualification-{qualification_key}.json",
        content,
        SMALL_JSON_MAX_BYTES,
    )


def _write_qualification_seal(
    *, parent: Path, qualification_key: str, files: Mapping[str, bytes]
) -> None:
    """final test 결과 전체를 한 파일로 먼저 원자 봉인해 release publish 실패를 재개 가능하게 한다."""

    expected_names = (*RELEASE_FILES, QUALIFICATION_RECEIPT, RELEASE_MANIFEST)
    if set(files) != set(expected_names):
        raise LightGbmContractError("qualification seal file set is not exact")
    metadata = {
        "sealVersion": "s5-production-qualification-seal-v1",
        "qualificationKey": qualification_key,
        "files": [
            {
                "name": name,
                "bytes": len(files[name]),
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name in expected_names
        ],
    }
    metadata_bytes = canonical_json_bytes(metadata)
    if len(metadata_bytes) > 1024 * 1024:
        raise LightGbmContractError("qualification seal metadata exceeds bound")
    content = (
        QUALIFICATION_SEAL_MAGIC
        + len(metadata_bytes).to_bytes(8, "big")
        + metadata_bytes
        + b"".join(files[name] for name in expected_names)
    )
    if len(content) > QUALIFICATION_SEAL_MAX_BYTES:
        raise LightGbmContractError("qualification seal exceeds bound")
    _write_private_exact(
        parent,
        f"qualification-{qualification_key}.bin",
        content,
        QUALIFICATION_SEAL_MAX_BYTES,
    )


def _read_qualification_seal(
    *, parent: Path, qualification_key: str
) -> Mapping[str, bytes] | None:
    path = parent / f"qualification-{qualification_key}.bin"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise LightGbmContractError("qualification seal path is invalid")
    content = _read_private(
        parent,
        f"qualification-{qualification_key}.bin",
        QUALIFICATION_SEAL_MAX_BYTES,
    )
    prefix = len(QUALIFICATION_SEAL_MAGIC)
    if len(content) < prefix + 8 or content[:prefix] != QUALIFICATION_SEAL_MAGIC:
        raise LightGbmContractError("qualification seal magic is invalid")
    metadata_size = int.from_bytes(content[prefix : prefix + 8], "big")
    if metadata_size <= 0 or metadata_size > 1024 * 1024:
        raise LightGbmContractError("qualification seal metadata size is invalid")
    metadata_start = prefix + 8
    metadata_end = metadata_start + metadata_size
    if metadata_end > len(content):
        raise LightGbmContractError("qualification seal is truncated")
    envelope = _parse_canonical_object(content[metadata_start:metadata_end])
    if set(envelope) != {"sealVersion", "qualificationKey", "files"} or (
        envelope["sealVersion"] != "s5-production-qualification-seal-v1"
        or envelope["qualificationKey"] != qualification_key
    ):
        raise LightGbmContractError("qualification seal binding is invalid")
    expected_names = (*RELEASE_FILES, QUALIFICATION_RECEIPT, RELEASE_MANIFEST)
    entries = envelope["files"]
    if not isinstance(entries, list) or len(entries) != len(expected_names):
        raise LightGbmContractError("qualification seal inventory is invalid")
    cursor = metadata_end
    files: dict[str, bytes] = {}
    for expected_name, entry in zip(expected_names, entries, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"name", "bytes", "sha256"}:
            raise LightGbmContractError("qualification seal entry is invalid")
        size = entry["bytes"]
        if (
            entry["name"] != expected_name
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(entry["sha256"], str)
            or not SHA256.fullmatch(entry["sha256"])
            or cursor + size > len(content)
        ):
            raise LightGbmContractError("qualification seal entry binding is invalid")
        payload = content[cursor : cursor + size]
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise LightGbmContractError("qualification seal payload digest mismatch")
        files[expected_name] = payload
        cursor += size
    if cursor != len(content):
        raise LightGbmContractError("qualification seal has trailing bytes")
    return files


def _resume_sealed_qualification(
    *,
    parent: Path,
    release_root: Path,
    qualification_key: str,
    feature_root: Path,
    feature_manifest_sha256: str,
) -> QualifiedProductionRelease | QualificationFailure | None:
    """sealed result가 있으면 final test를 다시 읽지 않고 release publish만 재개한다."""

    require_private_root(parent)
    sealed = _read_qualification_seal(parent=parent, qualification_key=qualification_key)
    if sealed is not None:
        _publish_sealed_release(release_root=release_root, files=sealed)
        manifest_sha = hashlib.sha256(sealed[RELEASE_MANIFEST]).hexdigest()
        return load_qualified_production_release(
            release_root=release_root,
            expected_release_manifest_sha256=manifest_sha,
            feature_root=feature_root,
            expected_feature_manifest_sha256=feature_manifest_sha256,
        )
    reservation = parent / f"qualification-{qualification_key}.json"
    try:
        reservation.lstat()
    except FileNotFoundError:
        return None
    reservation_bytes = _read_private(
        parent,
        f"qualification-{qualification_key}.json",
        SMALL_JSON_MAX_BYTES,
    )
    value = _parse_canonical_object(reservation_bytes)
    if (
        set(value)
        != {
            "reservationVersion",
            "qualificationKey",
            "selectedGridIndex",
            "finalTestMaxAccessCount",
        }
        or value["reservationVersion"] != QUALIFICATION_RESERVATION_VERSION
        or value["qualificationKey"] != qualification_key
        or value["selectedGridIndex"] not in {0, 1, 2, 3}
        or value["finalTestMaxAccessCount"] != 1
    ):
        raise LightGbmContractError("qualification reservation binding is invalid")
    return QualificationFailure("UNIDENTIFIABLE_OUTPUT", 1)


def _publish_sealed_release(*, release_root: Path, files: Mapping[str, bytes]) -> None:
    expected = {*RELEASE_FILES, QUALIFICATION_RECEIPT, RELEASE_MANIFEST}
    if set(files) != expected:
        raise LightGbmContractError("sealed release file set is invalid")
    _prepare_bundle_root(release_root, allowed=expected)
    for name in (*RELEASE_FILES, QUALIFICATION_RECEIPT):
        _write_private_exact(release_root, name, files[name], _file_cap(name))
    _write_private_exact(
        release_root,
        RELEASE_MANIFEST,
        files[RELEASE_MANIFEST],
        SMALL_JSON_MAX_BYTES,
    )


def _prepare_bundle_root(root: Path, *, allowed: set[str]) -> None:
    if not root.is_absolute():
        raise LightGbmContractError("production release root must be absolute")
    if root.exists():
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise LightGbmContractError("production release root is invalid")
        if stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != os.geteuid():
            raise LightGbmContractError("production release root must be owner-private")
        if not {entry.name for entry in root.iterdir()}.issubset(allowed):
            raise LightGbmContractError("production release root has unknown entries")
    else:
        parent = root.parent
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise LightGbmContractError("production release parent is invalid")
        root.mkdir(mode=0o700)


def _write_private_exact(root: Path, name: str, content: bytes, cap: int) -> None:
    try:
        result = write_approved_new_file(
            approved_root=root, relative_path=name, content=content, max_bytes=cap
        )
    except RagSafeIoError as error:
        existing = _read_private(root, name, cap)
        if existing != content:
            raise LightGbmContractError("production release resume content conflict") from error
        return
    descriptor = os.open(result.absolute_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _read_private(root: Path, name: str, cap: int) -> bytes:
    try:
        safe = read_approved_regular_file(approved_root=root, relative_path=name, max_bytes=cap)
    except RagSafeIoError as error:
        raise LightGbmContractError("production release file boundary is invalid") from error
    require_private_regular_file(
        safe.absolute_path, expected_device=safe.device, expected_inode=safe.inode
    )
    return safe.content


def _file_cap(name: str) -> int:
    if name == "model.txt":
        return MODEL_MAX_BYTES
    if name in {"calibrator.json", QUALIFICATION_RECEIPT, RELEASE_MANIFEST}:
        return SMALL_JSON_MAX_BYTES
    return JSON_MAX_BYTES


def _require_sha(value: str, field: str) -> None:
    if not SHA256.fullmatch(value):
        raise LightGbmContractError(f"{field} SHA-256 is invalid")


def _require_git_sha(value: str, field: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise LightGbmContractError(f"{field} Git SHA is invalid")
