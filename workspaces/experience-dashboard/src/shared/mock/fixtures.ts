/**
 * 합성 fixture. 실제 계약(contracts/schemas/*, openapi.json)의 필드 이름과 타입을 그대로 따른다.
 * 여기서 타입 오류가 나면 계약과 어긋난 것이므로 화면이 아니라 이 파일을 고쳐야 한다.
 *
 * 이 값들은 화면 검증용이며 실제 성과나 계좌 상태가 아니다.
 */
import type {
  AutomationPolicyV2,
  AutomationPositionPageV2,
  AutomationRunPageV2,
  AutomationStatusV2,
  DashboardBacktestView,
  DashboardEnvelope,
  DashboardModelEvaluationView,
  DashboardRagSourcesView,
  DashboardRiskResultView,
  DecisionProjection,
  PortfolioRisk,
  PrincipleCurrent,
  PrinciplePreset,
  PrinciplePresetListData,
  PrincipleOwnerListData,
  PrincipleRule,
  PrincipleRuleId,
  RagAnswerProjection,
  RagSourceListResponse,
  SignalV3Runtime,
  SystemHealthResponse,
} from '@/shared/api/wire';

const RULE_SHAPE: Record<
  PrincipleRuleId,
  { ruleType: string; metric: string; operator: '<=' | '>=' }
> = {
  max_position_per_asset: { ruleType: 'POSITION_LIMIT', metric: 'asset_weight', operator: '<=' },
  max_gold_etf_etn_weight: { ruleType: 'POSITION_LIMIT', metric: 'gold_etf_etn_weight', operator: '<=' },
  max_single_order_amount: { ruleType: 'ORDER_SIZE', metric: 'order_amount_krw', operator: '<=' },
  daily_loss_guard: { ruleType: 'LOSS_LIMIT', metric: 'daily_loss_rate', operator: '>=' },
  mdd_guard: { ruleType: 'DRAWDOWN_LIMIT', metric: 'mdd', operator: '>=' },
  max_daily_orders: { ruleType: 'TRADING_FREQUENCY', metric: 'daily_order_count', operator: '<=' },
  negative_news_guard: { ruleType: 'NEWS_GUARD', metric: 'negative_news_score', operator: '<=' },
  disclosure_risk_guard: { ruleType: 'DISCLOSURE_GUARD', metric: 'disclosure_risk_score', operator: '<=' },
};

const RULE_ORDER = Object.keys(RULE_SHAPE) as PrincipleRuleId[];

function buildRules(thresholds: number[], sixthSeverity: 'WARN' | 'BLOCK'): PrincipleRule[] {
  return RULE_ORDER.map((ruleId, index) => {
    const shape = RULE_SHAPE[ruleId];
    const enabled = index < 6;
    return {
      ruleId,
      ruleType: shape.ruleType,
      metric: shape.metric,
      operator: shape.operator,
      threshold: thresholds[index] ?? 0,
      severity: !enabled ? 'ALLOW' : index === 5 ? sixthSeverity : 'BLOCK',
      enabled,
      evidenceRequirement: index < 6 ? 'REQUIRED' : 'OPTIONAL',
    };
  });
}

const PRESETS: PrinciplePreset[] = [
  {
    presetId: 'conservative',
    nameKo: '보수형',
    nameEn: 'Conservative',
    descriptionKo: '손실 한도와 주문 횟수를 가장 좁게 잡습니다. 자동매매를 처음 검증할 때 권장합니다.',
    descriptionEn: 'Tightest loss and trading-frequency limits.',
    mode: 'GUIDE',
    order: 1,
    defaultRules: buildRules([0.15, 0.2, 300000, -0.02, -0.1, 2, 0.5, 0.5], 'BLOCK'),
  },
  {
    presetId: 'balanced',
    nameKo: '균형형',
    nameEn: 'Balanced',
    descriptionKo: '수익 기회와 손실 통제를 절충합니다. 하루 주문 횟수 초과는 경고로 처리합니다.',
    descriptionEn: 'Balances opportunity and loss control.',
    mode: 'GUIDE',
    order: 2,
    defaultRules: buildRules([0.2, 0.3, 500000, -0.03, -0.15, 3, 0.7, 0.7], 'WARN'),
  },
  {
    presetId: 'aggressive',
    nameKo: '공격형',
    nameEn: 'Aggressive',
    descriptionKo: '비중과 손실 허용치를 넓게 잡습니다. 백테스트로 손실 구간을 먼저 확인하세요.',
    descriptionEn: 'Widest exposure and loss tolerance.',
    mode: 'GUIDE',
    order: 3,
    defaultRules: buildRules([0.3, 0.4, 1000000, -0.05, -0.25, 5, 0.85, 0.85], 'WARN'),
  },
];

