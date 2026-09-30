import { api } from '@/shared/api/endpoints';
import type { DashboardBacktestView, DashboardMetrics, DashboardStrategyName } from '@/shared/api/wire';
import { fromDashboard, type ViewState } from '@/shared/lib/viewState';
import { formatDecimal, formatRatio, formatSignedRatio } from '@/shared/lib/format';

export interface StrategyRow {
  strategy: DashboardStrategyName;
  label: string;
  metrics: DashboardMetrics;
}

export interface DerivedCard {
  key: string;
  label: string;
  value: number | null;
  format: 'RATIO' | 'SIGNED_RATIO' | 'SIGNED_DECIMAL';
  note: string;
  emphasis?: boolean;
}

/** 전략 검증과 보고서가 같은 카드를 같은 단위로 보이도록 포맷을 한곳에 둔다. */
export function derivedCardFormatter(format: DerivedCard['format']): (value: number) => string {
  switch (format) {
    case 'SIGNED_DECIMAL':
      return (value) => `${value > 0 ? '+' : ''}${formatDecimal(value, 2)}`;
    case 'SIGNED_RATIO':
      return (value) => formatSignedRatio(value, 1);
    case 'RATIO':
      return (value) => formatRatio(value, 1);
  }
}

export interface EquityPoint {
  at: string;
  Baseline: number | null;
  Guide: number | null;
  Strict: number | null;
}

export interface BacktestReportView {
  runId: string;
  fixtureClass: 'REAL_ARTIFACT' | 'SYNTHETIC_FAKE_E2E';
  strategies: StrategyRow[];
  equityCurve: EquityPoint[];
  heatmap: { month: string; return: number }[];
  serverMetricCards: { metric: string; value: number | null }[];
  derivedCards: DerivedCard[];
  projectionHash: string;
}

const LABEL: Record<DashboardStrategyName, string> = {
  Baseline: 'Baseline · 원칙 없음',
  Guide: 'Guide · 경고 반영',
  Strict: 'Strict · 위반 차단',
};

function improvement(strict: number | null, baseline: number | null): number | null {
  if (strict === null || baseline === null || baseline === 0) return null;
  return 1 - strict / baseline;
}

export function toBacktestReportView(view: DashboardBacktestView): BacktestReportView {
  const byName = new Map(view.strategies.map((entry) => [entry.strategy, entry]));
  const baseline = byName.get('Baseline')?.metrics ?? null;
  const strict = byName.get('Strict')?.metrics ?? null;
  const bestNetReturn = view.metricCards
    .map((card) => {
      const match = /^(Baseline|Guide|Strict)\.netReturn$/.exec(card.metric);
      return match && card.value !== null && Number.isFinite(card.value)
        ? { scenario: match[1], value: card.value }
        : null;
    })
    .filter((item): item is { scenario: string; value: number } => item !== null)
    .sort((left, right) => right.value - left.value)[0] ?? null;

  const merged = new Map<string, EquityPoint>();
  for (const entry of view.strategies) {
    for (const point of entry.curve) {
      const row = merged.get(point.at) ?? { at: point.at, Baseline: null, Guide: null, Strict: null };
      row[entry.strategy] = point.value;
      merged.set(point.at, row);
    }
  }
  const equityCurve = Array.from(merged.values()).sort((a, b) => a.at.localeCompare(b.at));

  const derivedCards: DerivedCard[] = [
    {
      key: 'best_net_return',
      label: '이 기간 최고 관측값',
      value: bestNetReturn?.value ?? null,
      format: 'SIGNED_RATIO',
      note: bestNetReturn
        ? `${bestNetReturn.scenario}의 비용 반영 수익률입니다. 세 시나리오를 같은 원본 가격과 거래비용 조건으로 계산했습니다.`
        : '비용 반영 수익률이 저장되지 않았습니다.',
      emphasis: true,
    },
    {
      key: 'mdd_improvement',
      label: 'MDD 개선율',
      value: improvement(strict?.mdd ?? null, baseline?.mdd ?? null),
      format: 'RATIO',
      note: '1 − (Strict MDD ÷ Baseline MDD). 안전장치가 낙폭을 얼마나 줄였는지 봅니다.',
    },
    {
      key: 'cvar_improvement',
      label: 'CVaR 개선율',
      value: improvement(strict?.cvar95 ?? null, baseline?.cvar95 ?? null),
      format: 'RATIO',
      note: '꼬리 손실 구간이 얼마나 완화됐는지 봅니다.',
    },
    {
      key: 'sharpe_gap',
      label: 'Sharpe 차이',
      value:
        strict?.sharpe !== null && strict?.sharpe !== undefined && baseline?.sharpe != null
          ? strict.sharpe - baseline.sharpe
          : null,
      // Sharpe는 비율(%)이 아니라 무차원 값이라 소수로 보여 준다.
      format: 'SIGNED_DECIMAL',
      note: 'Strict − Baseline. 신뢰구간이 0을 포함하면 우열을 단정하지 않습니다.',
    },
    {
      key: 'cagr_cost',
      label: 'CAGR 차이',
      value:
        strict?.cagr !== null && strict?.cagr !== undefined && baseline?.cagr != null
          ? strict.cagr - baseline.cagr
          : null,
      format: 'SIGNED_RATIO',
      note: 'Strict − Baseline 연환산 수익률. 음수면 안전장치의 대가, 양수면 안전장치가 수익률도 지킨 것입니다.',
    },
  ];

  return {
    runId: view.runId,
    fixtureClass: view.fixtureClass,
    strategies: view.strategies.map((entry) => ({
      strategy: entry.strategy,
      label: LABEL[entry.strategy] ?? entry.strategy,
      metrics: entry.metrics,
    })),
    equityCurve,
    heatmap: view.heatmap,
    serverMetricCards: view.metricCards,
    derivedCards,
    projectionHash: view.projectionHash,
  };
}

export async function loadBacktestReportView(runId: string): Promise<ViewState<BacktestReportView>> {
  const { data } = await api.dashboardBacktest(runId);
  return fromDashboard<DashboardBacktestView, BacktestReportView>(data, toBacktestReportView, {
    title: '이 실행 ID에 저장된 백테스트 결과가 없습니다',
    detail: '백테스트 artifact가 아직 등록되지 않았습니다. 실행 ID를 확인하세요.',
  });
}
