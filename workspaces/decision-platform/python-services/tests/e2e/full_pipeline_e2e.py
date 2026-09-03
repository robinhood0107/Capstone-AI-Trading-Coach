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
import hashlib
import json
import os
import subprocess
import uuid
import time
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from . import team_b_bundle
from .harness import (
    Api,
    HarnessError as PipelineError,
    Recorder,
    cleanup,
    psql as _psql,
    quoted as _quoted,
    run as _run,
    snapshot,
    wait_healthy as _wait_healthy,
)

_OPT_IN: Final = "P1_FULL_PIPELINE_E2E"


def _use_real_seed() -> bool:
    """실물 seed 포인터로 돌지 여부. 기본은 합성 번들이다.

    합성 번들은 buy/sell 종목을 강제해 상태 기계만 보는 데 쓴다. 실물 seed 는 production 과
    같은 번들·모델로 돌아 실제 사용을 검증하지만 어느 종목이 뽑히는지는 모델이 정한다.
    """

    return os.environ.get("P1_E2E_USE_REAL_SEED", "").strip() == "1"


def _existing_pointer() -> dict[str, Any]:
    """이미 적재된 실물 seed 포인터를 호스트에서 읽는다.

    `./capstone up` 이 deploy/p1/seed/team-b 를 import 하고 그 포인터로 arm 이 열린다. 추론
    서버도 같은 seed 를 기동 시 로드하므로 이 포인터를 쓸 때만 요청의 bundleSha256 대조가 맞는다.
    """

    row = _psql(
        "select bundle_sha256 || '|' || artifact_id"
        " from public.current_p1_return_model_pointer limit 1;"
    ).strip()
    if "|" not in row:
        raise PipelineError(
            "적재된 Team B 포인터가 없다. ./capstone up 이 실물 seed 를 넣어야 한다"
        )
    bundle_sha256, artifact_id = row.split("|", 1)
    return {
        "outcome": "EXISTING_POINTER",
        "bundleSha256": bundle_sha256,
        "artifactId": artifact_id,
        "runId": None,
        "packetSha256": None,
    }


def _assert_universe_matches_catalog() -> None:
    """번들의 exact-31 이 커밋된 카탈로그와 같은지 호스트에서 대조한다.

    번들 쪽 값은 컨테이너에서도 읽혀야 하므로 상수다. 그 대신 조용한 드리프트를 여기서 막는다.
    카탈로그가 바뀌었는데 번들이 그대로면 테스트는 통과하면서 실제 명부를 검증하지 못한다.
    """

    catalog = json.loads(
        (_REPOSITORY / "contracts/catalogs/p1-return-universe.v1.json").read_text(encoding="utf-8")
    )
    expected = {str(entry["symbol"]) for entry in catalog["symbols"]}
    actual = set(team_b_bundle.UNIVERSE)
    if actual != expected:
        raise PipelineError(
            "exact-31 번들 종목이 카탈로그와 다르다: "
            f"번들만={sorted(actual - expected)} 카탈로그만={sorted(expected - actual)}"
        )


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
# 드라이버의 FIXTURE_PRICES와 같은 값이어야 위험 판정과 주문 산정이 같은 시세를 본다.
# DB 에 바가 없을 때만 쓰는 최소 fallback 이다. 정상 경로는 _fixture_prices() 가
# market_data_bars 의 마지막 종가를 읽어 exact-31 전체를 덮는다.
_FIXTURE_PRICES: Final[dict[str, int]] = {"005930": 70_000, "000660": 180_000}
# 호가 격자. _limit_price 가 매수 지정가에 이 값을 더한다.
_TICK_KRW: Final = 100
# 계약 automation-policy.v2 의 capitalLimitKrw multipleOf.
_CAPITAL_GRANULARITY_KRW: Final = 10_000


def _fixture_prices() -> dict[str, int]:
    """exact-31 전체의 호가 기준가를 market_data_bars 의 마지막 종가에서 만든다.

    실물 seed 로 돌면 매수 후보를 모델이 정하므로 특정 종목만 호가를 넣어 두면 그 후보의
    수량을 산정할 수 없어 PRECHECK 이 SKIPPED_NO_ACTION 으로 닫힌다. 값을 지어내지 않고
    LSTM 이 본 것과 같은 출처를 쓴다.
    """

    rows = _psql(
        "select symbol || '|' || round(close_price)::bigint from public.market_data_bars"
        " where session_date = (select max(session_date) from public.market_data_bars)"
        " order by symbol;"
    )
    prices = {
        line.split("|", 1)[0]: int(line.split("|", 1)[1])
        for line in rows.splitlines()
        if "|" in line
    }
    # 강제 매수 시나리오가 쓰는 두 종목은 항상 있어야 한다.
    for symbol, fallback in _FIXTURE_PRICES.items():
        prices.setdefault(symbol, fallback)
    return prices or dict(_FIXTURE_PRICES)


