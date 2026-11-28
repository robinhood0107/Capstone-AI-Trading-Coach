'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { riskCodeLabel, severityLabel } from '@/shared/lib/labels';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Disclosure } from '@/shared/ui/Disclosure';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { DecisionBadge, DecisionRail, decisionGloss } from '@/shared/ui/Decision';
import { useResource } from '@/shared/lib/useResource';
import { ID_PATTERN } from '@/shared/api/endpoints';
import { formatKstDateTime, formatRatio } from '@/shared/lib/format';
import { decisionMetricName, loadRiskResultView, type ReasonDisposition } from './viewModel';
import { OrderTicket } from './OrderTicket';
import { DecisionEvidence } from './DecisionEvidence';
import { Button } from '@/shared/ui/Button';
import { putJournalHandoff } from '@/shared/lib/journalHandoff';
import { PRINCIPLE_EDIT_ANCHOR, rememberReviewedPrinciple } from '@/shared/lib/principleReview';
import { FillsPanel } from './FillsPanel';
import type { DecisionRiskItemProjection } from '@/shared/api/wire';
import { api } from '@/shared/api/endpoints';
import { InstrumentIdentity, instrumentMap } from '@/shared/ui/InstrumentIdentity';
import { LIVE_REFRESH_MS } from '@/shared/lib/liveRefresh';

const DISPOSITION_META: Record<ReasonDisposition, { title: string; note: string; accent: string }> = {
  VIOLATION: { title: '원칙 위반', note: '기준을 실제로 넘은 항목입니다.', accent: 'border-block' },
  ISSUE: { title: '필수 근거 없음', note: '보류(HOLD)를 만드는 항목입니다.', accent: 'border-hold' },
  WARNING: { title: '경고', note: '진행은 가능하지만 확인이 필요합니다.', accent: 'border-warn' },
  ABSTENTION: {
    title: '평가하지 않음',
    note: '이번 주문에 해당하지 않거나 근거가 없어 비교하지 않은 항목입니다. 위반이 아닙니다.',
    accent: 'border-line',
  },
};

const ORDER: ReasonDisposition[] = ['VIOLATION', 'ISSUE', 'WARNING', 'ABSTENTION'];

const SEVERITY_TONE: Record<string, string> = {
  INFO: 'text-muted',
  WARN: 'text-warn',
  BLOCK: 'text-block',
};

