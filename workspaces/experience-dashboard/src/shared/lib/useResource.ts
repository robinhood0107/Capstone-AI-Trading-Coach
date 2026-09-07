'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiFailure, revokesAccess } from '@/shared/api/envelope';
import { hasData, type ViewState } from './viewState';

interface UseResource<T> {
  state: ViewState<T>;
  reload: () => void;
  refreshError: string | null;
}

/**
 * 갱신 실패에서 마지막 성공 값을 지킨다.
 *
 * 판정 기준이 `retryable` 이 아니라 `revokesAccess` 인 이유: `retryable` 은 "다시 조회
 * 버튼을 보여도 되나"라서 서버가 새 error code 를 추가하면 그 코드는 자동으로
 * non-retryable 이 되고, 그 순간 이미 보여 주던 값까지 통째로 사라졌다. 권한이 회수된
 * 경우에만 값을 지우고 나머지는 유지한다.
 */
export function retainDuringRefresh<T>(previous: ViewState<T>, next: ViewState<T>): ViewState<T> {
  return next.kind === 'error' && !revokesAccess(next.code) && hasData(previous) ? previous : next;
}

/** 연속 실패 시 폴링 간격 상한. 서버가 닫혀 있는 동안 5초마다 계속 때리지 않는다. */
const MAX_BACKOFF_MS = 60_000;

function backoffMs(baseMs: number, consecutiveFailures: number): number {
  if (consecutiveFailures <= 0) return baseMs;
  return Math.min(baseMs * 2 ** consecutiveFailures, MAX_BACKOFF_MS);
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
  refreshIntervalMs = 0,
): UseResource<T> {
  const [state, setState] = useState<ViewState<T>>({ kind: 'loading' });
  const [nonce, setNonce] = useState(0);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const previousDeps = useRef<unknown[] | null>(null);

  const reload = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) {
      previousDeps.current = null;
      setState({ kind: 'loading' });
      setRefreshError(null);
      return;
    }
    let active = true;
    let inFlight = false;
    const identity = [...deps, enabled];
    const sameIdentity = previousDeps.current !== null &&
      previousDeps.current.length === identity.length &&
      identity.every((value, index) => Object.is(value, previousDeps.current![index]));
    previousDeps.current = identity;
    setState((previous) => sameIdentity && hasData(previous) ? previous : { kind: 'loading' });
    let failures = 0;
    let timer: number | null = null;
    // setInterval 대신 매번 다시 예약한다. 실패가 이어질 때 간격을 늘리려면 다음 실행
    // 시점을 결과를 보고 정해야 한다.
    const schedule = () => {
      if (timer !== null) window.clearTimeout(timer);
      if (!active || refreshIntervalMs <= 0) return;
      timer = window.setTimeout(tick, backoffMs(refreshIntervalMs, failures));
    };
    const settle = (failed: boolean) => {
      inFlight = false;
      failures = failed ? failures + 1 : 0;
      schedule();
    };
    const load = () => {
      if (inFlight) return;
      inFlight = true;
      loader()
      .then((next) => {
        if (active) {
          setState((previous) => retainDuringRefresh(previous, next));
          setRefreshError(next.kind === 'error' ? next.message : null);
        }
        settle(next.kind === 'error');
      })
      .catch((cause: unknown) => {
        if (active) {
          const error = toErrorState<T>(cause);
          setState((previous) => retainDuringRefresh(previous, error));
          setRefreshError('갱신 연결을 확인하고 있습니다. 마지막 확인 값을 표시합니다.');
        }
        settle(true);
      });
    };
    // 숨은 탭에서는 조회하지 않되 예약은 이어 간다. 그러지 않으면 탭을 다시 볼 때까지
    // 갱신이 영구히 멈춘다.
    const tick = () => {
      if (document.visibilityState === 'visible') {
        load();
        return;
      }
      schedule();
    };
    // 사용자가 창으로 돌아온 것은 지금 보고 싶다는 뜻이므로 백오프를 접고 바로 조회한다.
    const refresh = () => {
      if (document.visibilityState !== 'visible') return;
      failures = 0;
      load();
    };
    load();
    if (refreshIntervalMs > 0) window.addEventListener('focus', refresh);
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
      window.removeEventListener('focus', refresh);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled, refreshIntervalMs]);

  return { state, reload, refreshError };
}
