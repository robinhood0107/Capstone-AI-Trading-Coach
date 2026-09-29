import { randomUUID } from 'node:crypto';
import { availableBarFor, projectShowcase, showcaseMetadata, visibleBars, visibleShowcaseEvents, scenarioAssumptions } from './ledger';
import { marketPhaseAt } from './clock';
import { defaultOverlay, getOverlay, resetOverlay, saveOverlay } from './store';
import type { DemoOverlay } from '../shared/contracts';

export class OverlayError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(code: string, status = 400) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

type OverlayAction = Record<string, unknown> & { action?: unknown };

function stringField(value: unknown, maxLength: number): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed || Array.from(trimmed).length > maxLength) return null;
  return trimmed;
}

function kstIso(now: Date): string {
  const clock = marketPhaseAt(now);
  return `${clock.dateKst}T${clock.timeKst}+09:00`;
}

function amountFee(amount: number, bps: number): number {
  return Math.floor(amount * bps / 10_000 + 0.5);
}

function riskOrderLimit(overlay: DemoOverlay, equity: number): number {
  const rule = overlay.principleRules?.find((candidate) => candidate.ruleId === 'max_single_order_amount');
  if (rule && rule.enabled === true && Number.isFinite(Number(rule.threshold))) return Math.floor(Number(rule.threshold));
  const ratio = overlay.riskProfile === 'conservative' ? 0.05 : overlay.riskProfile === 'growth' ? 0.30 : 0.15;
  return Math.floor(equity * ratio);
}

export function readSessionOverlay(sessionHash: string): DemoOverlay {
  return getOverlay(sessionHash);
}

