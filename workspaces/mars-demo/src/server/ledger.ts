import scenarioData from '../../data/scenario.v1.json';
import type { DemoBar, DemoDailyPoint, DemoOverlay, DemoPosition } from '../shared/contracts';

interface LedgerEvent extends Record<string, unknown> {
  id: string;
  atKst: string;
  type: string;
}

interface ScenarioDocument {
  seedVersion: string;
  source: {
    provider: string;
    sourceSha256: string;
    scenarioRange: { start: string; end: string };
  };
  bars: DemoBar[];
  showcasePortfolio: {
    id: string;
    label: string;
    selectionRule: string;
    sourceRange: { start: string; end: string };
    initialCapital: number;
    selectedSymbols: string[];
    final: { equity: number; cash: number; returnBps: number; realizedPnl: number; dividendCash: number };
    events: LedgerEvent[];
    benchmark: { ticker: string; returnBps: number };
  };
  backtest: {
    id: string;
    label: string;
    testRange: { start: string; end: string };
    signal: string;
    execution: string;
    universeLimitation: string;
    selectionMethod: string;
    initialCapital: number;
    events: Record<string, unknown>[];
    daily: { date: string; equity: number; cash: number; actionCount: number; openPositions: number }[];
    usesFutureData: false;
  };
  calendar: { policyVersion: string; sourceSha256: string };
  assumptions: Record<string, unknown>;
}

const scenario = scenarioData as ScenarioDocument;

function bps(value: number): number {
  return Math.floor(value + 0.5);
}

function sortEvents(events: LedgerEvent[]): LedgerEvent[] {
  return [...events].sort((left, right) => {
    const time = Date.parse(left.atKst) - Date.parse(right.atKst);
    return time || left.id.localeCompare(right.id);
  });
}

export interface LedgerProjection {
  cash: number;
  equity: number;
  returnBps: number;
  realizedPnl: number;
  unrealizedPnl: number;
  dividendCash: number;
  positions: DemoPosition[];
  daily: DemoDailyPoint[];
  events: LedgerEvent[];
  orders: Record<string, unknown>[];
  latestVisibleDate: string | null;
}

export function visibleBars(asOfDate: string): DemoBar[] {
  return scenario.bars.filter((bar) => bar.date <= asOfDate);
}

export function visibleShowcaseEvents(asOf: Date, retrospectiveAvailable: boolean): LedgerEvent[] {
  if (!retrospectiveAvailable) return [];
  return sortEvents(scenario.showcasePortfolio.events).filter((event) => Date.parse(event.atKst) <= asOf.getTime());
}

