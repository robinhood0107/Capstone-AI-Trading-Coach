import type { ReactNode } from 'react';
import { formatKstDateTime } from '@/shared/lib/format';
import { DecisionBadge } from '@/shared/ui/Decision';
import { Panel } from '@/shared/ui/Panel';
import { decisionMetricName, displayDecisionInputMetric, type RiskResultView } from './viewModel';

const REASON_LABEL = { ISSUE: '필수 근거 없음', WARNING: '경고', ABSTENTION: '평가하지 않음' };
const ACCOUNT_LABEL = { KIS_MOCK: 'KIS 모의계좌', INTERNAL_PAPER: '내부 가상원장' };

export function DecisionEvidence({
  view, title = '당시 판정 근거', children,
}: { view: RiskResultView; title?: string; children?: ReactNode }) {
  const { detail } = view;
  return (
    <Panel contract="decision-evidence" title={title} actions={<DecisionBadge status={view.action} />}>
      {detail ? (
        <>
          <dl className="grid gap-3 text-[13px] text-ink sm:grid-cols-3">
            <div><dt className="text-faint">당시 원칙 버전</dt><dd>v{detail.principleVersion}</dd></div>
            <div><dt className="text-faint">평가 준비도 정책</dt><dd>{detail.readinessPolicyVersion}</dd></div>
            <div><dt className="text-faint">당시 평가 계좌</dt><dd>{ACCOUNT_LABEL[detail.portfolioSource]}</dd></div>
          </dl>
          {detail.violatedPrinciples.length > 0 ? (
            <table className="mt-4 w-full text-[13px]">
              <thead className="border-b border-line text-left text-faint">
                <tr>
                  <th scope="col" className="pb-2 font-normal">위반 원칙</th>
                  <th scope="col" className="pb-2 text-right font-normal">당시 값</th>
                  <th scope="col" className="pb-2 text-right font-normal">당시 기준</th>
                </tr>
              </thead>
              <tbody>
                {detail.violatedPrinciples.map((item, index) => (
                  <tr key={item.ruleId + ':' + index} className="border-b border-line/60">
                    <th scope="row" className="py-2.5 pr-2 text-left font-medium text-ink">
                      {item.name}
                      <code className="mb-1 block break-all text-[11px] font-normal text-muted">{item.ruleId}</code>
                      <DecisionBadge status={item.severity} size="sm" />
                    </th>
                    <td className="tnum whitespace-nowrap py-2.5 pl-2 text-right text-block">{item.observed}</td>
                    <td className="tnum whitespace-nowrap py-2.5 pl-2 text-right text-muted">{item.limit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <ul className="mt-3 divide-y divide-line/60 text-[13px]">
            {detail.reasons.map((reason) => reason.disposition === 'VIOLATION' ? null : (
              <li key={reason.id} className="py-2">
                <p className="text-ink">
                  {REASON_LABEL[reason.disposition]}{reason.headline !== reason.code ? ' · ' + reason.headline : ''}
                </p>
                <code className="break-all text-[11px] text-muted">{reason.code}</code>
                {reason.sources.length > 0 ? <p className="break-all text-[12px] text-muted">출처 {reason.sources.join(', ')}</p> : null}
              </li>
            ))}
          </ul>
          {detail.riskItems.length > 0 ? (
            <ul className="mt-2 text-[12px] leading-5 text-muted">
              {detail.riskItems.map((item, index) => (
                <li key={item.metric + ':' + index} className="break-words">
                  {decisionMetricName(item.metric)} 출처 {item.source}
                  {item.sourceRefs?.length ? ' · ' + item.sourceRefs.join(', ') : ''}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-4 border-t border-line pt-3 text-[13px] text-muted">
            <p className="font-medium text-ink">판정에 저장된 입력값</p>
            {detail.inputMetricsUnavailable ? (
              <p className="mt-2">당시 입력값을 불러오지 못했습니다.</p>
            ) : detail.inputMetrics.length > 0 ? (
              <dl className="mt-2 grid gap-3 sm:grid-cols-2">
                {detail.inputMetrics.map((item) => {
                  const { name, value } = displayDecisionInputMetric(item);
                  return <div key={item.metric}>
                    <dt>{name}</dt>
                    <dd className="tnum text-ink">{value}</dd>
                    {item.observedAt ? <dd className="text-[11px]">관측 {formatKstDateTime(item.observedAt)}</dd> : null}
                  </div>;
                })}
              </dl>
            ) : <p className="mt-2">이 판정에 저장된 입력값이 없습니다.</p>}
          </div>
        </>
      ) : (
        <>
          <p className="text-[13px] text-warn">{view.detailUnavailableReason}</p>
          <ul className="mt-2 text-[13px] text-muted">
            {view.summaryReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </>
      )}
      {view.summaryRiskItems.length > 0 ? (
        <ul className="mt-3 space-y-1 text-[12px] text-muted">
          {view.summaryRiskItems.map((item, index) => (
            <li key={item.code + ':' + index}>
              <code className="break-all">{item.code}</code>{' · '}{item.summary}
            </li>
          ))}
        </ul>
      ) : null}
      {children ? <div className="mt-4 flex flex-wrap items-center gap-3">{children}</div> : null}
    </Panel>
  );
}
