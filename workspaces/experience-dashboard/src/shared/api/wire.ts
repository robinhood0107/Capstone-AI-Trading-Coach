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

export type KillSwitchReasonClass =
  | 'USER_MANUAL_STOP'
  | 'OPERATOR_MANUAL_STOP'
  | 'DATA_FRESHNESS_STOP'
  | 'BROKERAGE_FAILURE_STOP'
  | 'DEMO_SAFETY_STOP'
  | 'ADMIN_RESUME'
  | 'INITIAL_STATE';

export interface KillSwitchState {
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

export interface EvaluateOrderRequest {
  principleId: string;
  portfolioSource: PortfolioSource;
  orderIntent: {
    symbol: string;
    side: 'BUY' | 'SELL';
    orderType: 'MARKET' | 'LIMIT';
    /** 정수 원화. quantity * estimatedPrice와 정확히 일치해야 한다. */
    quantity: number;
    estimatedPrice: number;
    estimatedAmount: number;
    timeframe: '1d' | '60m';
    strategyId: string;
  };
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
  maxOpenPositions: 5;
  maxNewOrdersPerSession: 1;
  evaluationTimeKst: '09:30';
  buyCutoffTimeKst: '09:40';
  cancelTimeKst: '15:20';
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

export interface PredictiveAvailable {
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
  /** Daily generation usage; all fields are null in retrieval-only mode. */
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

export type RagSourceClassification = 'OFFICIAL' | 'SCHOLARLY' | 'INTERNAL_PAPER';

/** dashboard-rag-sources.v1 — citationCoverage는 여기 없고 ask 응답에 있다. */
export interface DashboardRagSourcesView {
  answerId: string;
  topSources: { sourceId: string; title: string; classification: RagSourceClassification; summary: string }[];
  expandableSources: { sourceId: string; title: string; classification: RagSourceClassification; summary: string }[];
}