export function OrderReviewView() {
  const router = useRouter();
  const catalog = useResource(async () => {
    const { data } = await api.instrumentDisplayCatalog();
    return { kind: 'ready' as const, data, asOf: null };
  }, []);
  const instruments = instrumentMap(
    catalog.state.kind === 'ready' || catalog.state.kind === 'stale' ? catalog.state.data.items : [],
  );
  const recent = useResource(async () => {
    const { data } = await api.dashboardRecentRiskResults();
    return { kind: 'ready' as const, data: data.items, asOf: data.items[0]?.asOf ?? null };
  }, []);
  const recentItems = recent.state.kind === 'ready' || recent.state.kind === 'stale' ? recent.state.data : [];
  const [selectedDecisionId, setSelectedDecisionId] = useState('');
  const decisionId = selectedDecisionId || recentItems[0]?.decisionId || '';
  const valid = ID_PATTERN.decisionId.test(decisionId);
  const { state, reload } = useResource(
    () => loadRiskResultView(decisionId),
    [decisionId],
    valid,
    LIVE_REFRESH_MS,
  );

  return (
    <div className="space-y-6">
      <AsyncBoundary state={recent.state} onRetry={recent.reload}>
        {(items) =>
          items.length > 0 ? (
            <Panel title="최근 주문 판정" hint="내 계정에 저장된 최신 판정을 시간순으로 보여줍니다.">
              <div className="flex flex-wrap gap-2">
                {items.map((item) => (
                  <button
                    key={item.decisionId}
                    type="button"
                    onClick={() => setSelectedDecisionId(item.decisionId)}
                    className={`rounded-full border px-3 py-1.5 text-[12px] ${
                      item.decisionId === decisionId
                        ? 'border-brand bg-brand text-on-brand'
                        : 'border-line text-muted hover:border-navy hover:text-navy'
                    }`}
                  >
                    <InstrumentIdentity symbol={item.symbol} instrument={instruments.get(item.symbol)} compact />
                    <span className="ml-2">{item.action} · {formatKstDateTime(item.asOf) ?? '시각 미상'}</span>
                  </button>
                ))}
              </div>
            </Panel>
          ) : (
            <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
              저장된 주문 판정이 없습니다. 자동운용이 판정을 마치면 여기에 표시됩니다.
            </p>
          )
        }
      </AsyncBoundary>

      <div id="decision-evidence" className="scroll-mt-4">
      {valid ? (
        <AsyncBoundary state={state} onRetry={reload}>
          {(view) => view.decisionId === decisionId ? (
            <div className="space-y-6">
              <Panel
                contract="dashboard-risk-result.v1"
                title={view.detail?.expired ? '지난 판정 · 다시 평가 필요' : '이 주문을 내도 되는지'}
                hint={view.detail?.expired
                  ? `당시 결과는 ${view.action}였지만 현재 주문에 사용할 수 없습니다.`
                  : decisionGloss(view.action)}
                actions={<DecisionBadge status={view.action} />}
              >
                {view.detail?.expired ? (
                  <p className="mb-4 border-l-2 border-hold bg-hold/5 px-3 py-2 text-[13px] text-ink">
                    이 기록은 과거 판정입니다. 현재 주문은 새 판정을 받은 뒤에만 제출할 수 있습니다.
                  </p>
                ) : null}
                <DecisionRail status={view.action} />

                {view.detail ? (
                  <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-5 md:grid-cols-4">
                    <Field
                      label="주문 제출 가능"
                      value={
                        view.detail.expired
                          ? '불가 · 재평가 필요'
                          : view.detail.canSubmitOrder
                            ? '가능'
                            : '불가'
                      }
                    />
                    <Field
                      label="운영 모드"
                      value={view.detail.mode === 'GUIDE' ? 'Guide · 경고 중심' : 'Strict · 차단 중심'}
                    />
                    <Field
                      label="계좌 구분"
                      value={
                        view.detail.portfolioSource === 'KIS_MOCK' ? 'KIS 모의계좌' : '내부 가상원장'
                      }
                    />
                    <Field label="원칙 버전" value={`v${view.detail.principleVersion}`} mono />
                    <Field
                      label="판정 유효시각"
                      value={formatKstDateTime(view.detail.validUntil) ?? '미상'}
                      mono
                    />
                  </dl>
                ) : null}
              </Panel>

              <DecisionEvidence view={view}>
                {view.detail && view.detail.violatedPrinciples.length > 0 ? (
                  <Link
                    href={'/principles#' + PRINCIPLE_EDIT_ANCHOR}
                    onClick={() => rememberReviewedPrinciple(view.detail!.principleId, view.detail!.principleVersion)}
                    className="text-[13px] font-medium text-navy underline"
                  >
                    현재 원칙 수정
                  </Link>
                ) : null}
                <Button
                  variant="secondary"
                  onClick={() => {
                    const detail = view.detail;
                    const facts = detail ? [
                      `당시 원칙 v${detail.principleVersion} · 평가 준비도 정책 ${detail.readinessPolicyVersion}`,
                      ...detail.violatedPrinciples.map((item) => `${item.ruleId} · 당시 값 ${item.observed} · 당시 기준 ${item.limit}`),
                      ...detail.reasons.filter((reason) => reason.disposition !== 'VIOLATION')
                        .map((reason) => [reason.code, reason.headline, ...reason.sources].join(' · ')),
                    ] : [view.detailUnavailableReason, ...view.summaryReasons];
                    putJournalHandoff({
                      title: `주문 판정 ${view.action}`,
                      content: facts.filter(Boolean).join('\n').slice(0, 8192),
                      decisionId: view.decisionId,
                    });
                    router.push('/journal');
                  }}
                >
                  학습일지로 남기기
                </Button>
              </DecisionEvidence>

              <Panel
                contract="dashboard-risk-result.v1 · reasons / principles"
                title="서버가 준 판정 요약"
                hint="서버 판정의 같은 사유를 묶고 관련 규칙을 함께 표시합니다."
              >
                <div className="grid gap-6 md:grid-cols-2">
                  <div>
                    <p className="text-eyebrow font-semibold uppercase text-faint">사유</p>
                    <ul className="mt-2 space-y-2">
                      {view.summaryReasons.length === 0 ? (
                        <li className="text-[13px] text-faint">표시할 사유가 없습니다.</li>
                      ) : (
                        view.summaryReasons.map((reason, index) => (
                          <li key={`${index}:${reason}`} className="text-[13px] leading-6 text-ink">
                            · {reason}
                          </li>
                        ))
                      )}
                    </ul>
                  </div>
                  <div>
                    <p className="text-eyebrow font-semibold uppercase text-faint">관련된 내 원칙</p>
                    <ul className="mt-2 space-y-2">
                      {view.summaryPrinciples.length === 0 ? (
                        <li className="text-[13px] text-faint">관련된 원칙이 없습니다.</li>
                      ) : (
                        view.summaryPrinciples.map((principle, index) => (
                          <li key={`${index}:${principle}`} className="text-[13px] leading-6 text-ink">
                            · {principle}
                          </li>
                        ))
                      )}
                    </ul>
                  </div>
                </div>

                {view.summaryRiskItems.length > 0 ? (
                  <ul className="mt-6 divide-y divide-line/60 border-t border-line pt-2">
                    {view.summaryRiskItems.map((item, index) => (
                      <li key={`${index}:${item.code}`} className="flex items-start justify-between gap-4 py-2.5">
                        <div className="min-w-0">
                          <p className="text-[13px] leading-5 text-ink">{item.summary}</p>
                          {/* 규칙 코드를 그대로 두면 읽는 사람이 해독해야 한다. 사전에 없는
                              코드는 코드 그대로 나오므로 새 규칙이 조용히 사라지지 않는다. */}
                          <p className="text-[11px] leading-5 text-faint">
                            {riskCodeLabel(item.code).label}
                            {riskCodeLabel(item.code).meaning
                              ? ` · ${riskCodeLabel(item.code).meaning}`
                              : ''}
                          </p>
                        </div>
                        <span
                          className={`shrink-0 text-[12px] font-medium ${SEVERITY_TONE[item.severity] ?? 'text-muted'}`}
                        >
                          {severityLabel(item.severity).label}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </Panel>

              {view.detail ? (
                <Disclosure
                  label="판정 근거 자세히 보기"
                  hint="서버의 사유 원문, 별도 위험 항목과 입력값 해시를 확인합니다."
                >
                  <div className="space-y-6">
                  <Panel
                    contract="GET /api/v1/decisions/{decisionId}"
                    title="판정 근거 상세"
                    hint="네 종류의 사유는 의미가 서로 다릅니다. 같은 목록으로 합치지 않습니다."
                  >
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                      {ORDER.map((disposition) => {
                        const rows = view.detail!.reasons.filter(
                          (reason) => reason.disposition === disposition,
                        );
                        const meta = DISPOSITION_META[disposition];
                        return (
                          <div key={disposition} className={`border-t-2 ${meta.accent} pt-3`}>
                            <p className="text-[13px] font-semibold text-ink">{meta.title}</p>
                            <p className="mt-1 text-[12px] leading-5 text-muted">{meta.note}</p>
                            <ul className="mt-3 space-y-3">
                              {rows.length === 0 ? (
                                <li className="text-[13px] text-faint">해당 없음</li>
                              ) : (
                                rows.map((reason) => (
                                  <li key={reason.id}>
                                    <p className="text-[13px] font-medium leading-5 text-ink">
                                      {reason.headline}
                                    </p>
                                    {reason.detail ? (
                                      <p className="mt-0.5 text-[13px] leading-5 text-muted">{reason.detail}</p>
                                    ) : null}
                                    <details className="mt-1 text-[11px] leading-5 text-faint">
                                      <summary className="cursor-pointer">관련 규칙·원문 보기</summary>
                                      {reason.ruleNames.length > 0 ? (
                                        <p>관련 규칙: {reason.ruleNames.join(', ')}</p>
                                      ) : null}
                                      <p className="font-mono">{reason.code}</p>
                                      {reason.rawMessages.map((message) => (
                                        <p key={message}>{message}</p>
                                      ))}
                                    </details>
                                  </li>
                                ))
                              )}
                            </ul>
                          </div>
                        );
                      })}
                    </div>
                  </Panel>

                  <Panel
                      contract="riskDecision.riskItems"
                      title="별도 위험 항목"
                      hint="위험 항목 기록이며 위의 판정 입력값과 별개입니다."
                    >
                      {view.detail.riskItems.length === 0 ? (
                        <p className="text-[13px] text-muted">별도로 기록된 위험 항목은 없습니다.</p>
                      ) : (
                        <ul className="divide-y divide-line/60">
                          {view.detail.riskItems.map((item, index) => (
                            <RiskItemRow key={`${index}:${item.metric}`} item={item} />
                          ))}
                        </ul>
                      )}
                    </Panel>

                  <p className="font-mono text-[11px] leading-5 text-faint">
                    semanticInputHash {view.detail.semanticInputHash.slice(0, 16)}… ·
                    snapshotArtifactHash {view.detail.snapshotArtifactHash.slice(0, 16)}…
                  </p>
                  </div>
                </Disclosure>
              ) : null}
            </div>
          ) : null}
        </AsyncBoundary>
      ) : null}
      </div>

      <OrderTicket onEvaluated={(id) => {
        setSelectedDecisionId(id);
        if (id === decisionId) reload();
        recent.reload();
      }} />
      <FillsPanel />
    </div>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-eyebrow font-semibold uppercase text-faint">{label}</dt>
      <dd className={`mt-1 text-[14px] text-ink ${mono ? 'tnum font-mono' : ''}`}>{value}</dd>
    </div>
  );
}

function RiskItemRow({ item }: { item: DecisionRiskItemProjection }) {
  return (
    <li className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0">
            <p className="text-[13px] text-ink">{decisionMetricName(item.metric)}</p>
            <p className="text-[11px] text-faint">
              {item.source === 'PORTFOLIO' ? '계좌' : item.source === 'KIS' ? '증권사' : item.source}
              {' · '}{severityLabel(item.severity).label}
        </p>
      </div>
      <Numeric value={item.value} format={(v) => formatRatio(v, 3)} className="text-[14px]" />
    </li>
  );
}
