'use client';

import { useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { toErrorState, useResource } from '@/shared/lib/useResource';
import { formatKrw, formatKstDateTime } from '@/shared/lib/format';
import { ready, type ViewState } from '@/shared/lib/viewState';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import type {
  AutomationPolicyV2,
  AutomationPositionV2,
  AutomationRunV2,
  AutomationStatusV2,
} from '@/shared/api/wire';
import {
  AUTOMATION_BLOCKER_LABELS,
  AUTOMATION_EVIDENCE_LINKS,
  AUTOMATION_PRESETS,
  AUTOMATION_STATE_LABELS,
  bpsToPercent,
  percentToBps,
  presetFor,
  slotBudgetKrw,
  validateAutomationPolicy,
} from './policy';

interface AutomationData {
  status: AutomationStatusV2;
  runs: AutomationRunV2[];
  positions: AutomationPositionV2[];
}

interface Draft {
  capitalLimitKrw: string;
  stopLossPercent: string;
  takeProfitPercent: string;
}

async function load(): Promise<ViewState<AutomationData>> {
  const [status, runs, positions] = await Promise.all([
    api.automationStatusV2(),
    api.automationRunsV2(),
    api.automationPositionsV2(),
  ]);
  return ready(
    { status: status.data, runs: runs.data.items, positions: positions.data.items },
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

  async function disarm() {
    if (busy || data.status.controlState === 'DISARMED') return;
    setBusy(true);
    setNotice(null);
    try {
      await api.disarmAutomation(data.status.controlVersion);
      setNotice({ tone: 'ok', text: '신규 주문을 중지했습니다. 미확정 주문 대사는 유지됩니다.' });
      onReload();
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setNotice({
        tone: 'error',
        text: error.kind === 'error' ? error.message : '자동운용을 중지하지 못했습니다.',
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        contract="GET /api/v2/automation/status"
        title="현재 자동운용 상태"
        hint="Kill Switch와 자동운용 상태는 서로 다른 값입니다. 서버가 내려준 상태를 그대로 표시합니다."
        actions={<StatusLabel status={data.status} />}
      >
        <dl className="grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-4">
          <StatusField label="계좌 모드" value={modeLabel(data.status.brokerageMode)} />
          <StatusField label="열린 포지션" value={`${data.status.openPositionCount} / 5`} mono />
          <StatusField
            label="Kill Switch"
            value={data.status.killSwitchActive ? '작동 중' : '꺼짐'}
          />
          <StatusField label="정책 버전" value={saved ? `v${saved.version}` : '미설정'} mono />
        </dl>

        {data.status.blockers.length > 0 ? (
          <div className="mt-5 border-l-2 border-hold bg-hold/5 px-4 py-3">
            <p className="text-[13px] font-semibold text-ink">현재 시작할 수 없습니다</p>
            <ul className="mt-2 space-y-2">
              {data.status.blockers.map((blocker) => (
                <li key={blocker} className="text-[13px] leading-5 text-muted">
                  <span className="font-mono text-[11px] text-hold">{blocker}</span>
                  <span className="ml-2">{AUTOMATION_BLOCKER_LABELS[blocker]}</span>
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
            {selectedPreset === 'custom' ? '직접 입력' : `${selectedPreset} preset`}
          </span>
        }
      >
        <div className="grid gap-px bg-line md:grid-cols-3">
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
            <p className="font-mono text-eyebrow uppercase text-faint">종목당 기본 슬롯</p>
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
              className="border border-line px-3 py-1.5 text-[13px] text-ink disabled:bg-line disabled:text-faint"
            >
              {busy ? '처리 중' : '정책 저장'}
            </Button>
            {data.status.controlState === 'DISARMED' ? (
              <Button
                                disabled={
                  busy || dirty || !saved || !data.status.canArm || data.status.blockers.length > 0
                }
                onClick={() => void arm()}
                title={data.status.blockers.map((item) => AUTOMATION_BLOCKER_LABELS[item]).join(' ')}
                variant="primary"
              >
                자동운용 시작
              </Button>
            ) : (
              <Button
                                disabled={busy}
                onClick={() => void disarm()}
                className="border border-block bg-block px-4 py-1.5 text-[13px] font-medium text-white disabled:border-line disabled:bg-line disabled:text-faint"
              >
                신규 주문 중지
              </Button>
            )}
          </div>
        </div>
      </Panel>

      <div className="grid gap-6 xl:grid-cols-2">
        <PositionPanel positions={data.positions} />
        <RunPanel runs={data.runs} />
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

function StatusLabel({ status }: { status: AutomationStatusV2 }) {
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
      <dt className="font-mono text-eyebrow uppercase text-faint">{label}</dt>
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
      <span className="mt-2 flex items-center border border-line bg-panel focus-within:border-navy">
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

function PositionPanel({ positions }: { positions: AutomationPositionV2[] }) {
  return (
    <Panel contract="GET /api/v2/automation/positions" title="자동운용 포지션">
      {positions.length === 0 ? (
        <p className="border border-dashed border-rule px-4 py-6 text-[13px] text-muted">
          자동운용이 보유한 포지션이 없습니다.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead className="border-b border-line text-left font-mono text-eyebrow uppercase text-faint">
              <tr>
                <th className="pb-2 font-normal">종목</th>
                <th className="pb-2 text-right font-normal">수량</th>
                <th className="pb-2 text-right font-normal">평균체결가</th>
                <th className="pb-2 text-right font-normal">상태</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <tr key={position.positionId} className="border-b border-line/60 last:border-0">
                  <td className="py-2.5 font-mono text-ink">{position.symbol}</td>
                  <td className="tnum py-2.5 text-right font-mono">{position.quantity}</td>
                  <td className="tnum py-2.5 text-right font-mono">
                    {formatKrw(position.entryAverageFillPriceKrw)}
                  </td>
                  <td className="py-2.5 text-right text-muted">{position.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function RunPanel({ runs }: { runs: AutomationRunV2[] }) {
  return (
    <Panel contract="GET /api/v2/automation/runs" title="최근 자동운용 실행">
      {runs.length === 0 ? (
        <p className="border border-dashed border-rule px-4 py-6 text-[13px] text-muted">
          기록된 자동운용 실행이 없습니다.
        </p>
      ) : (
        <ul className="divide-y divide-line/60">
          {runs.map((run) => (
            <li key={run.runId} className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <p className="font-mono text-[12px] text-ink">{run.sessionDate}</p>
                <p className="mt-1 truncate font-mono text-[10px] text-faint">{run.runId}</p>
              </div>
              <div className="text-right">
                <p className="font-mono text-[11px] text-muted">{run.state}</p>
                <p className="mt-1 text-[11px] text-faint">
                  {run.selectedSymbol ?? '주문 없음'} · {formatKstDateTime(run.updatedAt)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function modeLabel(mode: AutomationStatusV2['brokerageMode']): string {
  return mode === 'KIS_MOCK' ? 'KIS 모의계좌' : '내부 가상원장';
}
