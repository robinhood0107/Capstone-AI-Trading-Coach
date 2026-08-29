'use client';

import { useEffect, useState } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { useResource, toErrorState } from '@/shared/lib/useResource';
import { formatKstDate, formatRatio } from '@/shared/lib/format';
import type { ViewState } from '@/shared/lib/viewState';
import {
  EXTERNAL_DISCLOSURE,
  EXTERNAL_POLICY,
  EXTERNAL_PROCESSORS,
  askRag,
  loadConsentGranted,
  loadRegistry,
  recordConsent,
  type RagAnswerView,
  type SourceItem,
} from './viewModel';

const EXAMPLES = [
  '금 ETF의 롤오버 위험은 무엇인가요?',
  'MDD와 Sharpe는 각각 무엇을 말해주나요?',
  '삼성전자 지금 사도 되나요?',
];

const STATUS_TONE: Record<string, string> = {
  ANSWERED: 'border-allow',
  RETRIEVAL_ONLY: 'border-hold',
  RETRIEVAL_FAILURE: 'border-hold',
  BLOCKED_SENSITIVE: 'border-block',
  BLOCKED_ADVICE: 'border-block',
  GENERATION_UNAVAILABLE: 'border-hold',
};

const CITATION_KIND_LABEL: Record<string, string> = {
  PUBLIC_WEB: '공개 문헌',
  LOCAL_DOCUMENT: '내 문서',
};