export const presetList: PrinciplePresetListData = {
  disclaimer: {
    ko: '투자 원칙 preset은 참고 템플릿입니다. 어떤 preset도 수익을 보장하지 않으며 최종 판단과 책임은 사용자에게 있습니다.',
    en: 'Presets are reference templates and guarantee no returns.',
  },
  items: PRESETS,
};

const PRINCIPLE_ID = 'prc_9f2c41d7a83b4e15b0c6d2f7a1e83c40';

export const principle: PrincipleCurrent = {
  principleId: PRINCIPLE_ID,
  title: '내 균형형 원칙',
  presetId: 'balanced',
  mode: 'GUIDE',
  status: 'ACTIVE',
  version: 3,
  createdAt: '2026-08-02T10:12:00+09:00',
  updatedAt: '2026-08-20T21:41:00+09:00',
  rules: buildRules([0.18, 0.3, 400000, -0.03, -0.12, 3, 0.7, 0.7], 'WARN'),
};

export const principleList: PrincipleOwnerListData = {
  items: [
    {
      principleId: PRINCIPLE_ID,
      title: principle.title,
      presetId: principle.presetId,
      mode: principle.mode,
      status: principle.status,
      version: principle.version,
      createdAt: principle.createdAt,
      updatedAt: principle.updatedAt,
    },
  ],
  nextCursor: null,
};

export const health: SystemHealthResponse = {
  asOf: new Date().toISOString(),
  pythonService: 'UP',
  brokerage: 'UP',
  killSwitchActive: false,
  dataFreshness: { priceFresh: true, signalFresh: null, ragFresh: true },
  degradedFeatures: ['SIGNAL_PRODUCTION_POINTER_ABSENT'],
};

export const riskPortfolio: PortfolioRisk = {
  asOf: new Date(Date.now() - 4 * 60_000).toISOString(),
  portfolioValue: 10_000_000,
  dailyPnlRate: -0.012,
  mdd: -0.064,
  // 이 값들을 만드는 단계가 운영에 없어 계속 null이다.
  var95: null,
  cvar95: null,
  realizedVolatility20d: null,
  annualizedVolatility20d: 0.38,
  hmmRegime: null,
  hmmRegimeProbability: null,
  killSwitchActive: false,
  dataFreshness: { priceFresh: true, signalFresh: null, ragFresh: null },
};

/* ------------------------------------------------------------ Automation */

export let automationPolicy: AutomationPolicyV2 = {
  contractId: 'automation-policy.v1',
  policyId: 'auto_pol_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  version: 1,
  presetId: 'balanced',
  capitalLimitKrw: 1_000_000,
  stopLossBps: 500,
  takeProfitBps: 1000,
  maxOpenPositions: 5,
  maxNewOrdersPerSession: 1,
  evaluationTimeKst: '09:30',
  buyCutoffTimeKst: '09:40',
  cancelTimeKst: '15:20',
  createdAt: '2026-08-27T09:00:00+09:00',
  updatedAt: '2026-08-27T09:00:00+09:00',
};

export const automationStatus: AutomationStatusV2 = {
  contractId: 'automation-status.v2',
  controlState: 'DISARMED',
  projectionState: 'DISARMED',
  controlVersion: 1,
  brokerageMode: 'KIS_MOCK',
  accountId: 'acct_cccccccccccccccccccccccccccccccc',
  policy: automationPolicy,
  killSwitchActive: false,
  certificationStatus: 'REQUIRED',
  openPositionCount: 0,
  unresolvedReconciliation: false,
  canArm: false,
  blockers: ['BLOCKED_INCOMPLETE_RISK_BALANCE'],
};

