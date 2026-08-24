"""S5.6B 역할별 SECURITY DEFINER function만 호출하는 production DB adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.production_release import (
    ValidatedProductionRelease,
    ValidatedSignalBatch,
)


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...]) -> object: ...
    def fetchone(self) -> tuple[object, ...] | None: ...
    def __enter__(self) -> Cursor: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class StagedProductionArtifacts:
    """writer transaction에서 model release와 batch가 함께 stage된 결과."""

    release_outcome: str
    batch_outcome: str


def stage_release_and_batch(
    connection: Connection,
    *,
    release: ValidatedProductionRelease,
    batch: ValidatedSignalBatch,
) -> StagedProductionArtifacts:
    """writer role의 두 bounded function을 한 transaction으로 호출하고 어느 실패든 rollback한다."""

    manifest = release.manifest
    batch_manifest = batch.manifest
    if batch_manifest["modelReleaseId"] != manifest["modelReleaseId"]:
        raise LightGbmContractError("production batch is bound to a different model release")
    members = canonical_json_bytes(list(batch.rows)).decode("utf-8")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stage_signal_model_release(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    release.manifest_sha256,
                    canonical_json_bytes(dict(manifest)).decode("utf-8"),
                    manifest["modelReleaseId"],
                    manifest["modelVersion"],
                    manifest["modelReportId"],
                    manifest["featureManifestSha256"],
                    manifest["sourceBundleSetSha256"],
                    manifest["trainingDatasetSha256"],
                    manifest["codeHead"],
                    manifest["codeTree"],
                    manifest["uvLockSha256"],
                ),
            )
            release_row = cursor.fetchone()
            cursor.execute(
                "SELECT stage_signal_batch(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    batch.manifest_sha256,
                    canonical_json_bytes(dict(batch_manifest)).decode("utf-8"),
                    batch_manifest["signalBatchId"],
                    batch_manifest["modelReleaseId"],
                    batch_manifest["universeReleaseId"],
                    batch_manifest["membershipSha256"],
                    batch_manifest["sessionDate"],
                    batch_manifest["asOf"],
                    members,
                ),
            )
            batch_row = cursor.fetchone()
        if release_row is None or batch_row is None:
            raise LightGbmContractError("production DB staging returned no receipt")
        release_outcome = str(release_row[0])
        batch_outcome = str(batch_row[0])
        if release_outcome not in {"INSERTED", "REPLAYED"} or batch_outcome not in {
            "INSERTED",
            "REPLAYED",
        }:
            raise LightGbmContractError("production DB staging returned an unknown outcome")
        connection.commit()
        return StagedProductionArtifacts(release_outcome, batch_outcome)
    except Exception:
        connection.rollback()
        raise


def activate_release_and_batch(
    connection: Connection,
    *,
    model_release_id: str,
    signal_batch_id: str,
    expected_model_release_id: str | None,
    expected_signal_batch_id: str | None,
    release_manifest_sha256: str,
    batch_manifest_sha256: str,
    rollback: bool = False,
) -> int:
    """admin role의 expected-current CAS 하나로 model과 initial/latest batch를 함께 전환한다."""

    reason = "MANUAL_ROLLBACK" if rollback else "MANUAL_ACTIVATION"
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT activate_signal_model_and_batch(%s,%s,%s,%s,%s,%s,%s)",
                (
                    model_release_id,
                    signal_batch_id,
                    expected_model_release_id or "",
                    expected_signal_batch_id or "",
                    release_manifest_sha256,
                    batch_manifest_sha256,
                    reason,
                ),
            )
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], int) or row[0] <= 0:
            raise LightGbmContractError("production activation returned no generation")
        connection.commit()
        return row[0]
    except Exception:
        connection.rollback()
        raise


def publish_daily_batch(
    connection: Connection,
    *,
    signal_batch_id: str,
    expected_signal_batch_id: str,
    batch_manifest_sha256: str,
) -> int:
    """scheduler role은 active model의 complete batch만 CAS publish하며 model pointer는 바꾸지 않는다."""

    return _single_generation_call(
        connection,
        "SELECT publish_active_signal_batch(%s,%s,%s)",
        (signal_batch_id, expected_signal_batch_id, batch_manifest_sha256),
    )


def suspend_for_drift(
    connection: Connection, *, model_release_id: str, evidence_sha256: str
) -> None:
    """scheduler/admin이 evidence-bound ARTIFACT_DRIFT transition만 append한다."""

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT suspend_signal_model_for_drift(%s,%s)",
                (model_release_id, evidence_sha256),
            )
            cursor.fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _single_generation_call(connection: Connection, query: str, params: tuple[object, ...]) -> int:
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], int) or row[0] <= 0:
            raise LightGbmContractError("production pointer call returned no generation")
        connection.commit()
        return row[0]
    except Exception:
        connection.rollback()
        raise
