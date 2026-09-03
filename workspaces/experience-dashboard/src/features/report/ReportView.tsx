'use client';

import { useState } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { IdInput } from '@/shared/ui/IdInput';
import { DecisionRail } from '@/shared/ui/Decision';
import { useResource } from '@/shared/lib/useResource';
import { ID_PATTERN } from '@/shared/api/endpoints';
import { formatDecimal, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import { loadBacktestReportView } from '@/features/backtest-report/viewModel';
import { loadRiskResultView } from '@/features/order-review/viewModel';

/**
 * 캡처용 화면.
 * 자동 캡처 기능은 범위 밖이므로, 여기서는 "한 화면에 들어가는 배치"와 캡처 체크리스트만 제공한다.
 */
const CAPTURE_LIST = [
  { figure: '그림 4', title: 'RiskEngine 판단 흐름', where: '아래 판정 레일과 위반 목록' },
  { figure: '표 6', title: 'Baseline/Guide/Strict 검증 시나리오', where: '아래 시나리오 비교표' },
  { figure: '표 5', title: '모델별 역할 비교', where: '모델 비교 화면의 모델별 성과 표' },
  { figure: '그림 6', title: '화면 와이어프레임', where: '원칙 설정 / 주문 검토 / 금융 가이드 화면' },
  { figure: '표 10', title: '성과지표', where: '아래 원칙 효과 카드' },
];

export function ReportView() {
  const [decisionId, setDecisionId] = useState('');
  const [runId, setRunId] = useState('');

  const decisionValid = ID_PATTERN.decisionId.test(decisionId);
  const runValid = ID_PATTERN.runId.test(runId);

  const decision = useResource(() => loadRiskResultView(decisionId), [decisionId], decisionValid);
  const backtest = useResource(() => loadBacktestReportView(runId), [runId], runValid);

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

      <div className="grid gap-4 lg:grid-cols-2">
        <IdInput
          label="캡처할 판정 ID"
          hint="차단이나 보류가 난 판정을 넣으면 안전장치가 동작한 화면이 만들어집니다."
          placeholder="dec_00000000000000000001"
          value={decisionId}
          onChange={setDecisionId}
          pattern={ID_PATTERN.decisionId}
          patternHint="dec_ 로 시작해야 합니다."
        />
        <IdInput
          label="캡처할 백테스트 실행 ID"
          hint="Baseline / Guide / Strict 비교표와 지표 카드를 만듭니다."
          placeholder="demo_s8_offline_0001"
          value={runId}
          onChange={setRunId}
          pattern={ID_PATTERN.runId}
          patternHint="run_ 또는 demo_ 로 시작해야 합니다."
        />
      </div>

      {decisionValid ? (
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
      ) : null}

      {runValid ? (
        <AsyncBoundary state={backtest.state} onRetry={backtest.reload}>
          {(view) => (
            <>
              <Panel
                contract="report/metric-capture"
                title="원칙과 안전장치의 효과"
                hint="시장 상황과 무관하게 원칙의 효과를 보여주는 지표입니다."
              >
                <div className="grid gap-px overflow-hidden rounded-tile border border-line bg-line sm:grid-cols-2 xl:grid-cols-4">
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
                hint={`실행 ${view.runId}`}
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
