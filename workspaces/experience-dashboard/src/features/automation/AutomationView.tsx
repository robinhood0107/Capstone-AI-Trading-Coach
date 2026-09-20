'use client';

import { useState } from 'react';
import { Term } from '@/shared/ui/Term';
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
  AutomationCapitalPolicy,
  AutomationCapitalStatus,
  AutomationPolicyV3,
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
  validateAutomationPolicyV3,
} from './policy';
import { AutomationPersistenceNote } from './AutomationPersistenceNote';
import { CandidateFunnel } from './CandidateFunnel';
import { OrderProgress } from './OrderProgress';
import { LIVE_REFRESH_MS } from '@/shared/lib/liveRefresh';

/** 개인 중지와 관리자 전역 중지는 서로 다른 API와 상태를 사용한다. */
function KillSwitchControl({ onChanged }: { active: boolean; onChanged: () => void }) {
  const { user } = useSession();
  // 주문 중지는 다른 기기·다른 사람이 바꿀 수 있다. 1회 로드면 화면이 거짓말한다.
  const personal = useResource(
    async () => ready((await api.killSwitch()).data),
    [],
    true,
    LIVE_REFRESH_MS,
  );
  const global = useResource(
    async () => ready((await api.globalKillSwitch()).data),
    [],
    user?.role === 'ADMIN',
    LIVE_REFRESH_MS,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function change(active: boolean, scope: 'personal' | 'global') {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (scope === 'global') await api.changeGlobalKillSwitch(active);
      else await api.changeKillSwitch(active);
      personal.reload();
      if (user?.role === 'ADMIN') global.reload();
      onChanged();
      window.dispatchEvent(new Event('capstone-automation-changed'));
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '중지 상태를 바꾸지 못했습니다.');
    } finally { setBusy(false); }
  }
  return <div className="mt-5 space-y-4 border-t border-line pt-4">
    <p className="text-[13px] leading-6 text-muted">내 주문 중지는 직접 켜고 해제할 수 있습니다. 해제한 뒤 자동운용 시작은 별도로 선택합니다. 이미 종료된 당일 실행은 다시 시작하지 않으며, 보유 종목을 자동으로 팔거나 기존 체결을 되돌리지 않습니다.</p>
    {/* onRetry 가 없으면 조회가 한 번 실패한 순간 중지 UI 가 에러 카드로 대체되고 사용자가
        할 수 있는 행동이 0개가 된다. 위험 통제 수단에서 그건 허용할 수 없다. */}
    <AsyncBoundary state={personal.state} onRetry={personal.reload}>{(state) => <div className="flex flex-wrap items-center justify-between gap-3">
      <div><p className="text-sm font-semibold">내 주문 중지 · {state.active ? '작동 중' : '꺼짐'}</p>
        <p className="text-xs text-muted">변경 {formatKstDateTime(state.changedAt)}</p>
        {state.globalActive ? <p className="text-sm text-block">관리자가 시스템 전체 주문을 중지했습니다. 개인 중지를 해제해도 주문은 차단됩니다.</p> : null}
      </div>
      <Button disabled={busy} variant={state.active ? 'secondary' : 'danger'} onClick={() => void change(!state.active, 'personal')}>
        {state.active ? '내 주문 중지 해제' : '내 주문 즉시 중지'}
      </Button>
    </div>}</AsyncBoundary>
    {user?.role === 'ADMIN' ? <AsyncBoundary state={global.state} onRetry={global.reload}>{(state) => <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
      <p className="text-sm font-semibold">관리자 전용 · 시스템 전체 중지 {state.active ? '작동 중' : '꺼짐'}</p>
      <Button disabled={busy} variant="danger" onClick={() => void change(!state.active, 'global')}>
        {state.active ? '전역 중지 해제' : '전체 주문 즉시 중지'}
      </Button>
    </div>}</AsyncBoundary> : null}
    {error ? <p role="alert" className="text-sm text-block">{error}</p> : null}
  </div>;
}

interface AutomationData {
  status: AutomationStatusV3;
  runs: AutomationRunV3[];
  positions: AutomationPositionV3[];
  instruments: InstrumentDisplayCatalog;
  capitalPolicy: AutomationCapitalPolicy | null;
  capitalPolicyLoaded: boolean;
  capitalStatus: AutomationCapitalStatus | null;
}

