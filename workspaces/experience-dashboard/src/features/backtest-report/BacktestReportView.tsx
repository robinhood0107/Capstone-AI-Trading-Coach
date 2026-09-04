'use client';

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { useResource } from '@/shared/lib/useResource';
import { useLatestRun } from '@/shared/api/latestRun';
import { formatDecimal, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import { loadBacktestReportView, type DerivedCard, type StrategyRow } from './viewModel';

const COLOR: Record<string, string> = {
  Baseline: 'rgb(var(--c-faint))',
  Guide: 'rgb(var(--c-warn))',
  Strict: 'rgb(var(--c-navy))',
};

export function BacktestReportView() {
  const { runId, pending } = useLatestRun('backtests');
  const { state, reload } = useResource(
    () => loadBacktestReportView(runId ?? ''),
    [runId],
    runId !== null,
  );

  return (
    <div className="space-y-6">
      {runId === null ? (
        <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
          {pending ? '불러오는 중입니다.' : '아직 등록된 검증 결과가 없습니다.'}
        </p>
      ) : (
        <AsyncBoundary state={state} onRetry={reload}>
          {(view) => (
            <div className="space-y-6">
              {view.fixtureClass === 'SYNTHETIC_FAKE_E2E' ? (
                <p className="border-l-2 border-warn bg-warn/5 px-3 py-2 text-[13px] leading-6 text-ink">
                  이 결과는 합성 예시 데이터입니다. 실제 성과나 승격 근거로 인용하지 마세요.
                </p>
              ) : null}

              <DerivedPanel cards={view.derivedCards} />

              <Panel
                contract="dashboard-backtest.v1 · strategies"
                title="Baseline / Guide / Strict 비교"
                hint="같은 조건에서 원칙 개입 정도만 바꾼 결과입니다."
              >
                <div className="overflow-x-auto">
                  <ScenarioTable rows={view.strategies} />
                </div>
                <p className="mt-4 text-[12px] leading-5 text-muted">
                  Sharpe 차이는 신뢰구간과 함께 읽어야 합니다. 구간이 겹치면 우열을 단정하지 않습니다.
                </p>
              </Panel>

              <Panel
                contract="dashboard-backtest.v1 · strategies[].curve"
                title="자산 곡선"
                hint={`운영 시작 뒤 ${view.equityCurve.length}거래일을 같은 축에 표시합니다. 단기 표본이므로 성과를 단정하지 않습니다.`}
              >
                {view.equityCurve.length === 0 ? (
                  <EmptyBlock detail="이 실행에는 곡선 값이 기록되지 않았습니다." />
                ) : (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={view.equityCurve} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                        <CartesianGrid stroke="rgb(var(--c-line))" vertical={false} />
                        <XAxis
                          dataKey="at"
                          tick={{ fontSize: 10, fill: 'rgb(var(--c-faint))' }}
                          axisLine={{ stroke: 'rgb(var(--c-rule))' }}
                          tickLine={false}
                          minTickGap={48}
                          tickFormatter={(value: string) => value.slice(0, 10)}
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: 'rgb(var(--c-faint))' }}
                          axisLine={false}
                          tickLine={false}
                          domain={['auto', 'auto']}
                        />
                        <Tooltip
                          contentStyle={{ background: 'rgb(var(--c-panel))', border: '1px solid rgb(var(--c-line))', borderRadius: 8, fontSize: 12, color: 'rgb(var(--c-ink))' }}
                          formatter={(value: number | string) =>
                            typeof value === 'number' ? formatDecimal(value, 4) : '—'
                          }
                        />
                        <Legend wrapperStyle={{ fontSize: 12 }} />
                        <Line type="monotone" dataKey="Baseline" stroke={COLOR.Baseline} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
                        <Line type="monotone" dataKey="Guide" stroke={COLOR.Guide} dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
                        <Line type="monotone" dataKey="Strict" stroke={COLOR.Strict} dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Panel>

              <div className="grid gap-6 xl:grid-cols-2">
                <Panel
                  contract="dashboard-backtest.v1 · heatmap"
                  title="월별 수익률"
                  hint="손실 달이 어디에 몰려 있는지 봅니다."
                >
                  {view.heatmap.length === 0 ? (
                    <EmptyBlock detail="월별 값이 기록되지 않았습니다." />
                  ) : (
                    <Heatmap cells={view.heatmap} />
                  )}
                </Panel>

                <Panel
                  contract="dashboard-backtest.v1 · metricCards"
                  title="거래 결과"
                  hint="거래비용을 반영한 수익률과 실제 거래 횟수입니다."
                >
                  {view.serverMetricCards.filter((card) => card.value !== null).length === 0 ? (
                    <EmptyBlock detail="추가 지표가 없습니다." />
                  ) : (
                    <ul className="divide-y divide-line/60">
                      {view.serverMetricCards
                        .filter((card) => card.value !== null)
                        .map((card) => {
                          const meta = metricCardMeta(card.metric);
                          return (
                            <li key={card.metric} className="flex items-center justify-between py-2.5">
                              <span className="text-[12px] text-muted">{meta.label}</span>
                              <Numeric value={card.value} format={meta.format} />
                            </li>
                          );
                        })}
                    </ul>
                  )}
                </Panel>
              </div>

            </div>
          )}
        </AsyncBoundary>
      )}
    </div>
  );
}

function metricCardMeta(metric: string): { label: string; format: (value: number) => string } {
  const [scenario, name] = metric.split('.');
  const scenarioLabel = scenario === 'Baseline' ? 'Baseline' : scenario === 'Guide' ? 'Guide' : 'Strict';
  if (name === 'netReturn') {
    return { label: `${scenarioLabel} 비용 반영 수익률`, format: (value) => formatSignedRatio(value, 2) };
  }
  if (name === 'tradeCount') {
    return { label: `${scenarioLabel} 거래 수`, format: (value) => `${Math.trunc(value)}건` };
  }
  if (name === 'winRate') {
    return { label: `${scenarioLabel} 승률`, format: (value) => formatRatio(value, 1) };
  }
  if (name === 'principleViolationCount') {
    return { label: 'Strict 차단한 원칙 위반', format: (value) => `${Math.trunc(value)}건` };
  }
  return { label: '추가 측정값', format: (value) => formatDecimal(value, 4) };
}

const SCENARIO_COLUMNS: {
  key: 'cagr' | 'mdd' | 'sharpe' | 'sortino' | 'var95' | 'cvar95';
  label: string;
  format: (value: number) => string;
}[] = [
  { key: 'cagr', label: 'CAGR', format: (v) => formatSignedRatio(v, 1) },
  { key: 'mdd', label: 'MDD', format: (v) => formatRatio(v, 1) },
  { key: 'sharpe', label: 'Sharpe', format: (v) => formatDecimal(v, 2) },
  { key: 'sortino', label: 'Sortino', format: (v) => formatDecimal(v, 2) },
  { key: 'var95', label: 'VaR 95', format: (v) => formatRatio(v, 1) },
  { key: 'cvar95', label: 'CVaR 95', format: (v) => formatRatio(v, 1) },
];

function ScenarioTable({ rows }: { rows: StrategyRow[] }) {
  const columns = SCENARIO_COLUMNS.filter((column) =>
    rows.some((row) => {
      const value = row.metrics[column.key];
      return value !== null && Number.isFinite(value);
    }),
  );

  return (
    <table className="w-full min-w-[420px] text-[13px]">
      <thead>
        <tr className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
          <th className="pb-2 font-normal">시나리오</th>
          {columns.map((column) => (
            <th key={column.key} className="pb-2 text-right font-normal">
              {column.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.strategy} className="border-b border-line/60 last:border-0">
            <td className="py-3 pr-3 text-ink">{row.label}</td>
            {columns.map((column) => (
              <td key={column.key} className="py-3 text-right">
                <Numeric value={row.metrics[column.key]} format={column.format} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DerivedPanel({ cards }: { cards: DerivedCard[] }) {
  const usable = cards.filter((card) => card.value !== null && Number.isFinite(card.value));
  if (usable.length === 0) return null;
  return (
    <Panel
      title="원칙과 안전장치의 효과"
      hint="Baseline과 Strict를 비교해 화면에서 계산한 값입니다. 계산식을 함께 적어 둡니다."
    >
      <div className="grid gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-2 xl:grid-cols-5">
        {usable.map((card) => (
          <MetricTile key={card.key} card={card} />
        ))}
      </div>
    </Panel>
  );
}

function MetricTile({ card }: { card: DerivedCard }) {
  const format =
    card.format === 'SIGNED_RATIO' ? (v: number) => formatSignedRatio(v, 1) : (v: number) => formatRatio(v, 1);
  return (
    <div className="bg-panel px-4 py-4">
      <p className="text-eyebrow font-semibold uppercase text-faint">{card.label}</p>
      <p className="mt-2">
        <Numeric
          value={card.value}
          format={format}
          className={`text-2xl font-semibold ${card.emphasis && (card.value ?? 0) > 0 ? 'text-allow' : 'text-ink'}`}
        />
      </p>
      <p className="mt-2 text-[12px] leading-5 text-muted">{card.note}</p>
    </div>
  );
}

function EmptyBlock({ detail }: { detail: string }) {
  return (
    <div className="rounded-tile border border-dashed border-rule px-4 py-6">
      <p className="text-eyebrow font-semibold uppercase text-faint">데이터 없음</p>
      <p className="mt-2 text-[13px] leading-5 text-muted">{detail}</p>
    </div>
  );
}

function Heatmap({ cells }: { cells: { month: string; return: number }[] }) {
  const years = Array.from(new Set(cells.map((cell) => cell.month.slice(0, 4)))).sort();
  const months = Array.from({ length: 12 }, (_, index) => String(index + 1).padStart(2, '0'));
  const byKey = new Map(cells.map((cell) => [cell.month, cell.return]));
  const max = Math.max(...cells.map((cell) => Math.abs(cell.return)), 0.0001);

  return (
    <div className="overflow-x-auto">
      <table className="min-w-[560px] border-collapse text-[12px]">
        <thead>
          <tr>
            <th className="w-12 pb-2 text-left font-mono text-eyebrow font-normal uppercase text-faint">
              연도
            </th>
            {months.map((month) => (
              <th key={month} className="pb-2 text-center font-mono text-eyebrow font-normal text-faint">
                {month}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {years.map((year) => (
            <tr key={year}>
              <td className="pr-2 font-mono text-[12px] text-muted">{year}</td>
              {months.map((month) => {
                const value = byKey.get(`${year}-${month}`);
                if (value === undefined) {
                  return (
                    <td key={month} className="p-0.5">
                      <div className="hatch h-8 rounded-md border border-line/60" title="해당 월 데이터 없음" />
                    </td>
                  );
                }
                const intensity = Math.min(1, Math.abs(value) / max);
                const token = value >= 0 ? '--c-allow' : '--c-block';
                return (
                  <td key={month} className="p-0.5">
                    <div
                      className="tnum flex h-8 items-center justify-center rounded-md border border-line/60 font-mono text-[11px]"
                      style={{
                        backgroundColor: `rgb(var(${token}) / ${0.12 + intensity * 0.6})`,
                        color: intensity > 0.6 ? 'rgb(var(--c-panel))' : 'rgb(var(--c-ink))',
                      }}
                      title={`${year}-${month} · ${formatSignedRatio(value, 2)}`}
                    >
                      {(value * 100).toFixed(1)}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-[12px] text-muted">
        숫자는 % 단위입니다. 해치 칸은 해당 월 데이터가 없다는 뜻입니다.
      </p>
    </div>
  );
}
