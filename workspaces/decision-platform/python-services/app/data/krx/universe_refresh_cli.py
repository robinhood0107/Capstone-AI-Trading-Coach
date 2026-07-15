from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from app.data.kis.calendar import is_xkrx_trading_day
from app.data.kis.universe import (
    UniverseExportError,
    validate_universe_output_path,
    write_universe_manifest,
    write_universe_markdown_report,
)
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.settings import KrxOpenApiSettings
from app.data.krx.universe import (
    refresh_universe_from_krx_openapi,
    resolve_latest_available_date,
)


_EARLIEST_AVAILABLE_DATE = date(2010, 1, 4)


def main(argv: list[str] | None = None) -> int:
    """명시적 online gate 아래 최신 KRX top-30 universe manifest를 생성한다.

    API 실패를 CSV나 이전 manifest 성공으로 바꾸지 않으며, 오류에는 credential·provider 원문·
    request URL을 포함하지 않는다. 실제 호출 전에는 별도 운영 승인도 함께 충족해야 한다.
    """
    args = _parse_args(argv)
    try:
        latest = resolve_latest_available_date(datetime.now(UTC))
    except Exception:
        print(
            "source=krx operation=universe_refresh code=calendar_unavailable",
            file=sys.stderr,
        )
        return 2
    try:
        as_of = _validated_as_of(args.as_of, latest=latest)
    except ValueError as error:
        print(f"source=krx operation=universe_refresh code={error}", file=sys.stderr)
        return 2

    data_dir = Path(args.data_dir) if args.data_dir else Path("data/kis")
    manifest_path = data_dir / "universe_manifest.json"
    report_path = (
        Path(args.report_path) if args.report_path else data_dir / "reports" / "universe_refresh.md"
    )

    client: KrxOpenApiClient | None = None
    physical_attempts: int | str = 0
    try:
        # 두 target을 provider 호출 전에 검사해 symlink가 있으면 outbound와 부분 파일을 모두 막는다.
        validate_universe_output_path(manifest_path)
        validate_universe_output_path(report_path)
        settings = KrxOpenApiSettings()
        client = KrxOpenApiClient(settings)
        manifest = refresh_universe_from_krx_openapi(client, as_of=as_of)
        physical_attempts = _safe_physical_attempt_count(client)
        # 성공 산출물은 transport cleanup까지 끝난 뒤에만 게시해 성공/실패 상태를 단일하게 유지한다.
        client.close()
        client = None
        # report가 보조 파일이고 manifest가 consumer commit marker이므로 manifest를 마지막에 게시한다.
        write_universe_markdown_report(report_path, manifest)
        write_universe_manifest(manifest_path, manifest)
    except UniverseExportError:
        if client is not None:
            physical_attempts = _safe_physical_attempt_count(client)
        print(
            "source=krx operation=universe_refresh "
            f"code=output_path_invalid physical_attempts={physical_attempts}",
            file=sys.stderr,
        )
        return 1
    except Exception:
        if client is not None:
            physical_attempts = _safe_physical_attempt_count(client)
        print(
            "source=krx operation=universe_refresh "
            f"code=collection_failed physical_attempts={physical_attempts}",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                # primary 실패와 provider 원문을 cleanup 예외로 덮어쓰지 않는다.
                pass

    print(
        f"source=krx operation=universe_refresh code=complete physical_attempts={physical_attempts}"
    )
    return 0


def _validated_as_of(value: str | None, *, latest: date) -> date:
    if value is None:
        return latest
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid_date") from None
    # provider 지원 범위와 안전 최신일을 달력 호출보다 먼저 검사해 out-of-bounds 원문을 차단한다.
    if parsed < _EARLIEST_AVAILABLE_DATE:
        raise ValueError("date_out_of_supported_range")
    if parsed > latest:
        raise ValueError("date_not_yet_available")
    try:
        is_session = is_xkrx_trading_day(parsed)
    except Exception:
        raise ValueError("calendar_unavailable") from None
    if not is_session:
        raise ValueError("not_a_trading_session")
    return parsed


def _safe_physical_attempt_count(client: KrxOpenApiClient) -> int | str:
    """실패 evidence에 provider handoff 수만 남기고 잘못된 runtime 값은 원문 없이 unknown 처리한다."""
    try:
        value = client.physical_attempt_count
    except Exception:
        return "unknown"
    if type(value) is not int or value < 0:
        return "unknown"
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the internal top-30 universe from the approved KRX OPEN API services"
    )
    parser.add_argument(
        "--online",
        action="store_true",
        required=True,
        help="Acknowledge the separately approved online KRX execution gate",
    )
    parser.add_argument(
        "--as-of",
        help="Completed XKRX trading date in YYYY-MM-DD; defaults to the latest available date",
    )
    parser.add_argument("--data-dir")
    parser.add_argument("--report-path")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
