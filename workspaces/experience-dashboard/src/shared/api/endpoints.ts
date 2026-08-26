import { apiFetch, newIdempotencyKey } from './client';
import type { ApiResult } from './envelope';
import type {
  DashboardBacktestView,
  DashboardEnvelope,
  DashboardModelEvaluationView,
  DashboardRagSourcesView,
  DashboardRiskResultView,
  DecisionProjection,
  EvaluateOrderRequest,
  KillSwitchState,
  LoginResponse,
  PortfolioRisk,
  PrincipleCurrent,
  PrincipleOwnerListData,
  PrinciplePresetListData,
  PrincipleUpdateRequest,
  RagAnswerProjection,
  RagAskRequest,
  RagSourceListResponse,
  SignalV2Runtime,
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

  killSwitch(): Promise<ApiResult<KillSwitchState>> {
    return apiFetch<KillSwitchState>('/api/v1/risk/kill-switch');
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
  signal(symbol: string): Promise<ApiResult<SignalV2Runtime>> {
    return apiFetch<SignalV2Runtime>(`/api/v2/signals/${encodeURIComponent(symbol)}`);
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

  /* -------------------------------------------- Dashboard ViewModel 4종 */
  dashboardRiskResult(
    decisionId: string,
  ): Promise<ApiResult<DashboardEnvelope<DashboardRiskResultView>>> {
    return apiFetch(`/api/v1/dashboard/risk-results/${encodeURIComponent(decisionId)}`);
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