export function replaceAutomationPolicy(policy: AutomationPolicyV2): void {
  automationPolicy = policy;
  automationStatus.policy = policy;
}

export const automationRuns: AutomationRunPageV2 = {
  items: [
    {
      contractId: 'automation-run.v2',
      runId: 'auto_run_preview_blocked_0001',
      sessionDate: '2026-08-27',
      state: 'SKIPPED_DATA_UNAVAILABLE',
      brokerageMode: 'KIS_MOCK',
      policyId: automationPolicy.policyId,
      policyVersion: automationPolicy.version,
      selectedSymbol: null,
      selectedSide: null,
      orderQuantity: null,
      filledQuantity: null,
      leavesQuantity: null,
      limitPriceKrw: null,
      estimatedAmountKrw: null,
      exitReason: null,
      physicalSubmitCount: 0,
      providerCalls: 0,
      startedAt: '2026-08-27T09:30:00+09:00',
      updatedAt: '2026-08-27T09:30:01+09:00',
    },
  ],
  nextCursor: null,
};

export const automationPositions: AutomationPositionPageV2 = {
  realizedSummary: {
    closedPositionCount: 0,
    realizedPnlKrw: 0,
    realizedGrossKrw: 0,
    winningPositionCount: 0,
    losingPositionCount: 0,
    evidenceMode: 'KIS_MOCK',
    performanceClaimAllowed: false,
  },
  items: [],
  nextCursor: null,
};

/* -------------------------------------------------------------- Decision */

const iso = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();

export const dashboardRiskResults: Record<string, DashboardEnvelope<DashboardRiskResultView>> = {
  dec_demo_warn_000001: {
    viewState: 'READY',
    asOf: iso(2),
    freshUntil: iso(-8),
    evidenceMode: 'STORED_RUNTIME',
    performanceClaimAllowed: false,
    view: {
      decisionId: 'dec_demo_warn_000001',
      action: 'WARN',
      reasons: [
        '오늘 주문 횟수가 내가 정한 상한을 넘었습니다.',
        '부정 뉴스 근거가 없어 뉴스 기준은 평가하지 않았습니다.',
      ],
      principles: ['하루 최대 주문 횟수', '부정 뉴스 대응'],
      riskItems: [
        { code: 'DAILY_ORDER_COUNT', severity: 'WARN', summary: '오늘 주문 4건으로 기준 3건을 넘었습니다.' },
        { code: 'NEWS_EVIDENCE_MISSING', severity: 'INFO', summary: '뉴스 근거가 없어 비교하지 않았습니다.' },
      ],
    },
  },
  dec_demo_hold_000001: {
    viewState: 'READY',
    asOf: iso(1),
    freshUntil: iso(-9),
    evidenceMode: 'STORED_RUNTIME',
    performanceClaimAllowed: false,
    view: {
      decisionId: 'dec_demo_hold_000001',
      action: 'HOLD',
      reasons: ['현재가 관측이 허용 지연을 넘어 판단을 미뤘습니다.'],
      principles: ['단일 종목 최대 비중'],
      riskItems: [
        { code: 'PRICE_STALE', severity: 'BLOCK', summary: '가격 근거가 오래돼 사용할 수 없습니다.' },
      ],
    },
  },
  dec_demo_block_000001: {
    viewState: 'READY',
    asOf: iso(6),
    freshUntil: iso(-4),
    evidenceMode: 'STORED_RUNTIME',
    performanceClaimAllowed: false,
    view: {
      decisionId: 'dec_demo_block_000001',
      action: 'BLOCK',
      reasons: ['1회 주문 금액과 단일 종목 비중이 모두 기준을 넘었습니다.'],
      principles: ['1회 주문 최대 금액', '단일 종목 최대 비중'],
      riskItems: [
        { code: 'ORDER_AMOUNT_EXCEEDED', severity: 'BLOCK', summary: '주문 금액이 기준을 넘었습니다.' },
        { code: 'ASSET_WEIGHT_EXCEEDED', severity: 'BLOCK', summary: '종목 비중이 기준을 넘었습니다.' },
      ],
    },
  },
};

