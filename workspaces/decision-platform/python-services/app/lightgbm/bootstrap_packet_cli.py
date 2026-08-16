"""S5.6 bootstrap packet을 server-configured approved root에 신규 publish한다."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path

from app.lightgbm.bootstrap_packet import author_bootstrap_packet
from app.rag.safe_io import RagSafeIoError, write_approved_new_file


def main() -> int:
    """CLI 경로 입력 없이 `S5_SOURCE_ROOT` 아래 packet만 0600으로 기록한다."""

    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    if not root_value:
        print("S5_BOOTSTRAP_PACKET=SOURCE_ROOT_UNAVAILABLE")
        return 2
    root = Path(root_value)
    packet = author_bootstrap_packet(cutoff=datetime.now(UTC))
    filename = f"bootstrap-{packet.sha256}.json"
    try:
        result = write_approved_new_file(
            approved_root=root,
            relative_path=filename,
            content=packet.content,
            max_bytes=1 * 1024 * 1024,
        )
        os.chmod(result.absolute_path, 0o600, follow_symlinks=False)
    except (OSError, RagSafeIoError):
        print("S5_BOOTSTRAP_PACKET=PUBLISH_FAILED")
        return 1
    print(f"S5_BOOTSTRAP_PACKET=AUTHORED sha256={packet.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
