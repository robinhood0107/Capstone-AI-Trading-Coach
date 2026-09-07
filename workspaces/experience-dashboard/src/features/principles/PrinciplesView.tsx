'use client';

import { useState, type CSSProperties } from 'react';
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
  PrincipleHistoryData,
  PrinciplePreset,
  PrinciplePresetListData,
  PrincipleRule,
  PrincipleSummary,
  PrincipleVersion,
} from '@/shared/api/wire';

interface PrinciplesData {
  presets: PrinciplePresetListData;
  summaries: PrincipleSummary[];
  current: PrincipleCurrent | null;
  history: PrincipleHistoryData | null;
}

async function load(): Promise<ViewState<PrinciplesData>> {
  const [presets, list] = await Promise.all([api.principlePresets(), api.principles()]);
  const active = list.data.items.find((item) => item.status === 'ACTIVE') ?? list.data.items[0];
  const current = active ? (await api.principle(active.principleId)).data : null;
  // 이력은 곁다리다. 못 받아 와도 원칙 화면 자체는 떠야 하므로 실패를 여기서 흡수한다.
  const history = active
    ? await api
        .principleVersions(active.principleId)
        .then((result) => result.data)
        .catch(() => null)
    : null;
  return ready(
    { presets: presets.data, summaries: list.data.items, current, history },
    current?.updatedAt ?? null,
  );
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
  const savedPreset = data.current
    ? data.presets.items.find((preset) => matchesPreset(data.current?.rules ?? [], preset.defaultRules)) ?? null
    : null;
  const draftPreset = data.presets.items.find((preset) => matchesPreset(draft, preset.defaultRules)) ?? null;

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
        <div className="grid gap-px overflow-hidden rounded-card border border-line bg-line md:grid-cols-3">
          {[...data.presets.items]
            .sort((a, b) => a.order - b.order)
            .map((preset) => (
              <PresetCard
                key={preset.presetId}
                preset={preset}
                selected={matchesPreset(draft, preset.defaultRules)}
                current={savedPreset?.presetId === preset.presetId}
                onApply={() => setDraft(preset.defaultRules)}
              />
            ))}
        </div>
      </Panel>

      {data.current ? (
        <>
          <Panel
            contract="GET /api/v1/principles"
            title="현재 적용 중인 원칙"
            hint="주문 검토와 다음 자동운용 준비도는 저장된 원칙 버전을 기준으로 확인합니다."
          >
            <div className="grid gap-3 text-[13px] sm:grid-cols-3">
              <StatusItem label="저장된 설정" value={savedPreset?.nameKo ?? '사용자 설정'} />
              <StatusItem label="현재 버전" value={`v${data.current.version}`} />
              <StatusItem
                label="수정 상태"
                value={dirty ? `${draftPreset?.nameKo ?? '사용자 설정'} · 저장 전` : '저장된 설정과 일치'}
                tone={dirty ? 'warn' : 'ok'}
              />
            </div>
          </Panel>

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

          <VersionHistory history={data.history} currentVersion={data.current.version} />
        </>
      ) : (
        <CreatePrinciple presets={data.presets} draftPreset={draftPreset} onCreated={onSaved} />
      )}
    </div>
  );
}

/**
 * 원칙을 처음 만든다.
 *
 * 예전에는 "preset 을 골라 원칙을 먼저 만들어야 한다"고 안내만 하고 만들 방법이 없었다.
 * 원칙이 하나도 없는 계정은 주문 검토에 아예 들어갈 수 없는 막다른 길이었다.
 */
