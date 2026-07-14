from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import NoReturn

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.ecos.errors import ECOSApplicationError, ECOSParseError
from app.data.ecos.http_client import ECOSHttpClient, ECOSHttpError
from app.data.ecos.registry_preflight import RegistryInspectionResult, inspect_registry_metadata
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSSettings

_EXPECTED_PHYSICAL_ATTEMPTS = 4
_DEFAULT_FAILURE_CODE = "preflight_failed"
_SAFE_APPLICATION_FAILURE_CODES = frozenset({"ERROR-500", "ERROR-600", "ERROR-601", "ERROR-602"})
_INVALID_ARGUMENTS_LINE = "source=ecos operation=registry_preflight code=invalid_arguments"


class _CliArgumentError(ValueError):
    """잘못 전달된 argv 원문을 보존하지 않는 stable CLI usage 오류다."""


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _CliArgumentError("invalid_arguments")


def _build_client() -> ECOSHttpClient:
    """운영 Redis quota와 private credential transport를 preflight 전용 client에 연결한다."""
    return ECOSHttpClient(ECOSSettings())


def main(argv: Sequence[str] | None = None) -> int:
    """명시적 `--online`에서만 4-call metadata inspection을 실행하고 자동 activation은 막는다."""
    try:
        arguments = _argument_parser().parse_args(argv)
    except _CliArgumentError:
        print(_INVALID_ARGUMENTS_LINE)
        return 2
    except SystemExit as error:
        return int(error.code or 0)
    if not arguments.online:
        return 2

    client: object | None = None
    result: RegistryInspectionResult | None = None
    attempt_count = 0
    failure_code = _DEFAULT_FAILURE_CODE
    failed = False
    try:
        client = _build_client()
        result = inspect_registry_metadata(
            client=client,
            series=CANDIDATE_SERIES,
        )
        attempt_count = _physical_attempt_count(client)
        if attempt_count != _EXPECTED_PHYSICAL_ATTEMPTS:
            failed = True
    except Exception as error:
        attempt_count = _physical_attempt_count(client)
        failure_code = _failure_code(error)
        failed = True
    finally:
        try:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        except Exception:
            failed = True

    if failed or result is None:
        print(_render_failure(failure_code, physical_attempt_count=attempt_count))
        return 1
    print(_render_result(result, physical_attempt_count=attempt_count))
    return 0


def _render_result(
    result: RegistryInspectionResult,
    *,
    physical_attempt_count: int,
) -> str:
    """allowlist metadata만 deterministic JSON으로 출력해 activation 검토 입력으로 남긴다."""
    payload = {
        "activationRequired": True,
        "canActivate": False,
        "observedAt": result.observed_at.isoformat().replace("+00:00", "Z"),
        "operation": "ecos-registry-preflight",
        "physicalAttemptCount": physical_attempt_count,
        "series": [
            {
                "cycle": entry.cycle,
                "itemCode": entry.item_code,
                "itemName": entry.item_name,
                "searchable": entry.searchable,
                "seriesId": entry.series_id,
                "statCode": entry.stat_code,
                "tableName": entry.table_name,
                "unit": entry.unit,
            }
            for entry in result.entries
        ],
        "source": "ecos",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _failure_code(error: Exception) -> str:
    """provider 원문을 보존하지 않고 승인 판단에 필요한 allowlist 원인만 분류한다."""
    if isinstance(error, ECOSParseError):
        return "invalid_response"
    if isinstance(error, ECOSHttpError):
        if error.code == "response_invalid":
            return "invalid_response"
        if error.status_code == 429:
            return "http_429"
        return _DEFAULT_FAILURE_CODE
    if isinstance(error, ECOSApplicationError) and error.code in _SAFE_APPLICATION_FAILURE_CODES:
        return error.code
    if isinstance(error, QuotaUnavailableError):
        return "quota_unavailable"
    return _DEFAULT_FAILURE_CODE


def _physical_attempt_count(client: object | None) -> int:
    """실패 경로에서도 secret-bearing 객체를 출력하지 않고 bounded count만 읽는다."""
    try:
        value = getattr(client, "physical_attempt_count", 0)
    except Exception:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
        return 0
    return value


def _render_failure(code: str, *, physical_attempt_count: int) -> str:
    return (
        f"source=ecos operation=registry_preflight code={code} "
        f"physicalAttemptCount={physical_attempt_count}"
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(prog="ecos-registry-preflight", allow_abbrev=False)
    parser.add_argument("--online", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
