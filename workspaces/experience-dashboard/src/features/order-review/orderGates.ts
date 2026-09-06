/**
 * 주문 제출 관문.
 *
 * 제출 버튼이 눌리기까지 여섯 관문을 순서대로 통과해야 한다. **하나라도 막히면 왜 막혔는지를
 * 그 자리에 적는다** — 버튼만 회색으로 두면 고장으로 읽힌다.
 *
 * 계산은 여기 모아 둔다. 화면은 결과를 그리기만 한다.
 *
 * 여기서 막는 것은 사용자를 빨리 되돌려 주기 위한 것이지 안전장치의 전부가 아니다. 서버도
 * 같은 것들을 다시 본다 — Decision 근거 없는 주문은 백엔드가 거부하고, Kill Switch 와
 * 자동운용 상태는 DB 가 강제한다.
 */
import type { AutomationStatusV2, DecisionAction, MockBuyable, OrderIntent } from '@/shared/api/wire';

export type GateId = 'G1' | 'G2' | 'G3' | 'G4' | 'G5' | 'G6';

export interface Gate {
  id: GateId;
  label: string;
  /** 통과했나. `null` 은 아직 판단할 근거가 없다는 뜻이다 — 통과도 실패도 아니다. */
  passed: boolean | null;
  /** 막혔을 때, 또는 아직 모를 때 화면에 적을 말. */
  note: string;
}

export interface GateInput {
  status: AutomationStatusV2 | null;
  killSwitchActive: boolean | null;
  buyable: MockBuyable | null;
  intent: OrderIntent | null;
  /** evaluate-order 판정. 아직 안 했으면 null. */
  action: DecisionAction | null;
  /** 판정 유효시각이 지났나. */
  decisionExpired: boolean;
  /** 확인 화면을 지났나. */
  acknowledged: boolean;
}

/**
 * 판정 네 값을 그대로 다룬다.
 *
 * `HOLD` 는 위반이 아니라 **근거 부재**다(`EvaluationResult.kt`). 같은 "제출 불가"라도
 * 사용자가 할 일이 다르다 — 위반은 값을 고쳐야 하고, 보류는 근거가 갖춰지길 기다려야 한다.
 */
export function actionNote(action: DecisionAction): string {
  switch (action) {
    case 'ALLOW':
      return '원칙을 모두 통과했습니다.';
    case 'WARN':
      return '경고가 있습니다. 경고가 있는 주문은 이 화면에서 내보내지 않습니다.';
    case 'HOLD':
      return '위반은 아니지만 판단에 필요한 근거가 없습니다. 근거가 갖춰진 뒤 다시 평가하세요.';
    case 'BLOCK':
      return '원칙을 위반했습니다. 이 주문은 나가지 않습니다.';
  }
}

