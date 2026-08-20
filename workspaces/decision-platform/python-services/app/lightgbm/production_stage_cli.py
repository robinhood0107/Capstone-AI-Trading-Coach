"""검증 완료 S5.6B release/batch를 역할별 DB function으로 stage·수동 활성화한다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import psycopg

from app.lightgbm.private_root import require_private_root
from app.lightgbm.production_db import (
    Connection,
    activate_release_and_batch,
    stage_release_and_batch,
)
from app.lightgbm.production_release import (
    validate_production_model_release,
    validate_production_signal_batch,
)


def main() -> int:
    """CLI path 인자 없이 server root와 manifest trust anchor, 분리 role DSN만 사용한다."""

    # LightGBM은 연구 전용이다. 과거 release 검증 코드는 재현 연구를 위해 보존하지만
    # DB stage/activation/rollback 연결은 credential이나 artifact를 읽기 전에 닫는다.
    print("S5_PRODUCTION_STAGE=RESEARCH_ONLY")
    return 2

    # Kept as unreachable historical production implementation for audit reproduction.
    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    packet_sha = os.environ.get("S5_BOOTSTRAP_PACKET_SHA256", "")
    release_sha = os.environ.get("S5_RELEASE_MANIFEST_SHA256", "")
    batch_sha = os.environ.get("S5_BATCH_MANIFEST_SHA256", "")
    writer_dsn = os.environ.get("S5_SIGNAL_WRITER_DSN", "")
    if not all((root_value, packet_sha, release_sha, batch_sha, writer_dsn)):
        print("S5_PRODUCTION_STAGE=AUTHORITY_UNAVAILABLE")
        return 2
    try:
        root = Path(root_value)
        require_private_root(root)
        run_root = root / f"run-{packet_sha}"
        action = _manual_action()
        release = validate_production_model_release(
            approved_root=run_root / "release",
            expected_manifest_sha256=release_sha,
        )
        batch_root = run_root / "batch"
        if action == "ROLLBACK":
            rollback_state_sha = os.environ.get("S5_ROLLBACK_STATE_SHA256", "")
            if len(rollback_state_sha) != 64 or any(
                character not in "0123456789abcdef" for character in rollback_state_sha
            ):
                print("S5_PRODUCTION_STAGE=ROLLBACK_AUTHORITY_UNAVAILABLE")
                return 2
            batch_root = (
                root
                / "daily"
                / f"rollback-{rollback_state_sha}-{release_sha[:12]}"
                / "batch"
            )
        batch = validate_production_signal_batch(
            approved_root=batch_root,
            expected_manifest_sha256=batch_sha,
        )
        if (action == "ROLLBACK") != (batch.manifest["batchPurpose"] == "ROLLBACK"):
            print("S5_PRODUCTION_STAGE=BATCH_PURPOSE_INVALID")
            return 2
        with psycopg.connect(writer_dsn) as writer:
            staged = stage_release_and_batch(
                cast(Connection, writer),
                release=release,
                batch=batch,
            )
        generation: int | None = None
        if action in {"ACTIVATE", "ROLLBACK"}:
            admin_dsn = os.environ.get("S5_SIGNAL_ADMIN_DSN", "")
            if not admin_dsn:
                print("S5_PRODUCTION_STAGE=ACTIVATION_AUTHORITY_UNAVAILABLE")
                return 2
            with psycopg.connect(admin_dsn) as admin:
                generation = activate_release_and_batch(
                    cast(Connection, admin),
                    model_release_id=str(release.manifest["modelReleaseId"]),
                    signal_batch_id=str(batch.manifest["signalBatchId"]),
                    expected_model_release_id=os.environ.get("S5_EXPECTED_MODEL_RELEASE_ID") or None,
                    expected_signal_batch_id=os.environ.get("S5_EXPECTED_SIGNAL_BATCH_ID") or None,
                    release_manifest_sha256=release.manifest_sha256,
                    batch_manifest_sha256=batch.manifest_sha256,
                    rollback=action == "ROLLBACK",
                )
    except Exception:
        print("S5_PRODUCTION_STAGE=ABSTAIN")
        return 1
    print(
        "S5_PRODUCTION_STAGE=VERIFIED "
        f"action={action} "
        f"releaseOutcome={staged.release_outcome} batchOutcome={staged.batch_outcome} "
        f"activeGeneration={generation if generation is not None else 0}"
    )
    return 0


def _manual_action() -> str:
    """기존 activate flag를 보존하되 rollback은 명시적 bounded action으로만 연다."""

    configured = os.environ.get("S5_MANUAL_ACTION", "").upper()
    if not configured:
        configured = (
            "ACTIVATE"
            if os.environ.get("S5_MANUAL_ACTIVATE", "false").lower() == "true"
            else "STAGE"
        )
    if configured not in {"STAGE", "ACTIVATE", "ROLLBACK"}:
        raise ValueError("S5 manual action is invalid")
    return configured


if __name__ == "__main__":
    raise SystemExit(main())
