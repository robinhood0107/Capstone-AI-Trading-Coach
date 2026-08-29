"""Team A·B가 완성됐다고 가정하고 파이프라인을 처음부터 끝까지 한 번 관통시킨다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다. `tests/rehearsal/`·`tests/verification/`과 같은 경계다.

무엇을 확인하나. `Team B 번들 적재 → 신호 → 판단 → 위험 → 주문 → 체결 → 포지션 → 실현손익`이
production 경로로 실제 이어지는지 본다. 구간별 검증은 이미 있었지만 이어서 돌린 적이 없다.

가짜는 KIS 두 port뿐이다. importer, bridge, RiskEngine, Brokerage, DB, RLS는 전부 실제다.

무엇을 확인하지 않나. Team B 번들의 **파일 포맷**(parquet·safetensors·golden output)은 검사하지
않는다. 그건 Team B의 입력 계약이고 `app/p1_owner/importer.py`의 `validate_artifact_bundle`과 그
전용 테스트가 담당한다. 여기서는 그 검증을 통과한 뒤의 산출물인 import packet부터 시작한다.

정리. 이 테스트는 arm을 열기 위해 `evidence_mode='REAL_TEAM_B'` 번들을 넣어야 한다. 이것은 프로젝트가
일부러 분리해 둔 truth marker이므로 **성공하든 실패하든 만든 것을 전부 되돌린다.** 시작 시 기존 행의
기본키를 스냅샷하고 끝에서 차집합만 지운다. 정리에 실패하면 테스트를 FAIL로 본다. DB 볼륨은
삭제하지 않는다.

실행:
  P1_FULL_PIPELINE_E2E=1 python tests/e2e/full_pipeline_e2e.py \\
    --out artifacts/decision-platform/e2e/full-pipeline.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

_OPT_IN: Final = "P1_FULL_PIPELINE_E2E"
_REPOSITORY: Final = Path(__file__).resolve().parents[5]
_DEPLOY: Final = _REPOSITORY / "deploy/p1"
_STATE: Final = _DEPLOY / ".state-app"
# Docker Desktop을 쓸 때 Windows CLI는 WSL 경로를 번역하지 못해 bind mount가 빈 디렉터리가 된다.
# `p1ctl`과 같은 경로의 Linux CLI를 명시한다.
_DOCKER: Final = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"
_POSTGRES: Final = "capstone-p1-postgres-1"
_PLATFORM: Final = "capstone-p1-decision-platform-1"
_PROJECT: Final = "capstone-p1"
_OWNER: Final = "usr_demo_user"
_ACCOUNT: Final = "acct_" + "a" * 32
_TIMEOUT: Final = 180
_SEED_SOURCE: Final = "P1_E2E_FIXTURE"
# 드라이버의 `container_driver.INITIAL_CASH_KRW`와 같은 값이어야 bridge의 매수가능 판정과
# 원장이 같은 계좌를 말한다.
INITIAL_CASH_KRW: Final = 100_000_000


class PipelineError(RuntimeError):
    """관통 테스트를 중단시킨 사유. 개별 단언 실패와 구분한다."""


@dataclass
class Step:
    name: str
    verdict: str
    detail: str

    def projection(self) -> dict[str, str]:
        return {"detail": self.detail, "name": self.name, "verdict": self.verdict}


@dataclass
class Recorder:
    steps: list[Step] = field(default_factory=list)

    def add(self, name: str, verdict: str, detail: str) -> None:
        if verdict not in {"PASS", "FAIL", "INFO"}:
            raise PipelineError(f"unknown verdict: {verdict}")
        self.steps.append(Step(name, verdict, detail[:6000]))
        marker = {"PASS": "PASS", "FAIL": "FAIL", "INFO": "····"}[verdict]
        print(f"[{marker}] {name}: {detail[:6000]}", flush=True)

    def failed(self) -> bool:
        return any(step.verdict == "FAIL" for step in self.steps)


def _run(command: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    merged = dict(os.environ)
    merged.update(env or {})
    result = subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
        env=merged,
    )
    if result.returncode != 0:
        raise PipelineError(
            f"command failed ({result.returncode}): {' '.join(command[:4])}…\n"
            f"{result.stdout.strip()[-4000:]}\n{result.stderr.strip()[-4000:]}"
        )
    return result.stdout


def _psql(sql: str) -> str:
    """superuser 읽기·쓰기. RLS FORCE 때문에 runtime role로 읽으면 0행이 나와 거짓 통과한다."""

    return _run(
        [
            _DOCKER,
            "exec",
            "-i",
            _POSTGRES,
            "psql",
            "-U",
            "postgres",
            "-d",
            "capstone_p1",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAq",
        ],
        stdin=sql,
    ).strip()


def _compose(*args: str, offline_brokerage: bool = False) -> str:
    overrides: list[str] = []
    if offline_brokerage:
        overrides = ["-f", str(Path(__file__).resolve().parent / "compose.offline-brokerage.yml")]
    return _run(
        [
            _DOCKER,
            "compose",
            "--project-name",
            _PROJECT,
            "--env-file",
            str(_STATE / "runtime.env"),
            "-f",
            str(_DEPLOY / "compose.yml"),
            *overrides,
            "--profile",
            "owner",
            *args,
        ],
        env={
            "P1_OPERATOR_UID": str(os.getuid()),
            "P1_KIS_MOCK_RUNTIME_DIR": str(_STATE / "mock"),
        },
    )


def _wait_healthy(seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = _run([_DOCKER, "inspect", "-f", "{{.State.Health.Status}}", _PLATFORM]).strip()
        if state == "healthy":
            return
        time.sleep(3)
    raise PipelineError("decision-platform did not become healthy")


def _start_offline_brokerage(*, account_id: str, cash_krw: int) -> None:
    """Spring이 붙을 loopback 포트에 KIS 대역만 대체한 서버를 세운다."""

    _run(
        [
            _DOCKER,
            "exec",
            "-d",
            "-e",
            "PYTHONPATH=/app:/tmp",
            "-e",
            f"{_OPT_IN}=1",
            _PLATFORM,
            "/usr/local/bin/p1-secret-entrypoint",
            "decision-platform",
            "python",
            "-W",
            "ignore",
            "-m",
            "e2e.offline_brokerage",
            "--account",
            account_id,
            "--cash",
            str(cash_krw),
        ]
    )
    time.sleep(4)


def _stop_offline_brokerage() -> None:
    subprocess.run(
        [_DOCKER, "exec", _PLATFORM, "pkill", "-f", "e2e.offline_brokerage"],
        capture_output=True,
        timeout=30,
        check=False,
    )


def _driver(*args: str, allow_failure: bool = False) -> dict[str, Any]:
    """컨테이너 안의 드라이버를 호출하고 그 영수증을 돌려준다.

    `allow_failure`는 진단 단계용이다. 드라이버가 0이 아닌 코드로 끝나도 영수증만 있으면 그것을
    판정으로 쓰고 예외를 던지지 않는다.
    """

    command = [
        _DOCKER,
        "exec",
        "-e",
        "PYTHONPATH=/app:/tmp",
        "-e",
        f"{_OPT_IN}=1",
        _PLATFORM,
        "/usr/local/bin/p1-secret-entrypoint",
        "decision-platform",
        "python",
        "-W",
        "ignore",
        "-m",
        "e2e.container_driver",
        *args,
    ]
    if allow_failure:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=_TIMEOUT, check=False
        )
        output = completed.stdout
    else:
        output = _run(command)
    receipts: list[dict[str, Any]] = [
        json.loads(line.removeprefix("P1_E2E_RECEIPT "))
        for line in output.splitlines()
        if line.startswith("P1_E2E_RECEIPT ")
    ]
    if not receipts:
        raise PipelineError(f"driver emitted no receipt: {output.strip()[-800:]}")
    return receipts[-1]


def _publish_driver() -> None:
    """컨테이너 rootfs가 read-only라 `docker cp`가 안 된다. tmpfs로 tar를 흘려 넣는다."""

    tests_root = Path(__file__).resolve().parents[1]
    archive = subprocess.run(
        ["tar", "-cf", "-", "--exclude=__pycache__", "-C", str(tests_root), "e2e"],
        capture_output=True,
        timeout=_TIMEOUT,
        check=True,
    ).stdout
    subprocess.run(
        [_DOCKER, "exec", "-i", _PLATFORM, "tar", "-xf", "-", "-C", "/tmp"],
        input=archive,
        capture_output=True,
        timeout=_TIMEOUT,
        check=True,
    )


class Api:
    """읽기와 명시한 상태 변경만 한다. 인증은 로컬 secret 파일에서 읽는다."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._token: str | None = None

    def login(self) -> None:
        password = (_STATE / "secrets/demo-user.password").read_text(encoding="utf-8").strip()
        status, payload = self.request(
            "POST", "/api/v1/auth/login", {"username": "demo-user", "password": password}
        )
        token = (payload.get("data") or {}).get("accessToken")
        if status != 200 or not isinstance(token, str) or not token:
            raise PipelineError(f"demo login failed: HTTP {status}")
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self._base}{path}", method=method, data=data)
        request.add_header("Content-Type", "application/json")
        if self._token is not None:
            request.add_header("Authorization", f"Bearer {self._token}")
        if idempotency_key is not None:
            request.add_header("X-Idempotency-Key", idempotency_key)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except json.JSONDecodeError:
                return error.code, {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise PipelineError(f"api unreachable: {error}") from error


# --------------------------------------------------------------------------------------
# 스냅샷과 정리
# --------------------------------------------------------------------------------------

_SNAPSHOT_TABLES: Final[tuple[tuple[str, str], ...]] = (
    ("p1_return_artifact_bundle", "bundle_sha256"),
    ("automation_runs", "run_id"),
    ("orders", "order_id"),
    ("decisions", "decision_id"),
    # 판단 멱등 결과를 남기면 다음 실행이 이미 지운 판단을 재생해 온다. 그러면 체크포인트가
    # "exact Decision intent mismatch"로 닫히는데, 원인이 제품이 아니라 정리 누락이다.
    ("decision_idempotency_results", "idempotency_result_id"),
    ("automation_positions", "position_id"),
    ("automation_account_lineage", "lineage_id"),
    ("automation_runtime_events", "event_id"),
    ("automation_events", "event_id"),
    ("automation_processed_ticks", "tick_identity_hash"),
    ("automation_policy_idempotency", "scope_hash"),
    ("automation_policy_versions", "policy_id || '|' || version"),
    ("trading_sessions", "session_date::text"),
    ("market_data_manifests", "manifest_sha256"),
    ("dashboard_artifact_views", "artifact_id"),
    ("artifact_ingest_projection", "artifact_id"),
)


def snapshot() -> dict[str, list[str]]:
    taken: dict[str, list[str]] = {}
    for table, key in _SNAPSHOT_TABLES:
        rows = _psql(f"select {key} from public.{table};")
        taken[table] = [line for line in rows.splitlines() if line]
    return taken


def _quoted(values: list[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values) or "''"


def cleanup(before: dict[str, list[str]], recorder: Recorder) -> None:
    """테스트가 만든 것만 지운다. 기존 실거래 흔적은 건드리지 않는다."""

    statements = [
        "begin;",
        # append-only 트리거는 정상 운영 보호장치다. 정리 단계에서만 superuser 권한으로 잠시 끈다.
        # 테스트가 만든 행만 지우고 트랜잭션이 끝나면 원래대로 돌아온다.
        "set local session_replication_role = replica;",
        # automation 하위부터 지운다. FK 안전 순서다.
        f"delete from public.automation_runtime_events where event_id not in ({_quoted(before['automation_runtime_events'])});",
        f"delete from public.automation_events where event_id not in ({_quoted(before['automation_events'])});",
        "delete from public.automation_processed_ticks where tick_identity_hash not in "
        f"({_quoted(before['automation_processed_ticks'])});",
        "delete from public.automation_policy_idempotency where scope_hash not in "
        f"({_quoted(before['automation_policy_idempotency'])});",
        f"delete from public.automation_account_lineage where lineage_id not in ({_quoted(before['automation_account_lineage'])});",
        f"delete from public.automation_positions where position_id not in ({_quoted(before['automation_positions'])});",
        "delete from public.automation_order_reservations where run_id not in "
        f"({_quoted(before['automation_runs'])});",
        "delete from public.automation_runtime_checkpoint where run_id not in "
        f"({_quoted(before['automation_runs'])});",
        "delete from public.automation_runtime_claim where run_id not in "
        f"({_quoted(before['automation_runs'])});",
        f"delete from public.automation_runs where run_id not in ({_quoted(before['automation_runs'])});",
        "delete from public.automation_runtime_schedule where true;",
        "delete from public.automation_control_idempotency where true;",
        "delete from public.automation_activation_gate where true;",
        # 주문과 판단
        f"delete from public.orders where order_id not in ({_quoted(before['orders'])});",
        "delete from public.decision_idempotency_results where idempotency_result_id not in "
        f"({_quoted(before['decision_idempotency_results'])});",
        f"delete from public.decisions where decision_id not in ({_quoted(before['decisions'])});",
        # Team B 흔적. REAL_TEAM_B 표식을 남기지 않는 것이 이 절의 목적이다.
        "delete from public.artifact_ingest_projection where artifact_id not in "
        f"({_quoted(before['artifact_ingest_projection'])});",
        "delete from public.dashboard_artifact_views where artifact_id not in "
        f"({_quoted(before['dashboard_artifact_views'])});",
        "delete from public.p1_return_signal_projection where bundle_sha256 not in "
        f"({_quoted(before['p1_return_artifact_bundle'])});",
        "delete from public.p1_return_artifact_bundle where bundle_sha256 not in "
        f"({_quoted(before['p1_return_artifact_bundle'])});",
        # 달력과 시장데이터 fixture
        f"delete from public.trading_sessions where chosen_source_id = '{_SEED_SOURCE}';",
        "delete from public.market_data_manifests where manifest_sha256 not in "
        f"({_quoted(before['market_data_manifests'])});",
        # 정책 버전은 append-only다. 테스트가 만든 버전만 지운다.
        "delete from public.automation_policy_versions where policy_id || '|' || version not in "
        f"({_quoted(before['automation_policy_versions'])});",
        # 계좌 통제를 arm 이전 상태로 되돌린다.
        "update public.automation_control set control_state='DISARMED', brokerage_mode='INTERNAL_PAPER',"
        " certification_status='NOT_REQUIRED_INTERNAL_PAPER', policy_id=null, policy_version=null,"
        " principle_version_id=null, principle_version=null, team_b_integrity_receipt_sha256_v2=null,"
        " initial_account_digest_v2=null, expected_account_digest_v2=null,"
        " expected_account_projection_v2=null where user_id='" + _OWNER + "';",
        "commit;",
    ]
    try:
        _psql("\n".join(statements))
    except PipelineError as error:
        recorder.add("정리", "FAIL", f"테스트가 만든 행을 되돌리지 못했다: {error}")
        return

    residue = _psql(
        "select 'bundles=' || count(*) from public.p1_return_artifact_bundle"
        f" where bundle_sha256 not in ({_quoted(before['p1_return_artifact_bundle'])});"
        " select 'runs=' || count(*) from public.automation_runs"
        f" where run_id not in ({_quoted(before['automation_runs'])});"
        " select 'orders=' || count(*) from public.orders"
        f" where order_id not in ({_quoted(before['orders'])});"
        " select 'gate=' || count(*) from public.automation_activation_gate;"
        f" select 'calendar=' || count(*) from public.trading_sessions where chosen_source_id = '{_SEED_SOURCE}';"
    ).replace("\n", " ")
    clean = all(part.endswith("=0") for part in residue.split())
    recorder.add("정리", "PASS" if clean else "FAIL", f"잔여 {residue}")


# --------------------------------------------------------------------------------------
# 씨딩
# --------------------------------------------------------------------------------------


def seed_calendar(sessions: list[date]) -> None:
    """pinned XKRX 달력의 실제 세션 날짜만 넣는다. 날짜를 지어내지 않는다."""

    values = ",\n".join(
        "('XKRX', date '{0}', true, timestamptz '{0} 00:00:00+00', timestamptz '{0} 06:30:00+00',"
        " 'Asia/Seoul', '', '{1}', true, 'P1_E2E_PIPELINE_FIXTURE', now(), 3000, false,"
        " encode(sha256(convert_to('p1-e2e-calendar:{0}', 'UTF8')), 'hex'),"
        " 'P1_E2E_FIXTURE', 's1.6-confidence-v1', now(), now())".format(session, _SEED_SOURCE)
        for session in sessions
    )
    _psql(
        "insert into public.trading_sessions (exchange_mic, session_date, is_open, open_at,"
        " close_at, timezone, reason, chosen_source_id, degraded, fallback_reason, as_of,"
        " confidence_bps, has_conflict, canonical_hash, canonical_rule_version,"
        f" confidence_rule_version, created_at, updated_at) values\n{values}\n"
        " on conflict (exchange_mic, session_date) do nothing;"
    )


def seed_market_data(sessions: list[date]) -> str:
    """엔진의 `dailyReady`가 요구하는 DAILY 매니페스트 체인을 만든다.

    `p1_read_automation_runtime_state_v1`은 세션 09:20 KST 이전 `as_of`의 ACCEPTED DAILY 행이
    없으면 `dailyReady=false`를 내고, 엔진은 그것을 `SKIPPED_DATA_UNAVAILABLE`로 닫는다.
    `enforce_market_data_daily_chain`(V76)이 DAILY의 `previous_manifest_sha256`가 DB head와
    같기를 요구하므로 SEED를 먼저 넣고 그 sha를 이어 붙인다.
    """

    seed_sha = _psql("select encode(sha256(convert_to('p1-e2e-market-seed', 'UTF8')), 'hex');")
    first = min(sessions)
    _psql(
        "insert into public.market_data_manifests (manifest_sha256, manifest_kind, contract_id,"
        " session_date, as_of, generation, source_manifest_sha256, previous_manifest_sha256,"
        " supersedes_sha256, archive_sha256, receipt_set_sha256, calendar_revision, calendar_sha256,"
        " temporal_quality, entitlement_expires_at, status, created_at) values ("
        f"'{seed_sha}', 'SEED', 'market-data-seed.v1', date '{first}' - 1,"
        f" (date '{first}' - 1 + time '08:00') at time zone 'Asia/Seoul', 1,"
        f" '{seed_sha}', null, null, '{seed_sha}', null, 'xkrx-4.13.2', '{seed_sha}',"
        " 'COLLECTION_ONLY', null, 'ACCEPTED', now())"
        " on conflict (manifest_sha256) do nothing;"
    )
    previous = seed_sha
    for session in sorted(sessions):
        daily_sha = _psql(
            f"select encode(sha256(convert_to('p1-e2e-market-daily:{session}', 'UTF8')), 'hex');"
        )
        _psql(
            "insert into public.market_data_manifests (manifest_sha256, manifest_kind, contract_id,"
            " session_date, as_of, generation, source_manifest_sha256, previous_manifest_sha256,"
            " supersedes_sha256, archive_sha256, receipt_set_sha256, calendar_revision,"
            " calendar_sha256, temporal_quality, entitlement_expires_at, status, created_at) values ("
            f"'{daily_sha}', 'DAILY', 'market-data-daily-shard.v1', date '{session}',"
            f" (date '{session}' + time '08:00') at time zone 'Asia/Seoul', 1,"
            f" '{seed_sha}', '{previous}', null, '{daily_sha}', null, 'xkrx-4.13.2', '{daily_sha}',"
            " 'COLLECTION_ONLY', null, 'ACCEPTED', now())"
            " on conflict (manifest_sha256) do nothing;"
        )
        previous = daily_sha
    return seed_sha


def xkrx_sessions(count: int) -> list[date]:
    """컨테이너 안의 pinned 달력에게 물어본다. 호스트에 exchange-calendars가 없어도 된다."""

    script = (
        "import json, datetime, exchange_calendars, pandas\n"
        "cal = exchange_calendars.get_calendar('XKRX')\n"
        "today = datetime.date.today()\n"
        "found = []\n"
        "cursor = pandas.Timestamp(today)\n"
        f"while len(found) < {count + 1}:\n"
        "    cursor = cursor + pandas.Timedelta(days=1)\n"
        "    if cal.is_session(cursor):\n"
        "        found.append(cursor.date().isoformat())\n"
        "print(json.dumps(found))\n"
    )
    output = _run([_DOCKER, "exec", "-i", _PLATFORM, "python", "-W", "ignore", "-"], stdin=script)
    return [date.fromisoformat(value) for value in json.loads(output.strip().splitlines()[-1])]


def previous_xkrx_session(session: date) -> date:
    script = (
        "import datetime, exchange_calendars, pandas\n"
        "cal = exchange_calendars.get_calendar('XKRX')\n"
        f"current = cal.date_to_session(pandas.Timestamp('{session}'), direction='none')\n"
        "print(cal.previous_session(current).date().isoformat())\n"
    )
    output = _run([_DOCKER, "exec", "-i", _PLATFORM, "python", "-W", "ignore", "-"], stdin=script)
    return date.fromisoformat(output.strip().splitlines()[-1])


# --------------------------------------------------------------------------------------
# 단언
# --------------------------------------------------------------------------------------


def assert_pipeline(
    recorder: Recorder,
    *,
    buy_run_id: str,
    sell_run_id: str,
    api: Api,
) -> None:
    runs = _psql(
        "select run_id || '|' || state || '|' || coalesce(selected_symbol,'-') || '|'"
        " || coalesce(selected_side,'-') from public.automation_runs"
        f" where run_id in ('{buy_run_id}','{sell_run_id}') order by session_date;"
    )
    states = {line.split("|")[0]: line.split("|")[1] for line in runs.splitlines() if line}
    completed = states.get(buy_run_id) == "COMPLETED" and states.get(sell_run_id) == "COMPLETED"
    recorder.add(
        "run 종료 상태",
        "PASS" if completed else "FAIL",
        f"{runs.replace(chr(10), ' ; ') or 'no runs'} (SKIPPED_* 도 터미널이므로 COMPLETED만 인정)",
    )

    reservations = _psql(
        "select run_id || '|' || order_id || '|q' || quantity || '|f' || filled_quantity"
        " || '|l' || leaves_quantity || '|u' || unfilled_terminated_quantity || '|'"
        " || coalesce(reconciliation_status,'-') from public.automation_order_reservations"
        f" where run_id in ('{buy_run_id}','{sell_run_id}') order by run_id;"
    )
    conserved = []
    matched = []
    for line in reservations.splitlines():
        if not line:
            continue
        parts = line.split("|")
        quantity = int(parts[2][1:])
        filled = int(parts[3][1:])
        leaves = int(parts[4][1:])
        unfilled = int(parts[5][1:])
        conserved.append(filled + leaves + unfilled == quantity)
        matched.append(parts[6] == "MATCHED")
    recorder.add(
        "예약 수량 보존과 대사",
        "PASS" if conserved and all(conserved) and all(matched) else "FAIL",
        reservations.replace("\n", " ; ") or "no reservations",
    )

    order_ids = [line.split("|")[1] for line in reservations.splitlines() if line]
    if order_ids:
        placed = _psql(
            "select order_id || '|' || symbol || '|' || side || '|' || quantity || '|'"
            " || submitted_price_krw || '|' || status || '|' || brokerage_mode"
            f" from public.orders where order_id in ({_quoted(order_ids)}) order by order_id;"
        )
        recorder.add(
            "bridge → Brokerage 실제 주문",
            "PASS" if len(placed.splitlines()) == len(order_ids) else "FAIL",
            placed.replace("\n", " ; ") or "no orders",
        )
    else:
        recorder.add("bridge → Brokerage 실제 주문", "FAIL", "예약에 주문이 결속되지 않았다")

    decisions = _psql(
        "select checkpoint.run_id || '|' || coalesce(checkpoint.decision_id,'NULL') || '|'"
        " || coalesce(decision.decision_id,'NO-JOIN')"
        " from public.automation_runtime_checkpoint checkpoint"
        " left join public.decisions decision on decision.decision_id = checkpoint.decision_id"
        f" where checkpoint.run_id in ('{buy_run_id}','{sell_run_id}') order by checkpoint.run_id;"
    )
    joined = [
        line
        for line in decisions.splitlines()
        if line and "NULL" not in line and "NO-JOIN" not in line
    ]
    recorder.add(
        "RiskEngine 판단 결속",
        "PASS" if len(joined) == 2 else "FAIL",
        f"{decisions.replace(chr(10), ' ; ') or 'no checkpoints'} "
        "(decision_id가 null이면 위험 판정 구간을 건너뛴 것)",
    )

    positions = _psql(
        "select symbol || '|' || status || '|entry' || coalesce(entry_average_fill_price_krw::text,'-')"
        " || '|exit' || coalesce(exit_average_fill_price_krw::text,'-')"
        " || '|pnl' || coalesce(realized_pnl_krw::text,'-') || '|' || coalesce(exit_reason,'-')"
        " from public.automation_positions where user_id = '" + _OWNER + "'"
        " order by opened_session_date desc limit 3;"
    )
    closed = [line for line in positions.splitlines() if "|CLOSED|" in line and "|pnl-" not in line]
    recorder.add(
        "포지션 개설·청산과 실현손익",
        "PASS" if closed else "FAIL",
        positions.replace("\n", " ; ") or "no positions",
    )

    lineage = _psql(
        "select string_agg(reason, ' → ' order by sequence) from public.automation_account_lineage"
        " where user_id = '" + _OWNER + "';"
    )
    expected_lineage = (
        lineage.startswith("ARM_BASELINE") and "BUY_FILL" in lineage and "SELL_FILL" in lineage
    )
    recorder.add(
        "기대계좌 lineage 전진",
        "PASS" if expected_lineage else "FAIL",
        lineage or "no lineage",
    )

    status_code, status_body = api.request("GET", "/api/v2/automation/status")
    runs_code, runs_body = api.request("GET", "/api/v2/automation/runs")
    positions_code, positions_body = api.request("GET", "/api/v2/automation/positions")
    summary = ((positions_body.get("data") or {}).get("realizedSummary")) or {}
    recorder.add(
        "HTTP v2 노출",
        "PASS" if {status_code, runs_code, positions_code} == {200} else "FAIL",
        f"status={status_code} runs={runs_code} positions={positions_code} "
        f"realizedSummary={json.dumps(summary, ensure_ascii=False)[:200]}",
    )

    signal_code, signal_body = api.request("GET", "/api/v2/signals/005930")
    ingest_code, ingest_body = api.request("GET", "/api/v1/artifacts/ingest-status")
    recorder.add(
        "상류 노출 (신호·적재 상태)",
        "PASS" if signal_code == 200 and ingest_code == 200 else "FAIL",
        f"signals={signal_code} ingest-status={ingest_code} "
        f"{json.dumps((ingest_body.get('data') or {}), ensure_ascii=False)[:200]}",
    )


# --------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:18080")
    parser.add_argument("--out", default="")
    parser.add_argument("--keep", action="store_true", help="정리를 건너뛴다. 디버깅 전용")
    args = parser.parse_args(argv[1:])

    if os.environ.get(_OPT_IN) != "1":
        print(f"{_OPT_IN}=1 을 명시해야 실행된다.", file=sys.stderr)
        return 2

    recorder = Recorder()
    before = snapshot()
    recorder.add(
        "시작 스냅샷",
        "INFO",
        " ".join(f"{table}={len(rows)}" for table, rows in sorted(before.items())),
    )

    platform_switched = False
    try:
        # Spring의 brokerage 어댑터를 켠다. 그 포트에는 테스트가 세운 오프라인 서버가 응답하고,
        # 실제 KIS를 부르는 production brokerage 서버는 계속 꺼져 있다.
        _compose("up", "-d", "--no-deps", "decision-platform", offline_brokerage=True)
        platform_switched = True
        _wait_healthy()
        _publish_driver()
        _start_offline_brokerage(account_id=_ACCOUNT, cash_krw=INITIAL_CASH_KRW)
        recorder.add(
            "오프라인 brokerage",
            "PASS",
            "Spring gRPC 어댑터 ON, KIS transport만 대체. provider 물리 호출 0",
        )

        # brokerage 다리를 먼저 두드린다. 여기서 막히면 run은 항상 `AUTOMATION_BRIDGE_FAILED`로
        # 죽는데 그 예외에는 이유가 없어서, 원인을 판정표에 남기려면 이 단계가 필요하다.
        probe = _driver("probe-bridge", "--account", _ACCOUNT, allow_failure=True)
        bridge_ready = bool(probe.get("reachable"))
        recorder.add(
            "bridge → Brokerage 다리",
            "PASS" if bridge_ready else "FAIL",
            "BUYABLE 왕복 성공"
            if bridge_ready
            else (
                f"{probe.get('error')} — automation runtime bridge는 shared secret으로만 인증하고 "
                "Spring SecurityContext를 세우지 않는다. brokerage/decision 서비스는 "
                "`AuthenticatedActorRef.current()`로 actor capability를 발급하므로 이 다리로 오는 "
                "모든 명령이 닫힌다"
            ),
        )

        sessions = xkrx_sessions(2)
        buy_session, sell_session = sessions[0], sessions[1]
        certification_session = previous_xkrx_session(buy_session)
        recorder.add(
            "대상 세션",
            "INFO",
            f"인증 {certification_session} → 매수 {buy_session} → 매도 {sell_session}",
        )

        seed_calendar([certification_session, *sessions])
        recorder.add(
            "XKRX 달력 씨딩", "PASS", f"{len(sessions) + 1}개 세션 (pinned 4.13.2 실제 날짜)"
        )

        seed_market_data(sessions)
        recorder.add(
            "시장데이터 매니페스트 씨딩",
            "PASS",
            f"SEED 1 + DAILY {len(sessions)} (체인 가드 순서 준수)",
        )

        imported = _driver(
            "import-bundle",
            "--session",
            buy_session.isoformat(),
            "--buy",
            "005930",
            "--ordinal",
            "1",
        )
        recorder.add(
            "Team B 번들 적재 (매수 세션)",
            "PASS" if imported.get("outcome") == "IMPORTED" else "FAIL",
            f"{imported.get('outcome')} run={imported.get('runId')} "
            f"bundle={str(imported.get('bundleSha256'))[:16]}",
        )
        pointer = _psql(
            "select count(*) || '/' || count(distinct bundle_sha256)"
            " from public.current_p1_return_signal_pointer;"
        )
        recorder.add(
            "REAL_TEAM_B 포인터",
            "PASS" if pointer == "31/1" else "FAIL",
            f"{pointer} (31개 종목이 한 번들에서 나와야 arm이 열린다)",
        )

        gate = _compose(
            "run",
            "--rm",
            # postgres는 이미 healthy다. `--no-deps` 없이는 compose가 의존 컨테이너를 재생성한다.
            "--no-deps",
            "-e",
            f"P1_CERTIFICATION_RECEIPT_SHA256={'c' * 64}",
            "-e",
            f"P1_CERTIFICATION_SESSION_DATE={certification_session.isoformat()}",
            "-e",
            f"P1_RELEASE_BINDING_SHA256={'d' * 64}",
            "-e",
            f"P1_SOURCE_BINDING_SHA256={'e' * 64}",
            "automation-gate-author",
        )
        recorder.add(
            "activation gate 저술",
            "PASS" if "AUTOMATION_ACTIVATION_GATE=PASS" in gate else "FAIL",
            " ".join(line for line in gate.split() if "=" in line)[:300],
        )

        api = Api(args.api)
        api.login()
        status_code, status_body = api.request("GET", "/api/v2/automation/status")
        facts = status_body.get("data") or {}
        recorder.add(
            "arm 이전 상태",
            "PASS" if status_code == 200 else "FAIL",
            f"HTTP {status_code} controlVersion={facts.get('controlVersion')} "
            f"policyVersion={facts.get('policyVersion')} blockers={facts.get('blockers')}",
        )

        # status의 policyVersion은 control이 정책을 물기 전까지 비어 있다. 실제 최신 버전은
        # `automation_policy_versions`에 있고, PUT은 그 값을 expectedVersion으로 요구한다.
        current_policy_version = int(
            _psql(
                "select coalesce(max(version), 0) from public.automation_policy_versions"
                f" where user_id = '{_OWNER}';"
            )
            or 0
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        policy_code, policy_body = api.request(
            "PUT",
            "/api/v2/automation/policy",
            {
                # 원칙 한도가 실제로 물리는 값으로 둔다. 보수 프리셋의 종목당 상한보다 크게 잡아
                # 수량 산정이 상수가 아니라 원칙에서 나오는지 보이게 한다.
                "capitalLimitKrw": 5_000_000,
                "expectedVersion": current_policy_version,
                "stopLossBps": 500,
                "takeProfitBps": 1000,
            },
            idempotency_key=f"p1-e2e-policy-{stamp}",
        )
        policy_data = policy_body.get("data") or {}
        recorder.add(
            "정책 설정",
            "PASS" if policy_code == 200 else "FAIL",
            f"HTTP {policy_code} {json.dumps(policy_data or policy_body.get('error'), ensure_ascii=False)[:260]}",
        )
        if policy_code != 200:
            raise PipelineError("정책을 설정하지 못해 arm으로 갈 수 없다")

        arm_code, arm_body = api.request(
            "POST",
            "/api/v2/automation/arm",
            {
                "accountId": _ACCOUNT,
                "expectedControlVersion": int(facts.get("controlVersion") or 1),
                "expectedPolicyVersion": int(policy_data.get("version") or 1),
                "policyId": str(policy_data.get("policyId")),
            },
            idempotency_key=f"p1-e2e-arm-{stamp}",
        )
        arm_data = arm_body.get("data") or {}
        recorder.add(
            "arm",
            "PASS" if arm_code == 200 and arm_data.get("controlState") == "ARMED" else "FAIL",
            f"HTTP {arm_code} {json.dumps(arm_data or arm_body.get('error'), ensure_ascii=False)[:300]}",
        )
        if arm_code != 200:
            raise PipelineError("arm이 열리지 않아 관통을 계속할 수 없다")

        first = _driver("drive", "--session", buy_session.isoformat(), "--roll")
        recorder.add(
            "세션 1 구동 (매수)",
            "PASS" if first.get("finalState") == "COMPLETED" else "FAIL",
            f"{first.get('finalState')} 전이={'→'.join(first.get('transitions') or [])} "
            f"order={first.get('orderId')} decision={first.get('decisionId')} "
            f"cash={first.get('ledgerCashKrw')} pos={first.get('ledgerPositions')}",
        )

        imported_sell = _driver(
            "import-bundle",
            "--session",
            sell_session.isoformat(),
            "--sell",
            "005930",
            "--ordinal",
            "2",
        )
        recorder.add(
            "Team B 번들 적재 (매도 세션)",
            "PASS" if imported_sell.get("outcome") == "IMPORTED" else "FAIL",
            f"{imported_sell.get('outcome')} bundle={str(imported_sell.get('bundleSha256'))[:16]}",
        )

        second = _driver(
            "drive",
            "--session",
            sell_session.isoformat(),
            "--cash",
            str(first.get("ledgerCashKrw")),
            "--positions",
            json.dumps(first.get("ledgerPositions") or {}),
        )
        recorder.add(
            "세션 2 구동 (매도)",
            "PASS" if second.get("finalState") == "COMPLETED" else "FAIL",
            f"{second.get('finalState')} 전이={'→'.join(second.get('transitions') or [])} "
            f"order={second.get('orderId')} cash={second.get('ledgerCashKrw')} "
            f"pos={second.get('ledgerPositions')}",
        )

        assert_pipeline(
            recorder,
            buy_run_id=str(first.get("runId")),
            sell_run_id=str(second.get("runId")),
            api=api,
        )
    except PipelineError as error:
        recorder.add("관통 중단", "FAIL", str(error))
    finally:
        _stop_offline_brokerage()
        if platform_switched and not args.keep:
            # brokerage 어댑터를 끈 기본 구성으로 되돌린다. 테스트가 스택 설정을 남기지 않는다.
            try:
                _compose("up", "-d", "--no-deps", "decision-platform")
                _wait_healthy()
                recorder.add("스택 구성 복원", "PASS", "brokerage gRPC 어댑터 OFF로 되돌림")
            except PipelineError as error:
                recorder.add("스택 구성 복원", "FAIL", str(error))
        if args.keep:
            recorder.add("정리", "INFO", "--keep 이므로 건너뛴다. 배포 증거로 쓰지 않는다")
        else:
            cleanup(before, recorder)

    report = {
        "contractId": "p1-full-pipeline-e2e.v1",
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "kisCalls": 0,
        "steps": [step.projection() for step in recorder.steps],
        "verdict": "FAIL" if recorder.failed() else "PASS",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        print(f"\n판정표: {destination}")
    print(f"\nP1_FULL_PIPELINE_E2E={report['verdict']}")
    return 1 if recorder.failed() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
