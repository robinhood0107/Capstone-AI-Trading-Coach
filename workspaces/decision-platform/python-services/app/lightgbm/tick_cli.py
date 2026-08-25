"""S5 자율 운영의 한 tick. 상태를 읽고 그 단계의 남은 승인 작업만 하고 상태를 쓰고 끝난다.

지금까지 실행은 monolithic이라 끝까지 가야만 했고 중간에 죽으면 사람이 다시 시작했다. tick은
멱등하며 예산을 인지한다. 중간 종료가 안전한 것은 journal이 이미 query 단위 멱등성을 보장하기
때문이다. 그래서 tick에 새 resume 로직을 만들지 않는다.

실패는 분류가 다음 행동을 정한다. 일시 실패는 같은 단계에 머물러 다음 tick에 다시 시도하고,
증거 결손은 이미 단위 격리가 처리했으므로 여기까지 오면 단계를 넘기지 않으며, 계약 위반과 예산
소진은 NEEDS_HUMAN으로 고정한다. 분류를 선언하지 않은 실패는 계약 위반으로 취급한다.

종료 코드로 스케줄러가 다음 행동을 정한다. 0은 진척, 1은 무진척(다음 tick 재시도 가능),
2는 사람이 봐야 하는 상태다.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSS5ProductionSettings
from app.data.kis.http_client import KISHttpClient
from app.data.kis.settings import KISSettings
from app.data.krx.client import KrxOpenApiClient, attest_quota_backend_credentials
from app.data.krx.settings import KrxS5ProductionSettings
from app.lightgbm.bootstrap_calendar_recovery import validate_recovery_execution_authority
from app.lightgbm.bootstrap_executor import (
    BootstrapMaterialization,
    execute_bootstrap_materialization,
)
from app.lightgbm.bootstrap_fresh_authority import (
    validate_fresh_bootstrap_execution_authority,
)
from app.lightgbm.bootstrap_live import (
    LiveEcosBootstrapProvider,
    LiveKisBootstrapProvider,
    LiveKrxBootstrapProvider,
)
from app.lightgbm.bootstrap_packet import BootstrapPacket, validate_bootstrap_packet
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.outcomes import OutcomeClass, classify
from app.lightgbm.private_root import (
    acquire_bootstrap_root_lock,
    acquire_run_lock,
    release_run_lock,
    require_private_root,
)
from app.lightgbm.production_release import (
    QualificationFailure,
    qualify_and_write_production_release,
)
from app.lightgbm.run_state import (
    RUN_STATE_HISTORY_FILENAME,
    RunPhase,
    RunState,
    advance_run_state,
    read_run_state,
)
from app.lightgbm.runtime_inputs import (
    resolve_bootstrap_packet_sha256,
    resolve_code_provenance,
    resolve_repository_root,
)
from app.lightgbm.training_append import appended_sessions
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file

# 재검증을 여는 새 append 세션 수다. calibration block 하나 분량이며 매 tick마다 다시
# 학습하지 않도록 유계로 둔다.
REQUALIFICATION_SESSION_THRESHOLD = 21
EXIT_PROGRESS = 0
EXIT_NO_PROGRESS = 1
EXIT_NEEDS_HUMAN = 2


def main() -> int:
    """한 tick을 실행한다."""

    # 자동 materialization, qualification, release stage를 모두 폐쇄한다. 무진척 종료는 기존
    # scheduler 계약의 정상 종료 코드이며 root, quota backend, provider보다 먼저 반환한다.
    print("S5_TICK=RESEARCH_ONLY")
    return EXIT_NO_PROGRESS

    # Kept as unreachable historical autonomous implementation for audit reproduction.
    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    if not root_value:
        print("S5_TICK=AUTHORITY_UNAVAILABLE")
        return EXIT_NEEDS_HUMAN
    root = Path(root_value)
    try:
        packet_sha256 = resolve_bootstrap_packet_sha256(approved_root=root)
    except (OSError, LightGbmContractError):
        print("S5_TICK=AUTHORITY_UNAVAILABLE")
        return EXIT_NEEDS_HUMAN

    root_lock = -1
    run_lock = -1
    try:
        require_private_root(root)
        root_lock = acquire_bootstrap_root_lock(root)
        packet = _read_packet(root=root, packet_sha256=packet_sha256)
        run_root = root / f"run-{packet_sha256}"
        if not run_root.exists():
            run_root.mkdir(mode=0o700)
        run_lock = acquire_run_lock(run_root)
        state = read_run_state(run_root=run_root)
        if state.needs_human:
            print(f"S5_TICK=NEEDS_HUMAN phase={state.phase} tick={state.tick}")
            return EXIT_NEEDS_HUMAN
        try:
            _require_execution_authority(root=root, packet=packet)
        except _CapacityExhausted:
            _halt(run_root=run_root, state=state, outcome="KRX_CAPACITY_EXHAUSTED")
            print("S5_TICK=NEEDS_HUMAN reason=KRX_CAPACITY_EXHAUSTED")
            return EXIT_NEEDS_HUMAN
        if state.phase is not RunPhase.SERVING:
            try:
                attest_quota_backend_credentials()
            except QuotaUnavailableError:
                print("S5_TICK=NO_PROGRESS reason=QUOTA_BACKEND_AUTH")
                return EXIT_NO_PROGRESS
        return _run_phase(run_root=run_root, packet=packet, state=state)
    except (OSError, RagSafeIoError, LightGbmContractError) as error:
        print(f"S5_TICK=NEEDS_HUMAN reason=PACKET_OR_ROOT_INVALID errorType={type(error).__name__}")
        return EXIT_NEEDS_HUMAN
    finally:
        if run_lock >= 0:
            release_run_lock(run_lock)
        if root_lock >= 0:
            release_run_lock(root_lock)


class _CapacityExhausted(Exception):
    """승인 상한을 소진한 recovery packet이다."""


def _require_execution_authority(*, root: Path, packet: BootstrapPacket) -> None:
    if packet.lineage_mode == "FRESH":
        validate_fresh_bootstrap_execution_authority(approved_root=root, packet=packet)
        return
    if validate_recovery_execution_authority(approved_root=root, packet=packet) == (
        "CAPACITY_EXHAUSTED"
    ):
        raise _CapacityExhausted


def _run_phase(*, run_root: Path, packet: BootstrapPacket, state: RunState) -> int:
    """현재 단계의 작업을 한 번 수행한다. 단계 경계는 기존 실행 경로를 그대로 따른다."""

    try:
        if state.phase is RunPhase.MATERIALIZING:
            _collect(run_root=run_root, packet=packet)
            advance_run_state(
                run_root=run_root,
                current=state,
                phase=RunPhase.QUALIFYING,
                outcome="BUNDLE_SEALED",
            )
            print("S5_TICK=PROGRESS phase=MATERIALIZING next=QUALIFYING")
            return EXIT_PROGRESS
        if state.phase is RunPhase.QUALIFYING:
            outcome = _qualify(run_root=run_root, packet=packet)
            advance_run_state(
                run_root=run_root,
                current=state,
                phase=RunPhase.SERVING,
                outcome=outcome,
            )
            print(f"S5_TICK=PROGRESS phase=QUALIFYING next=SERVING outcome={outcome}")
            return EXIT_PROGRESS
        decision = requalification_decision(run_root=run_root)
        if decision is None:
            print(f"S5_TICK=NO_PROGRESS phase={state.phase} reason=STEADY_STATE")
            return EXIT_NO_PROGRESS
        reason, watermark = decision
        advance_run_state(
            run_root=run_root,
            current=state,
            phase=RunPhase.QUALIFYING,
            outcome=reason,
            marker=watermark.isoformat(),
        )
        print(
            f"S5_TICK=PROGRESS phase=SERVING next=QUALIFYING reason={reason} "
            f"through={watermark.isoformat()}"
        )
        return EXIT_PROGRESS
    except Exception as error:
        classification = classify(error)
        if classification in {
            OutcomeClass.RETRYABLE_TRANSIENT,
            OutcomeClass.EVIDENCE_GAP,
        }:
            advance_run_state(
                run_root=run_root,
                current=state,
                phase=state.phase,
                outcome=str(classification),
            )
            print(
                f"S5_TICK=NO_PROGRESS phase={state.phase} outcome={classification} "
                f"errorType={type(error).__name__}"
            )
            return EXIT_NO_PROGRESS
        _halt(run_root=run_root, state=state, outcome=str(classification))
        print(
            f"S5_TICK=NEEDS_HUMAN phase={state.phase} outcome={classification} "
            f"errorType={type(error).__name__}"
        )
        return EXIT_NEEDS_HUMAN


def _collect(*, run_root: Path, packet: BootstrapPacket) -> BootstrapMaterialization:
    """수집과 bundle 봉인. 완료된 query는 journal이 재사용하므로 provider 호출이 늘지 않는다."""

    krx_client = KrxOpenApiClient(KrxS5ProductionSettings())
    kis_client = KISHttpClient(
        KISSettings(kis_mode="live", kis_offline=False, kis_retry_attempts=1)
    )
    ecos_client = ECOSHttpClient(ECOSS5ProductionSettings())
    try:
        return execute_bootstrap_materialization(
            packet=packet,
            source_root=run_root / "source",
            feature_root=run_root / "feature",
            krx=LiveKrxBootstrapProvider(krx_client),
            kis=LiveKisBootstrapProvider(kis_client),
            ecos=LiveEcosBootstrapProvider(ecos_client),
            ecos_series=CANDIDATE_SERIES,
            resume=True,
        )
    finally:
        for resource in (krx_client, kis_client, ecos_client):
            try:
                resource.close()
            except Exception:
                pass


def _qualify(*, run_root: Path, packet: BootstrapPacket) -> str:
    """학습과 qualification. 통과하면 release를 stage까지 발행한다.

    pointer 전환은 하지 않는다. 계약이 수동 CAS로 고정했다. gate 실패는 계약 위반이 아니라 정상
    상태이므로 이전 release가 계속 서빙되고, 없으면 ABSTAIN이 유지된다.
    """

    materialization = _collect(run_root=run_root, packet=packet)
    code_head, code_tree, uv_lock_sha256 = resolve_code_provenance(
        repository_root=resolve_repository_root()
    )
    result = qualify_and_write_production_release(
        packet=packet,
        materialization=materialization,
        feature_root=run_root / "feature",
        expected_feature_manifest_sha256=materialization.feature_bundle.manifest_sha256,
        release_root=run_root / "release",
        code_head=code_head,
        code_tree=code_tree,
        uv_lock_sha256=uv_lock_sha256,
    )
    if isinstance(result, QualificationFailure):
        return f"QUALIFICATION_{result.reason}"[:64]
    return "RELEASE_STAGED"


def requalification_decision(*, run_root: Path) -> tuple[str, date] | None:
    """재검증을 열 이유와 그때 소비할 append 세션 경계를 준다. 없으면 안정 상태다.

    무엇을 이미 학습했는지는 상태 이력의 watermark가 권위다. 별도 표를 두면 두 곳이 어긋난다.
    """

    sessions = appended_sessions(run_root=run_root)
    if not sessions:
        return None
    through = last_qualified_session(run_root=run_root)
    fresh = [day for day in sessions if through is None or day > through]
    if not fresh:
        return None
    if len(fresh) >= REQUALIFICATION_SESSION_THRESHOLD:
        return "REQUALIFY_SESSION_THRESHOLD", sessions[-1]
    if through is not None and fresh[-1].strftime("%Y-%m") != through.strftime("%Y-%m"):
        return "REQUALIFY_MONTH_BOUNDARY", sessions[-1]
    return None


def last_qualified_session(*, run_root: Path) -> date | None:
    """마지막 재검증이 소비한 append 세션 경계를 append-only 이력에서 읽는다."""

    history = run_root / RUN_STATE_HISTORY_FILENAME
    if not history.exists():
        return None
    latest: date | None = None
    with history.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("toPhase") != str(RunPhase.QUALIFYING):
                continue
            marker = str(event.get("marker", ""))
            if not marker:
                continue
            try:
                candidate = date.fromisoformat(marker)
            except ValueError:
                continue
            if latest is None or candidate > latest:
                latest = candidate
    return latest


def _halt(*, run_root: Path, state: RunState, outcome: str) -> None:
    """사람이 봐야 하는 상태로 고정한다. 실패해도 원 실패를 가리지 않는다."""

    try:
        advance_run_state(
            run_root=run_root,
            current=state,
            phase=RunPhase.NEEDS_HUMAN,
            outcome=outcome,
        )
    except Exception:
        return


def _read_packet(*, root: Path, packet_sha256: str) -> BootstrapPacket:
    packet_file = read_approved_regular_file(
        approved_root=root,
        relative_path=f"bootstrap-{packet_sha256}.json",
        max_bytes=1 * 1024 * 1024,
    )
    return validate_bootstrap_packet(packet_file.content, expected_sha256=packet_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
