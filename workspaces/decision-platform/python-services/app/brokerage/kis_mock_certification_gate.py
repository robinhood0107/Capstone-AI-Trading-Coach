"""KIS 모의투자 certification의 XKRX 수동 실행시간 gate."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_KST = ZoneInfo("Asia/Seoul")
_OPEN = time(9, 10)
_CLOSE = time(15, 0)


class CertificationWindowClosed(RuntimeError):
    """거래일·수동 실행시간이 아니므로 provider 생성 전 중단한다."""


def require_certification_window(now: datetime) -> str:
    """XKRX session의 09:10~15:00 KST에서만 YYYY-MM-DD session을 반환한다."""

    if now.tzinfo is None:
        raise CertificationWindowClosed("KIS_MOCK_CERTIFICATION_CLOCK_INVALID")
    current = now.astimezone(_KST)
    calendar = xcals.get_calendar("XKRX")
    session = current.date().isoformat()
    try:
        if not calendar.is_session(session):
            raise CertificationWindowClosed("KIS_MOCK_CERTIFICATION_MARKET_CLOSED")
    except (ValueError, TypeError):
        raise CertificationWindowClosed("KIS_MOCK_CERTIFICATION_MARKET_CLOSED") from None
    if not _OPEN <= current.timetz().replace(tzinfo=None) <= _CLOSE:
        raise CertificationWindowClosed("KIS_MOCK_CERTIFICATION_MARKET_CLOSED")
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kis-mock-certification-gate")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        observed = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
        session = require_certification_window(observed)
    except (ValueError, CertificationWindowClosed) as error:
        code = str(error)
        if not code.startswith("KIS_MOCK_CERTIFICATION_"):
            code = "KIS_MOCK_CERTIFICATION_CLOCK_INVALID"
        print(f"{code}\nKIS_MOCK_PROVIDER_CALLS=0")
        return 2
    print(f"KIS_MOCK_CERTIFICATION_SESSION={session}\nKIS_MOCK_PROVIDER_CALLS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
