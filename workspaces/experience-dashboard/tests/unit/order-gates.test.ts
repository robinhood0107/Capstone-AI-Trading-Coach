import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildIntent,
  canConfirm,
  canSubmit,
  evaluateGates,
  fillWindow,
  firstBlocking,
  FILL_WINDOW_MAX_DAYS,
  type GateInput,
} from '../../src/features/order-review/orderGates.ts';
import type { AutomationStatusV2, MockBuyable } from '../../src/shared/api/wire.ts';

const DISARMED = {
  contractId: 'automation-status.v2',
  controlState: 'DISARMED',
  projectionState: 'DISARMED',
  controlVersion: 1,
  brokerageMode: 'KIS_MOCK',
  accountId: 'acct_cccccccccccccccccccccccccccccccc',
  policy: null,
  killSwitchActive: false,
  certificationStatus: 'VALID',
  openPositionCount: 0,
  unresolvedReconciliation: false,
  canArm: true,
  blockers: [],
} as AutomationStatusV2;

const BUYABLE: MockBuyable = {
  accountId: DISARMED.accountId!,
  brokerageMode: 'KIS_MOCK',
  symbol: '005930',
  cashKrw: 1_000_000,
  estimatedPrice: 71_000,
  buyableAmountKrw: 1_000_000,
  buyableQuantity: 14,
  observedAt: new Date().toISOString(),
  sourceVersion: 'test',
};

const INTENT = buildIntent({
  symbol: '005930',
  side: 'BUY',
  orderType: 'MARKET',
  quantity: 10,
  estimatedPrice: 71_000,
  strategyId: 'strategy_00000000',
})!;

function base(overrides: Partial<GateInput> = {}): GateInput {
  return {
    status: DISARMED,
    killSwitchActive: false,
    buyable: BUYABLE,
    intent: INTENT,
    action: 'ALLOW',
    decisionExpired: false,
    acknowledged: false,
    ...overrides,
  };
}

test('금액은 수량 곱하기 단가와 정확히 일치한다', () => {
  assert.equal(INTENT.estimatedAmount, 10 * 71_000);
  // 서버가 정수만 받는다. 소수나 0 이하는 주문 자체를 만들지 않는다.
  assert.equal(buildIntent({ ...INTENT, quantity: 0 }), null);
  assert.equal(buildIntent({ ...INTENT, quantity: 1.5 }), null);
  assert.equal(buildIntent({ ...INTENT, estimatedPrice: 0 }), null);
  assert.equal(buildIntent({ ...INTENT, strategyId: '' }), null);
});

test('전부 통과하면 확인 화면으로 넘어갈 수 있다', () => {
  const gates = evaluateGates(base());
  assert.equal(canConfirm(gates), true);
  // 확인 화면을 지나기 전에는 제출할 수 없다.
  assert.equal(canSubmit(gates), false);
  assert.equal(canSubmit(evaluateGates(base({ acknowledged: true }))), true);
});

test('자동운용이 켜져 있으면 손으로 주문하지 않는다', () => {
  const gates = evaluateGates(
    base({ status: { ...DISARMED, controlState: 'ARMED', projectionState: 'ARMED' } }),
  );
  assert.equal(canConfirm(gates), false);
  assert.equal(firstBlocking(gates)?.id, 'G1');
});

test('Kill Switch 가 켜져 있으면 막는다', () => {
  const gates = evaluateGates(base({ killSwitchActive: true }));
  assert.equal(firstBlocking(gates)?.id, 'G2');
});

test('주문가능금액을 넘으면 막고 최대 수량을 알려 준다', () => {
  const big = buildIntent({
    symbol: '005930',
    side: 'BUY',
    orderType: 'MARKET',
    quantity: 100,
    estimatedPrice: 71_000,
    strategyId: 'strategy_00000000',
  })!;
  const gates = evaluateGates(base({ intent: big }));
  const g3 = gates.find((gate) => gate.id === 'G3')!;
  assert.equal(g3.passed, false);
  assert.match(g3.note, /14주/);
});

test('매도는 주문가능금액을 보지 않는다', () => {
  const sell = { ...INTENT, side: 'SELL' as const };
  const gates = evaluateGates(base({ intent: sell, buyable: null }));
  assert.equal(gates.find((gate) => gate.id === 'G3')!.passed, true);
});

test('ALLOW 가 아니면 내보내지 않고, HOLD 는 위반과 다르게 설명한다', () => {
  for (const action of ['WARN', 'BLOCK', 'HOLD'] as const) {
    const gates = evaluateGates(base({ action }));
    assert.equal(canConfirm(gates), false, `${action} 는 통과하면 안 된다`);
  }
  const hold = evaluateGates(base({ action: 'HOLD' })).find((gate) => gate.id === 'G4')!;
  const block = evaluateGates(base({ action: 'BLOCK' })).find((gate) => gate.id === 'G4')!;
  assert.match(hold.note, /근거/);
  assert.match(block.note, /위반/);
  assert.notEqual(hold.note, block.note);
});

test('만료된 판정으로는 제출하지 않는다', () => {
  const gates = evaluateGates(base({ decisionExpired: true }));
  assert.equal(gates.find((gate) => gate.id === 'G4')!.passed, false);
});

test('체결 조회 창은 서버가 받는 31일을 넘지 않는다', () => {
  const { from, to } = fillWindow(new Date('2026-09-07T00:00:00Z'));
  assert.equal(to, '2026-09-07');
  assert.equal(from, '2026-08-08');
  // inclusive 라 두 날짜 차이는 30일이어야 31일 창이 된다. 31이면 서버가 거절한다.
  const spanDays = (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000;
  assert.equal(spanDays, FILL_WINDOW_MAX_DAYS - 1);
});

test('아직 모르는 것은 통과도 실패도 아니다', () => {
  const gates = evaluateGates(base({ status: null, killSwitchActive: null, action: null }));
  for (const id of ['G1', 'G2', 'G4']) {
    assert.equal(gates.find((gate) => gate.id === id)!.passed, null, `${id} 는 null 이어야 한다`);
  }
  assert.equal(canConfirm(gates), false);
});