interface Draft {
  capitalLimitKrw: string;
  stopLossPercent: string;
  takeProfitPercent: string;
  /* ── v3 정책이 더 요구하는 값 ── */
  atrPeriod: string;
  atrMultiplier: string;
  maxHoldingSessions: string;
  modelSellEnabled: boolean;
  /* ── 사이징 ── */
  maxOpenPositions: string;
  riskPerTradePercent: string;
}

/**
 * 이 화면은 v3 를 본다.
 *
 * v2 로는 자동운용이 **왜** 그렇게 판단했는지를 보여 줄 수 없다 — ATR 추적손절, 보유 기간,
 * AI 판단 근거가 전부 v3 에만 있다. 현황 화면은 실현손익 요약(`realizedSummary`)이 필요한데
 * v3 포지션 페이지에는 그 필드가 없어서 계속 v2 를 본다.
 */
async function load(): Promise<ViewState<AutomationData>> {
  // 상태만 있으면 정책 편집·시작·정지는 성립한다. 실행 이력이나 종목명 카탈로그가
  // 실패했다고 중지 버튼까지 사라지면 안 된다 - 이 화면은 위험 통제 수단을 담고 있다.
  const status = await api.automationStatusV3();
  const [runs, positions, instruments, capitalPolicy, capitalStatus] = await Promise.all([
    api.automationRunsV3().catch(() => null),
    api.automationPositionsV3().catch(() => null),
    api.instrumentDisplayCatalog().catch(() => null),
    api.automationCapitalPolicy().catch(() => null),
    api.automationCapitalStatus().catch(() => null),
  ]);
  return ready(
    {
      status: status.data,
      runs: runs?.data.items ?? [],
      positions: positions?.data.items ?? [],
      instruments: instruments?.data ?? { items: [] },
      capitalPolicy: capitalPolicy?.data ?? null,
      capitalPolicyLoaded: capitalPolicy !== null,
      capitalStatus: capitalStatus?.data ?? null,
    },
    status.data.policy?.updatedAt ?? null,
  );
}

/**
 * 저장된 정책에서 편집 초안을 만든다.
 *
 * 아직 v3 정책이 없으면(= v2 로만 저장돼 있으면) ATR 값들이 비어 있다. 기본값은 계약이
 * 허용하는 범위 안에서 흔히 쓰는 값으로 채워 두되, 사용자가 저장을 눌러야 실제로 반영된다.
 */
function draftFrom(policy: AutomationPolicyV3 | null): Draft {
  return {
    capitalLimitKrw: policy ? String(policy.capitalLimitKrw) : '',
    stopLossPercent: String(bpsToPercent(policy?.stopLossBps ?? 500)),
    takeProfitPercent: String(bpsToPercent(policy?.takeProfitBps ?? 1000)),
    atrPeriod: String(policy?.atrPeriod ?? 14),
    atrMultiplier: String((policy?.atrMultiplierMilli ?? 2500) / 1000),
    maxHoldingSessions: String(policy?.maxHoldingSessions ?? 60),
    modelSellEnabled: policy?.modelSellEnabled ?? true,
    maxOpenPositions: String(policy?.maxOpenPositions ?? 10),
    riskPerTradePercent: String((policy?.riskPerTradeBps ?? 100) / 100),
  };
}

function numericDraft(draft: Draft) {
  return {
    capitalLimitKrw: draft.capitalLimitKrw.trim() === '' ? 0 : Number(draft.capitalLimitKrw),
    stopLossBps: percentToBps(Number(draft.stopLossPercent)),
    takeProfitBps: percentToBps(Number(draft.takeProfitPercent)),
  };
}

function numericDraftV3(draft: Draft) {
  return {
    atrPeriod: Number(draft.atrPeriod),
    // 0.1배 단위를 정수 milli 로. 부동소수 반올림을 남기지 않는다.
    atrMultiplierMilli: Math.round(Number(draft.atrMultiplier) * 1000),
    maxHoldingSessions: Number(draft.maxHoldingSessions),
    maxOpenPositions: Number(draft.maxOpenPositions),
    // 0.01% 단위를 정수 bps 로. 1% -> 100bps.
    riskPerTradeBps: Math.round(Number(draft.riskPerTradePercent) * 100),
  };
}

