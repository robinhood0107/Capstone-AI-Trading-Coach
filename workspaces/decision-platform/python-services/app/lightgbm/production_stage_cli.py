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
        release = validate_production_model_release(
            approved_root=run_root / "release",
            expected_manifest_sha256=release_sha,
        )
        batch = validate_production_signal_batch(
            approved_root=run_root / "batch",
            expected_manifest_sha256=batch_sha,
        )
        with psycopg.connect(writer_dsn) as writer:
            staged = stage_release_and_batch(
                cast(Connection, writer),
                release=release,
                batch=batch,
            )
        generation: int | None = None
        if os.environ.get("S5_MANUAL_ACTIVATE", "false").lower() == "true":
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
                )
    except Exception:
        print("S5_PRODUCTION_STAGE=ABSTAIN")
        return 1
    print(
        "S5_PRODUCTION_STAGE=VERIFIED "
        f"releaseOutcome={staged.release_outcome} batchOutcome={staged.batch_outcome} "
        f"activeGeneration={generation if generation is not None else 0}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
