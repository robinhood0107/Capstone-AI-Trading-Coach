'use client';

import { useState } from 'react';
import Link from 'next/link';
import { api } from '@/shared/api/endpoints';
import { useSession } from '@/shared/api/session';
import { toErrorState, useResource } from '@/shared/lib/useResource';
import { formatKrw, formatKstDateTime } from '@/shared/lib/format';
import { ready, type ViewState } from '@/shared/lib/viewState';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import type {
  AutomationPolicyV2,
  AutomationPositionV3,
  AutomationRunV3,
  AutomationStatusV3,
  InstrumentDisplayCatalog,
} from '@/shared/api/wire';
import { InstrumentIdentity, instrumentMap } from '@/shared/ui/InstrumentIdentity';
import {
  AUTOMATION_BLOCKER_LABELS_V3,
  AUTOMATION_EVIDENCE_LINKS,
  AUTOMATION_EXIT_REASON_LABELS,
  MARKET_HISTORY_LABELS,
  AUTOMATION_PRESETS,
  AUTOMATION_STATE_LABELS,
  bpsToPercent,
  percentToBps,
  presetFor,
  slotBudgetKrw,
  validateAutomationPolicy,
} from './policy';
import { AutomationPersistenceNote } from './AutomationPersistenceNote';

/**
 * Kill Switch 조작.
 *
 * **정지와 해제가 대칭이 아니다.** 정지는 누구나 할 수 있고, 해제는 ADMIN 만 된다
 * (`KillSwitchTransitionPolicy.kt:22` — 안전 정지는 열되 재가동은 닫는다). 그래서 USER 에게는
 * 해제 버튼을 아예 두지 않고 **왜 없는지**를 적는다. 회색 버튼만 남기면 고장으로 읽힌다.
 *
 * 화면에서 막아도 서버가 최종 판단이다. 403 이 오면 그대로 보여 준다.
 */
function KillSwitchControl({ active, onChanged }: { active: boolean; onChanged: () => void }) {
  const { user } = useSession();
  const isAdmin = user?.role === 'ADMIN';
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(next: boolean) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.changeKillSwitch(next, next ? 'USER_MANUAL_STOP' : undefined);
      setConfirming(false);
      onChanged();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : 'Kill Switch 를 바꾸지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-5 border-t border-line pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-[13px] leading-6 text-muted">
          {active
            ? 'Kill Switch가 작동 중입니다. 새 주문이 나가지 않습니다.'
            : '문제가 보이면 Kill Switch로 새 주문을 즉시 멈출 수 있습니다. 켜는 것은 누구나 할 수 있고, 다시 끄는 것은 관리자만 할 수 있습니다.'}
        </p>

        {active ? (
          isAdmin ? (
            <Button
              disabled={busy}
              onClick={() => void change(false)}
              className="rounded-full border border-line px-4 py-1.5 text-[13px] font-semibold text-muted hover:border-navy hover:text-navy"
            >
              {busy ? '해제 중' : 'Kill Switch 해제'}
            </Button>
          ) : (
            <span className="text-[12px] text-faint">해제는 관리자만 할 수 있습니다.</span>
          )
        ) : confirming ? (
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-[12px] text-block">지금 즉시 새 주문이 멈춥니다.</span>
            <Button
              disabled={busy}
              onClick={() => void change(true)}
              className="rounded-full border border-block px-4 py-1.5 text-[13px] font-semibold text-block"
            >
              {busy ? '멈추는 중' : '멈춥니다'}
            </Button>
            <Button
              disabled={busy}
              onClick={() => setConfirming(false)}
              className="rounded-full border border-line px-3 py-1.5 text-[13px] text-muted"
            >
              취소
            </Button>
          </span>
        ) : (
          <Button
            onClick={() => setConfirming(true)}
            className="rounded-full border border-line px-4 py-1.5 text-[13px] font-semibold text-muted hover:border-block hover:text-block"
          >
            Kill Switch 켜기
          </Button>
        )}
      </div>

      {error ? (
        <p className="mt-3 border-l-2 border-block bg-block/5 px-3 py-2 text-[13px] leading-6 text-ink">
          {error}
        </p>
      ) : null}
    </div>
  );
}

interface AutomationData {
  status: AutomationStatusV3;
  runs: AutomationRunV3[];
  positions: AutomationPositionV3[];
  instruments: InstrumentDisplayCatalog;
}

