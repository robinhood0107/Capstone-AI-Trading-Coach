'use client';

import { useEffect, useState } from 'react';
import type { LoginUserResponse } from './wire';

/**
 * S8 보안 gate: access token은 URL·localStorage·IndexedDB·로그에 저장하지 않고
 * 메모리에서만 보유한다. 새로고침하면 재로그인이 필요한 것이 의도된 동작이다.
 */
interface SessionState {
  token: string | null;
  expiresAt: string | null;
  user: LoginUserResponse | null;
}

const state: SessionState = { token: null, expiresAt: null, user: null };
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export const session = {
  set(token: string, expiresAt: string, user: LoginUserResponse): void {
    state.token = token;
    state.expiresAt = expiresAt;
    state.user = user;
    emit();
  },
  clear(): void {
    state.token = null;
    state.expiresAt = null;
    state.user = null;
    emit();
  },
  token(): string | null {
    return state.token;
  },
  user(): LoginUserResponse | null {
    return state.user;
  },
  expiresAt(): string | null {
    return state.expiresAt;
  },
  isAuthenticated(): boolean {
    return state.token !== null;
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

export function useSession() {
  const [snapshot, setSnapshot] = useState(() => ({
    authenticated: session.isAuthenticated(),
    user: session.user(),
  }));

  useEffect(
    () =>
      session.subscribe(() => {
        setSnapshot({ authenticated: session.isAuthenticated(), user: session.user() });
      }),
    [],
  );

  return snapshot;
}

/** 외부 링크는 https만 열고 호출부에서 noopener noreferrer를 강제한다. */
export function safeExternalUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}
