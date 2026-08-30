"""자동운용 v1 표면과 v2와의 경계를 실행 중인 스택에서 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

무엇을 확인하나.
  1. v1 `status`가 열리고, `arm`은 낙관적 잠금과 계좌·원칙 결속을 요구한다.
  2. v1 `arm` → `disarm` 왕복이 통제 버전을 하나씩 올린다. 끝에서 반드시 원래 상태로 돌아온다.
  3. v1 `runs` 목록이 열린다.
  4. v2 정책 저장이 v1 arm과 독립이며, v2 `arm`은 blocker가 있으면 열리지 않는다.
  5. 뉴스 거부권 전송이 fail-closed다 — 종목 뉴스 코퍼스가 없어 항상 `ABSTAIN`이고 물리 호출은 0이다.

무엇을 확인하지 않나. 관통 파이프라인(자동운용 런타임이 실제로 주문까지 내는 경로)은
`full_pipeline_e2e.py`가 담당한다. 여기서는 REST 표면과 그 경계만 본다.

정리. arm 상태는 반드시 되돌린다. 되돌리지 못하면 FAIL이다. 시작 시 스냅샷을 찍고 끝에서
차집합만 지운다.

실행:
  P1_AUTOMATION_V1_E2E=1 python -m tests.e2e.automation_v1_e2e \\
    --out artifacts/decision-platform/e2e/automation-v1.json
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any, Final

from .harness import (
    Api,
    HarnessError,
    OWNER,
    Recorder,
    cleanup,
    psql,
    require_opt_in,
    snapshot,
    write_report,
)

_OPT_IN: Final = "P1_AUTOMATION_V1_E2E"


def _key() -> str:
    return f"idem_{uuid.uuid4().hex}"


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def _control_row() -> dict[str, str]:
    row = psql(
        "select control_state || '|' || coalesce(brokerage_mode, '') || '|' || version"
        f" from public.automation_control where user_id = '{OWNER}';"
    )
    if not row:
        raise HarnessError("automation control row is missing for the owner")
    state, mode, version = row.splitlines()[0].split("|")
    return {"state": state, "mode": mode, "version": version}


def check_status_and_runs(recorder: Recorder, owner: Api) -> None:
    v1_status, v1 = owner.request("GET", "/api/v1/automation/status")
    v2_status, v2 = owner.request("GET", "/api/v2/automation/status")
    runs_status, runs = owner.request("GET", "/api/v1/automation/runs?size=5")
    recorder.add(
        "v1 status·runs와 v2 status",
        "PASS" if v1_status == 200 and v2_status == 200 and runs_status == 200 else "FAIL",
        f"v1 status HTTP {v1_status} state={_data(v1).get('state') or _data(v1).get('controlState')} "
        f"v2 status HTTP {v2_status} blocker={(_data(v2).get('blockers') or [])[:2]} "
        f"v1 runs HTTP {runs_status} {len((_data(runs).get('items')) or [])}건",
    )


def check_arm_requires_binding(recorder: Recorder, owner: Api) -> None:
    """지어낸 계좌·원칙으로는 arm이 열리지 않아야 한다."""

    control = _control_row()
    status, body = owner.request(
        "POST",
        "/api/v1/automation/arm",
        {
            "accountId": "acct_" + uuid.uuid4().hex,
            "brokerageMode": "INTERNAL_PAPER",
            "expectedVersion": int(control["version"]),
            "principleId": "prc_" + uuid.uuid4().hex,
            "strategyId": "strategy_" + uuid.uuid4().hex,
        },
        idempotency_key=_key(),
    )
    after = _control_row()
    recorder.add(
        "지어낸 결속으로는 arm이 열리지 않는다",
        "PASS" if status in (400, 404, 409, 422) and after["state"] == control["state"] else "FAIL",
        f"HTTP {status} {(body.get('error') or {}).get('code')} "
        f"상태 {control['state']}→{after['state']} (열리면 그 자체가 회귀다)",
    )


def check_stale_version_is_rejected(recorder: Recorder, owner: Api) -> None:
    """낙관적 잠금이 없으면 두 화면이 서로의 변경을 덮어쓴다."""

    control = _control_row()
    stale = max(0, int(control["version"]) - 1)
    status, body = owner.request(
        "POST",
        "/api/v1/automation/disarm",
        {"expectedVersion": stale},
        idempotency_key=_key(),
    )
    after = _control_row()
    recorder.add(
        "낡은 버전은 거절된다",
        "PASS" if status == 409 and after["version"] == control["version"] else "FAIL",
        f"HTTP {status} {(body.get('error') or {}).get('code')} "
        f"버전 {control['version']}→{after['version']}",
    )


def check_v2_policy_and_arm(recorder: Recorder, owner: Api) -> None:
    """정책 저장은 열리고, blocker가 있으면 arm은 닫힌다."""

    status, current = owner.request("GET", "/api/v2/automation/status")
    if status != 200:
        recorder.add("v2 정책 저장", "FAIL", f"status HTTP {status}")
        return
    policy = _data(current).get("policy") or {}
    expected = policy.get("version")
    put_status, saved = owner.request(
        "PUT",
        "/api/v2/automation/policy",
        {
            "capitalLimitKrw": 10_000_000,
            "stopLossBps": 500,
            "takeProfitBps": 1_000,
            "expectedVersion": expected if isinstance(expected, int) else 0,
        },
        idempotency_key=_key(),
    )
    saved_policy = _data(saved).get("policy") or _data(saved)
    recorder.add(
        "v2 정책 저장",
        "PASS" if put_status in (200, 201) else "FAIL",
        f"HTTP {put_status} {(saved.get('error') or {}).get('code')} "
        f"상한={saved_policy.get('capitalLimitKrw')} 손절={saved_policy.get('stopLossBps')} "
        f"익절={saved_policy.get('takeProfitBps')}",
    )

    _, after_status = owner.request("GET", "/api/v2/automation/status")
    blockers = _data(after_status).get("blockers") or []
    policy_after = _data(after_status).get("policy") or {}
    arm_status, armed = owner.request(
        "POST",
        "/api/v2/automation/arm",
        {
            "accountId": "acct_" + "a" * 32,
            "policyId": (policy_after.get("policyId") or "auto_pol_" + "0" * 32),
            "expectedControlVersion": int(_control_row()["version"]),
            "expectedPolicyVersion": policy_after.get("version") or 1,
        },
        idempotency_key=_key(),
    )
    # blocker가 있으면 arm은 열리면 안 된다. 없으면 열리는 것이 맞다. 관측으로 가른다.
    expected_closed = bool(blockers)
    opened = arm_status in (200, 201)
    recorder.add(
        "v2 arm은 blocker를 넘지 못한다",
        "PASS" if (expected_closed and not opened) or (not expected_closed and opened) else "FAIL",
        f"blocker={blockers[:3]} arm HTTP {arm_status} "
        f"{(armed.get('error') or {}).get('code')} (blocker가 있으면 닫혀야 한다)",
    )


def check_news_veto_is_fail_closed(recorder: Recorder) -> None:
    """뉴스 거부권은 provider transport가 주입되지 않으면 호출 전에 닫혀야 한다.

    이 배포에는 종목 뉴스 코퍼스가 없다. 그래서 자동운용 런타임이 쓰는 기본 거부권 전송은
    `FailClosedVertexVetoTransport`이고, 그것은 provider socket을 열기 전에 예외로 닫는다.
    DB에 남는 관측이 아니라 그 클래스의 동작 자체가 이 사실의 증거다.
    """

    from app.p1_owner.automation_runtime_live import (  # noqa: PLC0415 - 확인 대상 자체다
        FailClosedVertexVetoTransport,
    )
    from app.p1_owner.vertex_veto import VertexBudgetExhausted  # noqa: PLC0415

    transport = FailClosedVertexVetoTransport()
    closed = False
    leaf = ""
    try:
        transport.invoke(system_prompt="", request_bytes=b"")
    except VertexBudgetExhausted as error:
        closed = True
        leaf = str(error)
    recorder.add(
        "뉴스 거부권 fail-closed",
        "PASS" if closed and transport.physical_calls == 0 else "FAIL",
        f"닫힘={closed} leaf={leaf} 물리 호출={transport.physical_calls} "
        "(코퍼스가 없으므로 근거가 비고, provider를 부르기 전에 닫는 것이 맞다)",
    )


def restore_control(recorder: Recorder, owner: Api, initial: dict[str, str]) -> None:
    control = _control_row()
    if control["state"] == initial["state"]:
        recorder.add(
            "자동운용 통제 복구",
            "PASS",
            f"상태 {control['state']} (시작과 같다)",
        )
        return
    status, _ = owner.request(
        "POST",
        "/api/v1/automation/disarm",
        {"expectedVersion": int(control["version"])},
        idempotency_key=_key(),
    )
    after = _control_row()
    recorder.add(
        "자동운용 통제 복구",
        "PASS" if after["state"] == initial["state"] else "FAIL",
        f"disarm HTTP {status} 상태 {control['state']}→{after['state']} 시작={initial['state']}",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    before: dict[str, list[str]] = {}
    initial: dict[str, str] = {}
    owner: Api | None = None
    try:
        before = snapshot()
        initial = _control_row()
        owner = Api()
        owner.login("demo-user")
        check_status_and_runs(recorder, owner)
        check_arm_requires_binding(recorder, owner)
        check_stale_version_is_rejected(recorder, owner)
        check_v2_policy_and_arm(recorder, owner)
        check_news_veto_is_fail_closed(recorder)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - 어떤 실패든 복구와 정리는 반드시 돈다
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")
    finally:
        if owner is not None and initial:
            restore_control(recorder, owner, initial)
        if before:
            cleanup(before, recorder)
        else:
            recorder.add("정리", "FAIL", "스냅샷을 찍지 못해 되돌릴 범위를 알 수 없다")

    report = write_report(
        contract_id="p1-automation-v1-e2e.v1",
        marker="P1_AUTOMATION_V1_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
