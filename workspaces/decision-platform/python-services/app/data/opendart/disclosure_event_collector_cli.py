"""공시 구조화 이벤트 수집 CLI.

이 CLI 가 뉴스 거부권의 근거 코퍼스를 채운다. 자동운용 런 밖에서만 돈다 - 런 안의 provider
상한은 `V100` 이 0~16 으로 못박고 있다.

환경값
  P1_DISCLOSURE_COLLECTOR_DSN     decision_collector role DSN
  OPENDART_API_KEY                OpenDART 인증정보(값은 transport attempt 동안만 존재한다)
  OPENDART_DAILY_CALL_LIMIT       운영자가 확인한 계정 상한. lower-only 로만 반영된다
  OPENDART_DAILY_CALL_BUDGET      그 상한 아래의 하루 예산
  OPENDART_MAX_CALLS_PER_RUN      한 실행의 호출 상한
  OPENDART_MAX_SYMBOLS_PER_RUN    한 실행이 완결시킬 종목 수

한 줄 표식을 남긴다. 실패도 표식으로 남기고 exit code 로 구분한다 - 시장데이터 수집기와
같은 규약이다. 원 응답은 저장하지 않는다.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from app.data._shared.repository_root import repository_artifact
from app.data.calendar.collector import (
    CalendarCollector,
    CollectionTask,
    CollectorRunLedger,
    OpenDARTAttemptExecutor,
)
from app.data.calendar.job import kst_usage_date
from app.data.calendar.repository import CalendarRepository, OpenDARTQuotaRepository
from app.data.calendar.settings import OpenDARTQuotaSettings
from app.data.opendart.client import OpenDARTClient
from app.data.opendart.corporation_registry_writer import (
    append_corporation_registry_fixture,
)
from app.data.opendart.disclosure_event_collector import (
    ADAPTER_VERSION,
    SymbolTarget,
    build_page_commit,
    collection_window,
    plan_fetches,
    collected_operations,
)
from app.data.opendart.http_client import OpenDARTHttpClient
from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.settings import OpenDARTSettings

_MARKER = "P1_DISCLOSURE_EVENTS"
_CATALOG = "contracts/catalogs/p1-return-universe.v1.json"


class DisclosureCollectorCliError(RuntimeError):
    """환경이나 계약을 만족하지 못해 수집을 시작할 수 없을 때 발생한다."""


def _marker(error: Exception) -> str:
    """예외 메시지를 한 낱말짜리 표식으로 줄인다.

    타입 이름만 남기면 `CheckViolation` 하나가 서로 다른 CHECK 를 모두 덮는다. 이 줄은
    공백으로 나뉜 key=value 로 읽히므로 공백을 넣을 수 없고, 값에 DSN 이 섞일 수 있으므로
    길이도 제한한다. `yfinance_daily_cli` 가 같은 이유로 같은 규약을 쓴다.
    """

    words = "".join(character if character.isalnum() else " " for character in str(error)).split()
    return "_".join(words)[:160] or "NO_MESSAGE"


def _catalog_path() -> Path:
    """커밋된 유니버스 카탈로그를 찾는다.

    호스트와 컨테이너의 리포 루트 깊이가 다르다(컨테이너는 /app). 한 상수로 맞출 수 없으니
    공용 해석기가 위로 걸어 올라가며 찾는다.
    """

    path = repository_artifact(__file__, _CATALOG)
    if path is None:
        raise DisclosureCollectorCliError("UNIVERSE_CATALOG_MISSING")
    return path


def _universe() -> list[str]:
    catalog = json.loads(_catalog_path().read_text(encoding="utf-8"))
    symbols = sorted({str(item["symbol"]) for item in catalog["symbols"]})
    if len(symbols) != int(catalog["symbolCount"]):
        raise DisclosureCollectorCliError("UNIVERSE_CATALOG_INCONSISTENT")
    return symbols


def _targets(client: OpenDARTClient, symbols: list[str]) -> list[SymbolTarget]:
    """고유번호 목록으로 종목코드를 corp_code 에 잇는다.

    ETF 처럼 DART 법인이 없는 종목은 대상이 아니다. 실측으로 31종목 중 29종목이 매핑된다.
    """

    by_stock = {code.stock_code: code.corp_code for code in client.corp_codes() if code.stock_code}
    return [
        SymbolTarget(symbol=symbol, corp_code=by_stock[symbol])
        for symbol in symbols
        if symbol in by_stock
    ]


def _publish_registry(targets: list[SymbolTarget], *, dsn: str, observed_at: datetime) -> int:
    """종목코드-corp_code 매핑을 법인 registry 관측으로 남긴다.

    이것이 없으면 근거를 읽는 쪽이 종목을 corp_code 로 풀지 못한다 -
    `PostgresStoredDisclosureRepository.load` 가 `current_corporation_registry_projection` 에서
    corp_code 를 찾고, 못 찾으면 이벤트가 있어도 0건을 돌려준다. 실측으로 그 상태였다.

    값의 출처가 이 수집기의 고유번호 조회이므로 여기서 함께 남기는 것이 옳다. 기존
    운영자 writer 를 그대로 쓰고 새 표도 새 role 도 만들지 않는다.
    """

    payload = {
        "mappings": [
            {
                "completeness": "COMPLETE",
                "corpCode": target.corp_code,
                "registryStatus": "ACTIVE",
                "symbol": target.symbol,
            }
            for target in sorted(targets, key=lambda item: item.symbol)
        ],
        "observedAt": observed_at.isoformat(),
        "receivedAt": observed_at.isoformat(),
        "schemaVersion": "opendart-corporation-registry.v1",
        "sourceVersion": ADAPTER_VERSION,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        path = Path(handle.name)
    try:
        return append_corporation_registry_fixture(path, database_dsn=dsn)
    finally:
        path.unlink(missing_ok=True)


def _fetch(
    client: OpenDARTClient, window_from: date, window_to: date
) -> Callable[[SymbolTarget, str], list[DisclosureRiskEvent]]:
    def call(target: SymbolTarget, operation: str) -> list[DisclosureRiskEvent]:
        return client.major_matter_events(
            endpoint_id=operation,
            symbol=target.symbol,
            corp_code=target.corp_code,
            start=window_from,
            end=window_to,
        )

    return call


def main(argv: list[str] | None = None) -> int:
    del argv
    dsn = os.environ.get("P1_DISCLOSURE_COLLECTOR_DSN", "").strip()
    if not dsn:
        print(f"{_MARKER}=INVALID reason=DSN_MISSING providerCalls=0", flush=True)
        return 1
    try:
        quota_config = OpenDARTQuotaSettings().to_config()  # type: ignore[call-arg]
    except Exception as error:  # pydantic ValidationError 를 이름으로 묶지 않는다
        print(
            f"{_MARKER}=INVALID reason=QUOTA_CONFIG_{type(error).__name__} providerCalls=0",
            flush=True,
        )
        return 1
    settings = OpenDARTSettings()
    if settings.offline:
        print(f"{_MARKER}=SKIPPED reason=OPENDART_OFFLINE providerCalls=0", flush=True)
        return 0

    # 유니버스는 커밋된 카탈로그에서 온다. provider 호출 전에 읽어, 카탈로그 문제와
    # provider 문제가 같은 표식으로 뭉개지지 않게 한다.
    try:
        symbols = _universe()
    except (DisclosureCollectorCliError, OSError, ValueError, KeyError) as error:
        print(
            f"{_MARKER}=INVALID reason=UNIVERSE_{type(error).__name__} providerCalls=0",
            flush=True,
        )
        return 1

    session_date = kst_usage_date(datetime.now(UTC))
    window_from, window_to = collection_window(session_date)
    ledger = CollectorRunLedger(max_calls=quota_config.max_calls_per_run)
    observed_at = datetime.now(UTC)
    operations = collected_operations()

    with psycopg.connect(dsn, autocommit=False) as connection:
        quota = OpenDARTQuotaRepository(connection)
        repository = CalendarRepository(connection)
        executor = OpenDARTAttemptExecutor(
            quota_repository=quota,
            config=quota_config,
            ledger=ledger,
            now=lambda: datetime.now(UTC),
        )
        http = OpenDARTHttpClient.for_online_collector(
            settings,
            before_send=executor.before_send,
            on_handoff=executor.record_http_handoff,
        )
        client = OpenDARTClient(settings, http_client=http)
        try:
            targets = _targets(client, symbols)
        except Exception as error:
            http.close()
            print(
                f"{_MARKER}=PROVIDER_UNAVAILABLE reason=CORP_CODE_{type(error).__name__} "
                f"providerCalls={ledger.actual_http_sends}",
                flush=True,
            )
            return 1

        # 매핑을 먼저 남긴다. 이벤트만 있고 매핑이 없으면 읽는 쪽이 0건을 돌려준다.
        try:
            registry_rows = _publish_registry(targets, dsn=dsn, observed_at=observed_at)
        except (ValueError, OSError, psycopg.Error) as error:
            http.close()
            print(
                f"{_MARKER}=STOPPED reason=REGISTRY_{type(error).__name__}_{_marker(error)}"
                f" providerCalls={ledger.actual_http_sends}",
                flush=True,
            )
            return 1

        committed = 0
        events_total = 0
        failed: str | None = None
        call = _fetch(client, window_from, window_to)

        def publish(target: SymbolTarget, operation: str) -> Callable[[object], object]:
            def apply(result: object) -> object:
                nonlocal committed, events_total
                events: list[DisclosureRiskEvent] = (
                    [item for item in result if isinstance(item, DisclosureRiskEvent)]
                    if isinstance(result, list)
                    else []
                )
                commit = build_page_commit(
                    target=target,
                    operation=operation,
                    events=events,
                    window_from=window_from,
                    window_to=window_to,
                    observed_at=observed_at,
                )
                repository.publish_page(commit)
                committed += 1
                events_total += len(events)
                return result

            return apply

        tasks = [
            CollectionTask(
                operation=planned.operation,
                subject=planned.target.corp_code,
                page=1,
                send=planned.send,
                publish=publish(planned.target, planned.operation),
            )
            for planned in plan_fetches(targets=targets, fetch=call)
        ]
        try:
            CalendarCollector(lock=repository, executor=executor, config=quota_config).run(tasks)
        except Exception as error:
            failed = f"{type(error).__name__}_{_marker(error)}"
        finally:
            http.close()

    complete_symbols = committed // len(operations) if operations else 0
    status = "STOPPED" if failed else ("ADOPTED" if committed else "NO_TARGETS")
    print(
        f"{_MARKER}={status}"
        f" symbols={len(targets)}"
        f" registryRows={registry_rows}"
        f" completeSymbols={complete_symbols}"
        f" pages={committed}"
        f" events={events_total}"
        f" window={window_from.isoformat()}~{window_to.isoformat()}"
        f" operations={len(operations)}"
        f" stoppedAt={failed or 'none'}"
        f" providerCalls={ledger.actual_http_sends}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
