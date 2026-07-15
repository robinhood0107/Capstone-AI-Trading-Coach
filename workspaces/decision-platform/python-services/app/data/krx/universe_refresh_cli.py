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


def main(argv: list[str] | None = None) -> int:
    """명시적 online gate 아래 최신 KRX top-30 universe manifest를 생성한다.

    API 실패를 CSV나 이전 manifest 성공으로 바꾸지 않으며, 오류에는 credential·provider 원문·
    request URL을 포함하지 않는다. 실제 호출 전에는 별도 운영 승인도 함께 충족해야 한다.
    """
    args = _parse_args(argv)
    latest = resolve_latest_available_date(datetime.now(UTC))
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
    try:
        # 두 target을 provider 호출 전에 검사해 symlink가 있으면 outbound와 부분 파일을 모두 막는다.
        validate_universe_output_path(manifest_path)
        validate_universe_output_path(report_path)
        settings = KrxOpenApiSettings()
        client = KrxOpenApiClient(settings)
        manifest = refresh_universe_from_krx_openapi(client, as_of=as_of)
        # report가 보조 파일이고 manifest가 consumer commit marker이므로 manifest를 마지막에 게시한다.
        write_universe_markdown_report(report_path, manifest)
        write_universe_manifest(manifest_path, manifest)
    except UniverseExportError:
        print(
            "source=krx operation=universe_refresh code=output_path_invalid",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "source=krx operation=universe_refresh code=collection_failed",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            client.close()

    print(f"KRX Open API universe manifest written to {manifest_path}")
    print(f"KRX Open API universe report written to {report_path}")
    return 0


def _validated_as_of(value: str | None, *, latest: date) -> date:
    if value is None:
        return latest
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid_date") from None
    if not is_xkrx_trading_day(parsed):
        raise ValueError("not_a_trading_session")
    if parsed > latest:
        raise ValueError("date_not_yet_available")
    return parsed


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