export function AutomationView() {
  // 장중에는 체결이 계속 바뀐다. `useResource` 의 폴링은 처음부터 있었는데 아무도
  // 쓰지 않아 이 화면이 1회 로드였다 - 주문이 나가고 체결돼도 새로고침해야 보였다.
  const { state, reload } = useResource(load, [], true, LIVE_REFRESH_MS);
  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(data) => (
        <AutomationBody
          key={`${data.status.controlVersion}:${data.status.policy?.version ?? 0}:${data.capitalPolicy?.version ?? 0}`}
          data={data}
          onReload={reload}
        />
      )}
    </AsyncBoundary>
  );
}

function AutomationBody({ data, onReload }: { data: AutomationData; onReload: () => void }) {
  /*
   * 아직 끝나지 않은 오늘 실행. 이 화면은 15초마다 다시 받지만 패널이 보여 주는 값들이
   * 거의 바뀌지 않아 멈춘 것처럼 보였다 - 지금 벌어지는 일을 맨 위에 올린다.
   */
  const pendingRun =
    data.runs.find(
      (run) => run.state === 'ORDER_SUBMITTED' || run.state === 'PENDING_RECONCILIATION',
    ) ?? null;
  const [draft, setDraft] = useState(() => draftFrom(data.status.policy));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [confirmingDisarm, setConfirmingDisarm] = useState(false);
  const [reinvestRealizedPnl, setReinvestRealizedPnl] = useState(
    data.capitalPolicy?.reinvestRealizedPnl ?? true,
  );
  const values = numericDraft(draft);
  const valuesV3 = numericDraftV3(draft);
  const errors = [...validateAutomationPolicy(values), ...validateAutomationPolicyV3(valuesV3)];
  const selectedPreset = presetFor(values.stopLossBps, values.takeProfitBps);
  const saved = data.status.policy;
  const dirty =
    !saved ||
    saved.capitalLimitKrw !== values.capitalLimitKrw ||
    saved.stopLossBps !== values.stopLossBps ||
    saved.takeProfitBps !== values.takeProfitBps ||
    saved.atrPeriod !== valuesV3.atrPeriod ||
    saved.atrMultiplierMilli !== valuesV3.atrMultiplierMilli ||
    saved.maxHoldingSessions !== valuesV3.maxHoldingSessions ||
    saved.modelSellEnabled !== draft.modelSellEnabled;
  const locked = data.status.controlState !== 'DISARMED';
  const capitalPolicyDirty =
    data.capitalPolicy?.reinvestRealizedPnl !== reinvestRealizedPnl;

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
      await api.putAutomationPolicyV3({
        expectedVersion: saved?.version ?? 0,
        ...values,
        ...valuesV3,
        modelSellEnabled: draft.modelSellEnabled,
      });
      setNotice({ tone: 'ok', text: '자동운용 정책을 새 버전으로 저장했습니다.' });
      onReload();
      window.dispatchEvent(new Event('capstone-automation-changed'));
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

  async function saveCapitalPolicy() {
    if (busy || !data.capitalPolicyLoaded || !capitalPolicyDirty) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.putAutomationCapitalPolicy({
        reinvestRealizedPnl,
        expectedVersion: data.capitalPolicy?.version ?? 0,
      });
      setNotice({ tone: 'ok', text: '재투자 설정을 다음 거래 세션 정책으로 저장했습니다.' });
      onReload();
      window.dispatchEvent(new Event('capstone-automation-changed'));
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setNotice({
        tone: 'error',
        text:
          error.kind === 'error' && error.code === 'CONFLICT'
            ? '재투자 정책이 다른 화면에서 먼저 바뀌었습니다. 최신 값을 다시 확인하세요.'
            : error.kind === 'error'
              ? error.message
              : '재투자 정책을 저장하지 못했습니다.',
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
      await api.armAutomationV3({
        accountId: data.status.accountId,
        policyId: saved.policyId,
        expectedPolicyVersion: saved.version,
        expectedControlVersion: data.status.controlVersion,
      });
      setNotice({ tone: 'ok', text: '자동운용을 시작 대기 상태로 전환했습니다.' });
      onReload();
      window.dispatchEvent(new Event('capstone-automation-changed'));
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setNotice({
        tone: 'error',
        text: error.kind === 'error' ? error.message : '자동운용을 시작하지 못했습니다.',
      });
      onReload();
      window.dispatchEvent(new Event('capstone-automation-changed'));
    } finally {
      setBusy(false);
    }
  }

  /**
   * 자동운용 정지.
   *
   * 이 버튼은 한 번 지워졌다(`5a133675`). 지운 이유는 두 가지였다 - 옛 라벨이 주문 차단을
   * 뜻하는 문구여서 Kill Switch 와 목적이 뒤섞였고, 정지한 뒤 재무장이 `canArm` 게이트에
   * 막히면 시연 중 되돌릴 방법이 없었다. 그래서 라벨을 "자동운용 정지"로 바꿔 주문 차단과
   * 구분하고, 확인 단계에서 **지금 다시 켤 수 있는지**를 함께 보여 준다. 켤 수만 있고 끌 수
   * 없는 쪽이 더 위험하다는 판단이다.
   *
   * `POST /api/v1/automation/disarm` 은 control 행 하나를 보므로 v1/v2/v3 공통이다.
   * `p1_disarm_automation_v1` 이 controlVersion CAS 와 멱등 재생을 보장하고 ARMED 가 아니면
   * 상태를 바꾸지 않으므로 중복 클릭이 위험하지 않다. 보유 종목을 팔거나 기존 체결을
   * 되돌리지 않는다 - 다음 세션 실행이 열리지 않는 것뿐이다.
   */
  async function disarm() {
    if (busy || data.status.controlState !== 'ARMED') return;
    setBusy(true);
    setNotice(null);
    try {
      await api.disarmAutomation(data.status.controlVersion);
      setNotice({
        tone: 'ok',
        text: '자동운용을 정지했습니다. 예약된 다음 실행은 열리지 않습니다. 보유 종목과 기존 체결은 그대로입니다.',
      });
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setNotice({
        tone: 'error',
        text:
          error.kind === 'error' && error.code === 'CONFLICT'
            ? '다른 화면에서 상태가 먼저 바뀌었습니다. 최신 상태를 확인한 뒤 다시 정지하세요.'
            : error.kind === 'error'
              ? error.message
              : '자동운용을 정지하지 못했습니다.',
      });
    } finally {
      setBusy(false);
      setConfirmingDisarm(false);
      onReload();
      window.dispatchEvent(new Event('capstone-automation-changed'));
    }
  }

  return (
    <div className="space-y-6">
      {/* 이 화면이 스스로 갱신한다는 사실을 알린다. 모르면 사용자는 새로고침을 누른다. */}
      <p role="status" className="h-5 overflow-hidden text-[11px] text-muted">
        15초마다 자동 갱신 · 창으로 돌아오면 다시 확인합니다.
      </p>
      <Panel
        contract="GET /api/v3/automation/status"
        title="현재 자동운용 상태"
        hint="선택한 계좌의 운용 예약, 주문 중지, 보유 종목과 적용 정책을 확인합니다."
        actions={<StatusLabel status={data.status} />}
      >
        {/*
         * 이 패널의 나머지 값(계좌 모드·포지션 수·킬스위치·정책 버전)은 거의 바뀌지
         * 않는다. 지금 벌어지는 일을 맨 위에 올리지 않으면, 15초마다 새로 받아도
         * 화면이 멈춰 있는 것으로 보인다.
         */}
        {pendingRun ? (
          <div className="mb-4 border-l-2 border-hold pl-4">
            <p className="text-[12px] font-medium text-ink">진행 중인 주문</p>
            <p className="mt-0.5 text-[13px] text-muted">
              {pendingRun.selectedSymbol
                ? (instrumentMap(data.instruments.items).get(pendingRun.selectedSymbol)?.nameKo ??
                  pendingRun.selectedSymbol)
                : '종목 미상'}
            </p>
            <OrderProgress run={pendingRun} />
          </div>
        ) : null}
        <dl className="grid gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          <StatusField label="계좌 모드" value={modeLabel(data.status.brokerageMode)} />
          {/* 상한은 사용자가 원칙에서 고르는 값이다. 화면이 5로 단정하면 실제 상한이
              10이어도 5로 보이고, 그러면 "왜 더 안 사는가"를 잘못 설명하게 된다. */}
          <StatusField
            label="열린 포지션"
            value={`${data.status.openPositionCount} / ${saved?.maxOpenPositions ?? '—'}`}
            mono
          />
          <StatusField
            label="Kill Switch"
            value={data.status.killSwitchActive ? '작동 중' : '꺼짐'}
          />
          <StatusField label="저장된 정책" value={saved ? `v${saved.version}` : '미설정'} mono />
          <StatusField label="적용 중인 정책" value={data.status.appliedPolicyVersion ? `v${data.status.appliedPolicyVersion}` : '미설정'} mono />
          <StatusField
            label="LLM 후보 검토"
            value={data.status.aiJudgementEnabled ? `켜짐 · ${data.status.thinkingLevel}` : '꺼짐'}
          />
          <StatusField
            label="시세 이력"
            value={MARKET_HISTORY_LABELS[data.status.marketHistoryStatus]}
          />
          <StatusField
            label="청산 정책 미지정 포지션"
            value={`${data.status.legacyOpenPositionCount}건`}
            mono
          />
        </dl>

        <div className="mt-4 flex justify-end">
          <Link href="/order-review" className="text-[13px] font-semibold text-navy hover:underline">
            최근 주문 판정 보기 →
          </Link>
        </div>

        {data.status.policyRecoverySourceVersion ? <p className="mt-3 text-xs text-warn">청산 기준은 이전 저장 정책 v{data.status.policyRecoverySourceVersion}의 값으로 복원했습니다. 이전 이력은 보존되어 있습니다.</p> : null}
        <p className="mt-3 text-xs leading-6 text-muted">다음 자동평가: {data.status.nextRunAt ? formatKstDateTime(data.status.nextRunAt) : '예약 없음'}{data.status.policy ? ` · 평가 ${data.status.policy.evaluationTimeKst} · 당일 운용 마감 ${data.status.policy.cancelTimeKst} (한국 시간)` : ''}</p>
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
              className={`!flex !flex-col !items-stretch !justify-start !rounded-none bg-panel px-4 py-4 text-left disabled:text-faint ${
                selectedPreset === preset.presetId ? 'ring-2 ring-inset ring-navy' : ''
              }`}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="shrink-0 text-[14px] font-semibold text-ink">{preset.label}</span>
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

        <div className="mt-6 border-t border-line pt-5">
          <p className="text-eyebrow font-semibold uppercase text-faint">청산 기준</p>
          <p className="mt-1.5 text-[12px] leading-5 text-muted">
            <Term name="stopLoss">손절</Term>·<Term name="takeProfit">익절</Term> 외에 두 가지가
            더 청산을 만든다 — 최고가에서 <Term name="atr" /> 배수만큼 밀리면{' '}
            <Term name="trailingStop">추적손절</Term>,
            보유 기간을 넘기면 기간 초과. 모델 매도 신호를 따를지도 여기서 정한다.
          </p>
          <div className="mt-4 grid gap-5 md:grid-cols-3">
            <PolicyInput
              label="ATR 기간"
              value={draft.atrPeriod}
              min={5}
              max={100}
              step={1}
              suffix="세션"
              disabled={locked}
              onChange={(value) => setDraft((current) => ({ ...current, atrPeriod: value }))}
            />
            <PolicyInput
              label="ATR 배수"
              value={draft.atrMultiplier}
              min={1}
              max={10}
              step={0.1}
              suffix="배"
              disabled={locked}
              onChange={(value) => setDraft((current) => ({ ...current, atrMultiplier: value }))}
            />
            <PolicyInput
              label="최대 보유 기간"
              value={draft.maxHoldingSessions}
              min={0}
              max={1260}
              step={1}
              suffix="세션"
              disabled={locked}
              onChange={(value) =>
                setDraft((current) => ({ ...current, maxHoldingSessions: value }))
              }
            />
          </div>
          <label className="mt-4 flex items-center gap-2 text-[13px] text-ink">
            <input
              type="checkbox"
              className="ink-checkbox"
              checked={draft.modelSellEnabled}
              disabled={locked}
              onChange={(event) =>
                setDraft((current) => ({ ...current, modelSellEnabled: event.target.checked }))
              }
            />
            모델이 매도 신호를 내면 따른다
          </label>
        </div>

        <div className="mt-6 border-t border-line pt-5">
          <p className="text-eyebrow font-semibold uppercase text-faint">동시 보유와 주문 크기</p>
          <p className="mt-2 text-[13px] leading-6 text-muted">
            동시에 몇 종목까지 들고 갈지, 한 거래에 자본의 몇 퍼센트까지 잃을 수 있는지를
            정합니다. 수량은 변동성(ATR)에 맞춰 계산합니다 — 많이 흔들리는 종목은 적게, 덜
            흔들리는 종목은 많이 사서 종목별 위험을 비슷하게 맞춥니다.
          </p>
          <div className="mt-4 grid gap-5 md:grid-cols-2">
            <PolicyInput
              label="동시 보유 상한"
              value={draft.maxOpenPositions}
              min={1}
              max={20}
              step={1}
              suffix="종목"
              disabled={locked}
              onChange={(value) =>
                setDraft((current) => ({ ...current, maxOpenPositions: value }))
              }
            />
            <PolicyInput
              label="거래당 위험"
              value={draft.riskPerTradePercent}
              min={0.1}
              max={3}
              step={0.1}
              suffix="%"
              disabled={locked}
              onChange={(value) =>
                setDraft((current) => ({ ...current, riskPerTradePercent: value }))
              }
            />
          </div>
          {/* 설정이 거래를 막을 수 있다는 사실을 화면이 먼저 말한다. 상한을 크게 잡으면
              종목당 금액이 줄어 비싼 종목은 1주도 못 사게 된다. */}
          <p className="mt-3 text-[12px] leading-6 text-muted">
            현재 설정이면 종목당 최대{" "}
            <span className="tnum font-mono text-ink">
              {formatKrw(
                Math.floor(
                  (Number(draft.capitalLimitKrw) || 0) /
                    Math.max(1, Number(draft.maxOpenPositions) || 1),
                ),
              )}
            </span>{" "}
            까지 넣습니다. 이 금액보다 비싼 종목은 살 수 없고, 거래당 위험을 낮추면 수량이
            줄어 0주가 되는 날이 생길 수 있습니다. 그런 날에는 실행 상세의 단계별 사유에
            이유가 남습니다.
          </p>
          <p className="mt-2 text-[12px] leading-6 text-faint">
            이 시스템은 정규장(09:00~15:30)만 운용합니다. 2026-09-14 부터 열린 KRX
            애프터마켓(16:00~20:00)은 다루지 않습니다 — 연결된 계좌의 시간외 주문 지원이
            확인되지 않았습니다.
          </p>
        </div>

        <div className="mt-6 border-t border-line pt-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-2xl">
              <p className="text-eyebrow font-semibold uppercase text-faint">자본 재투자와 목표비중</p>
              <label className="mt-3 flex items-center gap-2 text-[13px] text-ink">
                <input
                  type="checkbox"
                  className="ink-checkbox"
                  checked={reinvestRealizedPnl}
                  disabled={!data.capitalPolicyLoaded || busy}
                  onChange={(event) => setReinvestRealizedPnl(event.target.checked)}
                />
                전환 이후 확정 순손익을 다음 세션 운용자금에 반영한다
              </label>
              <p className="mt-2 text-[12px] leading-5 text-muted">
                기본 ON · 현금 여유 1% · 목표비중 편차 2%p · 최소 조정 1만원/1주 · 하루 최대 3건입니다.
                수익성 최적값이 아니며, 수동 보유와 다른 계좌는 자동 편입하지 않습니다.
              </p>
              <p className="mt-1 text-[12px] leading-5 text-muted">
                {data.capitalPolicy
                  ? `v${data.capitalPolicy.version} · ${data.capitalPolicy.effectiveFromSession} 세션부터 적용`
                  : data.capitalPolicyLoaded
                    ? '아직 저장된 자본정책이 없습니다. 기본 ON을 저장하면 다음 XKRX 세션부터 적용됩니다.'
                    : '자본정책 조회에 실패해 현재 적용값을 확인할 수 없습니다.'}
              </p>
            </div>
            <Button
              disabled={busy || !data.capitalPolicyLoaded || !capitalPolicyDirty}
              variant="secondary"
              onClick={() => void saveCapitalPolicy()}
            >
              재투자 설정 저장
            </Button>
          </div>
          {data.capitalStatus ? (
            <>
              <dl className="mt-5 grid gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
                <StatusField label="설정 자금" value={formatKrw(data.capitalStatus.configuredCapitalKrw)} mono />
                <StatusField label="전환 후 확정 순손익" value={formatKrw(data.capitalStatus.realizedPnlSinceTransitionKrw)} mono />
                <StatusField label="봇 보유 평가액" value={formatKrw(data.capitalStatus.botPositionMarketValueKrw)} mono />
                <StatusField label="주문 예약금" value={formatKrw(data.capitalStatus.reservedBuyCashKrw)} mono />
                <StatusField label="실제 매수가능 현금" value={formatKrw(data.capitalStatus.brokerBuyableCashKrw)} mono />
                <StatusField label="이번 정책 가용 현금" value={formatKrw(data.capitalStatus.availableBuyCashKrw)} mono />
                <StatusField label="종목당 목표액" value={formatKrw(data.capitalStatus.targetPerPositionKrw)} mono />
                <StatusField label="기존 봇 포지션 편입" value={`${data.capitalStatus.existingBotPositionsAdopted}건`} mono />
              </dl>
              {data.capitalStatus.positions.length > 0 ? (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[620px] text-[12px]">
                    <thead className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
                      <tr>
                        <th className="pb-2 font-normal">종목</th>
                        <th className="pb-2 text-right font-normal">현재/목표 수량</th>
                        <th className="pb-2 text-right font-normal">현재/목표 비중</th>
                        <th className="pb-2 text-right font-normal">현재/목표 평가액</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {data.capitalStatus.positions.map((position) => (
                        <tr key={position.symbol}>
                          <td className="py-3 font-mono">{position.symbol}</td>
                          <td className="tnum py-3 text-right font-mono">
                            {position.currentQuantity} / {position.targetQuantity ?? '미확인'}
                          </td>
                          <td className="tnum py-3 text-right font-mono">
                            {position.currentWeightBps == null ? '미확인' : `${(position.currentWeightBps / 100).toFixed(2)}%`} / {(position.targetWeightBps / 100).toFixed(2)}%
                          </td>
                          <td className="tnum py-3 text-right font-mono">
                            {position.currentMarketValueKrw == null ? '미확인' : formatKrw(position.currentMarketValueKrw)} / {formatKrw(position.targetMarketValueKrw)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="mt-4 text-[12px] text-muted">편입된 자동운용 포지션이 없습니다.</p>
              )}
              {data.capitalStatus.valuationMissingCount > 0 ? (
                <p className="mt-3 text-[12px] text-block">평가액을 확인하지 못한 포지션 {data.capitalStatus.valuationMissingCount}건은 비중 조정에서 제외됩니다.</p>
              ) : null}
            </>
          ) : null}
        </div>

        <div className="mt-5 grid gap-4 border-t border-line pt-5 md:grid-cols-2">
          <div>
            <p className="text-eyebrow font-semibold uppercase text-faint">종목당 기본 슬롯</p>
            <p className="tnum mt-1 font-mono text-[16px] text-ink">
              {values.capitalLimitKrw > 0 ? formatKrw(slotBudgetKrw(values.capitalLimitKrw, valuesV3.maxOpenPositions)) : '—'}
            </p>
            <p className="mt-1 text-[12px] leading-5 text-muted">
              동시 보유 상한 {values.capitalLimitKrw > 0 ? valuesV3.maxOpenPositions : '—'}종목으로 나눈 기준입니다. 실제 수량은 원칙·잔고·매수가능수량 중 가장 작은
              한도로 계산합니다.
            </p>
          </div>
          <div className="text-[12px] leading-5 text-muted">
            <p>최대 자동운용 금액은 주문 한도이며 수익·원금 보장 금액이 아닙니다.</p>
            <p className="mt-1">
              손절·익절은 서버에 예약된 거래 세션의 평가 뒤 지정가 청산을 시도하며 즉시 체결을 보장하지
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
            ) : data.status.controlState !== 'ARMED' ? null : confirmingDisarm ? (
              <>
                <span className="text-[12px] leading-5 text-block">
                  {data.status.canArm && data.status.blockers.length === 0
                    ? '정지 뒤 다시 켤 수 있는 상태입니다.'
                    : `지금 정지하면 다시 켤 수 없습니다 · ${data.status.blockers
                        .map((item) => AUTOMATION_BLOCKER_LABELS_V3[item])
                        .join(' · ') || '시작 조건 미충족'}`}
                </span>
                <Button
                  disabled={busy}
                  onClick={() => void disarm()}
                  variant="danger"
                >
                  {busy ? '처리 중' : '정지 확인'}
                </Button>
                <Button disabled={busy} variant="secondary" onClick={() => setConfirmingDisarm(false)}>
                  취소
                </Button>
              </>
            ) : (
              <Button disabled={busy} variant="secondary" onClick={() => setConfirmingDisarm(true)}>
                자동운용 정지
              </Button>
            )}
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
      <span className={`mt-2 flex items-center overflow-hidden rounded-control border border-line focus-within:border-navy ${disabled ? 'bg-subtle' : 'bg-panel'}`}>
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="tnum min-w-0 flex-1 border-0 bg-transparent px-3 py-2 text-right font-mono text-[14px] text-ink focus:outline-none disabled:cursor-not-allowed disabled:bg-transparent disabled:text-muted"
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
                <th className="pb-2 text-right font-normal">
                  <Term name="trailingStop">ATR 추적손절</Term>
                </th>
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
          {/* 주문의 수량·지정가·체결 진행. 서버가 주고 있었는데 화면이 안 그렸다. */}
          <OrderProgress run={run} />
          <p className="mt-1 text-[11px] text-faint">
            {/* 근거가 0건이면 AI 판단 없이 넘어간 실행이다. 그 사실을 감추지 않되,
                결과만 말하고 끝내지 않는다. 사용자가 알아야 하는 것은 원인이다.
                퍼널을 펼쳐야 사유를 알 수 있으므로 여기서는 있다는 사실만 알린다. */}
            {run.evidenceCount > 0
              ? `판단 근거 ${run.evidenceCount}건 · AI 호출 ${run.judgeCallCount}회`
              : 'AI 심사 전에 후보가 정리됐습니다 · 아래에서 단계별 사유를 볼 수 있습니다'}
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
              (data.stageOutcomes?.length ?? 0) > 0 ? (
                <div className="space-y-3">
                  <CandidateFunnel
                    outcomes={data.stageOutcomes ?? []}
                    nameOf={(symbol) => bySymbol.get(symbol)?.nameKo ?? symbol}
                  />
                  {data.candidateScreenings.length > 0 ? (
                    <p className="text-[11px] text-faint">
                      AI 심사에 도달한 후보 {data.candidateScreenings.length}종목
                    </p>
                  ) : null}
                </div>
              ) : data.candidateScreenings.length === 0 ? (
                <p className="text-[12px] leading-6 text-muted">
                  이 실행에는 단계별 기록이 남아 있지 않습니다. 기록이 시작되기 전의 실행입니다.
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
                          {screening.status === 'ABSTAIN'
                            ? '검토 미완료 · 기본 후보 경로 사용'
                            : screening.verdict === 'VETO_BUY'
                              ? '근거 확인 · 매수 후보 제외'
                              : `거부 근거 없음 · 점수 ${screening.score.toFixed(2)}`}
                        </span>
                      </p>
                      <p className="mt-1 text-[12px] leading-6 text-muted">
                        {screening.reason === 'SCREENING_ERROR'
                          ? '뉴스·AI 검토에 실패했습니다. 정상 0건으로 확인된 결과가 아닙니다.'
                          : screening.reason === 'AI_DISABLED_OR_UNAVAILABLE'
                            ? 'AI 검토가 꺼져 있거나 사용 가능한 provider가 없습니다.'
                            : screening.reason}
                      </p>
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
