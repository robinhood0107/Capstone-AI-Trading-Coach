from __future__ import annotations

import argparse
import re
import stat
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NoReturn

from app.data.kis.calendar import is_xkrx_trading_day
from app.data.kis.universe import (
    UniverseExportError,
    validate_universe_output_path,
    write_universe_manifest,
    write_universe_markdown_report,
)
from app.data.krx._credential_transport import KrxCredentialError
from app.data.krx.client import KrxHttpError, KrxOpenApiClient
from app.data.krx.catalog import KRX_OPEN_API_FIRST_AVAILABLE_DATE
from app.data.krx.errors import KrxParseError, KrxValidationDiagnostic
from app.data.krx.settings import KrxOpenApiSettings
from app.data.krx.universe import (
    refresh_universe_from_krx_openapi,
    resolve_latest_available_date,
)


_EXACT_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_PYTHON_SERVICES_ROOT = Path(__file__).resolve().parents[3]
_ALLOWED_OUTPUT_ROOTS = (
    _REPOSITORY_ROOT / "data",
    _PYTHON_SERVICES_ROOT / "data",
)


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
        _validate_output_scope(
            data_dir=data_dir,
            manifest_path=manifest_path,
            report_path=report_path,
        )
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
    except Exception as error:
        if client is not None:
            physical_attempts = _safe_physical_attempt_count(client)
        diagnostic_code = _safe_collection_failure_code(error)
        validation_suffix = _safe_validation_diagnostic_suffix(error)
        print(
            "source=krx operation=universe_refresh "
            f"code={diagnostic_code} physical_attempts={physical_attempts}"
            f"{validation_suffix}",
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
    if _EXACT_DATE.fullmatch(value) is None:
        raise ValueError("invalid_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid_date") from None
    # provider 지원 범위와 안전 최신일을 달력 호출보다 먼저 검사해 out-of-bounds 원문을 차단한다.
    if parsed < KRX_OPEN_API_FIRST_AVAILABLE_DATE:
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


def _safe_collection_failure_code(error: Exception) -> str:
    """이미 정규화된 오류만 allowlist로 공개하고 provider 원문과 cause는 버린다."""
    if isinstance(error, KrxHttpError):
        if error.code == "http_status":
            if error.status_code in {401, 403}:
                return "authentication_failed"
            if error.status_code == 429:
                return "rate_limited"
            return "http_status"
        if error.code == "redirect_rejected":
            return "redirect_rejected"
        if error.code == "parse_invalid_response":
            return "invalid_response"
        return "collection_failed"
    if isinstance(error, KrxParseError):
        return "invalid_response"
    if isinstance(error, KrxCredentialError) and error.code in {
        "authentication_unavailable",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
        "connect_unavailable",
        "read_unavailable",
        "write_unavailable",
        "protocol_unavailable",
        "logical_deadline_exceeded",
        "response_too_large",
        "response_unavailable",
        "transport_unavailable",
    }:
        return error.code
    return "collection_failed"


def _safe_validation_diagnostic_suffix(error: Exception) -> str:
    """typed allowlist만 CLI에 추가하고 임의 exception 속성이나 문자열은 읽지 않는다."""
    diagnostic = None
    if isinstance(error, KrxHttpError):
        diagnostic = error.validation_diagnostic
    elif isinstance(error, KrxParseError):
        diagnostic = error.diagnostic
    if type(diagnostic) is not KrxValidationDiagnostic:
        return ""
    return " " + " ".join(f"{name}={value}" for name, value in diagnostic.to_cli_fields())


def _validate_output_scope(
    *,
    data_dir: Path,
    manifest_path: Path,
    report_path: Path,
) -> None:
    """산출물을 승인된 ignored data root와 단일 data directory 아래로 한정한다."""
    data_absolute = _lexical_absolute_path(data_dir)
    manifest_absolute = _lexical_absolute_path(manifest_path)
    report_absolute = _lexical_absolute_path(report_path)
    allowed_roots = tuple(_lexical_absolute_path(root) for root in _ALLOWED_OUTPUT_ROOTS)
    if not any(
        data_absolute == root or data_absolute.is_relative_to(root) for root in allowed_roots
    ):
        raise UniverseExportError("universe output path is not safe")
    if (
        manifest_absolute.parent != data_absolute
        or report_absolute == manifest_absolute
        or not report_absolute.is_relative_to(data_absolute)
    ):
        raise UniverseExportError("universe output path is not safe")
    if _existing_paths_share_identity(manifest_absolute, report_absolute):
        raise UniverseExportError("universe output path is not safe")


def _lexical_absolute_path(path: Path) -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts or expanded.name in {"", ".", ".."}:
        raise UniverseExportError("universe output path is not safe")
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if absolute.anchor != "/":
        raise UniverseExportError("universe output path is not safe")
    return absolute


def _existing_paths_share_identity(first: Path, second: Path) -> bool:
    try:
        first_stat = first.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise UniverseExportError("universe output path is not safe") from None
    try:
        second_stat = second.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise UniverseExportError("universe output path is not safe") from None
    if not stat.S_ISREG(first_stat.st_mode) or not stat.S_ISREG(second_stat.st_mode):
        return False
    return (first_stat.st_dev, first_stat.st_ino) == (
        second_stat.st_dev,
        second_stat.st_ino,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _StableArgumentParser(
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


class _StableArgumentParser(argparse.ArgumentParser):
    """잘못된 caller argv를 되풀이하지 않고 고정된 CLI 오류만 출력한다."""

    def error(self, message: str) -> NoReturn:
        del message
        self.exit(
            2,
            "source=krx operation=universe_refresh code=invalid_arguments\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())