export const decisions: Record<string, DecisionProjection> = {
  dec_demo_warn_000001: {
    decisionId: 'dec_demo_warn_000001',
    createdAt: iso(2),
    enforcementAction: 'WARN',
    mode: 'GUIDE',
    portfolioSource: 'KIS_MOCK',
    principleId: PRINCIPLE_ID,
    principleVersion: 3,
    principleVersionId: 'pv_2c7f0b91',
    validUntil: iso(-8),
    riskDecision: {
      decisionId: 'dec_demo_warn_000001',
      evaluationId: 'evl_demo_warn_000001',
      decision: 'WARN',
      canSubmitOrder: true,
      mode: 'GUIDE',
      portfolioSource: 'KIS_MOCK',
      principleVersion: 3,
      principleVersionId: 'pv_2c7f0b91',
      catalogVersion: 1,
      readinessPolicyVersion: 'rp_v1',
      schemaVersion: '1.0.0',
      semanticInputHash: '4b1f9d0c2a77e5b8c31d6f0a9e4b27c58d3a1f6072be94dc5a08f3e1b7c62d90',
      snapshotArtifactHash: 'c07a2e51b9d34f6a8c1e70bd5924af38e6c1b0d792453fae81b6c3d0f27a94e5',
      validUntil: iso(-8),
      violations: [
        {
          ruleId: 'max_daily_orders',
          message: '하루 주문 횟수 상한을 넘었습니다.',
          metricValue: 4,
          threshold: 3,
          severity: 'WARN',
        },
      ],
      issues: [],
      warnings: [
        {
          code: 'OPTIONAL_EVIDENCE_MISSING',
          message: '부정 뉴스 점수 근거가 없어 뉴스 기준은 평가하지 않았습니다.',
          source: 'news_sentiment',
          ruleId: 'negative_news_guard',
        },
      ],
      abstentions: [
        {
          code: 'OPTIONAL_EVIDENCE_MISSING',
          component: 'news_sentiment',
          disposition: 'ABSTAIN',
          message: '뉴스 기준 비교를 수행하지 않았습니다.',
          ruleId: 'negative_news_guard',
        },
      ],
      riskItems: [
        { metric: 'asset_weight', value: 0.146, severity: 'INFO', source: 'PORTFOLIO' },
        { metric: 'daily_loss_rate', value: -0.012, severity: 'INFO', source: 'PORTFOLIO' },
        { metric: 'daily_order_count', value: 4, severity: 'WARN', source: 'LEDGER' },
        {
          metric: 'disclosure_risk_score',
          value: 0.21,
          severity: 'INFO',
          source: 'OPENDART',
          eventCodes: ['DART_REG_STATEMENT'],
          mappingVersion: 'dm_v1',
          sourceRefs: ['dart:20260820000123'],
        },
      ],
    },
  },
  dec_demo_hold_000001: {
    decisionId: 'dec_demo_hold_000001',
    createdAt: iso(1),
    enforcementAction: 'HOLD',
    mode: 'GUIDE',
    portfolioSource: 'KIS_MOCK',
    principleId: PRINCIPLE_ID,
    principleVersion: 3,
    principleVersionId: 'pv_2c7f0b91',
    validUntil: iso(-9),
    riskDecision: {
      decisionId: 'dec_demo_hold_000001',
      evaluationId: 'evl_demo_hold_000001',
      decision: 'HOLD',
      canSubmitOrder: false,
      mode: 'GUIDE',
      portfolioSource: 'KIS_MOCK',
      principleVersion: 3,
      principleVersionId: 'pv_2c7f0b91',
      catalogVersion: 1,
      readinessPolicyVersion: 'rp_v1',
      schemaVersion: '1.0.0',
      semanticInputHash: '9d3e7c150ab24f68e0c7b3a1d582f60947be3c1a58d0f2e64b7a913c05fd8e27',
      snapshotArtifactHash: '2f81ba0c74d9e3517a06c85bf1d34e29c7085a6b3e94f0d21c6a75be08349fd1',
      validUntil: iso(-9),
      violations: [],
      issues: [
        {
          code: 'PRICE_STALE',
          message: '현재가 관측이 허용 지연을 넘었습니다. 새 관측이 들어온 뒤 다시 평가하세요.',
          source: 'market_quote',
        },
      ],
      warnings: [],
      abstentions: [],
      riskItems: [{ metric: 'daily_loss_rate', value: -0.012, severity: 'INFO', source: 'PORTFOLIO' }],
    },
  },
  dec_demo_block_000001: {
    decisionId: 'dec_demo_block_000001',
    createdAt: iso(6),
    enforcementAction: 'BLOCK',
    mode: 'STRICT',
    portfolioSource: 'KIS_MOCK',
    principleId: PRINCIPLE_ID,
    principleVersion: 3,
    principleVersionId: 'pv_2c7f0b91',
    validUntil: iso(-4),
    riskDecision: {
      decisionId: 'dec_demo_block_000001',
      evaluationId: 'evl_demo_block_000001',
      decision: 'BLOCK',
      canSubmitOrder: false,
      mode: 'STRICT',
      portfolioSource: 'KIS_MOCK',
      principleVersion: 3,
      principleVersionId: 'pv_2c7f0b91',
      catalogVersion: 1,
      readinessPolicyVersion: 'rp_v1',
      schemaVersion: '1.0.0',
      semanticInputHash: 'e51c780ab3d9426f1907c4be25a3f08d6c19b7402e5d3fa8619c04b7d2e83f56',
      snapshotArtifactHash: '7a3d09f2c85be14670d2a9c3f1085eb47d629c0a53fb812e94d75a0c6b3e2f18',
      validUntil: iso(-4),
      violations: [
        {
          ruleId: 'max_single_order_amount',
          message: '1회 주문 금액 상한을 넘었습니다.',
          metricValue: 728000,
          threshold: 400000,
          severity: 'BLOCK',
        },
        {
          ruleId: 'max_position_per_asset',
          message: '단일 종목 비중 상한을 넘었습니다.',
          metricValue: 0.211,
          threshold: 0.18,
          severity: 'BLOCK',
        },
      ],
      issues: [],
      warnings: [],
      abstentions: [
        {
          code: 'NOT_APPLICABLE_V1',
          component: 'system_rule',
          disposition: 'NOT_APPLICABLE',
          message: '이 기준은 v1에서 평가하지 않습니다.',
        },
      ],
      riskItems: [
        { metric: 'asset_weight', value: 0.211, severity: 'BLOCK', source: 'PORTFOLIO' },
        { metric: 'order_amount_krw', value: 728000, severity: 'BLOCK', source: 'ORDER_INTENT' },
      ],
    },
  },
};

