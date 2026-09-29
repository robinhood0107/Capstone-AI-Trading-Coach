import { createHash, randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { NextRequest, NextResponse } from 'next/server';
import { askVertex, AgentConfigurationError } from './vertex';
import { readAgentConfig, type DemoAgentConfig } from './config';
import { buildDemoState } from './app-state';
import { evidenceIndexVersion, retrieveEvidence } from './evidence';
import { availableBarFor, sourceMetadata } from './ledger';
import { applyOverlayAction, readSessionOverlay } from './overlay';
import { saveOverlay, usageSnapshot } from './store';
import { requireDemoSession, requireSameOrigin } from './http';
import type { DemoOverlay } from '../shared/contracts';

type JsonRecord = Record<string, unknown>;

const FALLBACK_AGENT_CONFIG: DemoAgentConfig = {
  perSessionDailyLimit: 5,
  globalDailyLimit: 50,
  maxConcurrent: 1,
  maxInputChars: 1_500,
  maxInputBytes: 24_576,
  maxContextChars: 4_000,
  maxOutputTokens: 512,
  perSessionRequestsPerMinute: 3,
  globalRequestsPerMinute: 15,
  timeoutMs: 45_000,
  model: 'gemini-2.5-flash',
  location: 'us-central1',
  projectId: '',
  inputUsdPerMillionTokens: 0.30,
  outputUsdPerMillionTokens: 2.50,
  pricingReviewedAt: '2026-09-30',
  pricingSource: 'Google Cloud Vertex AI pricing',
};

function hashHex(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

function requestId(request: NextRequest): string {
  const supplied = request.headers.get('X-Request-Id');
  return supplied && /^req_[A-Za-z0-9_-]{8,96}$/.test(supplied) ? supplied : `req_${randomUUID().replaceAll('-', '')}`;
}

function envelope(data: unknown, id: string, status = 200) {
  return NextResponse.json({ success: true, requestId: id, data, warnings: [], error: null }, { status, headers: { 'Cache-Control': 'no-store' } });
}

function envelopeError(code: string, message: string, id: string, status: number) {
  return NextResponse.json({ success: false, requestId: id, data: null, warnings: [], error: { code, message } }, { status, headers: { 'Cache-Control': 'no-store' } });
}

function bareError(code: string, message: string, id: string, status: number) {
  return NextResponse.json({ code, message, requestId: id }, { status, headers: { 'Cache-Control': 'no-store' } });
}

function fullSymbol(value: string): string {
  return value.replace(/\.(KS|KQ)$/, '');
}

function demoSymbol(value: string): string {
  if (value.startsWith('^')) return value;
  return value.includes('.') ? value : `${value}.KS`;
}

function accountIdFor(sessionHash: string): string {
  return `acct_${sessionHash.slice(0, 32)}`;
}

function policyFor(overlay: DemoOverlay, sessionHash: string, now: Date) {
  const saved = overlay.automationPolicy ?? {};
  const presetId = overlay.riskProfile === 'growth' ? 'aggressive' : overlay.riskProfile;
  const identity = `auto_pol_${sessionHash.slice(0, 32)}`;
  return {
    contractId: 'automation-policy.v2',
    policyId: identity,
    version: Number(saved.version ?? 1),
    presetId: String(saved.presetId ?? presetId),
    capitalLimitKrw: Number(saved.capitalLimitKrw ?? 3_000_000),
    stopLossBps: Number(saved.stopLossBps ?? 500),
    takeProfitBps: Number(saved.takeProfitBps ?? 1_000),
    riskPerTradeBps: Number(saved.riskPerTradeBps ?? 100),
    maxOpenPositions: Number(saved.maxOpenPositions ?? 5),
    maxNewOrdersPerSession: Number(saved.maxNewOrdersPerSession ?? 1),
    evaluationTimeKst: String(saved.evaluationTimeKst ?? '09:30'),
    buyCutoffTimeKst: String(saved.buyCutoffTimeKst ?? '09:40'),
    cancelTimeKst: String(saved.cancelTimeKst ?? '15:20'),
    atrPeriod: Number(saved.atrPeriod ?? 14),
    atrMultiplierMilli: Number(saved.atrMultiplierMilli ?? 2_500),
    maxHoldingSessions: Number(saved.maxHoldingSessions ?? 60),
    modelSellEnabled: saved.modelSellEnabled !== false,
    createdAt: now.toISOString(),
    updatedAt: String(saved.updatedAt ?? now.toISOString()),
  };
}

function policyV2(policy: ReturnType<typeof policyFor>) {
  return { ...policy, contractId: 'automation-policy.v1' };
}

function capitalPolicyFor(overlay: DemoOverlay) {
  const saved = overlay.automationPolicy ?? {};
  const version = Number(saved.capitalPolicyVersion ?? 0);
  if (version < 1) return null;
  return {
    contractId: 'automation-capital-policy.v1',
    version,
    reinvestRealizedPnl: Boolean(saved.reinvestRealizedPnl),
    cashBufferBps: 100,
    rebalanceDeviationBps: 200,
    minimumAdjustmentKrw: 10_000,
    maxOrdersPerSession: 1 as const,
    effectiveFromSession: String(saved.capitalPolicyEffectiveFromSession ?? ''),
    transitionStartedAt: String(saved.capitalPolicyTransitionStartedAt ?? ''),
  };
}

function positionRows(state: ReturnType<typeof buildDemoState>, overlay: DemoOverlay, sessionHash: string, now: Date) {
  const policy = policyFor(overlay, sessionHash, now);
  const bars = state.bars;
  return (state.showcase?.positions ?? []).map((position, index) => {
    const bar = bars.find((candidate) => candidate.symbol === position.symbol && candidate.date === position.priceDate);
    const entry = (state.showcase?.events ?? []).find((event) => event.type === 'FILL' && event.symbol === position.symbol && event.side === 'BUY') as JsonRecord | undefined;
    const entryPrice = Number(entry?.price ?? bar?.close ?? position.lastClose);
    return {
      v2: {
        contractId: 'automation-position.v2',
        positionId: `paper_pos_${sessionHash.slice(0, 10)}_${index + 1}`,
        accountId: accountIdFor(sessionHash),
        symbol: fullSymbol(position.symbol),
        quantity: position.quantity,
        entryAverageFillPriceKrw: entryPrice,
        entrySession: String(entry?.sourceDate ?? position.priceDate),
        expirySession: null,
        policyId: policy.policyId,
        policyVersion: policy.version,
        stopLossBps: policy.stopLossBps,
        takeProfitBps: policy.takeProfitBps,
        status: 'OPEN',
        exitReason: null,
        exitAverageFillPriceKrw: null,
        realizedPnlKrw: null,
        botOwned: true,
        shortAllowed: false,
        createdAt: String(entry?.atKst ?? `${position.priceDate}T09:01:00+09:00`),
        closedAt: null,
      },
      v3: {
        contractId: 'automation-position.v3',
        positionId: `paper_pos_${sessionHash.slice(0, 10)}_${index + 1}`,
        accountId: accountIdFor(sessionHash),
        symbol: fullSymbol(position.symbol),
        quantity: position.quantity,
        entryAverageFillPriceKrw: entryPrice,
        entrySession: String(entry?.sourceDate ?? position.priceDate),
        expirySession: null,
        policyId: policy.policyId,
        policyVersion: policy.version,
        stopLossBps: policy.stopLossBps,
        takeProfitBps: policy.takeProfitBps,
        status: 'OPEN',
        exitReason: null,
        peakPriceKrw: Math.max(entryPrice, position.lastClose),
        trailingStopKrw: null,
        atrPeriod: policy.atrPeriod,
        atrMultiplierMilli: policy.atrMultiplierMilli,
        atrAsOfSession: position.priceDate,
        maxHoldingSessions: policy.maxHoldingSessions,
        modelSellEnabled: policy.modelSellEnabled,
        botOwned: true,
        shortAllowed: false,
        createdAt: String(entry?.atKst ?? `${position.priceDate}T09:01:00+09:00`),
        closedAt: null,
      },
    };
  });
}

function statusV2(overlay: DemoOverlay, state: ReturnType<typeof buildDemoState>, sessionHash: string, now: Date) {
  const policy = policyFor(overlay, sessionHash, now);
  const positions = positionRows(state, overlay, sessionHash, now);
  const armed = overlay.autoArmed;
  return {
    contractId: 'automation-status.v2',
    controlState: armed ? 'ARMED' : 'DISARMED',
    projectionState: armed ? 'ARMED' : 'DISARMED',
    controlVersion: policy.version,
    brokerageMode: 'KIS_MOCK',
    accountId: accountIdFor(sessionHash),
    policy: policyV2(policy),
    killSwitchActive: false,
    certificationStatus: 'VALID',
    openPositionCount: positions.length,
    unresolvedReconciliation: false,
    canArm: true,
    blockers: [],
  };
}

function statusV3(overlay: DemoOverlay, state: ReturnType<typeof buildDemoState>, sessionHash: string, now: Date) {
  const status = statusV2(overlay, state, sessionHash, now);
  const policy = policyFor(overlay, sessionHash, now);
  const dateKst = state.clock.dateKst;
  const armed = overlay.autoArmed;
  return {
    ...status,
    contractId: 'automation-status.v3',
    projectionState: armed ? (state.clock.isTradingSession ? 'RUNNING' : 'ARMED') : 'DISARMED',
    policy: { ...policy, contractId: 'automation-policy.v2' },
    blockers: [],
    aiJudgementEnabled: true,
    thinkingLevel: 'low',
    marketHistoryStatus: 'READY',
    legacyOpenPositionCount: 0,
    ownerConnectionReady: false,
    orderPathVerified: false,
    orderFailureCode: null,
    unlinkedOpenPositionCount: 0,
    unresolvedUnlinkedOrderCount: 0,
    unresolvedUnlinkedRunCount: 0,
    quarantinedPositionCount: 0,
    historicalPaperOpenPositionCount: 0,
    historicalPaperClosedPositionCount: 0,
    historicalPaperRunCount: 0,
    appliedPolicyVersion: policy.version,
    nextRunAt: armed ? `${dateKst}T09:30:00+09:00` : null,
  };
}

function instrumentCatalog(state: ReturnType<typeof buildDemoState>) {
  const items = new Map<string, { symbol: string; nameKo: string; logoText: string; brandColor: string; market: string }>();
  for (const bar of state.bars) {
    if (!/^\d{6}\.K[QS]$/.test(bar.symbol)) continue;
    const symbol = fullSymbol(bar.symbol);
    items.set(symbol, {
      symbol,
      nameKo: bar.displayName,
      logoText: bar.displayName.slice(0, 2),
      brandColor: '#284d75',
      market: bar.displayName.startsWith('KODEX') ? 'ETF' : 'KOSPI',
    });
  }
  return { items: [...items.values()] };
}

function accountBalance(state: ReturnType<typeof buildDemoState>, overlay: DemoOverlay, sessionHash: string, now: Date) {
  const positions = state.showcase?.positions ?? [];
  const cash = state.showcase?.cash ?? state.backtest.initialCapital;
  const equity = state.showcase?.equity ?? state.backtest.finalEquity;
  const lastVisibleDate = state.showcase?.daily.at(-1)?.date ?? state.source.latestVisibleDate;
  const observedAt = lastVisibleDate
    ? new Date(`${lastVisibleDate}T15:30:00+09:00`).toISOString()
    : now.toISOString();
  return {
    accountId: accountIdFor(sessionHash),
    brokerageMode: 'KIS_MOCK',
    cashKrw: cash,
    portfolioEquityKrw: equity,
    marginRequirementKrw: 0,
    positions: positions.map((position) => ({
      symbol: fullSymbol(position.symbol),
      quantity: position.quantity,
      marketValueKrw: position.marketValue,
      isGoldEtfEtn: position.displayName.toLocaleLowerCase('ko-KR').includes('gold'),
    })),
    observedAt,
    sourceVersion: 'mars-demo-2026-09-29.1',
  };
}

function riskPortfolio(state: ReturnType<typeof buildDemoState>, now: Date) {
  return {
    asOf: now.toISOString(),
    portfolioValue: state.showcase?.equity ?? state.backtest.finalEquity,
    dailyPnlRate: null,
    mdd: state.backtest.maxDrawdownBps / 10_000,
    var95: null,
    cvar95: null,
    realizedVolatility20d: null,
    annualizedVolatility20d: null,
    hmmRegime: null,
    hmmRegimeProbability: null,
    killSwitchActive: false,
    dataFreshness: { priceFresh: true, signalFresh: null, ragFresh: null },
  };
}

function runRows(state: ReturnType<typeof buildDemoState>, sessionHash: string, overlay: DemoOverlay) {
  const policy = policyFor(overlay, sessionHash, new Date());
  const events = [...(state.showcase?.events ?? []), ...overlay.virtualEvents] as JsonRecord[];
  const orderEvents = events.filter((event) => event.type === 'ORDER_CREATED');
  return orderEvents.map((order, index) => {
    const fill = events.find((event) => event.type === 'FILL' && (event.orderId === order.id || event.id === `${String(order.id).replace('order', 'fill')}`));
    const eventAt = String(order.atKst ?? new Date().toISOString());
    const sessionDate = String(order.sourceDate ?? eventAt.slice(0, 10));
    const symbol = String(order.symbol ?? '000660.KS');
    const quantity = Number(order.quantity ?? 0);
    const runId = `demo_run_${hashHex(`${sessionHash}:${String(order.id)}`).slice(0, 24)}`;
    const base = {
      runId,
      sessionDate,
      state: fill ? 'COMPLETED' : 'SKIPPED_NO_ACTION',
      brokerageMode: 'KIS_MOCK',
      policyId: policy.policyId,
      policyVersion: policy.version,
      selectedSymbol: fullSymbol(symbol),
      selectedSide: order.side === 'SELL' ? 'SELL' : 'BUY',
      orderQuantity: quantity,
      filledQuantity: fill ? Number(fill.quantity ?? quantity) : 0,
      leavesQuantity: 0,
      limitPriceKrw: fill ? Number(fill.price ?? 0) : null,
      estimatedAmountKrw: fill ? Number(fill.grossAmount ?? Number(fill.price ?? 0) * quantity) : null,
      exitReason: null,
      physicalSubmitCount: 0,
      providerCalls: 0,
      startedAt: eventAt,
      updatedAt: String(fill?.atKst ?? eventAt),
      evidenceCount: 0,
      evidenceSetSha256: null,
      aiSettingsSha256: null,
      judgeCallCount: 0,
      groundingQueryCount: 0,
      screeningProviderCallCount: 0,
    };
    return { ...base, contractId: 'automation-run.v3', index };
  });
}

const RULE_SHAPES = [
  ['max_position_per_asset', 'POSITION_LIMIT', 'asset_weight', '<='],
  ['max_gold_etf_etn_weight', 'POSITION_LIMIT', 'gold_etf_etn_weight', '<='],
  ['max_single_order_amount', 'ORDER_SIZE', 'order_amount_krw', '<='],
  ['daily_loss_guard', 'LOSS_LIMIT', 'daily_loss_rate', '>='],
  ['mdd_guard', 'DRAWDOWN_LIMIT', 'mdd', '>='],
  ['max_daily_orders', 'TRADING_FREQUENCY', 'daily_order_count', '<='],
  ['negative_news_guard', 'NEWS_GUARD', 'negative_news_score', '<='],
  ['disclosure_risk_guard', 'DISCLOSURE_GUARD', 'disclosure_risk_score', '<='],
] as const;

function defaultRules(preset: 'conservative' | 'balanced' | 'aggressive') {
  const thresholds = preset === 'conservative'
    ? [0.15, 0.2, 300_000, -0.02, -0.1, 2, 0.5, 0.5]
    : preset === 'aggressive'
      ? [0.3, 0.4, 1_000_000, -0.05, -0.25, 5, 0.85, 0.85]
      : [0.2, 0.3, 500_000, -0.03, -0.15, 3, 0.7, 0.7];
  return RULE_SHAPES.map(([ruleId, ruleType, metric, operator], index) => {
    const enabled = index < 6;
    return {
      ruleId,
      ruleType,
      metric,
      operator,
      threshold: thresholds[index],
      severity: !enabled ? 'ALLOW' : index === 5 && preset !== 'conservative' ? 'WARN' : 'BLOCK',
      enabled,
      evidenceRequirement: enabled ? 'REQUIRED' : 'OPTIONAL',
    };
  });
}

function presetFromRules(value: unknown, fallback: 'conservative' | 'balanced' | 'aggressive') {
  if (!Array.isArray(value)) return fallback;
  const thresholds = new Map(value.map((item) => {
    const rule = item as JsonRecord;
    return [String(rule.ruleId), Number(rule.threshold)];
  }));
  const presets = ['conservative', 'balanced', 'aggressive'] as const;
  return presets.find((preset) => {
    const defaults = defaultRules(preset) as JsonRecord[];
    return defaults.every((rule) => thresholds.get(String(rule.ruleId)) === Number(rule.threshold));
  }) ?? fallback;
}

function ensurePrinciple(overlay: DemoOverlay, sessionHash: string, now: Date) {
  const preset = overlay.principlePresetId ?? (overlay.riskProfile === 'growth' ? 'aggressive' : overlay.riskProfile);
  const rules = overlay.principleRules ?? defaultRules(preset);
  return {
    principleId: overlay.principleId ?? `prc_${sessionHash.slice(0, 32)}`,
    title: overlay.principleTitle ?? `내 ${preset === 'aggressive' ? '공격형' : preset === 'conservative' ? '보수형' : '균형형'} 원칙`,
    presetId: preset,
    mode: 'GUIDE',
    status: 'ACTIVE',
    version: overlay.principleVersion ?? 1,
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    rules,
  };
}

function timestamps(data: ReturnType<typeof buildDemoState>) {
  return data.showcase?.daily.map((point) => ({ at: point.date, value: point.equity / data.showcase!.initialCapital })) ?? [];
}

function dashboardEnvelope<T>(view: T, now: Date) {
  return {
    viewState: 'READY',
    asOf: now.toISOString(),
    freshUntil: new Date(now.getTime() + 15 * 60_000).toISOString(),
    evidenceMode: 'REAL_ARTIFACT',
    performanceClaimAllowed: false,
    view,
  };
}

function defaultPresets() {
  return {
    disclaimer: {
      ko: '원칙은 위험 한도를 검토하는 기준입니다. 어떤 기준도 수익을 보장하지 않습니다.',
      en: 'Principles are risk limits and do not guarantee returns.',
    },
    items: [
      { presetId: 'conservative', nameKo: '보수형', nameEn: 'Conservative', descriptionKo: '손실 한도와 주문 횟수를 좁게 설정합니다.', descriptionEn: 'Tighter loss and order limits.', mode: 'GUIDE', order: 1, defaultRules: defaultRules('conservative') },
      { presetId: 'balanced', nameKo: '균형형', nameEn: 'Balanced', descriptionKo: '기회와 위험을 균형 있게 설정합니다.', descriptionEn: 'Balanced opportunity and risk limits.', mode: 'GUIDE', order: 2, defaultRules: defaultRules('balanced') },
      { presetId: 'aggressive', nameKo: '공격형', nameEn: 'Aggressive', descriptionKo: '상대적으로 넓은 한도를 설정합니다.', descriptionEn: 'Wider exposure limits.', mode: 'GUIDE', order: 3, defaultRules: defaultRules('aggressive') },
    ],
  };
}

function recentRiskResults(overlay: DemoOverlay) {
  return [...(overlay.decisions ?? [])]
    .map((item) => item as JsonRecord)
    .sort((left, right) => String(right.createdAt ?? '').localeCompare(String(left.createdAt ?? '')))
    .map((decision) => ({
      decisionId: String(decision.decisionId),
      action: String(decision.action),
      symbol: String((decision.orderIntent as JsonRecord | undefined)?.symbol ?? '000660'),
      asOf: String(decision.createdAt),
      validUntil: String(decision.validUntil),
    }));
}

function dashboardBacktest(data: ReturnType<typeof buildDemoState>, now: Date) {
  const runId = `demo_${hashHex(data.dataSource.sourceSha256).slice(0, 20)}`;
  const daily = data.backtest.daily.map((point) => ({ at: point.date, value: point.equity / data.backtest.initialCapital }));
  const guideReturn = data.backtest.returnBps / 10_000;
  const metric = {
    cagr: null,
    mdd: data.backtest.maxDrawdownBps / 10_000,
    sharpe: null,
    sortino: null,
    var95: null,
    cvar95: null,
  };
  return dashboardEnvelope({
    runId,
    fixtureClass: 'REAL_ARTIFACT',
    strategies: [
      { strategy: 'Baseline', metrics: metric, curve: daily },
      { strategy: 'Guide', metrics: { ...metric, cagr: guideReturn }, curve: daily },
      { strategy: 'Strict', metrics: metric, curve: [] },
    ],
    heatmap: [...new Set(data.backtest.daily.map((point) => point.date.slice(0, 7)))].map((month) => ({ month, return: 0 })),
    metricCards: [
      { metric: 'Guide.netReturn', value: guideReturn },
      { metric: 'Baseline.netReturn', value: null },
      { metric: 'Strict.netReturn', value: null },
    ],
    projectionHash: hashHex(JSON.stringify(data.backtest.daily)),
  }, now);
}

function dashboardModelEvaluation(data: ReturnType<typeof buildDemoState>, now: Date) {
  const runId = `demo_model_${hashHex(data.dataSource.sourceSha256).slice(0, 18)}`;
  const unavailable = { cagr: null, mdd: null, sharpe: null, sortino: null, var95: null, cvar95: null };
  return dashboardEnvelope({
    runId,
    models: [
      { modelId: 'BASELINE', status: 'ABSTAIN', metrics: unavailable },
      { modelId: 'LSTM', status: 'ABSTAIN', metrics: unavailable },
      { modelId: 'LIGHTGBM', status: 'ABSTAIN', metrics: unavailable },
    ],
    timeline: timestamps(data),
    sourceRunIds: [],
  }, now);
}

function performanceReport(data: ReturnType<typeof buildDemoState>, overlay: DemoOverlay, sessionHash: string, now: Date) {
  const source = sourceMetadata();
  const principle = ensurePrinciple(overlay, sessionHash, now);
  return {
    report: {
      contractId: 'owner-performance-report.v1',
      reportId: `rpt_${hashHex(`${source.sourceSha256}:${principle.version}`).slice(0, 24)}`,
      reportVersion: 1,
      supersedesReportId: null,
      correctionOfReportId: null,
      generatedAt: now.toISOString(),
      sourceStart: source.scenarioRange.start,
      sourceEnd: data.backtest.evaluatedThrough ?? source.scenarioRange.start,
      sourceGenerationSha256: source.sourceSha256,
      modelSha256: hashHex('mars-demo-fixed-sma20-sma50'),
      principleVersionId: `prv_${principle.principleId.slice(4)}_${principle.version}`,
      principleVersion: principle.version,
      costBps: 21.5,
      modelAdoption: {
        state: 'RESEARCH_EVALUATED',
        candidateId: null,
        currentModel: 'RULE_BASELINE',
        predictionAccepted: false,
        performanceAccepted: false,
        automaticActivation: false,
        blockers: ['INSUFFICIENT_EVALUATION_HISTORY'],
      },
      sections: {
        recalculatedBacktest: {
          status: 'RECALCULATED',
          baselineNetReturn: null,
          guideNetReturn: data.backtest.returnBps / 10_000,
          strictNetReturn: null,
        },
        fixedDailyForecast: { status: 'NOT_AVAILABLE', totalCount: 0, realizedCount: 0, pendingCount: 0, mae: null, rmse: null, bias: null },
        actualTrading: {
          status: data.showcase?.realizedPnl ? 'REALIZED' : 'NO_REALIZED_TRADES',
          closedPositionCount: data.showcase?.realizedPnl ? 1 : 0,
          openPositionCount: data.showcase?.positions.length ?? 0,
          realizedPnlKrw: data.showcase?.realizedPnl ?? 0,
          unrealizedStatus: data.showcase?.positions.length ? 'OPEN' : 'NONE',
        },
      },
    },
    lastRefreshStatus: 'SUCCESS',
    lastFailureCode: null,
    lastFailureAt: null,
  };
}

function journalRows(overlay: DemoOverlay) {
  return (overlay.journalEntries ?? []).map((entry) => entry as JsonRecord);
}

function asJournal(entry: JsonRecord, sessionHash: string, now: Date) {
  return {
    contractId: 'journal.v1',
    journalId: String(entry.journalId ?? `jrn_${hashHex(`${sessionHash}:${randomUUID()}`).slice(0, 32)}`),
    ownerScope: 'OWNER',
    title: String(entry.title ?? '학습 메모'),
    content: String(entry.content ?? ''),
    tags: Array.isArray(entry.tags) ? entry.tags : [],
    links: typeof entry.links === 'object' && entry.links !== null
      ? entry.links
      : { decisionId: null, backtestRunId: null, ragAnswerId: null, orderId: null, automationRunId: null },
    version: Number(entry.version ?? 1),
    createdAt: String(entry.createdAt ?? now.toISOString()),
    updatedAt: String(entry.updatedAt ?? now.toISOString()),
    deletedAt: null,
  };
}

function publicEvidence() {
  return retrieveEvidence('주식 위험 시장 투자 배당 수수료', 20).map((item) => ({
    sourceId: item.id,
    title: item.title,
    institution: item.title.split(' — ')[0] ?? 'Public source',
    topic: item.tags[0] ?? 'finance',
    attribution: item.text,
    canonicalUrl: item.url,
    lastCheckedAt: item.publishedAt ?? null,
  }));
}

function principalList(overlay: DemoOverlay, sessionHash: string, now: Date) {
  const current = ensurePrinciple(overlay, sessionHash, now);
  if (!overlay.principleRules) {
    overlay.principleId = current.principleId;
    overlay.principleTitle = current.title;
    overlay.principlePresetId = current.presetId;
    overlay.principleVersion = current.version;
    overlay.principleRules = current.rules;
    overlay.principleHistory = [{ ...current, changedFields: [] } as unknown as JsonRecord];
    saveOverlay(sessionHash, overlay, now);
  }
  const summary = {
    principleId: current.principleId,
    title: current.title,
    presetId: current.presetId,
    mode: current.mode,
    status: current.status,
    version: current.version,
    createdAt: current.createdAt,
    updatedAt: current.updatedAt,
  };
  return { current, list: { items: [summary], nextCursor: null } };
}

function cashAndPrice(state: ReturnType<typeof buildDemoState>, symbol: string) {
  const bar = availableBarFor(demoSymbol(symbol), state.clock.dateKst);
  return bar ? bar.close : 0;
}

function ordersFromEvents(state: ReturnType<typeof buildDemoState>, overlay: DemoOverlay) {
  return [...(state.showcase?.events ?? []), ...overlay.virtualEvents].filter((event) => event.type === 'ORDER_CREATED') as JsonRecord[];
}

function fillsFromEvents(state: ReturnType<typeof buildDemoState>, overlay: DemoOverlay) {
  return [...(state.showcase?.events ?? []), ...overlay.virtualEvents].filter((event) => event.type === 'FILL') as JsonRecord[];
}

function orderDetail(orderId: string, state: ReturnType<typeof buildDemoState>, overlay: DemoOverlay, sessionHash: string) {
  const order = ordersFromEvents(state, overlay).find((event) => String(event.id) === orderId);
  if (!order) return null;
  return {
    orderId,
    accountId: accountIdFor(sessionHash),
    decisionId: String(order.decisionId ?? order.id),
    brokerageMode: 'KIS_MOCK',
    status: 'ACCEPTED',
    submittedAt: String(order.atKst ?? new Date().toISOString()),
  };
}

function fullPositionFills(state: ReturnType<typeof buildDemoState>, overlay: DemoOverlay) {
  return fillsFromEvents(state, overlay).map((event) => {
    const amount = Number(event.grossAmount ?? Number(event.price ?? 0) * Number(event.quantity ?? 0));
    return {
      orderId: String(event.orderId ?? event.id),
      brokerageMode: 'KIS_MOCK',
      execRefHash: hashHex(String(event.id)),
      symbol: fullSymbol(String(event.symbol ?? '')),
      side: event.side === 'SELL' ? 'SELL' : 'BUY',
      fillQuantity: Number(event.quantity ?? 0),
      fillPriceKrw: Number(event.price ?? 0),
      fillAmountKrw: amount,
      filledAt: String(event.atKst ?? new Date().toISOString()),
    };
  });
}

function decisionForOrder(orderIntent: JsonRecord, overlay: DemoOverlay, sessionHash: string, state: ReturnType<typeof buildDemoState>, now: Date) {
  const principle = ensurePrinciple(overlay, sessionHash, now);
  const symbol = String(orderIntent.symbol ?? '');
  const quantity = Number(orderIntent.quantity ?? 0);
  const price = Number(orderIntent.estimatedPrice ?? 0);
  const amount = Number(orderIntent.estimatedAmount ?? quantity * price);
  const maxOrder = (principle.rules as JsonRecord[]).find((rule) => rule.ruleId === 'max_single_order_amount');
  const maxLoss = (principle.rules as JsonRecord[]).find((rule) => rule.ruleId === 'daily_loss_guard');
  const ruleLimit = Number(maxOrder?.threshold ?? 500_000);
  const cash = state.showcase?.cash ?? state.backtest.finalEquity;
  const currentPrice = cashAndPrice(state, symbol);
  const availableSymbol = state.bars.some((bar) => fullSymbol(bar.symbol) === symbol);
  const riskItems: JsonRecord[] = [
    { metric: 'order_amount_krw', value: amount, severity: amount > ruleLimit ? 'BLOCK' : 'ALLOW', source: 'visitor_principle' },
    { metric: 'daily_loss_rate', value: state.backtest.returnBps / 10_000, severity: 'ALLOW', source: 'fixed_backtest' },
  ];
  const violations = amount > ruleLimit
    ? [{ ruleId: 'max_single_order_amount', message: `주문 한도 ${ruleLimit.toLocaleString('ko-KR')}원을 넘었습니다.`, metricValue: amount, threshold: ruleLimit, severity: 'BLOCK' }]
    : [];
  const issues = !availableSymbol || currentPrice <= 0
    ? [{ code: 'PRICE_UNAVAILABLE', message: '이 종목의 평가 가격을 확인할 수 없습니다.', source: 'demo_market_fixture' }]
    : amount > cash && orderIntent.side === 'BUY'
      ? [{ code: 'INSUFFICIENT_CASH', message: '계좌 현금이 주문 금액보다 적습니다.', source: 'demo_ledger' }]
      : [];
  const action = violations.length || issues.length ? 'BLOCK' : maxLoss?.severity === 'WARN' ? 'WARN' : 'ALLOW';
  const decisionId = `dec_${randomUUID().replaceAll('-', '')}`;
  const validUntil = new Date(now.getTime() + 60_000).toISOString();
  const riskDecision = {
    decisionId,
    evaluationId: `eval_${randomUUID().replaceAll('-', '')}`,
    decision: action,
    canSubmitOrder: action === 'ALLOW' || action === 'WARN',
    mode: principle.mode,
    portfolioSource: 'KIS_MOCK',
    principleVersion: principle.version,
    principleVersionId: `prv_${principle.principleId.slice(4)}_${principle.version}`,
    catalogVersion: 1,
    readinessPolicyVersion: 'mars-demo-v2',
    schemaVersion: 'decision-projection.v1',
    semanticInputHash: hashHex(JSON.stringify(orderIntent)),
    snapshotArtifactHash: hashHex(`${state.dataSource.sourceSha256}:${state.clock.dateKst}`),
    validUntil,
    violations,
    issues,
    warnings: [],
    abstentions: [],
    riskItems,
  };
  const decision = {
    decisionId,
    createdAt: now.toISOString(),
    enforcementAction: action,
    mode: principle.mode,
    portfolioSource: 'KIS_MOCK',
    principleId: principle.principleId,
    principleVersion: principle.version,
    principleVersionId: riskDecision.principleVersionId,
    validUntil,
    riskDecision,
    orderIntent,
  };
  overlay.decisions = [...(overlay.decisions ?? []), decision as unknown as JsonRecord].slice(-50);
  saveOverlay(sessionHash, overlay, now);
  return decision;
}

function dashboardRiskResult(decision: JsonRecord) {
  const riskDecision = decision.riskDecision as JsonRecord;
  const action = String(riskDecision.decision ?? 'HOLD');
  return {
    decisionId: String(decision.decisionId),
    action,
    reasons: [...(riskDecision.violations as JsonRecord[] ?? []).map((item) => String(item.message)), ...(riskDecision.issues as JsonRecord[] ?? []).map((item) => String(item.message))],
    principles: [...new Set((riskDecision.violations as JsonRecord[] ?? []).map((item) => String(item.ruleId)))],
    riskItems: (riskDecision.riskItems as JsonRecord[] ?? []).map((item) => ({ code: String(item.metric), severity: String(item.severity), summary: `${String(item.metric)} · ${String(item.value)}` })),
  };
}

function decisionInputs(decision: JsonRecord) {
  const riskDecision = decision.riskDecision as JsonRecord;
  return {
    decisionId: String(decision.decisionId),
    items: (riskDecision.riskItems as JsonRecord[] ?? []).map((item) => ({
      metric: String(item.metric),
      value: typeof item.value === 'number' ? item.value : null,
      unit: String(item.metric).endsWith('_krw') ? 'KRW' : null,
      availability: 'AVAILABLE',
      observedAt: String(decision.createdAt),
    })),
  };
}

function fullJournalPage(overlay: DemoOverlay, sessionHash: string, now: Date) {
  return { items: journalRows(overlay).map((entry) => asJournal(entry, sessionHash, now)), nextCursor: null };
}

function ragCorpusStatus(sessionHash: string, config: DemoAgentConfig, now: Date, vertexServiceConfigured: boolean) {
  const usage = usageSnapshot(sessionHash, config, now);
  return {
    state: 'FULL_READY',
    publicCorpusVersion: evidenceIndexVersion(),
    privateOverlayState: 'ABSENT',
    progressPercent: 100,
    failureCode: null,
    generationDailyCap: null,
    generationUsedToday: null,
    generationRemaining: null,
    strongLlmProvider: 'vertex',
    strongLlmFallbackProvider: null,
    strongLlmModelId: config.model,
    strongLlmFallbackModelId: null,
    strongLlmBaseUrl: null,
    strongLlmFallbackBaseUrl: null,
    strongLlmAnswerLanguage: 'ko',
    strongLlmDailyGenerateCallCap: null,
    strongLlmKeyLast4: null,
    strongLlmFallbackKeyLast4: null,
    agentUsage: {
      configured: vertexServiceConfigured,
      sessionCalls: usage.sessionCalls,
      sessionLimit: usage.sessionLimit,
      globalCalls: usage.globalCalls,
      globalLimit: usage.globalLimit,
      sessionInputTokens: usage.sessionInputTokens,
      sessionOutputTokens: usage.sessionOutputTokens,
      globalInputTokens: usage.globalInputTokens,
      globalOutputTokens: usage.globalOutputTokens,
      estimatedSessionCostUsd: usage.estimatedSessionCostUsd,
      estimatedGlobalCostUsd: usage.estimatedGlobalCostUsd,
      maxDailyCostUsd: usage.maxDailyCostUsd,
      activeCalls: usage.activeCalls,
      unknownOutcomes: usage.unknownOutcomes,
    },
  };
}

function withRagConsent(overlay: DemoOverlay) {
  const effective = overlay.ragConsent === true;
  return {
    contractId: 's4-rag-v2-effective-consent-v1',
    schemaVersion: 1,
    consentEventId: effective ? `rce_${hashHex('consent').slice(0, 12)}` : '',
    effective,
    policyDigest: hashHex('Questions are sent to Google Vertex AI for answer generation. No account, balance, or order data is included.'),
    processorSetDigest: hashHex('GOOGLE_VERTEX_AI'),
    state: effective ? 'GRANTED' : 'NOT_GRANTED',
    disclosureText: '질문은 답변 생성을 위해 Google Vertex AI로 전송됩니다. 계좌·잔고·주문 데이터는 전송하지 않습니다.',
    policyText: '질문 원문은 사용량 원장에 저장하지 않습니다. 동의는 언제든 철회할 수 있습니다.',
    processorNames: 'Google Vertex AI',
  };
}

export async function dispatchFullUiApi(request: NextRequest, routeParts: string[]) {
  const id = requestId(request);
  const originError = request.method === 'GET' ? null : requireSameOrigin(request);
  if (originError) return originError;
  const identity = requireDemoSession(request);
  if (!identity) {
    return routeParts[0] === 'v2'
      ? bareError('UNAUTHORIZED', '로그인이 필요합니다.', id, 401)
      : envelopeError('UNAUTHORIZED', '로그인이 필요합니다.', id, 401);
  }
  let body: JsonRecord = {};
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    try {
      const value = await request.json();
      if (typeof value === 'object' && value !== null && !Array.isArray(value)) body = value as JsonRecord;
    } catch {
      return routeParts[0] === 'v2'
        ? bareError('VALIDATION_ERROR', '요청 형식이 올바르지 않습니다.', id, 400)
        : envelopeError('VALIDATION_ERROR', '요청 형식이 올바르지 않습니다.', id, 400);
    }
  }
  const now = new Date();
  const sessionHash = identity.hash;
  let config = FALLBACK_AGENT_CONFIG;
  let agentConfigAvailable = true;
  try {
    config = readAgentConfig();
  } catch {
    agentConfigAvailable = false;
  }
  const serviceAccountPath = process.env.MARS_DEMO_VERTEX_SERVICE_ACCOUNT_FILE;
  const vertexServiceConfigured = Boolean(agentConfigAvailable && serviceAccountPath && existsSync(serviceAccountPath));
  const overlay = readSessionOverlay(sessionHash);
  const state = buildDemoState(sessionHash, overlay, config, now);
  const pathname = `/${routeParts.join('/')}`;
  const method = request.method.toUpperCase();
  const accountId = accountIdFor(sessionHash);

  if (method === 'GET' && pathname === '/v1/system/health') {
    return envelope({ asOf: now.toISOString(), pythonService: 'UP', brokerage: 'UP', killSwitchActive: overlay.globalKillSwitchActive ?? false, dataFreshness: { priceFresh: true, signalFresh: null, ragFresh: true }, degradedFeatures: [] }, id);
  }
  if (method === 'GET' && pathname === '/v1/risk/portfolio') return envelope(riskPortfolio(state, now), id);
  if (method === 'GET' && pathname === '/v1/instruments/display') return envelope(instrumentCatalog(state), id);
  if (method === 'GET' && pathname === '/v1/risk/kill-switch') return envelope({ globalActive: overlay.globalKillSwitchActive ?? false, effectiveActive: overlay.globalKillSwitchActive ?? false, active: overlay.globalKillSwitchActive ?? false, changedAt: now.toISOString(), reasonClass: 'INITIAL_STATE' }, id);
  if (method === 'GET' && pathname === '/v2/risk/kill-switch') return envelope({ effectiveActive: false, active: false, changedAt: now.toISOString(), reasonClass: 'INITIAL_STATE' }, id);
  if (method === 'POST' && (pathname === '/v1/risk/kill-switch' || pathname === '/v2/risk/kill-switch')) {
    overlay.globalKillSwitchActive = Boolean(body.active);
    saveOverlay(sessionHash, overlay, now);
    return envelope({ globalActive: overlay.globalKillSwitchActive, effectiveActive: overlay.globalKillSwitchActive, active: overlay.globalKillSwitchActive, changedAt: now.toISOString(), reasonClass: 'USER_MANUAL_STOP' }, id);
  }
  if (method === 'GET' && pathname === '/v1/principle-presets') return envelope(defaultPresets(), id);
  if (method === 'GET' && pathname === '/v1/principles') return envelope(principalList(overlay, sessionHash, now).list, id);
  if (method === 'GET' && pathname === '/v1/automation/status') {
    return envelope({ contractId: 'automation-control.v1', controlState: overlay.autoArmed ? 'ARMED' : 'DISARMED', projectionState: overlay.autoArmed ? 'ARMED' : 'DISARMED', version: 1, brokerageMode: 'KIS_MOCK', principleId: ensurePrinciple(overlay, sessionHash, now).principleId, strategyId: 'strategy_demo_fixed_sma', killSwitchActive: false, certificationStatus: 'VALID' }, id);
  }
  if (method === 'GET' && pathname === '/v2/automation/status') return envelope(statusV2(overlay, state, sessionHash, now), id);
  if (method === 'GET' && pathname === '/v3/automation/status') return envelope(statusV3(overlay, state, sessionHash, now), id);
  if (method === 'GET' && pathname === '/v3/automation/policy') return envelope(policyFor(overlay, sessionHash, now), id);
  if (method === 'GET' && pathname === '/v3/automation/runs') return envelope({ items: runRows(state, sessionHash, overlay).slice(0, Number(request.nextUrl.searchParams.get('size') ?? 20)), nextCursor: null }, id);
  if (method === 'GET' && pathname === '/v3/automation/positions') return envelope({ items: positionRows(state, overlay, sessionHash, now).map((row) => row.v3) }, id);
  if (method === 'GET' && pathname === '/v2/automation/positions') return envelope({ realizedSummary: { closedPositionCount: 1, realizedPnlKrw: state.showcase?.realizedPnl ?? 0, realizedGrossKrw: state.showcase?.realizedPnl ?? 0, winningPositionCount: 1, losingPositionCount: 0, evidenceMode: 'KIS_MOCK', performanceClaimAllowed: false }, items: positionRows(state, overlay, sessionHash, now).map((row) => row.v2), nextCursor: null }, id);

  return await dispatchFullUiApiCore({ request, routeParts, method, pathname, body, id, identity, sessionHash, accountId, now, overlay, state, config, agentConfigAvailable, vertexServiceConfigured });
}

interface DispatchContext {
  request: NextRequest;
  routeParts: string[];
  method: string;
  pathname: string;
  body: JsonRecord;
  id: string;
  identity: NonNullable<ReturnType<typeof requireDemoSession>>;
  sessionHash: string;
  accountId: string;
  now: Date;
  overlay: DemoOverlay;
  state: ReturnType<typeof buildDemoState>;
  config: DemoAgentConfig;
  agentConfigAvailable: boolean;
  vertexServiceConfigured: boolean;
}

async function dispatchFullUiApiCore(context: DispatchContext) {
  const { request, method, pathname, body, id, identity, sessionHash, accountId, now, overlay, state, config, agentConfigAvailable, vertexServiceConfigured } = context;
  const principle = ensurePrinciple(overlay, sessionHash, now);
  const latest = recentRiskResults(overlay);

  if (method === 'POST' && pathname === '/v1/automation/disarm') {
    overlay.autoArmed = false;
    saveOverlay(sessionHash, overlay, now);
    return envelope({ contractId: 'automation-control.v1', controlState: 'DISARMED', projectionState: 'DISARMED', version: policyFor(overlay, sessionHash, now).version, brokerageMode: 'KIS_MOCK', principleId: principle.principleId, strategyId: 'strategy_demo_fixed_sma', killSwitchActive: false, certificationStatus: 'VALID' }, id);
  }
  if (method === 'PUT' && pathname === '/v3/automation/policy') {
    const current = policyFor(overlay, sessionHash, now);
    if (Number(body.expectedVersion) !== current.version) return envelopeError('CONFLICT', '운용 정책이 먼저 변경되었습니다.', id, 409);
    overlay.automationPolicy = {
      ...current,
      ...body,
      contractId: 'automation-policy.v2',
      version: current.version + 1,
      policyId: current.policyId,
      updatedAt: now.toISOString(),
    };
    saveOverlay(sessionHash, overlay, now);
    return envelope(policyFor(overlay, sessionHash, now), id);
  }
  if (method === 'POST' && pathname === '/v3/automation/arm') {
    if (overlay.globalKillSwitchActive) return envelopeError('CONFLICT', '안전 중지 상태에서는 운용을 시작할 수 없습니다.', id, 409);
    overlay.autoArmed = true;
    saveOverlay(sessionHash, overlay, now);
    return envelope(statusV3(overlay, state, sessionHash, now), id);
  }
  if (method === 'POST' && pathname === '/v4/automation/capital-policy') {
    const currentVersion = Number(overlay.automationPolicy?.capitalPolicyVersion ?? 0);
    if (Number(body.expectedVersion ?? 0) !== currentVersion) return envelopeError('CONFLICT', '재투자 정책이 먼저 변경되었습니다.', id, 409);
    overlay.automationPolicy = {
      ...(overlay.automationPolicy ?? {}),
      reinvestRealizedPnl: Boolean(body.reinvestRealizedPnl),
      capitalPolicyVersion: currentVersion + 1,
      capitalPolicyEffectiveFromSession: state.clock.dateKst,
      capitalPolicyTransitionStartedAt: now.toISOString(),
    };
    saveOverlay(sessionHash, overlay, now);
    return envelope(capitalPolicyFor(overlay), id);
  }
  if (method === 'PUT' && pathname === '/v4/automation/capital-policy') {
    const currentVersion = Number(overlay.automationPolicy?.capitalPolicyVersion ?? 0);
    if (Number(body.expectedVersion ?? 0) !== currentVersion) return envelopeError('CONFLICT', '재투자 정책이 먼저 변경되었습니다.', id, 409);
    overlay.automationPolicy = {
      ...(overlay.automationPolicy ?? {}),
      reinvestRealizedPnl: Boolean(body.reinvestRealizedPnl),
      capitalPolicyVersion: currentVersion + 1,
      capitalPolicyEffectiveFromSession: state.clock.dateKst,
      capitalPolicyTransitionStartedAt: now.toISOString(),
    };
    saveOverlay(sessionHash, overlay, now);
    return envelope(capitalPolicyFor(overlay), id);
  }
  if (method === 'GET' && pathname === '/v4/automation/capital-policy') {
    return envelope(capitalPolicyFor(overlay), id);
  }
  if (method === 'GET' && pathname === '/v4/automation/capital-status') return envelope(null, id);
  if (method === 'GET' && pathname.startsWith('/v3/automation/runs/')) {
    const runId = decodeURIComponent(pathname.split('/').at(-1) ?? '');
    const run = runRows(state, sessionHash, overlay).find((item) => item.runId === runId);
    return run ? envelope({ contractId: 'automation-run-detail.v3', run, candidateScreenings: [] }, id) : envelopeError('NOT_FOUND', '실행 내역을 찾을 수 없습니다.', id, 404);
  }

  if (method === 'GET' && pathname === '/v1/dashboard/risk-results/recent') return envelope({ items: latest }, id);
  if (method === 'GET' && pathname === '/v1/dashboard/risk-results/latest') return envelope(latest[0] ?? null, id);
  if (method === 'GET' && pathname.startsWith('/v1/dashboard/risk-results/')) {
    const decisionId = decodeURIComponent(pathname.split('/').at(-1) ?? '');
    const decision = (overlay.decisions ?? []).find((entry) => String(entry.decisionId) === decisionId) as JsonRecord | undefined;
    return decision ? envelope(decision, id) : envelopeError('NOT_FOUND', '판정을 찾을 수 없습니다.', id, 404);
  }
  if (method === 'GET' && pathname.endsWith('/inputs') && pathname.startsWith('/v1/decisions/')) {
    const decisionId = decodeURIComponent(pathname.split('/').filter(Boolean).at(-2) ?? '');
    const decision = (overlay.decisions ?? []).find((entry) => String(entry.decisionId) === decisionId) as JsonRecord | undefined;
    return decision ? envelope(decisionInputs(decision), id) : envelopeError('NOT_FOUND', '판정 입력을 찾을 수 없습니다.', id, 404);
  }
  if (method === 'GET' && pathname.startsWith('/v1/decisions/')) {
    const decisionId = decodeURIComponent(pathname.split('/').at(-1) ?? '');
    const decision = (overlay.decisions ?? []).find((entry) => String(entry.decisionId) === decisionId) as JsonRecord | undefined;
    return decision ? envelope(decision, id) : envelopeError('NOT_FOUND', '판정을 찾을 수 없습니다.', id, 404);
  }
  if (method === 'POST' && pathname === '/v1/decisions/evaluate-order') {
    const orderIntent = typeof body.orderIntent === 'object' && body.orderIntent !== null ? body.orderIntent as JsonRecord : {};
    const decision = decisionForOrder(orderIntent, overlay, sessionHash, state, now);
    return envelope(decision, id);
  }
  if (method === 'POST' && pathname === '/v1/brokerage/mock/orders') {
    const decisionId = String(body.decisionId ?? '');
    const decision = (overlay.decisions ?? []).find((entry) => String(entry.decisionId) === decisionId) as JsonRecord | undefined;
    const riskDecision = decision?.riskDecision as JsonRecord | undefined;
    const orderIntent = typeof body.orderIntent === 'object' && body.orderIntent !== null ? body.orderIntent as JsonRecord : {};
    if (!decision || !riskDecision || riskDecision.canSubmitOrder !== true) {
      return envelopeError('RISK_BLOCKED', '위험 검토를 통과하지 못한 주문입니다.', id, 409);
    }
    const key = request.headers.get('X-Idempotency-Key') ?? `demo.${randomUUID()}`;
    try {
      const result = applyOverlayAction(sessionHash, {
        action: 'place-virtual-order',
        side: orderIntent.side,
        symbol: demoSymbol(String(orderIntent.symbol ?? '')),
        quantity: Number(orderIntent.quantity ?? 0),
        idempotencyKey: key,
      }, now);
      const newOrder = [...result.overlay.virtualEvents].reverse().find((event) => event.type === 'ORDER_CREATED') as JsonRecord | undefined;
      if (!newOrder) return envelopeError('INTERNAL_ERROR', '주문을 저장하지 못했습니다.', id, 500);
      const orderId = String(newOrder.id);
      return envelope({ orderId, accountId, brokerageMode: 'KIS_MOCK', status: 'ACCEPTED', submittedAt: String(newOrder.atKst ?? now.toISOString()) }, id);
    } catch (error) {
      const code = error instanceof Error ? error.message : 'INTERNAL_ERROR';
      const status = typeof error === 'object' && error !== null && 'status' in error ? Number((error as { status?: unknown }).status ?? 400) : 400;
      return envelopeError(code, '주문 검토를 완료하지 못했습니다.', id, status);
    }
  }
  if (pathname.startsWith('/v1/brokerage/orders/')) {
    const orderId = decodeURIComponent(pathname.split('/')[4] ?? '');
    if (method === 'GET') {
      const detail = orderDetail(orderId, state, overlay, sessionHash);
      return detail ? envelope(detail, id) : envelopeError('NOT_FOUND', '주문을 찾을 수 없습니다.', id, 404);
    }
    if (method === 'POST' && pathname.endsWith('/cancel')) {
      const detail = orderDetail(orderId, state, overlay, sessionHash);
      if (!detail) return envelopeError('NOT_FOUND', '주문을 찾을 수 없습니다.', id, 404);
      const filled = fullPositionFills(state, overlay).some((fill) => fill.orderId === orderId);
      return filled
        ? envelopeError('CONFLICT', '이미 체결된 주문은 취소할 수 없습니다.', id, 409)
        : envelope({ ...detail, status: 'CANCELLED' }, id);
    }
  }
  if (method === 'GET' && pathname.startsWith('/v1/brokerage/mock/accounts/') && pathname.endsWith('/balances')) return envelope(accountBalance(state, overlay, sessionHash, now), id);
  if (method === 'GET' && pathname.startsWith('/v1/brokerage/mock/accounts/') && pathname.endsWith('/buyable')) {
    const symbol = request.nextUrl.searchParams.get('symbol') ?? '';
    const price = Number(request.nextUrl.searchParams.get('price') ?? 0);
    const balance = accountBalance(state, overlay, sessionHash, now);
    return envelope({ accountId, brokerageMode: 'KIS_MOCK', symbol, cashKrw: balance.cashKrw, estimatedPrice: price, buyableAmountKrw: balance.cashKrw, buyableQuantity: price > 0 ? Math.floor(balance.cashKrw / price) : 0, observedAt: now.toISOString(), sourceVersion: 'mars-demo-2026-09-29.1' }, id);
  }
  if (method === 'GET' && pathname.startsWith('/v1/brokerage/mock/accounts/') && pathname.endsWith('/fills')) {
    const from = request.nextUrl.searchParams.get('from') ?? '0000-01-01';
    const to = request.nextUrl.searchParams.get('to') ?? '9999-12-31';
    const items = fullPositionFills(state, overlay).filter((fill) => String(fill.filledAt).slice(0, 10) >= from && String(fill.filledAt).slice(0, 10) <= to);
    return envelope({ items, nextCursor: null }, id);
  }
  if (method === 'GET' && pathname.startsWith('/v3/signals/')) {
    const symbol = decodeURIComponent(pathname.split('/').at(-1) ?? '');
    return envelope({ symbol, timeframe: '1d', asOf: state.clock.kst, composite: { status: 'ABSTAIN', reason: '해당 종목의 검증된 모델 신호가 없습니다.' }, slots: [], disagrees: false, distinctSignals: [], warnings: [] }, id);
  }

  if (method === 'GET' && pathname === '/v2/rag/corpus-status') return NextResponse.json(ragCorpusStatus(sessionHash, config, now, vertexServiceConfigured), { headers: { 'Cache-Control': 'no-store' } });
  if (method === 'GET' && pathname === '/v2/rag/consent') return NextResponse.json(withRagConsent(overlay), { headers: { 'Cache-Control': 'no-store' } });
  if (method === 'POST' && pathname === '/v2/rag/consents') {
    overlay.ragConsent = body.action === 'GRANT';
    saveOverlay(sessionHash, overlay, now);
    return new NextResponse(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });
  }
  if (method === 'POST' && pathname === '/v2/rag/ask') {
    if (!overlay.ragConsent) return bareError('EXTERNAL_AI_CONSENT_REQUIRED', '외부 처리 동의가 필요합니다.', id, 403);
    const question = typeof body.question === 'string' ? body.question.trim() : '';
    if (!question) return bareError('RAG_VALIDATION_FAILED', '질문을 입력하세요.', id, 400);
    if (!agentConfigAvailable) return bareError('AGENT_CONFIGURATION_INVALID', 'Agent 설정을 확인할 수 없습니다.', id, 503);
    if (Array.from(question).length > config.maxInputChars) return bareError('PAYLOAD_TOO_LARGE', '질문 길이 한도를 넘었습니다.', id, 413);
    try {
      const answer = await askVertex(identity.session, question, config, now);
      const citations = answer.sources.map((source, index) => ({
        citationId: `cit_${index + 1}`,
        citationKind: 'PUBLIC_WEB',
        sourceId: source.id,
        title: source.title,
        canonicalUrl: source.url || null,
        locator: null,
        chunkRevisionId: `evidence_${source.id}`,
        sourceRevisionId: evidenceIndexVersion(),
        generationId: `generation_${answer.usage.modelVersion}`,
      }));
      return NextResponse.json({
        requestId: id,
        answerId: `rag_${randomUUID().replaceAll('-', '')}`,
        generationStatus: 'ANSWERED',
        answer: answer.answer,
        usage: answer.usage,
        citationCoverage: citations.length ? 1 : 0,
        retrievalFailure: citations.length === 0,
        guardrailFlags: [],
        citations,
      }, { headers: { 'Cache-Control': 'no-store' } });
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      if (error instanceof AgentConfigurationError) return bareError('AGENT_NOT_CONFIGURED', 'Vertex AI 연결을 사용할 수 없습니다.', id, 503);
      if (message.startsWith('DEMO_AGENT_SESSION_LIMIT') || message.startsWith('DEMO_AGENT_GLOBAL_LIMIT') || message.startsWith('DEMO_AGENT_CONCURRENT_LIMIT') || message.startsWith('DEMO_AGENT_RATE_LIMIT')) {
        return bareError('RATE_LIMITED', '질문 사용 한도에 도달했습니다.', id, 429);
      }
      if (message === 'DEMO_AGENT_INPUT_BUDGET') return bareError('PAYLOAD_TOO_LARGE', '질문과 근거가 입력 한도를 넘었습니다.', id, 413);
      return bareError('RAG_UNAVAILABLE', 'Vertex AI 응답을 받을 수 없습니다.', id, 503);
    }
  }
  if (method === 'GET' && pathname === '/v2/rag/history') return NextResponse.json({ items: [], nextCursor: null }, { headers: { 'Cache-Control': 'no-store' } });
  if (method === 'GET' && pathname === '/v2/rag/world-news') return NextResponse.json({ items: [], collections: [], asOf: now.toISOString(), decisionAuthority: 'NONE', signalAuthority: 'NONE', orderAuthority: 'NONE' }, { headers: { 'Cache-Control': 'no-store' } });

  if (method === 'GET' && pathname === '/v1/admin/limits') {
    const calls = usageSnapshot(sessionHash, config, now);
    return envelope({ signupCap: null, automationActiveCap: 1, updatedBy: null, updatedAt: null, userCount: 1, activeUserCount: 1, armedCount: overlay.autoArmed ? 1 : 0, agentCalls: calls.globalCalls }, id);
  }
  if (method === 'GET' && pathname === '/v1/admin/ai') {
    const calls = usageSnapshot(sessionHash, config, now);
    return envelope({
      operator: { configured: vertexServiceConfigured, projectId: null, modelId: config.model, reachable: false },
      deploymentAllowsShared: false,
      sharedEnabled: false,
      sharedEffective: false,
      switchUpdatedBy: null,
      switchUpdatedAt: null,
      users: [{ userId: 'visitor-session', username: '투자자', email: null, hasOwnKey: false, aiJudgementEnabled: true, ownToday: calls.sessionCalls, sharedToday: calls.globalCalls, ownMonth: calls.sessionCalls, sharedMonth: calls.globalCalls }],
    }, id);
  }
  if (method === 'GET' && pathname === '/v1/admin/users') {
    const page = Number(request.nextUrl.searchParams.get('page') ?? 0);
    const search = (request.nextUrl.searchParams.get('search') ?? '').toLocaleLowerCase('ko-KR');
    const user = { userId: 'visitor-session', username: '투자자', role: 'USER', status: 'ACTIVE', createdAt: now.toISOString(), email: null, providers: [], brokerState: 'KIS_MOCK', automationState: overlay.autoArmed ? 'ARMED' : 'DISARMED' };
    const items = !search || '투자자 visitor-session'.includes(search) ? (page === 0 ? [user] : []) : [];
    return envelope({ items, total: items.length }, id);
  }
  if (method === 'GET' && pathname === '/v1/admin/automation') {
    const rows = overlay.autoArmed ? [{ userId: 'visitor-session', username: '투자자', controlState: 'ARMED', accountId, controlUpdatedAt: now.toISOString(), todayClaimState: null, todayRunId: null }] : [];
    return envelope(rows, id);
  }
  if (pathname.startsWith('/v1/admin/') && method !== 'GET') return envelopeError('FORBIDDEN', '이 작업은 허용되지 않습니다.', id, 403);

  return await dispatchFullUiApiRemainder(context);
}

