import { apiFetch, apiFetchBare, newIdempotencyKey } from './client';
import type { ApiResult } from './envelope';
import type {
  ArmAutomationV2Request,
  AutomationControlV1,
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
  EvaluateOrderRequest,
  KillSwitchState,
  LatestArtifactRun,
  JournalEntry,
  JournalPage,
  InstrumentDisplayCatalog,
  RecentRiskResult,
  RecentRiskResultList,
  LoginResponse,
  MockBalance,
  MockBuyable,
  MockOrderRequest,
  MockOrderSubmitted,
  OrderDetail,
  OrderFillPage,
  PortfolioRisk,
  PrincipleCreateRequest,
  PrincipleCurrent,
  PrincipleHistoryData,
  PrincipleOwnerListData,
  PrinciplePresetListData,
  PrincipleUpdateRequest,
  PutAutomationPolicyV2Request,
  PutStrongLlmSettingsRequest,
  RagAnswerProjection,
  RagAskRequest,
  RagSourceListResponse,
  RagV2Answer,
  RagV2CorpusStatus,
  RagV2EffectiveConsent,
  RagV2ExternalConsentRequest,
  RagV2HistoryDetail,
  RagV2HistoryPage,
  SignalV3Runtime,
  SystemHealthResponse,
} from './wire';

/**
 * Dashboard가 호출하는 endpoint 목록.
 * 경로와 응답 타입은 contracts/openapi/openapi.json 에서 검증했다.
 *
 * 주의: /api/v1/dashboard/* 는 query parameter를 하나라도 붙이면 VALIDATION_ERROR다.
 */
