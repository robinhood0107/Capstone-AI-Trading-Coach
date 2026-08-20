"""승인 packet SHA 하나로만 S5.6 one-shot live read-only bootstrap을 실행한다."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSS5ProductionSettings
from app.data.kis.http_client import KISHttpClient
from app.data.kis.settings import KISSettings
from app.data.krx.client import KrxOpenApiClient, attest_quota_backend_credentials
from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.krx.settings import KrxS5ProductionSettings
from app.lightgbm.bootstrap_executor import (
    DIVERGENCE_CANDIDATES_FILENAME,
    MAX_DIVERGENCE_BLOCK_BYTES,
    build_current_inference_feature_table,
    execute_bootstrap_materialization,
)
from app.lightgbm.bootstrap_fresh_authority import (
    validate_fresh_bootstrap_execution_authority,
)
from app.lightgbm.bootstrap_calendar_recovery import (
    validate_recovery_execution_authority,
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
from app.lightgbm.errors import CalendarDivergenceSuspected, LightGbmContractError
from app.lightgbm.daily_refresh import write_initial_daily_state
from app.lightgbm.runtime_inputs import (
    resolve_bootstrap_packet_sha256,
    resolve_code_provenance,
    resolve_repository_root,
)
from app.lightgbm.private_root import (
    acquire_bootstrap_root_lock,
    acquire_run_lock,
    release_run_lock,
    require_private_root,
)
from app.lightgbm.production_release import (
    QualificationFailure,
    qualify_and_write_production_release,
    validate_qualification_bindings,
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

    # LightGBM production bootstrap은 연구 전용 전환으로 폐쇄됐다. 아래 역사 구현은 봉인된
    # 실행을 감사·재현하기 위해 보존하지만 root, quota credential, provider client보다 먼저 닫는다.
    print("S5_BOOTSTRAP=RESEARCH_ONLY")
    return 2

    # Kept as unreachable historical production implementation for audit reproduction.
    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    resume_sha256 = os.environ.get("S5_BOOTSTRAP_RESUME_PACKET_SHA256", "")
    if not root_value:
        print("S5_BOOTSTRAP=AUTHORITY_UNAVAILABLE")
        return 2
    root = Path(root_value)
    try:
        # 실행 대상 packet은 root가 이미 확정했다. 사람이 SHA를 옮겨 적지 않는다.
        packet_sha256 = resolve_bootstrap_packet_sha256(approved_root=root)
    except (OSError, LightGbmContractError):
        print("S5_BOOTSTRAP=AUTHORITY_UNAVAILABLE")
        return 2
    root_lock = -1
    run_lock = -1
    try:
        require_private_root(root)
        root_lock = acquire_bootstrap_root_lock(root)
        packet_file = read_approved_regular_file(
            approved_root=root,
            relative_path=f"bootstrap-{packet_sha256}.json",
            max_bytes=1 * 1024 * 1024,
        )
        packet = validate_bootstrap_packet(
            packet_file.content, expected_sha256=packet_sha256
        )
        if packet.lineage_mode == "FRESH":
            validate_fresh_bootstrap_execution_authority(
                approved_root=root,
                packet=packet,
            )
        run_root = root / f"run-{packet_sha256}"
        is_recovery = packet.lineage_mode == "CALENDAR_RECOVERY"
        if not resume_sha256 and not is_recovery:
            run_root.mkdir(mode=0o700)
        run_lock = acquire_run_lock(run_root)
        recovery_status = validate_recovery_execution_authority(
            approved_root=root,
            packet=packet,
        )
        if recovery_status == "CAPACITY_EXHAUSTED":
            release_run_lock(run_lock)
            run_lock = -1
            release_run_lock(root_lock)
            root_lock = -1
            print(
                "S5_BOOTSTRAP=DATASET_UNAVAILABLE "
                "reason=KRX_CAPACITY_EXHAUSTED providerCalls=0"
            )
            return 1
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
        # provenance는 실제 저장소 상태에서 유도한다. 명시값이 있으면 유도값과 대조한다.
        code_head, code_tree, uv_lock_sha256 = resolve_code_provenance(
            repository_root=resolve_repository_root()
        )
        validate_qualification_bindings(
            code_head=code_head,
            code_tree=code_tree,
            uv_lock_sha256=uv_lock_sha256,
        )
        try:
            attest_quota_backend_credentials()
        except QuotaUnavailableError:
            if run_lock >= 0:
                release_run_lock(run_lock)
            if root_lock >= 0:
                release_run_lock(root_lock)
            print(
                "S5_BOOTSTRAP=CREDENTIALS_UNAVAILABLE "
                "reason=QUOTA_BACKEND_AUTH providerCalls=0"
            )
            return 2
    except (OSError, RagSafeIoError, LightGbmContractError):
        if run_lock >= 0:
            release_run_lock(run_lock)
        if root_lock >= 0:
            release_run_lock(root_lock)
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
            resume=bool(resume_sha256 or is_recovery),
        )
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
    except CalendarDivergenceSuspected:
        # 같은 query를 다시 열면 승인 호출만 더 태우므로 resume packet을 만들지 않는다.
        candidates = 0
        try:
            block = read_approved_regular_file(
                approved_root=source_root,
                relative_path=DIVERGENCE_CANDIDATES_FILENAME,
                max_bytes=MAX_DIVERGENCE_BLOCK_BYTES,
            )
            payload = json.loads(block.content.decode("utf-8"))
            candidates = len(payload["candidates"])
        except Exception:
            candidates = 0
        print(
            "S5_BOOTSTRAP=DATASET_UNAVAILABLE "
            "reason=CALENDAR_DIVERGENCE_SUSPECTED "
            f"candidates={candidates}"
        )
        return 1
    except Exception as error:
        # 실패 분류만 알린다. 원인을 못 읽는 종료는 승인 호출을 태우고도 진단이 불가능하다.
        # message는 provider 응답 조각을 담을 수 있어 type 이름만 내보낸다.
        error_type = type(error).__name__
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
                f"errorType={error_type} "
                f"resumePacketSha256={resume_packet.sha256}"
            )
        except Exception as resume_error:
            print(
                "S5_BOOTSTRAP=DATASET_UNAVAILABLE "
                f"errorType={error_type} "
                f"resumeErrorType={type(resume_error).__name__}"
            )
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
        release_run_lock(root_lock)
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
