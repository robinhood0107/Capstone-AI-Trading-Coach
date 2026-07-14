from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import NoReturn

from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.registry_preflight import RegistryInspectionResult, inspect_registry_metadata
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSSettings

_EXPECTED_PHYSICAL_ATTEMPTS = 4
_FAILURE_LINE = "source=ecos operation=registry_preflight code=preflight_failed"
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
    failed = False
    try:
        client = _build_client()
        result = inspect_registry_metadata(
            client=client,
            series=CANDIDATE_SERIES,
        )
        attempt_count = getattr(client, "physical_attempt_count", 0)
        if attempt_count != _EXPECTED_PHYSICAL_ATTEMPTS:
            failed = True
    except Exception:
        failed = True
    finally:
        try:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        except Exception:
            failed = True

    if failed or result is None:
        print(_FAILURE_LINE)
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


def _argument_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(prog="ecos-registry-preflight", allow_abbrev=False)
    parser.add_argument("--online", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
