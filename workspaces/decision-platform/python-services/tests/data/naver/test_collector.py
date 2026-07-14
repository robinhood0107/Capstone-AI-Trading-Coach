from __future__ import annotations

import socket
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

import httpx
import pytest

from app.data._shared.redis_quota import QuotaDeniedError
from app.data.kis.universe import UniverseManifest, UniverseManifestSymbol
from app.data.naver.collector import NaverCollectionError, collect_news_batch
from app.data.naver.models import NaverNewsPage


_RETRIEVED_AT = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)


def _symbol(rank: int, symbol: str, name: str) -> UniverseManifestSymbol:
    return UniverseManifestSymbol(
        rank=rank,
        symbol=symbol,
        name=name,
        market="KOSPI",
        market_cap=10_000 - rank,
        trading_value=5_000 - rank,
    )


def _manifest(*, empty: bool = False) -> UniverseManifest:
    symbols = ()
    if not empty:
        # 입력 tuple 순서를 의도적으로 섞어 collector가 감사 rank를 기준으로 정렬하는지 확인한다.
        symbols = (
            _symbol(3, "000003", "세 번째 합성회사"),
            _symbol(1, "000001", "첫 번째 합성회사"),
            _symbol(6, "000006", "여섯 번째 합성회사"),
            _symbol(2, "000002", "두 번째 합성회사"),
            _symbol(5, "000005", "다섯 번째 합성회사"),
            _symbol(4, "000004", "네 번째 합성회사"),
        )
    return UniverseManifest(
        schema_version=1,
        generated_at=datetime(2026, 7, 14, tzinfo=UTC),
        as_of_date=date(2026, 7, 14),
        source="synthetic-krx-export.csv",
        source_sha256="a" * 64,
        ranking_rule="market cap desc, trading value desc, symbol asc",
        limit=len(symbols),
        symbols=symbols,
    )


def _empty_page(requested_display: int) -> NaverNewsPage:
    return NaverNewsPage(
        status="empty",
        providerTotal=0,
        requestedDisplay=requested_display,
        providerDisplay=0,
        receivedCount=0,
        acceptedCount=0,
        filteredCount=0,
        redactedUrlCount=0,
        items=[],
    )


@dataclass
class _RecordingClient:
    fail_with_quota_on_call: int | None = None
    calls: list[tuple[str, datetime, int]] = field(default_factory=list)

    def search_news(
        self,
        query: str,
        *,
        retrieved_at: datetime,
        requested_display: int,
    ) -> NaverNewsPage:
        self.calls.append((query, retrieved_at, requested_display))
        if self.fail_with_quota_on_call == len(self.calls):
            raise QuotaDeniedError(retry_after_ms=60_000, observed_count=2_000)
        return _empty_page(requested_display)


def test_batch_uses_exact_audited_names_in_rank_order_without_article_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("article URL fetch or DNS is forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "head", forbidden)
    client = _RecordingClient()

    result = collect_news_batch(
        universe=_manifest(),
        client=client,
        batch_cursor=0,
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    assert [call[0] for call in client.calls] == [
        "첫 번째 합성회사",
        "두 번째 합성회사",
        "세 번째 합성회사",
        "네 번째 합성회사",
    ]
    assert [(query.rank, query.symbol, query.query) for query in result.queries] == [
        (1, "000001", "첫 번째 합성회사"),
        (2, "000002", "두 번째 합성회사"),
        (3, "000003", "세 번째 합성회사"),
        (4, "000004", "네 번째 합성회사"),
    ]
    assert all(query.status == "empty" for query in result.queries)
    assert result.next_batch_cursor == 4
    assert result.deferred_queries == []
    assert result.partial is False
    assert result.coverage == "empty"


def test_four_symbol_batch_wraps_and_advances_cursor_modulo_universe() -> None:
    client = _RecordingClient()

    result = collect_news_batch(
        universe=_manifest(),
        client=client,
        batch_cursor=4,
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    assert [query.rank for query in result.queries] == [5, 6, 1, 2]
    assert result.next_batch_cursor == 2


def test_quota_denial_defers_current_and_remaining_queries_at_first_deferred_cursor() -> None:
    client = _RecordingClient(fail_with_quota_on_call=3)

    result = collect_news_batch(
        universe=_manifest(),
        client=client,
        batch_cursor=0,
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    assert [call[0] for call in client.calls] == [
        "첫 번째 합성회사",
        "두 번째 합성회사",
        "세 번째 합성회사",
    ]
    assert [query.status for query in result.queries] == ["empty", "empty", "deferred", "deferred"]
    assert result.deferred_queries == [3, 4]
    assert result.next_batch_cursor == 2
    assert result.partial is True
    assert result.coverage == "partial"


def test_empty_or_unaudited_universe_never_falls_back_to_seed_names() -> None:
    client = _RecordingClient()

    with pytest.raises(NaverCollectionError, match="audited universe"):
        collect_news_batch(
            universe=_manifest(empty=True),
            client=client,
            batch_cursor=0,
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert client.calls == []


@pytest.mark.parametrize("defect", ["blank_name", "duplicate_rank", "duplicate_symbol"])
def test_universe_identity_defects_fail_before_queries(defect: str) -> None:
    universe = _manifest()
    symbols = list(universe.symbols)
    if defect == "blank_name":
        symbols[0] = replace(symbols[0], name=" ")
    elif defect == "duplicate_rank":
        symbols[1] = replace(symbols[1], rank=symbols[0].rank)
    else:
        symbols[1] = replace(symbols[1], symbol=symbols[0].symbol)
    client = _RecordingClient()

    with pytest.raises(NaverCollectionError, match="audited universe"):
        collect_news_batch(
            universe=replace(universe, symbols=tuple(symbols)),
            client=client,
            batch_cursor=0,
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert client.calls == []
