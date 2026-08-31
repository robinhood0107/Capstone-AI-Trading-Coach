"""자동운용 폐루프를 한 세션 안에서 연속 구동한 기록을 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

**이것은 3 XKRX session soak이 아니다.** 릴리스 계약의 `THREE_XKRX_SESSION_SOAK`은 연속된 세
거래일에 걸쳐 자동운용이 arm된 채로 도는 것을 요구한다. 그것은 사흘이 지나야만 만들어진다.
여기서 보는 것은 **같은 하루 안에서 폐루프를 세 번 이상 무사고로 돌렸다**는 사실뿐이고,
판정표에도 그렇게 적는다. 관측하지 않은 것을 PASS로 적지 않는다는 이 폴더의 규약을 따른다.

무엇을 확인하나. `tests/rehearsal/kis_mock_closed_loop_rehearsal.py`가 같은 세션 날짜로 남긴
폐루프 기록을 모아서 본다.

  1. 같은 세션 날짜의 폐루프 기록이 세 개 이상이다
  2. 전부 SUCCESS로 끝났다
  3. 매번 계좌가 원복됐다 - 예수금과 보유수량이 시작과 같다
  4. 매수와 매도가 실제 주문번호를 남겼다
  5. 청산 사유가 한 종류에 몰려 있지 않다

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


def check_exit_variety(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    reasons = Counter(
        str(_step(item, "positionClosed").get("exitReason", "없음")) for item in records
    )
    recorder.add(
        "청산 사유가 한 종류에 몰려 있지 않다",
        "PASS" if len(reasons) >= 2 else "INFO",
        f"사유 분포={dict(reasons)} "
        "(한 종류뿐이면 다른 청산 경로는 이 세션에서 관측되지 않은 것이다)",
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
            f"관측 세션 수=1 관측 폐루프 수={len(records)} "
            "릴리스 계약의 THREE_XKRX_SESSION_SOAK은 연속 세 거래일을 요구하며 "
            "그 증거는 사흘이 지나야 만들어진다. 여기서는 하루 안의 연속 구동만 본다",
        )
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
