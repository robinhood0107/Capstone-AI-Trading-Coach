# P1 Automation closed-loop fixture 운영 가이드

## 현재 판정

```text
OWNER_AUTOMATION_CLOSED_LOOP_FIXTURE=IMPLEMENTED_MERGE_CANDIDATE
FIXTURE_PHYSICAL_CALLS=0
SESSION_LOGICAL_SUBMIT_MAX=1
QUANTITY=1
VERTEX_SECOND_CANDIDATE=0
SELL_VERTEX_CALLS=0
LIVE_AUTOMATION_ADAPTER=NOT_IMPLEMENTED
```

`app.p1_owner.automation`은 provider-free acceptance runtime이다. shared fixture store를 유지한 채 매
tick마다 engine instance를 새로 만들어 restart boundary를 검증한다. run snapshot과 bot position은
bounded projection으로 유지하고 모든 state change는 sanitized append-only `automation-event.v1`으로
기록한다.

## 상태 진행

```text
SCHEDULED -> PRECHECK
PRECHECK -> RECONCILING_PREVIOUS | EXIT_SELECTED | BUY_CANDIDATE_SELECTED | SKIPPED_*
EXIT_SELECTED -> RISK_CHECKING
BUY_CANDIDATE_SELECTED -> NEWS_CHECKING
NEWS_CHECKING -> NEWS_VETOED | RISK_CHECKING
RISK_CHECKING -> ORDER_SUBMITTING | SKIPPED_NO_ACTION | HALTED
ORDER_SUBMITTING -> ORDER_SUBMITTED | PENDING_RECONCILIATION | SKIPPED_* | HALTED
ORDER_SUBMITTED/PENDING_RECONCILIATION -> COMPLETED | CANCELLED_UNFILLED | HALTED
```

`NEWS_VETOED`, `CANCELLED_UNFILLED`, `COMPLETED`, `SKIPPED_*`, `HALTED`는 fixture terminal이다.
`DISARMED`여도 이미 outstanding인 order reconciliation은 계속하지만 신규 run은 주문을 만들지 않는다.

## 선택과 주문 불변식

- unfinished reconciliation이 exit와 BUY보다 먼저다.
- expiry/model SELL은 bot-owned quantity 1 lot만 대상으로 하며 Vertex를 호출하지 않는다.
- BUY candidate는 deterministic ordering으로 하나만 선택한다.
- `VETO_BUY | ABSTAIN`은 order 0, second candidate 0이다.
- reservation은 `LIMIT`, quantity 1, long-only이고 quote read와 logical submit은 각각 최대 1이다.
- ambiguous submit은 재주문하지 않는다.
- 09:20 이후 새 주문은 `SKIPPED_LATE_START`, 15:20 미체결은 cancel/reconcile한다.
- 휴장일, stale shard/quote, incomplete balance, manual drift와 Kill Switch는 provider transport 전에 또는
  fail-closed state에서 종료한다.

## Acceptance scenarios

- BUY 1주 fill 및 account reconciliation
- BUY 뒤 다섯 번째 XKRX session SELL로 같은 lot close
- model SELL 조기 exit와 multiple-exit deterministic selection
- Vertex VETO/ABSTAIN과 second candidate 0
- clean unfilled cancel, cancel failure, ambiguous submit recovery
- engine restart after every state와 duplicate tick strict no-op
- holiday, late start, stale shard/quote, incomplete balance, manual drift, Kill Switch
- baseline manual position 제외, pending reconciliation 우선, disarm 뒤 outstanding reconciliation 유지
- automation control/run/position/event v1 schema validation

검증 명령:

```bash
cd workspaces/decision-platform/python-services
.venv/bin/ruff check app/p1_owner/automation.py tests/p1_owner/test_automation.py
.venv/bin/mypy app/p1_owner/automation.py tests/p1_owner/test_automation.py
.venv/bin/pytest -q tests/p1_owner/test_automation.py tests/p1_owner
```

이 fixture PASS는 KIS Mock certification, credential, live provider·계좌·주문 실행 또는 Release readiness가
아니다. 실제 activation은 Team 결과, exact-56 API, full Compose와 별도 exact approval 뒤에만 가능하다.