# automation._MAX_OPEN_POSITIONS 와 같은 값이다. 정책 표의 컬럼이 아니라 런타임 상수이므로
# 여기서 다시 적되 어긋나면 아래 검증이 잡는다.
_SLOTS: Final = 5


def _capital_limit_krw() -> int:
    """유니버스 최고가가 슬롯에 들어가는 자본 한도를 만든다.

    슬롯 예산은 capital_limit // 5 이고 매수 지정가는 호가 + tick 이다. 그래서 최고가 종목이
    최상위 후보가 되는 날에도 1주 이상이 들어가려면 슬롯 예산이 (최고가 + tick) 보다 커야 한다.

    상수로 두면 안 된다. 실물 seed 로 돌면 어느 종목이 뽑히는지 모델이 정하므로, 유니버스에
    비싼 종목이 하나라도 있으면 그날 관통이 주문까지 가지 못한다. 값을 지어내지 않고
    호가 fixture 와 같은 출처(마지막 종가)에서 파생시킨다.
    """

    prices = _fixture_prices()
    if not prices:
        raise PipelineError("호가 fixture 가 비어 자본 한도를 정할 수 없다")
    slot_floor = max(prices.values()) + _TICK_KRW
    # 3배는 여러 주가 들어가 수량이 상수가 아니라 예산·원칙에서 나오는 것을 보이게 하는 여유다.
    raw = slot_floor * _SLOTS * 3
    # 계약이 multipleOf 10000 을 요구한다(automation-policy.v2). 올림해서 하한을 지킨다.
    return -(-raw // _CAPITAL_GRANULARITY_KRW) * _CAPITAL_GRANULARITY_KRW


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
            **_observed_rag_environment(),
        },
    )


# compose는 RAG 플래그를 자기 환경에서 치환한다. 여기서 넘겨 주지 않으면 파일의 기본값인
# 꺼짐으로 컨테이너가 다시 선다. 그러면 이 러너가 스택 설정을 바꿔 놓고 끝나고, 뒤이어 도는
# RAG 검사가 "로컬 루트가 없다"로 죽는다. 원인은 제품이 아니라 복원 누락이다.
#
# 어떤 값이어야 하는지는 우리가 정하지 않는다. 지금 도는 컨테이너에게 물어 그대로 되돌린다.
_RAG_ENVIRONMENT_KEYS: Final = (
    ("RAG_V2_GRPC_ENABLED", "P1_RAG_V2_ENABLED"),
    ("RAG_V2_VERTEX_ENABLED", "P1_RAG_V2_VERTEX_ENABLED"),
    ("RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED", "P1_RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED"),
    ("RAG_V2_VERTEX_HEAD_COMMIT", "P1_RAG_V2_VERTEX_HEAD_COMMIT"),
    ("RAG_V2_VERTEX_TREE_DIGEST", "P1_RAG_V2_VERTEX_TREE_DIGEST"),
    ("RAG_V2_VERTEX_CI_DIGEST", "P1_RAG_V2_VERTEX_CI_DIGEST"),
    ("RAG_V2_VERTEX_SECURITY_DIGEST", "P1_RAG_V2_VERTEX_SECURITY_DIGEST"),
    ("S4_9_STRONG_LLM_ENABLED", "P1_STRONG_LLM_ENABLED"),
    ("RAG_WEB_GOOGLE_BILLING_ACCOUNT_FINGERPRINT", "P1_GOOGLE_BILLING_ACCOUNT_FINGERPRINT"),
    ("BROKERAGE_GRPC_ENABLED", "P1_KIS_MOCK_ONLINE_ENABLED"),
    ("KIS_OFFLINE", "P1_KIS_OFFLINE"),
    ("P1_AUTOMATION_RUNTIME_ENABLED", "P1_AUTOMATION_RUNTIME_ENABLED"),
)
_rag_environment: dict[str, str] | None = None


