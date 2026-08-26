'use client';

import { useState } from 'react';
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
import { IdInput } from '@/shared/ui/IdInput';
import { useResource } from '@/shared/lib/useResource';
import { ID_PATTERN } from '@/shared/api/endpoints';
import { formatDecimal, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import { loadBacktestReportView, type DerivedCard } from './viewModel';

const COLOR: Record<string, string> = {
  Baseline: '#8A9099',
  Guide: '#9A6510',
  Strict: '#1D3557',
};

export function BacktestReportView({ defaultRunId = '' }: { defaultRunId?: string }) {
  const [runId, setRunId] = useState(defaultRunId);
  const valid = ID_PATTERN.runId.test(runId);
  const { state, reload } = useResource(() => loadBacktestReportView(runId), [runId], valid);

  return (
    <div className="space-y-6">
      <IdInput
        label="백테스트 실행 ID"
        hint="백테스트 artifact가 등록된 실행 단위입니다. run_ 또는 demo_ 로 시작합니다."
        placeholder="demo_s8_offline_0001"
        value={runId}
        onChange={setRunId}
        pattern={ID_PATTERN.runId}
        patternHint="run_ 또는 demo_ 로 시작하고 뒤에 8~96자가 와야 합니다."
      />

      {!valid ? (
        <p className="border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
          실행 ID를 입력하면 Baseline / Guide / Strict 비교를 불러옵니다.
        </p>
      ) : (
        <AsyncBoundary state={state} onRetry={reload}>
          {(view) => (
            <div className="space-y-6">
              {view.fixtureClass === 'SYNTHETIC_FAKE_E2E' ? (
                <p className="border-l-2 border-warn bg-warn/5 px-3 py-2 text-[13px] leading-6 text-ink">
                  이 결과는 합성 데이터(SYNTHETIC_FAKE_E2E)입니다. 실제 성과나 승격 근거로 인용하지 마세요.
                </p>
              ) : null}

              <Panel
                contract="dashboard-backtest.v1 · 파생 지표"
                title="원칙과 안전장치의 효과"
                hint="Baseline과 Strict를 비교해 화면에서 계산한 값입니다. 계산식을 함께 적어 둡니다."
              >
                <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-4">
                  {view.derivedCards.map((card) => (
                    <MetricTile key={card.key} card={card} />
                  ))}
                </div>
              </Panel>

              <Panel
                contract="dashboard-backtest.v1 · strategies"
                title="Baseline / Guide / Strict 비교"
                hint="같은 조건에서 원칙 개입 정도만 바꾼 결과입니다."
              >
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-[13px]">
                    <thead>
                      <tr className="border-b border-line text-left font-mono text-eyebrow uppercase text-faint">
                        <th className="pb-2 font-normal">시나리오</th>
                        <th className="pb-2 text-right font-normal">CAGR</th>
                        <th className="pb-2 text-right font-normal">MDD</th>
                        <th className="pb-2 text-right font-normal">Sharpe</th>
                        <th className="pb-2 text-right font-normal">Sortino</th>
                        <th className="pb-2 text-right font-normal">VaR 95</th>
                        <th className="pb-2 text-right font-normal">CVaR 95</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.strategies.map((row) => (
                        <tr key={row.strategy} className="border-b border-line/60 last:border-0">
                          <td className="py-3 pr-3 text-ink">{row.label}</td>
                          <td className="py-3 text-right">
                            <Numeric value={row.metrics.cagr} format={(v) => formatSignedRatio(v, 1)} />
                          </td>
                          <td className="py-3 text-right">
                            <Numeric value={row.metrics.mdd} format={(v) => formatRatio(v, 1)} />
                          </td>
                          <td className="py-3 text-right">
                            <Numeric value={row.metrics.sharpe} format={(v) => formatDecimal(v, 2)} />
                          </td>
                          <td className="py-3 text-right">
                            <Numeric value={row.metrics.sortino} format={(v) => formatDecimal(v, 2)} />
                          </td>
                          <td className="py-3 text-right">
                            <Numeric value={row.metrics.var95} format={(v) => formatRatio(v, 1)} />
                          </td>
                          <td className="py-3 text-right">
                            <Numeric value={row.metrics.cvar95} format={(v) => formatRatio(v, 1)} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-4 text-[12px] leading-5 text-muted">
                  Sharpe 차이는 신뢰구간과 함께 읽어야 합니다. 구간이 겹치면 우열을 단정하지 않습니다.
                </p>
              </Panel>

              <Panel
                contract="dashboard-backtest.v1 · strategies[].curve"
                title="자산 곡선"
                hint="세 시나리오를 같은 시각 축에 겹쳐 봅니다."
              >
                {view.equityCurve.length === 0 ? (
                  <EmptyBlock detail="이 실행에는 곡선 값이 기록되지 않았습니다." />
                ) : (
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={view.equityCurve} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                        <CartesianGrid stroke="#D5D9D1" vertical={false} />
                        <XAxis
                          dataKey="at"
                          tick={{ fontSize: 10, fill: '#8A9099' }}
                          axisLine={{ stroke: '#BFC6BC' }}
                          tickLine={false}
                          minTickGap={48}
                          tickFormatter={(value: string) => value.slice(0, 10)}
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: '#8A9099' }}
                          axisLine={false}
                          tickLine={false}
                          domain={['auto', 'auto']}
                        />
                        <Tooltip
                          contentStyle={{ border: '1px solid #D5D9D1', borderRadius: 2, fontSize: 12 }}
                          formatter={(value: number | string) =>
                            typeof value === 'number' ? formatDecimal(value, 4) : '근거 없음'
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
                  title="서버가 준 지표"
                  hint="서버가 이름과 값만 내려줍니다. 화면에서 뜻을 임의로 붙이지 않습니다."
                >
                  {view.serverMetricCards.length === 0 ? (
                    <EmptyBlock detail="추가 지표가 없습니다." />
                  ) : (
                    <ul className="divide-y divide-line/60">
                      {view.serverMetricCards.map((card) => (
                        <li key={card.metric} className="flex items-center justify-between py-2.5">
                          <span className="font-mono text-[12px] text-muted">{card.metric}</span>
                          <Numeric value={card.value} format={(v) => formatDecimal(v, 4)} />
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </div>

              <p className="font-mono text-[11px] text-faint">
                projectionHash {view.projectionHash.slice(0, 24)}…
              </p>
            </div>
          )}
        </AsyncBoundary>
      )}
    </div>
  );
}

function MetricTile({ card }: { card: DerivedCard }) {
  const format =
    card.format === 'SIGNED_RATIO' ? (v: number) => formatSignedRatio(v, 1) : (v: number) => formatRatio(v, 1);
  return (
    <div className="bg-panel px-4 py-4">
      <p className="font-mono text-eyebrow uppercase text-faint">{card.label}</p>
      <p className="mt-2">
        <Numeric value={card.value} format={format} className="text-2xl font-semibold text-ink" />
      </p>
      <p className="mt-2 text-[12px] leading-5 text-muted">{card.note}</p>
    </div>
  );
}

function EmptyBlock({ detail }: { detail: string }) {
  return (
    <div className="border border-dashed border-rule px-4 py-6">
      <p className="font-mono text-eyebrow uppercase text-faint">데이터 없음</p>
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
                      <div className="hatch h-8 border border-line/60" title="해당 월 데이터 없음" />
                    </td>
                  );
                }
                const intensity = Math.min(1, Math.abs(value) / max);
                const color = value >= 0 ? '22, 101, 63' : '150, 38, 43';
                return (
                  <td key={month} className="p-0.5">
                    <div
                      className="tnum flex h-8 items-center justify-center border border-line/60 font-mono text-[11px]"
                      style={{
                        backgroundColor: `rgba(${color}, ${0.12 + intensity * 0.6})`,
                        color: intensity > 0.6 ? '#FFFFFF' : '#14181D',
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
