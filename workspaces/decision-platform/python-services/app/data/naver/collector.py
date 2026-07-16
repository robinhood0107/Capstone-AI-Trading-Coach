from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.data._shared.redis_quota import QuotaDeniedError
from app.data.kis.universe import UniverseManifest, UniverseManifestSymbol
from app.data.naver._credential_transport import NaverCredentialError
from app.data.naver.errors import NaverError, NaverResponseError
from app.data.naver.models import NaverNewsItem, NaverNewsPage
from app.data.naver.policy import validate_news_query


_MAX_BATCH_SIZE = 4
_SYMBOL = re.compile(r"[0-9A-Z._:-]{1,20}")
NaverQueryStatus = Literal["complete", "empty", "failed", "deferred"]
NaverCoverage = Literal["complete", "partial", "empty"]


class NaverCollectionError(RuntimeError):
    """감사 universe 또는 batch 불변식 위반을 provider 값 없이 보고한다."""


class NaverCollectionIncompleteError(NaverCollectionError):
    """strict 수집의 첫 incomplete 원인을 allowlisted code로만 전달한다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("incomplete_collection")


class NewsSearchClient(Protocol):
    def search_news(
        self,
        query: str,
        *,
        retrieved_at: datetime,
        requested_display: int,
    ) -> NaverNewsPage: ...


@dataclass(frozen=True)
class NaverQueryResult:
    """한 universe symbol의 검색 상태와 sanitize된 count/item만 보존한다."""

    rank: int
    symbol: str
    query: str
    status: NaverQueryStatus
    provider_total: int
    requested_display: int
    provider_display: int
    received_count: int
    accepted_count: int
    filtered_count: int
    redacted_url_count: int
    items: tuple[NaverNewsItem, ...]

    def to_json(self) -> dict[str, object]:
        """Naver snapshot query 계약의 camelCase allowlist로 변환한다."""
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "query": self.query,
            "status": self.status,
            "providerTotal": self.provider_total,
            "requestedDisplay": self.requested_display,
            "providerDisplay": self.provider_display,
            "receivedCount": self.received_count,
            "acceptedCount": self.accepted_count,
            "filteredCount": self.filtered_count,
            "redactedUrlCount": self.redacted_url_count,
            "items": [item.model_dump(by_alias=True, mode="json") for item in self.items],
        }


@dataclass(frozen=True)
class NaverCollectionResult:
    """lower-only batch의 cursor·deferred·coverage와 내부 실패 분류 결과다."""

    queries: tuple[NaverQueryResult, ...]
    next_batch_cursor: int
    deferred_queries: list[int]
    partial: bool
    coverage: NaverCoverage
    failure_codes: tuple[str, ...] = ()


def collect_news_batch(
    *,
    universe: UniverseManifest,
    client: NewsSearchClient,
    batch_cursor: int,
    retrieved_at: datetime,
    requested_display: int,
    batch_size: int = 4,
    require_complete: bool = False,
) -> NaverCollectionResult:
    """감사된 rank/name만 사용해 설정에서 확정한 1~4개 News query를 순차 수행한다.

    quota가 현재 query를 outbound 전에 거부하면 현재와 남은 query를 deferred로 기록하고
    next cursor를 첫 deferred universe index에 고정한다. 기사 URL은 fetch하지 않는다.
    """
    if not isinstance(require_complete, bool):
        raise NaverCollectionError("audited universe batch arguments are invalid")
    selected, next_cursor = select_audited_news_batch(
        universe,
        batch_size=batch_size,
        batch_cursor=batch_cursor,
    )
    if (
        retrieved_at.tzinfo is None
        or retrieved_at.utcoffset() is None
        or isinstance(requested_display, bool)
        or not isinstance(requested_display, int)
        or not 1 <= requested_display <= 20
    ):
        raise NaverCollectionError("audited universe collection arguments are invalid")

    results: list[NaverQueryResult] = []
    deferred_ranks: list[int] = []
    failure_codes: list[str] = []
    universe_size = len(universe.symbols)

    for offset, symbol in enumerate(selected):
        try:
            page = client.search_news(
                symbol.name,
                retrieved_at=retrieved_at,
                requested_display=requested_display,
            )
        except QuotaDeniedError:
            if require_complete:
                raise NaverCollectionIncompleteError("rate_limited") from None
            remainder = selected[offset:]
            results.extend(
                _empty_query_result(item, requested_display, status="deferred")
                for item in remainder
            )
            deferred_ranks.extend(item.rank for item in remainder)
            failure_codes.append("rate_limited")
            next_cursor = (batch_cursor + offset) % universe_size
            break
        except NaverCredentialError as error:
            failure_code = _collection_failure_code(error)
            if require_complete:
                raise NaverCollectionIncompleteError(failure_code) from None
            if error.code == "logical_deadline_exceeded":
                remainder = selected[offset:]
                results.extend(
                    _empty_query_result(item, requested_display, status="deferred")
                    for item in remainder
                )
                deferred_ranks.extend(item.rank for item in remainder)
                failure_codes.append(failure_code)
                next_cursor = (batch_cursor + offset) % universe_size
                break
            if error.code != "transport_unavailable":
                # credential/profile/config 오류는 다음 query로 진행해도 회복할 수 없어 전체 fail-closed한다.
                raise
            results.append(_empty_query_result(symbol, requested_display, status="failed"))
            failure_codes.append(failure_code)
        except NaverResponseError as error:
            failure_code = _collection_failure_code(error)
            if require_complete:
                raise NaverCollectionIncompleteError(failure_code) from None
            if error.code == "authentication_failed":
                raise
            results.append(_empty_query_result(symbol, requested_display, status="failed"))
            failure_codes.append(failure_code)
        except NaverError as error:
            failure_code = _collection_failure_code(error)
            if require_complete:
                raise NaverCollectionIncompleteError(failure_code) from None
            results.append(_empty_query_result(symbol, requested_display, status="failed"))
            failure_codes.append(failure_code)
        else:
            query_result = _query_result(symbol, page)
            if require_complete and (
                query_result.status == "empty" or query_result.accepted_count == 0
            ):
                raise NaverCollectionIncompleteError("partial_collection")
            results.append(query_result)

    partial = any(result.status in {"failed", "deferred"} for result in results)
    if partial:
        coverage: NaverCoverage = "partial"
    elif all(result.status == "empty" for result in results):
        coverage = "empty"
    else:
        coverage = "complete"
    return NaverCollectionResult(
        queries=tuple(results),
        next_batch_cursor=next_cursor,
        deferred_queries=deferred_ranks,
        partial=partial,
        coverage=coverage,
        failure_codes=tuple(failure_codes),
    )


def select_audited_news_batch(
    universe: UniverseManifest,
    *,
    batch_size: int,
    batch_cursor: int,
) -> tuple[tuple[UniverseManifestSymbol, ...], int]:
    """감사 universe와 lower-only batch/cursor를 검증해 선택 종목과 다음 cursor를 반환한다.

    provider client를 만들기 전 호출할 수 있는 순수 경계이며, 잘못된 rank·symbol·query는 외부 호출 없이 거부한다.
    """
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= _MAX_BATCH_SIZE
    ):
        raise NaverCollectionError("audited universe batch arguments are invalid")
    ranked = _validated_universe(universe, batch_size=batch_size)
    if (
        isinstance(batch_cursor, bool)
        or not isinstance(batch_cursor, int)
        or not 0 <= batch_cursor < len(ranked)
    ):
        raise NaverCollectionError("audited universe batch cursor is invalid")
    selected = tuple(ranked[(batch_cursor + offset) % len(ranked)] for offset in range(batch_size))
    return selected, (batch_cursor + batch_size) % len(ranked)


def _validated_universe(
    universe: UniverseManifest,
    *,
    batch_size: int,
) -> tuple[UniverseManifestSymbol, ...]:
    symbols = tuple(universe.symbols)
    if (
        universe.schema_version != 1
        or len(symbols) < batch_size
        or universe.limit != len(symbols)
        or not re.fullmatch(r"[0-9a-f]{64}", universe.source_sha256)
    ):
        raise NaverCollectionError("audited universe manifest is invalid")
    ranked = tuple(sorted(symbols, key=lambda item: item.rank))
    ranks = [item.rank for item in ranked]
    symbol_codes = [item.symbol for item in ranked]
    query_names = [item.name for item in ranked]
    if (
        ranks != list(range(1, len(ranked) + 1))
        or len(symbol_codes) != len(set(symbol_codes))
        or len(query_names) != len(set(query_names))
    ):
        raise NaverCollectionError("audited universe identity is invalid")
    for item in ranked:
        if (
            _SYMBOL.fullmatch(item.symbol) is None
            or not item.name
            or item.name != item.name.strip()
        ):
            raise NaverCollectionError("audited universe identity is invalid")
        try:
            validate_news_query(item.name)
        except ValueError:
            raise NaverCollectionError("audited universe identity is invalid") from None
    return ranked


def _collection_failure_code(error: Exception) -> str:
    code = getattr(error, "code", "collection_failed")
    if code in {
        "authentication_unavailable",
        "authentication_failed",
        "logical_deadline_exceeded",
        "transport_unavailable",
        "rate_limited",
        "invalid_response",
    }:
        return str(code)
    if code == "provider_unavailable":
        return "transport_unavailable"
    if code in {"response_too_large", "response_unavailable"}:
        return "invalid_response"
    if code in {"invalid_query", "redirect_rejected", "profile_invalid"}:
        return "invalid_response"
    return "collection_failed"


def _query_result(symbol: UniverseManifestSymbol, page: NaverNewsPage) -> NaverQueryResult:
    return NaverQueryResult(
        rank=symbol.rank,
        symbol=symbol.symbol,
        query=symbol.name,
        status=page.status,
        provider_total=page.provider_total,
        requested_display=page.requested_display,
        provider_display=page.provider_display,
        received_count=page.received_count,
        accepted_count=page.accepted_count,
        filtered_count=page.filtered_count,
        redacted_url_count=page.redacted_url_count,
        items=tuple(page.items),
    )


def _empty_query_result(
    symbol: UniverseManifestSymbol,
    requested_display: int,
    *,
    status: Literal["failed", "deferred"],
) -> NaverQueryResult:
    return NaverQueryResult(
        rank=symbol.rank,
        symbol=symbol.symbol,
        query=symbol.name,
        status=status,
        provider_total=0,
        requested_display=requested_display,
        provider_display=0,
        received_count=0,
        accepted_count=0,
        filtered_count=0,
        redacted_url_count=0,
        items=(),
    )
