from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.data.gdelt.approval import validate_approval_packet
from app.data.gdelt.collector import GdeltCollector
from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.policy import QueryDefinition
from app.data.gdelt.storage import publish_observation
from app.data.gdelt.transport import FixtureResponse, FixtureTransport

_FIXTURE_ROOT = Path(__file__).with_name("fixtures")
_DEFAULT_WINDOW_START = "2026-07-30T00:00:00Z"
_DEFAULT_WINDOW_END = "2026-07-31T00:00:00Z"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """offline fixture를 기본으로 파싱하고 online은 exact packet 입력 없이는 즉시 거부한다."""

    parser = argparse.ArgumentParser(description="GDELT aggregate offline fixture collector")
    parser.add_argument("--mode", choices=("offline", "online"), default="offline")
    parser.add_argument("--approval-packet", type=Path)
    parser.add_argument("--approval-packet-sha256")
    parser.add_argument("--head-sha")
    parser.add_argument("--window-start")
    parser.add_argument("--window-end")
    parser.add_argument("--physical-cap", type=int)
    parser.add_argument("--operator-purpose")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "online":
        required = (
            args.approval_packet,
            args.approval_packet_sha256,
            args.head_sha,
            args.window_start,
            args.window_end,
            args.physical_cap,
            args.operator_purpose,
        )
        if any(value is None for value in required):
            parser.error(
                "online mode requires an exact approval packet and bounded execution fields"
            )
        if args.physical_cap != 1:
            parser.error("online physical cap must be exactly 1")
    else:
        args.window_start = args.window_start or _DEFAULT_WINDOW_START
        args.window_end = args.window_end or _DEFAULT_WINDOW_END
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """online activation을 만들지 않으며 현재 CLI는 fixture planning 확인만 수행한다."""

    args = parse_args(argv)
    if args.mode == "online":
        packet = args.approval_packet
        assert isinstance(packet, Path)
        if not packet.is_file():
            raise GdeltAggregateError("PROVIDER_DISABLED", "approval packet is missing or drifted")
        validate_approval_packet(
            content=_read_regular_file(packet, max_bytes=64 * 1024),
            expected_sha256=args.approval_packet_sha256,
            expected_head_sha=args.head_sha,
            query=_default_query(),
            now=datetime.now(UTC),
        )
        raise GdeltAggregateError("PROVIDER_DISABLED", "online transport is not activated")

    start = _parse_utc(args.window_start)
    end = _parse_utc(args.window_end)
    transport = FixtureTransport(
        {
            "TIMELINE_TONE": FixtureResponse(
                content=_read_regular_file(
                    _FIXTURE_ROOT / "timeline_tone.synthetic.json",
                    max_bytes=4 * 1024 * 1024,
                ),
                content_type="application/json",
                redirected=False,
            ),
            "TIMELINE_VOL_RAW": FixtureResponse(
                content=_read_regular_file(
                    _FIXTURE_ROOT / "timeline_vol_raw.synthetic.json",
                    max_bytes=4 * 1024 * 1024,
                ),
                content_type="application/json",
                redirected=False,
            ),
        }
    )
    observation = GdeltCollector(transport=transport).collect(
        query=_default_query(),
        window_start=start,
        window_end=end,
        observed_at=end,
        received_at=end + timedelta(seconds=1),
        available_at=end + timedelta(seconds=2),
    )
    published = False
    if args.output_root is not None:
        publish_observation(root=args.output_root, observation=observation)
        published = True
    print(
        f"GDELT fixture status={observation['status']} "
        f"artifactHash={observation['artifactHash']} "
        f"published={str(published).lower()} physicalProviderCalls=0"
    )
    return 0


def _default_query() -> QueryDefinition:
    return QueryDefinition(
        query_registry_id="global_semiconductor_stress_v1",
        aliases=("semiconductor", "chip supply"),
        entity_mapping_version="issuer_alias_v1",
        symbol="005930",
    )


def _parse_utc(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise GdeltAggregateError("INVALID_RESPONSE", "CLI window is invalid") from None


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    """local fixture/packet의 final symlink와 byte 초과를 읽기 전에 거부한다."""

    file_fd = -1
    try:
        file_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > max_bytes
        ):
            raise GdeltAggregateError("PROVIDER_DISABLED", "local input is invalid")
        with os.fdopen(file_fd, "rb") as file:
            file_fd = -1
            content = file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise GdeltAggregateError("PROVIDER_DISABLED", "local input is invalid")
        return content
    except GdeltAggregateError:
        raise
    except OSError:
        raise GdeltAggregateError("PROVIDER_DISABLED", "local input is unavailable") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)


if __name__ == "__main__":
    raise SystemExit(main())
