"""Historical S5 packet의 calendar drift와 누적 cap을 offline으로 봉인한다."""

from __future__ import annotations

import os
from pathlib import Path

from app.lightgbm.bootstrap_calendar_recovery import (
    assess_bootstrap_calendar_recovery,
    materialize_recovery_adoption,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.private_root import require_private_root
from app.lightgbm.runtime_inputs import resolve_recovery_prior_packet_sha256
from app.rag.safe_io import (
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_new_file,
)


def main() -> int:
    """Server root와 prior packet SHA만 받아 provider client 생성 없이 recovery receipt를 쓴다."""

    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    prior_packet_sha256 = os.environ.get("S5_BOOTSTRAP_PACKET_SHA256", "")
    if not root_value:
        print("S5_CALENDAR_RECOVERY=AUTHORITY_UNAVAILABLE")
        return 2
    root = Path(root_value)
    if not prior_packet_sha256:
        try:
            # prior는 아직 현재 세대로 교정되지 않은 최신 소비 run이다.
            prior_packet_sha256 = resolve_recovery_prior_packet_sha256(approved_root=root)
        except (OSError, LightGbmContractError):
            print("S5_CALENDAR_RECOVERY=AUTHORITY_UNAVAILABLE")
            return 2
    try:
        require_private_root(root)
        recovery = assess_bootstrap_calendar_recovery(
            approved_root=root,
            prior_packet_sha256=prior_packet_sha256,
        )
        _publish_exact(
            root,
            f"bootstrap-{recovery.corrected_packet.sha256}.json",
            recovery.corrected_packet.content,
            max_bytes=1 * 1024 * 1024,
        )
        _publish_exact(
            root,
            f"calendar-recovery-binding-{recovery.recovery_binding_sha256}.json",
            recovery.content,
            max_bytes=64 * 1024,
        )
        materialize_recovery_adoption(approved_root=root, recovery=recovery)
    except (OSError, RagSafeIoError, LightGbmContractError):
        print("S5_CALENDAR_RECOVERY=INVALID")
        return 2
    print(
        f"S5_CALENDAR_RECOVERY={recovery.status} "
        f"correctedPacketSha256={recovery.corrected_packet.sha256} "
        f"recoverySha256={recovery.sha256} "
        f"reusableChunks={recovery.reusable_chunks} "
        f"temporalRebindings={recovery.temporal_rebindings} "
        f"missingKrxQueries={recovery.missing_krx_queries} "
        f"projectedKrxCalls={recovery.projected_krx_physical_calls} "
        f"krxShortfall={recovery.krx_shortfall} "
        f"adoptedKisChunks={len(recovery.kis_adopted_attempts)} "
        f"supersededKisCalls={len(recovery.kis_superseded_attempts)} "
        f"providerCalls=0"
    )
    return 1 if recovery.status == "CAPACITY_EXHAUSTED" else 0


def _publish_exact(root: Path, filename: str, content: bytes, *, max_bytes: int) -> None:
    try:
        result = write_approved_new_file(
            approved_root=root,
            relative_path=filename,
            content=content,
            max_bytes=max_bytes,
        )
        os.chmod(result.absolute_path, 0o600, follow_symlinks=False)
    except RagSafeIoError as error:
        existing = read_approved_regular_file(
            approved_root=root,
            relative_path=filename,
            max_bytes=max_bytes,
        )
        if existing.content != content:
            raise LightGbmContractError("calendar recovery artifact conflicts") from error


if __name__ == "__main__":
    raise SystemExit(main())
