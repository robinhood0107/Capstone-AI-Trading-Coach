/**
 * Spring Decision Platform이 실제로 내려주는 wire payload 타입.
 *
 * 출처: contracts/openapi/openapi.json (Decision Platform API 0.0.1)
 *       contracts/schemas/dashboard-*.v1.schema.json
 * 이 파일은 손으로 추측하지 않고 위 계약에서 그대로 옮긴다.
 *
 * 규칙: 서버가 null로 내려준 값은 화면 계층까지 null로 전달한다. 0으로 채우지 않는다.
 */

/* ------------------------------------------------------------------ 공통 */

export type DecisionAction = 'ALLOW' | 'WARN' | 'HOLD' | 'BLOCK';
export type PortfolioSource = 'KIS_MOCK' | 'INTERNAL_PAPER';
export type PrincipleMode = 'GUIDE' | 'STRICT';

/* ---------------------------------------------------------------- Auth */

export interface LoginUserResponse {
  userId: string;
  username: string;
  role: 'USER' | 'ADMIN';
}

export interface LoginResponse {
  accessToken: string;
  expiresAt: string;
  tokenType: string;
  user: LoginUserResponse;
}

export interface MockCredentialSummary {
  accountId: string;
  state: 'STORED' | 'CONNECTED' | 'CERTIFIED' | 'DISCONNECTING';
  revision: number;
  appKeyLast4: string;
  accountNoLast4: string;
  connected: boolean;
  certified: boolean;
  certificationStatus: 'NOT_STARTED' | 'RUNNING' | 'RECOVERY_REQUIRED' | 'PASS' | 'FAILED';
  certificationFailureCode: string | null;
  certificationSessionDate: string | null;
}

export interface MockCredentialReadResponse {
  registered: boolean;
  credential: MockCredentialSummary | null;
}

export interface MockCredentialCertificationOutcome {
  status: 'PASS' | 'FAILED' | 'RECOVERY_REQUIRED';
  certificationId: string | null;
  sessionDate: string | null;
  quoteCalls: number;
  brokerageCalls: number;
  tokenCalls: number;
  failureCode: string | null;
}

/* ---------------------------------------------------------------- Health */

export interface DataFreshness {
  priceFresh: boolean | null;
  signalFresh: boolean | null;
  ragFresh: boolean | null;
}

export interface SystemHealthResponse {
  asOf: string;
  pythonService: string;
  brokerage: string;
  killSwitchActive: boolean;
  dataFreshness: DataFreshness;
  degradedFeatures: string[];
}

/* ------------------------------------------------------------------ Risk */

/** contracts/schemas/s2-4-risk-portfolio.schema.json */
export interface PortfolioRisk {
  asOf: string;
  portfolioValue: number | null;
  dailyPnlRate: number | null;
  mdd: number | null;
  var95: number | null;
  cvar95: number | null;
  realizedVolatility20d: number | null;
  annualizedVolatility20d: number | null;
  hmmRegime: string | null;
  hmmRegimeProbability: number | null;
  killSwitchActive: boolean;
  dataFreshness: DataFreshness;
}

export interface MockBalancePosition {
  symbol: string;
  quantity: number;
  marketValueKrw: number;
  isGoldEtfEtn: boolean;
}

export interface MockBalance {
  accountId: string;
  brokerageMode: 'KIS_MOCK';
  cashKrw: number;
  portfolioEquityKrw: number;
  marginRequirementKrw: number;
  positions: MockBalancePosition[];
  observedAt: string;
  sourceVersion: string;
}

export interface InstrumentDisplayItem {
  symbol: string;
  nameKo: string;
  logoText: string;
  brandColor: string;
  market: 'KOSPI' | 'ETF';
}

export interface InstrumentDisplayCatalog {
  items: InstrumentDisplayItem[];
}

export type KillSwitchReasonClass =
  | 'USER_RESUME'
  | 'USER_MANUAL_STOP'
  | 'OPERATOR_MANUAL_STOP'
  | 'DATA_FRESHNESS_STOP'
  | 'BROKERAGE_FAILURE_STOP'
  | 'DEMO_SAFETY_STOP'
  | 'ADMIN_RESUME'
  | 'INITIAL_STATE';

export interface KillSwitchState {
  globalActive?: boolean;
  effectiveActive?: boolean;
  active: boolean;
  changedAt: string;
  reasonClass: KillSwitchReasonClass;
}

/* ------------------------------------------------------------- Principle */

export type PrincipleRuleId =
  | 'max_position_per_asset'
  | 'max_gold_etf_etn_weight'
  | 'max_single_order_amount'
  | 'daily_loss_guard'
  | 'mdd_guard'
  | 'max_daily_orders'
  | 'negative_news_guard'
  | 'disclosure_risk_guard';

export interface PrincipleRule {
  ruleId: PrincipleRuleId;
  ruleType: string;
  metric: string;
  operator: '<=' | '>=';
  threshold: number;
  /** enabled=false이면 severity는 반드시 ALLOW다. */
  severity: 'ALLOW' | 'WARN' | 'BLOCK';
  enabled: boolean;
  evidenceRequirement: 'REQUIRED' | 'OPTIONAL';
}

export type PresetId = 'conservative' | 'balanced' | 'aggressive';

