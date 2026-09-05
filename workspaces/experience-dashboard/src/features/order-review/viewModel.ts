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
  DecisionRiskItemProjection,
} from '@/shared/api/wire';
import { ruleName } from '@/shared/lib/ruleLabels';
import { formatCount, formatKrw, formatRatio } from '@/shared/lib/format';
import { fromDashboard, type ViewState } from '@/shared/lib/viewState';

/** disposition은 절대 섞지 않는다. */
export type ReasonDisposition = 'VIOLATION' | 'ISSUE' | 'WARNING' | 'ABSTENTION';

export interface ReasonRow {
  disposition: ReasonDisposition;
  code: string;
  headline: string;
  detail: string;
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

function buildDetail(projection: DecisionProjection): NonNullable<RiskResultView['detail']> {
  const risk = projection.riskDecision;

  const reasons: ReasonRow[] = [
    ...risk.violations.map<ReasonRow>((violation) => ({
      disposition: 'VIOLATION',
      code: violation.ruleId,
      headline: `${ruleName(violation.ruleId)} 기준을 넘었습니다`,
      detail: `현재 ${formatMetric(metricOf(violation.ruleId), violation.metricValue)} · 기준 ${formatMetric(
        metricOf(violation.ruleId),
        violation.threshold,
      )}`,
    })),
    ...risk.issues.map<ReasonRow>((issue) => ({
      disposition: 'ISSUE',
      code: issue.code,
      headline: '필수 근거가 없어 판단을 미뤘습니다',
      detail: issue.message,
    })),
    ...risk.warnings.map<ReasonRow>((warning) => ({
      disposition: 'WARNING',
      code: warning.code,
      headline: '확인이 필요한 사항',
      detail: warning.message,
    })),
    ...risk.abstentions.map<ReasonRow>((abstention) => ({
      disposition: 'ABSTENTION',
      code: abstention.code,
      headline:
        abstention.disposition === 'NOT_APPLICABLE'
          ? '이번 주문에는 해당하지 않는 기준'
          : '근거가 없어 비교하지 않은 기준',
      detail:
        abstention.disposition === 'NOT_APPLICABLE'
          ? '이 주문의 평가 대상이 아닌 기준입니다.'
          : abstention.message,
    })),
  ];

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
    semanticInputHash: risk.semanticInputHash,
    snapshotArtifactHash: risk.snapshotArtifactHash,
  };
}

export async function loadRiskResultView(decisionId: string): Promise<ViewState<RiskResultView>> {
  const dashboard = await api.dashboardRiskResult(decisionId);

  // 상세는 보조 정보다. 실패해도 판정 화면 자체는 떠야 한다.
  let detail: RiskResultView['detail'] = null;
  let detailUnavailableReason: string | null = null;
  try {
    const projection = await api.decision(decisionId);
    detail = buildDetail(projection.data);
  } catch {
    detailUnavailableReason =
      '판정 상세(위반 값과 근거)를 불러오지 못했습니다. 요약 사유는 아래에 그대로 표시됩니다.';
  }

  return fromDashboard<DashboardRiskResultView, RiskResultView>(
    dashboard.data,
    (view) => ({
      decisionId: view.decisionId,
      action: view.action,
      summaryReasons: view.reasons,
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
