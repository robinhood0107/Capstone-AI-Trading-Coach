'use client';

import { useId, useState } from 'react';
import { apiFetch } from '@/shared/api/client';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { ready, type ViewState } from '@/shared/lib/viewState';
import { toErrorState, useResource } from '@/shared/lib/useResource';
import {
  encodeServiceAccountJson,
  loadSettings,
  saveSettings,
  toFullRequest,
  type StrongLlmSettingsView,
} from './viewModel';

/** `GET /api/v1/ai-review/status` - 키 자체는 없고 키 ID 끝 네 글자만 있다. */
export interface AiReviewStatus {
  aiJudgementEnabled: boolean;
  ownKeyRegistered: boolean;
  ownKeyLast4: string | null;
  sharedAllowed: boolean;
  effectiveSource: 'OWN' | 'SHARED' | 'NONE';
  usage: { ownToday: number; sharedToday: number; ownMonth: number; sharedMonth: number };
}

interface OwnerAiState {
  settings: StrongLlmSettingsView;
  review: AiReviewStatus;
  aiJudgementEnabled: boolean;
}

async function loadOwnerAi(): Promise<ViewState<OwnerAiState>> {
  const [settings, review] = await Promise.all([
    loadSettings(),
    apiFetch<AiReviewStatus>('/api/v1/ai-review/status'),
  ]);
  if (settings.kind !== 'ready') return settings as ViewState<never>;
  return ready({ settings: settings.data, review: review.data, aiJudgementEnabled: review.data.aiJudgementEnabled });
}

/** 지금 AI 검토가 누구의 키로 불리는지 한 문장. 무장·실행 경로와 같은 규칙(서버 계산)을 그대로 말한다. */
export function effectiveSourceText(review: AiReviewStatus): string {
  if (review.effectiveSource === 'OWN') return 'AI 검토는 내 Vertex 서비스 계정(내 Google Cloud 프로젝트)으로 호출됩니다.';
  if (review.effectiveSource === 'SHARED') return 'AI 검토는 서비스 운영자의 공용 Vertex로 호출됩니다.';
  return review.aiJudgementEnabled
    ? '공용 Vertex 사용이 꺼져 있어 내 키를 등록하거나 AI 검토를 꺼야 자동매매를 시작할 수 있습니다.'
    : '공용 Vertex 사용이 꺼져 있습니다. AI 검토를 켜려면 내 키를 등록하세요.';
}

/**
 * FULL 설정의 "자동매매 AI 검토" 칸.
 *
 * 사용자 자기 Vertex 서비스 계정을 등록하면 AI 검토가 그 계정(사용자 GCP 프로젝트)으로 불린다. 등록하지
 * 않으면 이 서비스가 허용하는 동안 운영자 공용 Vertex 로 검토한다. AI 검토를 끄면 자동매매는 규칙만으로
 * 판단한다. 등록한 키는 암호화해 저장하고 다시 보여 주지 않는다 - 화면에는 키 ID 끝 네 글자만 나온다.
 */
export function OwnerVertexCredentialView() {
  const loaded = useResource(loadOwnerAi, []);
  return (
    <AsyncBoundary state={loaded.state} onRetry={loaded.reload}>
      {(initial) => <OwnerVertexForm initial={initial} reload={loaded.reload} />}
    </AsyncBoundary>
  );
}

function OwnerVertexForm({ initial, reload }: { initial: OwnerAiState; reload: () => void }) {
  const id = useId();
  const [json, setJson] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [aiEnabled, setAiEnabled] = useState(initial.aiJudgementEnabled);
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const keyLast4 = initial.settings.keyLast4;
  const encoded = json.trim() === '' ? null : encodeServiceAccountJson(json);
  const blocked = encoded !== null && !encoded.ok ? encoded.error : null;
  const unchanged = encoded === null && !clearKey && aiEnabled === initial.aiJudgementEnabled;

  async function submit() {
    if (pending || blocked !== null || unchanged) return;
    setPending(true);
    setOutcome(null);
    setError(null);
    try {
      await saveSettings(
        toFullRequest(initial.settings, encoded?.ok ? encoded.value : null, clearKey, aiEnabled),
      );
      setJson('');
      setClearKey(false);
      setOutcome('저장했습니다. 다음 자동매매 시작(무장)부터 적용됩니다.');
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(
        state.kind === 'error' && state.code === 'VALIDATION_ERROR'
          ? 'Vertex 서비스 계정 키 파일 형식이 아닙니다. 파일 내용을 그대로 붙여 넣으세요.'
          : state.kind === 'error'
            ? state.message
            : '저장하지 못했습니다.',
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel
      contract="owner-vertex-credential"
      title="내 Vertex 키 · 자동매매 AI 검토"
      hint="AI 검토는 후보 종목의 공개 악재를 한 번 더 확인합니다. 끄면 규칙만으로 판단합니다."
    >
      <div className="space-y-4 text-[13px] leading-6 text-ink">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={aiEnabled}
            onChange={(event) => setAiEnabled(event.target.checked)}
          />
          자동매매에서 AI 검토 사용
        </label>
        <div className="rounded-tile border border-line bg-subtle px-4 py-3">
          {keyLast4 !== null ? (
            <p data-testid="owner-vertex-registered">내 Vertex 서비스 계정 등록됨 (키 ID …{keyLast4})</p>
          ) : (
            <p data-testid="owner-vertex-missing">내 Vertex 서비스 계정이 없습니다.</p>
          )}
          <p data-testid="owner-vertex-effective" className="text-muted">
            {effectiveSourceText(initial.review)}
          </p>
          <p data-testid="owner-vertex-usage" className="text-muted">
            AI 검토 호출 · 오늘 내 키 {initial.review.usage.ownToday}회 / 공용 {initial.review.usage.sharedToday}회 · 이번 달 내 키{' '}
            {initial.review.usage.ownMonth}회 / 공용 {initial.review.usage.sharedMonth}회
          </p>
        </div>
        <div className="space-y-1">
          <label className="block text-xs font-medium text-muted" htmlFor={`${id}-sa`}>
            Vertex 서비스 계정 키 JSON {keyLast4 !== null ? '(비워 두면 그대로 둡니다)' : ''}
          </label>
          <textarea
            id={`${id}-sa`}
            className="h-28 w-full rounded-control border border-line bg-panel px-3 py-2 font-mono text-[12px] text-ink focus:border-navy focus:outline-none"
            autoComplete="off"
            spellCheck={false}
            disabled={clearKey}
            value={json}
            placeholder='{"type": "service_account", ...}'
            onChange={(event) => setJson(event.target.value)}
          />
          {keyLast4 !== null && (
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} />
              등록한 서비스 계정 지우기
            </label>
          )}
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            className="rounded bg-brand px-4 py-2 text-sm font-medium text-on-brand disabled:opacity-50"
            disabled={pending || blocked !== null || unchanged}
            onClick={() => void submit()}
          >
            {pending ? '저장 중…' : '저장'}
          </Button>
          {blocked !== null && <p className="text-sm text-hold">{blocked}</p>}
          {outcome !== null && <p className="text-sm text-allow">{outcome}</p>}
          {error !== null && <p className="text-sm text-block">{error}</p>}
        </div>
      </div>
    </Panel>
  );
}
