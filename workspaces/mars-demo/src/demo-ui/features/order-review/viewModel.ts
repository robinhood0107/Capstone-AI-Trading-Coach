/**
 * 주문 검토 ViewModel.
 *
 * 두 계약을 합쳐 쓴다.
 *  1) GET /api/v1/dashboard/risk-results/{decisionId}  — 서버가 판정한 sanitized ViewModel (권위)
 *  2) GET /api/v1/decisions/{decisionId}               — 위반·사유·근거값 상세 (보강, 없어도 화면은 뜬다)
 *
 * 판정은 여기서 만들지 않는다. 서버의 action을 그대로 옮긴다.
 */
import { api } from '@/shared/api/endpoints';
import type {
  DashboardRiskResultView,
  DecisionAction,
  DecisionProjection,
  DecisionInputMetricProjection,
  DecisionRiskItemProjection,
} from '@/shared/api/wire';
import { ruleName } from '@/shared/lib/ruleLabels';
import { decisionReasonLabel } from '@/shared/lib/labels';
import { formatCount, formatKrw, formatRatio } from '@/shared/lib/format';
import { fromDashboard, type ViewState } from '@/shared/lib/viewState';

/** disposition은 절대 섞지 않는다. */
export type ReasonDisposition = 'VIOLATION' | 'ISSUE' | 'WARNING' | 'ABSTENTION';

export interface ReasonRow {
  id: string;
  disposition: ReasonDisposition;
  code: string;
  headline: string;
  detail: string;
  count: number;
  ruleNames: string[];
  rawMessages: string[];
}

interface ReasonInput {
  disposition: ReasonDisposition;
  code: string;
  ruleId?: string;
  message: string;
  metricDetail?: string;
}

export function groupDecisionReasons(inputs: ReasonInput[]): ReasonRow[] {
  const groups = new Map<string, ReasonInput[]>();
  for (const input of inputs) {
    const key = `${input.disposition}:${input.code}`;
    groups.set(key, [...(groups.get(key) ?? []), input]);
  }
  return [...groups].map(([id, items]) => {
    const first = items[0]!;
    const ruleNames = [...new Set(items.flatMap((item) => item.ruleId ? [ruleName(item.ruleId)] : []))];
    const metricDetails = [...new Set(items.flatMap((item) => item.metricDetail ? [item.metricDetail] : []))];
    const detail = first.code === 'NOT_APPLICABLE_V1'
      ? `해당하지 않는 기준 ${items.length}개`
      : [
          ruleNames.length > 0
            ? `${items.length}개 규칙 (${ruleNames.join(', ')})`
            : items.length > 1 ? `${items.length}건` : '',
          ...metricDetails,
        ].filter(Boolean).join(' · ');
    return {
      id,
      disposition: first.disposition,
      code: first.code,
      headline: first.disposition === 'VIOLATION'
        ? `${ruleName(first.code)} 기준을 넘었습니다`
        : decisionReasonLabel(first.code),
      detail,
      count: items.length,
      ruleNames,
      rawMessages: [...new Set(items.map((item) => item.message).filter(Boolean))],
    };
  });
}

function displaySummaryReasons(reasons: string[], detail: RiskResultView['detail']): string[] {
  if (detail && detail.reasons.length > 0) {
    return detail.reasons.map((reason) =>
      reason.detail ? `${reason.headline} · ${reason.detail}` : reason.headline);
  }
  return [...new Set(reasons.map((reason) =>
    reason === 'Required evaluation input is unavailable.'
      ? '평가에 필요한 입력을 확인할 수 없음'
      : decisionReasonLabel(reason)))];
}

export interface ViolatedPrinciple {
  ruleId: string;
  name: string;
  observed: string;
  limit: string;
  severity: 'WARN' | 'BLOCK';
}

export interface RiskResultView {
  decisionId: string;
  action: DecisionAction;
  /** 서버가 준 요약 문장. 상세를 못 불러와도 이건 항상 있다. */
  summaryReasons: string[];
  summaryPrinciples: string[];
  summaryRiskItems: DashboardRiskResultView['riskItems'];
  /** 아래는 상세 조회가 성공했을 때만 채워진다. */
  detail: {
    canSubmitOrder: boolean;
    mode: 'GUIDE' | 'STRICT';
    portfolioSource: 'KIS_MOCK' | 'INTERNAL_PAPER';
    principleVersion: number;
    validUntil: string;
    expired: boolean;
    reasons: ReasonRow[];
    violatedPrinciples: ViolatedPrinciple[];
    riskItems: DecisionRiskItemProjection[];
    inputMetrics: DecisionInputMetricProjection[];
    inputMetricsUnavailable: boolean;
    semanticInputHash: string;
    snapshotArtifactHash: string;
  } | null;
  detailUnavailableReason: string | null;
}

function formatMetric(metric: string, value: number): string {
  if (metric === 'order_amount_krw') return formatKrw(value);
  if (metric === 'daily_order_count') return `${formatCount(value)}건`;
  return formatRatio(value);
}

function metricOf(ruleId: string): string {
  const map: Record<string, string> = {
    max_single_order_amount: 'order_amount_krw',
    max_daily_orders: 'daily_order_count',
  };
  return map[ruleId] ?? ruleId;
}