/* ---------------------------------------------------------------- Signal */

export const signals: Record<string, SignalV3Runtime> = {
  '005930': {
    symbol: '005930',
    timeframe: '1d',
    asOf: iso(720),
    modelReportId: 'mr_2026_08_20_lstm_lgbm',
    composite: { status: 'ABSTAIN', reason: 'REQUIRED_COMPONENT_UNAVAILABLE' },
    components: {
      ruleBaseline: {
        status: 'AVAILABLE',
        producer: 'RULE_BASELINE',
        sourceWorkspace: 'return-engine',
        asOf: iso(720),
        signal: 'BUY',
        predictedReturn: 0.0082,
      },
      lstm: {
        status: 'AVAILABLE',
        producer: 'LSTM',
        sourceWorkspace: 'return-engine',
        asOf: iso(720),
        signal: 'HOLD',
        predictedReturn: 0.0011,
      },
      lightgbm: {
        status: 'ABSTAIN',
        producer: 'LIGHTGBM',
        sourceWorkspace: 'decision-platform',
        reason: 'MISSING_EVIDENCE',
      },
      hmmRegime: {
        status: 'ABSTAIN',
        producer: 'HMM',
        sourceWorkspace: 'decision-platform',
        reason: 'CALIBRATION_FAILED',
      },
    },
    warnings: ['LightGBM 운영 결과가 없어 종합 신호를 계산하지 않았습니다.'],
  },
  '000660': {
    symbol: '000660',
    timeframe: '1d',
    asOf: iso(700),
    composite: { status: 'AVAILABLE', signal: 'HOLD' },
    components: {
      ruleBaseline: {
        status: 'AVAILABLE',
        producer: 'RULE_BASELINE',
        sourceWorkspace: 'return-engine',
        asOf: iso(700),
        signal: 'SELL',
        predictedReturn: -0.0064,
      },
      lstm: {
        status: 'AVAILABLE',
        producer: 'LSTM',
        sourceWorkspace: 'return-engine',
        asOf: iso(700),
        signal: 'HOLD',
        predictedReturn: -0.0007,
      },
      lightgbm: {
        status: 'AVAILABLE',
        producer: 'LIGHTGBM',
        sourceWorkspace: 'decision-platform',
        asOf: iso(700),
        signal: 'HOLD',
        predictedReturn: 0.0004,
      },
      hmmRegime: {
        status: 'AVAILABLE',
        producer: 'HMM',
        sourceWorkspace: 'decision-platform',
        asOf: iso(700),
        state: 'HIGH_VOLATILITY',
      },
    },
    warnings: [],
  },
  '132030': {
    symbol: '132030',
    timeframe: '1d',
    composite: { status: 'ABSTAIN', reason: 'REQUIRED_COMPONENT_UNAVAILABLE' },
    components: {
      ruleBaseline: {
        status: 'ABSTAIN',
        producer: 'RULE_BASELINE',
        sourceWorkspace: 'return-engine',
        reason: 'MISSING_EVIDENCE',
      },
      lstm: {
        status: 'ABSTAIN',
        producer: 'LSTM',
        sourceWorkspace: 'return-engine',
        reason: 'MISSING_EVIDENCE',
      },
      lightgbm: {
        status: 'ABSTAIN',
        producer: 'LIGHTGBM',
        sourceWorkspace: 'decision-platform',
        reason: 'MISSING_EVIDENCE',
      },
      hmmRegime: {
        status: 'ABSTAIN',
        producer: 'HMM',
        sourceWorkspace: 'decision-platform',
        reason: 'MISSING_EVIDENCE',
      },
    },
    warnings: ['검증된 신호 근거가 없습니다.'],
  },
};

