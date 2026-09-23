'use client';

import { useEffect, useState } from 'react';
import type { LoginUserResponse } from './wire';

// Bearer tokens exist only in this JavaScript module and disappear on reload.
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
    return state.expiresAt && Date.parse(state.expiresAt) > Date.now() ? state.token : null;
  },
  user(): LoginUserResponse | null {
    return state.user;
  },
  expiresAt(): string | null {
    return state.expiresAt;
  },
  isAuthenticated(): boolean {
    return state.token !== null && state.expiresAt !== null && Date.parse(state.expiresAt) > Date.now();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

export function useSession() {
  // Read module memory after mount so server and initial client markup remain equal.
  const [snapshot, setSnapshot] = useState({
    authenticated: false,
    user: null as LoginUserResponse | null,
  });

  useEffect(() => {
    const sync = () =>
      setSnapshot({ authenticated: session.isAuthenticated(), user: session.user() });
    sync();
    return session.subscribe(sync);
  }, []);

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