export function evaluateGates(input: GateInput): Gate[] {
  const { status, killSwitchActive, buyable, intent, action, decisionExpired, acknowledged } = input;

  const g1: Gate = status
    ? status.controlState === 'DISARMED'
      ? { id: 'G1', label: '자동운용 꺼짐', passed: true, note: '' }
      : {
          id: 'G1',
          label: '자동운용 꺼짐',
          passed: false,
          note: '자동운용이 켜져 있는 동안에는 손으로 주문을 내지 않습니다. 먼저 자동운용을 멈추세요.',
        }
    : { id: 'G1', label: '자동운용 꺼짐', passed: null, note: '자동운용 상태를 아직 못 읽었습니다.' };

  const g2: Gate =
    killSwitchActive === null
      ? { id: 'G2', label: 'Kill Switch 꺼짐', passed: null, note: 'Kill Switch 상태를 아직 못 읽었습니다.' }
      : killSwitchActive
        ? {
            id: 'G2',
            label: 'Kill Switch 꺼짐',
            passed: false,
            note: 'Kill Switch가 작동 중입니다. 해제 전에는 주문이 나가지 않습니다.',
          }
        : { id: 'G2', label: 'Kill Switch 꺼짐', passed: true, note: '' };

  const g3: Gate = ((): Gate => {
    const label = '주문가능금액 충족';
    if (!intent) return { id: 'G3', label, passed: null, note: '주문 내용을 먼저 입력하세요.' };
    if (intent.side === 'SELL') {
      return { id: 'G3', label, passed: true, note: '매도는 주문가능금액을 보지 않습니다.' };
    }
    if (!buyable) return { id: 'G3', label, passed: null, note: '주문가능금액을 아직 못 읽었습니다.' };
    if (intent.estimatedAmount > buyable.buyableAmountKrw) {
      return {
        id: 'G3',
        label,
        passed: false,
        note: `주문가능금액을 넘었습니다. 최대 ${buyable.buyableQuantity}주까지 낼 수 있습니다.`,
      };
    }
    return { id: 'G3', label, passed: true, note: '' };
  })();

  const g4: Gate = ((): Gate => {
    const label = '원칙 판정 ALLOW';
    if (action === null) return { id: 'G4', label, passed: null, note: '아직 평가하지 않았습니다.' };
    if (decisionExpired) {
      return { id: 'G4', label, passed: false, note: '판정이 만료됐습니다. 다시 평가하세요.' };
    }
    if (action !== 'ALLOW') {
      return { id: 'G4', label, passed: false, note: actionNote(action) };
    }
    return { id: 'G4', label, passed: true, note: '' };
  })();

  const g5: Gate = acknowledged
    ? { id: 'G5', label: '내용 확인', passed: true, note: '' }
    : { id: 'G5', label: '내용 확인', passed: null, note: '제출 전에 확인 화면을 한 번 거칩니다.' };

  // 실계좌 경로는 이 화면에 아예 없다. 모의계좌 하나만 부른다.
  const g6: Gate = { id: 'G6', label: '모의계좌 경로', passed: true, note: '' };

  return [g1, g2, g3, g4, g5, g6];
}

/** 확인 화면으로 넘어갈 수 있나. G5 를 뺀 나머지가 전부 통과여야 한다. */
export function canConfirm(gates: Gate[]): boolean {
  return gates.filter((gate) => gate.id !== 'G5').every((gate) => gate.passed === true);
}

/** 실제로 제출할 수 있나. 여섯 개가 전부 통과여야 한다. */
export function canSubmit(gates: Gate[]): boolean {
  return gates.every((gate) => gate.passed === true);
}

/** 지금 막고 있는 첫 관문. 화면 맨 위에 이유를 한 줄로 적는 데 쓴다. */
export function firstBlocking(gates: Gate[]): Gate | null {
  return gates.find((gate) => gate.passed !== true) ?? null;
}

/**
 * 체결 조회 창. 서버가 KST 일 경계로 **최대 31일**만 받는다
 * (`BrokerageFillRequestParser.kt:20`). 넘기면 `RANGE_EXCEEDS_31_DAYS` 로 거절된다.
 *
 * 화면에서 임의로 넓히지 못하도록 여기서만 만든다.
 */
export const FILL_WINDOW_MAX_DAYS = 31;

export function fillWindow(now = new Date()): { from: string; to: string } {
  const day = (date: Date) => date.toISOString().slice(0, 10);
  const from = new Date(now);
  // inclusive 라 30일을 빼야 31일 창이 된다.
  from.setUTCDate(from.getUTCDate() - (FILL_WINDOW_MAX_DAYS - 1));
  return { from: day(from), to: day(now) };
}

/** 수량과 단가에서 주문 의도를 만든다. 금액은 서버가 정확히 일치하기를 요구한다. */
export function buildIntent(fields: {
  symbol: string;
  side: 'BUY' | 'SELL';
  orderType: 'MARKET' | 'LIMIT';
  quantity: number;
  estimatedPrice: number;
  strategyId: string;
}): OrderIntent | null {
  const { symbol, quantity, estimatedPrice, strategyId } = fields;
  if (!symbol || !strategyId) return null;
  if (!Number.isInteger(quantity) || quantity < 1) return null;
  if (!Number.isInteger(estimatedPrice) || estimatedPrice < 1) return null;
  return {
    symbol,
    side: fields.side,
    orderType: fields.orderType,
    quantity,
    estimatedPrice,
    estimatedAmount: quantity * estimatedPrice,
    timeframe: '1d',
    strategyId,
  };
}