/* ------------------------------------------------------------ Dashboard */

export const modelEvaluations: Record<string, DashboardEnvelope<DashboardModelEvaluationView>> = {
  demo_s8_offline_0001: {
    viewState: 'READY',
    asOf: iso(1440),
    freshUntil: null,
    evidenceMode: 'SYNTHETIC_DEMO',
    performanceClaimAllowed: false,
    view: {
      runId: 'demo_s8_offline_0001',
      models: [
        {
          modelId: 'BASELINE',
          status: 'AVAILABLE',
          metrics: { cagr: 0.041, mdd: -0.133, sharpe: 0.42, sortino: 0.55, var95: -0.031, cvar95: -0.048 },
        },
        {
          modelId: 'LSTM',
          status: 'AVAILABLE',
          metrics: { cagr: 0.067, mdd: -0.151, sharpe: 0.58, sortino: 0.74, var95: -0.028, cvar95: -0.044 },
        },
        {
          modelId: 'LIGHTGBM',
          status: 'ABSTAIN',
          metrics: { cagr: null, mdd: null, sharpe: null, sortino: null, var95: null, cvar95: null },
        },
      ],
      timeline: Array.from({ length: 12 }, (_, index) => ({
        at: new Date(Date.UTC(2025, index, 15)).toISOString(),
        value: Number((0.5 + Math.sin(index / 2) * 0.08).toFixed(4)),
      })),
      sourceRunIds: ['demo_s8_offline_0001'],
    },
  },
};

function buildCurve(drift: number, volatility: number) {
  let seed = 20260623;
  const next = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648 - 0.5;
  };
  let value = 1;
  const points: { at: string; value: number }[] = [];
  const start = Date.UTC(2023, 0, 2);
  for (let day = 0; day < 500; day += 1) {
    const date = new Date(start + day * 86_400_000);
    if (date.getUTCDay() === 0 || date.getUTCDay() === 6) continue;
    value *= 1 + drift + next() * volatility;
    points.push({ at: date.toISOString(), value: Number(value.toFixed(5)) });
  }
  return points;
}