def _observed_rag_environment() -> dict[str, str]:
    global _rag_environment
    if _rag_environment is None:
        _rag_environment = {}
        for container_name, compose_name in _RAG_ENVIRONMENT_KEYS:
            try:
                value = _run([_DOCKER, "exec", _PLATFORM, "printenv", container_name]).strip()
            except PipelineError:
                continue
            if value:
                _rag_environment[compose_name] = value
    return dict(_rag_environment)


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


def _disarm_if_armed(api: Api, recorder: Recorder) -> dict[str, Any] | None:
    """운영 중이면 제품 API 로 해제하고 복원에 필요한 값을 돌려준다.

    정책 변경은 DISARMED 를 요구하므로 ARMED 상태에서는 관통이 시작조차 못 한다. 그래서
    테스트가 스스로 해제한다 - 대시보드 disarm 버튼과 같은 경로다.

    돌려주는 값으로 끝에서 다시 arm 한다. None 이면 원래 꺼져 있었다는 뜻이므로 복원하지 않는다.
    """

    code, body = api.request("GET", "/api/v2/automation/status")
    facts = body.get("data") or {}
    if code != 200 or facts.get("controlState") != "ARMED":
        recorder.add(
            "운영 상태 확인",
            "PASS" if code == 200 else "FAIL",
            f"HTTP {code} controlState={facts.get('controlState')} - 해제 불필요",
        )
        return None

    account_id = _psql(
        f"select account_id from public.automation_control where user_id = '{_OWNER}';"
    ).strip()
    policy_row = _psql(
        "select policy_id || '|' || version from public.automation_policy_versions"
        f" where user_id = '{_OWNER}' order by version desc limit 1;"
    ).strip()
    restore = {
        "accountId": account_id,
        "policyId": policy_row.split("|", 1)[0] if "|" in policy_row else "",
        "policyVersion": int(policy_row.split("|", 1)[1]) if "|" in policy_row else 0,
    }

    code, body = api.request(
        "POST",
        "/api/v1/automation/disarm",
        {"expectedVersion": int(facts.get("controlVersion") or 1)},
        idempotency_key=f"e2e-disarm-{uuid.uuid4()}",
    )
    recorder.add(
        "운영 중 자동운용 해제",
        "PASS" if code == 200 else "FAIL",
        f"HTTP {code} 정책 변경이 DISARMED 를 요구하므로 먼저 내린다. 끝에서 되돌린다",
    )
    if code != 200:
        raise PipelineError("운영 중인 자동운용을 해제하지 못해 관통을 시작할 수 없다")
    return restore


def _rearm(api: Api, recorder: Recorder, restore: dict[str, Any]) -> None:
    """관통이 끝난 뒤 자동운용을 원래대로 되돌린다.

    정리 구문이 control 을 DISARMED 로 되돌리므로 이 단계가 없으면 테스트 한 번이 자동운용을
    꺼 버린다. 예약 시각이 와도 아무 일도 일어나지 않고 로그에도 흔적이 남지 않는다.
    """

    try:
        api.login()
        code, body = api.request("GET", "/api/v2/automation/status")
        facts = body.get("data") or {}
        code, body = api.request(
            "POST",
            "/api/v2/automation/arm",
            {
                "accountId": restore["accountId"],
                "expectedControlVersion": int(facts.get("controlVersion") or 1),
                "expectedPolicyVersion": int(restore["policyVersion"]),
                "policyId": restore["policyId"],
            },
            idempotency_key=f"e2e-rearm-{uuid.uuid4()}",
        )
        armed = body.get("data") or {}
        recorder.add(
            "자동운용 복원",
            "PASS" if code == 200 and armed.get("controlState") == "ARMED" else "FAIL",
            f"HTTP {code} controlState={armed.get('controlState')} "
            f"brokerageMode={armed.get('brokerageMode')} "
            f"version={armed.get('controlVersion')}",
        )
    except Exception as error:  # noqa: BLE001 - 복원 실패를 삼키면 자동운용이 꺼진 채 남는다
        recorder.add("자동운용 복원", "FAIL", f"{type(error).__name__}: {error}")


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
        # 실물 seed 로 돌면 매수 후보를 모델이 정하므로 exact-31 전체의 호가가 있어야 한다.
        # 드라이버는 컨테이너 안에서 market_data_bars 를 읽을 수 없으니(역할 분리) 여기서
        # 같은 출처를 읽어 넘긴다. 셸을 거치지 않아 JSON 을 그대로 전달할 수 있다.
        "-e",
        f"P1_E2E_FIXTURE_PRICES={json.dumps(_fixture_prices(), separators=(',', ':'))}",
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


