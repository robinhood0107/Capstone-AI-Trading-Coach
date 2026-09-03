# P1 daily operation: what was measured, what was fixed, and what still needs a decision

## KR

"매일 스스로 도는가"를 설계 문서가 아니라 실행으로 확인했다. 도는 것과 도는 척하는 것을
가르는 지점은 전부 실측으로 특정했고, 막던 것 다섯을 고쳤다.

### 결론부터 - 매일 돈다

`./capstone up --mock` 한 번으로 스택이 서고 저장된 의도가 실제 상태로 복원된다.

```
control    ARMED v22 KIS_MOCK VALID
policy     v5 CUSTOM 한도=2,000,000
schedule   2026-09-04 ARMED run_at=09:30 KST
gate       VALID eligible_from=2026-09-03
의도       MODELS=0 MOCK=1 AUTOMATION=1
캘린더     2026-01-01 ~ 2027-09-03 / 416 세션
seed 포인터 31 종목 / 1 번들
smoke      PASS, providerCalls=0
```

첫 세션은 09:30, 이후 매 거래일 개장 전 08:55 에 `roll_schedule`(V90:672)이 다음 세션을
예약한다. `control_state` 가 DB 행이라 재기동에 생존하고, `up` 의 리콘실러가 저장된 의도와
실제를 맞춘다. 양방향으로 실측했다 - `mock start` 뒤 `up` 은 `RECONCILED=ARMED`,
`mock stop` 뒤 `up` 은 `DISARMED_BY_INTENT` 다.

### 고친 것 다섯 - 전부 정상 경로를 막던 것

**일일 추론이 매일 막혔다.** `daily_inference.py:236` 이 이력의 마지막 바가 소스 세션과
같기를 요구하는데, 내가 캘린더 seeding 에 걸어 둔 날짜 컷 때문에 시장데이터가 있는 4~8월
구간에 캘린더 행이 없었다. LSTM 의 20세션 lookback 이 성립하지 않는다. 날짜로 자르는 대신
**이미 있는 행만 건드리지 않도록** 바꿨다. 확정 이력 보존이라는 목적은 그대로이고 공백만
메워진다 - `seeded=165 alreadyPresent=82`.

**연말이 하드스톱이었다.** pinned XKRX 달력은 유한하다(실측 2006-09-04 ~ 2027-09-03).
연도 단위로 요구하면 마지막 해가 `DateOutOfBounds` 로 통째로 거부되어 캘린더가 2026-12-30
에서 끊기고 그 뒤 arm 이 다음 세션을 찾지 못한다. 요청 범위를 달력 경계까지 잘라서 채운다 -
`clampedYears=2027:2027-01-01~2027-09-03`. 지금 캘린더는 1년 이상의 여유를 갖는다.

**주문이 나가지 않았다.** `daily_ready` 가 `manifest_kind='DAILY'` 만 인정하는데 DB 에는
`AUTOMATION_BOOTSTRAP` 하나뿐이라 영원히 false 였고 `SKIPPED_DATA_UNAVAILABLE` 로 닫혔다.
status API 의 blockers 에도 나타나지 않아 원인을 알 방법이 없었다. V117 로 두 종류를 모두
인정하되 `status='ACCEPTED'` 와 `as_of <= session_date + 09:20 KST` 는 그대로 뒀다.

**커스텀 정책이 하나도 저장되지 않았다.** V91:62 의 이름 없는 CHECK 가
`automation_policy_versions_check1` 로 자동 명명됐고, V111 이 v3 형태를 도입하며 이름이 다른
그 제약을 놓쳤다. v3 행은 `profile_v2` 를, 낡은 제약은 `profile_v1` 을 동시에 요구하므로 두
함수가 같은 이름을 내는 프리셋 세 조합 밖에서는 구조적으로 저장할 수 없다. 화면이 열어 둔
손절·익절·보유기간·ATR 조정이 전부 500 이었다. V118 로 그 제약만 지웠다 - V111 의
`v3_shape_check` 가 v1 형태에서 같은 조건을 그대로 요구하므로 잃는 보호가 없다.

**RAG 가 계약상 유효한 질문을 거절했다.** `topics` 는 스키마상 optional 인데 생략하면 검색
범위 함수가 최소 하나를 요구해 `503 RAG_UNAVAILABLE` 이 됐다. 런타임은 정상인데 메시지가
거짓이었다. optional 의 뜻대로 생략을 전체 허용 주제로 해석한다.

### 관통 하네스를 완성했다

`tests/e2e/full_pipeline_e2e.py` 는 자기 docstring 에 "구간별 검증은 이미 있었지만 이어서
돌린 적이 없다"고 적어 두었다. 끝까지 돌려보고 막는 것 넷을 고쳤다.

발명한 종목(`900000`~`900027`)을 커밋된 exact-31 카탈로그로 바꿨다. 런타임 이력 리더가 종목이
현재 명부 안에 있어야 한다고 요구하므로(V110) 발명한 코드로는 주문 경로를 한 번도 통과할 수
없었다 - 테스트가 통과해도 실제 사용을 검증하지 못한다는 뜻이다.

claim 직전에 일일 추론을 부른다. production 은 `serve()` 가 그 자리에서 부르는데
(`automation_runtime.py:872`) 드라이버는 claim 을 직접 잡아 그 단계를 건너뛰었다.

매니페스트와 함께 소스 세션 바를 씨딩하고, 호가 fixture 를 exact-31 전체로 넓혔다.
그리고 `P1_E2E_USE_REAL_SEED=1` 로 실물 seed 포인터를 쓰는 경로를 뒀다 - 추론 요청의
`bundleSha256` 을 모델이 자기가 로드한 번들과 대조하므로 합성 번들은 실물 서버가 서빙할 수
없다.

### 그래서 관통이 어디까지 갔는가

```
READINESS  accountComplete=True dailyShardFreshComplete=True principleActiveCurrent=True
           releaseActive=True signalCount=31 controlState=ARMED
전이       SCHEDULED -> PRECHECK -> SKIPPED_NO_ACTION
```

`SKIPPED_NO_ACTION` 이 **정답이다.** 매수 후보는 두 생산자가 모두 BUY 여야 하는데
(`lstm_signal == baseline_signal == "BUY"`) 실물 seed 의 분포가 이렇다.

| LSTM | RULE | 종목 |
|---|---|---|
| BUY | HOLD | 2 |
| HOLD | BUY | 1 |
| HOLD | HOLD | 15 |
| SELL | HOLD | 12 |
| SELL | SELL | 1 |
| **둘 다 BUY** | | **없음** |

즉 합의 게이트가 제 일을 했다. 신호 값도 건전하다 - `expectedReturn −3.651% ~ +1.639%`,
로그수익률 타깃이 의도대로 동작한다. 일일 배치는 `status=COMPLETE`,
`source_session=2026-09-03`, `bundle=a020ac6e`(실물 seed), inference request/response sha 기록,
LSTM 31 + RULE_BASELINE 31 = 62 신호다.

### 여전히 결정이 필요한 것

**시장데이터가 매일 갱신되지 않는다.** `market_data_bars` 를 쓰는 production 경로는
`market_data/repository.py` 하나이고 그것을 부르는 것은 `./capstone market-data …` 운영자
명령뿐이다. 스케줄러도 크론도 없고 `P1_DATA_ONLY_COLLECTOR_ENABLED` 는 compose 에서 false 다.

그런데 **루프가 멈추지는 않는다.** 소스 세션과 이력의 마지막 바가 같은 manifest 상태에서
파생되므로 함께 뒤로 밀린다. 실측으로 확인했다 - manifest 가 2026-08-28 하나뿐인 상태에서
2026-09-07 / 09-08 / 10-05 의 일일 추론이 모두 `IMPORTED` 로 성립한다.

문제는 가용성이 아니라 최신성이다. 매일 같은 20세션 창으로 같은 신호를 내고, 그것을 걸러내는
게이트가 없다 - `daily_ready` 의 조건은 `as_of <= session + 09:20` 상한뿐이라 오래된
manifest 는 항상 통과한다. 과제한의 거울상인 **과관용**이다.

이건 정책 결정이라 코드로 정하지 않았다. 선택지는 셋이다 - 일일 수집을 자동화한다,
`daily_ready` 에 하한을 두어 오래된 데이터로는 주문하지 않는다, 또는 최신성을 화면에 드러내고
운영자가 판단한다. 어느 쪽이든 "오래된 가격으로 조용히 거래한다"만 피하면 된다.

**실주문은 진입 창이 10분이다.** `automation.py:136-137` 이 `_KST_OPEN_TIME=09:30`,
`_KST_CLOSE_ORDER_TIME=09:40` 으로 고정한다. 그 밖에서 강제 실행하면 설계대로
`SKIPPED_LATE_START` 가 된다(실측 확인). 즉 실주문 검증은 그 창에서만 가능하다.

## EN

Whether the system runs itself every day was verified by execution, not by reading design
documents. Every point that separates running from appearing to run was pinned down by
measurement, and five blockers were fixed.

### The conclusion first: it does run every day

One `./capstone up --mock` brings the stack up and restores the saved intent into real state:
control `ARMED v22 KIS_MOCK VALID`, policy `v5 CUSTOM`, schedule `2026-09-04 ARMED` at 09:30
KST, gate `VALID`, intent `AUTOMATION=1`, calendar covering 2026-01-01 to 2027-09-03 (416
sessions), seed pointer 31 symbols in 1 bundle, smoke `PASS` with zero provider calls.

The first session runs at 09:30 and every trading day after that `roll_schedule` (V90:672)
books the next session for 08:55, before the open. `control_state` is a database row so it
survives a restart, and the reconciler in `up` aligns saved intent with reality — measured in
both directions: after `mock start`, `up` reports `RECONCILED=ARMED`; after `mock stop`, it
reports `DISARMED_BY_INTENT`.

### Five fixes, all of them things that blocked the normal path

**Daily inference was blocked every day.** `daily_inference.py:236` requires the last bar of
the history to equal the source session, but the date cut I had placed on calendar seeding
left the April-to-August range — where the market data actually lives — with no calendar rows,
so the LSTM's twenty-session lookback could not form. The cut was replaced by a rule that
leaves *existing rows* alone instead of cutting by date: the preservation intent is kept and
only the gaps fill (`seeded=165 alreadyPresent=82`).

**The year end was a hard stop.** The pinned XKRX calendar is finite (measured 2006-09-04 to
2027-09-03). Requesting whole years made the final year fail wholesale with `DateOutOfBounds`,
so the calendar ended at 2026-12-30 and arming could no longer find a next session. The
requested range is now clamped to the calendar bound, and the clamp is reported rather than
hidden.

**Orders could never leave.** `daily_ready` accepted only `manifest_kind='DAILY'` while the
database holds a single `AUTOMATION_BOOTSTRAP`, so it was permanently false and the run closed
as `SKIPPED_DATA_UNAVAILABLE` — and the status API's `blockers` stayed empty, so the cause was
invisible. V117 accepts both kinds while leaving `status='ACCEPTED'` and the
`as_of <= session_date + 09:20 KST` freshness bound untouched.

**No custom policy could be saved.** The unnamed CHECK from V91:62 was auto-named
`automation_policy_versions_check1`, and V111 — introducing the v3 shape — dropped a
differently named constraint and missed it. A v3 row must satisfy `profile_v2` while the stale
constraint simultaneously demanded `profile_v1`, so anything outside the three preset tuples
where both functions agree was structurally unsavable: every stop-loss, take-profit, holding
period and ATR adjustment the UI offers returned 500. V118 drops just that constraint; V111's
`v3_shape_check` already requires the same condition for v1-shaped rows, so no protection is
lost.

**RAG refused contract-valid questions.** `topics` is optional in the schema, but omitting it
left the retrieval scope function demanding at least one topic, producing
`503 RAG_UNAVAILABLE` — the runtime was healthy and the message was false. Omission now means
what optional means: no topic restriction.

### The end-to-end harness was completed

`tests/e2e/full_pipeline_e2e.py` states in its own docstring that the segments had been
verified but never run in sequence. Running it to the end exposed four blockers, all fixed:
invented symbols (`900000`–`900027`) replaced by the committed exact-31 catalogue, because the
runtime history reader requires a symbol to be in the current membership (V110) and invented
codes can therefore never traverse the order path; daily inference invoked immediately before
the claim, where production calls it (`automation_runtime.py:872`); source-session bars seeded
alongside the manifests; and the quote fixture widened to all 31 symbols. A
`P1_E2E_USE_REAL_SEED=1` path uses the already-imported real seed pointer, because the model
compares the request's `bundleSha256` against the bundle it loaded and a synthetic bundle can
never be served by the real inference server.

### How far the traversal got

Readiness came back fully green — `accountComplete`, `dailyShardFreshComplete`,
`principleActiveCurrent`, `releaseActive` all true with 31 signals and control `ARMED` — and
the state machine advanced `SCHEDULED → PRECHECK → SKIPPED_NO_ACTION`.

`SKIPPED_NO_ACTION` is the **correct** answer. A buy candidate requires both producers to say
BUY (`lstm_signal == baseline_signal == "BUY"`), and the real seed's distribution has no symbol
where both do: BUY/HOLD on 2, HOLD/BUY on 1, HOLD/HOLD on 15, SELL/HOLD on 12, SELL/SELL on 1.
The agreement gate did its job. The values are sound too — `expectedReturn` spans −3.651% to
+1.639%, which is the log-return target behaving as intended. The daily batch recorded
`status=COMPLETE`, `source_session=2026-09-03`, the real seed bundle, both inference request and
response hashes, and 62 signals (31 LSTM, 31 rule baseline).

### What still needs a decision

**Market data does not refresh itself.** The only production writer of `market_data_bars` is
`market_data/repository.py`, reached only through the operator commands under
`./capstone market-data …`. There is no scheduler or cron, and `P1_DATA_ONLY_COLLECTOR_ENABLED`
is false in compose.

The loop does not stop, though. The source session and the last bar of the history derive from
the same manifest state, so they slide together — measured: with the manifest fixed at
2026-08-28, daily inference for 2026-09-07, 09-08 and even 10-05 all complete as `IMPORTED`.

The problem is freshness, not availability. The same twenty-session window produces the same
signals every day, and nothing catches it: `daily_ready` bounds only the upper side
(`as_of <= session + 09:20`), so an old manifest always passes. This is the mirror image of an
over-restriction — an over-tolerance.

That is a policy decision, so it was not settled in code. Three options exist: automate the
daily collection; add a lower bound to `daily_ready` so stale data cannot trade; or surface the
staleness in the interface and let the operator judge. Any of them is acceptable as long as
"quietly trading on stale prices" is not.

**Live ordering has a ten-minute entry window.** `automation.py:136-137` fixes
`_KST_OPEN_TIME=09:30` and `_KST_CLOSE_ORDER_TIME=09:40`. Forcing a run outside it yields
`SKIPPED_LATE_START` by design, which was confirmed by measurement. Live order verification is
therefore only possible inside that window.
