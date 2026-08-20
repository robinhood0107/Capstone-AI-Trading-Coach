"""이전 ACCEPTED LightGBM release의 current-session rollback batch를 provider 없이 생성한다."""

from __future__ import annotations

import os
from pathlib import Path

from app.lightgbm.daily_refresh import read_daily_state, write_daily_rollback_batch
from app.lightgbm.private_root import require_private_root
from app.lightgbm.production_release import (
    load_qualified_production_release,
    validate_production_model_release,
)


def main() -> int:
    """서버 root와 prior release state digest만 사용해 immutable ROLLBACK batch를 만든다."""

    print("S5_ROLLBACK_BATCH=RESEARCH_ONLY")
    return 2

    # Kept as unreachable historical production implementation for audit reproduction.
    root_value = os.environ.get("S5_SOURCE_ROOT", "")
    state_sha = os.environ.get("S5_ROLLBACK_STATE_SHA256", "")
    bootstrap_sha = os.environ.get("S5_BOOTSTRAP_PACKET_SHA256", "")
    release_sha = os.environ.get("S5_RELEASE_MANIFEST_SHA256", "")
    if not all((root_value, state_sha, bootstrap_sha, release_sha)):
        print("S5_ROLLBACK_BATCH=AUTHORITY_UNAVAILABLE")
        return 2
    try:
        root = Path(root_value)
        require_private_root(root)
        daily_root = root / "daily"
        require_private_root(daily_root)
        state = read_daily_state(state_root=daily_root, expected_sha256=state_sha)
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in (bootstrap_sha, release_sha)
        ):
            raise ValueError("rollback release trust anchor is invalid")
        bootstrap_root = root / f"run-{bootstrap_sha}"
        validated = validate_production_model_release(
            approved_root=bootstrap_root / "release",
            expected_manifest_sha256=release_sha,
        )
        release = load_qualified_production_release(
            release_root=bootstrap_root / "release",
            expected_release_manifest_sha256=release_sha,
            feature_root=bootstrap_root / "feature",
            expected_feature_manifest_sha256=str(validated.manifest["featureManifestSha256"]),
        )
        rollback_root = daily_root / f"rollback-{state.sha256}-{release_sha[:12]}"
        if rollback_root.exists():
            require_private_root(rollback_root)
        else:
            rollback_root.mkdir(mode=0o700)
        batch = write_daily_rollback_batch(
            state=state,
            release=release,
            batch_root=rollback_root / "batch",
        )
    except Exception:
        print("S5_ROLLBACK_BATCH=ABSTAIN")
        return 1
    print(
        "S5_ROLLBACK_BATCH=VERIFIED "
        f"stateSha256={state.sha256} batchManifestSha256={batch.manifest_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
