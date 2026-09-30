export type DemoSide = 'BUY' | 'SELL';
export type DemoMarketPhase =
  | 'PREOPEN'
  | 'INTRADAY'
  | 'CLOSE'
  | 'AFTER_HOURS'
  | 'NIGHT'
  | 'WEEKEND'
  | 'HOLIDAY'
  | 'CALENDAR_UNAVAILABLE';

export interface DemoOverlayNote {
  id: string;
  text: string;
  title?: string;
  tags?: string[];
  createdAt: string;
  sourceEventId?: string;
  version?: number;
}

export interface DemoOverlay {
  autoArmed: boolean;
  riskProfile: 'conservative' | 'balanced' | 'growth';
  dailyLossLimitPct: number;
  virtualEvents: Record<string, unknown>[];
  notes: DemoOverlayNote[];
  principleId?: string;
  principleTitle?: string;
  principlePresetId?: 'conservative' | 'balanced' | 'aggressive';
  principleVersion?: number;
  principleRules?: Record<string, unknown>[];
  principleHistory?: Record<string, unknown>[];
  automationPolicy?: Record<string, unknown>;
  personalKillSwitchActive?: boolean;
  personalKillSwitchChangedAt?: string;
  personalKillSwitchReasonClass?: string;
  globalKillSwitchActive?: boolean;
  globalKillSwitchChangedAt?: string;
  ragConsent?: boolean;
  decisions?: Record<string, unknown>[];
  journalEntries?: Record<string, unknown>[];
  journalSeedVersion?: string;
  deletedSeedJournalIds?: string[];
}

export interface DemoBar {
  symbol: string;
  displayName: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  adjustedClose: number;
  volume: number;
  dividendPerShare: number;
  splitRatio: number;
}

export interface DemoPosition {
  symbol: string;
  displayName: string;
  quantity: number;
  lastClose: number;
  priceDate: string;
  marketValue: number;
  costBasis: number;
  grossCostBasis: number;
  averageFillPriceKrw: number;
  unrealizedPnl: number;
}

export interface DemoDailyPoint {
  date: string;
  equity: number;
  cash: number;
  realizedPnl: number;
  returnBps: number;
  drawdownBps: number;
  actionCount?: number;
  openPositions?: number;
}

export interface DemoUsage {
  sessionCalls: number;
  sessionLimit: number;
  globalCalls: number;
  globalLimit: number;
  remainingSessionCalls: number;
  remainingGlobalCalls: number;
  estimatedSessionCostUsd: number;
  estimatedGlobalCostUsd: number;
  sessionInputTokens: number;
  sessionOutputTokens: number;
  globalInputTokens: number;
  globalOutputTokens: number;
  unknownOutcomes: number;
  maxDailyCostUsd: number;
  model: string;
  pricingSource: string;
  pricingReviewedAt: string;
  activeCalls: number;
}

export interface DemoState {
  source: {
    seedVersion: string;
    provider: string;
    sourceSha256: string;
    sourceRange: { start: string; end: string };
    latestVisibleDate: string | null;
    retrospectiveAvailable: boolean;
  };
  clock: {
    utc: string;
    kst: string;
    dateKst: string;
    timeKst: string;
    phase: DemoMarketPhase;
    phaseLabel: string;
    isTradingSession: boolean;
    sourceScenarioDate: string | null;
    calendarPolicyVersion: string;
  };
  showcase: {
    label: string;
    selectionRule: string;
    selectedSymbols: string[];
    initialCapital: number;
    cash: number;
    equity: number;
    returnBps: number;
    realizedPnl: number;
    unrealizedPnl: number;
    dividendCash: number;
    positions: DemoPosition[];
    daily: DemoDailyPoint[];
    events: Record<string, unknown>[];
    orders: Record<string, unknown>[];
    benchmark: { ticker: string; returnBps: number } | null;
  } | null;
  backtest: {
    label: string;
    testRange: { start: string; end: string };
    evaluatedThrough: string | null;
    initialCapital: number;
    finalEquity: number;
    returnBps: number;
    maxDrawdownBps: number;
    activeDays: number;
    noActionDays: number;
    observedDays: number;
    daily: DemoDailyPoint[];
    events: Record<string, unknown>[];
    usesFutureData: false;
  };
  bars: DemoBar[];
  overlay: DemoOverlay;
  usage: DemoUsage;
  assumptions: Record<string, unknown>;
  dataSource: Record<string, unknown>;
  backtestDefinition: Record<string, unknown>;
}

export interface DemoEvidence {
  id: string;
  title: string;
  url: string;
  publishedAt?: string;
  text: string;
  tags: string[];
}
