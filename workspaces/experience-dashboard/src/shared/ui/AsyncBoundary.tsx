'use client';

import type { ReactNode } from 'react';
import type { ViewState } from '@/shared/lib/viewState';
import { formatKstDateTime, relativeAge } from '@/shared/lib/format';

interface AsyncBoundaryProps<T> {
  state: ViewState<T>;
  onRetry?: () => void;
  children: (data: T, meta: { stale: boolean; asOf: string | null }) => ReactNode;
}

/** loading / empty / error / stale 네 상태를 서로 다른 모양으로 구분한다 (최종 명세서 10.4). */
export function AsyncBoundary<T>({ state, onRetry, children }: AsyncBoundaryProps<T>) {
  if (state.kind === 'loading') {
    return (
      <div className="animate-pulse space-y-2.5" role="status" aria-live="polite">
        <div className="h-3 w-40 rounded-full bg-line" />
        <div className="h-3 w-full rounded-full bg-line/70" />
        <div className="h-3 w-3/4 rounded-full bg-line/50" />
        <span className="sr-only">불러오는 중</span>
      </div>
    );
  }

  if (state.kind === 'empty') {
    return (
      <div className="rounded-tile border border-dashed border-rule px-5 py-7 text-center">
        <p className="text-eyebrow font-semibold uppercase text-faint">데이터 없음</p>
        <p className="mt-2 text-sm font-medium text-ink">{state.title}</p>
        <p className="mt-1 text-[13px] leading-5 text-muted">{state.detail}</p>
      </div>
    );
  }

  if (state.kind === 'error') {
    return (
      <div className="rounded-tile bg-block/[0.06] px-5 py-5">
        <p className="text-eyebrow font-semibold uppercase text-block">
          불러오기 실패 · {state.code}
        </p>
        <p className="mt-2 text-sm leading-6 text-ink">{state.message}</p>
        {state.requestId ? (
          <p className="mt-1 font-mono text-[11px] text-faint">requestId {state.requestId}</p>
        ) : null}
        {state.retryable && onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="mt-4 rounded-full bg-block px-4 py-1.5 text-[13px] font-medium text-white hover:opacity-90"
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
        <div className="mb-4 rounded-tile bg-warn/[0.07] px-4 py-3">
          <p className="text-eyebrow font-semibold uppercase text-warn">지연된 데이터</p>
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