async function dispatchFullUiApiRemainder(context: DispatchContext) {
  const { request, method, pathname, body, id, sessionHash, accountId, now, overlay, state } = context;
  const principle = ensurePrinciple(overlay, sessionHash, now);
  const latest = recentRiskResults(overlay);

  if (method === 'GET' && pathname === '/v1/dashboard/risk-results/recent') return envelope({ items: latest }, id);
  if (method === 'GET' && pathname === '/v1/dashboard/risk-results/latest') {
    return envelope(latest[0] ?? null, id);
  }
  if (method === 'GET' && pathname.startsWith('/v1/dashboard/risk-results/')) {
    const decisionId = decodeURIComponent(pathname.split('/').at(-1) ?? '');
    const decision = (overlay.decisions ?? []).find((entry) => String(entry.decisionId) === decisionId) as JsonRecord | undefined;
    return decision
      ? envelope(dashboardEnvelope(dashboardRiskResult(decision), now), id)
      : envelopeError('NOT_FOUND', '저장된 판정이 없습니다.', id, 404);
  }

  if (method === 'GET' && pathname === '/v1/dashboard/model-evaluations/latest') {
    const runId = `demo_model_${hashHex(state.dataSource.sourceSha256).slice(0, 18)}`;
    return envelope({ runId, fixtureClass: 'REAL_ARTIFACT', asOf: now.toISOString() }, id);
  }
  if (method === 'GET' && pathname.startsWith('/v1/dashboard/model-evaluations/')) return envelope(dashboardModelEvaluation(state, now), id);
  if (method === 'GET' && pathname === '/v1/dashboard/backtests/latest') {
    const runId = `demo_${hashHex(state.dataSource.sourceSha256).slice(0, 20)}`;
    return envelope({ runId, fixtureClass: 'REAL_ARTIFACT', asOf: now.toISOString() }, id);
  }
  if (method === 'GET' && pathname.startsWith('/v1/dashboard/backtests/')) return envelope(dashboardBacktest(state, now), id);
  if (method === 'GET' && pathname === '/v1/dashboard/performance-reports/latest') return envelope(performanceReport(state, overlay, sessionHash, now), id);

  if (method === 'GET' && pathname === '/v1/principles') return envelope(principalList(overlay, sessionHash, now).list, id);
  if (method === 'POST' && pathname === '/v1/principles') {
    const preset = body.presetId === 'conservative' || body.presetId === 'aggressive' ? body.presetId : 'balanced';
    overlay.principleId = `prc_${sessionHash.slice(0, 32)}`;
    overlay.principleTitle = typeof body.title === 'string' ? body.title.slice(0, 80) : '내 투자 원칙';
    overlay.principlePresetId = preset;
    overlay.principleVersion = 1;
    overlay.principleRules = Array.isArray(body.rules) ? body.rules as JsonRecord[] : defaultRules(preset);
    overlay.riskProfile = preset === 'aggressive' ? 'growth' : preset;
    overlay.principleHistory = [{ ...ensurePrinciple(overlay, sessionHash, now), changedFields: [] } as unknown as JsonRecord];
    saveOverlay(sessionHash, overlay, now);
    return envelope(ensurePrinciple(overlay, sessionHash, now), id);
  }
  if (pathname.startsWith('/v1/principles/')) {
    const parts = pathname.split('/').filter(Boolean);
    const principleId = decodeURIComponent(parts[2] ?? '');
    if (principleId !== principle.principleId) return envelopeError('NOT_FOUND', '원칙을 찾을 수 없습니다.', id, 404);
    if (parts[3] === 'versions' && method === 'GET') {
      return envelope({ items: overlay.principleHistory ?? [{ ...principle, changedFields: [] }], nextCursor: null }, id);
    }
    if (method === 'GET') return envelope(principle, id);
    if (method === 'PUT') {
      if (Number(body.expectedVersion) !== principle.version) return envelopeError('CONFLICT', '원칙이 먼저 변경되었습니다.', id, 409);
      overlay.principleHistory = (overlay.principleHistory ?? []).map((entry) => ({ ...entry, status: 'ARCHIVED' }));
      overlay.principleRules = Array.isArray(body.rules) ? body.rules as JsonRecord[] : principle.rules;
      overlay.principleVersion = principle.version + 1;
      if (typeof body.title === 'string') overlay.principleTitle = body.title.slice(0, 80);
      const preset = presetFromRules(body.rules, principle.presetId as 'conservative' | 'balanced' | 'aggressive');
      overlay.principlePresetId = preset;
      overlay.riskProfile = preset === 'aggressive' ? 'growth' : preset;
      const updated = ensurePrinciple(overlay, sessionHash, now);
      overlay.principleHistory = [{ ...updated, changedFields: ['rules'] } as unknown as JsonRecord, ...(overlay.principleHistory ?? [])];
      saveOverlay(sessionHash, overlay, now);
      return envelope(updated, id);
    }
  }

  if (method === 'GET' && pathname === '/v1/brokerage/mock/credential') {
    return envelope({ registered: false, credential: null }, id);
  }
  if (method === 'GET' && pathname === '/v1/system/health') {
    return envelope({ asOf: now.toISOString(), pythonService: 'UP', brokerage: 'UP', killSwitchActive: overlay.globalKillSwitchActive ?? false, dataFreshness: { priceFresh: true, signalFresh: null, ragFresh: true }, degradedFeatures: [] }, id);
  }
  if (method === 'GET' && pathname === '/v1/risk/portfolio') return envelope(riskPortfolio(state, now), id);
  if (method === 'GET' && pathname === '/v1/instruments/display') return envelope(instrumentCatalog(state), id);

  if (method === 'GET' && pathname.startsWith('/v1/brokerage/mock/accounts/') && pathname.endsWith('/balances')) {
    return envelope(accountBalance(state, overlay, sessionHash, now), id);
  }
  if (method === 'GET' && pathname.startsWith('/v1/brokerage/mock/accounts/') && pathname.endsWith('/buyable')) {
    const symbol = request.nextUrl.searchParams.get('symbol') ?? '';
    const price = Number(request.nextUrl.searchParams.get('price') ?? 0);
    const balance = accountBalance(state, overlay, sessionHash, now);
    return envelope({ accountId, brokerageMode: 'KIS_MOCK', symbol, cashKrw: balance.cashKrw, estimatedPrice: price, buyableAmountKrw: balance.cashKrw, buyableQuantity: price > 0 ? Math.floor(balance.cashKrw / price) : 0, observedAt: now.toISOString(), sourceVersion: 'mars-demo-2026-09-29.1' }, id);
  }
  if (method === 'GET' && pathname.startsWith('/v1/brokerage/mock/accounts/') && pathname.endsWith('/fills')) {
    const from = request.nextUrl.searchParams.get('from') ?? '0000-01-01';
    const to = request.nextUrl.searchParams.get('to') ?? '9999-12-31';
    const items = fullPositionFills(state, overlay).filter((fill) => String(fill.filledAt).slice(0, 10) >= from && String(fill.filledAt).slice(0, 10) <= to);
    return envelope({ items, nextCursor: null }, id);
  }
  if (method === 'GET' && pathname === '/v1/rag/sources') return envelope({ items: publicEvidence() }, id);

  if (method === 'GET' && pathname === '/v1/journals') return envelope(fullJournalPage(overlay, sessionHash, now), id);
  if (method === 'POST' && pathname === '/v1/journals') {
    const entry = asJournal({ ...body, journalId: `jrn_${randomUUID().replaceAll('-', '')}`, createdAt: now.toISOString(), updatedAt: now.toISOString(), version: 1 }, sessionHash, now);
    overlay.journalEntries = [...journalRows(overlay), entry as unknown as JsonRecord].slice(0, 25);
    saveOverlay(sessionHash, overlay, now);
    return envelope(entry, id);
  }
  if (pathname.startsWith('/v1/journals/')) {
    const journalId = decodeURIComponent(pathname.split('/').at(-1) ?? '');
    const rows = journalRows(overlay);
    const index = rows.findIndex((item) => item.journalId === journalId);
    if (index < 0) return envelopeError('NOT_FOUND', '학습일지를 찾을 수 없습니다.', id, 404);
    const current = asJournal(rows[index]!, sessionHash, now);
    if (method === 'DELETE' || method === 'PATCH') {
      if (Number(body.expectedVersion) !== current.version) return envelopeError('CONFLICT', '학습일지가 먼저 변경되었습니다.', id, 409);
      if (method === 'DELETE') {
        rows.splice(index, 1);
        overlay.journalEntries = rows;
        saveOverlay(sessionHash, overlay, now);
        return envelope(current, id);
      }
      const updated = asJournal({ ...current, ...body, journalId, updatedAt: now.toISOString(), version: current.version + 1 }, sessionHash, now);
      rows[index] = updated as unknown as JsonRecord;
      overlay.journalEntries = rows;
      saveOverlay(sessionHash, overlay, now);
      return envelope(updated, id);
    }
  }

  return envelopeError('NOT_FOUND', '해당 자료를 찾을 수 없습니다.', id, 404);
}