function buildDetail(
  projection: DecisionProjection,
  inputMetrics: DecisionInputMetricProjection[],
  inputMetricsUnavailable: boolean,
): NonNullable<RiskResultView['detail']> {
  const risk = projection.riskDecision;

  const reasons = groupDecisionReasons([
    ...risk.violations.map<ReasonInput>((violation) => ({
      disposition: 'VIOLATION',
      code: violation.ruleId,
      ruleId: violation.ruleId,
      message: violation.message,
      metricDetail: `현재 ${formatMetric(metricOf(violation.ruleId), violation.metricValue)} · 기준 ${formatMetric(
        metricOf(violation.ruleId),
        violation.threshold,
      )}`,
    })),
    ...risk.issues.map<ReasonInput>((issue) => ({
      disposition: 'ISSUE',
      code: issue.code,
      ruleId: issue.ruleId,
      message: issue.message,
    })),
    ...risk.warnings.map<ReasonInput>((warning) => ({
      disposition: 'WARNING',
      code: warning.code,
      ruleId: warning.ruleId,
      message: warning.message,
    })),
    ...risk.abstentions.map<ReasonInput>((abstention) => ({
      disposition: 'ABSTENTION',
      code: abstention.code,
      ruleId: abstention.ruleId,
      message: abstention.message,
    })),
  ]);

  return {
    canSubmitOrder: risk.canSubmitOrder,
    mode: projection.mode,
    portfolioSource: projection.portfolioSource,
    principleVersion: projection.principleVersion,
    validUntil: projection.validUntil,
    expired: Date.parse(projection.validUntil) <= Date.now(),
    reasons,
    violatedPrinciples: risk.violations.map((violation) => ({
      ruleId: violation.ruleId,
      name: ruleName(violation.ruleId),
      observed: formatMetric(metricOf(violation.ruleId), violation.metricValue),
      limit: formatMetric(metricOf(violation.ruleId), violation.threshold),
      severity: violation.severity === 'BLOCK' ? 'BLOCK' : 'WARN',
    })),
    riskItems: risk.riskItems,
    inputMetrics,
    inputMetricsUnavailable,
    semanticInputHash: risk.semanticInputHash,
    snapshotArtifactHash: risk.snapshotArtifactHash,
  };
}

const INPUT_METRIC_NAMES: Record<string, string> = {
  annualized_volatility: '연환산 변동성',
  asset_weight: '종목 비중',
  current_price_krw: '현재가',
  daily_loss_rate: '일일 손실률',
  daily_order_count: '오늘 주문 횟수',
  disclosure_risk_score: '공시 위험 점수',
  etf_etn_product_risk_score: 'ETF·ETN 상품 위험 점수',
  gold_etf_etn_weight: '금 ETF 비중',
  hmm_risk_off_probability: '위험회피 국면 확률',
  margin_requirement_krw: '증거금',
  mdd: '최대낙폭',
  mean_reversion_abs_z_score: '평균회귀 편차',
  negative_news_score: '부정 뉴스 점수',
  order_amount_krw: '주문 금액',
  owner_position_quantity: '보유 수량',
  portfolio_equity_krw: '계좌 평가금액',
};

export function decisionMetricName(metric: string): string {
  return INPUT_METRIC_NAMES[metric] ?? metric;
}

export function displayDecisionInputMetric(item: DecisionInputMetricProjection): { name: string; value: string } {
  const name = decisionMetricName(item.metric);
  if (item.availability !== 'AVAILABLE' || item.value === null) {
    const status = item.availability === 'NOT_APPLICABLE' ? '이번 주문에 해당하지 않음'
      : item.availability === 'STALE' ? '관측이 오래됨' : '관측 없음';
    return { name, value: status };
  }
  if (item.unit === 'KRW') return { name, value: formatKrw(item.value) };
  if (item.unit === 'RATIO') return { name, value: formatRatio(item.value) };
  if (item.unit === 'COUNT') return { name, value: `${formatCount(item.value)}건` };
  if (item.unit === 'QUANTITY') return { name, value: `${formatCount(item.value)}주` };
  return { name, value: String(item.value) };
}

export async function loadRiskResultView(decisionId: string): Promise<ViewState<RiskResultView>> {
  const dashboard = await api.dashboardRiskResult(decisionId);

  // 상세는 보조 정보다. 실패해도 판정 화면 자체는 떠야 한다.
  let detail: RiskResultView['detail'] = null;
  let detailUnavailableReason: string | null = null;
  try {
    const [projection, inputs] = await Promise.allSettled([
      api.decision(decisionId), api.decisionInputs(decisionId),
    ]);
    if (projection.status === 'rejected') throw projection.reason;
    detail = buildDetail(
      projection.value.data,
      inputs.status === 'fulfilled' ? inputs.value.data.items : [],
      inputs.status === 'rejected',
    );
  } catch {
    detailUnavailableReason =
      '판정 상세(위반 값과 근거)를 불러오지 못했습니다. 요약 사유는 아래에 그대로 표시됩니다.';
  }

  return fromDashboard<DashboardRiskResultView, RiskResultView>(
    dashboard.data,
    (view) => ({
      decisionId: view.decisionId,
      action: view.action,
      summaryReasons: displaySummaryReasons(view.reasons, detail),
      summaryPrinciples: view.principles,
      summaryRiskItems: view.riskItems,
      detail,
      detailUnavailableReason,
    }),
    {
      title: '표시할 판정이 없습니다',
      detail: '이 판정 ID에 저장된 결과가 없습니다. 주문을 먼저 평가한 뒤 다시 조회하세요.',
    },
  );
}