# --------------------------------------------------------------------------------------
# 스냅샷과 정리
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


def seed_source_session_bars(manifest_sha256: str, session: date) -> int:
    """SEED 매니페스트 세션에 31종목 바를 넣는다.

    일일 추론은 sourceSession 과 이력 마지막 바가 같기를 요구한다(daily_inference.py:236).
    매니페스트만 앞으로 밀면 두 값이 어긋나 DAILY_INFERENCE_HISTORY_INCOMPLETE 가 된다.

    값은 각 종목의 마지막 실측 바에서 종목명 해시로 뽑은 결정론적 변동(-1.0% ~ +1.0%)이다.
    전부 같은 값이면 LogRet 이 0 이 되어 전 종목 HOLD 로 끝나고 주문 경로를 태우지 못한다.
    합성이라는 사실은 이 매니페스트에 매여 있고 정리에서 함께 사라진다.
    """

    last = _psql("select max(session_date) from public.market_data_bars;").strip()
    if not last:
        raise PipelineError("기존 시장데이터 바가 없어 소스 세션 바를 만들 수 없다")
    rows = [
        line
        for line in _psql(
            "select symbol || '|' || open_price || '|' || high_price || '|' || low_price"
            " || '|' || close_price || '|' || volume from public.market_data_bars"
            f" where session_date = date '{last}' order by symbol;"
        ).splitlines()
        if line.strip()
    ]
    if not rows:
        raise PipelineError(f"{last} 바를 읽지 못했다")

    receipt = hashlib.sha256(f"p1-e2e-source-bar/{session}".encode()).hexdigest()
    values = []
    for row in rows:
        symbol, _open, _high, _low, close, volume = row.split("|")
        drift = (int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % 21 - 10) / 1000.0
        base = float(close)
        new_close = round(base * (1.0 + drift), 2)
        new_open = round(base, 2)
        new_high = round(max(new_open, new_close) * 1.002, 2)
        new_low = round(min(new_open, new_close) * 0.998, 2)
        values.append(
            f"('{manifest_sha256}', 1, '{symbol}', date '{session}', {new_open}, {new_high},"
            f" {new_low}, {new_close}, {int(float(volume))}, 'KRW', 'COLLECTION_ONLY', '{receipt}')"
        )
    _psql(
        "insert into public.market_data_bars (manifest_sha256, generation, symbol, session_date,"
        " open_price, high_price, low_price, close_price, volume, currency, temporal_quality,"
        " source_receipt_sha256) values "
        + ",".join(values)
        + " on conflict (symbol, session_date, generation) do nothing;"
    )
    return len(values)


def quiesce_rival_portfolio_contexts() -> list[str]:
    """자동운용 계좌 하나만 ACTIVE로 남긴다. 되돌릴 수 있게 대상 id를 돌려준다.

    `JdbcPortfolioContextAdapter`는 KIS_MOCK ACTIVE 잔고 관측이 둘 이상이면 어느 계좌인지 고르지
    않고 `CONFLICT`로 닫는다. 그러면 RiskEngine의 지표가 하나도 조립되지 않아 모든 판단이 HOLD가
    된다 — 개별 원천이 비어서가 아니다.

    이 DB에는 Team A acceptance seed가 남긴 다른 scope의 관측이 함께 ACTIVE라 그 상태다. 지우지
    않고 `INACTIVE`로 내렸다가 정리에서 되돌린다.
    """

    scope_prefix = _ACCOUNT[5:]
    rivals = [
        line
        for line in _psql(
            "select observation_id from public.portfolio_balance_observations"
            f" where owner_user_id = '{_OWNER}' and source = 'KIS_MOCK'"
            " and context_status = 'ACTIVE'"
            f" and account_scope_hash not like '{scope_prefix}%';"
        ).splitlines()
        if line
    ]
    if rivals:
        _psql(
            "update public.portfolio_balance_observations set context_status = 'INACTIVE'"
            f" where observation_id in ({_quoted(rivals)});"
        )
    return rivals


def restore_portfolio_contexts(observation_ids: list[str]) -> None:
    if not observation_ids:
        return
    _psql(
        "update public.portfolio_balance_observations set context_status = 'ACTIVE'"
        f" where observation_id in ({_quoted(observation_ids)});"
    )