function CreatePrinciple({
  presets,
  draftPreset,
  onCreated,
}: {
  presets: PrinciplePresetListData;
  draftPreset: PrinciplePreset | null;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState('');
  const [presetId, setPresetId] = useState<PrincipleCurrent['presetId'] | null>(
    draftPreset?.presetId ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  const chosen = presets.items.find((preset) => preset.presetId === presetId) ?? null;
  const canSubmit = title.trim().length > 0 && chosen !== null && !busy;

  async function create() {
    if (!canSubmit || !chosen) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.createPrinciple({ title: title.trim(), presetId: chosen.presetId, mode: chosen.mode });
      setNotice({ tone: 'ok', text: '원칙을 만들었습니다.' });
      onCreated();
    } catch (cause) {
      const errorState = toErrorState<never>(cause);
      // `POST /api/v1/principles` 는 멱등키를 받지 않는다(서버 계약 확인함). 그래서 응답이
      // 오지 않은 실패는 "만들어졌는지 알 수 없음"이고, 다시 누르면 원칙이 두 개가 될 수 있다.
      // 안전한 재시도라고 말하지 않고 목록을 먼저 확인하라고 안내한다.
      const uncertain =
        errorState.kind === 'error' &&
        (errorState.code === 'NETWORK_UNAVAILABLE' || errorState.code === 'INTERNAL_ERROR');
      setNotice({
        tone: 'error',
        text:
          errorState.kind !== 'error'
            ? '원칙을 만들지 못했습니다.'
            : uncertain
              ? `${errorState.message} 만들어졌는지 알 수 없으므로 아래 목록을 먼저 확인하세요. 그대로 다시 만들면 원칙이 두 개가 될 수 있습니다.`
              : errorState.message,
      });
      // 불확실한 실패에서는 목록을 새로 읽어 사용자가 직접 판단할 근거를 준다.
      if (uncertain) onCreated();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      contract="POST /api/v1/principles"
      title="원칙 만들기"
      hint="원칙이 있어야 주문 검토와 자동운용이 동작합니다. 값은 만든 뒤에 언제든 바꿀 수 있습니다."
    >
      <div className="space-y-4">
        <div>
          <label htmlFor="principle-title" className="text-[12px] font-medium text-muted">
            이름
          </label>
          <input
            id="principle-title"
            value={title}
            placeholder="예: 내 균형형 원칙"
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1.5 w-full max-w-[420px] rounded-control border border-line bg-subtle px-4 py-2.5 text-[15px] text-ink placeholder:text-faint focus:border-navy focus:bg-panel"
          />
        </div>

        <div>
          <p className="text-[12px] font-medium text-muted">시작할 preset</p>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {[...presets.items]
              .sort((a, b) => a.order - b.order)
              .map((preset) => (
                <Button
                  key={preset.presetId}
                  aria-pressed={preset.presetId === presetId}
                  onClick={() => setPresetId(preset.presetId)}
                  className={`rounded-full border px-3.5 py-1.5 text-[13px] ${
                    preset.presetId === presetId
                      ? 'border-navy font-semibold text-navy'
                      : 'border-line text-muted hover:border-navy/40'
                  }`}
                >
                  {preset.nameKo}
                </Button>
              ))}
          </div>
          {chosen ? (
            <p className="mt-2 text-[13px] leading-6 text-muted">{chosen.descriptionKo}</p>
          ) : null}
        </div>

        {notice ? (
          <p
            className={`border-l-2 px-3 py-2 text-[13px] leading-6 ${
              notice.tone === 'ok' ? 'border-allow bg-allow/5 text-ink' : 'border-block bg-block/5 text-ink'
            }`}
          >
            {notice.text}
          </p>
        ) : null}

        <div className="border-t border-line pt-4">
          <Button disabled={!canSubmit} onClick={() => void create()} variant="primary">
            {busy ? '만드는 중' : '이 원칙으로 시작하기'}
          </Button>
        </div>
      </div>
    </Panel>
  );
}

/**
 * 버전 이력.
 *
 * 저장은 덮어쓰기가 아니라 새 버전을 쌓는 것이므로, 무엇이 언제 바뀌었는지 되짚을 수 있어야
 * 다음 원칙을 더 낫게 고칠 수 있다. 바뀐 항목은 백엔드가 준 `changedFields` 를 그대로 쓴다 —
 * 화면에서 지어내지 않는다.
 */
function VersionHistory({
  history,
  currentVersion,
}: {
  history: PrincipleHistoryData | null;
  currentVersion: number;
}) {
  if (!history) {
    return (
      <Panel contract="GET /api/v1/principles/{principleId}/versions" title="바뀐 기록">
        <p className="text-[13px] leading-6 text-muted">이력을 불러오지 못했습니다.</p>
      </Panel>
    );
  }
  if (history.items.length === 0) {
    return (
      <Panel contract="GET /api/v1/principles/{principleId}/versions" title="바뀐 기록">
        <p className="text-[13px] leading-6 text-muted">아직 쌓인 버전이 없습니다.</p>
      </Panel>
    );
  }
  return (
    <Panel
      contract="GET /api/v1/principles/{principleId}/versions"
      title="바뀐 기록"
      hint="저장할 때마다 한 버전씩 쌓입니다. 이전 버전은 지워지지 않습니다."
    >
      <ul className="divide-y divide-line">
        {history.items.map((version) => (
          <VersionRow key={version.version} version={version} current={version.version === currentVersion} />
        ))}
      </ul>
    </Panel>
  );
}

/** 규칙 안의 어떤 항목이 바뀌었는지. 백엔드가 쓰는 이름 그대로만 옮긴다. */
const CHANGED_FIELD_NAMES: Record<string, string> = {
  threshold: '기준값',
  severity: '위반 시 처리',
  enabled: '사용 여부',
  evidenceRequirement: '근거 요구',
};

/**
 * `changedFields` 한 항목을 사람이 읽는 말로 옮긴다.
 *
 * 백엔드는 `rules.<ruleId>.<field>` 같은 경로를 준다. 아는 형태면 규칙 이름으로 풀고,
 * 모르는 형태면 **원문을 그대로 보여 준다** — 지어내는 것보다 낫다.
 */
function changedFieldLabel(field: string): string {
  const parts = field.split('.');
  if (parts[0] !== 'rules' || parts.length < 2) return field;
  const ruleId = parts[1] as keyof typeof RULE_LABELS;
  const rule = RULE_LABELS[ruleId];
  if (!rule) return field;
  const attr = parts[2] ? CHANGED_FIELD_NAMES[parts[2]] ?? parts[2] : null;
  return attr ? `${rule.name} ${attr}` : rule.name;
}

function VersionRow({ version, current }: { version: PrincipleVersion; current: boolean }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-3 first:pt-0 last:pb-0">
      <span className="flex items-baseline gap-2">
        <span className="tnum text-[14px] font-semibold text-ink">v{version.version}</span>
        {current ? (
          <span className="rounded-full border border-navy px-2 py-0.5 text-[11px] font-medium text-navy">
            적용 중
          </span>
        ) : null}
        <span className="text-[12px] text-faint">{version.mode}</span>
      </span>
      <span className="min-w-0 flex-1 text-[13px] leading-6 text-muted">
        {version.changedFields.length > 0
          ? version.changedFields.map(changedFieldLabel).join(' · ')
          : '처음 만든 버전'}
      </span>
      <span className="tnum shrink-0 text-[12px] text-faint">{formatKstDateTime(version.createdAt)}</span>
    </li>
  );
}

function PresetCard({
  preset,
  selected,
  current,
  onApply,
}: {
  preset: PrinciplePreset;
  selected: boolean;
  current: boolean;
  onApply: () => void;
}) {
  const loss = preset.defaultRules.find((rule) => rule.ruleId === 'daily_loss_guard');
  const orders = preset.defaultRules.find((rule) => rule.ruleId === 'max_daily_orders');
  return (
    <Button
      onClick={onApply}
      aria-pressed={selected}
      className={`h-full w-full bg-panel px-4 py-4 text-left ${selected ? 'ring-2 ring-inset ring-navy' : ''}`}
    >
      <div className="w-full">
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-[15px] font-semibold text-ink">{preset.nameKo}</p>
          {current ? (
            <span className="shrink-0 text-eyebrow font-semibold uppercase text-allow">현재 적용</span>
          ) : null}
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
      </div>
    </Button>
  );
}

function StatusItem({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: string;
  tone?: 'default' | 'ok' | 'warn';
}) {
  const toneClass = tone === 'ok' ? 'text-allow' : tone === 'warn' ? 'text-warn' : 'text-ink';
  return (
    <div className="rounded-tile border border-line px-4 py-3">
      <p className="text-eyebrow font-semibold uppercase text-faint">{label}</p>
      <p className={`mt-1 text-[14px] font-medium ${toneClass}`}>{value}</p>
    </div>
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
  const min = negative ? -1 : 0;
  const max = negative ? 0 : 1;
  const fillPct = isRatio ? ((rule.threshold - min) / (max - min)) * 100 : 0;

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
            min={min}
            max={max}
            step={0.0001}
            value={rule.threshold}
            disabled={!rule.enabled}
            onChange={(event) => onThreshold(Number(event.target.value))}
            aria-label={`${meta.name} 값`}
            style={{ '--range-fill': `${fillPct}%` } as CSSProperties}
            className="ink-slider w-40"
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
            className="tnum w-32 rounded-control border border-line bg-panel px-3 py-1 text-right font-mono text-[13px] text-ink focus:border-navy focus:outline-none disabled:bg-subtle disabled:text-muted"
          />
        )}
        <span className="tnum w-24 text-right font-mono text-[13px] text-ink">{display}</span>
        <label className="flex items-center gap-2 whitespace-nowrap text-[12px] text-muted">
          <input
            type="checkbox"
            checked={rule.enabled}
            onChange={(event) => onToggle(event.target.checked)}
            className="ink-checkbox"
          />
          사용
        </label>
      </div>
    </li>
  );
}
