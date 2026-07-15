from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.kis.universe import refresh_universe_from_krx_export
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.parsers import KrxDailyRow
from app.data.krx.universe import (
    refresh_universe_from_krx_openapi,
    resolve_latest_available_date,
)


_AS_OF = date(2026, 7, 15)
_GENERATED_AT = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
_SOURCE = "krx-open-api:stk_bydd_trd+ksq_bydd_trd"


@dataclass
class _FakeClient:
    rows: tuple[KrxDailyRow, ...]
    error: Exception | None = None
    calls: list[date] = field(default_factory=list)
    physical_attempt_count: int = 2

    def fetch_universe_rows(self, as_of: date) -> tuple[KrxDailyRow, ...]:
        self.calls.append(as_of)
        if self.error is not None:
            raise self.error
        return self.rows


def _row(
    index: int,
    *,
    market: str | None = None,
    market_cap: int | None = None,
    trading_value: int | None = None,
) -> KrxDailyRow:
    return KrxDailyRow(
        as_of_date=_AS_OF,
        symbol=f"{index:06d}",
        name=f"합성종목{index:02d}",
        market=market or ("KOSPI" if index % 2 else "KOSDAQ"),
        market_cap=market_cap if market_cap is not None else 1_000_000 - index * 1_000,
        trading_value=(trading_value if trading_value is not None else 500_000 - index * 100),
    )


def _valid_rows(count: int = 31) -> tuple[KrxDailyRow, ...]:
    return tuple(_row(index) for index in range(1, count + 1))


def _as_client(fake: _FakeClient) -> KrxOpenApiClient:
    return cast(KrxOpenApiClient, fake)


def _canonical_source_rows(rows: tuple[KrxDailyRow, ...]) -> list[dict[str, object]]:
    ranked = sorted(
        rows,
        key=lambda row: (-row.market_cap, -row.trading_value, row.symbol),
    )
    return [
        {
            "asOfDate": row.as_of_date.isoformat(),
            "symbol": row.symbol,
            "name": row.name,
            "market": row.market,
            "marketCap": row.market_cap,
            "tradingValue": row.trading_value,
        }
        for row in ranked
    ]


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 7, 14, 23, 9, 59, tzinfo=UTC), date(2026, 7, 13)),
        (datetime(2026, 7, 14, 23, 10, 0, tzinfo=UTC), date(2026, 7, 14)),
        (datetime(2026, 7, 17, 23, 9, 59, tzinfo=UTC), date(2026, 7, 16)),
        (datetime(2026, 7, 17, 23, 10, 0, tzinfo=UTC), date(2026, 7, 17)),
        (datetime(2026, 7, 18, 23, 9, 59, tzinfo=UTC), date(2026, 7, 16)),
        (datetime(2026, 7, 18, 23, 10, 0, tzinfo=UTC), date(2026, 7, 17)),
        (datetime(2026, 7, 19, 23, 9, 59, tzinfo=UTC), date(2026, 7, 16)),
        (datetime(2026, 7, 19, 23, 10, 0, tzinfo=UTC), date(2026, 7, 17)),
    ],
)
def test_latest_available_date_uses_0810_kst_cutoff_and_previous_xkrx_session(
    now: datetime,
    expected: date,
) -> None:
    assert resolve_latest_available_date(now) == expected


def test_latest_available_date_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="timezone|aware"):
        resolve_latest_available_date(datetime(2026, 7, 15, 8, 10))


def test_online_refresh_builds_manifest_v1_and_exact_top30_with_canonical_rows_hash(
    tmp_path: Path,
) -> None:
    rows = _valid_rows(31)
    fake = _FakeClient(rows=tuple(reversed(rows)))
    manifest_path = tmp_path / "data" / "kis" / "universe_manifest.json"

    manifest = refresh_universe_from_krx_openapi(
        _as_client(fake),
        as_of=_AS_OF,
        limit=30,
        manifest_path=manifest_path,
        generated_at=_GENERATED_AT,
    )

    assert fake.calls == [_AS_OF]
    assert manifest.schema_version == 1
    assert manifest.generated_at == _GENERATED_AT
    assert manifest.as_of_date == _AS_OF
    assert manifest.source == _SOURCE
    assert manifest.source_sha256 == canonical_json_sha256(_canonical_source_rows(rows))
    assert manifest.limit == 30
    assert len(manifest.symbols) == 30
    assert [item.rank for item in manifest.symbols] == list(range(1, 31))
    assert [item.symbol for item in manifest.symbols] == [f"{index:06d}" for index in range(1, 31)]
    assert manifest_path.exists()


def test_online_ranking_uses_market_cap_then_trading_value_then_symbol() -> None:
    rows = (
        _row(3, market_cap=1_000_000, trading_value=90),
        _row(2, market_cap=1_000_000, trading_value=100),
        _row(1, market_cap=2_000_000, trading_value=1),
        _row(4, market_cap=1_000_000, trading_value=100),
        *_valid_rows(30)[4:],
    )

    manifest = refresh_universe_from_krx_openapi(
        _as_client(_FakeClient(rows=rows)),
        as_of=_AS_OF,
        generated_at=_GENERATED_AT,
    )

    assert [item.symbol for item in manifest.symbols[:4]] == [
        "000001",
        "000002",
        "000004",
        "000003",
    ]


