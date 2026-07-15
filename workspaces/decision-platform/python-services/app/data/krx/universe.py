from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.kis.calendar import previous_xkrx_trading_day
from app.data.kis.universe import (
    KRX_EXPORT_RANKING_RULE,
    UNIVERSE_MANIFEST_SCHEMA_VERSION,
    UniverseManifest,
    UniverseManifestSymbol,
    write_universe_manifest,
)
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.parsers import KrxDailyRow


KRX_OPENAPI_UNIVERSE_SOURCE = "krx-open-api:stk_bydd_trd+ksq_bydd_trd"
_KST = ZoneInfo("Asia/Seoul")
_SAFE_AVAILABILITY_CUTOFF = time(8, 10)
_UNIVERSE_SIZE = 30
_ALLOWED_MARKETS = frozenset({"KOSPI", "KOSDAQ"})


def resolve_latest_available_date(now: datetime) -> date:
    """KRX 일별 공개 조건에 10분 여유를 둔 최신 완료 XKRX 거래일을 반환한다.

    공식 서비스는 직전 거래일 자료를 다음 날 08시부터 허용한다. 자동 실행은 08:10 KST를
    경계로 사용하며, 주말·휴일은 로컬 XKRX 캘린더에서 이전 세션으로 낮춘다.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware current time is required")
    local = now.astimezone(_KST)
    latest_session = previous_xkrx_trading_day(local.date() - timedelta(days=1))
    if local.time().replace(tzinfo=None) >= _SAFE_AVAILABILITY_CUTOFF:
        return latest_session
    # KRX 개발명세의 08시 이전 조건은 주말·휴일에도 최신 session을 한 단계 더 제외한다.
    return previous_xkrx_trading_day(latest_session - timedelta(days=1))


def refresh_universe_from_krx_openapi(
    client: KrxOpenApiClient,
    *,
    as_of: date,
    limit: int = _UNIVERSE_SIZE,
    manifest_path: Path | None = None,
    generated_at: datetime | None = None,
) -> UniverseManifest:
    """승인된 KOSPI·KOSDAQ 두 응답을 검증해 기존 universe manifest v1을 만든다.

    두 endpoint가 모두 성공하고 정확히 30개를 고를 수 있을 때만 파일을 게시한다. provider
    raw 응답은 저장하지 않고 검증된 canonical row hash만 provenance로 남긴다.
    """
    if limit != _UNIVERSE_SIZE:
        raise ValueError("KRX Open API universe limit must be exactly 30")
    rows = client.fetch_universe_rows(as_of)
    all_ranked = _validated_ranked_rows(rows, as_of=as_of)
    candidates = [row for row in all_ranked if row.market_cap > 0 and row.trading_value > 0]
    if len(candidates) < limit:
        raise ValueError("KRX Open API universe requires at least 30 candidates")

    canonical_rows = [_canonical_source_row(row) for row in all_ranked]
    manifest = UniverseManifest(
        schema_version=UNIVERSE_MANIFEST_SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(UTC),
        as_of_date=as_of,
        source=KRX_OPENAPI_UNIVERSE_SOURCE,
        source_sha256=canonical_json_sha256(canonical_rows),
        ranking_rule=KRX_EXPORT_RANKING_RULE,
        limit=limit,
        symbols=tuple(
            UniverseManifestSymbol(
                rank=index,
                symbol=row.symbol,
                name=row.name,
                market=row.market,
                market_cap=row.market_cap,
                trading_value=row.trading_value,
            )
            for index, row in enumerate(candidates[:limit], start=1)
        ),
    )
    if manifest_path is not None:
        write_universe_manifest(manifest_path, manifest)
    return manifest


def _validated_ranked_rows(
    rows: tuple[KrxDailyRow, ...],
    *,
    as_of: date,
) -> list[KrxDailyRow]:
    symbols: set[str] = set()
    names: set[str] = set()
    for row in rows:
        if row.as_of_date != as_of:
            raise ValueError("KRX Open API candidate date did not match")
        if len(row.symbol) != 6 or not row.symbol.isascii() or not row.symbol.isdecimal():
            raise ValueError("KRX Open API candidate symbol is invalid")
        if not row.name.strip():
            raise ValueError("KRX Open API candidate name is invalid")
        if row.market not in _ALLOWED_MARKETS:
            raise ValueError("KRX Open API candidate market is invalid")
        if row.market_cap < 0:
            raise ValueError("KRX Open API candidate market cap must be positive or zero")
        if row.trading_value < 0:
            raise ValueError("KRX Open API candidate trading value must be positive or zero")
        if row.symbol in symbols:
            raise ValueError("KRX Open API duplicate symbol is not allowed")
        if row.name in names:
            raise ValueError("KRX Open API duplicate name is not allowed")
        symbols.add(row.symbol)
        names.add(row.name)
    return sorted(
        rows,
        key=lambda row: (-row.market_cap, -row.trading_value, row.symbol),
    )


def _canonical_source_row(row: KrxDailyRow) -> dict[str, object]:
    return {
        "asOfDate": row.as_of_date.isoformat(),
        "symbol": row.symbol,
        "name": row.name,
        "market": row.market,
        "marketCap": row.market_cap,
        "tradingValue": row.trading_value,
    }
