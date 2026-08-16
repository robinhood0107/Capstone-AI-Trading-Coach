"""S5.6B bounded daily provider refresh, inference, stage와 scheduler publish CLI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import psycopg

from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSS5ProductionSettings
from app.data.kis.http_client import KISHttpClient
from app.data.kis.settings import KISSettings
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.settings import KrxS5ProductionSettings
from app.lightgbm.bootstrap_live import (
    LiveEcosBootstrapProvider,
    LiveKisBootstrapProvider,
    LiveKrxBootstrapProvider,
)
from app.lightgbm.daily_refresh import (
    DAILY_PACKET_MAX_BYTES,
    execute_daily_refresh,
    read_daily_state,
    validate_daily_refresh_packet,
)
from app.lightgbm.private_root import (
    acquire_run_lock,
    release_run_lock,
    require_private_root,
)
from app.lightgbm.production_db import (
    Connection,
    publish_daily_batch,
    stage_release_and_batch,
)
from app.lightgbm.production_release import validate_production_model_release
from app.rag.safe_io import read_approved_regular_file


def main() -> int:
    """path 인자 없이 exact packet/state/CAS trust anchor가 모두 있을 때만 provider를 연다."""

    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    packet_sha = os.environ.get("S5_DAILY_PACKET_SHA256", "")
    state_sha = os.environ.get("S5_DAILY_PREVIOUS_STATE_SHA256", "")
    expected_batch_id = os.environ.get("S5_EXPECTED_SIGNAL_BATCH_ID", "")
    writer_dsn = os.environ.get("S5_SIGNAL_WRITER_DSN", "")
    scheduler_dsn = os.environ.get("S5_SIGNAL_SCHEDULER_DSN", "")
    if not all(
        (root_value, packet_sha, state_sha, expected_batch_id, writer_dsn, scheduler_dsn)
    ):
        print("S5_DAILY_REFRESH=AUTHORITY_UNAVAILABLE")
        return 2
    lock = -1
    try:
        root = Path(root_value)
        require_private_root(root)
        daily_root = root / "daily"
        require_private_root(daily_root)
        state = read_daily_state(state_root=daily_root, expected_sha256=state_sha)
        packet_file = read_approved_regular_file(
            approved_root=daily_root,
            relative_path=f"packet-{packet_sha}.json",
            max_bytes=DAILY_PACKET_MAX_BYTES,
        )
        packet = validate_daily_refresh_packet(
            packet_file.content,
            expected_sha256=packet_sha,
            state=state,
        )
        run_root = daily_root / f"run-{packet_sha}"
        run_root.mkdir(mode=0o700)
        lock = acquire_run_lock(run_root)
        bootstrap_root = root / f"run-{state.bootstrap_packet_sha256}"
        feature_root = bootstrap_root / "feature"
        release_root = bootstrap_root / "release"
        require_private_root(feature_root)
        require_private_root(release_root)
    except Exception:
        if lock >= 0:
            release_run_lock(lock)
        print("S5_DAILY_REFRESH=PACKET_OR_ROOT_INVALID")
        return 2

    krx_client: KrxOpenApiClient | None = None
    kis_client: KISHttpClient | None = None
    ecos_client: ECOSHttpClient | None = None
    try:
        krx_client = KrxOpenApiClient(KrxS5ProductionSettings())
        kis_client = KISHttpClient(
            KISSettings(kis_mode="live", kis_offline=False, kis_retry_attempts=1)
        )
        ecos_client = ECOSHttpClient(ECOSS5ProductionSettings())
        result = execute_daily_refresh(
            packet=packet,
            state=state,
            state_root=daily_root,
            run_root=run_root,
            feature_root=feature_root,
            release_root=release_root,
            krx=LiveKrxBootstrapProvider(krx_client),
            kis=LiveKisBootstrapProvider(kis_client),
            ecos=LiveEcosBootstrapProvider(ecos_client),
            ecos_series=CANDIDATE_SERIES,
        )
        release = validate_production_model_release(
            approved_root=release_root,
            expected_manifest_sha256=state.release_manifest_sha256,
        )
        with psycopg.connect(writer_dsn) as writer:
            staged = stage_release_and_batch(
                cast(Connection, writer),
                release=release,
                batch=result.batch,
            )
        with psycopg.connect(scheduler_dsn) as scheduler:
            generation = publish_daily_batch(
                cast(Connection, scheduler),
                signal_batch_id=str(result.batch.manifest["signalBatchId"]),
                expected_signal_batch_id=expected_batch_id,
            )
    except Exception:
        print("S5_DAILY_REFRESH=ABSTAIN")
        return 1
    finally:
        for client in (ecos_client, kis_client, krx_client):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        release_run_lock(lock)
    print(
        "S5_DAILY_REFRESH=VERIFIED "
        f"stateSha256={result.state.sha256} "
        f"batchManifestSha256={result.batch.manifest_sha256} "
        f"releaseOutcome={staged.release_outcome} batchOutcome={staged.batch_outcome} "
        f"generation={generation} budgetedProviderOperations={result.budgeted_calls} "
        "maxPhysicalCalls=41"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
