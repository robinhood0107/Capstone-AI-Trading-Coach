"""승인 packet SHA 하나로만 S5.6 one-shot live read-only bootstrap을 실행한다."""

from __future__ import annotations

import os
from pathlib import Path

from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSS5ProductionSettings
from app.data.kis.http_client import KISHttpClient
from app.data.kis.settings import KISSettings
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.settings import KrxS5ProductionSettings
from app.lightgbm.bootstrap_executor import (
    build_current_inference_feature_table,
    execute_bootstrap_materialization,
)
from app.lightgbm.bootstrap_journal import (
    BootstrapJournal,
    build_resume_packet,
    validate_resume_packet,
)
from app.lightgbm.bootstrap_live import (
    LiveEcosBootstrapProvider,
    LiveKisBootstrapProvider,
    LiveKrxBootstrapProvider,
)
from app.lightgbm.bootstrap_packet import validate_bootstrap_packet
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.daily_refresh import write_initial_daily_state
from app.lightgbm.private_root import acquire_run_lock, release_run_lock, require_private_root
from app.lightgbm.production_release import (
    QualificationFailure,
    qualify_and_write_production_release,
    write_production_signal_batch,
)
from app.lightgbm.temporal import next_xkrx_evidence_clock
from app.rag.safe_io import (
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_new_file,
)


def main() -> int:
    """CLI path 인자는 받지 않고 server root와 승인 digest가 모두 있을 때만 provider를 연다."""

    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    packet_sha256 = os.environ.get("S5_BOOTSTRAP_PACKET_SHA256", "")
    resume_sha256 = os.environ.get("S5_BOOTSTRAP_RESUME_PACKET_SHA256", "")
    if not root_value or not packet_sha256:
        print("S5_BOOTSTRAP=AUTHORITY_UNAVAILABLE")
        return 2
    root = Path(root_value)
    run_lock = -1
    try:
        require_private_root(root)
        packet_file = read_approved_regular_file(
            approved_root=root,
            relative_path=f"bootstrap-{packet_sha256}.json",
            max_bytes=1 * 1024 * 1024,
        )
        packet = validate_bootstrap_packet(
            packet_file.content, expected_sha256=packet_sha256
        )
        run_root = root / f"run-{packet_sha256}"
        if not resume_sha256:
            run_root.mkdir(mode=0o700)
        run_lock = acquire_run_lock(run_root)
        if resume_sha256:
            resume_file = read_approved_regular_file(
                approved_root=root,
                relative_path=f"resume-{resume_sha256}.json",
                max_bytes=64 * 1024,
            )
            validate_resume_packet(
                resume_file.content,
                expected_sha256=resume_sha256,
                bootstrap_packet_sha256=packet_sha256,
                journal=BootstrapJournal(run_root / "source"),
                total_cap=packet.budget.total,
            )
        source_root = run_root / "source"
        feature_root = run_root / "feature"
    except (OSError, RagSafeIoError, LightGbmContractError):
        if run_lock >= 0:
            release_run_lock(run_lock)
        print("S5_BOOTSTRAP=PACKET_OR_ROOT_INVALID")
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
        result = execute_bootstrap_materialization(
            packet=packet,
            source_root=source_root,
            feature_root=feature_root,
            krx=LiveKrxBootstrapProvider(krx_client),
            kis=LiveKisBootstrapProvider(kis_client),
            ecos=LiveEcosBootstrapProvider(ecos_client),
            ecos_series=CANDIDATE_SERIES,
            resume=bool(resume_sha256),
        )
        code_head = os.environ.get("S5_CODE_HEAD_SHA", "")
        code_tree = os.environ.get("S5_CODE_TREE_SHA", "")
        uv_lock_sha256 = os.environ.get("S5_UV_LOCK_SHA256", "")
        if not code_head or not code_tree or not uv_lock_sha256:
            print("S5_BOOTSTRAP=DATASET_UNAVAILABLE qualificationBinding=ABSENT")
            return 1
        qualification = qualify_and_write_production_release(
            packet=packet,
            materialization=result,
            feature_root=feature_root,
            expected_feature_manifest_sha256=result.feature_bundle.manifest_sha256,
            release_root=run_root / "release",
            code_head=code_head,
            code_tree=code_tree,
            uv_lock_sha256=uv_lock_sha256,
        )
        if isinstance(qualification, QualificationFailure):
            print(
                "S5_BOOTSTRAP=DATASET_UNAVAILABLE "
                f"qualificationReason={qualification.reason} "
                f"finalTestAccessCount={qualification.final_test_access_count}"
            )
            return 1
        inference_table = build_current_inference_feature_table(
            packet=packet,
            acquisition=result.acquisition,
        )
        inference_effective_day = next_xkrx_evidence_clock(
            packet.window.latest_completed
        ).date()
        inference_effective_month = (
            f"{inference_effective_day.year:04d}-{inference_effective_day.month:02d}"
        )
        current_universe = next(
            universe
            for universe in result.acquisition.universes
            if universe.effective_month == inference_effective_month
        )
        batch = write_production_signal_batch(
            release=qualification,
            inference_universe=current_universe,
            inference_table=inference_table,
            session_date=packet.window.latest_completed,
            as_of=next_xkrx_evidence_clock(packet.window.latest_completed),
            batch_root=run_root / "batch",
        )
        daily_root = root / "daily"
        daily_root.mkdir(mode=0o700, exist_ok=True)
        require_private_root(daily_root)
        daily_state = write_initial_daily_state(
            packet=packet,
            acquisition=result.acquisition,
            source_root=source_root,
            feature_manifest_sha256=result.feature_bundle.manifest_sha256,
            release_manifest_sha256=qualification.release_manifest_sha256,
            state_root=daily_root,
        )
    except Exception:
        try:
            resume_packet = build_resume_packet(
                bootstrap_packet_sha256=packet_sha256,
                journal=BootstrapJournal(source_root),
                total_cap=packet.budget.total,
            )
            write_approved_new_file(
                approved_root=root,
                relative_path=f"resume-{resume_packet.sha256}.json",
                content=resume_packet.content,
                max_bytes=64 * 1024,
            )
            print(
                "S5_BOOTSTRAP=DATASET_UNAVAILABLE "
                f"resumePacketSha256={resume_packet.sha256}"
            )
        except Exception:
            print("S5_BOOTSTRAP=DATASET_UNAVAILABLE")
        return 1
    finally:
        for client in (ecos_client, kis_client, krx_client):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # cleanup 오류가 이미 봉인된 실행 결과나 다른 client close를 가리지 않게 한다.
                    pass
        release_run_lock(run_lock)
    print(
        "S5_BOOTSTRAP=VERIFIED "
        f"sourceManifestSha256={result.acquisition.source_bundle.manifest_sha256} "
        f"featureManifestSha256={result.feature_bundle.manifest_sha256} "
        f"releaseManifestSha256={qualification.release_manifest_sha256} "
        f"batchManifestSha256={batch.manifest_sha256} "
        f"dailyStateSha256={daily_state.sha256} "
        f"budgetedCalls={result.acquisition.budgeted_calls}"
    )
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