export function RagGuideView() {
  const [question, setQuestion] = useState('');
  const [answerMode, setAnswerMode] = useState<'CONCISE' | 'DETAILED'>('CONCISE');
  const [answerState, setAnswerState] = useState<ViewState<RagAnswerView> | null>(null);
  const [pending, setPending] = useState(false);
  const [consentGranted, setConsentGranted] = useState<boolean | null>(null);
  const [consentPending, setConsentPending] = useState(false);
  const registry = useResource(loadRegistry, []);

  useEffect(() => {
    let cancelled = false;
    void loadConsentGranted().then((granted) => {
      if (!cancelled) setConsentGranted(granted);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function changeConsent(action: 'GRANT' | 'REVOKE') {
    if (consentPending) return;
    setConsentPending(true);
    try {
      await recordConsent(action);
      setConsentGranted(action === 'GRANT');
      if (action === 'REVOKE') setAnswerState(null);
    } finally {
      setConsentPending(false);
    }
  }

  async function submit(text: string) {
    const trimmed = text.trim();
    if (trimmed.length === 0 || pending) return;
    setPending(true);
    setAnswerState({ kind: 'loading' });
    try {
      setAnswerState(await askRag(trimmed, answerMode));
    } catch (cause) {
      setAnswerState(toErrorState<RagAnswerView>(cause));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-6">
      <Panel
        contract="POST /api/v2/rag/consents"
        title="외부 처리 동의"
        hint="동의하기 전에는 질문이 외부로 나가지 않습니다."
        actions={
          <span className="font-mono text-eyebrow uppercase text-faint">
            {consentGranted === null ? 'CHECKING' : consentGranted ? 'GRANTED' : 'REQUIRED'}
          </span>
        }
      >
        <p className="text-[13px] leading-6 text-muted">{EXTERNAL_DISCLOSURE}</p>
        <p className="mt-2 text-[13px] leading-6 text-muted">{EXTERNAL_POLICY}</p>
        <p className="mt-2 font-mono text-[11px] text-faint">처리자: {EXTERNAL_PROCESSORS}</p>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={() => void changeConsent('GRANT')}
            disabled={consentPending || consentGranted === true}
            className="border border-navy bg-navy px-3 py-1.5 text-[13px] text-white disabled:border-line disabled:bg-line disabled:text-faint"
          >
            동의
          </button>
          <button
            type="button"
            onClick={() => void changeConsent('REVOKE')}
            disabled={consentPending || consentGranted !== true}
            className="border border-line px-3 py-1.5 text-[13px] text-muted hover:border-navy hover:text-navy disabled:text-faint"
          >
            철회
          </button>
        </div>
      </Panel>

      <Panel
        contract="POST /api/v2/rag/ask"
        title="금융 개념 물어보기"
        hint="개념과 위험을 설명합니다. 무엇을 사고 팔지는 답하지 않습니다."
      >
        <div className="space-y-3">
          <label htmlFor="rag-question" className="sr-only">
            질문
          </label>
          <textarea
            id="rag-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value.slice(0, 1000))}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void submit(question);
            }}
            rows={3}
            placeholder="예: 금 ETF의 롤오버 위험은 무엇인가요?"
            className="w-full resize-y border border-line bg-panel px-3 py-2.5 text-[14px] leading-6 text-ink placeholder:text-faint"
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {(['CONCISE', 'DETAILED'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setAnswerMode(mode)}
                  className={`border px-3 py-1.5 text-[13px] ${
                    answerMode === mode
                      ? 'border-navy bg-navy text-white'
                      : 'border-line bg-panel text-muted hover:border-navy hover:text-navy'
                  }`}
                >
                  {mode === 'CONCISE' ? '짧게' : '자세히'}
                </button>
              ))}
              <span className="tnum font-mono text-[11px] text-faint">{question.length}/1000</span>
            </div>
            <button
              type="button"
              onClick={() => void submit(question)}
              disabled={pending || consentGranted !== true || question.trim().length === 0}
              className="border border-navy bg-navy px-4 py-1.5 text-[13px] font-medium text-white disabled:border-line disabled:bg-line disabled:text-faint"
            >
              {pending ? '찾는 중' : '물어보기'}
            </button>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => {
                  setQuestion(example);
                  void submit(example);
                }}
                className="border border-line px-2.5 py-1 text-[12px] text-muted hover:border-navy hover:text-navy"
              >
                {example}
              </button>
            ))}
          </div>
        </div>
      </Panel>

      {answerState ? (
        <AsyncBoundary state={answerState}>
          {(view) => (
            <Panel
              contract="rag-v2-answer.v1"
              title={view.statusHeadline}
              hint={view.statusDetail}
              actions={
                <span className="font-mono text-eyebrow uppercase text-faint">
                  {view.generationStatus}
                </span>
              }
            >
              <div className={`border-l-2 ${STATUS_TONE[view.generationStatus] ?? 'border-line'} pl-4`}>
                {view.answer ? (
                  <p className="whitespace-pre-line text-[14px] leading-7 text-ink">{view.answer}</p>
                ) : (
                  <p className="text-[13px] leading-6 text-muted">
                    설명 문장이 생성되지 않았습니다. 아래 출처를 직접 확인하세요.
                  </p>
                )}
              </div>

              <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-line pt-4">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-eyebrow uppercase text-faint">출처 연결률</span>
                  <Numeric
                    value={view.citationCoverage}
                    format={(v) => formatRatio(v, 0)}
                    missingReason="생성된 문장이 없어 출처 연결률을 계산하지 않았습니다."
                  />
                </div>
                {view.retrievalFailure ? (
                  <span className="border border-hold px-2 py-0.5 font-mono text-[11px] text-hold">
                    RETRIEVAL_FAILURE
                  </span>
                ) : null}
                {view.guardrailFlags.map((flag) => (
                  <span
                    key={flag}
                    className="border border-block px-2 py-0.5 font-mono text-[11px] text-block"
                  >
                    {flag}
                  </span>
                ))}
                <span className="ml-auto font-mono text-[11px] text-faint">{view.answerId}</span>
              </div>

              {view.sourcesUnavailableReason ? (
                <p className="mt-4 border border-dashed border-rule px-4 py-4 text-[13px] leading-5 text-muted">
                  {view.sourcesUnavailableReason}
                </p>
              ) : null}

              {view.topSources.length > 0 ? (
                <div className="mt-5">
                  <p className="font-mono text-eyebrow uppercase text-faint">핵심 출처</p>
                  <ul className="mt-2 space-y-4">
                    {view.topSources.map((source) => (
                      <SourceRow key={source.sourceId} source={source} />
                    ))}
                  </ul>
                </div>
              ) : null}

              {view.expandableSources.length > 0 ? (
                <details className="mt-5 border-t border-line pt-4">
                  <summary className="cursor-pointer text-[13px] text-navy">
                    관련 출처 {view.expandableSources.length}개 더 보기
                  </summary>
                  <ul className="mt-3 space-y-4">
                    {view.expandableSources.map((source) => (
                      <SourceRow key={source.sourceId} source={source} />
                    ))}
                  </ul>
                </details>
              ) : null}
            </Panel>
          )}
        </AsyncBoundary>
      ) : null}

      <AsyncBoundary state={registry.state} onRetry={registry.reload}>
        {(cards) => (
          <Panel
            contract="GET /api/v1/rag/sources"
            title="이 시스템이 참고하는 자료"
            hint="뉴스 원문은 넣지 않습니다. 검증된 출처 카드만 사용합니다."
          >
            {cards.length === 0 ? (
              <p className="text-[13px] text-muted">등록된 출처가 없습니다.</p>
            ) : (
              <ul className="grid gap-x-8 gap-y-4 md:grid-cols-2">
                {cards.map((card) => (
                  <li key={card.sourceId} className="border-t border-line pt-3">
                    <p className="text-[13px] font-medium text-ink">{card.title}</p>
                    <p className="mt-1 text-[12px] text-muted">
                      {card.institution} · {card.attribution}
                    </p>
                    <p className="mt-1 font-mono text-[11px] text-faint">
                      {card.topic}
                      {card.lastCheckedAt ? ` · 확인 ${formatKstDate(card.lastCheckedAt)}` : ''}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}
      </AsyncBoundary>
    </div>
  );
}

function SourceRow({ source }: { source: SourceItem }) {
  return (
    <li>
      <div className="flex flex-wrap items-baseline gap-2">
        <p className="text-[13px] font-medium leading-5 text-ink">{source.title}</p>
        <span className="border border-line px-1.5 py-0.5 font-mono text-[10px] uppercase text-faint">
          {CITATION_KIND_LABEL[source.citationKind] ?? source.citationKind}
        </span>
      </div>
      <p className="mt-1 text-[13px] leading-6 text-muted">{source.summary}</p>
      <p className="mt-1 font-mono text-[11px] text-faint">
        {source.sourceId}
        {source.institution ? ` · ${source.institution}` : ''}
      </p>
      {source.href ? (
        <a
          href={source.href}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-block text-[12px] text-navy underline underline-offset-2"
        >
          원문 열기
        </a>
      ) : null}
    </li>
  );
}
