"""영역별 e2e runner가 함께 쓰는 손.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다. `tests/rehearsal/`·`tests/verification/`과 같은 경계다.

여기 있는 것은 전부 `full_pipeline_e2e.py`가 먼저 증명한 절차다. runner가 여섯 개로 늘면서
같은 절차를 여섯 번 베끼는 대신 한 곳으로 모았다. 특히 **스냅샷 차집합 정리**는 규약이라
runner마다 다시 쓰면 언젠가 하나가 `where true`로 미끄러진다.

규약:
  * 판정은 관측에서만 나온다. 상수로 박은 PASS는 두지 않는다.
  * 시작 시 기존 행의 기본키를 스냅샷하고, 끝에서 **차집합만** 지운다. DB 볼륨은 삭제하지 않는다.
  * 정리에 실패하면 runner는 FAIL이다.
  * 실패 메시지에 요청 본문·토큰·자격증명을 남기지 않는다. 상태 코드와 코드 이름만 남긴다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

REPOSITORY: Final = Path(__file__).resolve().parents[5]
DEPLOY: Final = REPOSITORY / "deploy/p1"
STATE: Final = DEPLOY / ".state-app"
# Docker Desktop을 쓸 때 Windows CLI는 WSL 경로를 번역하지 못해 bind mount가 빈 디렉터리가 된다.
# `p1ctl`과 같은 경로의 Linux CLI를 명시한다.
DOCKER: Final = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"
POSTGRES: Final = "capstone-p1-postgres-1"
PLATFORM: Final = "capstone-p1-decision-platform-1"
PROJECT: Final = "capstone-p1"
BASE_URL: Final = "http://127.0.0.1:18080"
OWNER: Final = "usr_demo_user"
SEED_SOURCE: Final = "P1_E2E_FIXTURE"
TIMEOUT: Final = 180


class HarnessError(RuntimeError):
    """확인 절차 자체가 성립하지 못했다. 개별 단언 실패와 구분한다."""


def require_opt_in(name: str) -> None:
    if os.environ.get(name) != "1":
        print(f"{name}=1 must be set explicitly", file=sys.stderr)
        raise SystemExit(2)


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
            raise HarnessError(f"unknown verdict: {verdict}")
        self.steps.append(Step(name, verdict, detail[:6000]))
        marker = {"PASS": "PASS", "FAIL": "FAIL", "INFO": "····"}[verdict]
        print(f"[{marker}] {name}: {detail[:6000]}", flush=True)

    def failed(self) -> bool:
        return any(step.verdict == "FAIL" for step in self.steps)


def run(command: list[str], *, stdin: str | None = None, env: dict[str, str] | None = None) -> str:
    merged = dict(os.environ)
    merged.update(env or {})
    result = subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
        env=merged,
    )
    if result.returncode != 0:
        # 인자에 값이 실릴 수 있으므로 실패 메시지에는 명령 이름만 남긴다.
        head = command[1] if len(command) > 1 else ""
        raise HarnessError(
            f"command failed ({result.returncode}): {Path(command[0]).name} {head}…\n"
            f"{result.stdout.strip()[-4000:]}\n{result.stderr.strip()[-4000:]}"
        )
    return result.stdout


def psql(sql: str) -> str:
    """superuser 읽기·쓰기. RLS FORCE 때문에 runtime role로 읽으면 0행이 나와 거짓 통과한다."""

    return run(
        [
            DOCKER,
            "exec",
            "-i",
            POSTGRES,
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


def platform(command: str) -> str:
    return run([DOCKER, "exec", PLATFORM, "sh", "-c", command]).strip()


def wait_healthy(seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = run([DOCKER, "inspect", "-f", "{{.State.Health.Status}}", PLATFORM]).strip()
        if state == "healthy":
            return
        time.sleep(3)
    raise HarnessError("decision-platform did not become healthy")


class Api:
    """읽기와 명시한 상태 변경만 한다. 인증은 로컬 secret 파일에서 읽는다.

    비밀번호는 파일에서 직접 읽어 요청에만 싣는다. 값을 출력하거나 기록하지 않는다.
    """

    def __init__(self, base_url: str = BASE_URL) -> None:
        self._base = base_url.rstrip("/")
        self._token: str | None = None
        self._username: str | None = None

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def login(self, username: str = "demo-user") -> None:
        password = (STATE / f"secrets/{username}.password").read_text(encoding="utf-8").strip()
        status, payload = self.request(
            "POST", "/api/v1/auth/login", {"username": username, "password": password}
        )
        token = (payload.get("data") or {}).get("accessToken")
        if status != 200 or not isinstance(token, str) or not token:
            raise HarnessError(f"{username} login failed: HTTP {status}")
        self._token = token
        self._username = username

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self._base}{path}", method=method, data=data)
        request.add_header("Content-Type", "application/json")
        if self._token is not None:
            request.add_header("Authorization", f"Bearer {self._token}")
        if idempotency_key is not None:
            request.add_header("X-Idempotency-Key", idempotency_key)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except json.JSONDecodeError:
                return error.code, {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise HarnessError(f"api unreachable: {error}") from error


# --------------------------------------------------------------------------------------
# 스냅샷과 정리
# --------------------------------------------------------------------------------------

SNAPSHOT_TABLES: Final[tuple[tuple[str, str], ...]] = (
    ("p1_return_artifact_bundle", "bundle_sha256"),
    ("automation_runs", "run_id"),
    ("orders", "order_id"),
    ("decisions", "decision_id"),
    # 판단 멱등 결과를 남기면 다음 실행이 이미 지운 판단을 재생해 온다. 그러면 체크포인트가
    # "exact Decision intent mismatch"로 닫히는데, 원인이 제품이 아니라 정리 누락이다.
    ("decision_idempotency_results", "idempotency_result_id"),
    ("automation_positions", "position_id"),
    ("automation_account_lineage", "lineage_id"),
    ("automation_candidate_evidence", "run_id || '|' || symbol || '|' || citation_id"),
    ("automation_candidate_screenings", "run_id || '|' || symbol"),
    ("automation_ai_provider_operations", "run_id || '|' || phase"),
    ("automation_ai_judgements", "run_id || '|' || checkpoint_version"),
    ("automation_v3_usage", "run_id"),
    ("automation_runtime_events", "event_id"),
    ("automation_events", "event_id"),
    ("automation_processed_ticks", "tick_identity_hash"),
    ("automation_policy_idempotency", "scope_hash"),
    ("automation_policy_versions", "policy_id || '|' || version"),
    ("automation_activation_gate", "user_id"),
    ("automation_runtime_schedule", "schedule_id"),
    ("automation_control_idempotency", "scope_hash"),
    ("trading_sessions", "session_date::text"),
    ("market_data_manifests", "manifest_sha256"),
    # 수집 writer가 남기는 관측도 테스트가 만든 것만 되돌린다.
    ("market_quote_observations", "observation_id"),
    ("deterministic_risk_observations", "observation_id"),
    ("daily_order_count_observations", "observation_id"),
    ("portfolio_balance_observations", "observation_id"),
    ("dashboard_artifact_views", "artifact_id"),
    ("artifact_ingest_projection", "artifact_id"),
    # 아래 넷은 API 표면 runner가 건드리는 소유자 자산이다. 관통 runner는 만들지 않지만
    # 같은 스냅샷을 쓰면 어느 runner가 돌든 되돌릴 범위가 하나로 정해진다.
    ("principles", "principle_id"),
    ("principle_versions", "principle_version_id"),
    ("journals", "journal_id"),
    ("journal_idempotency", "scope_hash"),
    ("rag_consent_events", "consent_event_id"),
)


def snapshot() -> dict[str, list[str]]:
    taken: dict[str, list[str]] = {}
    for table, key in SNAPSHOT_TABLES:
        rows = psql(f"select {key} from public.{table};")
        taken[table] = [line for line in rows.splitlines() if line]
    return taken


def quoted(values: list[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values) or "''"


def cleanup_statements(before: dict[str, list[str]]) -> list[str]:
    """테스트가 만든 것만 지우는 문장을 FK 안전 순서로 만든다.

    `where true`를 쓰지 않는다. 소유자별로 한 행씩만 갖는 표(activation gate, schedule,
    control idempotency)에서 그것은 테스트가 만들지 않은 다른 소유자의 행까지 지운다.
    """

    return [
        "begin;",
        # append-only 트리거는 정상 운영 보호장치다. 정리 단계에서만 superuser 권한으로 잠시 끈다.
        # 테스트가 만든 행만 지우고 트랜잭션이 끝나면 원래대로 돌아온다.
        "set local session_replication_role = replica;",
        # automation 하위부터 지운다. FK 안전 순서다.
        "delete from public.automation_candidate_evidence where run_id || '|' || symbol || '|' || citation_id not in "
        f"({quoted(before['automation_candidate_evidence'])});",
        "delete from public.automation_candidate_screenings where run_id || '|' || symbol not in "
        f"({quoted(before['automation_candidate_screenings'])});",
        "delete from public.automation_ai_provider_operations where run_id || '|' || phase not in "
        f"({quoted(before['automation_ai_provider_operations'])});",
        "delete from public.automation_ai_judgements where run_id || '|' || checkpoint_version not in "
        f"({quoted(before['automation_ai_judgements'])});",
        "delete from public.automation_v3_usage where run_id not in "
        f"({quoted(before['automation_v3_usage'])});",
        "delete from public.automation_runtime_events where event_id not in "
        f"({quoted(before['automation_runtime_events'])});",
        f"delete from public.automation_events where event_id not in ({quoted(before['automation_events'])});",
        "delete from public.automation_processed_ticks where tick_identity_hash not in "
        f"({quoted(before['automation_processed_ticks'])});",
        "delete from public.automation_policy_idempotency where scope_hash not in "
        f"({quoted(before['automation_policy_idempotency'])});",
        "delete from public.automation_account_lineage where lineage_id not in "
        f"({quoted(before['automation_account_lineage'])});",
        "delete from public.automation_positions where position_id not in "
        f"({quoted(before['automation_positions'])});",
        "delete from public.automation_order_reservations where run_id not in "
        f"({quoted(before['automation_runs'])});",
        "delete from public.automation_runtime_checkpoint where run_id not in "
        f"({quoted(before['automation_runs'])});",
        "delete from public.automation_runtime_claim where run_id not in "
        f"({quoted(before['automation_runs'])});",
        f"delete from public.automation_runs where run_id not in ({quoted(before['automation_runs'])});",
        "delete from public.automation_runtime_schedule where schedule_id not in "
        f"({quoted(before['automation_runtime_schedule'])});",
        "delete from public.automation_control_idempotency where scope_hash not in "
        f"({quoted(before['automation_control_idempotency'])});",
        "delete from public.automation_activation_gate where user_id not in "
        f"({quoted(before['automation_activation_gate'])});",
        # 주문과 판단
        f"delete from public.orders where order_id not in ({quoted(before['orders'])});",
        "delete from public.decision_idempotency_results where idempotency_result_id not in "
        f"({quoted(before['decision_idempotency_results'])});",
        f"delete from public.decisions where decision_id not in ({quoted(before['decisions'])});",
        # Team B 흔적. REAL_TEAM_B 표식을 남기지 않는 것이 이 절의 목적이다.
        "delete from public.artifact_ingest_projection where artifact_id not in "
        f"({quoted(before['artifact_ingest_projection'])});",
        "delete from public.dashboard_artifact_views where artifact_id not in "
        f"({quoted(before['dashboard_artifact_views'])});",
        "delete from public.p1_return_signal_projection where bundle_sha256 not in "
        f"({quoted(before['p1_return_artifact_bundle'])});",
        # bundle 로의 FK 가 ON DELETE RESTRICT 다. 정리 단계는 FK 를 끄고 지우므로 이 표를
        # 빼먹으면 bundle 만 사라지고 signal 행이 orphan 으로 남는다. bundle 보다 먼저 지운다.
        "delete from public.p1_return_model_seed_signal where bundle_sha256 not in "
        f"({quoted(before['p1_return_artifact_bundle'])});",
        "delete from public.p1_return_artifact_bundle where bundle_sha256 not in "
        f"({quoted(before['p1_return_artifact_bundle'])});",
        # 달력과 시장데이터 fixture
        f"delete from public.trading_sessions where chosen_source_id = '{SEED_SOURCE}';",
        # bar 의 FK 가 매니페스트를 참조하므로 매니페스트보다 먼저 지운다. 스냅샷에 없던
        # 매니페스트의 bar 만 지우므로 기존 실측 바는 그대로 남는다.
        "delete from public.market_data_bars where manifest_sha256 not in "
        f"({quoted(before['market_data_manifests'])});",
        "delete from public.market_data_manifests where manifest_sha256 not in "
        f"({quoted(before['market_data_manifests'])});",
        "delete from public.portfolio_position_observations where balance_observation_id not in "
        f"({quoted(before['portfolio_balance_observations'])});",
        "delete from public.portfolio_balance_observations where observation_id not in "
        f"({quoted(before['portfolio_balance_observations'])});",
        "delete from public.market_quote_observations where observation_id not in "
        f"({quoted(before['market_quote_observations'])});",
        "delete from public.deterministic_risk_observations where observation_id not in "
        f"({quoted(before['deterministic_risk_observations'])});",
        "delete from public.daily_order_count_observations where observation_id not in "
        f"({quoted(before['daily_order_count_observations'])});",
        # 정책 버전은 append-only다. 테스트가 만든 버전만 지운다.
        "delete from public.automation_policy_versions where policy_id || '|' || version not in "
        f"({quoted(before['automation_policy_versions'])});",
        # 소유자 자산. 원칙은 버전이 먼저다.
        "delete from public.journal_idempotency where scope_hash not in "
        f"({quoted(before['journal_idempotency'])});",
        f"delete from public.journals where journal_id not in ({quoted(before['journals'])});",
        "delete from public.principle_versions where principle_version_id not in "
        f"({quoted(before['principle_versions'])});",
        f"delete from public.principles where principle_id not in ({quoted(before['principles'])});",
        # 동의 원장은 소유자별로 단조 증가하는 append-only다. 테스트가 만든 것은 항상 꼬리이므로
        # 차집합 삭제가 곧 꼬리 삭제다. 중간을 파내지 않는다.
        "delete from public.rag_consent_events where consent_event_id not in "
        f"({quoted(before['rag_consent_events'])});",
        # 계좌 통제를 arm 이전 상태로 되돌린다.
        "update public.automation_control set control_state='DISARMED', brokerage_mode='INTERNAL_PAPER',"
        " certification_status='NOT_REQUIRED_INTERNAL_PAPER', policy_id=null, policy_version=null,"
        " principle_version_id=null, principle_version=null, team_b_integrity_receipt_sha256_v2=null,"
        " initial_account_digest_v2=null, expected_account_digest_v2=null,"
        f" expected_account_projection_v2=null where user_id='{OWNER}';",
        "commit;",
    ]


def cleanup(before: dict[str, list[str]], recorder: Recorder) -> None:
    """테스트가 만든 것만 지운다. 기존 흔적은 건드리지 않는다."""

    try:
        psql("\n".join(cleanup_statements(before)))
    except HarnessError as error:
        recorder.add("정리", "FAIL", f"테스트가 만든 행을 되돌리지 못했다: {error}")
        return

    residue = psql(
        "select 'bundles=' || count(*) from public.p1_return_artifact_bundle"
        f" where bundle_sha256 not in ({quoted(before['p1_return_artifact_bundle'])});"
        " select 'seedSignals=' || count(*) from public.p1_return_model_seed_signal"
        f" where bundle_sha256 not in ({quoted(before['p1_return_artifact_bundle'])});"
        " select 'runs=' || count(*) from public.automation_runs"
        f" where run_id not in ({quoted(before['automation_runs'])});"
        " select 'orders=' || count(*) from public.orders"
        f" where order_id not in ({quoted(before['orders'])});"
        " select 'principles=' || count(*) from public.principles"
        f" where principle_id not in ({quoted(before['principles'])});"
        " select 'journal=' || count(*) from public.journals"
        f" where journal_id not in ({quoted(before['journals'])});"
        f" select 'calendar=' || count(*) from public.trading_sessions where chosen_source_id = '{SEED_SOURCE}';"
    ).replace("\n", " ")
    clean = all(part.endswith("=0") for part in residue.split())
    recorder.add("정리", "PASS" if clean else "FAIL", f"잔여 {residue}")


def write_report(*, contract_id: str, marker: str, recorder: Recorder, out: str) -> dict[str, Any]:
    report = {
        "contractId": contract_id,
        "steps": [step.projection() for step in recorder.steps],
        "verdict": "FAIL" if recorder.failed() else "PASS",
    }
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n판정표: {path}")
    print(f"\n{marker}={report['verdict']}")
    return report
