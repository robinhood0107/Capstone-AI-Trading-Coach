from __future__ import annotations

import pytest

from app.s8_demo.container_async_smoke import partition_key


def test_partition_key_is_stable_and_opaque() -> None:
    key = partition_key(b"k" * 32)

    assert key == "hmac-sha256:cba64c665dd73317328659319b37190701d9d35a3d26ed1b8a61a8ffe261c277"


def test_partition_key_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="too short"):
        partition_key(b"short")
