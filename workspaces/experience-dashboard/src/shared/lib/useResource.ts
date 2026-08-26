'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiFailure } from '@/shared/api/envelope';
import type { ViewState } from './viewState';

interface UseResource<T> {
  state: ViewState<T>;
  reload: () => void;
}

/** 실패를 화면 상태로 바꾸는 공통 변환. 화면마다 try/catch를 반복하지 않기 위해 한곳에 둔다. */
export function toErrorState<T>(cause: unknown): ViewState<T> {
  if (cause instanceof ApiFailure) {
    return {
      kind: 'error',
      code: cause.code,
      message: cause.userMessage,
      retryable: cause.retryable,
      requestId: cause.requestId,
    };
  }
  return {
    kind: 'error',
    code: 'NETWORK_UNAVAILABLE',
    message: '서버에 연결하지 못했습니다. 연결 상태와 API 주소를 확인한 뒤 다시 조회하세요.',
    retryable: true,
    requestId: null,
  };
}

/**
 * loader는 데이터가 아니라 ViewState를 반환한다.
 * "비었는가(empty)"와 "지연됐는가(stale)"의 판정은 도메인 어댑터의 책임이기 때문이다.
 *
 * enabled=false이면 호출하지 않는다. 로그인 전 화면에서 401을 만들지 않기 위해 쓴다.
 */
export function useResource<T>(
  loader: () => Promise<ViewState<T>>,
  deps: unknown[],
  enabled = true,
): UseResource<T> {
  const [state, setState] = useState<ViewState<T>>({ kind: 'loading' });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => {
    setState({ kind: 'loading' });
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setState({ kind: 'loading' });
    loader()
      .then((next) => {
        if (active) setState(next);
      })
      .catch((cause: unknown) => {
        if (active) setState(toErrorState<T>(cause));
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled]);

  return { state, reload };
}