interface Draft {
  capitalLimitKrw: string;
  stopLossPercent: string;
  takeProfitPercent: string;
}

/**
 * 이 화면은 v3 를 본다.
 *
 * v2 로는 자동운용이 **왜** 그렇게 판단했는지를 보여 줄 수 없다 — ATR 추적손절, 보유 기간,
 * AI 판단 근거가 전부 v3 에만 있다. 현황 화면은 실현손익 요약(`realizedSummary`)이 필요한데
 * v3 포지션 페이지에는 그 필드가 없어서 계속 v2 를 본다.
 */
async function load(): Promise<ViewState<AutomationData>> {
  const [status, runs, positions, instruments] = await Promise.all([
    api.automationStatusV3(),
    api.automationRunsV3(),
    api.automationPositionsV3(),
    api.instrumentDisplayCatalog(),
  ]);
  return ready(
    { status: status.data, runs: runs.data.items, positions: positions.data.items, instruments: instruments.data },
    status.data.policy?.updatedAt ?? null,
  );
}

function draftFrom(policy: AutomationPolicyV2 | null): Draft {
  return {
    capitalLimitKrw: policy ? String(policy.capitalLimitKrw) : '',
    stopLossPercent: String(bpsToPercent(policy?.stopLossBps ?? 500)),
    takeProfitPercent: String(bpsToPercent(policy?.takeProfitBps ?? 1000)),
  };
}

function numericDraft(draft: Draft) {
  return {
    capitalLimitKrw: draft.capitalLimitKrw.trim() === '' ? 0 : Number(draft.capitalLimitKrw),
    stopLossBps: percentToBps(Number(draft.stopLossPercent)),
    takeProfitBps: percentToBps(Number(draft.takeProfitPercent)),
  };
}

export function AutomationView() {
  const { state, reload } = useResource(load, []);
  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(data) => (
        <AutomationBody
          key={`${data.status.controlVersion}:${data.status.policy?.version ?? 0}`}
          data={data}
          onReload={reload}
        />
      )}
    </AsyncBoundary>
  );
}

