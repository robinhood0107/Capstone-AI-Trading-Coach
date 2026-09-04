'use client';

import { useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { toErrorState, useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { formatKstDateTime } from '@/shared/lib/format';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Button } from '@/shared/ui/Button';
import { Panel } from '@/shared/ui/Panel';
import type { JournalEntry } from '@/shared/api/wire';

interface Draft {
  title: string;
  content: string;
  tags: string;
}

const EMPTY: Draft = { title: '', content: '', tags: '' };

export function JournalView() {
  const resource = useResource(async () => {
    const { data } = await api.journals();
    return ready(data.items, data.items[0]?.updatedAt ?? null);
  }, []);
  const [selected, setSelected] = useState<JournalEntry | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function select(entry: JournalEntry | null) {
    setSelected(entry);
    setDraft(
      entry
        ? { title: entry.title, content: entry.content, tags: entry.tags.join(', ') }
        : EMPTY,
    );
    setMessage(null);
  }

  async function save() {
    const title = draft.title.trim();
    const content = draft.content.trim();
    const tags = Array.from(
      new Set(
        draft.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    ).slice(0, 20);
    if (!title || !content || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      if (selected) await api.updateJournal(selected.journalId, { expectedVersion: selected.version, title, content, tags });
      else await api.createJournal({ title, content, tags });
      select(null);
      resource.reload();
      setMessage('저장했습니다.');
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setMessage(error.kind === 'error' ? error.message : '저장하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selected || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.deleteJournal(selected.journalId, selected.version);
      select(null);
      resource.reload();
      setMessage('삭제했습니다.');
    } catch (cause) {
      const error = toErrorState<never>(cause);
      setMessage(error.kind === 'error' ? error.message : '삭제하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
      <AsyncBoundary state={resource.state} onRetry={resource.reload}>
        {(items) => (
          <Panel title="최근 기록" hint="최신 20개 기록입니다.">
            {items.length === 0 ? (
              <p className="text-[13px] text-muted">첫 학습일지를 작성해 보세요.</p>
            ) : (
              <ul className="divide-y divide-line/60">
                {items.map((entry) => (
                  <li key={entry.journalId}>
                    <button type="button" onClick={() => select(entry)} className="w-full py-3 text-left">
                      <p className="text-[14px] font-medium text-ink">{entry.title}</p>
                      <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-muted">{entry.content}</p>
                      <p className="mt-1 text-[11px] text-faint">{formatKstDateTime(entry.updatedAt)}</p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}
      </AsyncBoundary>

      <Panel title={selected ? '기록 수정' : '새 기록'} hint="제목과 내용은 내 계정에만 저장됩니다.">
        <div className="space-y-4">
          <input
            aria-label="학습일지 제목"
            value={draft.title}
            maxLength={120}
            onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
            placeholder="예: 오늘 자동주문에서 확인한 점"
            className="w-full rounded-control border border-line bg-panel px-4 py-2.5 text-[14px] text-ink"
          />
          <textarea
            aria-label="학습일지 내용"
            value={draft.content}
            maxLength={8192}
            rows={10}
            onChange={(event) => setDraft((current) => ({ ...current, content: event.target.value }))}
            placeholder="판단 근거, 배운 개념, 다음에 확인할 내용을 적으세요."
            className="w-full resize-y rounded-control border border-line bg-panel px-4 py-3 text-[14px] leading-6 text-ink"
          />
          <input
            aria-label="학습일지 태그"
            value={draft.tags}
            onChange={(event) => setDraft((current) => ({ ...current, tags: event.target.value }))}
            placeholder="태그는 쉼표로 구분"
            className="w-full rounded-control border border-line bg-panel px-4 py-2.5 text-[14px] text-ink"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={busy || !draft.title.trim() || !draft.content.trim()} onClick={() => void save()}>
              {busy ? '처리 중' : selected ? '수정 저장' : '기록 저장'}
            </Button>
            {selected ? (
              <>
                <Button variant="secondary" disabled={busy} onClick={() => select(null)}>새 기록</Button>
                <Button variant="secondary" disabled={busy} onClick={() => void remove()}>삭제</Button>
              </>
            ) : null}
            {message ? <p className="text-[13px] text-muted">{message}</p> : null}
          </div>
        </div>
      </Panel>
    </div>
  );
}
