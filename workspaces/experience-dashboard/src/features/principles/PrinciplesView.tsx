'use client';

import { useState } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DecisionBadge } from '@/shared/ui/Decision';
import { useResource, toErrorState } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { ready, type ViewState } from '@/shared/lib/viewState';
import { RULE_LABELS } from '@/shared/lib/ruleLabels';
import { formatCount, formatKrw, formatKstDateTime, formatRatio } from '@/shared/lib/format';
import { matchesPreset } from './preset';
import type {
  PrincipleCurrent,
  PrinciplePreset,
  PrinciplePresetListData,
  PrincipleRule,
  PrincipleSummary,
} from '@/shared/api/wire';

interface PrinciplesData {
  presets: PrinciplePresetListData;
  summaries: PrincipleSummary[];
  current: PrincipleCurrent | null;
}

async function load(): Promise<ViewState<PrinciplesData>> {
  const [presets, list] = await Promise.all([api.principlePresets(), api.principles()]);
  const active = list.data.items.find((item) => item.status === 'ACTIVE') ?? list.data.items[0];
  const current = active ? (await api.principle(active.principleId)).data : null;
  return ready({ presets: presets.data, summaries: list.data.items, current }, current?.updatedAt ?? null);
}

export function PrinciplesView() {
  const { state, reload } = useResource(load, []);
  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(data) => <PrinciplesBody data={data} onSaved={reload} />}
    </AsyncBoundary>
  );
}

