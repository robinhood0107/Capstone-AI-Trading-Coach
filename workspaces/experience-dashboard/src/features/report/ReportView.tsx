'use client';

import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { DecisionRail } from '@/shared/ui/Decision';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { empty, ready } from '@/shared/lib/viewState';
import type { OwnerPerformanceReport } from '@/shared/api/wire';
import { useLatestRun } from '@/shared/api/latestRun';
import { LatestRunFallback } from '@/shared/ui/LatestRunFallback';
import { formatDecimal, formatKstDateTime, formatRatio, formatSignedRatio } from '@/shared/lib/format';
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
  const { runId, pending, failed, errorMessage, reload: reloadLatest } = useLatestRun('backtests');

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
  const performance = useResource<OwnerPerformanceReport>(async () => {
    const { data } = await api.dashboardPerformanceReport();
    return ready(data, data.report.generatedAt);
  }, []);

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

      <AsyncBoundary state={performance.state} onRetry={performance.reload}>
        {(data) => {
          const report = data.report;
          const fixed = report.sections.fixedDailyForecast;
          const actual = report.sections.actualTrading;
          const replay = report.sections.recalculatedBacktest;
          return (
            <Panel
              contract="owner-performance-report.v1"
              title="누적 성과 보고서"
              hint="재계산 백테스트, 매일 고정한 예측의 실현 평가, 실제 운용 손익을 서로 섞지 않습니다."
            >
              {data.lastRefreshStatus === 'FAILED_LAST_SUCCESS_PRESERVED' ? (
                <p className="mb-4 rounded-tile border border-hold/40 bg-hold/5 px-4 py-3 text-[12px] text-muted">
                  최근 갱신 실패: {data.lastFailureCode ?? '원인 미상'} · 마지막 성공본을 표시합니다.
                </p>
              ) : null}
              <dl className="grid gap-3 text-[12px] text-muted sm:grid-cols-2 xl:grid-cols-4">
                <div><dt className="text-faint">생성 시각</dt><dd>{formatKstDateTime(report.generatedAt) ?? '미상'}</dd></div>
                <div><dt className="text-faint">source 종료일</dt><dd>{report.sourceEnd}</dd></div>
                <div><dt className="text-faint">원칙 버전</dt><dd>v{report.principleVersion}</dd></div>
                <div><dt className="text-faint">거래비용</dt><dd>{report.costBps} bps</dd></div>
              </dl>
              <p className="mt-4 rounded-tile border border-line px-4 py-3 text-[12px] text-muted">
                모델 채택 상태: {report.modelAdoption.state} · 현재 {report.modelAdoption.currentModel}
                {report.modelAdoption.candidateId === null ? ' · 두 기준을 모두 통과한 후보 없음' : ''}
                {report.modelAdoption.automaticActivation ? '' : ' · 자동 production 전환 금지'}
              </p>
              <div className="mt-5 grid gap-3 lg:grid-cols-3">
                <div className="rounded-tile border border-line px-4 py-4">
                  <p className="text-eyebrow font-semibold uppercase text-faint">재계산 백테스트</p>
                  <p className="mt-2 text-[13px] text-muted">Guide 순수익</p>
                  <Numeric value={replay.guideNetReturn} format={(v) => formatSignedRatio(v, 2)} className="text-xl font-semibold" />
                </div>
                <div className="rounded-tile border border-line px-4 py-4">
                  <p className="text-eyebrow font-semibold uppercase text-faint">고정 예측 실현 평가</p>
                  <p className="mt-2 text-[13px] text-muted">RMSE · 실현 {fixed.realizedCount} / 대기 {fixed.pendingCount}</p>
                  <Numeric value={fixed.rmse} format={(v) => formatRatio(v, 2)} className="text-xl font-semibold" />
                </div>
                <div className="rounded-tile border border-line px-4 py-4">
                  <p className="text-eyebrow font-semibold uppercase text-faint">실제 운용 손익</p>
                  <p className="mt-2 text-[13px] text-muted">{actual.status} · 미실현 {actual.unrealizedStatus}</p>
                  <Numeric value={actual.realizedPnlKrw} format={(v) => `${formatDecimal(v, 0)}원`} className="text-xl font-semibold" />
                </div>
              </div>
            </Panel>
          );
        }}
      </AsyncBoundary>

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
      ) : (
        // 예전에는 `: null` 이라 조회가 503 이면 이 절이 문구 하나 없이 통째로 사라졌다.
        // 보고서에서 절이 조용히 없어지는 것은 값이 틀린 것보다 알아채기 어렵다.
        <LatestRunFallback
          pending={pending}
          failed={failed}
          errorMessage={errorMessage}
          onRetry={reloadLatest}
          emptyText="아직 등록된 검증 결과가 없어 이 절을 만들지 못했습니다."
        />
      )}
    </div>
  );
}
