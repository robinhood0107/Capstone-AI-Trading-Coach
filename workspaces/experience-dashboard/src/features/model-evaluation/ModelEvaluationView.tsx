'use client';

import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { IdInput } from '@/shared/ui/IdInput';
import { AbstainChip } from '@/shared/ui/Decision';
import { useResource } from '@/shared/lib/useResource';
import { ID_PATTERN } from '@/shared/api/endpoints';
import { useLatestRun } from '@/shared/api/latestRun';
import { formatDecimal, formatKstDateTime, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import { loadModelEvaluationView, loadSignalView, type ModelRow, type SignalSlot } from './viewModel';

const SIGNAL_LABEL: Record<string, string> = { BUY: '매수', SELL: '매도', HOLD: '보류' };
const SIGNAL_TONE: Record<string, string> = {
  BUY: 'text-allow',
  SELL: 'text-block',
  HOLD: 'text-hold',
};

export function ModelEvaluationView() {
  const { runId, pending } = useLatestRun('model-evaluations');
  const [symbol, setSymbol] = useState('');
  const symbolValid = ID_PATTERN.symbol.test(symbol);

  const evaluation = useResource(
    () => loadModelEvaluationView(runId ?? ''),
    [runId],
    runId !== null,
  );
  const signal = useResource(() => loadSignalView(symbol), [symbol], symbolValid);

  return (
    <div className="space-y-6">
      <IdInput
        label="종목 코드"
        hint="종목을 넣으면 그 종목의 현재 신호를 함께 봅니다. 성과 지표와는 다른 축입니다."
        placeholder="005930"
        value={symbol}
        onChange={setSymbol}
        pattern={ID_PATTERN.symbol}
        patternHint="숫자 6자리여야 합니다."
      />

      {runId !== null ? (
        <AsyncBoundary state={evaluation.state} onRetry={evaluation.reload}>
          {(view) => {
            const chartRows = view.rows.map((row) => ({
              name: row.displayName,
              sharpe: row.status === 'AVAILABLE' ? row.metrics.sharpe : null,
              mdd: row.status === 'AVAILABLE' ? row.metrics.mdd : null,
            }));
            const hasChart = chartRows.some((row) => row.sharpe !== null);

            return (
              <div className="space-y-6">
                <Panel
                  contract="dashboard-model-evaluation.v1"
                  title="모델별 성과"
                  hint="같은 실행에서 나온 결과만 나란히 놓습니다. 근거를 내지 못한 모델은 ABSTAIN으로 남습니다."
                  actions={
                    <span className="font-mono text-[12px] text-faint">
                      비교 가능 {view.comparableCount} / {view.rows.length}
                    </span>
                  }
                >
                  {view.comparableCount === 0 ? (
                    <p className="rounded-tile border border-dashed border-rule px-4 py-5 text-[13px] leading-6 text-muted">
                      이 실행의 모든 모델이 ABSTAIN입니다. 성과를 비교할 근거가 없습니다.
                    </p>
                  ) : null}

                  <div className="overflow-x-auto">
                    <ModelTable rows={view.rows} />
                  </div>

                </Panel>

                <div className="grid gap-6 lg:grid-cols-2">
                  <Panel
                    contract="dashboard-model-evaluation.v1 · models"
                    title="Sharpe 비교"
                    hint="ABSTAIN인 모델은 막대가 없습니다. 0으로 그리지 않습니다."
                  >
                    {hasChart ? (
                      <div className="h-56">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={chartRows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                            <CartesianGrid stroke="rgb(var(--c-line))" vertical={false} />
                            <XAxis
                              dataKey="name"
                              tick={{ fontSize: 12, fill: 'rgb(var(--c-muted))' }}
                              axisLine={{ stroke: 'rgb(var(--c-rule))' }}
                              tickLine={false}
                            />
                            <YAxis
                              tick={{ fontSize: 11, fill: 'rgb(var(--c-faint))' }}
                              axisLine={false}
                              tickLine={false}
                            />
                            <Tooltip
                              cursor={{ fill: 'rgb(var(--c-navy) / 0.08)' }}
                              formatter={(value: number | string) =>
                                typeof value === 'number' ? formatDecimal(value, 2) : '—'
                              }
                              contentStyle={{ background: 'rgb(var(--c-panel))', border: '1px solid rgb(var(--c-line))', borderRadius: 8, fontSize: 12, color: 'rgb(var(--c-ink))' }}
                            />
                            <Bar dataKey="sharpe" isAnimationActive={false}>
                              {chartRows.map((row) => (
                                <Cell key={row.name} fill="rgb(var(--c-navy))" />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    ) : (
                      <p className="text-[13px] text-muted">비교할 Sharpe 값이 없습니다.</p>
                    )}
                  </Panel>

                  <Panel
                    contract="dashboard-model-evaluation.v1 · timeline"
                    title="평가 타임라인"
                    hint="이 실행에 기록된 시점별 값입니다."
                  >
                    {view.timeline.length === 0 ? (
                      <div className="rounded-tile border border-dashed border-rule px-4 py-6">
                        <p className="text-eyebrow font-semibold uppercase text-faint">데이터 없음</p>
                        <p className="mt-2 text-[13px] leading-5 text-muted">
                          이 실행에는 타임라인 값이 기록되지 않았습니다.
                        </p>
                      </div>
                    ) : (
                      <ul className="max-h-56 divide-y divide-line/60 overflow-y-auto">
                        {view.timeline.slice(0, 40).map((point) => (
                          <li key={point.at} className="flex justify-between py-2 text-[13px]">
                            <span className="font-mono text-muted">{formatKstDateTime(point.at)}</span>
                            <span className="tnum font-mono text-ink">
                              {formatDecimal(point.value, 4)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Panel>
                </div>
              </div>
            );
          }}
        </AsyncBoundary>
      ) : (
        <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
          {pending ? '불러오는 중입니다.' : '아직 등록된 모델 평가 결과가 없습니다.'}
        </p>
      )}

      {symbolValid ? (
        <AsyncBoundary state={signal.state} onRetry={signal.reload}>
          {(view) => (
            <Panel
              contract="GET /api/v2/signals/{symbol}"
              title={`${view.symbol} 현재 신호`}
              hint="성과 지표와 다른 축입니다. 지금 이 종목에 대해 각 모델이 무엇을 말하는지 봅니다."
              actions={
                view.composite.status === 'AVAILABLE' ? (
                  <span className="rounded-full border border-line px-3 py-1 text-[13px]">
                    종합{' '}
                    <strong className={SIGNAL_TONE[view.composite.signal]}>
                      {SIGNAL_LABEL[view.composite.signal]}
                    </strong>
                  </span>
                ) : (
                  <AbstainChip reason={view.composite.reason} />
                )
              }
            >
              <ul className="divide-y divide-line/60">
                {view.slots.map((slot) => (
                  <SignalSlotRow key={slot.key} slot={slot} />
                ))}
              </ul>

              <div className="mt-5 border-t border-line pt-4">
                <p className="text-[13px] font-medium text-ink">
                  {view.disagrees ? '모델 의견이 갈립니다' : '비교 가능한 모델이 같은 방향입니다'}
                </p>
                <p className="mt-1 text-[12px] leading-5 text-muted">
                  불일치는 표시만 하며 주문 가능 여부를 바꾸지 않습니다. HMM은 예측 모델이 아니므로
                  비교에서 제외합니다.
                </p>
              </div>

              {view.warnings.length > 0 ? (
                <ul className="mt-4 space-y-1.5">
                  {view.warnings.map((warning) => (
                    <li key={warning} className="text-[13px] text-muted">
                      · {warning}
                    </li>
                  ))}
                </ul>
              ) : null}
            </Panel>
          )}
        </AsyncBoundary>
      ) : null}
    </div>
  );
}

const METRIC_COLUMNS: {
  key: keyof ModelRow['metrics'];
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

function ModelTable({ rows }: { rows: ModelRow[] }) {
  const columns = METRIC_COLUMNS.filter((column) =>
    rows.some((row) => {
      const value = row.metrics[column.key];
      return value !== null && Number.isFinite(value);
    }),
  );

  return (
    <table className="mt-2 w-full min-w-[520px] text-[13px]">
      <thead>
        <tr className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
          <th className="pb-2 font-normal">모델</th>
          <th className="pb-2 font-normal">상태</th>
          {columns.map((column) => (
            <th key={column.key} className="pb-2 text-right font-normal">
              {column.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <ModelTableRow key={row.modelId} row={row} columns={columns} />
        ))}
      </tbody>
    </table>
  );
}

function ModelTableRow({
  row,
  columns,
}: {
  row: ModelRow;
  columns: typeof METRIC_COLUMNS;
}) {
  const abstain = row.status === 'ABSTAIN';
  return (
    <tr className="border-b border-line/60 align-top last:border-0">
      <td className="py-3 pr-3">
        <p className="font-medium text-ink">{row.displayName}</p>
      </td>
      <td className="py-3 pr-3">
        {abstain ? (
          <AbstainChip reason="이 실행에서 검증된 결과가 없습니다" />
        ) : (
          <span className="text-[12px] text-allow">사용 가능</span>
        )}
      </td>
      {columns.map((column) => (
        <td key={column.key} className="py-3 text-right">
          <Numeric value={row.metrics[column.key]} format={column.format} />
        </td>
      ))}
    </tr>
  );
}

function SignalSlotRow({ slot }: { slot: SignalSlot }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-ink">{slot.displayName}</p>
        <p className="font-mono text-[11px] text-faint">{slot.sourceWorkspace}</p>
      </div>
      {slot.status === 'ABSTAIN' ? (
        <div className="flex items-center gap-3">
          <AbstainChip reason={slot.reason} />
          <span className="text-[12px] text-muted">{slot.reason}</span>
        </div>
      ) : (
        <div className="flex items-center gap-5">
          {slot.regimeState ? (
            <span className="font-mono text-[13px] text-ink">{slot.regimeState}</span>
          ) : (
            <span className={`text-[13px] font-medium ${SIGNAL_TONE[slot.signal ?? 'HOLD']}`}>
              {SIGNAL_LABEL[slot.signal ?? 'HOLD']}
            </span>
          )}
          <Numeric value={slot.predictedReturn} format={(v) => formatSignedRatio(v, 2)} />
        </div>
      )}
    </li>
  );
}
