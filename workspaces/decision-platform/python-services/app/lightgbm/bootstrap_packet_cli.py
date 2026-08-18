"""S5.6 bootstrap packet을 server-configured approved root에 신규 publish한다."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path

from app.lightgbm.bootstrap_packet import (
    author_bootstrap_packet,
    latest_publishable_bootstrap_cutoff,
)
from app.lightgbm.bootstrap_fresh_authority import (
    fresh_bootstrap_authority_exists,
    publish_fresh_bootstrap_authority,
    read_fresh_bootstrap_authority,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.private_root import (
    acquire_bootstrap_root_lock,
    release_run_lock,
    require_private_root,
)
from app.rag.safe_io import RagSafeIoError


def main() -> int:
    """CLI 경로 입력 없이 `S5_SOURCE_ROOT` 아래 packet만 0600으로 기록한다."""

    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    if not root_value:
        print("S5_BOOTSTRAP_PACKET=SOURCE_ROOT_UNAVAILABLE")
        return 2
    root = Path(root_value)
    root_lock = -1
    try:
        require_private_root(root)
        root_lock = acquire_bootstrap_root_lock(root)
    except (OSError, LightGbmContractError):
        print("S5_BOOTSTRAP_PACKET=DATASET_UNAVAILABLE")
        return 1
    try:
        try:
            if any(
                name.startswith(("run-", "calendar-recovery-binding-"))
                for name in os.listdir(root)
            ):
                print("S5_BOOTSTRAP_PACKET=RECOVERY_REQUIRED")
                return 1
            if fresh_bootstrap_authority_exists(approved_root=root):
                selected = read_fresh_bootstrap_authority(approved_root=root)
            else:
                selected = None
            if selected is not None:
                print(f"S5_BOOTSTRAP_PACKET=SELECTED sha256={selected.packet.sha256}")
                return 0
            publishable_cutoff = latest_publishable_bootstrap_cutoff(
                cutoff=datetime.now(UTC)
            )
            packet = author_bootstrap_packet(cutoff=publishable_cutoff)
        except (OSError, LightGbmContractError):
            print("S5_BOOTSTRAP_PACKET=DATASET_UNAVAILABLE")
            return 1
        try:
            selected = publish_fresh_bootstrap_authority(
                approved_root=root,
                packet=packet,
            )
        except (OSError, RagSafeIoError, LightGbmContractError):
            print("S5_BOOTSTRAP_PACKET=PUBLISH_FAILED")
            return 1
        print(f"S5_BOOTSTRAP_PACKET=AUTHORED sha256={selected.packet.sha256}")
        return 0
    finally:
        release_run_lock(root_lock)


if __name__ == "__main__":
    raise SystemExit(main())