def test_provider_row_order_does_not_change_manifest_or_source_hash() -> None:
    rows = _valid_rows(31)

    first = refresh_universe_from_krx_openapi(
        _as_client(_FakeClient(rows=rows)),
        as_of=_AS_OF,
        generated_at=_GENERATED_AT,
    )
    second = refresh_universe_from_krx_openapi(
        _as_client(_FakeClient(rows=tuple(reversed(rows)))),
        as_of=_AS_OF,
        generated_at=_GENERATED_AT,
    )

    assert first.symbols == second.symbols
    assert first.source_sha256 == second.source_sha256


def test_online_refresh_rejects_29_candidates_instead_of_publishing_short_universe(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "universe_manifest.json"

    with pytest.raises(ValueError, match="30|candidate"):
        refresh_universe_from_krx_openapi(
            _as_client(_FakeClient(rows=_valid_rows(29))),
            as_of=_AS_OF,
            manifest_path=manifest_path,
            generated_at=_GENERATED_AT,
        )

    assert not manifest_path.exists()


@pytest.mark.parametrize(
    ("target_index", "mutate", "expected"),
    [
        (
            1,
            lambda rows: replace(rows[1], symbol=rows[0].symbol),
            "duplicate.*symbol|symbol.*duplicate",
        ),
        (
            1,
            lambda rows: replace(rows[1], name=rows[0].name),
            "duplicate.*name|name.*duplicate",
        ),
        (0, lambda rows: replace(rows[0], name=""), "name"),
        (0, lambda rows: replace(rows[0], market="KONEX"), "market"),
        (0, lambda rows: replace(rows[0], market_cap=-1), "market.*cap|positive"),
        (0, lambda rows: replace(rows[0], trading_value=-1), "trading.*value|positive"),
    ],
)
def test_online_refresh_rejects_ambiguous_or_invalid_candidate_set(
    tmp_path: Path,
    target_index: int,
    mutate: Callable[[list[KrxDailyRow]], KrxDailyRow],
    expected: str,
) -> None:
    rows = list(_valid_rows(31))
    rows[target_index] = mutate(rows)
    manifest_path = tmp_path / "universe_manifest.json"

    with pytest.raises(ValueError, match=expected):
        refresh_universe_from_krx_openapi(
            _as_client(_FakeClient(rows=tuple(rows))),
            as_of=_AS_OF,
            manifest_path=manifest_path,
            generated_at=_GENERATED_AT,
        )

    assert not manifest_path.exists()


def test_zero_value_rows_are_excluded_without_blocking_thirty_liquid_candidates() -> None:
    rows = list(_valid_rows(32))
    rows[0] = replace(rows[0], trading_value=0)
    rows[1] = replace(rows[1], market_cap=0)

    manifest = refresh_universe_from_krx_openapi(
        _as_client(_FakeClient(rows=tuple(rows))),
        as_of=_AS_OF,
        generated_at=_GENERATED_AT,
    )

    assert len(manifest.symbols) == 30
    assert all(item.market_cap > 0 for item in manifest.symbols)
    assert all(item.trading_value > 0 for item in manifest.symbols)
    assert {item.symbol for item in manifest.symbols}.isdisjoint({"000001", "000002"})


def test_zero_value_rows_still_fail_when_only_twenty_nine_liquid_candidates_remain() -> None:
    rows = list(_valid_rows(31))
    rows[0] = replace(rows[0], trading_value=0)
    rows[1] = replace(rows[1], market_cap=0)

    with pytest.raises(ValueError, match="30|candidate"):
        refresh_universe_from_krx_openapi(
            _as_client(_FakeClient(rows=tuple(rows))),
            as_of=_AS_OF,
            generated_at=_GENERATED_AT,
        )


def test_online_refresh_rejects_non_top30_limit() -> None:
    with pytest.raises(ValueError, match="limit|30"):
        refresh_universe_from_krx_openapi(
            _as_client(_FakeClient(rows=_valid_rows(31))),
            as_of=_AS_OF,
            limit=29,
            generated_at=_GENERATED_AT,
        )


def test_failure_on_either_endpoint_preserves_existing_manifest_bytes(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "universe_manifest.json"
    previous = b'{"previous":"accepted"}\n'
    manifest_path.write_bytes(previous)
    fake = _FakeClient(
        rows=(),
        error=RuntimeError("synthetic endpoint failure"),
        physical_attempt_count=2,
    )

    with pytest.raises(RuntimeError, match="endpoint"):
        refresh_universe_from_krx_openapi(
            _as_client(fake),
            as_of=_AS_OF,
            manifest_path=manifest_path,
            generated_at=_GENERATED_AT,
        )

    assert manifest_path.read_bytes() == previous


def test_online_and_csv_sources_have_semantically_identical_valid_manifest(
    tmp_path: Path,
) -> None:
    rows = _valid_rows(31)
    csv_path = tmp_path / "krx-export.csv"
    csv_path.write_text(
        "\n".join(
            [
                "종목코드,종목명,시장구분,시가총액,거래대금",
                *[
                    (f"{row.symbol},{row.name},{row.market},{row.market_cap},{row.trading_value}")
                    for row in rows
                ],
            ]
        ),
        encoding="utf-8",
    )

    online = refresh_universe_from_krx_openapi(
        _as_client(_FakeClient(rows=tuple(reversed(rows)))),
        as_of=_AS_OF,
        generated_at=_GENERATED_AT,
    )
    exported = refresh_universe_from_krx_export(
        csv_path,
        as_of=_AS_OF,
        limit=30,
        generated_at=_GENERATED_AT,
    )

    assert online.schema_version == exported.schema_version == 1
    assert online.as_of_date == exported.as_of_date == _AS_OF
    assert online.ranking_rule == exported.ranking_rule
    assert online.limit == exported.limit == 30
    assert online.symbols == exported.symbols