function AutomationBody({ data, onReload }: { data: AutomationData; onReload: () => void }) {
  const [draft, setDraft] = useState(() => draftFrom(data.status.policy));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const values = numericDraft(draft);
  const errors = validateAutomationPolicy(values);
  const selectedPreset = presetFor(values.stopLossBps, values.takeProfitBps);
  const saved = data.status.policy;
  const dirty =
    !saved ||
    saved.capitalLimitKrw !== values.capitalLimitKrw ||
    saved.stopLossBps !== values.stopLossBps ||
    saved.takeProfitBps !== values.takeProfitBps;
  const locked = data.status.controlState !== 'DISARMED';

  function applyPreset(stopLossBps: number, takeProfitBps: number) {
    setDraft((current) => ({
      ...current,
      stopLossPercent: String(bpsToPercent(stopLossBps)),
      takeProfitPercent: String(bpsToPercent(takeProfitBps)),
    }));
    setNotice(null);
  }

  async function savePolicy() {
    if (busy || locked || errors.length > 0) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.putAutomationPolicyV2({
        expectedVersion: saved?.version ?? 0,
        ...values,
      });
      setNotice({ tone: 'ok', text: '자동운용 정책을 새 버전으로 저장했습니다.' });
      onReload();
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setNotice({
        tone: 'error',
        text:
          error.kind === 'error' && error.code === 'CONFLICT'
            ? '다른 화면에서 정책이 먼저 바뀌었습니다. 최신 값을 다시 불러온 뒤 직접 저장하세요.'
            : error.kind === 'error'
              ? error.message
              : '정책을 저장하지 못했습니다.',
      });
    } finally {
      setBusy(false);
    }
  }

  async function arm() {
    if (
      busy ||
      dirty ||
      !data.status.canArm ||
      data.status.blockers.length > 0 ||
      !data.status.accountId ||
      !saved
    ) {
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await api.armAutomationV2({
        accountId: data.status.accountId,
        policyId: saved.policyId,
        expectedPolicyVersion: saved.version,
        expectedControlVersion: data.status.controlVersion,
      });
      setNotice({ tone: 'ok', text: '자동운용을 시작 대기 상태로 전환했습니다.' });
      onReload();
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setNotice({
        tone: 'error',
        text: error.kind === 'error' ? error.message : '자동운용을 시작하지 못했습니다.',
      });
      onReload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        contract="GET /api/v3/automation/status"
        title="현재 자동운용 상태"
        hint="Kill Switch와 자동운용 상태는 서로 다른 값입니다. 서버가 내려준 상태를 그대로 표시합니다."
        actions={<StatusLabel status={data.status} />}
      >
        <dl className="grid gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          <StatusField label="계좌 모드" value={modeLabel(data.status.brokerageMode)} />
          <StatusField label="열린 포지션" value={`${data.status.openPositionCount} / 5`} mono />
          <StatusField
            label="Kill Switch"
            value={data.status.killSwitchActive ? '작동 중' : '꺼짐'}
          />
          <StatusField label="정책 버전" value={saved ? `v${saved.version}` : '미설정'} mono />
          <StatusField
            label="AI 판단"
            value={data.status.aiJudgementEnabled ? `켜짐 · ${data.status.thinkingLevel}` : '꺼짐'}
          />
          <StatusField
            label="시세 이력"
            value={MARKET_HISTORY_LABELS[data.status.marketHistoryStatus]}
          />
          <StatusField
            label="봇 외 포지션"
            value={`${data.status.legacyOpenPositionCount}건`}
            mono
          />
        </dl>

        <div className="mt-4 flex justify-end">
          <Link href="/order-review" className="text-[13px] font-semibold text-navy hover:underline">
            최근 주문 판정 보기 →
          </Link>
        </div>

        <KillSwitchControl active={data.status.killSwitchActive} onChanged={onReload} />

        {data.status.blockers.length > 0 ? (
          <div className="mt-5 border-l-2 border-hold bg-hold/5 px-4 py-3">
            <p className="text-[13px] font-semibold text-ink">현재 시작할 수 없습니다</p>
            <ul className="mt-2 space-y-2">
              {data.status.blockers.map((blocker) => (
                <li key={blocker} className="text-[13px] leading-5 text-muted">
                  <span title={blocker}>{AUTOMATION_BLOCKER_LABELS_V3[blocker]}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Panel>

      <Panel
        contract="PUT /api/v2/automation/policy"
        title="예산과 매도 기준"
        hint="세 값만 입력합니다. AI와 LSTM은 후보 순위만 정하며 주문 수량을 직접 결정하지 않습니다."
        actions={
          <span className="font-mono text-[11px] uppercase text-faint">
            {selectedPreset === 'custom'
              ? '직접 입력'
              : AUTOMATION_PRESETS.find((preset) => preset.presetId === selectedPreset)?.label}
          </span>
        }
      >
        <div className="grid gap-px overflow-hidden rounded-card border border-line bg-line md:grid-cols-3">
          {AUTOMATION_PRESETS.map((preset) => (
            <Button
              key={preset.presetId}
              variant="secondary"
              disabled={locked}
              aria-pressed={selectedPreset === preset.presetId}
              onClick={() => applyPreset(preset.stopLossBps, preset.takeProfitBps)}
              className={`bg-panel px-4 py-4 text-left disabled:text-faint ${
                selectedPreset === preset.presetId ? 'ring-2 ring-inset ring-navy' : ''
              }`}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[14px] font-semibold text-ink">{preset.label}</span>
                <span className="tnum font-mono text-[12px] text-muted">
                  -{bpsToPercent(preset.stopLossBps)}% / +{bpsToPercent(preset.takeProfitBps)}%
                </span>
              </div>
              <p className="mt-2 text-[12px] leading-5 text-muted">{preset.description}</p>
            </Button>
          ))}
        </div>

        <div className="mt-6 grid gap-5 md:grid-cols-3">
          <PolicyInput
            label="최대 자동운용 금액"
            value={draft.capitalLimitKrw}
            min={10_000}
            max={10_000_000_000}
            step={10_000}
            suffix="원"
            disabled={locked}
            onChange={(value) => setDraft((current) => ({ ...current, capitalLimitKrw: value }))}
          />
          <PolicyInput
            label="손절률"
            value={draft.stopLossPercent}
            min={1}
            max={15}
            step={0.1}
            suffix="%"
            disabled={locked}
            onChange={(value) => setDraft((current) => ({ ...current, stopLossPercent: value }))}
          />
          <PolicyInput
            label="익절률"
            value={draft.takeProfitPercent}
            min={2}
            max={30}
            step={0.1}
            suffix="%"
            disabled={locked}
            onChange={(value) => setDraft((current) => ({ ...current, takeProfitPercent: value }))}
          />
        </div>

        <div className="mt-5 grid gap-4 border-t border-line pt-5 md:grid-cols-2">
          <div>
            <p className="text-eyebrow font-semibold uppercase text-faint">종목당 기본 슬롯</p>
            <p className="tnum mt-1 font-mono text-[16px] text-ink">
              {values.capitalLimitKrw > 0 ? formatKrw(slotBudgetKrw(values.capitalLimitKrw)) : '—'}
            </p>
            <p className="mt-1 text-[12px] leading-5 text-muted">
              최대 5개 포지션으로 나눈 기준입니다. 실제 수량은 원칙·잔고·매수가능수량 중 가장 작은
              한도로 계산합니다.
            </p>
          </div>
          <div className="text-[12px] leading-5 text-muted">
            <p>최대 자동운용 금액은 주문 한도이며 수익·원금 보장 금액이 아닙니다.</p>
            <p className="mt-1">
              손절·익절은 매 XKRX 세션 09:30 KST 평가 뒤 지정가 청산을 시도하며 즉시 체결을 보장하지
              않습니다.
            </p>
          </div>
        </div>

        {errors.length > 0 ? (
          <ul className="mt-4 border-l-2 border-block bg-block/5 px-3 py-2 text-[12px] leading-5 text-ink">
            {errors.map((error) => (
              <li key={error}>· {error}</li>
            ))}
          </ul>
        ) : null}

        {notice ? (
          <p
            className={`mt-4 border-l-2 px-3 py-2 text-[13px] leading-5 text-ink ${
              notice.tone === 'ok' ? 'border-allow bg-allow/5' : 'border-block bg-block/5'
            }`}
          >
            {notice.text}
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
          <p className="text-[12px] leading-5 text-muted">
            {locked ? '자동운용 중에는 정책을 변경할 수 없습니다.' : '저장 뒤 최신 정책만 시작에 사용할 수 있습니다.'}
          </p>
          <div className="flex items-center gap-2">
            <Button
                            disabled={!dirty || busy || locked || errors.length > 0}
              onClick={() => void savePolicy()}
              className="rounded-full border border-line px-3 py-1.5 text-[13px] text-ink disabled:bg-line disabled:text-faint"
            >
              {busy ? '처리 중' : '정책 저장'}
            </Button>
            {data.status.controlState === 'DISARMED' ? (
              <Button
                                disabled={
                  busy || dirty || !saved || !data.status.canArm || data.status.blockers.length > 0
                }
                onClick={() => void arm()}
                title={data.status.blockers.map((item) => AUTOMATION_BLOCKER_LABELS_V3[item]).join(' ')}
                variant="primary"
              >
                자동운용 시작
              </Button>
            ) : null}
          </div>
          <AutomationPersistenceNote status={data.status} />
        </div>
      </Panel>

      <div className="grid gap-6 xl:grid-cols-2">
        <PositionPanel positions={data.positions} instruments={data.instruments} />
        <RunPanel runs={data.runs} instruments={data.instruments} />
      </div>

      <Panel title="빠른 선택값의 근거" hint="연구 기반 고정 기본값이며 이 프로젝트 데이터에서 최적화한 값이나 수익 보장이 아닙니다.">
        <ul className="space-y-2 text-[12px] leading-5">
          {AUTOMATION_EVIDENCE_LINKS.map((source) => (
            <li key={source.href}>
              <a
                href={source.href}
                target="_blank"
                rel="noreferrer noopener"
                className="text-navy underline underline-offset-2"
              >
                {source.label}
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-4 border-t border-line pt-4 text-[12px] leading-5 text-muted">
          {data.status.brokerageMode === 'KIS_MOCK'
            ? 'KIS 모의계좌 전용입니다. 실제 계좌 주문은 실행하지 않습니다.'
            : '현재 선택된 모드는 내부 가상원장입니다. KIS 실패 시 이 모드로 자동 전환하지 않습니다.'}
        </p>
      </Panel>
    </div>
  );
}

function StatusLabel({ status }: { status: AutomationStatusV3 }) {
  const tone =
    status.projectionState === 'RUNNING'
      ? 'text-allow'
      : status.projectionState === 'HALTED'
        ? 'text-block'
        : status.projectionState === 'ARMED'
          ? 'text-warn'
          : 'text-muted';
  return (
    <span className={`font-mono text-[12px] font-semibold ${tone}`}>
      {AUTOMATION_STATE_LABELS[status.projectionState]}
    </span>
  );
}

function StatusField({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-panel px-4 py-4">
      <dt className="text-eyebrow font-semibold uppercase text-faint">{label}</dt>
      <dd className={`mt-2 text-[14px] text-ink ${mono ? 'tnum font-mono' : ''}`}>{value}</dd>
    </div>
  );
}

function PolicyInput({
  label,
  value,
  min,
  max,
  step,
  suffix,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  min: number;
  max: number;
  step: number;
  suffix: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-[13px] font-medium text-ink">{label}</span>
      <span className="mt-2 flex items-center rounded-full border border-line bg-panel focus-within:border-navy">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="tnum min-w-0 flex-1 bg-transparent px-3 py-2 text-right font-mono text-[14px] disabled:bg-surface disabled:text-faint"
        />
        <span className="border-l border-line px-3 text-[12px] text-muted">{suffix}</span>
      </span>
    </label>
  );
}

function PositionPanel({ positions, instruments }: { positions: AutomationPositionV3[]; instruments: InstrumentDisplayCatalog }) {
  const bySymbol = instrumentMap(instruments.items);
  return (
    <Panel contract="GET /api/v3/automation/positions" title="자동운용 포지션">
      {positions.length === 0 ? (
        <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] text-muted">
          자동운용이 보유한 포지션이 없습니다.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
              <tr>
                <th className="pb-2 font-normal">종목</th>
                <th className="pb-2 text-right font-normal">수량</th>
                <th className="pb-2 text-right font-normal">평균체결가</th>
                <th className="pb-2 text-right font-normal">ATR 추적손절</th>
                <th className="pb-2 text-right font-normal">보유 한도</th>
                <th className="pb-2 text-right font-normal">상태</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <tr key={position.positionId} className="border-b border-line/60 last:border-0">
                  <td className="py-2.5"><InstrumentIdentity symbol={position.symbol} instrument={bySymbol.get(position.symbol)} compact /></td>
                  <td className="tnum py-2.5 text-right font-mono">{position.quantity}</td>
                  <td className="tnum py-2.5 text-right font-mono">
                    {formatKrw(position.entryAverageFillPriceKrw)}
                  </td>
                  <td className="tnum py-2.5 text-right font-mono">
                    {position.trailingStopKrw === null ? (
                      // 아직 계산되지 않았다. 0 원으로 적으면 손절선이 바닥이라는 뜻이 된다.
                      <span className="text-faint">미산출</span>
                    ) : (
                      <>
                        {formatKrw(position.trailingStopKrw)}
                        <span className="ml-1 text-faint">
                          ATR{position.atrPeriod}×{(position.atrMultiplierMilli / 1000).toFixed(1)}
                        </span>
                      </>
                    )}
                  </td>
                  <td className="tnum py-2.5 text-right font-mono text-muted">
                    {position.maxHoldingSessions}세션
                  </td>
                  <td className="py-2.5 text-right text-muted">
                    {position.status === 'OPEN'
                      ? '보유 중'
                      : position.status === 'EXIT_PENDING'
                        ? '청산 대기'
                        : position.status === 'CLOSED'
                          ? '종료'
                          : '대사 확인 필요'}
                    {position.exitReason ? (
                      <span className="ml-1.5 text-faint">
                        · {AUTOMATION_EXIT_REASON_LABELS[position.exitReason]}
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function RunPanel({ runs, instruments }: { runs: AutomationRunV3[]; instruments: InstrumentDisplayCatalog }) {
  const bySymbol = instrumentMap(instruments.items);
  return (
    <Panel contract="GET /api/v3/automation/runs" title="최근 자동운용 실행">
      {runs.length === 0 ? (
        <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] text-muted">
          기록된 자동운용 실행이 없습니다.
        </p>
      ) : (
        <ul className="divide-y divide-line/60">
          {runs.map((run) => (
            <RunRow key={run.runId} run={run} bySymbol={bySymbol} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

/**
 * 실행 한 건. 펼치면 그날 AI 가 무엇을 읽고 그렇게 정했는지를 가져온다.
 *
 * 근거는 펼칠 때 처음 부른다 — 목록을 여는 것만으로 실행 수만큼 상세를 당길 이유가 없다.
 */
function RunRow({
  run,
  bySymbol,
}: {
  run: AutomationRunV3;
  bySymbol: ReturnType<typeof instrumentMap>;
}) {
  const [open, setOpen] = useState(false);
  const detail = useResource(
    async () => {
      const { data } = await api.automationRunDetailV3(run.runId);
      return ready(data, run.updatedAt);
    },
    [run.runId],
    open,
  );

  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-[12px] text-ink">{run.sessionDate}</p>
          <p className="mt-1 text-[11px] text-faint">
            {/* 근거가 0건이면 AI 판단 없이 넘어간 실행이다. 그 사실을 감추지 않는다. */}
            {run.evidenceCount > 0
              ? `판단 근거 ${run.evidenceCount}건 · AI 호출 ${run.judgeCallCount}회`
              : 'AI 판단 근거 없음'}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-[11px] text-muted">{runStateLabel(run.state)}</p>
          <p className="mt-1 text-[11px] text-faint">
            {run.selectedSymbol
              ? (bySymbol.get(run.selectedSymbol)?.nameKo ?? run.selectedSymbol)
              : '주문 없음'}{' '}
            · {formatKstDateTime(run.updatedAt)}
          </p>
          {run.exitReason ? (
            <p className="mt-1 text-[11px] text-faint">
              청산 · {AUTOMATION_EXIT_REASON_LABELS[run.exitReason]}
            </p>
          ) : null}
          <Button
            onClick={() => setOpen((prev) => !prev)}
            className="mt-1.5 text-[11px] font-medium text-navy hover:underline"
          >
            {open ? '판단 근거 접기' : '판단 근거 보기'}
          </Button>
        </div>
      </div>

      {open ? (
        <div className="mt-3 border-l-2 border-line pl-4">
          <AsyncBoundary state={detail.state} onRetry={detail.reload}>
            {(data) =>
              data.candidateScreenings.length === 0 ? (
                <p className="text-[12px] leading-6 text-muted">
                  이 실행에는 남은 후보 심사 기록이 없습니다.
                </p>
              ) : (
                <ul className="space-y-3">
                  {data.candidateScreenings.map((screening) => (
                    <li key={screening.symbol}>
                      <p className="text-[12px] font-semibold text-ink">
                        <InstrumentIdentity
                          symbol={screening.symbol}
                          instrument={bySymbol.get(screening.symbol)}
                          compact
                        />
                        <span className="tnum ml-2 font-mono text-[11px] text-faint">
                          점수 {screening.score.toFixed(2)}
                        </span>
                      </p>
                      <p className="mt-1 text-[12px] leading-6 text-muted">{screening.reason}</p>
                      {screening.evidence.length > 0 ? (
                        <ul className="mt-2 space-y-1.5">
                          {screening.evidence.map((item) => (
                            <li key={item.citationId} className="text-[12px] leading-6">
                              <span className="text-ink">&ldquo;{item.boundedQuote}&rdquo;</span>
                              <span className="ml-1.5 text-faint">
                                — {item.sourceType === 'OFFICIAL_PRIMARY' ? '공식 원문' : '등록 독립'}
                                {item.sourceEventDate ? ` · ${item.sourceEventDate}` : ''}
                              </span>
                              {item.ageWarning ? (
                                <span className="ml-1.5 text-warn">오래된 근거</span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )
            }
          </AsyncBoundary>
        </div>
      ) : null}
    </li>
  );
}

function runStateLabel(state: string): string {
  if (state === 'COMPLETED') return '완료';
  if (state === 'HALTED') return '안전 중단';
  if (state.startsWith('SKIPPED_')) return '주문 없이 종료';
  if (state.includes('FAILED')) return '실패';
  return '진행 중';
}

function modeLabel(mode: AutomationStatusV3['brokerageMode']): string {
  return mode === 'KIS_MOCK' ? 'KIS 모의계좌' : '내부 가상원장';
}
