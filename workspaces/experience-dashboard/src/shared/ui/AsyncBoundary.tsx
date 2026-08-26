'use client';

import type { ReactNode } from 'react';
import type { ViewState } from '@/shared/lib/viewState';
import { formatKstDateTime, relativeAge } from '@/shared/lib/format';

interface AsyncBoundaryProps<T> {
  state: ViewState<T>;
  onRetry?: () => void;
  children: (data: T, meta: { stale: boolean; asOf: string | null }) => ReactNode;
}

export function AsyncBoundary<T>({ state, onRetry, children }: AsyncBoundaryProps<T>) {
  if (state.kind === 'loading') {
    return (
      <div className="space-y-2" role="status" aria-live="polite">
        <div className="h-3 w-40 bg-line" />
        <div className="h-3 w-full bg-line/70" />
        <div className="h-3 w-3/4 bg-line/50" />
        <span className="sr-only">불러오는 중</span>
      </div>
    );
  }

  if (state.kind === 'empty') {
    return (
      <div className="border border-dashed border-rule px-4 py-6">
        <p className="font-mono text-eyebrow uppercase text-faint">데이터 없음</p>
        <p className="mt-2 text-sm font-medium text-ink">{state.title}</p>
        <p className="mt-1 text-[13px] leading-5 text-muted">{state.detail}</p>
      </div>
    );
  }

  if (state.kind === 'error') {
    return (
      <div className="border border-block/40 bg-block/5 px-4 py-5">
        <p className="font-mono text-eyebrow uppercase text-block">불러오기 실패 · {state.code}</p>
        <p className="mt-2 text-sm text-ink">{state.message}</p>
        {state.requestId ? (
          <p className="mt-1 font-mono text-[11px] text-faint">requestId {state.requestId}</p>
        ) : null}
        {state.retryable && onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 border border-block px-3 py-1.5 text-[13px] font-medium text-block hover:bg-block hover:text-white"
          >
            다시 조회
          </button>
        ) : null}
      </div>
    );
  }

  const isStale = state.kind === 'stale';
  return (
    <div>
      {isStale ? (
        <div className="mb-4 border-l-2 border-warn bg-warn/5 px-3 py-2">
          <p className="font-mono text-eyebrow uppercase text-warn">지연된 데이터</p>
          <p className="mt-1 text-[13px] leading-5 text-ink">{state.detail}</p>
          <p className="mt-0.5 text-[12px] text-muted">
            기준 시각 {formatKstDateTime(state.asOf) ?? '미상'}
            {relativeAge(state.asOf) ? ` · ${relativeAge(state.asOf)}` : ''}
          </p>
        </div>
      ) : null}
      {children(state.data, { stale: isStale, asOf: state.asOf })}
    </div>
  );
}
