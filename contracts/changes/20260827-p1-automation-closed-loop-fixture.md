# P1 automation closed-loop fixture runtime

## KR

Owner Phase A의 자동운용 폐루프를 deterministic fixture transport와 append-only state boundary로
구현한다. 기존 `automation-control|run|position|event.v1` wire bytes는 변경하지 않는다.

- tick 하나는 durable boundary 하나만 전진하며 같은 `runId + tickId` 재전달은 strict no-op이다.
- arm 시 제공된 baseline account digest를 첫 append-only event로 봉인한다.
- 미완료 주문 대사 → 만기 SELL → LSTM+baseline 합의 SELL → 신규 BUY 후보 순서를 강제한다.
- 여러 SELL은 만기 초과 session 내림차순, entry session 오름차순, symbol 오름차순으로 하나만 고른다.
- 신규 BUY는 두 signal 모두 BUY, 기존 bot/baseline position 없음, expected return 내림차순,
  confidence 내림차순, symbol 오름차순으로 최종 하나만 선택한다.
- BUY만 Vertex fixture를 한 번 사용한다. `VETO_BUY`와 `ABSTAIN`은 order 0으로 종료하며 두 번째
  후보를 찾지 않는다. SELL의 Vertex call은 0이다.
- Risk, Kill Switch, fresh complete account/quote, buyable quantity와 session cap을 통과한 뒤에만
  quantity 1 LIMIT reservation을 만들며 quote는 한 번만 읽는다.
- BUY limit는 valid tick 한 단계 위, SELL limit는 한 단계 아래에서 상·하한가로 clamp한다.
- session logical submit은 최대 1이며 ambiguous submit은 재주문 없이 `PENDING_RECONCILIATION`이다.
- 15:20 미체결은 한 번 cancel하고, cancel 실패와 account drift는 `HALTED`다.
- bot이 직접 채운 quantity 1 lot만 자동 SELL하며 entry 뒤 다섯 번째 XKRX session 만기를 계산한다.
  HALTED에서는 자동 청산하지 않는다.

fixture transport의 Vertex, quote, submit, reconcile, cancel은 logical outcome만 제공한다. provider,
account, balance, order, KIS와 GDELT physical call은 모두 0이다. Live adapter, credential 처리,
public endpoint와 실제 주문 권한은 이 변경에 포함되지 않는다.

## EN

This change implements the Owner Phase A automation closed loop with a deterministic fixture transport and an
append-only state boundary. Existing `automation-control|run|position|event.v1` wire bytes remain unchanged.

- One tick advances at most one durable boundary; redelivery of the same `runId + tickId` is a strict no-op.
- The baseline account digest supplied at arm time is sealed in the first append-only event.
- Selection priority is unfinished-order reconciliation, expiry SELL, LSTM plus baseline consensus SELL, then
  one new-BUY candidate.
- Multiple SELL exits are ordered by sessions overdue descending, entry session ascending, and symbol ascending.
- A new BUY requires both BUY signals and no bot/baseline position, then ranks by expected return descending,
  confidence descending, and symbol ascending.
- Only BUY invokes the Vertex fixture once. `VETO_BUY` and `ABSTAIN` end with zero orders and no second
  candidate. SELL makes zero Vertex calls.
- A quantity-one LIMIT reservation is created only after Risk, Kill Switch, fresh complete account/quote,
  buyable quantity, and session-cap checks. A quote is read once.
- BUY uses one valid tick above and SELL one valid tick below, clamped to the price limits.
- Logical submit is capped at one per session. An ambiguous submit becomes `PENDING_RECONCILIATION` without
  resubmission.
- An unfilled order is cancelled once at 15:20. Cancel failure and account drift become `HALTED`.
- Only one-share bot-filled lots are eligible for automatic SELL, with expiry on the fifth XKRX session after
  entry. HALTED never triggers automatic liquidation.

Vertex, quote, submit, reconciliation, and cancellation in the fixture transport are logical outcomes only.
Provider, account, balance, order, KIS, and GDELT physical calls are all zero. This change includes no live
adapter, credential handling, public endpoint, or real order authority.
