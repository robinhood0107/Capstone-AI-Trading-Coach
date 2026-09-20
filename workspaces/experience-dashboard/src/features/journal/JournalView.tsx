'use client';

import { useEffect, useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { toErrorState, useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { formatKstDateTime } from '@/shared/lib/format';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Button } from '@/shared/ui/Button';
import { Panel } from '@/shared/ui/Panel';
import type { JournalEntry, JournalLinks } from '@/shared/api/wire';
import { takeJournalHandoff } from '@/shared/lib/journalHandoff';

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
  // 성공과 실패가 같은 회색 한 줄로 나오던 것을 구분한다. 저장이 실패했는지 사용자가 알아야 한다.
  const [messageTone, setMessageTone] = useState<'ok' | 'error'>('ok');
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  /*
   * 다른 화면에서 넘어온 초안. 한 번만 읽고 지우므로 뒤로 가기로 돌아와도 같은 초안이
   * 다시 뜨지 않는다. 연결 식별자는 저장할 때 함께 보낸다.
   */
  const [handoffLinks, setHandoffLinks] = useState<Partial<JournalLinks>>({});

  useEffect(() => {
    const handoff = takeJournalHandoff();
    if (!handoff) return;
    setDraft({ title: handoff.title, content: handoff.content, tags: '' });
    if (handoff.ragAnswerId) setHandoffLinks({ ragAnswerId: handoff.ragAnswerId });
  }, []);

  function notify(text: string, tone: 'ok' | 'error') {
    setMessage(text);
    setMessageTone(tone);
  }

  function select(entry: JournalEntry | null) {
    setSelected(entry);
    setDraft(
      entry
        ? { title: entry.title, content: entry.content, tags: entry.tags.join(', ') }
        : EMPTY,
    );
    setMessage(null);
    // 다른 기록을 고르면 이전 삭제 확인은 그 기록의 것이 아니다.
    setConfirmingRemove(false);
    // 인계 초안도 그 순간 끝난다. 남겨 두면 엉뚱한 기록에 연결이 붙는다.
    setHandoffLinks({});
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
      if (selected) {
        // 서버는 `links` 를 필수로 읽고 빠진 필드를 null 로 본다. 그대로 되돌려 보내지
        // 않으면 제목만 고쳐도 판단·주문·답변 연결이 지워진다.
        await api.updateJournal(selected.journalId, {
          expectedVersion: selected.version,
          title,
          content,
          tags,
          links: selected.links,
        });
      } else {
        await api.createJournal({ title, content, tags, links: handoffLinks });
      }
      select(null);
      resource.reload();
      notify('저장했습니다.', 'ok');
    } catch (cause) {
      const error = toErrorState<never>(cause);
      notify(error.kind === 'error' ? error.message : '저장하지 못했습니다.', 'error');
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
      notify('삭제했습니다.', 'ok');
    } catch (cause) {
      const error = toErrorState<never>(cause);
      notify(error.kind === 'error' ? error.message : '삭제하지 못했습니다.', 'error');
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
                      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-[11px] text-faint">{formatKstDateTime(entry.updatedAt)}</span>
                        <LinkChips links={entry.links} />
                      </div>
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
          {selected ? (
            <div className="flex flex-wrap items-center gap-2 border-b border-line pb-4">
              <span className="text-[12px] text-muted">연결된 기록</span>
              <LinkChips links={selected.links} emptyText="없음" />
            </div>
          ) : null}
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
            className="w-full resize-y rounded-card border border-line bg-panel px-4 py-3 text-[14px] leading-6 text-ink"
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
                {/* 삭제는 되돌릴 수 없다. RAG 질문 기록 삭제가 이미 쓰는 2단계 확인을 따른다 -
                    한 번 클릭으로 사라지던 것이 이 화면만 예외였다. */}
                {confirmingRemove ? (
                  <>
                    <span className="text-[13px] leading-6 text-block">되돌릴 수 없습니다.</span>
                    <Button variant="secondary" disabled={busy} onClick={() => void remove()}>삭제 확인</Button>
                    <Button variant="secondary" disabled={busy} onClick={() => setConfirmingRemove(false)}>취소</Button>
                  </>
                ) : (
                  <Button variant="secondary" disabled={busy} onClick={() => setConfirmingRemove(true)}>삭제</Button>
                )}
              </>
            ) : null}
            {message ? (
              <p role={messageTone === 'error' ? 'alert' : undefined} className={`text-[13px] ${messageTone === 'error' ? 'text-block' : 'text-muted'}`}>
                {message}
              </p>
            ) : null}
          </div>
        </div>
      </Panel>
    </div>
  );
}


/**
 * 이 기록이 무엇에 대한 것인지 보여 주는 칩.
 *
 * `journals` 표와 API 는 처음부터 다섯 가지 연결을 들고 있었는데 화면이 하나도 쓰지
 * 않았다. 식별자를 통째로 보여 주면 읽히지 않으므로 **무엇에 연결됐는지**만 말하고,
 * 실제 식별자는 마우스를 올렸을 때 보여 준다.
 */
function LinkChips({ links, emptyText }: { links: JournalLinks; emptyText?: string }) {
  const chips: { label: string; id: string }[] = [
    { label: '판단', id: links.decisionId },
    { label: '주문', id: links.orderId },
    { label: '금융 Agent 답변', id: links.ragAnswerId },
    { label: '백테스트', id: links.backtestRunId },
    { label: '자동운용 실행', id: links.automationRunId },
  ].flatMap((chip) => (chip.id ? [{ label: chip.label, id: chip.id }] : []));

  if (chips.length === 0) {
    return emptyText ? <span className="text-[11px] text-faint">{emptyText}</span> : null;
  }
  return (
    <>
      {chips.map((chip) => (
        <span
          key={chip.label}
          title={chip.id}
          className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted"
        >
          {chip.label}
        </span>
      ))}
    </>
  );
}