export const api = {
  /* -------------------------------------------------------------- 인증 */
  login(username: string, password: string): Promise<ApiResult<LoginResponse>> {
    return apiFetch<LoginResponse>('/api/v1/auth/login', {
      method: 'POST',
      body: { username, password },
      anonymous: true,
    });
  },

  /* -------------------------------------------------------------- 상태 */
  health(): Promise<ApiResult<SystemHealthResponse>> {
    return apiFetch<SystemHealthResponse>('/api/v1/system/health');
  },

  riskPortfolio(): Promise<ApiResult<PortfolioRisk>> {
    return apiFetch<PortfolioRisk>('/api/v1/risk/portfolio');
  },

  /** 이 종목을 지금 얼마나 살 수 있나. 주문 관문 G3 이 쓴다. */
  mockBuyable(accountId: string, symbol: string): Promise<ApiResult<MockBuyable>> {
    return apiFetch<MockBuyable>(
      `/api/v1/brokerage/mock/accounts/${encodeURIComponent(accountId)}/buyable?symbol=${encodeURIComponent(symbol)}`,
    );
  },

  mockFills(accountId: string, from: string, to: string): Promise<ApiResult<OrderFillPage>> {
    return apiFetch<OrderFillPage>(
      `/api/v1/brokerage/mock/accounts/${encodeURIComponent(accountId)}/fills` +
        `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
    );
  },

  order(orderId: string): Promise<ApiResult<OrderDetail>> {
    return apiFetch<OrderDetail>(`/api/v1/brokerage/orders/${encodeURIComponent(orderId)}`);
  },

  /**
   * 모의계좌에 주문을 낸다.
   *
   * **멱등키는 호출부가 준다.** 여기서 만들면 사용자가 수량을 고쳐 다시 제출할 때 같은 키가
   * 재사용돼 두 번째 주문이 조용히 무시된다. 확인 화면에 들어갈 때마다 새로 발급한다.
   */
  submitMockOrder(
    request: MockOrderRequest,
    idempotencyKey: string,
  ): Promise<ApiResult<MockOrderSubmitted>> {
    return apiFetch<MockOrderSubmitted>('/api/v1/brokerage/mock/orders', {
      method: 'POST',
      body: request,
      idempotencyKey,
    });
  },

  cancelOrder(orderId: string, idempotencyKey: string): Promise<ApiResult<OrderDetail>> {
    return apiFetch<OrderDetail>(
      `/api/v1/brokerage/orders/${encodeURIComponent(orderId)}/cancel`,
      { method: 'POST', idempotencyKey },
    );
  },

  mockBalance(accountId: string): Promise<ApiResult<MockBalance>> {
    return apiFetch<MockBalance>(
      `/api/v1/brokerage/mock/accounts/${encodeURIComponent(accountId)}/balances`,
    );
  },

  instrumentDisplayCatalog(): Promise<ApiResult<InstrumentDisplayCatalog>> {
    return apiFetch<InstrumentDisplayCatalog>('/api/v1/instruments/display');
  },

  killSwitch(): Promise<ApiResult<KillSwitchState>> {
    return apiFetch<KillSwitchState>('/api/v1/risk/kill-switch');
  },

  /**
   * Kill Switch 를 켜거나 끈다.
   *
   * **정지는 USER 도 할 수 있지만 해제는 ADMIN 만 된다**
   * (`KillSwitchTransitionPolicy.kt:22`). USER 가 해제를 시도하면 403 이 온다 — 화면에서
   * 먼저 막되, 서버가 최종 판단이라는 사실은 바뀌지 않는다.
   */
  changeKillSwitch(active: boolean, reason?: string): Promise<ApiResult<KillSwitchState>> {
    return apiFetch<KillSwitchState>('/api/v1/risk/kill-switch', {
      method: 'POST',
      body: reason ? { active, reason } : { active },
      idempotencyKey: newIdempotencyKey('kill-switch'),
    });
  },

  automationStatusV2(): Promise<ApiResult<AutomationStatusV2>> {
    return apiFetch<AutomationStatusV2>('/api/v2/automation/status');
  },

  putAutomationPolicyV2(
    request: PutAutomationPolicyV2Request,
  ): Promise<ApiResult<AutomationPolicyV2>> {
    return apiFetch<AutomationPolicyV2>('/api/v2/automation/policy', {
      method: 'PUT',
      body: request,
      idempotencyKey: newIdempotencyKey('automation-policy'),
    });
  },

  armAutomationV2(request: ArmAutomationV2Request): Promise<ApiResult<AutomationStatusV2>> {
    return apiFetch<AutomationStatusV2>('/api/v2/automation/arm', {
      method: 'POST',
      body: request,
      idempotencyKey: newIdempotencyKey('automation-arm-v2'),
    });
  },

  /**
   * v1 자동운용 통제 상태.
   *
   * v2 status 를 두고도 이걸 쓰는 이유는 하나다 — **`strategyId` 는 여기에만 있다.**
   * 주문을 낼 때 계약이 요구하는데 v2/v3 status 에는 그 필드가 없다(계약 확인함).
   */
  automationControlV1(): Promise<ApiResult<AutomationControlV1>> {
    return apiFetch<AutomationControlV1>('/api/v1/automation/status');
  },

  disarmAutomation(expectedVersion: number): Promise<ApiResult<AutomationControlV1>> {
    return apiFetch<AutomationControlV1>('/api/v1/automation/disarm', {
      method: 'POST',
      body: { expectedVersion },
      idempotencyKey: newIdempotencyKey('automation-disarm'),
    });
  },

  automationRunsV2(size = 20): Promise<ApiResult<AutomationRunPageV2>> {
    return apiFetch<AutomationRunPageV2>(`/api/v2/automation/runs?size=${size}`);
  },

  automationPositionsV2(): Promise<ApiResult<AutomationPositionPageV2>> {
    return apiFetch<AutomationPositionPageV2>('/api/v2/automation/positions');
  },

  /* -------------------------------------------------------------- 원칙 */
  principlePresets(): Promise<ApiResult<PrinciplePresetListData>> {
    return apiFetch<PrinciplePresetListData>('/api/v1/principle-presets');
  },

  principles(): Promise<ApiResult<PrincipleOwnerListData>> {
    return apiFetch<PrincipleOwnerListData>('/api/v1/principles');
  },

  principle(principleId: string): Promise<ApiResult<PrincipleCurrent>> {
    return apiFetch<PrincipleCurrent>(`/api/v1/principles/${encodeURIComponent(principleId)}`);
  },

  /** 저장할 때마다 한 버전씩 쌓인다. 무엇이 언제 바뀌었는지 되짚는 용도다. */
  principleVersions(principleId: string): Promise<ApiResult<PrincipleHistoryData>> {
    return apiFetch<PrincipleHistoryData>(
      `/api/v1/principles/${encodeURIComponent(principleId)}/versions`,
    );
  },

  /** 원칙을 처음 만든다. 이게 없으면 원칙이 하나도 없는 계정은 주문 검토를 쓸 수 없다. */
  createPrinciple(request: PrincipleCreateRequest): Promise<ApiResult<PrincipleCurrent>> {
    return apiFetch<PrincipleCurrent>('/api/v1/principles', { method: 'POST', body: request });
  },

  /** expectedVersion 기반 CAS. 409가 오면 재조회 후 사용자가 다시 선택하게 한다. */
  updatePrinciple(
    principleId: string,
    request: PrincipleUpdateRequest,
  ): Promise<ApiResult<PrincipleCurrent>> {
    return apiFetch<PrincipleCurrent>(`/api/v1/principles/${encodeURIComponent(principleId)}`, {
      method: 'PUT',
      body: request,
    });
  },

  /* ------------------------------------------------------------ 판정 */
  decision(decisionId: string): Promise<ApiResult<DecisionProjection>> {
    return apiFetch<DecisionProjection>(`/api/v1/decisions/${encodeURIComponent(decisionId)}`);
  },

  /** 실패해도 자동 재시도하지 않는다. 같은 키로 다시 보내는 것은 사용자가 선택한다. */
  evaluateOrder(request: EvaluateOrderRequest): Promise<ApiResult<DecisionProjection>> {
    return apiFetch<DecisionProjection>('/api/v1/decisions/evaluate-order', {
      method: 'POST',
      body: request,
      idempotencyKey: newIdempotencyKey('evaluate-order'),
    });
  },

  /* ------------------------------------------------------------ 신호 */
  signal(symbol: string): Promise<ApiResult<SignalV3Runtime>> {
    return apiFetch<SignalV3Runtime>(`/api/v3/signals/${encodeURIComponent(symbol)}`);
  },

  /* -------------------------------------------------------------- RAG */
  ragSources(): Promise<ApiResult<RagSourceListResponse>> {
    return apiFetch<RagSourceListResponse>('/api/v1/rag/sources');
  },

  ragAsk(request: RagAskRequest): Promise<ApiResult<RagAnswerProjection>> {
    return apiFetch<RagAnswerProjection>('/api/v1/rag/ask', {
      method: 'POST',
      body: request,
      idempotencyKey: newIdempotencyKey('rag-ask'),
    });
  },

  /* ----------------------------------------------------------- RAG v2 */
  ragV2CorpusStatus(): Promise<RagV2CorpusStatus> {
    return apiFetchBare<RagV2CorpusStatus>('/api/v2/rag/corpus-status');
  },

  ragV2Consent(): Promise<RagV2EffectiveConsent> {
    return apiFetchBare<RagV2EffectiveConsent>('/api/v2/rag/consent');
  },

  ragV2RecordConsent(request: RagV2ExternalConsentRequest): Promise<void> {
    return apiFetchBare<void>('/api/v2/rag/consents', { method: 'POST', body: request });
  },

  ragV2Ask(request: RagAskRequest): Promise<RagV2Answer> {
    return apiFetchBare<RagV2Answer>('/api/v2/rag/ask', { method: 'POST', body: request });
  },

  ragV2History(limit = 5): Promise<RagV2HistoryPage> {
    return apiFetchBare<RagV2HistoryPage>(`/api/v2/rag/history?limit=${limit}`);
  },

  ragV2HistoryDetail(answerId: string): Promise<RagV2HistoryDetail> {
    return apiFetchBare<RagV2HistoryDetail>(`/api/v2/rag/history/${encodeURIComponent(answerId)}`);
  },

  /** 저장된 질문 하나를 지운다. 되돌릴 수 없으므로 화면에서 한 번 더 확인받는다. */
  ragV2DeleteHistory(answerId: string): Promise<void> {
    return apiFetchBare<void>(`/api/v2/rag/history/${encodeURIComponent(answerId)}`, {
      method: 'DELETE',
    });
  },

  /**
   * 답변이 도움이 됐는지 남긴다.
   *
   * 경로가 v1 뿐이다(v2 에는 없다). 답변 id 체계는 두 버전이 같으므로 v2 로 받은 답변에도
   * 그대로 쓴다.
   */
  ragFeedback(answerId: string, helpful: boolean): Promise<ApiResult<unknown>> {
    return apiFetch(`/api/v1/rag/answers/${encodeURIComponent(answerId)}/feedback`, {
      method: 'POST',
      body: { helpful },
    });
  },

  /* ------------------------------------------------------ Strong LLM 설정 */
  /**
   * 응답 본문이 없다. 키를 담을 수 있는 응답을 아예 만들지 않는 것이 키를 응답에서 지우는
   * 것보다 확실하다. 저장된 값은 corpus-status가 돌려준다.
   */
  putStrongLlmSettings(request: PutStrongLlmSettingsRequest): Promise<void> {
    return apiFetchBare<void>('/api/v2/strong-llm/settings', { method: 'PUT', body: request });
  },

  /* -------------------------------------------- Dashboard ViewModel 4종 */
  dashboardRiskResult(
    decisionId: string,
  ): Promise<ApiResult<DashboardEnvelope<DashboardRiskResultView>>> {
    return apiFetch(`/api/v1/dashboard/risk-results/${encodeURIComponent(decisionId)}`);
  },

  dashboardLatestRiskResult(): Promise<ApiResult<RecentRiskResult>> {
    return apiFetch('/api/v1/dashboard/risk-results/latest');
  },

  dashboardRecentRiskResults(): Promise<ApiResult<RecentRiskResultList>> {
    return apiFetch('/api/v1/dashboard/risk-results/recent');
  },

  journals(): Promise<ApiResult<JournalPage>> {
    return apiFetch('/api/v1/journals?size=20');
  },

  createJournal(request: { title: string; content: string; tags: string[] }): Promise<ApiResult<JournalEntry>> {
    return apiFetch('/api/v1/journals', {
      method: 'POST',
      body: { ...request, links: {} },
      idempotencyKey: newIdempotencyKey('journal-create'),
    });
  },

  updateJournal(
    journalId: string,
    request: { expectedVersion: number; title: string; content: string; tags: string[] },
  ): Promise<ApiResult<JournalEntry>> {
    return apiFetch(`/api/v1/journals/${encodeURIComponent(journalId)}`, {
      method: 'PATCH',
      body: { ...request, links: {} },
      idempotencyKey: newIdempotencyKey('journal-update'),
    });
  },

  deleteJournal(journalId: string, expectedVersion: number): Promise<ApiResult<JournalEntry>> {
    return apiFetch(`/api/v1/journals/${encodeURIComponent(journalId)}`, {
      method: 'DELETE',
      body: { expectedVersion },
      idempotencyKey: newIdempotencyKey('journal-delete'),
    });
  },

  dashboardLatestRun(kind: 'model-evaluations' | 'backtests'): Promise<ApiResult<LatestArtifactRun>> {
    return apiFetch<LatestArtifactRun>(`/api/v1/dashboard/${kind}/latest`);
  },

  dashboardModelEvaluation(
    runId: string,
  ): Promise<ApiResult<DashboardEnvelope<DashboardModelEvaluationView>>> {
    return apiFetch(`/api/v1/dashboard/model-evaluations/${encodeURIComponent(runId)}`);
  },

  dashboardBacktest(runId: string): Promise<ApiResult<DashboardEnvelope<DashboardBacktestView>>> {
    return apiFetch(`/api/v1/dashboard/backtests/${encodeURIComponent(runId)}`);
  },

  dashboardRagSources(
    answerId: string,
  ): Promise<ApiResult<DashboardEnvelope<DashboardRagSourcesView>>> {
    return apiFetch(`/api/v1/dashboard/rag-sources/${encodeURIComponent(answerId)}`);
  },
};

/** 서버가 강제하는 ID 형식. 화면에서 미리 걸러 불필요한 400을 줄인다. */
export const ID_PATTERN = {
  principleId: /^prc_[0-9a-f]{32}$/,
  decisionId: /^dec_[A-Za-z0-9_-]{8,96}$/,
  runId: /^(run|demo)_[A-Za-z0-9_-]{8,96}$/,
  answerId: /^rag_[A-Za-z0-9_-]{12,96}$/,
  symbol: /^[0-9]{6}$/,
} as const;
