'use client';

import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { DecisionRail } from '@/shared/ui/Decision';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { empty } from '@/shared/lib/viewState';
import { useLatestRun } from '@/shared/api/latestRun';
import { formatDecimal, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import { loadBacktestReportView } from '@/features/backtest-report/viewModel';
import { loadRiskResultView, type RiskResultView } from '@/features/order-review/viewModel';

const CAPTURE_LIST = [
  { figure: '그림 4', title: 'RiskEngine 판단 흐름', where: '아래 판정 레일과 위반 목록' },
  { figure: '표 6', title: 'Baseline/Guide/Strict 검증 시나리오', where: '아래 시나리오 비교표' },
  { figure: '표 5', title: '모델별 역할 비교', where: '모델 비교 화면의 모델별 성과 표' },
  { figure: '그림 6', title: '화면 와이어프레임', where: '원칙 설정 / 주문 검토 / 금융 가이드 화면' },
  { figure: '표 10', title: '성과지표', where: '아래 원칙 효과 카드' },
];

export function ReportView() {
  const { runId } = useLatestRun('backtests');

  const decision = useResource<RiskResultView>(async () => {
    const { data } = await api.dashboardRecentRiskResults();
    const latest = data.items[0];
    if (!latest) return empty('저장된 판정이 없습니다', '자동운용이 판정을 마치면 보고서에 표시됩니다.');
    return loadRiskResultView(latest.decisionId);
  }, []);
  const backtest = useResource(
    () => loadBacktestReportView(runId ?? ''),
    [runId],
    runId !== null,
  );

  return (
    <div className="space-y-6">
      <Panel
        contract="report/capture-guide"
        title="캡처 목록"
        hint="보고서 그림·표 번호와 실제 화면을 1:1로 연결합니다."
      >
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
              <th className="pb-2 font-normal">번호</th>
              <th className="pb-2 font-normal">보고서 항목</th>
              <th className="pb-2 font-normal">캡처 위치</th>
            </tr>
          </thead>
          <tbody>
            {CAPTURE_LIST.map((item) => (
              <tr key={item.figure} className="border-b border-line/60 last:border-0">
                <td className="py-2.5 pr-3 font-mono text-muted">{item.figure}</td>
                <td className="py-2.5 pr-3 text-ink">{item.title}</td>
                <td className="py-2.5 text-muted">{item.where}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <AsyncBoundary state={decision.state} onRetry={decision.reload}>
          {(view) => (
            <Panel
              contract="report/decision-capture"
              title="안전장치가 실제로 동작한 화면"
              hint="판정 레일과 넘어선 원칙을 한 장에 담습니다."
            >
              <DecisionRail status={view.action} />
              {view.detail && view.detail.violatedPrinciples.length > 0 ? (
                <ul className="mt-5 space-y-2">
                  {view.detail.violatedPrinciples.map((item) => (
                    <li
                      key={item.ruleId}
                      className="flex items-baseline justify-between border-b border-line/60 pb-2 text-[13px]"
                    >
                      <span className="text-ink">{item.name}</span>
                      <span className="tnum font-mono text-muted">
                        현재 <span className="text-block">{item.observed}</span> · 기준 {item.limit}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <ul className="mt-5 space-y-2">
                  {view.summaryReasons.map((reason) => (
                    <li key={reason} className="text-[13px] leading-6 text-ink">
                      · {reason}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          )}
      </AsyncBoundary>

      {runId !== null ? (
        <AsyncBoundary state={backtest.state} onRetry={backtest.reload}>
          {(view) => (
            <>
              <Panel
                contract="report/metric-capture"
                title="원칙과 안전장치의 효과"
                hint="시장 상황과 무관하게 원칙의 효과를 보여주는 지표입니다."
              >
                <div className="grid gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-2 xl:grid-cols-4">
                  {view.derivedCards.map((card) => (
                    <div key={card.key} className="bg-panel px-4 py-4">
                      <p className="text-eyebrow font-semibold uppercase text-faint">{card.label}</p>
                      <p className="mt-2">
                        <Numeric
                          value={card.value}
                          format={
                            card.format === 'SIGNED_RATIO'
                              ? (v) => formatSignedRatio(v, 1)
                              : (v) => formatRatio(v, 1)
                          }
                          className="text-2xl font-semibold text-ink"
                        />
                      </p>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel
                contract="report/scenario-capture"
                title="Baseline / Guide / Strict"
                hint="검증된 artifact 안의 세 시나리오를 같은 조건에서 비교합니다."
              >
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
                      <th className="pb-2 font-normal">시나리오</th>
                      <th className="pb-2 text-right font-normal">CAGR</th>
                      <th className="pb-2 text-right font-normal">MDD</th>
                      <th className="pb-2 text-right font-normal">Sharpe</th>
                      <th className="pb-2 text-right font-normal">CVaR 95</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.strategies.map((row) => (
                      <tr key={row.strategy} className="border-b border-line/60 last:border-0">
                        <td className="py-2.5 pr-3 font-mono text-ink">{row.strategy}</td>
                        <td className="py-2.5 text-right">
                          <Numeric value={row.metrics.cagr} format={(v) => formatSignedRatio(v, 1)} />
                        </td>
                        <td className="py-2.5 text-right">
                          <Numeric value={row.metrics.mdd} format={(v) => formatRatio(v, 1)} />
                        </td>
                        <td className="py-2.5 text-right">
                          <Numeric value={row.metrics.sharpe} format={(v) => formatDecimal(v, 2)} />
                        </td>
                        <td className="py-2.5 text-right">
                          <Numeric value={row.metrics.cvar95} format={(v) => formatRatio(v, 1)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {view.fixtureClass === 'SYNTHETIC_FAKE_E2E' ? (
                  <p className="mt-4 text-[12px] text-warn">
                    이 결과는 합성 데이터입니다. 보고서에 성과로 인용하지 마세요.
                  </p>
                ) : null}
              </Panel>
            </>
          )}
        </AsyncBoundary>
      ) : null}
    </div>
  );
}