export function projectShowcase(
  baseEvents: LedgerEvent[],
  overlay: DemoOverlay,
  asOf: Date,
  bars: DemoBar[],
): LedgerProjection {
  const initialCapital = scenario.showcasePortfolio.initialCapital;
  const overlayEvents = overlay.virtualEvents as LedgerEvent[];
  const events = sortEvents([...baseEvents, ...overlayEvents]).filter((event) => Date.parse(event.atKst) <= asOf.getTime());
  const latestBarBySymbol = new Map<string, DemoBar>();
  for (const bar of bars) {
    const existing = latestBarBySymbol.get(bar.symbol);
    if (!existing || bar.date > existing.date) latestBarBySymbol.set(bar.symbol, bar);
  }

  let cash = initialCapital;
  let realizedPnl = 0;
  let dividendCash = 0;
  const quantities = new Map<string, number>();
  const costBases = new Map<string, number>();
  const marks = new Map<string, number>();
  const orders = new Map<string, Record<string, unknown>>();
  const daily: DemoDailyPoint[] = [];
  let peak = initialCapital;

  for (const event of events) {
    const symbol = typeof event.symbol === 'string' ? event.symbol : undefined;
    switch (event.type) {
      case 'ORDER_CREATED': {
        orders.set(event.id, { ...event, filledQuantity: 0 });
        break;
      }
      case 'FILL': {
        if (!symbol) break;
        const quantity = Number(event.quantity ?? 0);
        const gross = Number(event.grossAmount ?? 0);
        const fees = Number(event.commission ?? 0) + Number(event.transactionTax ?? 0);
        if (event.side === 'BUY') {
          cash -= gross + fees;
          quantities.set(symbol, (quantities.get(symbol) ?? 0) + quantity);
          costBases.set(symbol, (costBases.get(symbol) ?? 0) + gross + fees);
        } else if (event.side === 'SELL') {
          const priorQuantity = quantities.get(symbol) ?? 0;
          const basis = costBases.get(symbol) ?? 0;
          const soldBasis = priorQuantity > 0 ? bps((basis * quantity) / priorQuantity) : 0;
          const net = gross - fees;
          cash += net;
          quantities.set(symbol, priorQuantity - quantity);
          costBases.set(symbol, basis - soldBasis);
          realizedPnl += net - soldBasis;
        }
        const orderId = typeof event.orderId === 'string' ? event.orderId : undefined;
        if (orderId && orders.has(orderId)) {
          const order = orders.get(orderId)!;
          const filledQuantity = Number(order.filledQuantity ?? 0) + quantity;
          orders.set(orderId, { ...order, filledQuantity, status: 'SIMULATED_FILLED' });
        }
        break;
      }
      case 'DIVIDEND': {
        const amount = Number(event.cashAmount ?? 0);
        cash += amount;
        dividendCash += amount;
        break;
      }
      case 'SPLIT': {
        if (!symbol) break;
        quantities.set(symbol, Number(event.quantityAfter ?? quantities.get(symbol) ?? 0));
        break;
      }
      case 'MARKET_MARK': {
        const prices = event.prices;
        if (prices && typeof prices === 'object') {
          for (const [ticker, price] of Object.entries(prices)) marks.set(ticker, Number(price));
        }
        const equity = cash + [...quantities.entries()].reduce(
          (total, [ticker, quantity]) => total + quantity * (marks.get(ticker) ?? 0),
          0,
        );
        peak = Math.max(peak, equity);
        daily.push({
          date: event.atKst.slice(0, 10),
          equity,
          cash,
          realizedPnl,
          returnBps: bps((equity / initialCapital - 1) * 10_000),
          drawdownBps: peak > 0 ? bps((equity / peak - 1) * 10_000) : 0,
        });
        break;
      }
      default:
        break;
    }
  }

  for (const [symbol, bar] of latestBarBySymbol) {
    if (!marks.has(symbol)) marks.set(symbol, bar.close);
  }
  const positions = [...quantities.entries()]
    .filter(([, quantity]) => quantity > 0)
    .map(([symbol, quantity]) => {
      const bar = latestBarBySymbol.get(symbol);
      const lastClose = marks.get(symbol) ?? bar?.close ?? 0;
      const marketValue = quantity * lastClose;
      const costBasis = costBases.get(symbol) ?? 0;
      return {
        symbol,
        displayName: bar?.displayName ?? symbol,
        quantity,
        lastClose,
        priceDate: bar?.date ?? '',
        marketValue,
        costBasis,
        unrealizedPnl: marketValue - costBasis,
      };
    });
  const equity = cash + positions.reduce((total, position) => total + position.marketValue, 0);
  return {
    cash,
    equity,
    returnBps: bps((equity / initialCapital - 1) * 10_000),
    realizedPnl,
    unrealizedPnl: positions.reduce((total, position) => total + position.unrealizedPnl, 0),
    dividendCash,
    positions,
    daily,
    events,
    orders: [...orders.values()].sort((left, right) => String(right.atKst).localeCompare(String(left.atKst))),
    latestVisibleDate: bars.reduce<string | null>((latest, bar) => !latest || bar.date > latest ? bar.date : latest, null),
  };
}

export function showcaseMetadata() {
  return scenario.showcasePortfolio;
}

export function backtestData() {
  return scenario.backtest;
}

export function sourceMetadata() {
  return scenario.source;
}

export function scenarioAssumptions() {
  return scenario.assumptions;
}

export function calendarMetadata() {
  return scenario.calendar;
}

export function availableBarFor(ticker: string, asOfDate: string): DemoBar | null {
  const candidates = scenario.bars.filter((bar) => bar.symbol === ticker && bar.date <= asOfDate);
  return candidates.length ? candidates[candidates.length - 1] : null;
}

export function barAt(ticker: string, date: string): DemoBar | null {
  return barsByTickerDate.get(`${ticker}:${date}`) ?? null;
}

const barsByTickerDate = new Map(scenario.bars.map((bar) => [`${bar.symbol}:${bar.date}`, bar]));

export function projectBacktest(asOfDate: string) {
  const data = scenario.backtest;
  const daily = data.daily.filter((row) => row.date <= asOfDate);
  const last = daily[daily.length - 1];
  const initial = data.initialCapital;
  let peak = initial;
  const points = daily.map((row) => {
    peak = Math.max(peak, row.equity);
    return {
      date: row.date,
      equity: row.equity,
      cash: row.cash,
      realizedPnl: row.equity - row.cash,
      returnBps: bps((row.equity / initial - 1) * 10_000),
      drawdownBps: peak > 0 ? bps((row.equity / peak - 1) * 10_000) : 0,
      actionCount: row.actionCount,
      openPositions: row.openPositions,
    };
  });
  const events = data.events.filter((event) => String(event.date) <= asOfDate);
  return {
    label: data.label,
    testRange: data.testRange,
    evaluatedThrough: last?.date ?? null,
    initialCapital: initial,
    finalEquity: last?.equity ?? initial,
    returnBps: last ? bps((last.equity / initial - 1) * 10_000) : 0,
    maxDrawdownBps: points.reduce((minimum, point) => Math.min(minimum, point.drawdownBps), 0),
    activeDays: daily.filter((row) => row.actionCount > 0).length,
    noActionDays: daily.filter((row) => row.actionCount === 0).length,
    observedDays: daily.length,
    daily: points,
    events,
    usesFutureData: false as const,
  };
}
