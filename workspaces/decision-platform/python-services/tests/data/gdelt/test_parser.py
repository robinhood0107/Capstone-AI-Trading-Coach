from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.parser import parse_aggregate_modes

FIXTURE_ROOT = Path(__file__).with_name("fixtures")
WINDOW_START = datetime(2026, 7, 30, tzinfo=UTC)
WINDOW_END = datetime(2026, 7, 31, tzinfo=UTC)


def _fixture(name: str) -> bytes:
    return (FIXTURE_ROOT / name).read_bytes()


def _payload(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def test_parse_aggregate_modes_joins_tone_and_volume_deterministically() -> None:
    """두 aggregate mode를 timestamp로 결합하고 coverage 소수 정책을 고정한다."""

    points = parse_aggregate_modes(
        tone_bytes=_fixture("timeline_tone.valid.json"),
        volume_bytes=_fixture("timeline_vol_raw.valid.json"),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert points == [
        {
            "timestamp": "2026-07-30T00:00:00Z",
            "averageTone": -2.5,
            "articleCount": 24,
            "norm": 120000,
            "coverageRatio": 0.0002,
        },
        {
            "timestamp": "2026-07-30T12:00:00Z",
            "averageTone": -1.25,
            "articleCount": 15,
            "norm": 120000,
            "coverageRatio": 0.000125,
        },
    ]


@pytest.mark.parametrize(
    ("tone", "volume", "code"),
    [
        ({"timeline": []}, {"timeline": []}, "EMPTY_WINDOW"),
        (
            {"timeline": [{"date": "20260730T000000Z", "value": -2.5}]},
            {"timeline": []},
            "INCOMPLETE_SOURCE",
        ),
        (
            {
                "timeline": [
                    {"date": "20260730T000000Z", "value": -2.5},
                    {"date": "20260730T000000Z", "value": -1.0},
                ]
            },
            {
                "timeline": [
                    {"date": "20260730T000000Z", "value": 24, "norm": 120000},
                    {"date": "20260730T120000Z", "value": 15, "norm": 120000},
                ]
            },
            "INVALID_RESPONSE",
        ),
        (
            {"timeline": [{"date": "20260729T235959Z", "value": -2.5}]},
            {"timeline": [{"date": "20260729T235959Z", "value": 24, "norm": 120000}]},
            "INVALID_RESPONSE",
        ),
        (
            {"timeline": [{"date": "20260730T000000Z", "value": -2.5, "title": "x"}]},
            {"timeline": [{"date": "20260730T000000Z", "value": 24, "norm": 120000}]},
            "INVALID_RESPONSE",
        ),
        (
            {"timeline": [{"date": "20260730T000000Z", "value": -2.5}]},
            {"timeline": [{"date": "20260730T000000Z", "value": 24, "norm": 0}]},
            "NORM_ZERO",
        ),
        (
            {"timeline": [{"date": "20260730T000000Z", "value": -101}]},
            {"timeline": [{"date": "20260730T000000Z", "value": 24, "norm": 120000}]},
            "INVALID_RESPONSE",
        ),
    ],
)
def test_parse_aggregate_modes_rejects_incomplete_or_unsafe_shapes(
    tone: object,
    volume: object,
    code: str,
) -> None:
    with pytest.raises(GdeltAggregateError, match=code):
        parse_aggregate_modes(
            tone_bytes=_payload(tone),
            volume_bytes=_payload(volume),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )


@pytest.mark.parametrize(
    "payload",
    [
        b'{"timeline":[{"date":"20260730T000000Z","value":NaN}]}',
        b'{"timeline":[],"timeline":[]}',
        b'{"timeline":[],"unknown":true}',
        b"{" + b'"padding":"' + (b"x" * (4 * 1024 * 1024)) + b'"}',
    ],
)
def test_parse_aggregate_modes_rejects_non_finite_duplicate_unknown_and_oversize(
    payload: bytes,
) -> None:
    with pytest.raises(GdeltAggregateError, match="INVALID_RESPONSE"):
        parse_aggregate_modes(
            tone_bytes=payload,
            volume_bytes=_fixture("timeline_vol_raw.valid.json"),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )
