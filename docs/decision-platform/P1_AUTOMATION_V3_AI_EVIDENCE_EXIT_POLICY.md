# Automation V3: AI 근거 우선 판단과 사용자 청산정책

## 현재 결론

구현 범위는 `GO_WITH_EXTERNAL_HARD_GATES`다. V3는 근거 없는 모델 출력에
주문 권한을 주지 않고, 사용자 정책과 RiskEngine이 정한 범위를 AI가 넓히지
못하게 한다. 이 문서는 수익성·최적성·실거래 준비 완료를 주장하지 않는다.

## 매수 흐름

```text
PRECHECK
→ deterministic eligibility (정지/관리/정리매매/unknown 제외)
→ NEWS_SCREENING (후보 전체, 최대 grounded call 1)
→ host citation/quote/domain/injection 검증
→ AI_JUDGING (검증 근거가 한 건 이상일 때만)
→ BUY_CANDIDATE_SELECTED
→ deterministic quantity/RiskEngine
→ KIS Mock submit/reconciliation
```

- AI OFF이면 screening/judge 호출은 모두 0이다.
- 근거 0건이면 `NO_VETO`, score 0.5이며 judge 호출 없이 규칙 순위를 쓴다.
- `VETO_BUY`는 host가 관측한 같은 후보의 citation과 exact quote가 있을 때만
  유효하다.
- 미래 날짜, 미등록 25-domain, support 없는 quote는 근거가 아니다.
- prompt injection은 그 후보만 `ABSTAIN/PROMPT_INJECTION`으로 제외한다.
- provider/budget/ledger 전체 실패는 해당 run의 신규매수 0이다.
- SCREEN/JUDGE는 socket 이전에 run/phase/input hash와 물리 호출 상한을 별도
  트랜잭션으로 예약한다. 호출 중 종료된 `RESERVED`/`FAILED_UNKNOWN`은 자동
  재호출하지 않으며, 완료된 JUDGE만 정제된 bounded 결과를 재생한다.
- URL은 검증 중 자동 GET하지 않으며 paywall/login을 우회하지 않는다.

## 청산 우선순위

```text
UNRESOLVED_RECONCILIATION
→ STOP_LOSS
→ ATR_TRAILING
→ MODEL_SELL
→ TAKE_PROFIT
→ MAX_HOLDING_SESSIONS
→ NEW_BUY
```

Wilder TR은 `max(high-low, |high-prevClose|, |low-prevClose|)`다. 최초
`atrPeriod`개 TR의 산술평균 뒤에는
`(priorATR × (period-1) + TR) / period`를 쓴다. 구현은 float 없이
정수와 precision-50 `Decimal`만 사용한다.

신규 BUY fill의 peak는 실제 평균 체결가다. 이후 peak는 완료 bar high와 현재
quote를 포함한 최댓값으로만 증가하고 trailing stop은 이전 값보다 내려가지
않는다. 진입 세션에는 ATR 청산을 발화하지 않는다. 부분 SELL은 잔여수량과
peak/trailing state를 보존한다.

## 프리셋

| preset | stop/take | max holding | ATR | MODEL_SELL |
|---|---:|---:|---:|---|
| conservative | 300/500 bps | 20 sessions | 22 × 2.5 | ON |
| balanced | 500/1,000 bps | 60 sessions | 22 × 3.0 | ON |
| aggressive | 800/1,500 bps | unlimited | 22 × 3.5 | ON |

한 필드라도 달라지면 `custom`이다. 기존 position은 entry snapshot을 유지하고
새 정책은 신규 position에만 적용한다.

## AI 설정

`minimal | low | medium`만 사용자 표면에 허용한다. 2026-08-31 구현 시점의
[Google 공식 Gemini 3.5 Flash 가이드](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-5-flash)는
세 수준을 모두 지원한다(`high`도 provider는 지원하지만 이 제품 계약에서는
비용·지연 상한을 위해 노출하지 않는다).

AI ON인데 credential 또는 최소 run budget이 없으면 arm은
`AI_PROVIDER_NOT_READY`로 닫힌다. secret을 제외한 provider/model/base URL/언어/
budget/AI enable/thinking 설정 JSON과 canonical hash는 arm과 run에 함께 snapshot되어
진행 중 설정 변경이 기존 run의 provider 입력을 바꾸지 못한다.

## 아직 PASS가 아닌 것

- 실제 Google grounding 기능 probe
- KIS Live read-only bootstrap
- 실제 장중 KIS Mock 주문·대사
- 연속 3 XKRX 실거래일 soak
- Team A REAL UI와 Team B REAL artifact를 포함한 `P1_FINAL`

이 항목들은 synthetic/history replay 성공으로 자동 승격하지 않는다.
