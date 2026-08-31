"""실제 provider 판단이 자동매매의 1등을 바꾼 run을 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

`ai_judgement_e2e.py`와 무엇이 다른가. 그쪽은 AI 판단 경로가 **배포에 실재하는지**를 본다.
상태와 제약과 권한이 DB에 있는지까지다. 그 파일이 스스로 적어 둔 미커버 두 가지가 여기 대상이다.

  1. 실제 provider를 불러 판단을 받아 온다. 외부 호출이고 비용이다.
  2. AI가 1등을 실제로 바꾼 run이다. 규칙이 고른 1등과 최종 선택이 다른 run을 말한다.

무엇을 확인하나. `tests/rehearsal/kis_mock_closed_loop_rehearsal.py`의 `AI_RERANK` 시나리오가
남긴 폐루프 기록을 읽는다. 그 리허설은 자동운용 엔진을 그대로 구동하되 transport만 실제 KIS
모의 brokerage와 실제 Strong LLM으로 바꾼 것이다.

  1. AI가 1등을 바꾼 run이 기록에 있다
  2. 판단이 실제 provider에서 왔다 - judge 호출이 한 번 이상 있었고 후보별 점수가 돌아왔다
  3. 선택된 종목이 후보 집합 안에 있다
  4. 실제 주문번호가 매수와 매도 양쪽에 있다
  5. 폐루프가 끝났고 계좌가 원복됐다

1번을 "가장 최근 run이 1등을 바꿨다"로 두면 안 된다. 모델은 같은 후보에 매번 같은 점수를
주지 않는다. 두 후보가 비슷하면 동점을 주고, 그러면 규칙 순서가 그대로 남는다. **그것도
정상 동작이다** - AI는 순위를 바꿀 수 있을 뿐 반드시 바꿔야 하는 것이 아니다. 그래서 1번은
기록 전체에서 하나라도 바뀐 run이 있는지를 보고, 나머지는 가장 최근 run을 본다.

무엇을 확인하지 않나. DB `automation_ai_judgements` 표에 남는 판단 기록은 자동운용 런타임이
arm된 뒤에만 쓰인다. 이 리허설은 인메모리 store로 엔진을 구동하므로 그 표에는 남지 않는다.
표의 모양과 권한은 `ai_judgement_e2e.py`가 따로 확인한다.

실행:
  P1_AI_JUDGEMENT_LIVE_E2E=1 python -m tests.e2e.ai_judgement_live_e2e \\
    --out artifacts/decision-platform/e2e/ai-judgement-live.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Final, Sequence

from .harness import (
    REPOSITORY,
    HarnessError,
    Recorder,
    require_opt_in,
    write_report,
)

_OPT_IN: Final = "P1_AI_JUDGEMENT_LIVE_E2E"
_REHEARSAL_DIR: Final = REPOSITORY / "artifacts/decision-platform/live-rehearsal"
_PREFIX: Final = "closed-loop-ai-rerank-"


def all_records() -> list[dict[str, Any]]:
    """AI_RERANK 리허설 기록을 전부 읽는다. 없으면 지어내지 않고 멈춘다."""

    records: list[dict[str, Any]] = []
    for path in sorted(_REHEARSAL_DIR.glob(f"{_PREFIX}*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        value["__path__"] = path.name
        records.append(value)
    if not records:
        raise HarnessError(
            f"{_REHEARSAL_DIR.name}에 {_PREFIX}*.json 기록이 없다. "
            "P1_KIS_MOCK_REHEARSAL_SCENARIO=AI_RERANK 리허설을 먼저 돌려야 한다"
        )
    records.sort(key=lambda item: str(item.get("sessionKst", "")))
    return records


def _step(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in record.get("engineSteps") or []:
        if isinstance(item, dict) and item.get("step") == name:
            return item
    return None


def _transport_steps(record: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [
        item
        for item in record.get("transportSteps") or []
        if isinstance(item, dict) and item.get("step") == name
    ]


def check_rerank(recorder: Recorder, records: list[dict[str, Any]]) -> None:
    """기록 전체에서 AI가 1등을 바꾼 run이 하나라도 있어야 한다.

    매 run 마다 바뀌기를 요구하지 않는다. 모델이 두 후보에 동점을 주면 규칙 순서가 그대로
    남고, 그것도 정상이다. 여기서 증명하려는 것은 "AI가 순위를 바꿀 수 있다"이지
    "AI가 늘 순위를 바꾼다"가 아니다.
    """

    changed = [
        (item, step)
        for item in records
        if (step := _step(item, "aiJudgement")) is not None and step.get("aiChangedTop")
    ]
    summary = ", ".join(
        f"{item.get('__path__')}[{step.get('ruleTopSymbol')}→{step.get('selectedSymbol')}]"
        for item, step in ((item, _step(item, "aiJudgement") or {}) for item in records)
    )
    recorder.add(
        "AI가 1등을 바꾼 run이 있다",
        "PASS" if changed else "FAIL",
        f"기록 {len(records)}개 중 순위를 바꾼 run {len(changed)}개 :: {summary}",
    )


def check_provider(recorder: Recorder, record: dict[str, Any]) -> None:
    """판단이 실제 provider에서 왔는지 본다. 호출 0회면 규칙만으로 돈 run이다."""

    judgement = _step(record, "aiJudgement") or {}
    calls = judgement.get("judgeCalls")
    payload = judgement.get("judgement")
    verdicts = payload.get("verdicts") if isinstance(payload, dict) else None
    scored = [
        item
        for item in verdicts or []
        if isinstance(item, dict) and isinstance(item.get("score"), (int, float))
    ]
    recorder.add(
        "실제 provider가 판단을 돌려줬다",
        "PASS" if isinstance(calls, int) and calls >= 1 and len(scored) >= 2 else "FAIL",
        f"judge 호출={calls} 점수를 받은 후보={len(scored)} "
        f"확신도={payload.get('confidence') if isinstance(payload, dict) else None} "
        "(호출이 0이면 provider가 붙지 않아 AI_NOT_PARTICIPATED로 돈 것이다)",
    )


def check_selection_bounded(recorder: Recorder, record: dict[str, Any]) -> None:
    """AI는 후보 집합 밖 종목을 선택하게 만들 수 없다."""

    judgement = _step(record, "aiJudgement") or {}
    payload = judgement.get("judgement")
    verdicts = payload.get("verdicts") if isinstance(payload, dict) else []
    symbols = {
        str(item.get("symbol"))
        for item in verdicts or []
        if isinstance(item, dict) and item.get("symbol")
    }
    selected = str(judgement.get("selectedSymbol", ""))
    opened = _step(record, "positionOpened") or {}
    recorder.add(
        "선택은 후보 집합 안에서만 일어났다",
        "PASS" if selected and selected in symbols and opened.get("symbol") == selected else "FAIL",
        f"후보={sorted(symbols)} 선택={selected} 실제 매수={opened.get('symbol')} "
        "(후보 밖 종목이 매수되면 AI가 집합을 넓힌 것이다)",
    )


def check_real_orders(recorder: Recorder, record: dict[str, Any]) -> None:
    """실제 KIS 모의 원장에 매수와 매도가 각각 남아야 한다."""

    buys = _transport_steps(record, "submitBuy")
    sells = _transport_steps(record, "submitSell")
    buy_no = str(buys[0].get("orderNo", "")) if buys else ""
    sell_no = str(sells[0].get("orderNo", "")) if sells else ""
    recorder.add(
        "실제 주문번호가 양쪽에 있다",
        "PASS" if buy_no.isdigit() and sell_no.isdigit() else "FAIL",
        f"매수 주문번호={buy_no or '없음'} 매도 주문번호={sell_no or '없음'} "
        "(번호가 없으면 주문이 원장에 닿지 않은 것이다)",
    )


def check_closed(recorder: Recorder, record: dict[str, Any]) -> None:
    """폐루프가 끝나고 계좌가 원복돼야 다음 관측이 오염되지 않는다."""

    closed = _step(record, "positionClosed") or {}
    post = _step(record, "postBalance") or {}
    pre = _step(record, "preBalance") or {}
    same_cash = pre.get("cashKrw") == post.get("cashKrw")
    same_qty = pre.get("symbolQuantity") == post.get("symbolQuantity")
    recorder.add(
        "폐루프가 닫히고 계좌가 원복됐다",
        "PASS" if record.get("status") == "SUCCESS" and same_cash and same_qty else "FAIL",
        f"status={record.get('status')} 청산사유={closed.get('exitReason')} "
        f"실현손익={closed.get('realizedPnlKrw')} "
        f"예수금 {pre.get('cashKrw')}→{post.get('cashKrw')} "
        f"수량 {pre.get('symbolQuantity')}→{post.get('symbolQuantity')}",
    )


def main(argv: Sequence[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        records = all_records()
        record = records[-1]
        recorder.add(
            "관측 대상 기록",
            "INFO",
            f"기록 {len(records)}개, 최근={record['__path__']} "
            f"session={record.get('sessionKst')} 물리 호출={record.get('physicalCalls')}",
        )
        check_rerank(recorder, records)
        check_provider(recorder, record)
        check_selection_bounded(recorder, record)
        check_real_orders(recorder, record)
        check_closed(recorder, record)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")

    # 이 러너는 기록을 읽기만 한다. 만든 것이 없으므로 되돌릴 것도 없다.
    report = write_report(
        contract_id="p1-ai-judgement-live-e2e.v1",
        marker="P1_AI_JUDGEMENT_LIVE_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