const strictCurve = buildCurve(0.0002, 0.013);

export const backtests: Record<string, DashboardEnvelope<DashboardBacktestView>> = {
  demo_s8_offline_0001: {
    viewState: 'READY',
    asOf: iso(1440),
    freshUntil: null,
    evidenceMode: 'SYNTHETIC_DEMO',
    performanceClaimAllowed: false,
    view: {
      runId: 'demo_s8_offline_0001',
      fixtureClass: 'SYNTHETIC_FAKE_E2E',
      strategies: [
        {
          strategy: 'Baseline',
          metrics: { cagr: 0.084, mdd: -0.172, sharpe: 0.71, sortino: 0.92, var95: -0.026, cvar95: -0.041 },
          curve: buildCurve(0.00012, 0.019),
        },
        {
          strategy: 'Guide',
          metrics: { cagr: 0.079, mdd: -0.138, sharpe: 0.77, sortino: 1.01, var95: -0.022, cvar95: -0.035 },
          curve: buildCurve(0.00016, 0.016),
        },
        {
          strategy: 'Strict',
          metrics: { cagr: 0.073, mdd: -0.109, sharpe: 0.83, sortino: 1.08, var95: -0.018, cvar95: -0.03 },
          curve: strictCurve,
        },
      ],
      heatmap: buildHeatmap(strictCurve),
      metricCards: [
        { metric: 'turnover', value: 1.9 },
        { metric: 'principle_violations', value: 0 },
        { metric: 'trade_count', value: 49 },
      ],
      projectionHash: 'sha256:0f3c1a7d92b46e08c5713fa2be40d69cf815072a3d94be1c6a08f52d3e7b14c9',
    },
  },
};

function buildHeatmap(curve: { at: string; value: number }[]) {
  const byMonth = new Map<string, { first: number; last: number }>();
  for (const point of curve) {
    const month = point.at.slice(0, 7);
    const bucket = byMonth.get(month);
    if (!bucket) byMonth.set(month, { first: point.value, last: point.value });
    else bucket.last = point.value;
  }
  return Array.from(byMonth.entries()).map(([month, bucket]) => ({
    month,
    return: Number((bucket.last / bucket.first - 1).toFixed(4)),
  }));
}

/* ------------------------------------------------------------------- RAG */

export const ragSourceList: RagSourceListResponse = {
  items: [
    {
      sourceId: 'src_project_gold_futures_etf_132030_001',
      title: '132030 금선물 ETF의 구조와 롤오버 위험',
      institution: '프로젝트 작성 자료',
      topic: 'PRODUCT_RISK',
      attribution: '운용사 공식 상품 페이지 요약',
      canonicalUrl: 'https://www.samsungfund.com/etf/',
      lastCheckedAt: '2026-08-01T09:00:00+09:00',
    },
    {
      sourceId: 'src_project_mdd_sharpe_reading_006',
      title: 'MDD와 Sharpe를 함께 읽는 법',
      institution: '프로젝트 작성 자료',
      topic: 'RISK',
      attribution: '성과지표 정의 문서 요약',
      canonicalUrl: 'https://www.krx.co.kr/',
      lastCheckedAt: '2026-08-01T09:00:00+09:00',
    },
    {
      sourceId: 'src_project_etf_etn_difference_003',
      title: 'ETF와 ETN의 발행사 신용위험 차이',
      institution: '프로젝트 작성 자료',
      topic: 'PRODUCT_RISK',
      attribution: '감독기관 투자위험 안내 요약',
      canonicalUrl: 'https://www.fss.or.kr/',
      lastCheckedAt: '2026-08-01T09:00:00+09:00',
    },
    {
      sourceId: 'src_project_backtest_overfitting_004',
      title: '백테스트 과최적화를 의심해야 하는 신호',
      institution: '프로젝트 작성 자료',
      topic: 'METHODOLOGY',
      attribution: 'walk-forward·거래비용·다중시도 보정 요약',
      canonicalUrl: 'https://www.krx.co.kr/',
      lastCheckedAt: '2026-08-01T09:00:00+09:00',
    },
  ],
};

