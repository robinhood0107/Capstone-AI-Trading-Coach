"""S5.6B 다음 한 XKRX session의 bounded daily refresh packet author CLI."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

from app.lightgbm.daily_refresh import (
    DAILY_PACKET_MAX_BYTES,
    author_daily_refresh_packet,
    read_daily_state,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.private_root import require_private_root
from app.rag.safe_io import RagSafeIoError, write_approved_new_file


def main() -> int:
    """서버 root와 previous-state digest만 받아 content-free daily packet을 0600으로 쓴다."""

    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    state_sha = os.environ.get("S5_DAILY_PREVIOUS_STATE_SHA256", "")
    if not root_value or not state_sha:
        print("S5_DAILY_PACKET=AUTHORITY_UNAVAILABLE")
        return 2
    try:
        root = Path(root_value)
        require_private_root(root)
        daily_root = root / "daily"
        require_private_root(daily_root)
        state = read_daily_state(state_root=daily_root, expected_sha256=state_sha)
        requested_text = os.environ.get("S5_DAILY_RESUME_SESSION_DATE", "")
        requested = date.fromisoformat(requested_text) if requested_text else None
        packet = author_daily_refresh_packet(
            state=state,
            cutoff=datetime.now(UTC),
            requested_session=requested,
        )
        write_approved_new_file(
            approved_root=daily_root,
            relative_path=f"packet-{packet.sha256}.json",
            content=packet.content,
            max_bytes=DAILY_PACKET_MAX_BYTES,
        )
    except (OSError, RagSafeIoError, LightGbmContractError):
        print("S5_DAILY_PACKET=DATASET_UNAVAILABLE")
        return 1
    print(f"S5_DAILY_PACKET=AUTHORED sha256={packet.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
