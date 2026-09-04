'use client';

import { useState } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { useResource, toErrorState } from '@/shared/lib/useResource';
import type { ViewState } from '@/shared/lib/viewState';
import {
  PROVIDERS,
  PROVIDER_LABEL,
  PROVIDER_NEEDS_KEY,
  loadSettings,
  saveSettings,
  toRequest,
  validationError,
  type Provider,
  type StrongLlmSettingsView as SettingsView,
} from './viewModel';

const FIELD =
  'w-full rounded-control border border-line bg-panel px-3 py-2 text-sm text-ink ' +
  'focus:border-navy focus:outline-none';
const LABEL = 'block text-xs font-medium text-muted';

export function StrongLlmSettingsView() {
  const loaded = useResource(loadSettings, []);
  return (
    <AsyncBoundary state={loaded.state} onRetry={loaded.reload}>
      {(initial) => <SettingsForm initial={initial} />}
    </AsyncBoundary>
  );
}

function SettingsForm({ initial }: { initial: SettingsView }) {
  const [view, setView] = useState<SettingsView>(initial);
  const [apiKey, setApiKey] = useState('');
  const [fallbackApiKey, setFallbackApiKey] = useState('');
  const [dailyCapInput, setDailyCapInput] = useState(String(initial.dailyGenerateCallCap));
  const [clearPrimary, setClearPrimary] = useState(false);
  const [clearFallback, setClearFallback] = useState(false);
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ViewState<string> | null>(null);

  const blocked =
    dailyCapInput.trim() === ''
      ? '하루 호출 상한을 입력하세요.'
      : validationError(view, apiKey, fallbackApiKey);
  const patch = (next: Partial<SettingsView>) => setView((current) => ({ ...current, ...next }));

  async function submit() {
    if (blocked !== null || pending) return;
    setPending(true);
    setOutcome(null);
    try {
      await saveSettings(toRequest(view, apiKey, fallbackApiKey, clearPrimary, clearFallback));
      setApiKey('');
      setFallbackApiKey('');
      setClearPrimary(false);
      setClearFallback(false);
      const saved = await loadSettings();
      if (saved.kind === 'ready') {
        setView(saved.data);
        setDailyCapInput(String(saved.data.dailyGenerateCallCap));
      }
      setOutcome({ kind: 'ready', data: '저장했습니다.', asOf: null });
    } catch (error) {
      setOutcome(toErrorState(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        contract="strong-llm-settings"
        title="판단과 설명에 쓰는 모델"
        hint="1차가 실패하면 2차로 넘어갑니다. 둘 다 실패하면 자동매매는 규칙만으로 계속하고 그 사실을 판단에 남깁니다."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <ProviderColumn
            heading="1차 provider"
            provider={view.provider}
            modelId={view.modelId}
            baseUrl={view.baseUrl}
            keyLast4={view.keyLast4}
            apiKey={apiKey}
            clearKey={clearPrimary}
            onProvider={(provider) => patch({ provider: provider === '' ? 'vertex' : provider })}
            onModelId={(modelId) => patch({ modelId })}
            onBaseUrl={(baseUrl) => patch({ baseUrl })}
            onApiKey={setApiKey}
            onClearKey={setClearPrimary}
          />
          <ProviderColumn
            heading="2차 provider (선택)"
            provider={view.fallbackProvider}
            modelId={view.fallbackModelId}
            baseUrl={view.fallbackBaseUrl}
            keyLast4={view.fallbackKeyLast4}
            apiKey={fallbackApiKey}
            clearKey={clearFallback}
            allowNone
            onProvider={(provider) => patch({ fallbackProvider: provider })}
            onModelId={(fallbackModelId) => patch({ fallbackModelId })}
            onBaseUrl={(fallbackBaseUrl) => patch({ fallbackBaseUrl })}
            onApiKey={setFallbackApiKey}
            onClearKey={setClearFallback}
          />
        </div>
      </Panel>

      <Panel
        contract="strong-llm-settings"
        title="답변 언어와 하루 호출 상한"
        hint="출력 길이가 아니라 횟수로 통제합니다. 상한을 좁게 두면 답이 문장 중간에서 잘리고, 잘린 답은 통째로 버려집니다."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1">
            <label className={LABEL} htmlFor="answer-language">
              답변 언어
            </label>
            <select
              id="answer-language"
              className={FIELD}
              value={view.answerLanguage}
              onChange={(event) =>
                patch({ answerLanguage: event.target.value === 'en' ? 'en' : 'ko' })
              }
            >
              <option value="ko">한국어</option>
              <option value="en">English</option>
            </select>
          </div>
          <div className="space-y-1">
            <label className={LABEL} htmlFor="daily-cap">
              하루 호출 상한 (1–500)
            </label>
            <input
              id="daily-cap"
              className={FIELD}
              type="number"
              min={1}
              max={500}
              value={dailyCapInput}
              onChange={(event) => {
                const next = event.target.value;
                setDailyCapInput(next);
                if (/^[0-9]+$/.test(next)) patch({ dailyGenerateCallCap: Number(next) });
              }}
            />
          </div>
        </div>
        <UsageLine view={view} />
      </Panel>

      <div className="flex items-center gap-3">
        <Button
          variant="primary"
          className="rounded bg-brand px-4 py-2 text-sm font-medium text-on-brand disabled:opacity-50"
          disabled={blocked !== null || pending}
          onClick={() => void submit()}
        >
          {pending ? '저장 중…' : '저장'}
        </Button>
        {blocked !== null && <p className="text-sm text-hold">{blocked}</p>}
        {outcome?.kind === 'ready' && <p className="text-sm text-allow">{outcome.data}</p>}
        {outcome?.kind === 'error' && (
          <p className="text-sm text-block">저장하지 못했습니다: {outcome.message}</p>
        )}
      </div>
    </div>
  );
}

function UsageLine({ view }: { view: SettingsView }) {
  if (view.effectiveDailyCap === null) {
    return (
      <p className="mt-3 text-xs text-muted">
        생성이 아직 열려 있지 않아 오늘 사용량이 없습니다. 검색과 인용만 동작합니다.
      </p>
    );
  }
  return (
    <p className="mt-3 text-xs text-muted">
      오늘 {view.usedToday === null ? '집계 없음' : `${view.usedToday}회 사용`},{' '}
      {view.remaining === null ? '잔여 집계 없음' : `${view.remaining}회 남음`} (적용 중인 상한{' '}
      {view.effectiveDailyCap}회).
      {view.effectiveDailyCap !== view.dailyGenerateCallCap &&
        ' 배포 정책이 더 좁은 상한을 쓰고 있습니다.'}
    </p>
  );
}

interface ColumnProps {
  heading: string;
  provider: Provider | '';
  modelId: string;
  baseUrl: string;
  keyLast4: string | null;
  apiKey: string;
  clearKey: boolean;
  allowNone?: boolean;
  onProvider: (provider: Provider | '') => void;
  onModelId: (value: string) => void;
  onBaseUrl: (value: string) => void;
  onApiKey: (value: string) => void;
  onClearKey: (value: boolean) => void;
}

function ProviderColumn(props: ColumnProps) {
  const selected = props.provider;
  const needsKey = selected !== '' && PROVIDER_NEEDS_KEY[selected];
  return (
    <fieldset className="space-y-3 rounded-card border border-line p-4">
      <legend className="px-1 text-xs font-medium text-ink">{props.heading}</legend>
      <div className="space-y-1">
        <label className={LABEL}>제공자</label>
        <select
          className={FIELD}
          value={selected}
          onChange={(event) => props.onProvider(event.target.value as Provider | '')}
        >
          {props.allowNone === true && <option value="">사용 안 함</option>}
          {PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {PROVIDER_LABEL[provider]}
            </option>
          ))}
        </select>
      </div>
      {selected !== '' && (
        <>
          <div className="space-y-1">
            <label className={LABEL}>모델 이름 (비우면 배포 기본값)</label>
            <input
              className={FIELD}
              value={props.modelId}
              placeholder="gemini-3.5-flash"
              onChange={(event) => props.onModelId(event.target.value)}
            />
          </div>
          {selected === 'custom' && (
            <div className="space-y-1">
              <label className={LABEL}>https 주소</label>
              <input
                className={FIELD}
                value={props.baseUrl}
                placeholder="https://…/v1"
                onChange={(event) => props.onBaseUrl(event.target.value)}
              />
            </div>
          )}
          {needsKey ? (
            <div className="space-y-1">
              <label className={LABEL}>
                API 키
                {props.keyLast4 !== null && (
                  <span className="ml-2 text-faint">저장됨 (…{props.keyLast4})</span>
                )}
              </label>
              <input
                className={FIELD}
                type="password"
                autoComplete="off"
                value={props.apiKey}
                disabled={props.clearKey}
                placeholder={props.keyLast4 === null ? '키를 입력하세요' : '비워 두면 그대로 둡니다'}
                onChange={(event) => props.onApiKey(event.target.value)}
              />
              {props.keyLast4 !== null && (
                <label className="flex items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={props.clearKey}
                    onChange={(event) => props.onClearKey(event.target.checked)}
                  />
                  저장된 키 지우기
                </label>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted">
              Vertex는 서버에 놓인 서비스계정으로 붙습니다. 이 화면에서 키를 받지 않습니다.
            </p>
          )}
        </>
      )}
    </fieldset>
  );
}
