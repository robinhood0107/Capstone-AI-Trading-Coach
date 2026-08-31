"""자동운용 폐루프를 한 세션 안에서 연속 구동한 기록을 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

**이것은 릴리스 계약이 요구하는 3 XKRX session soak이 아니다.** `THREE_XKRX_SESSION_SOAK`은
연속된 세 거래일이 **실제로 지나는 동안** 자동운용이 arm된 채 도는 것을 요구하고, 그 증거는
사흘이 지나야만 만들어진다.

여기서 보는 것은 그것을 하루 안에서 흉내 낸 것이다. `THREE_SESSION_SOAK` 시나리오가 엔진에
연속 세 세션 날짜를 주어 tick을 돌린다. 세션 날짜는 실제 XKRX 달력에서 뽑고 주문도 실제
KIS 모의 원장에 나가지만, 사흘이 지난 것은 아니다. 판정표가 그 사실을 첫 줄에 적는다.

무엇을 확인하나. `tests/rehearsal/kis_mock_closed_loop_rehearsal.py`가 남긴 폐루프 기록을 본다.

  1. 연속 세 세션에 걸친 구동 기록이 있고, 가운데 세션들에서 포지션이 살아 있었다
  2. 같은 세션 날짜의 폐루프 기록이 세 개 이상이다
  3. 전부 SUCCESS로 끝났다
  4. 매번 계좌가 원복됐다 - 예수금과 보유수량이 시작과 같다
  5. 매수와 매도가 실제 주문번호를 남겼다
  6. 네 가지 청산 사유가 모두 관측됐다

실행:
  P1_XKRX_SESSION_SOAK_E2E=1 python -m tests.e2e.xkrx_session_soak_e2e \\
    --out artifacts/decision-platform/e2e/xkrx-session-soak.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Final, Sequence

from .harness import (
    REPOSITORY,
    HarnessError,
    Recorder,
    require_opt_in,
    write_report,
)

_OPT_IN: Final = "P1_XKRX_SESSION_SOAK_E2E"
_REHEARSAL_DIR: Final = REPOSITORY / "artifacts/decision-platform/live-rehearsal"
_REQUIRED_RUNS: Final = 3


def _session_date(record: dict[str, Any]) -> str:
    return str(record.get("sessionKst", ""))[:10]


def load_records() -> list[dict[str, Any]]:
    """폐루프 기록을 전부 읽어 세션 날짜가 가장 최근인 것만 남긴다."""

    records: list[dict[str, Any]] = []
    for path in sorted(_REHEARSAL_DIR.glob("closed-loop-*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or not value.get("sessionKst"):
            continue
        value["__path__"] = path.name
        records.append(value)
    if not records:
        raise HarnessError(f"{_REHEARSAL_DIR.name}에 closed-loop 기록이 없다")
    latest = max(_session_date(item) for item in records)
    return [item for item in records if _session_date(item) == latest]


def _step(record: dict[str, Any], name: str) -> dict[str, Any]:
    for item in record.get("engineSteps") or []:
        if isinstance(item, dict) and item.get("step") == name:
            return item
    return {}


def check_count(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    session = _session_date(records[0]) if records else "?"
    recorder.add(
        "같은 세션에서 폐루프를 세 번 이상 돌렸다",
        "PASS" if len(records) >= _REQUIRED_RUNS else "FAIL",
        f"세션={session} 기록={len(records)}개 요구={_REQUIRED_RUNS}개 "
        + ", ".join(str(item.get("__path__")) for item in records),
    )


def check_outcomes(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    failed = [str(item.get("__path__")) for item in records if item.get("status") != "SUCCESS"]
    recorder.add(
        "전부 SUCCESS로 끝났다",
        "PASS" if not failed else "FAIL",
        f"실패={failed or '없음'} (하나라도 중단되면 연속 구동이 아니다)",
    )


def check_balance_restored(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    drifted = []
    for item in records:
        pre, post = _step(item, "preBalance"), _step(item, "postBalance")
        if pre.get("cashKrw") != post.get("cashKrw") or pre.get("symbolQuantity") != post.get(
            "symbolQuantity"
        ):
            drifted.append(str(item.get("__path__")))
    recorder.add(
        "매번 계좌가 원복됐다",
        "PASS" if not drifted else "FAIL",
        f"원복되지 않은 기록={drifted or '없음'} "
        "(잔여 포지션이 남으면 다음 관측이 그것을 물려받는다)",
    )


def check_real_orders(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    missing = []
    for item in records:
        numbers = [
            str(step.get("orderNo", ""))
            for step in item.get("transportSteps") or []
            if isinstance(step, dict) and step.get("step") in {"submitBuy", "submitSell"}
        ]
        if len(numbers) < 2 or not all(value.isdigit() for value in numbers):
            missing.append(str(item.get("__path__")))
    recorder.add(
        "매수와 매도가 실제 주문번호를 남겼다",
        "PASS" if not missing else "FAIL",
        f"주문번호가 온전하지 않은 기록={missing or '없음'}",
    )


_EXIT_REASONS: Final = ("MAX_HOLDING_SESSIONS", "MODEL_SELL", "STOP_LOSS", "TAKE_PROFIT")


def check_exit_variety(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    reasons = Counter(
        str(_step(item, "positionClosed").get("exitReason", "없음")) for item in records
    )
    missing = [reason for reason in _EXIT_REASONS if reason not in reasons]
    recorder.add(
        "네 가지 청산 사유가 모두 관측됐다",
        "PASS" if not missing else "FAIL",
        f"사유 분포={dict(reasons)} 빠진 사유={missing or '없음'}",
    )


def check_multi_session(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    """연속 세 세션에 걸친 구동 기록이 있어야 한다."""

    soaked = [
        item
        for item in records
        if any(
            isinstance(step, dict) and step.get("step") == "soakSession"
            for step in item.get("engineSteps") or []
        )
    ]
    if not soaked:
        recorder.add(
            "연속 세 세션에 걸쳐 돌았다",
            "FAIL",
            "soakSession 단계를 담은 기록이 없다 "
            "(P1_KIS_MOCK_REHEARSAL_SCENARIO=THREE_SESSION_SOAK 을 돌려야 한다)",
        )
        return
    record = soaked[-1]
    sessions = [
        step
        for step in record.get("engineSteps") or []
        if isinstance(step, dict) and step.get("step") == "soakSession"
    ]
    entry = _step(record, "positionOpened").get("entrySession")
    held = all(step.get("openPositions") == 1 for step in sessions)
    recorder.add(
        "연속 세 세션에 걸쳐 돌았다",
        "PASS" if len(sessions) >= 2 and held else "FAIL",
        f"매수 세션={entry} 이후 세션="
        + ", ".join(f"{step.get('sessionDate')}({step.get('state')})" for step in sessions)
        + f" 보유 유지={held} "
        "(가운데 세션에서 포지션이 사라지면 만기 청산 경로를 못 본 것이다)",
    )


def main(argv: Sequence[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        records = load_records()
        recorder.add(
            "이것은 3거래일 soak이 아니다",
            "INFO",
            f"관측 폐루프 수={len(records)} "
            "릴리스 계약의 THREE_XKRX_SESSION_SOAK은 연속 세 거래일이 실제로 지나는 동안 "
            "자동운용이 도는 것을 요구한다. 여기서는 엔진에 연속 세 세션 날짜를 주어 "
            "하루 안에서 그것을 흉내 냈다. 주문은 실제이고 사흘은 지나지 않았다",
        )
        check_multi_session(recorder, records)
        check_count(recorder, records)
        check_outcomes(recorder, records)
        check_balance_restored(recorder, records)
        check_real_orders(recorder, records)
        check_exit_variety(recorder, records)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")

    # 기록을 읽기만 한다. 만든 것이 없으므로 되돌릴 것도 없다.
    report = write_report(
        contract_id="p1-xkrx-session-soak-e2e.v1",
        marker="P1_XKRX_SESSION_SOAK_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