function PrinciplesBody({ data, onSaved }: { data: PrinciplesData; onSaved: () => void }) {
  const [draft, setDraft] = useState<PrincipleRule[]>(data.current?.rules ?? []);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  const dirty = JSON.stringify(draft) !== JSON.stringify(data.current?.rules ?? []);

  function updateThreshold(ruleId: string, threshold: number) {
    setDraft((rules) => rules.map((rule) => (rule.ruleId === ruleId ? { ...rule, threshold } : rule)));
  }

  function toggleRule(ruleId: string, enabled: boolean) {
    setDraft((rules) =>
      rules.map((rule) =>
        rule.ruleId === ruleId
          ? {
              ...rule,
              enabled,
              // enabled=false이면 severity는 반드시 ALLOW다.
              severity: enabled ? (rule.severity === 'ALLOW' ? 'WARN' : rule.severity) : 'ALLOW',
            }
          : rule,
      ),
    );
  }

  async function save() {
    if (!data.current || saving) return;
    setSaving(true);
    setNotice(null);
    try {
      await api.updatePrinciple(data.current.principleId, {
        expectedVersion: data.current.version,
        mode: data.current.mode,
        status: data.current.status,
        title: data.current.title,
        rules: draft,
      });
      setNotice({ tone: 'ok', text: '새 버전으로 저장했습니다.' });
      onSaved();
    } catch (cause) {
      const errorState = toErrorState<never>(cause);
      const conflict = errorState.kind === 'error' && errorState.code === 'CONFLICT';
      setNotice({
        tone: 'error',
        text: conflict
          ? '다른 곳에서 원칙이 먼저 바뀌었습니다. 최신 내용을 다시 불러온 뒤 저장할지 직접 정하세요. 자동으로 다시 보내지 않습니다.'
          : errorState.kind === 'error'
            ? errorState.message
            : '저장하지 못했습니다.',
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        contract="GET /api/v1/principle-presets"
        title="어떤 방식으로 시작할까요"
        hint={data.presets.disclaimer.ko}
      >
        <div className="grid gap-px overflow-hidden rounded-tile border border-line bg-line md:grid-cols-3">
          {[...data.presets.items]
            .sort((a, b) => a.order - b.order)
            .map((preset) => (
              <PresetCard
                key={preset.presetId}
                preset={preset}
                selected={matchesPreset(draft, preset.defaultRules)}
                onApply={() => setDraft(preset.defaultRules)}
              />
            ))}
        </div>
      </Panel>

      {data.current ? (
        <Panel
          contract="PUT /api/v1/principles/{principleId}"
          title="내 원칙 값 조정"
          hint="자연어가 아니라 구조화된 값으로만 저장합니다. 저장된 값은 주문 판단과 백테스트에 같이 적용됩니다."
          actions={
            <span className="font-mono text-[12px] text-faint">
              v{data.current.version} · {formatKstDateTime(data.current.updatedAt)}
            </span>
          }
        >
          <ul className="divide-y divide-line">
            {draft.map((rule) => (
              <RuleRow
                key={rule.ruleId}
                rule={rule}
                onThreshold={(value) => updateThreshold(rule.ruleId, value)}
                onToggle={(enabled) => toggleRule(rule.ruleId, enabled)}
              />
            ))}
          </ul>

          {notice ? (
            <p
              className={`mt-5 border-l-2 px-3 py-2 text-[13px] leading-6 ${
                notice.tone === 'ok' ? 'border-allow bg-allow/5 text-ink' : 'border-block bg-block/5 text-ink'
              }`}
            >
              {notice.text}
            </p>
          ) : null}

          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
            <p className="max-w-2xl text-[12px] leading-5 text-muted">
              저장하면 새 버전이 만들어지고 이전 버전은 그대로 남습니다. 저장이 실패하면 자동으로 다시
              보내지 않습니다.
            </p>
            <div className="flex items-center gap-2">
              <Button
                                disabled={!dirty || saving}
                onClick={() => setDraft(data.current?.rules ?? [])}
                className="rounded-full border border-line px-3 py-1.5 text-[13px] text-muted disabled:text-faint"
              >
                되돌리기
              </Button>
              <Button
                                disabled={!dirty || saving}
                onClick={() => void save()}
                variant="primary"
                title={`expectedVersion=${data.current.version}으로 저장합니다.`}
              >
                {saving ? '저장 중' : '변경 사항 저장'}
              </Button>
            </div>
          </div>
        </Panel>
      ) : (
        <Panel contract="GET /api/v1/principles" title="내 원칙 값 조정">
          <div className="rounded-tile border border-dashed border-rule px-4 py-6">
            <p className="text-eyebrow font-semibold uppercase text-faint">데이터 없음</p>
            <p className="mt-2 text-sm font-medium text-ink">아직 만든 원칙이 없습니다</p>
            <p className="mt-1 text-[13px] leading-5 text-muted">
              위 preset 중 하나를 골라 원칙을 먼저 만들어야 주문 검토를 쓸 수 있습니다.
            </p>
          </div>
        </Panel>
      )}
    </div>
  );
}

function PresetCard({
  preset,
  selected,
  onApply,
}: {
  preset: PrinciplePreset;
  selected: boolean;
  onApply: () => void;
}) {
  const loss = preset.defaultRules.find((rule) => rule.ruleId === 'daily_loss_guard');
  const orders = preset.defaultRules.find((rule) => rule.ruleId === 'max_daily_orders');
  return (
    <Button
            onClick={onApply}
      aria-pressed={selected}
      className={`bg-panel px-4 py-4 text-left ${selected ? 'ring-2 ring-inset ring-navy' : ''}`}
    >
      <div className="flex items-baseline justify-between">
        <p className="text-[15px] font-semibold text-ink">{preset.nameKo}</p>
        <span className="text-eyebrow font-semibold uppercase text-faint">{preset.presetId}</span>
      </div>
      <p className="mt-2 text-[13px] leading-6 text-muted">{preset.descriptionKo}</p>
      <dl className="mt-3 space-y-1 text-[12px]">
        <div className="flex justify-between">
          <dt className="text-faint">하루 손실 한도</dt>
          <dd className="tnum font-mono text-ink">{loss ? formatRatio(loss.threshold, 0) : '—'}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-faint">하루 주문 상한</dt>
          <dd className="tnum font-mono text-ink">
            {orders ? `${formatCount(orders.threshold)}건` : '—'}
          </dd>
        </div>
      </dl>
    </Button>
  );
}

function RuleRow({
  rule,
  onThreshold,
  onToggle,
}: {
  rule: PrincipleRule;
  onThreshold: (value: number) => void;
  onToggle: (enabled: boolean) => void;
}) {
  const meta = RULE_LABELS[rule.ruleId];
  if (!meta) return null;

  const isRatio = meta.unit === 'RATIO';
  const negative = rule.operator === '>=';

  const display = isRatio
    ? formatRatio(rule.threshold, 0)
    : meta.unit === 'KRW'
      ? formatKrw(rule.threshold)
      : `${formatCount(rule.threshold)}건`;

  return (
    <li className="flex flex-wrap items-center gap-x-6 gap-y-3 py-4">
      <div className="min-w-[240px] flex-1">
        <div className="flex items-center gap-2">
          <p className="text-[14px] font-medium text-ink">{meta.name}</p>
          {rule.enabled ? (
            <DecisionBadge status={rule.severity === 'BLOCK' ? 'BLOCK' : 'WARN'} size="sm" />
          ) : (
            <span className="rounded-full border border-line px-2 py-0.5 font-mono text-[11px] text-faint">
              사용 안 함
            </span>
          )}
        </div>
        <p className="mt-1 text-[12px] leading-5 text-muted">{meta.reading}</p>
        <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.06em] text-faint">
          {rule.ruleId} · 근거 {rule.evidenceRequirement === 'REQUIRED' ? '필수' : '선택'}
        </p>
      </div>

      <div className="flex items-center gap-4">
        {isRatio ? (
          <input
            type="range"
            min={negative ? -1 : 0}
            max={negative ? 0 : 1}
            step={0.0001}
            value={rule.threshold}
            disabled={!rule.enabled}
            onChange={(event) => onThreshold(Number(event.target.value))}
            aria-label={`${meta.name} 값`}
            className="w-40 accent-navy disabled:opacity-40"
          />
        ) : (
          <input
            type="number"
            min={0}
            step={meta.unit === 'KRW' ? 10000 : 1}
            value={rule.threshold}
            disabled={!rule.enabled}
            onChange={(event) => onThreshold(Number(event.target.value))}
            aria-label={`${meta.name} 값`}
            className="tnum w-32 rounded-full border border-line px-3 py-1 text-right font-mono text-[13px] disabled:bg-surface disabled:text-faint"
          />
        )}
        <span className="tnum w-24 text-right font-mono text-[13px] text-ink">{display}</span>
        <label className="flex items-center gap-2 text-[12px] text-muted">
          <input
            type="checkbox"
            checked={rule.enabled}
            onChange={(event) => onToggle(event.target.checked)}
            className="accent-navy"
          />
          사용
        </label>
      </div>
    </li>
  );
}
