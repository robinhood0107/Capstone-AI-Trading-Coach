'use client';

import { useEffect, useState } from 'react';
import type { LoginUserResponse } from './wire';

// FULL bearer tokens exist only in this JavaScript module and disappear on reload.
// The private LOCAL product keeps its existing tab-scoped session behavior.
interface SessionState {
  token: string | null;
  expiresAt: string | null;
  user: LoginUserResponse | null;
}

const LOCAL_SESSION_KEY = 'capstone.session.v1';

function localStore(): Storage | null {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'full' || process.env.NEXT_PUBLIC_MARS_PRODUCT === 'demo') return null;
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function restore(): SessionState {
  const empty: SessionState = { token: null, expiresAt: null, user: null };
  try {
    const raw = localStore()?.getItem(LOCAL_SESSION_KEY);
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as SessionState;
    if (typeof parsed.token !== 'string' || typeof parsed.expiresAt !== 'string') return empty;
    if (Date.parse(parsed.expiresAt) <= Date.now()) return empty;
    return { token: parsed.token, expiresAt: parsed.expiresAt, user: parsed.user ?? null };
  } catch {
    return empty;
  }
}

function persist(next: SessionState): void {
  try {
    const storage = localStore();
    if (!storage) return;
    if (next.token === null) storage.removeItem(LOCAL_SESSION_KEY);
    else storage.setItem(LOCAL_SESSION_KEY, JSON.stringify(next));
  } catch {
    // Private LOCAL storage can be unavailable in restricted browsers.
  }
}

const state: SessionState = restore();
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

export const session = {
  set(token: string, expiresAt: string, user: LoginUserResponse): void {
    state.token = token;
    state.expiresAt = expiresAt;
    state.user = user;
    persist(state);
    emit();
  },
  clear(): void {
    state.token = null;
    state.expiresAt = null;
    state.user = null;
    persist(state);
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
