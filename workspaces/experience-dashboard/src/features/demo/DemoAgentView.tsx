'use client';

import { useState } from 'react';

const QUESTIONS = [
  { id: 'diversification', title: '분산투자는 위험을 어떻게 줄이나요?', note: '한 종목에 집중하는 위험을 살펴봅니다.' },
  { id: 'asset_allocation', title: '자산 배분은 무엇을 고려하나요?', note: '투자 기간과 위험 감수 성향을 살펴봅니다.' },
  { id: 'past_performance', title: '과거 성과는 어떻게 읽어야 하나요?', note: '백테스트와 실제 성과의 차이를 살펴봅니다.' },
] as const;

type DemoAnswer = {
  answer: string;
  citations: { citationId: string; title: string; url: string }[];
  generationStatus: 'ANSWERED' | 'RETRIEVAL_ONLY';
};

function readAnswer(payload: unknown): DemoAnswer | null {
  if (!payload || typeof payload !== 'object') return null;
  const envelope = payload as { success?: unknown; data?: unknown };
  if (envelope.success !== true || !envelope.data || typeof envelope.data !== 'object') return null;
  const data = envelope.data as { answer?: unknown; citations?: unknown; generationStatus?: unknown };
  if (
    typeof data.answer !== 'string' ||
    data.answer.length < 1 ||
    data.answer.length > 8192 ||
    !Array.isArray(data.citations) ||
    data.citations.length > 3 ||
    (data.generationStatus !== 'ANSWERED' && data.generationStatus !== 'RETRIEVAL_ONLY')
  ) return null;
  const citations = data.citations.map((item: unknown) => {
    if (!item || typeof item !== 'object') return null;
    const citation = item as { citationId?: unknown; title?: unknown; url?: unknown };
    if (
      typeof citation.citationId !== 'string' ||
      typeof citation.title !== 'string' ||
      typeof citation.url !== 'string'
    ) return null;
    try {
      const url = new URL(citation.url);
      if (url.protocol !== 'https:' || url.hostname !== 'www.investor.gov') return null;
    } catch {
      return null;
    }
    return { citationId: citation.citationId, title: citation.title, url: citation.url };
  });
  if (citations.some((item) => item === null)) return null;
  return {
    answer: data.answer,
    citations: citations as DemoAnswer['citations'],
    generationStatus: data.generationStatus,
  };
}

export function DemoAgentView() {
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [answer, setAnswer] = useState<DemoAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(questionId: string) {
    if (pending) return;
    setSelected(questionId);
    setPending(true);
    setAnswer(null);
    setError(null);
    try {
      const response = await fetch('/api/v1/demo/agent/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'omit',
        cache: 'no-store',
        body: JSON.stringify({ questionId }),
      });
      if (response.status === 429) {
        setError('데모 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.');
        return;
      }
      if (!response.ok) throw new Error('DEMO_AGENT_UNAVAILABLE');
      const parsed = readAnswer(await response.json());
      if (!parsed) throw new Error('DEMO_AGENT_RESPONSE_INVALID');
      setAnswer(parsed);
    } catch {
      setError('지금은 데모 Agent를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-5 py-8 text-ink sm:px-8 sm:py-14">
      <header className="flex items-center justify-between border-b border-line pb-5">
        <span className="text-xl font-semibold tracking-tight">MARS</span>
        <span className="rounded-full bg-subtle px-3 py-1 text-xs font-medium text-muted">공개 데모</span>
      </header>

      <section className="pt-12">
        <p className="text-sm font-semibold text-navy">로그인 없는 교육용 Agent</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">금융 개념을 예제로 살펴보세요</h1>
        <p className="mt-4 max-w-2xl text-sm leading-7 text-muted">
          아래 질문 하나를 선택하면 공개 교육 자료를 바탕으로 답변합니다. 계좌 연결, 주문, 자동운용은 이 데모에 없습니다.
        </p>
      </section>

      <section aria-label="예제 질문" className="mt-9 grid gap-3">
        {QUESTIONS.map((question) => (
          <button
            key={question.id}
            type="button"
            disabled={pending}
            aria-pressed={selected === question.id}
            onClick={() => void ask(question.id)}
            className="tap rounded-panel border border-line bg-panel px-5 py-4 text-left transition-colors hover:border-navy disabled:cursor-wait disabled:opacity-60"
          >
            <span className="block text-[15px] font-semibold text-ink">{question.title}</span>
            <span className="mt-1 block text-xs text-muted">{question.note}</span>
          </button>
        ))}
      </section>

      <section aria-live="polite" className="mt-8 min-h-28">
        {pending ? <p className="text-sm text-muted">예제 근거를 읽고 있습니다…</p> : null}
        {error ? <p role="alert" className="rounded-panel border border-line bg-panel px-5 py-4 text-sm text-block">{error}</p> : null}
        {answer ? (
          <div className="rounded-panel border border-line bg-panel px-5 py-6">
            <h2 className="text-lg font-semibold">Agent 답변</h2>
            <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-ink">{answer.answer}</p>
            {answer.citations.length > 0 ? (
              <div className="mt-6 border-t border-line pt-4">
                <h3 className="text-xs font-semibold text-muted">참고 자료</h3>
                <ul className="mt-2 space-y-2">
                  {answer.citations.map((citation) => (
                    <li key={citation.citationId}>
                      <a href={citation.url} target="_blank" rel="noopener noreferrer" className="text-sm text-navy underline underline-offset-2">
                        {citation.title}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <footer className="mt-auto border-t border-line pt-5 text-xs leading-6 text-muted">
        공개 교육 예제만 사용합니다. 이 답변은 종목 판단이나 주문에 사용하지 않습니다.
      </footer>
    </main>
  );
}