def _secret_value(file_name: str, key: str) -> str:
    """로컬 secret 파일에서 값을 읽는다. 값은 프로세스 밖으로 나가지 않는다."""

    for line in (_STATE / "secrets" / file_name).read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name == key:
            return value.strip()
    raise PipelineError(f"{key} is unavailable in {file_name}")


def seed_risk_metrics(
    *, cash_krw: int = INITIAL_CASH_KRW, positions: dict[str, int] | None = None
) -> str:
    """RiskEngine 지표를 production 오프라인 수집 writer로 적재한다.

    체결 뒤에는 다시 호출한다. 잔고 관측은 KIS를 다시 폴링해야 갱신되는 축이고, 자동운용
    런타임은 그 표를 쓰지 않는다. 갱신하지 않으면 매도 세션의 `asset_weight`가 보유수량을
    찾지 못해 `BROKERAGE_UNAVAILABLE`로 닫힌다.

    직접 INSERT 하지 않는다. `app.decision_source_cli`의 writer가 fixture를 검증하고 정해진 표에만
    append하며, 그 DSN이 쓸 수 있는 표까지 스스로 확인한다. 즉 이 단계가 태우는 것은 수집 계층의
    오프라인 경로 그대로이고, 테스트가 만드는 것은 그 입력 fixture뿐이다.

    시각을 지금으로 두는 이유는 판단이 실제 시각에 이뤄지기 때문이다. 관측이 오래되면
    `SOURCE_STALE`로 닫혀 HOLD가 된다. 값 자체는 fixture이며 실측이라고 주장하지 않는다.
    """

    now = datetime.now(UTC)
    observed = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    received = (now - timedelta(seconds=29)).isoformat().replace("+00:00", "Z")
    trading_date = (now + timedelta(hours=9)).date().isoformat()
    quote = {
        "observedAt": observed,
        "quotes": [
            {
                "askKrw": price,
                "bidKrw": price - 100,
                "completeness": "COMPLETE",
                "previousCloseKrw": price - 100,
                "priceKrw": price,
                "symbol": symbol,
            }
            for symbol, price in sorted(_fixture_prices().items())
        ],
        "receivedAt": received,
        "schemaVersion": "market-quote-observation.v1",
        "sourceVersion": "p1-e2e-pipeline-quote-fixture",
    }
    metrics = {
        "dailyOrderCount": {
            "completeness": "COMPLETE",
            # 계약이 `coveredThrough <= observedAt`을 요구한다.
            "coveredThrough": observed,
            "observedAt": observed,
            "orderCount": 0,
            "receivedAt": received,
            "tradingDate": trading_date,
        },
        "ownerScopeHash": "a" * 32 + "0" * 32,
        "ownerUserId": _OWNER,
        "portfolioSource": "KIS_MOCK",
        "risk": {
            "annualizedVolatility": "0.1800",
            "completeness": "COMPLETE",
            "dailyLossRate": "-0.0020",
            "maxDrawdown": "-0.0150",
            "observedAt": observed,
            "receivedAt": received,
        },
        "schemaVersion": "decision-deterministic-observation.v1",
        "sourceVersion": "p1-e2e-pipeline-metric-fixture",
    }

    # 잔고도 지금 시각으로 다시 관측한다. 재생본(2026-08-28)은 그대로 두지만 하루가 지나
    # `BALANCE_STALE`이라 위험 판정이 닫힌다. 같은 계좌 scope로 최신 관측을 하나 더 넣으면
    # `latest_portfolio_balance_observations`가 DISTINCT ON으로 최신만 고르므로 컨텍스트는 여전히
    # 하나다. `sourceVersion`은 arm 게이트가 요구하는 값을 그대로 쓴다.
    held = dict(sorted((positions or {}).items()))
    observed_positions: list[dict[str, Any]] = [
        {
            "isGoldEtfEtn": False,
            "marketValueKrw": quantity * _FIXTURE_PRICES[symbol],
            "quantity": quantity,
            "symbol": symbol,
        }
        for symbol, quantity in held.items()
    ]
    market_value_total = sum(
        quantity * _FIXTURE_PRICES[symbol] for symbol, quantity in held.items()
    )
    balance = {
        "cashKrw": cash_krw,
        "completeness": "COMPLETE",
        "marginRequirementKrw": 0,
        "observedAt": observed,
        "ownerScopeHash": _ACCOUNT[5:] + "0" * 32,
        "ownerUserId": _OWNER,
        "portfolioEquityKrw": cash_krw + market_value_total,
        "positions": observed_positions,
        "receivedAt": received,
        "schemaVersion": "2",
        "sourceVersion": "kis-mock-online-complete-v2",
    }

    outcomes: list[str] = []
    for entry, payload, key_file, key_name, role, dsn_name, script in (
        (
            "market_quote",
            quote,
            "postgres.env",
            "POSTGRES_MARKET_WRITER_PASSWORD",
            "decision_market_writer",
            "DECISION_MARKET_WRITER_DATABASE_DSN",
            "app.decision_source_cli:market_quote_main",
        ),
        (
            "kis_mock_portfolio",
            balance,
            "postgres.env",
            "POSTGRES_PORTFOLIO_WRITER_PASSWORD",
            "decision_portfolio_writer",
            "DECISION_PORTFOLIO_WRITER_DATABASE_DSN",
            "app.decision_source_cli:kis_mock_portfolio_main",
        ),
        (
            "deterministic_metrics",
            metrics,
            "postgres.env",
            "POSTGRES_RISK_WRITER_PASSWORD",
            "decision_risk_writer",
            "DECISION_RISK_WRITER_DATABASE_DSN",
            "app.decision_source_cli:deterministic_metrics_main",
        ),
    ):
        remote = f"/tmp/e2e-{entry}.json"
        _run(
            [_DOCKER, "exec", "-i", _PLATFORM, "sh", "-c", f"cat > {remote}"],
            stdin=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        module, _, function = script.partition(":")
        dsn = (
            f"postgresql://{role}:{_secret_value(key_file, key_name)}"
            "@postgres:5432/capstone_p1?sslmode=disable"
        )
        # DSN을 argv나 `-e`로 넘기지 않는다. 그러면 `ps`와 `docker inspect`, 그리고 실패 메시지에
        # 비밀번호가 남는다. stdin으로 한 줄만 흘려 넣고 컨테이너 안에서 환경변수로 세운다.
        outcomes.append(
            _run(
                [
                    _DOCKER,
                    "exec",
                    "-i",
                    _PLATFORM,
                    "sh",
                    "-c",
                    f'IFS= read -r dsn; export {dsn_name}="$dsn"; '
                    "export DECISION_SOURCE_WRITER_OFFLINE_TARGET=offline; "
                    f"exec python -W ignore -c \"import sys;sys.argv=['{entry}','{remote}'];"
                    f'import {module} as m;m.{function}()"',
                ],
                stdin=dsn + "\n",
            ).strip()
        )
    return " ".join(outcomes)


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
        # 실현손익은 음수일 수 있다. 빈 값의 표식을 '-'로 두면 음수 부호와 구분되지 않는다.
        " || '|pnl=' || coalesce(realized_pnl_krw::text,'none') || '|' || coalesce(exit_reason,'-')"
        " from public.automation_positions where user_id = '" + _OWNER + "'"
        " order by entry_session desc, created_at desc limit 3;"
    )
    closed = [
        line for line in positions.splitlines() if "|CLOSED|" in line and "|pnl=none|" not in line
    ]
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
    # 적재 상태는 설계상 ADMIN 전용이다(SecurityConfig와 `@PreAuthorize` 양쪽에서 고정). 소유자
    # 토큰으로 부르면 403이 정상이므로 관리자 세션을 따로 열어 확인한다.
    admin = Api(api.base_url)
    admin.login("demo-admin")
    ingest_code, ingest_body = admin.request("GET", "/api/v1/artifacts/ingest-status")
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
    # 시작 시 자동운용이 켜져 있었으면 그 복원 계획을 담는다. finally 에서 쓴다.
    rearm_plan: dict[str, Any] | None = None
    recorder.add(
        "시작 스냅샷",
        "INFO",
        " ".join(f"{table}={len(rows)}" for table, rows in sorted(before.items())),
    )

    # 번들 종목이 커밋된 exact-31 과 같은지 먼저 본다. 다르면 이후 전부가 무의미하다.
    _assert_universe_matches_catalog()

    platform_switched = False
    quiesced: list[str] = []
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

        market_seed_sha = seed_market_data(sessions)
        source_session = min(sessions) - timedelta(days=1)
        seeded_bars = seed_source_session_bars(market_seed_sha, source_session)
        recorder.add(
            "시장데이터 매니페스트 씨딩",
            "PASS",
            f"SEED 1 + DAILY {len(sessions)} (체인 가드 순서 준수)"
            f" + 소스 세션 {source_session} 바 {seeded_bars}행",
        )

        metrics = seed_risk_metrics()
        recorder.add(
            "위험 지표 수집 (오프라인 writer)",
            "PASS",
            f"{metrics} — 시세와 결정론 지표를 production writer로 적재",
        )

        quiesced = quiesce_rival_portfolio_contexts()
        recorder.add(
            "포트폴리오 컨텍스트 단일화",
            "PASS",
            f"다른 scope의 ACTIVE 관측 {len(quiesced)}건을 INACTIVE로 내림 "
            "(둘 이상이면 RiskEngine이 CONFLICT로 전 지표를 닫는다). 정리에서 되돌린다",
        )

        # 실물 seed 경로에서는 새로 import 하지 않고 이미 적재된 포인터를 쓴다. 추론 서버가
        # 요청의 bundleSha256 을 자기가 로드한 번들과 대조하므로 그때만 추론이 성립한다.
        # 컨테이너 안 드라이버는 decision_worker 로만 붙어 이 뷰를 못 읽으므로 호스트에서 읽는다.
        if _use_real_seed():
            imported = _existing_pointer()
        else:
            imported = _driver(
                "import-bundle",
                "--session",
                buy_session.isoformat(),
                "--buy",
                "005930",
                "--ordinal",
                "1",
            )
        expected_outcome = "EXISTING_POINTER" if _use_real_seed() else "IMPORTED"
        recorder.add(
            "Team B 번들 적재 (매수 세션)",
            "PASS" if imported.get("outcome") == expected_outcome else "FAIL",
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
        # 운영 중(ARMED)이면 먼저 내린다. 정책 변경이 DISARMED 를 요구하므로 그러지 않으면
        # 관통이 409 로 시작조차 못 한다. finally 에서 되돌린다.
        rearm_plan = _disarm_if_armed(api, recorder)
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
                # 유니버스 최고가가 슬롯에 들어가는 값으로 둔다. 상수로 두면 실물 seed 에서
                # 비싼 종목이 최상위 후보가 되는 날 수량이 0 이 되어 관통이 주문까지 가지
                # 못한다. 원칙 한도가 실제로 물리는지도 이 값이 충분히 커야 보인다.
                "capitalLimitKrw": _capital_limit_krw(),
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

        # 체결 뒤 잔고를 다시 관측한다. 실제 배포에서는 수집기가 KIS를 다시 폴링하는 지점이고,
        # 여기서는 같은 오프라인 writer에 체결 후 원장을 넣는다.
        refreshed = seed_risk_metrics(
            cash_krw=int(first.get("ledgerCashKrw") or INITIAL_CASH_KRW),
            positions={
                str(symbol): int(quantity)
                for symbol, quantity in (first.get("ledgerPositions") or {}).items()
            },
        )
        recorder.add(
            "체결 후 잔고 재관측",
            "PASS",
            f"{refreshed} — 매수 체결분을 반영한 관측이 없으면 매도 판단이 "
            "BROKERAGE_UNAVAILABLE로 닫힌다",
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
        restore_portfolio_contexts(quiesced)
        _stop_offline_brokerage()
        if platform_switched and not args.keep:
            # 테스트 시작 때 관측한 brokerage/Strong LLM/RAG 구성으로 정확히 되돌린다.
            try:
                _compose("up", "-d", "--no-deps", "decision-platform")
                _wait_healthy()
                recorder.add("스택 구성 복원", "PASS", "테스트 시작 시 런타임 플래그로 되돌림")
            except PipelineError as error:
                recorder.add("스택 구성 복원", "FAIL", str(error))
        if args.keep:
            recorder.add("정리", "INFO", "--keep 이므로 건너뛴다. 배포 증거로 쓰지 않는다")
        else:
            cleanup(before, recorder)
        # 정리가 control 을 DISARMED 로 되돌리므로, 원래 켜져 있었다면 반드시 다시 켠다.
        # 이 단계가 없으면 테스트 한 번이 자동운용을 조용히 꺼 버린다.
        if rearm_plan is not None:
            _rearm(api, recorder, rearm_plan)

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
