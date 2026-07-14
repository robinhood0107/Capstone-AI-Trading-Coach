from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import NoReturn

from app.data.ecos.collector import ECOSCollector
from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries, verified_series
from app.data.ecos.settings import ECOSSettings
from app.data.ecos.storage import ECOSSnapshotPublisher

_COLLECTION_FAILURE_LINE = "source=ecos operation=macro_collect code=collection_failed"
_INVALID_ARGUMENTS_LINE = "source=ecos operation=macro_collect code=invalid_arguments"


class _CliArgumentError(ValueError):
    """argv 원문이나 공급자 값을 출력하지 않는 stable ECOS CLI usage 오류다."""


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _CliArgumentError("invalid_arguments")


def _load_series_registry() -> tuple[ECOSSeries, ...]:
    """source-controlled ECOS registry를 반환하며 candidate는 activation 전까지 provisional이다."""
    return CANDIDATE_SERIES


def _build_collector(settings: ECOSSettings | None = None) -> ECOSCollector:
    """운영 Redis quota·TLS transport·secure snapshot publisher를 private하게 연결한다."""
    runtime_settings = settings or ECOSSettings()
    client = ECOSHttpClient(runtime_settings)
    return ECOSCollector(
        client=client,
        publisher=ECOSSnapshotPublisher(root=runtime_settings.snapshot_root),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """명시적 `--online`에서만 ECOS 수집을 허용하고 `--persist`를 별도 gate로 전달한다."""
    parser = _argument_parser()
    try:
        arguments = parser.parse_args(argv)
        start = date.fromisoformat(arguments.start)
        end = date.fromisoformat(arguments.end)
    except _CliArgumentError:
        print(_INVALID_ARGUMENTS_LINE)
        return 2
    except SystemExit as error:
        return int(error.code or 0)
    except ValueError:
        print(_INVALID_ARGUMENTS_LINE)
        return 2
    if start > end or (end - start).days + 1 > 366:
        return 2

    registry = _load_series_registry()
    try:
        approved = verified_series(registry)
    except Exception:
        return 2
    if not arguments.online or (arguments.persist and not arguments.online):
        return 2

    collector: object | None = None
    outcome_line = _COLLECTION_FAILURE_LINE
    exit_code = 1
    close_failed = False
    try:
        collector = _build_collector()
        collect = getattr(collector, "collect")
        result = collect(
            series=approved,
            start=start,
            end=end,
            retrieved_at=datetime.now(UTC),
            persist=arguments.persist,
        )
        coverage = getattr(result, "coverage", "unknown")
        partial = getattr(result, "partial", True)
        outcome_line = f"source=ecos operation=macro_collect coverage={coverage} partial={partial}"
        exit_code = 0
    except Exception:
        outcome_line = _COLLECTION_FAILURE_LINE
        exit_code = 1
    finally:
        try:
            close = getattr(collector, "close", None)
            if callable(close):
                close()
        except Exception:
            close_failed = True
    if close_failed:
        outcome_line = _COLLECTION_FAILURE_LINE
        exit_code = 1
    print(outcome_line)
    return exit_code


def _argument_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(prog="ecos-macro-collect", allow_abbrev=False)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