export function applyOverlayAction(
  sessionHash: string,
  input: OverlayAction,
  now = new Date(),
): { overlay: DemoOverlay; notice?: string } {
  const overlay = getOverlay(sessionHash);
  const action = input.action;
  if (action === 'reset') {
    resetOverlay(sessionHash);
    return { overlay: defaultOverlay(), notice: '계좌 설정과 메모를 초기화했습니다. Agent의 오늘 사용 횟수는 유지됩니다.' };
  }

  if (action === 'set-auto') {
    if (typeof input.armed !== 'boolean') throw new OverlayError('INVALID_CONTROL_STATE');
    overlay.autoArmed = input.armed;
    saveOverlay(sessionHash, overlay, now);
    return { overlay, notice: input.armed ? '자동운용을 시작했습니다.' : '자동운용을 중단했습니다.' };
  }

  if (action === 'set-principle') {
    const value = input.riskProfile;
    if (value !== 'conservative' && value !== 'balanced' && value !== 'growth') {
      throw new OverlayError('INVALID_PRINCIPLE');
    }
    overlay.riskProfile = value;
    overlay.dailyLossLimitPct = value === 'conservative' ? 1 : value === 'growth' ? 3 : 2;
    saveOverlay(sessionHash, overlay, now);
    return { overlay, notice: '투자 원칙을 저장했습니다.' };
  }

  if (action === 'add-note') {
    const text = stringField(input.text, 500);
    if (!text) throw new OverlayError('NOTE_TEXT_INVALID');
    if (overlay.notes.length >= 25) throw new OverlayError('NOTE_LIMIT_REACHED', 409);
    const requestedEventId = stringField(input.sourceEventId, 100);
    const eventIds = new Set([
      ...showcaseMetadata().events.map((event) => event.id),
      ...overlay.virtualEvents.map((event) => String(event.id)),
    ]);
    const note = {
      id: randomUUID(),
      text,
      createdAt: kstIso(now),
      ...(requestedEventId && eventIds.has(requestedEventId) ? { sourceEventId: requestedEventId } : {}),
    };
    overlay.notes = [note, ...overlay.notes];
    saveOverlay(sessionHash, overlay, now);
    return { overlay, notice: '메모를 저장했습니다.' };
  }

  if (action === 'delete-note') {
    const noteId = stringField(input.noteId, 100);
    if (!noteId) throw new OverlayError('NOTE_ID_INVALID');
    overlay.notes = overlay.notes.filter((note) => note.id !== noteId);
    saveOverlay(sessionHash, overlay, now);
    return { overlay, notice: '메모를 삭제했습니다.' };
  }

  if (action === 'place-virtual-order') {
    const metadata = showcaseMetadata();
    const date = marketPhaseAt(now).dateKst;
    if (date < metadata.sourceRange.end) throw new OverlayError('SCENARIO_NOT_FINALIZED', 409);
    const side = input.side;
    if (side !== 'BUY' && side !== 'SELL') throw new OverlayError('ORDER_SIDE_INVALID');
    const ticker = stringField(input.symbol, 20);
    const availableSymbols = new Set(visibleBars(date).map((bar) => bar.symbol));
    if (!ticker || !availableSymbols.has(ticker)) throw new OverlayError('ORDER_SYMBOL_UNAVAILABLE');
    const quantity = Number(input.quantity);
    if (!Number.isSafeInteger(quantity) || quantity < 1 || quantity > 100) throw new OverlayError('ORDER_QUANTITY_INVALID');
    const idempotencyKey = stringField(input.idempotencyKey, 128);
    if (!idempotencyKey || !/^[A-Za-z0-9._~-]{16,128}$/.test(idempotencyKey)) throw new OverlayError('ORDER_IDEMPOTENCY_INVALID');
    const prior = overlay.virtualEvents.find((event) => event.idempotencyKey === idempotencyKey);
    if (prior) return { overlay, notice: '이미 반영한 주문입니다.' };
    const orderCount = overlay.virtualEvents.filter((event) => event.type === 'ORDER_CREATED').length;
    if (orderCount >= 20) throw new OverlayError('VIRTUAL_ORDER_LIMIT_REACHED', 409);

    const baseEvents = visibleShowcaseEvents(now, true);
    const bars = visibleBars(date);
    const current = projectShowcase(baseEvents, overlay, now, bars);
    const bar = availableBarFor(ticker, date);
    if (!bar) throw new OverlayError('PRICE_SOURCE_UNAVAILABLE', 409);
    const assumptions = scenarioAssumptions();
    const slippageBps = Number(assumptions.slippageBpsPerSide ?? 10);
    const commissionBps = Number(assumptions.commissionBpsPerSide ?? 1.5);
    const sellTaxBps = Number(assumptions.kospiSellTaxBps ?? 20);
    const riskLimit = riskOrderLimit(overlay, current.equity);
    const referencePrice = bar.close;
    const price = Math.floor(referencePrice * (side === 'BUY' ? 1 + slippageBps / 10_000 : 1 - slippageBps / 10_000) + 0.5);
    const grossAmount = price * quantity;
    const commission = amountFee(grossAmount, commissionBps);
    const transactionTax = side === 'SELL' ? amountFee(grossAmount, sellTaxBps) : 0;
    const baseAt = kstIso(now);
    const orderId = randomUUID();
    const eventId = randomUUID();

    if (grossAmount > riskLimit) {
      overlay.virtualEvents.push({
        id: eventId,
        atKst: baseAt,
        type: 'DECISION_REJECTED',
        symbol: ticker,
        side,
        quantity,
        reason: `현재 원칙의 1회 주문 상한 ${riskLimit.toLocaleString('ko-KR')}원을 넘었습니다.`,
        sourceDate: bar.date,
        simulationOnly: true,
        idempotencyKey,
      });
      saveOverlay(sessionHash, overlay, now);
      return { overlay, notice: '위험 검토에서 주문을 보류했습니다. 원칙과 주문 크기를 조정할 수 있습니다.' };
    }

    if (side === 'BUY' && grossAmount + commission > current.cash) {
      throw new OverlayError('VIRTUAL_INSUFFICIENT_CASH', 409);
    }
    if (side === 'SELL') {
      const position = current.positions.find((candidate) => candidate.symbol === ticker);
      if (!position || quantity > position.quantity) throw new OverlayError('VIRTUAL_INSUFFICIENT_POSITION', 409);
    }

    overlay.virtualEvents.push(
      {
        id: orderId,
        atKst: baseAt,
        type: 'ORDER_CREATED',
        symbol: ticker,
        side,
        quantity,
        status: 'SIMULATED_ACCEPTED',
        reason: '주문 검토',
        sourceDate: bar.date,
        simulationOnly: true,
        idempotencyKey,
      },
      {
        id: eventId,
        orderId,
        atKst: kstIso(new Date(now.getTime() + 1_000)),
        type: 'FILL',
        symbol: ticker,
        side,
        quantity,
        price,
        referencePrice,
        grossAmount,
        commission,
        transactionTax,
        sourceDate: bar.date,
        priceField: `last historical close (${bar.date}) with assumed slippage`,
        simulationOnly: true,
        idempotencyKey,
      },
    );
    saveOverlay(sessionHash, overlay, now);
    return { overlay, notice: `${grossAmount.toLocaleString('ko-KR')}원 주문을 반영했습니다.` };
  }

  throw new OverlayError('UNKNOWN_OVERLAY_ACTION');
}
