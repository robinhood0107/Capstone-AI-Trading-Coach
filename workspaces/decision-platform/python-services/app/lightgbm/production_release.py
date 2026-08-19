"""S5.6B production qualification, immutable model release와 exact signal batch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
import hashlib
from io import BytesIO
import math
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
from app.lightgbm.diagnostics import (
    QUALIFICATION_REPORT_OUTCOME,
    record_report,
)
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
    macro_timing_sensitivity_metrics,
    macro_timing_sensitivity_verdict,
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
BATCH_ROW_COUNT = 31
BATCH_READ_ROWS = 1
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SIGNAL_BATCH_SCHEMA = pa.schema(
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
SIGNAL_BATCH_COLUMNS = SIGNAL_BATCH_SCHEMA.names
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
    final_blocks = _final_arrays(rows, plan.final)
    projected_final_rows: list[_TrainingRows] = []

    def project_final_test() -> tuple[np.ndarray, np.ndarray]:
        final_rows = _rows_for_sessions(rows, plan.final.evaluation_sessions)
        projected_final_rows.append(final_rows)
        return final_rows.features, final_rows.labels

    loader = UntouchedTestLoader.deferred(project_final_test)

    delayed_table = build_production_feature_table(
        packet=packet,
        acquisition=materialization.acquisition,
        macro_delay_sessions=1,
    )
    delayed_rows = _training_rows_from_table(delayed_table, labels)
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
            primary_sensitivity, delayed_evaluation = _intersect_rows(
                evaluation_rows,
                delayed_rows,
                name=f"macro sensitivity {split.name}",
            )
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
                fold_run.model, fold_run.calibrator, primary_sensitivity.features
            )
            _, delayed_probabilities = calibrated_probabilities(
                fold_run.model, fold_run.calibrator, delayed_evaluation.features
            )
            macro_metrics = macro_timing_sensitivity_metrics(
                primary_probabilities=primary_probabilities,
                delayed_probabilities=delayed_probabilities,
                labels=primary_sensitivity.labels.tolist(),
                primary_row_count=len(evaluation_rows.labels),
            )
            macro_pass = macro_timing_sensitivity_verdict(macro_metrics)
            passed = fold_run.evaluation.passed and corporate_fold_pass and macro_pass
            updated_evaluation = replace(fold_run.evaluation, passed=passed)
            updated_folds.append(replace(fold_run, evaluation=updated_evaluation))
            candidate_sensitivity.append(
                {
                    "fold": split.name,
                    "basePass": fold_run.evaluation.passed,
                    "corporateActionPass": corporate_fold_pass,
                    "macroTimingPass": macro_pass,
                    "rowCount": len(evaluation_rows.labels),
                    "macroRowCount": len(primary_sensitivity.labels),
                    "eventFreeRowCount": len(event_indices),
                    "calibratedEce": _rounded(fold_run.evaluation.calibrated.ece),
                    "calibratedBrier": _rounded(fold_run.evaluation.calibrated.brier),
                    "calibratedLogLoss": _rounded(fold_run.evaluation.calibrated.log_loss),
                    "rawBrier": _rounded(fold_run.evaluation.raw.brier),
                    "rawLogLoss": _rounded(fold_run.evaluation.raw.log_loss),
                    "macro": {
                        key: _rounded(value) for key, value in sorted(macro_metrics.items())
                    },
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
        # 코드 이름만 남기면 모델 gate 실패와 계산 결함을 구분할 수 없다. 어떤 후보의 어떤 fold가
        # 어느 조건에서 걸렸는지 측정값과 함께 남긴다.
        _record_qualification_diagnostic(
            source_root=release_root.parent / "source",
            reason="CALIBRATION_FAILED",
            report=sensitivity_report,
        )
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
        _record_qualification_diagnostic(
            source_root=release_root.parent / "source",
            reason="FINAL_TEST_CALIBRATION_FAILED",
            report=[
                {
                    "gridIndex": final_run.candidate.grid_index,
                    "folds": [
                        {
                            "fold": "FINAL",
                            "basePass": final_run.evaluation.passed,
                            "calibratedEce": _rounded(final_run.evaluation.calibrated.ece),
                            "calibratedBrier": _rounded(
                                final_run.evaluation.calibrated.brier
                            ),
                            "calibratedLogLoss": _rounded(
                                final_run.evaluation.calibrated.log_loss
                            ),
                            "rawBrier": _rounded(final_run.evaluation.raw.brier),
                            "rawLogLoss": _rounded(final_run.evaluation.raw.log_loss),
                        }
                    ],
                }
            ],
        )
        return QualificationFailure("CALIBRATION_FAILED", loader.access_count)
    if len(projected_final_rows) != 1:
        raise LightGbmContractError("untouched final test projection was not consumed exactly once")
    final_rows = projected_final_rows[0]

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
        "primaryEvaluation": [
            {
                "gridIndex": run.evaluation.candidate.grid_index,
                "folds": [
                    {
                        "fold": split.name,
                        "raw": _metrics(fold.evaluation.raw),
                        "calibrated": _metrics(fold.evaluation.calibrated),
                        "passed": fold.evaluation.passed,
                    }
                    for fold, split in zip(run.folds, plan.folds, strict=True)
                ],
            }
            for run in candidate_runs
        ],
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
    batch_purpose: str = "DAILY",
) -> ValidatedSignalBatch:
    """활성 release로 exact 31 membership의 AVAILABLE-only immutable daily batch를 만든다."""

    if as_of.tzinfo is None:
        raise LightGbmContractError("signal batch asOf must be timezone aware")
    if batch_purpose not in {"DAILY", "ROLLBACK"}:
        raise LightGbmContractError("signal batch purpose is invalid")
    symbols = tuple(sorted(inference_universe.symbols))
    if len(symbols) != 31 or len(set(symbols)) != 31 or "132030" not in symbols:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: inference universe must be exact 31")
    table_rows = inference_table.to_pylist()
    if (
        len(table_rows) != 31
        or any(row.get("sessionDate") != session_date for row in table_rows)
        or any(str(row.get("symbol")) not in symbols for row in table_rows)
    ):
        raise DatasetUnavailable(
            "DATASET_UNAVAILABLE: inference table must contain exact current 31 rows"
        )
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
    members_sha = hashlib.sha256(canonical_json_bytes(rows)).hexdigest()
    parquet = _signal_parquet(rows)
    membership_sha = hashlib.sha256(
        b"s5-inference-universe-v1\x00" + canonical_json_bytes(list(symbols))
    ).hexdigest()
    universe_release_id = f"sur-{membership_sha[:12]}"
    preimage = {
        "batchVersion": "s5-signal-batch-v1",
        "batchPurpose": batch_purpose,
        "modelReleaseId": release.model_release_id,
        "universeReleaseId": universe_release_id,
        "membershipSha256": membership_sha,
        "sessionDate": session_date.isoformat(),
        "asOf": as_of_text,
        "timeframe": "1d",
        "rowCount": 31,
        "membersSha256": members_sha,
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


def validate_qualification_bindings(
    *, code_head: str, code_tree: str, uv_lock_sha256: str
) -> None:
    """provider handoff 전에 static code/dependency trust anchors의 형식을 닫는다."""

    _require_git_sha(code_head, "code HEAD")
    _require_git_sha(code_tree, "code tree")
    _require_sha(uv_lock_sha256, "uv.lock")


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
        "batchVersion", "batchPurpose", "signalBatchId", "modelReleaseId", "universeReleaseId",
        "membershipSha256", "sessionDate", "asOf", "timeframe", "rowCount",
        "membersSha256", "parquetFile", "parquetSha256", "fixture", "provenanceClass",
        "semanticSha256",
    }
    if set(manifest) != expected:
        raise LightGbmContractError("batch manifest field set is not closed")
    if (
        manifest["batchVersion"] != "s5-signal-batch-v1"
        or manifest["batchPurpose"] not in {"DAILY", "ROLLBACK"}
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
    if manifest["membersSha256"] != hashlib.sha256(
        canonical_json_bytes(list(rows))
    ).hexdigest():
        raise LightGbmContractError("batch member projection digest mismatch")
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



QUALIFICATION_DIAGNOSTIC_VERSION = "s5-qualification-diagnostic-v1"


def _rounded(value: float) -> float:
    """비교 가능한 자리수로만 남긴다. 실수 표현 차이가 원장을 흔들지 않게 한다."""

    return round(float(value), 6)


def _record_qualification_diagnostic(
    *, source_root: Path, reason: str, report: Sequence[Mapping[str, object]]
) -> None:
    """어떤 후보의 어떤 fold가 어느 조건에서 걸렸는지 원장에 한 줄씩 남긴다.

    모델 gate 판정은 계약 위반도 증거 결손도 아니다. 재검증 루프가 읽는 보고이므로 실패 분류를
    쓰지 않는다. 우리 모델의 집계 지표만 담고 provider 응답은 담지 않는다.
    """

    for candidate in report:
        folds = candidate.get("folds")
        if not isinstance(folds, list):
            continue
        for fold in folds:
            if not isinstance(fold, dict):
                continue
            measured: dict[str, object] = {
                "reason": reason,
                "diagnosticVersion": QUALIFICATION_DIAGNOSTIC_VERSION,
                "gridIndex": candidate.get("gridIndex", -1),
            }
            for key, value in fold.items():
                if key == "macro" and isinstance(value, Mapping):
                    for macro_key, macro_value in value.items():
                        measured[f"macro{macro_key[:1].upper()}{macro_key[1:]}"] = (
                            macro_value
                        )
                    continue
                measured[key] = value
            record_report(
                source_root=source_root,
                phase="QUALIFYING",
                report=QUALIFICATION_REPORT_OUTCOME,
                measured=measured,
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
    """그 날짜의 corporate action 증거만 본다.

    mod_yn은 반환된 가격이 수정주가인지를 나타내며 요청한 조정 모드에서 따라온다. 원주가 요청에서는
    모든 행이 N이므로 corporate action 판정에 쓰면 아무 것도 걸러내지 못하거나 전부 걸러낸다.
    """

    return (
        row.flng_cls_code not in {"", "00"}
        or row.prtt_rate != 0
        or bool(row.revl_issu_reas)
    )


def _intersect_rows(
    primary: _TrainingRows,
    delayed: _TrainingRows,
    *,
    name: str,
) -> tuple[_TrainingRows, _TrainingRows]:
    """Primary 순서를 유지한 exact key intersection이 98% 이상일 때만 sensitivity를 연다."""

    delayed_index = {key: index for index, key in enumerate(delayed.keys)}
    if len(delayed_index) != len(delayed.keys):
        raise LightGbmContractError(f"{name} contains duplicate delayed keys")
    primary_indices: list[int] = []
    delayed_indices: list[int] = []
    for index, key in enumerate(primary.keys):
        other = delayed_index.get(key)
        if other is not None:
            primary_indices.append(index)
            delayed_indices.append(other)
    if not primary.keys or len(primary_indices) < int(np.ceil(len(primary.keys) * 0.98)):
        raise DatasetUnavailable(f"UNIDENTIFIABLE_OUTPUT: {name} coverage is below 98%")

    def aligned(rows: _TrainingRows, indices: list[int]) -> _TrainingRows:
        return _TrainingRows(
            tuple(rows.keys[index] for index in indices),
            rows.features[indices],
            rows.labels[indices],
            rows.forward_returns[indices],
        )

    left = aligned(primary, primary_indices)
    right = aligned(delayed, delayed_indices)
    if left.keys != right.keys or not np.array_equal(left.labels, right.labels):
        raise LightGbmContractError(f"{name} intersection alignment is invalid")
    return left, right


def _metrics(value: object) -> dict[str, float]:
    return {
        "ece": float(value.ece),  # type: ignore[attr-defined]
        "brier": float(value.brier),  # type: ignore[attr-defined]
        "logLoss": float(value.log_loss),  # type: ignore[attr-defined]
    }


def _signal_parquet(rows: Sequence[Mapping[str, object]]) -> bytes:
    sink = BytesIO()
    pq.write_table(  # type: ignore[no-untyped-call]
        pa.Table.from_pylist(list(rows), schema=SIGNAL_BATCH_SCHEMA),
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
        BytesIO(content),
        thrift_string_size_limit=SMALL_JSON_MAX_BYTES,
        thrift_container_size_limit=1_024,
        page_checksum_verification=True,
    )
    metadata = parquet.metadata
    if (
        metadata.num_rows != BATCH_ROW_COUNT
        or metadata.num_columns != len(SIGNAL_BATCH_COLUMNS)
        or parquet.schema_arrow != SIGNAL_BATCH_SCHEMA
    ):
        raise LightGbmContractError("signal batch schema or row count is invalid")
    declared_decoded = sum(
        metadata.row_group(group).column(column).total_uncompressed_size
        for group in range(metadata.num_row_groups)
        for column in range(metadata.num_columns)
    )
    if declared_decoded > BATCH_MAX_BYTES:
        raise LightGbmContractError("signal batch declared decoded bound is invalid")
    rows: list[Mapping[str, object]] = []
    decoded = 0
    for batch in parquet.iter_batches(  # type: ignore[no-untyped-call]
        batch_size=BATCH_READ_ROWS,
        use_threads=False,
    ):
        decoded += batch.nbytes
        if decoded > BATCH_MAX_BYTES or len(rows) + batch.num_rows > BATCH_ROW_COUNT:
            raise LightGbmContractError("signal batch actual decoded bound is invalid")
        rows.extend(batch.to_pylist())
    if len(rows) != BATCH_ROW_COUNT:
        raise LightGbmContractError("signal batch schema or row count is invalid")
    rows_tuple = tuple(rows)
    symbols = tuple(str(row["symbol"]) for row in rows_tuple)
    if (
        symbols != tuple(sorted(set(symbols)))
        or "132030" not in symbols
        or any(re.fullmatch(r"[0-9]{6}", symbol) is None for symbol in symbols)
    ):
        raise LightGbmContractError("signal batch membership is not sorted unique exact-31")
    model_versions = {str(row["modelVersion"]) for row in rows}
    model_report_ids = {str(row["modelReportId"]) for row in rows}
    if (
        len(model_versions) != 1
        or len(model_report_ids) != 1
        or re.fullmatch(r"lgbm-v1-[0-9a-f]{12}", next(iter(model_versions))) is None
        or re.fullmatch(r"mrp-[0-9a-f]{12}", next(iter(model_report_ids))) is None
    ):
        raise LightGbmContractError("signal batch model binding is invalid")
    for row in rows_tuple:
        confidence = row["confidence"]
        if (
            set(row) != set(SIGNAL_BATCH_COLUMNS)
            or row["status"] != "AVAILABLE"
            or row["signal"] not in {"SELL", "HOLD", "BUY"}
            or not isinstance(confidence, float)
            or not np.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
            or not IDENTIFIER.fullmatch(str(row["modelVersion"]))
            or not IDENTIFIER.fullmatch(str(row["modelReportId"]))
        ):
            raise LightGbmContractError("signal batch row violates AVAILABLE union")
    return rows_tuple


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
    selected_grid_index = _validate_qualification_report(report, model_text)
    _validate_gain_report(_parse_canonical_object(files["gain-importance.json"]))
    _validate_contribution_report(_parse_canonical_object(files["contribution-report.json"]))
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
    if qualification.get("selectedGridIndex") != selected_grid_index:
        raise LightGbmContractError("production selected candidate binding mismatch")


def _validate_qualification_report(report: Mapping[str, object], model_text: str) -> int:
    expected = {
        "reportVersion",
        "historicalMode",
        "temporalQuality",
        "strictProviderPITClaim",
        "selectedCandidate",
        "primaryEvaluation",
        "sensitivity",
        "corporateActionGlobalPass",
        "finalTest",
        "costReport",
        "modelReportId",
    }
    if set(report) != expected or (
        report["reportVersion"] != "s5-production-qualification-v1"
        or report["historicalMode"] != "HISTORICAL_REPLAY_RECONSTRUCTED"
        or report["temporalQuality"] != "RECONSTRUCTED_FIXED_LAG"
        or report["strictProviderPITClaim"] is not False
        or report["corporateActionGlobalPass"] is not True
    ):
        raise LightGbmContractError("production qualification report authority is invalid")
    candidate = _mapping_with_keys(
        report["selectedCandidate"],
        {"gridIndex", "numLeaves", "classWeight"},
        "selected candidate",
    )
    grid = ((15, "NONE"), (15, "CAPPED_BALANCED"), (31, "NONE"), (31, "CAPPED_BALANCED"))
    grid_index = candidate["gridIndex"]
    if (
        not isinstance(grid_index, int)
        or isinstance(grid_index, bool)
        or not 0 <= grid_index < len(grid)
        or (candidate["numLeaves"], candidate["classWeight"]) != grid[grid_index]
        or f"[num_leaves: {candidate['numLeaves']}]" not in model_text
    ):
        raise LightGbmContractError("production selected candidate is invalid")
    sensitivity = report["sensitivity"]
    if not isinstance(sensitivity, list) or len(sensitivity) != 4:
        raise LightGbmContractError("production sensitivity candidate set is invalid")
    selected_sensitivity: Mapping[str, object] | None = None
    sensitivity_pass_by_index: dict[int, bool] = {}
    for expected_index, raw_candidate in enumerate(sensitivity):
        item = _mapping_with_keys(raw_candidate, {"gridIndex", "folds"}, "sensitivity candidate")
        if item["gridIndex"] != expected_index:
            raise LightGbmContractError("production sensitivity grid order is invalid")
        folds = item["folds"]
        if not isinstance(folds, list) or len(folds) != 3:
            raise LightGbmContractError("production sensitivity fold set is invalid")
        for fold_index, raw_fold in enumerate(folds, start=1):
            fold = _mapping_with_keys(
                raw_fold,
                {
                    "fold",
                    "corporateActionPass",
                    "macroTimingPass",
                    "rowCount",
                    "macroRowCount",
                    "eventFreeRowCount",
                },
                "sensitivity fold",
            )
            if fold["fold"] != f"fold-{fold_index}":
                raise LightGbmContractError("production sensitivity fold order is invalid")
            row_count = _positive_int(fold["rowCount"], "sensitivity rowCount")
            macro_count = _positive_int(fold["macroRowCount"], "sensitivity macroRowCount")
            event_count = _positive_int(
                fold["eventFreeRowCount"], "sensitivity eventFreeRowCount"
            )
            if macro_count > row_count or macro_count < math.ceil(row_count * 0.98):
                raise LightGbmContractError("production macro sensitivity coverage is invalid")
            if event_count > row_count:
                raise LightGbmContractError("production corporate sensitivity coverage is invalid")
            if not isinstance(fold["corporateActionPass"], bool) or not isinstance(
                fold["macroTimingPass"], bool
            ):
                raise LightGbmContractError("production sensitivity PASS type is invalid")
        sensitivity_pass_by_index[expected_index] = all(
            isinstance(fold, dict)
            and fold.get("corporateActionPass") is True
            and fold.get("macroTimingPass") is True
            for fold in folds
        )
        if expected_index == grid_index:
            selected_sensitivity = item
    assert selected_sensitivity is not None
    selected_folds = selected_sensitivity["folds"]
    assert isinstance(selected_folds, list)
    if any(
        fold["corporateActionPass"] is not True or fold["macroTimingPass"] is not True
        for fold in selected_folds
        if isinstance(fold, dict)
    ):
        raise LightGbmContractError("production selected candidate sensitivity did not pass")
    primary = report["primaryEvaluation"]
    if not isinstance(primary, list) or len(primary) != 4:
        raise LightGbmContractError("production primary evaluation candidate set is invalid")
    selected_primary: Mapping[str, object] | None = None
    selection_keys: list[tuple[float, float, float, int]] = []
    for expected_index, raw_candidate in enumerate(primary):
        item = _mapping_with_keys(raw_candidate, {"gridIndex", "folds"}, "primary candidate")
        if item["gridIndex"] != expected_index:
            raise LightGbmContractError("production primary evaluation grid order is invalid")
        folds = item["folds"]
        if not isinstance(folds, list) or len(folds) != 3:
            raise LightGbmContractError("production primary evaluation fold set is invalid")
        calibrated_values: list[dict[str, float]] = []
        primary_pass = True
        for fold_index, raw_fold in enumerate(folds, start=1):
            fold = _mapping_with_keys(
                raw_fold,
                {"fold", "raw", "calibrated", "passed"},
                "primary evaluation fold",
            )
            raw_metrics = _validate_metrics(fold["raw"], "primary raw metrics")
            calibrated_metrics = _validate_metrics(
                fold["calibrated"], "primary calibrated metrics"
            )
            expected_pass = (
                calibrated_metrics["ece"] <= 0.05
                and calibrated_metrics["brier"] <= raw_metrics["brier"] + 0.005
                and calibrated_metrics["logLoss"] <= raw_metrics["logLoss"] + 0.01
            )
            if fold["fold"] != f"fold-{fold_index}" or fold["passed"] is not expected_pass:
                raise LightGbmContractError("production primary evaluation PASS is invalid")
            primary_pass = primary_pass and expected_pass
            calibrated_values.append(calibrated_metrics)
        if primary_pass and sensitivity_pass_by_index[expected_index]:
            selection_keys.append(
                (
                    sum(value["logLoss"] for value in calibrated_values) / 3,
                    sum(value["brier"] for value in calibrated_values) / 3,
                    sum(value["ece"] for value in calibrated_values) / 3,
                    expected_index,
                )
            )
        if expected_index == grid_index:
            selected_primary = item
    assert selected_primary is not None
    selected_primary_folds = selected_primary["folds"]
    assert isinstance(selected_primary_folds, list)
    if any(
        fold.get("passed") is not True
        for fold in selected_primary_folds
        if isinstance(fold, dict)
    ):
        raise LightGbmContractError("production selected candidate primary folds did not pass")
    if not selection_keys or min(selection_keys)[3] != grid_index:
        raise LightGbmContractError("production selected candidate does not match locked ranking")
    final_test = _mapping_with_keys(
        report["finalTest"],
        {"accessCount", "rowCount", "raw", "calibrated", "passed"},
        "final test",
    )
    if (
        final_test["accessCount"] != 1
        or _positive_int(final_test["rowCount"], "final test rowCount") < 1
        or final_test["passed"] is not True
    ):
        raise LightGbmContractError("production final test qualification is invalid")
    raw = _validate_metrics(final_test["raw"], "final raw metrics")
    calibrated = _validate_metrics(final_test["calibrated"], "final calibrated metrics")
    if not (
        calibrated["ece"] <= 0.05
        and calibrated["brier"] <= raw["brier"] + 0.005
        and calibrated["logLoss"] <= raw["logLoss"] + 0.01
    ):
        raise LightGbmContractError("production final calibration gate did not pass")
    _validate_cost_report(report["costReport"])
    return grid_index


def _validate_gain_report(value: Mapping[str, object]) -> None:
    report = _mapping_with_keys(
        value, {"reportVersion", "featureNames", "gain"}, "gain importance"
    )
    if report["reportVersion"] != "s5-gain-importance-v1" or report[
        "featureNames"
    ] != list(CORE_FEATURE_COLUMNS):
        raise LightGbmContractError("production gain importance contract is invalid")
    gain = _mapping_with_keys(report["gain"], set(CORE_FEATURE_COLUMNS), "gain map")
    if any(_finite_number(gain[name], f"gain {name}") < 0 for name in CORE_FEATURE_COLUMNS):
        raise LightGbmContractError("production gain importance is negative")


def _validate_contribution_report(value: Mapping[str, object]) -> None:
    report = _mapping_with_keys(
        value,
        {"reportVersion", "reportOnly", "featureNames", "rowCount", "rows"},
        "contribution report",
    )
    rows = report["rows"]
    row_count = _positive_int(report["rowCount"], "contribution rowCount")
    if (
        report["reportVersion"] != "s5-pred-contrib-v1"
        or report["reportOnly"] is not True
        or report["featureNames"] != list(CORE_FEATURE_COLUMNS)
        or not isinstance(rows, list)
        or not 1 <= row_count <= 500
        or len(rows) != row_count
    ):
        raise LightGbmContractError("production contribution report contract is invalid")
    seen: set[str] = set()
    for raw_row in rows:
        row = _mapping_with_keys(raw_row, {"rowKeyHash", "classes"}, "contribution row")
        row_hash = row["rowKeyHash"]
        classes = row["classes"]
        if not isinstance(row_hash, str) or not SHA256.fullmatch(row_hash) or row_hash in seen:
            raise LightGbmContractError("production contribution row identity is invalid")
        seen.add(row_hash)
        if not isinstance(classes, list) or len(classes) != 3:
            raise LightGbmContractError("production contribution class set is invalid")
        for class_index, raw_class in enumerate(classes):
            item = _mapping_with_keys(
                raw_class,
                {"classIndex", "bias", "contributions", "rawMargin"},
                "contribution class",
            )
            contributions = item["contributions"]
            if item["classIndex"] != class_index or not isinstance(contributions, list) or len(
                contributions
            ) != len(CORE_FEATURE_COLUMNS):
                raise LightGbmContractError("production contribution class contract is invalid")
            bias = _finite_number(item["bias"], "contribution bias")
            margin = _finite_number(item["rawMargin"], "contribution raw margin")
            values = [_finite_number(number, "contribution value") for number in contributions]
            if not math.isclose(bias + sum(values), margin, rel_tol=0.0, abs_tol=1e-6):
                raise LightGbmContractError("production contribution additivity is invalid")


def _validate_cost_report(value: object) -> None:
    report = _mapping_with_keys(
        value,
        {
            "directionalEdgeOnly",
            "costSensitivityBps",
            "meanEdge35Bps",
            "logLoss",
            "brier",
            "ece",
            "macroF1",
            "confusionMatrix",
            "alwaysHold",
            "trainOnlyPrior",
            "fakeArtifactsIncluded",
        },
        "cost report",
    )
    if report["directionalEdgeOnly"] is not True or report["fakeArtifactsIncluded"] is not False:
        raise LightGbmContractError("production cost report authority is invalid")
    baseline_keys = {
        "costSensitivityBps",
        "meanEdge35Bps",
        "logLoss",
        "brier",
        "ece",
        "macroF1",
        "confusionMatrix",
    }
    _validate_baseline_metrics(
        {key: report[key] for key in baseline_keys},
        include_probabilities=False,
        name="cost report",
    )
    _validate_baseline_metrics(report["alwaysHold"], include_probabilities=False, name="always HOLD")
    _validate_baseline_metrics(
        report["trainOnlyPrior"], include_probabilities=True, name="train-only prior"
    )


def _validate_baseline_metrics(value: object, *, include_probabilities: bool, name: str) -> None:
    keys = {
        "costSensitivityBps",
        "meanEdge35Bps",
        "logLoss",
        "brier",
        "ece",
        "macroF1",
        "confusionMatrix",
    }
    if include_probabilities:
        keys.add("probabilities")
    report = _mapping_with_keys(value, keys, name)
    sensitivity = _mapping_with_keys(
        report["costSensitivityBps"], {"25", "30", "35"}, f"{name} cost sensitivity"
    )
    for field in ("25", "30", "35"):
        _finite_number(sensitivity[field], f"{name} cost {field}")
    if report["meanEdge35Bps"] != sensitivity["35"]:
        raise LightGbmContractError(f"{name} mean edge binding is invalid")
    for field in ("logLoss", "brier", "ece", "macroF1"):
        _finite_number(report[field], f"{name} {field}")
    matrix = report["confusionMatrix"]
    if not isinstance(matrix, list) or len(matrix) != 3 or any(
        not isinstance(row, list)
        or len(row) != 3
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in row)
        for row in matrix
    ):
        raise LightGbmContractError(f"{name} confusion matrix is invalid")
    if include_probabilities:
        probabilities = report["probabilities"]
        if not isinstance(probabilities, list) or len(probabilities) != 3:
            raise LightGbmContractError(f"{name} probabilities are invalid")
        parsed = [_finite_number(item, f"{name} probability") for item in probabilities]
        if any(item < 0 for item in parsed) or not math.isclose(
            sum(parsed), 1.0, rel_tol=0.0, abs_tol=1e-12
        ):
            raise LightGbmContractError(f"{name} probabilities are invalid")


def _validate_metrics(value: object, name: str) -> dict[str, float]:
    metrics = _mapping_with_keys(value, {"ece", "brier", "logLoss"}, name)
    parsed = {field: _finite_number(metrics[field], f"{name} {field}") for field in metrics}
    if not (0 <= parsed["ece"] <= 1 and 0 <= parsed["brier"] <= 2 and parsed["logLoss"] >= 0):
        raise LightGbmContractError(f"{name} range is invalid")
    return parsed


def _mapping_with_keys(value: object, keys: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise LightGbmContractError(f"production {name} field set is not closed")
    return value


def _positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LightGbmContractError(f"production {name} is invalid")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LightGbmContractError(f"production {name} is invalid")
    return float(value)


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