export interface PrinciplePreset {
  presetId: PresetId;
  nameKo: string;
  nameEn: string;
  descriptionKo: string;
  descriptionEn: string;
  mode: PrincipleMode;
  order: number;
  defaultRules: PrincipleRule[];
}

export interface PrinciplePresetListData {
  disclaimer: { ko: string; en: string };
  items: PrinciplePreset[];
}

/** GET/PUT /api/v1/principles/{principleId} — principleVersionId는 여기에 없다. */
export interface PrincipleCurrent {
  principleId: string;
  title: string;
  presetId: PresetId;
  mode: PrincipleMode;
  status: 'ACTIVE' | 'ARCHIVED';
  version: number;
  createdAt: string;
  updatedAt: string;
  rules: PrincipleRule[];
}

export interface PrincipleSummary {
  principleId: string;
  title: string;
  presetId: PresetId;
  mode: PrincipleMode;
  status: 'ACTIVE' | 'ARCHIVED';
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface PrincipleOwnerListData {
  items: PrincipleSummary[];
  nextCursor: string | null;
}

export interface PrincipleUpdateRequest {
  expectedVersion: number;
  mode: PrincipleMode;
  rules: PrincipleRule[];
  status: 'ACTIVE' | 'ARCHIVED';
  title: string;
}

/**
 * POST /api/v1/principles — 원칙을 처음 만들 때.
 *
 * `rules` 를 비우면 preset 의 기본값이 그대로 쓰인다. 화면에서 값을 손봤을 때만 채운다.
 */
export interface PrincipleCreateRequest {
  title: string;
  presetId: PresetId;
  mode?: PrincipleMode;
  rules?: PrincipleRule[];
}

/** GET /api/v1/principles/{principleId}/versions 의 한 항목. 저장할 때마다 하나씩 쌓인다. */
export interface PrincipleVersion {
  principleId: string;
  title: string;
  presetId: PresetId;
  mode: PrincipleMode;
  status: 'ACTIVE' | 'ARCHIVED';
  version: number;
  createdAt: string;
  /** 이 버전에서 바뀐 필드 이름들. 무엇이 달라졌는지 화면에서 그대로 보여 준다. */
  changedFields: string[];
  rules: PrincipleRule[];
}

export interface PrincipleHistoryData {
  items: PrincipleVersion[];
  nextCursor: string | null;
}

/* -------------------------------------------------------------- Decision */

export interface DecisionViolationProjection {
  ruleId: string;
  message: string;
  metricValue: number;
  threshold: number;
  severity: string;
}

export interface DecisionIssueProjection {
  code: string;
  message: string;
  source: string;
  ruleId?: string;
}

export type DecisionWarningProjection = DecisionIssueProjection;

export interface DecisionAbstentionProjection {
  code: string;
  component: string;
  disposition: 'ABSTAIN' | 'NOT_APPLICABLE';
  message: string;
  ruleId?: string;
}

/** riskItem은 metric/value/severity/source 구조다. label·unit·freshUntil은 계약에 없다. */
export interface DecisionRiskItemProjection {
  metric: string;
  value: number;
  severity: string;
  source: string;
  eventCodes?: string[];
  mappingVersion?: string;
  sourceRefs?: string[];
}

export interface RiskDecisionProjection {
  decisionId: string;
  evaluationId: string;
  decision: DecisionAction;
  canSubmitOrder: boolean;
  mode: PrincipleMode;
  portfolioSource: PortfolioSource;
  principleVersion: number;
  principleVersionId: string;
  catalogVersion: number;
  readinessPolicyVersion: string;
  schemaVersion: string;
  semanticInputHash: string;
  snapshotArtifactHash: string;
  validUntil: string;
  violations: DecisionViolationProjection[];
  issues: DecisionIssueProjection[];
  warnings: DecisionWarningProjection[];
  abstentions: DecisionAbstentionProjection[];
  riskItems: DecisionRiskItemProjection[];
}

/** GET /api/v1/decisions/{decisionId} — orderIntent는 응답에 포함되지 않는다. */
export interface DecisionProjection {
  decisionId: string;
  createdAt: string;
  enforcementAction: string;
  mode: PrincipleMode;
  portfolioSource: PortfolioSource;
  principleId: string;
  principleVersion: number;
  principleVersionId: string;
  validUntil: string;
  riskDecision: RiskDecisionProjection;
}

/** 판정과 제출이 같은 모양을 쓴다. 두 곳에서 따로 만들면 어긋난다. */
export interface OrderIntent {
  symbol: string;
  side: 'BUY' | 'SELL';
  orderType: 'MARKET' | 'LIMIT';
  /** 정수 원화. quantity * estimatedPrice와 정확히 일치해야 한다. */
  quantity: number;
  estimatedPrice: number;
  estimatedAmount: number;
  timeframe: '1d' | '60m';
  strategyId: string;
}

export interface EvaluateOrderRequest {
  principleId: string;
  portfolioSource: PortfolioSource;
  orderIntent: OrderIntent;
}

/* ------------------------------------------------------------ Brokerage */

/** GET /api/v1/brokerage/mock/accounts/{accountId}/buyable */
export interface MockBuyable {
  accountId: string;
  brokerageMode: 'KIS_MOCK';
  symbol: string;
  cashKrw: number;
  estimatedPrice: number;
  buyableAmountKrw: number;
  buyableQuantity: number;
  observedAt: string;
  sourceVersion: string;
}

export type OrderStatus =
  | 'SUBMITTED'
  | 'ACCEPTED'
  | 'PARTIALLY_FILLED'
  | 'FILLED'
  | 'CANCEL_REQUESTED'
  | 'CANCELLED'
  | 'REJECTED'
  | 'PENDING_RECONCILIATION';

/** GET /api/v1/brokerage/orders/{orderId} · POST .../cancel */
export interface OrderDetail {
  orderId: string;
  accountId: string;
  decisionId: string;
  brokerageMode: PortfolioSource;
  status: OrderStatus;
  submittedAt: string;
}

/** POST /api/v1/brokerage/mock/orders */
export interface MockOrderSubmitted {
  orderId: string;
  accountId: string;
  brokerageMode: 'KIS_MOCK';
  status: 'SUBMITTED' | 'ACCEPTED' | 'PENDING_RECONCILIATION';
  submittedAt: string;
}

export interface MockOrderRequest {
  decisionId: string;
  orderIntent: OrderIntent;
  /** 화면이 경고를 보여 주고 사용자가 받아들였다는 사실. 서버가 이걸 요구한다. */
  userAcknowledgement: { warningsAccepted: boolean };
}

export interface OrderFill {
  orderId: string;
  brokerageMode: 'KIS_MOCK' | 'INTERNAL_PAPER';
  execRefHash: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  fillQuantity: number;
  fillPriceKrw: number;
  fillAmountKrw: number;
  filledAt: string;
}

export interface OrderFillPage {
  items: OrderFill[];
  nextCursor: string | null;
}

/* ------------------------------------------------------------ Automation */

export type AutomationPresetId = 'conservative' | 'balanced' | 'aggressive' | 'custom';

export type AutomationBlocker =
  | 'ACCOUNT_NOT_CONFIGURED'
  | 'POLICY_NOT_CONFIGURED'
  | 'POLICY_VERSION_DRIFT'
  | 'PRINCIPLE_NOT_CONFIGURED'
  | 'REAL_TEAM_B_POINTER_INACTIVE'
  | 'RELEASE_BINDING_UNCLEAN'
  | 'CERTIFICATION_INVALID'
  | 'KILL_SWITCH_ACTIVE'
  | 'UNRESOLVED_RECONCILIATION'
  | 'CONTROL_HALTED'
  | 'BLOCKED_INCOMPLETE_RISK_BALANCE';

export type AutomationControlState = 'DISARMED' | 'ARMED' | 'HALTED';
export type AutomationProjectionState = AutomationControlState | 'RUNNING';
export type AutomationBrokerageMode = 'KIS_MOCK' | 'INTERNAL_PAPER';
export type AutomationExitReason =
  | 'STOP_LOSS'
  | 'MAX_HOLDING_SESSIONS'
  | 'MODEL_SELL'
  | 'TAKE_PROFIT';

export interface AutomationPolicyV2 {
  contractId: 'automation-policy.v1';
  policyId: string;
  version: number;
  presetId: AutomationPresetId;
  capitalLimitKrw: number;
  stopLossBps: number;
  takeProfitBps: number;
  /** 사용자가 원칙에서 고르는 동시 보유 상한. 리터럴로 굳히면 화면이 실제와 어긋난다. */
  maxOpenPositions: number;
  /** ATR 변동성 기반 사이징의 거래당 위험(자본 대비 bps). 100 = 1%. */
  riskPerTradeBps?: number;
  maxNewOrdersPerSession: number;
  evaluationTimeKst: string;
  buyCutoffTimeKst: string;
  /**
   * 당일 미체결을 정리하는 경계.
   *
   * 이 시스템은 정규장(09:00~15:30)만 운용한다. 2026-09-14 부터 열린 KRX 애프터마켓
   * (16:00~20:00)은 다루지 않는다 - KIS 모의계좌의 시간외 주문 지원이 확인되지 않았고
   * 인증 창도 09:10~15:00 이다.
   */
  cancelTimeKst: string;
  createdAt: string;
  updatedAt: string;
}

export interface AutomationStatusV2 {
  contractId: 'automation-status.v2';
  controlState: AutomationControlState;
  projectionState: AutomationProjectionState;
  controlVersion: number;
  brokerageMode: 'KIS_MOCK';
  accountId: string | null;
  policy: AutomationPolicyV2 | null;
  killSwitchActive: boolean;
  certificationStatus: 'NOT_REQUIRED_INTERNAL_PAPER' | 'REQUIRED' | 'VALID' | 'EXPIRED' | 'INVALID';
  openPositionCount: number;
  unresolvedReconciliation: boolean;
  canArm: boolean;
  blockers: AutomationBlocker[];
}

export interface PutAutomationPolicyV2Request {
  expectedVersion: number;
  capitalLimitKrw: number;
  stopLossBps: number;
  takeProfitBps: number;
}

export interface ArmAutomationV2Request {
  accountId: string;
  policyId: string;
  expectedPolicyVersion: number;
  expectedControlVersion: number;
}

export interface AutomationRunV2 {
  contractId: 'automation-run.v2';
  runId: string;
  sessionDate: string;
  state: string;
  brokerageMode: AutomationBrokerageMode;
  policyId: string | null;
  policyVersion: number | null;
  selectedSymbol: string | null;
  selectedSide: 'BUY' | 'SELL' | null;
  orderQuantity: number | null;
  filledQuantity: number | null;
  leavesQuantity: number | null;
  limitPriceKrw: number | null;
  estimatedAmountKrw: number | null;
  exitReason: AutomationExitReason | null;
  physicalSubmitCount: number;
  providerCalls: number;
  startedAt: string;
  updatedAt: string;
}

export interface AutomationRunPageV2 {
  items: AutomationRunV2[];
  nextCursor: string | null;
}

/* ------------------------------------------------------- Automation v3
 *
 * v3 는 v2 의 상위집합이 아니다. 실행과 상태는 필드가 늘었지만 **포지션 페이지에서는
 * `realizedSummary` 가 빠졌다.** 그래서 화면마다 필요한 쪽을 쓴다 — 실현손익 요약이 필요한
 * 현황은 v2 를, 청산 근거(ATR 추적손절·보유기간·AI 판단)가 필요한 자동운용은 v3 를 본다.
 */

/** v3 는 ATR 추적손절을 청산 사유로 추가한다. v2 계약에는 없다(스키마 확인함). */
export type AutomationExitReasonV3 = AutomationExitReason | 'ATR_TRAILING';

/** v3 차단 사유 17종. v2 의 11종에 여섯이 더 붙는다. */
export type AutomationBlockerV3 =
  | AutomationBlocker
  | 'POLICY_V3_REQUIRED'
  | 'LEGACY_POSITION_PRESENT'
  | 'MARKET_HISTORY_EMPTY'
  | 'MARKET_HISTORY_INSUFFICIENT'
  | 'MARKET_DATA_CATCHUP_REQUIRED'
  | 'AI_PROVIDER_NOT_READY';

export type MarketHistoryStatus = 'EMPTY' | 'PARTIAL' | 'READY' | 'CATCHUP_REQUIRED';

/** v3 정책은 v2 에 ATR 추적손절·보유기간·모델매도 네 값을 더한다. */
export interface AutomationPolicyV3 extends Omit<AutomationPolicyV2, 'contractId'> {
  contractId: 'automation-policy.v2';
  atrPeriod: number;
  /** 1000 = 1.0배. 100 단위로만 저장된다. */
  atrMultiplierMilli: number;
  maxHoldingSessions: number;
  modelSellEnabled: boolean;
}

export interface PutAutomationPolicyV3Request extends PutAutomationPolicyV2Request {
  atrPeriod: number;
  atrMultiplierMilli: number;
  maxHoldingSessions: number;
  modelSellEnabled: boolean;
  /** 동시 보유 상한(1~20). 서버가 생략을 허용하므로 옛 클라이언트도 깨지지 않는다. */
  maxOpenPositions?: number;
  /** ATR 사이징의 거래당 위험(10~300 bps). */
  riskPerTradeBps?: number;
}

export interface AutomationCapitalPolicy {
  contractId: 'automation-capital-policy.v1';
  version: number;
  reinvestRealizedPnl: boolean;
  cashBufferBps: 100;
  rebalanceDeviationBps: 200;
  minimumAdjustmentKrw: 10000;
  maxOrdersPerSession: 1 | 2 | 3;
  effectiveFromSession: string;
  transitionStartedAt: string;
}

export interface PutAutomationCapitalPolicyRequest {
  reinvestRealizedPnl: boolean;
  expectedVersion: number;
}

export interface AutomationCapitalPosition {
  symbol: string;
  currentQuantity: number;
  targetQuantity: number | null;
  currentMarketValueKrw: number | null;
  targetMarketValueKrw: number;
  currentWeightBps: number | null;
  targetWeightBps: number;
  valuationStatus: 'COMPLETE' | 'MISSING';
}

export interface AutomationCapitalStatus {
  contractId: 'automation-capital-status.v1';
  policyVersion: number;
  reinvestRealizedPnl: boolean;
  configuredCapitalKrw: number;
  realizedPnlSinceTransitionKrw: number;
  brokerBuyableCashKrw: number;
  botPositionMarketValueKrw: number;
  reservedBuyCashKrw: number;
  allocationCapKrw: number;
  investableCapKrw: number;
  availableBuyCashKrw: number;
  targetPerPositionKrw: number;
  existingBotPositionsAdopted: number;
  valuationMissingCount: number;
  unusedCashReason: string | null;
  positions: AutomationCapitalPosition[];
  asOf: string;
}

export interface AutomationStatusV3
  extends Omit<AutomationStatusV2, 'contractId' | 'blockers' | 'policy'> {
  contractId: 'automation-status.v3';
  appliedPolicyVersion?: number | null;
  policyRecoverySourceVersion?: number | null;
  nextRunAt?: string | null;
  policy: AutomationPolicyV3 | null;
  blockers: AutomationBlockerV3[];
  /** AI 판단 단계를 켜 두었나. 꺼져 있으면 실행에 판단 근거가 남지 않는다. */
  aiJudgementEnabled: boolean;
  thinkingLevel: 'minimal' | 'low' | 'medium';
  marketHistoryStatus: MarketHistoryStatus;
  /** 선택 계좌에서 청산 정책을 복원할 근거도 없는 포지션 수. */
  legacyOpenPositionCount: number;
}

export interface AutomationRunV3 extends Omit<AutomationRunV2, 'contractId' | 'exitReason'> {
  contractId: 'automation-run.v3';
  exitReason: AutomationExitReasonV3 | null;
  /** AI 판단에 실제로 쓰인 근거 수. 0 이면 근거 없이 넘어간 실행이다. */
  evidenceCount: number;
  evidenceSetSha256: string | null;
  aiSettingsSha256: string | null;
  judgeCallCount: number;
  groundingQueryCount: number;
  screeningProviderCallCount: number;
}

export interface AutomationRunPageV3 {
  items: AutomationRunV3[];
  nextCursor: string | null;
}

export interface AutomationPositionV3 {
  contractId: 'automation-position.v3';
  positionId: string;
  accountId: string;
  symbol: string;
  quantity: number;
  entryAverageFillPriceKrw: number;
  entrySession: string;
  expirySession: string | null;
  policyId: string;
  policyVersion: number;
  stopLossBps: number;
  takeProfitBps: number;
  status: 'OPEN' | 'EXIT_PENDING' | 'CLOSED' | 'HALTED_MISMATCH';
  exitReason: AutomationExitReasonV3 | null;
  /** 진입 뒤 최고가. ATR 추적손절의 기준점이다. */
  peakPriceKrw: number;
  /** 지금 걸려 있는 추적손절 가격. `null` 이면 아직 계산되지 않았다. */
  trailingStopKrw: number | null;
  atrPeriod: number;
  /** ATR 배수를 1000배 정수로 담는다. 2500 = 2.5배. */
  atrMultiplierMilli: number;
  atrAsOfSession: string | null;
  /** 이 세션을 넘기면 보유 기간 초과로 청산된다. */
  maxHoldingSessions: number;
  /** 모델이 매도 신호를 내면 따를 것인가. */
  modelSellEnabled: boolean;
  botOwned: true;
  shortAllowed: false;
  createdAt: string;
  closedAt: string | null;
}

export interface AutomationPositionPageV3 {
  items: AutomationPositionV3[];
}

/**
 * AI 판단이 실제로 무엇을 읽고 그렇게 정했는지.
 *
 * 인용문은 길이가 제한돼 있고(240자) 출처와 해시가 함께 온다 — 화면은 이것을 요약하지 않고
 * 그대로 보여 준다.
 */
export interface AutomationEvidenceV3 {
  citationId: string;
  symbol: string;
  sourceId: string;
  sourceType: 'OFFICIAL_PRIMARY' | 'REGISTERED_INDEPENDENT';
  sourceEventDate: string | null;
  boundedQuote: string;
  quoteSha256: string;
  uriSha256: string;
  /** 근거가 오래됐다는 표시. 서버가 붙인다. */
  ageWarning: boolean;
  verified: true;
}

export interface AutomationCandidateScreeningV3 {
  symbol: string;
  status: 'AVAILABLE' | 'ABSTAIN';
  verdict: 'VETO_BUY' | 'NO_VETO' | 'ABSTAIN';
  score: number;
  reason: string;
  evidence: AutomationEvidenceV3[];
}

/** 후보가 주문까지 가는 동안 지나는 단계. 서버 CHECK 와 같은 값이어야 한다. */
export type AutomationStageName =
  | 'OBSERVATION'
  | 'RULE_BUY'
  | 'LSTM_VETO'
  | 'ATR_HISTORY'
  | 'QUOTE_SAFETY'
  | 'NEWS_DISCLOSURE'
  | 'AI_JUDGE'
  | 'RISK_ENGINE'
  | 'ORDER';

export interface AutomationStageOutcome {
  stage: AutomationStageName;
  symbol: string;
  outcome: 'PASS' | 'DROPPED';
  reasonCode: string | null;
  reasonDetail: string | null;
}

export interface AutomationRunDetailV3 {
  contractId: 'automation-run-detail.v3';
  run: AutomationRunV3;
  candidateScreenings: AutomationCandidateScreeningV3[];
  /** 서버가 아직 안 내려줄 수 있다. 없으면 퍼널을 감추고 기존 표시로 되돌린다. */
  stageOutcomes?: AutomationStageOutcome[];
}

export interface AutomationPositionV2 {
  contractId: 'automation-position.v2';
  positionId: string;
  accountId: string;
  symbol: string;
  quantity: number;
  entryAverageFillPriceKrw: number;
  entrySession: string;
  /** 보유 만기가 아직 정해지지 않은 포지션에서는 비어 있다. */
  expirySession: string | null;
  policyId: string;
  policyVersion: number;
  stopLossBps: number;
  takeProfitBps: number;
  status: 'OPEN' | 'EXIT_PENDING' | 'CLOSED' | 'HALTED_MISMATCH';
  exitReason: AutomationExitReason | null;
  exitAverageFillPriceKrw: number | null;
  realizedPnlKrw: number | null;
  botOwned: true;
  shortAllowed: false;
  createdAt: string;
  closedAt: string | null;
}

export interface AutomationRealizedSummaryV2 {
  closedPositionCount: number;
  realizedPnlKrw: number;
  realizedGrossKrw: number;
  winningPositionCount: number;
  losingPositionCount: number;
  evidenceMode: 'KIS_MOCK';
  performanceClaimAllowed: false;
}

export interface AutomationPositionPageV2 {
  realizedSummary: AutomationRealizedSummaryV2;
  items: AutomationPositionV2[];
  nextCursor: string | null;
}

export interface AutomationControlV1 {
  contractId: 'automation-control.v1';
  controlState: AutomationControlState;
  projectionState: AutomationProjectionState;
  version: number;
  brokerageMode: AutomationBrokerageMode;
  principleId: string;
  strategyId: string;
  killSwitchActive: boolean;
  certificationStatus: AutomationStatusV2['certificationStatus'];
}

/* ---------------------------------------------------------------- Signal */

export type SignalProducer = 'RULE_BASELINE' | 'LSTM' | 'LIGHTGBM' | 'HMM';

export type AbstainReason =
  | 'ARTIFACT_DRIFT'
  | 'CALIBRATION_FAILED'
  | 'MISSING_EVIDENCE'
  | 'POSTERIOR_BELOW_THRESHOLD'
  | 'PRODUCER_FAILED'
  | 'STALE_EVIDENCE'
  | 'UNIDENTIFIABLE_OUTPUT';

export type RegimeState = 'NORMAL' | 'SIDEWAYS' | 'HIGH_VOLATILITY' | 'RISK_OFF' | 'RISK_ON';

export interface ReturnForecast {
  horizonSessions: number; targetSession: string; expectedReturn: number;
  forecastClose: number; trainSamples: number; trainedThrough: string;
}

export interface PredictiveAvailable {
  returnForecasts?: ReturnForecast[];
  estimator?: string;
  sourceSession?: string;
  qualityStatus?: string;
  status: 'AVAILABLE';
  producer: SignalProducer;
  sourceWorkspace: string;
  asOf: string;
  signal: 'BUY' | 'HOLD' | 'SELL';
  predictedReturn?: number | null;
  modelReportId?: string;
  modelVersion?: string;
  featureSummary?: string[];
}

export interface RegimeAvailable {
  status: 'AVAILABLE';
  producer: 'HMM';
  sourceWorkspace: string;
  asOf: string;
  state: RegimeState;
  modelReportId?: string;
  modelVersion?: string;
}

export interface ComponentAbstain {
  status: 'ABSTAIN';
  producer: SignalProducer;
  sourceWorkspace: string;
  reason: AbstainReason;
  modelReportId?: string;
  modelVersion?: string;
  warnings?: string[];
}

export type PredictiveComponent = PredictiveAvailable | ComponentAbstain;
export type RegimeComponent = RegimeAvailable | ComponentAbstain;

/** composite AVAILABLE에는 asOf가 없다. 최상위 asOf만 존재한다. */
export type CompositeSignal =
  | { status: 'AVAILABLE'; signal: 'BUY' | 'HOLD' | 'SELL'; predictedReturn?: number | null }
  | { status: 'ABSTAIN'; reason: 'REQUIRED_COMPONENT_UNAVAILABLE' };

export interface SignalV3Runtime {
  symbol: string;
  timeframe: string;
  asOf?: string;
  modelReportId?: string;
  composite: CompositeSignal;
  components: {
    ruleBaseline: PredictiveComponent;
    lstm: PredictiveComponent;
    lightgbm: PredictiveComponent;
    hmmRegime: RegimeComponent;
  };
  warnings: string[];
}

/* ------------------------------------------------------------------- RAG */

export type RagGenerationStatus =
  | 'ANSWERED'
  | 'RETRIEVAL_ONLY'
  | 'RETRIEVAL_FAILURE'
  | 'BLOCKED_SENSITIVE'
  | 'BLOCKED_ADVICE'
  | 'GENERATION_UNAVAILABLE';

export interface RagPublicCitation {
  citationId: string;
  sourceId: string;
  title: string;
  sectionTitle: string;
  canonicalUrl: string;
}

export interface RagAnswerProjection {
  requestId: string;
  answerId: string;
  generationStatus: RagGenerationStatus;
  answer: string | null;
  citationCoverage: number;
  retrievalFailure: boolean;
  citations: RagPublicCitation[];
  guardrailFlags: string[];
}

/* --------------------------------------------------------- RAG v2 공개 계약 */

/**
 * v2 표면은 공통 `ApiEnvelope`를 쓰지 않고 DTO를 그대로 돌려준다.
 * 오류도 `{ code, message, requestId }` 본문이다. `apiFetchBare`가 그 차이를 흡수한다.
 */
export interface RagV2CorpusStatus {
  state: string;
  publicCorpusVersion: string;
  privateOverlayState: string;
  progressPercent: number;
  failureCode: string | null;
  /** Retired quotas stay null; generationUsedToday counts reservations for telemetry only. */
  generationDailyCap: number | null;
  generationUsedToday: number | null;
  generationRemaining: number | null;
  /** Owner settings expose credential suffixes only. */
  strongLlmProvider: string | null;
  strongLlmFallbackProvider: string | null;
  strongLlmModelId: string | null;
  strongLlmFallbackModelId: string | null;
  strongLlmBaseUrl: string | null;
  strongLlmFallbackBaseUrl: string | null;
  strongLlmAnswerLanguage: string | null;
  strongLlmDailyGenerateCallCap: number | null;
  strongLlmKeyLast4: string | null;
  strongLlmFallbackKeyLast4: string | null;
}

/** 설정 쓰기 요청. apiKey는 쓰기 전용이며 어떤 응답에도 담기지 않는다. */
export interface PutStrongLlmSettingsRequest {
  provider: string;
  fallbackProvider: string | null;
  modelId: string | null;
  fallbackModelId: string | null;
  baseUrl: string | null;
  fallbackBaseUrl: string | null;
  answerLanguage: string;
  dailyGenerateCallCap: number;
  /** 생략하면 저장된 키를 그대로 둔다. 빈 문자열이면 지운다. */
  apiKey?: string;
  fallbackApiKey?: string;
}

export interface RagV2EffectiveConsent {
  contractId: string;
  schemaVersion: number;
  consentEventId: string;
  effective: boolean;
  policyDigest: string;
  processorSetDigest: string;
  state: string;
}

export interface RagV2ExternalConsentRequest {
  contractId: 's4-rag-v2-external-consent-v1';
  schemaVersion: 1;
  consentType: 'EXTERNAL_AI_RAG_V2';
  action: 'GRANT' | 'REVOKE';
  disclosureDigest: string;
  policyDigest: string;
  processorSetDigest: string;
}

export type RagV2CitationKind = 'PUBLIC_WEB' | 'LOCAL_DOCUMENT';

export interface RagV2Citation {
  citationId: string;
  citationKind: RagV2CitationKind;
  sourceId: string;
  title: string;
  canonicalUrl: string | null;
  locator: { page?: number; section?: string } | null;
  chunkRevisionId: string;
  sourceRevisionId: string;
  generationId: string;
}

export interface RagV2Answer {
  requestId: string;
  answerId: string | null;
  generationStatus: RagGenerationStatus;
  answer: string | null;
  citationCoverage: number;
  citations: RagV2Citation[];
  retrievalFailure: boolean;
  guardrailFlags: string[];
}

export interface RagV2HistoryPage {
  items: {
    answerId: string;
    createdAt: string;
    expiresAt: string;
    generationStatus: RagGenerationStatus;
  }[];
  nextCursor: string | null;
}

export interface RagV2HistoryDetail {
  answerId: string;
  question: string;
  answer: string | null;
  generationStatus: RagGenerationStatus;
  citations: RagV2Citation[];
  createdAt: string;
  expiresAt: string;
}

export type WorldNewsPublicationStatus = 'VERIFIED' | 'MISSING' | 'CONFLICT';
export type WorldNewsCollectionState = 'COMPLETE' | 'PARTIAL' | 'COLLECTION_FAILED' | 'NOT_COLLECTED';

export interface WorldNewsItem {
  documentId: string;
  documentVersionId: string;
  sourceId: string;
  provider: 'GDELT_GQG' | 'GDELT_GEMG' | 'FINNHUB_MARKET_NEWS';
  providerDocumentId: string | null;
  canonicalUrl: string;
  republicationOfDocumentId: string | null;
  identityStatus: 'VERIFIED' | 'PROVIDER_ID_CONFLICT' | 'URL_HASH_CONFLICT';
  title: string | null;
  boundedQuote: string | null;
  boundedPassage: string | null;
  language: string;
  publishedAt: string | null;
  publicationStatus: WorldNewsPublicationStatus;
  providerObservedAt: string;
  firstSeenAt: string;
  availableAt: string;
  rightsProfile: 'GDELT_METADATA_QUOTE' | 'FINNHUB_PERSONAL_LOCAL';
  externalLlmAllowed: boolean;
  lookupAllowed: boolean;
  ragRetrievalAllowed: boolean;
  promptUntrusted: true;
  collectionStatus: WorldNewsCollectionState;
  contentSha256: string;
  versionSha256: string;
}

export interface WorldNewsPage {
  items: WorldNewsItem[];
  collections: {
    provider: WorldNewsItem['provider'];
    collectionStatus: WorldNewsCollectionState;
    startedAt: string;
    completedAt: string | null;
    observedThrough: string | null;
    itemCount: number;
    errorCode: string | null;
  }[];
  asOf: string;
  decisionAuthority: 'NONE';
  signalAuthority: 'NONE';
  orderAuthority: 'NONE';
}

export interface RagSourceResponse {
  sourceId: string;
  title: string;
  institution: string;
  topic: string;
  attribution: string;
  canonicalUrl: string;
  lastCheckedAt: string | null;
}

/** GET /api/v1/rag/sources는 배열이 아니라 { items } 객체를 반환한다. */
export interface RagSourceListResponse {
  items: RagSourceResponse[];
}

export interface RagAskRequest {
  question: string;
  answerMode: 'CONCISE' | 'DETAILED';
  topics?: ('API' | 'DATA' | 'FINANCIAL_ENGINEERING' | 'METHODOLOGY' | 'PRODUCT_RISK' | 'RISK')[];
  relatedSymbols?: string[];
}

/* ------------------------------------------------------- Dashboard 계약 */

export type DashboardViewState = 'READY' | 'EMPTY' | 'STALE';
export type DashboardEvidenceMode = 'STORED_RUNTIME' | 'REAL_ARTIFACT' | 'SYNTHETIC_DEMO';

/**
 * 네 Dashboard endpoint의 공통 봉투.
 * viewState와 evidenceMode를 서버가 이미 판정해서 내려주므로 프론트가 다시 계산하지 않는다.
 * performanceClaimAllowed는 계약상 항상 false다.
 */
export interface DashboardEnvelope<TView> {
  viewState: DashboardViewState;
  asOf: string | null;
  freshUntil: string | null;
  evidenceMode: DashboardEvidenceMode;
  performanceClaimAllowed: false;
  view: TView | null;
}

/** GET /api/v1/dashboard/{model-evaluations|backtests}/latest */
export interface LatestArtifactRun {
  runId: string;
  fixtureClass: string;
  asOf: string;
}

export interface RecentRiskResult {
  decisionId: string;
  action: DecisionAction;
  symbol: string;
  asOf: string;
  validUntil: string;
}

export interface RecentRiskResultList {
  items: RecentRiskResult[];
}

export interface JournalLinks {
  decisionId: string | null;
  backtestRunId: string | null;
  ragAnswerId: string | null;
  orderId: string | null;
  automationRunId: string | null;
}

export interface JournalEntry {
  contractId: 'journal.v1';
  journalId: string;
  ownerScope: string;
  title: string;
  content: string;
  tags: string[];
  links: JournalLinks;
  version: number;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface JournalPage {
  items: JournalEntry[];
  nextCursor: string | null;
}

/** dashboard-risk-result.v1 */
export interface DashboardRiskResultView {
  decisionId: string;
  action: DecisionAction;
  reasons: string[];
  principles: string[];
  riskItems: { code: string; severity: 'INFO' | 'WARN' | 'BLOCK'; summary: string }[];
}

export type DashboardModelId = 'BASELINE' | 'LSTM' | 'LIGHTGBM';

export interface DashboardMetrics {
  cagr: number | null;
  mdd: number | null;
  sharpe: number | null;
  sortino: number | null;
  var95: number | null;
  cvar95: number | null;
}

/** dashboard-model-evaluation.v1 */
export interface DashboardModelEvaluationView {
  runId: string;
  models: { modelId: DashboardModelId; status: 'AVAILABLE' | 'ABSTAIN'; metrics: DashboardMetrics }[];
  timeline: { at: string; value: number }[];
  sourceRunIds: string[];
}

export type DashboardStrategyName = 'Baseline' | 'Guide' | 'Strict';

/** dashboard-backtest.v1 — strategies는 정확히 Baseline, Guide, Strict 3개 고정 순서다. */
export interface DashboardBacktestView {
  runId: string;
  fixtureClass: 'REAL_ARTIFACT' | 'SYNTHETIC_FAKE_E2E';
  strategies: {
    strategy: DashboardStrategyName;
    metrics: DashboardMetrics;
    curve: { at: string; value: number }[];
  }[];
  heatmap: { month: string; return: number }[];
  metricCards: { metric: string; value: number | null }[];
  projectionHash: string;
}

export interface OwnerPerformanceReport {
  report: {
    contractId: 'owner-performance-report.v1';
    reportId: string;
    reportVersion: number;
    supersedesReportId: string | null;
    correctionOfReportId: string | null;
    generatedAt: string;
    sourceStart: string;
    sourceEnd: string;
    sourceGenerationSha256: string;
    modelSha256: string;
    principleVersionId: string;
    principleVersion: number;
    costBps: number;
    modelAdoption: {
      state: 'RESEARCH_EVALUATED' | 'SHADOW_DAILY' | 'ACCEPTANCE_REVIEWED' | 'CURRENT_MODEL';
      candidateId: string | null;
      currentModel: string;
      predictionAccepted: boolean;
      performanceAccepted: boolean;
      automaticActivation: false;
      blockers: string[];
    };
    sections: {
      recalculatedBacktest: {
        status: 'RECALCULATED';
        baselineNetReturn: number | null;
        guideNetReturn: number | null;
        strictNetReturn: number | null;
      };
      fixedDailyForecast: {
        status: 'NOT_AVAILABLE' | 'PARTIAL' | 'REALIZED';
        totalCount: number;
        realizedCount: number;
        pendingCount: number;
        mae: number | null;
        rmse: number | null;
        bias: number | null;
      };
      actualTrading: {
        status: 'NO_REALIZED_TRADES' | 'REALIZED';
        closedPositionCount: number;
        openPositionCount: number;
        realizedPnlKrw: number;
        unrealizedStatus: 'NONE' | 'OPEN';
      };
    };
  };
  lastRefreshStatus: 'SUCCESS' | 'FAILED_LAST_SUCCESS_PRESERVED';
  lastFailureCode: string | null;
  lastFailureAt: string | null;
}

export type RagSourceClassification = 'OFFICIAL' | 'SCHOLARLY' | 'INTERNAL_PAPER';

/** dashboard-rag-sources.v1 — citationCoverage는 여기 없고 ask 응답에 있다. */
export interface DashboardRagSourcesView {
  answerId: string;
  topSources: { sourceId: string; title: string; classification: RagSourceClassification; summary: string }[];
  expandableSources: { sourceId: string; title: string; classification: RagSourceClassification; summary: string }[];
}
