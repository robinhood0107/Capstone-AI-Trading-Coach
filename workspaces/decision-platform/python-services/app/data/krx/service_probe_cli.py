from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import NoReturn

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.krx.catalog import ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE, KrxMarket
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.parsers import (
    KrxDailyRow,
    is_kis_compatible_symbol,
    is_krx_issue_code,
)
from app.data.krx.settings import KrxOpenApiSettings
from app.data.krx.universe import resolve_latest_available_date
from app.data.krx.universe_refresh_cli import (
    _safe_collection_failure_code,
    _safe_physical_attempt_count,
    _safe_validation_diagnostic_suffix,
    _validated_as_of,
)


_PROBE_READ_TIMEOUT_SECONDS = 120.0
_PROBE_LOGICAL_DEADLINE_SECONDS = 130.0
_MAX_ELAPSED_MS = 999_999_999


@dataclass(frozen=True, slots=True)
class _ProbeSummary:
    row_count: int
    positive_candidate_count: int
    source_sha256: str


def main(argv: list[str] | None = None) -> int:
    """승인된 단일 KRX endpoint를 strict parse하고 안전한 scalar summary만 출력한다.

    probe는 universe manifest/report를 작성하지 않으며, production private transport와 Redis quota를
    그대로 사용해 정확히 한 physical attempt만 허용한다.
    """
    args = _parse_args(argv)
    try:
        latest = resolve_latest_available_date(datetime.now(UTC))
    except Exception:
        print(
            "source=krx operation=service_probe code=calendar_unavailable",
            file=sys.stderr,
        )
        return 2
    try:
        as_of = _validated_as_of(args.as_of, latest=latest)
    except ValueError as error:
        print(f"source=krx operation=service_probe code={error}", file=sys.stderr)
        return 2

    service = args.service
    client: KrxOpenApiClient | None = None
    physical_attempts: int | str = 0
    started_ns = time.monotonic_ns()
    try:
        # env의 기존 2-call/짧은 timeout 값보다 probe 승인 계약이 우선한다.
        settings = KrxOpenApiSettings(
            max_calls_per_run=1,
            read_timeout_seconds=_PROBE_READ_TIMEOUT_SECONDS,
            logical_deadline_seconds=_PROBE_LOGICAL_DEADLINE_SECONDS,
        )
        client = KrxOpenApiClient(settings)
        rows = client.fetch_service_rows(as_of, service=service)
        physical_attempts = _safe_physical_attempt_count(client)
        summary = _summarize_rows(
            rows,
            as_of=as_of,
            expected_market=ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE[service].market,
        )
        client.close()
        client = None
    except Exception as error:
        if client is not None:
            physical_attempts = _safe_physical_attempt_count(client)
        diagnostic_code = _safe_collection_failure_code(error)
        validation_suffix = _safe_validation_diagnostic_suffix(error)
        print(
            "source=krx operation=service_probe "
            f"code={diagnostic_code} service={service} "
            f"physical_attempts={physical_attempts}{validation_suffix}",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    if physical_attempts != 1:
        print(
            "source=krx operation=service_probe "
            f"code=attempt_accounting_invalid service={service} "
            f"physical_attempts={physical_attempts}",
            file=sys.stderr,
        )
        return 1

    elapsed_ms = _elapsed_ms(started_ns, time.monotonic_ns())
    print(
        "source=krx operation=service_probe code=complete "
        f"service={service} as_of={as_of.isoformat()} "
        f"row_count={summary.row_count} "
        f"positive_candidate_count={summary.positive_candidate_count} "
        f"source_sha256={summary.source_sha256} "
        f"elapsed_ms={elapsed_ms} physical_attempts=1"
    )
    return 0


def _summarize_rows(
    rows: tuple[KrxDailyRow, ...],
    *,
    as_of: date,
    expected_market: KrxMarket,
) -> _ProbeSummary:
    """strict parser 결과를 재검증하고 provider row 순서와 무관한 안전 summary를 만든다."""
    if not rows or len(rows) > 5_000:
        raise ValueError("KRX probe rows are invalid")
    symbols: set[str] = set()
    names: set[str] = set()
    for row in rows:
        if (
            type(row) is not KrxDailyRow
            or row.as_of_date != as_of
            or row.market != expected_market
            or not is_krx_issue_code(row.symbol)
            or not row.name.strip()
            or row.market_cap < 0
            or row.trading_value < 0
            or row.symbol in symbols
            or row.name in names
        ):
            raise ValueError("KRX probe rows are invalid")
        symbols.add(row.symbol)
        names.add(row.name)
    ordered = sorted(
        rows,
        key=lambda row: (-row.market_cap, -row.trading_value, row.symbol),
    )
    canonical_rows = [
        {
            "asOfDate": row.as_of_date.isoformat(),
            "symbol": row.symbol,
            "name": row.name,
            "market": row.market,
            "marketCap": row.market_cap,
            "tradingValue": row.trading_value,
        }
        for row in ordered
    ]
    return _ProbeSummary(
        row_count=len(ordered),
        positive_candidate_count=sum(
            is_kis_compatible_symbol(row.symbol)
            and row.market_cap > 0
            and row.trading_value > 0
            for row in ordered
        ),
        source_sha256=canonical_json_sha256(canonical_rows),
    )


def _elapsed_ms(started_ns: int, completed_ns: int) -> int:
    if type(started_ns) is not int or type(completed_ns) is not int:
        return 0
    elapsed_ns = max(0, completed_ns - started_ns)
    return min(elapsed_ns // 1_000_000, _MAX_ELAPSED_MS)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _StableArgumentParser(
        description="Probe exactly one approved KRX OPEN API stock service without publishing"
    )
    parser.add_argument(
        "--online",
        action="store_true",
        required=True,
        help="Acknowledge the separately approved staged KRX online gate",
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="Completed XKRX trading date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--service",
        required=True,
        choices=tuple(ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE),
    )
    return parser.parse_args(argv)


class _StableArgumentParser(argparse.ArgumentParser):
    """잘못된 caller argv를 되풀이하지 않고 probe 고정 오류만 출력한다."""

    def error(self, message: str) -> NoReturn:
        del message
        self.exit(
            2,
            "source=krx operation=service_probe code=invalid_arguments\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())
