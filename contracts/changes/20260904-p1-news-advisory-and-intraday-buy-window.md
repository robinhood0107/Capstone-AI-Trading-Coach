# P1 자동운용 — 뉴스 거부권을 자문으로, 신규 BUY 창을 장중 전체로

- 날짜: 2026-09-04
- 범위: `app/p1_owner/automation.py` 의 `NEWS_CHECKING` 판정과 `_KST_CLOSE_ORDER_TIME`
- 성격: **위험 성질이 바뀌는 변경.** 소유자가 명시적으로 결정했다.

## 왜 바꾸는가 — 실측

2026-09-04 09:30 KST 세션은 이렇게 끝났다.

    BASELINE_CAPTURED -> PRECHECK -> AI_JUDGING -> BUY_SELECTED
      -> NEWS_CHECKING -> NEWS_VETOED
    selected_symbol = 006400,  vertex_call_count = 1,  physical_submit_count = 0

같은 요청을 재현해 판정을 확인했다.

    transport            VertexAiVetoTransport   (실 Vertex. fail-closed 아님)
    publicEvidence       0 건
    status               ABSTAIN
    reason               BUDGET_EXHAUSTED  <- 실제 사유는 VERTEX_NO_REGISTERED_EVIDENCE

즉 provider 가 실패한 것이 아니다. 런타임이 `EmptyCorpusDocumentSource` 를 쓰고 공시 표
(`disclosure_event_observation_projection` 등)가 전부 0 행이라 **등록된 근거가 하나도 없어서
Vertex 를 부르기도 전에** `vertex_transport.py` 가 닫았다.

    if not grounding_sources:
        raise VertexBudgetExhausted("VERTEX_NO_REGISTERED_EVIDENCE")

그런데 `NEWS_CHECKING` 은 "판단자가 붙어 있는데 ABSTAIN" 을 차단으로 해석한다. 그래서
**근거가 없는 한 어떤 세션도 매수에 도달할 수 없었다.** "판단할 재료가 없었다" 와 "판단자가
실패했다" 를 같은 차단으로 묶은 것이 이 상태의 원인이다.

## 무엇을 바꾸는가

### 1. 뉴스 판정은 자문이다

`transport.vertex(...)` 는 그대로 호출하고 `vertex_call_count` 도 그대로 센다. 다만 그 결과가
run 을 `NEWS_VETOED` 로 닫지 않고 항상 `ORDER_SIZING` 으로 진행한다.

**이 변경으로 위험이 늘어난다.** 이전에는 모델이 `VETO_BUY` 를 내면 매수가 막혔지만 이제는
진행된다. 손실 통제는 다음이 맡는다.

- 원칙 가드와 `RISK_CHECKING` (`risk_allow`, 킬스위치)
- 손절 `stop_loss_bps` · 익절 `take_profit_bps` · ATR 트레일링 · 최대보유기간
- 세션당 신규 주문 1건, 동시보유 5개 상한

`AI_JUDGING` 의 veto 는 **이번에 건드리지 않는다.** AI 가 모든 후보를 차단하면 여전히
`SKIPPED_NO_ACTION` 이다.

### 2. 신규 BUY 컷오프 09:40 -> 15:20

    _KST_OPEN_TIME        = 09:30   (그대로)
    _KST_CLOSE_ORDER_TIME = 09:40 -> 15:20
    _CANCEL_TIME          = 15:20   (그대로)

이전 계약(`20260827-p1-1-1-budget-variable-automation-v91.md:17`,
`20260903-p1-daily-operation-verification.md:118`)은 09:40 신규 BUY 컷오프를 고정했다. 하루
10 분만 진입할 수 있었고 그 10 분이 막히면 세션이 끝났다. 15:20 은 이미 미체결 정리 시각이라
새 상수를 들이지 않고 "장중에는 진입, 15:20 에 정리" 로 하루가 닫힌다.

## 되돌리는 법

두 변경은 서로 독립이다. `_KST_CLOSE_ORDER_TIME` 을 `time(9, 40)` 으로 되돌리면 창만 좁아지고,
`NEWS_CHECKING` 블록의 판정 분기를 되살리면 거부권만 돌아온다.

## 다음 단계 (이 문서의 범위 밖)

두 값을 상수로 두는 것은 중간 상태다. `automation_policy_versions` 는 이미
`capital_limit_krw` · `stop_loss_bps` · `take_profit_bps` 를 대시보드 폼
(`AutomationView` -> `PUT /api/v2/automation/policy` -> `p1_put_automation_policy_v2`)에서
받고 있다. 같은 슬라이스에 **주문 창**과 **AI veto on/off** 를 컬럼으로 올리면 소유자가 화면에서
직접 조절할 수 있다. migration · 함수 v3 · OpenAPI · Kotlin DTO · 폼을 한 번에 옮기는 작업이다.