const ADVICE_PATTERN = /(사도|살까|매수해|매도해|추천해|사줘|팔아|종목\s*추천|얼마나 사)/;
const SENSITIVE_PATTERN = /(내 계좌|잔고|보유 종목|주문 내역|전화번호|주민)/;

let answerCounter = 0;

export function ragAnswerFor(question: string): RagAnswerProjection {
  answerCounter += 1;
  const answerId = `rag_demo_answer_${String(answerCounter).padStart(6, '0')}`;
  const base = {
    requestId: 'req_opaque',
    answerId,
    citationCoverage: 0,
    retrievalFailure: false,
    citations: [],
    guardrailFlags: [] as string[],
  };

  if (SENSITIVE_PATTERN.test(question)) {
    return { ...base, generationStatus: 'BLOCKED_SENSITIVE', answer: null, guardrailFlags: ['ACCOUNT_SCOPE_QUESTION'] };
  }
  if (ADVICE_PATTERN.test(question)) {
    return { ...base, generationStatus: 'BLOCKED_ADVICE', answer: null, guardrailFlags: ['DIRECT_INVESTMENT_ADVICE'] };
  }
  if (/금\s*ETF|132030|롤오버/.test(question)) {
    return {
      ...base,
      generationStatus: 'ANSWERED',
      citationCoverage: 1,
      answer:
        '금 선물 ETF는 만기가 다가온 선물을 다음 월물로 교체하는 과정을 반복합니다. 다음 월물이 더 비싸면 교체할 때마다 비용이 발생해, 금 가격이 그대로여도 ETF 수익률은 낮아질 수 있습니다. 따라서 금 가격 전망과 ETF 성과를 같은 것으로 보고 판단하면 안 됩니다.',
      citations: [
        {
          citationId: 'cit_1',
          sourceId: 'src_project_gold_futures_etf_132030_001',
          title: '132030 금선물 ETF의 구조와 롤오버 위험',
          sectionTitle: '핵심 한계',
          canonicalUrl: 'https://www.samsungfund.com/etf/',
        },
      ],
    };
  }
  if (/MDD|Sharpe|최대낙폭|샤프/.test(question)) {
    return {
      ...base,
      generationStatus: 'ANSWERED',
      citationCoverage: 1,
      answer:
        'MDD는 고점 대비 가장 크게 떨어졌던 폭이고, Sharpe는 변동성 한 단위당 초과수익입니다. 두 지표는 서로를 대체하지 않습니다. 같은 Sharpe라도 MDD가 크면 실제로 버티기 어려운 전략일 수 있습니다.',
      citations: [
        {
          citationId: 'cit_1',
          sourceId: 'src_project_mdd_sharpe_reading_006',
          title: 'MDD와 Sharpe를 함께 읽는 법',
          sectionTitle: '지표 해석',
          canonicalUrl: 'https://www.krx.co.kr/',
        },
      ],
    };
  }
  return { ...base, generationStatus: 'RETRIEVAL_ONLY', answer: null };
}

export function ragSourcesFor(
  answerId: string,
  citedSourceIds: string[],
): DashboardEnvelope<DashboardRagSourcesView> {
  const cited = ragSourceList.items.filter((item) => citedSourceIds.includes(item.sourceId));
  const rest = ragSourceList.items.filter((item) => !citedSourceIds.includes(item.sourceId));
  const toCard = (item: (typeof ragSourceList.items)[number]) => ({
    sourceId: item.sourceId,
    title: item.title,
    classification: 'INTERNAL_PAPER' as const,
    summary: item.attribution,
  });
  return {
    viewState: cited.length === 0 && rest.length === 0 ? 'EMPTY' : 'READY',
    asOf: new Date().toISOString(),
    freshUntil: null,
    evidenceMode: 'STORED_RUNTIME',
    performanceClaimAllowed: false,
    view: {
      answerId,
      topSources: cited.slice(0, 3).map(toCard),
      expandableSources: rest.slice(0, 5).map(toCard),
    },
  };
}

/** 마지막 답변의 인용 목록. mock에서 rag-sources 응답을 만들 때 참조한다. */
export const lastCitations: { answerId: string; sourceIds: string[] } = { answerId: '', sourceIds: [] };
