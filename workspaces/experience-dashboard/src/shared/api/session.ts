'use client';

import { useEffect, useState } from 'react';
import type { LoginUserResponse } from './wire';

// Loopback demo tokens persist only for the lifetime of the current tab.
interface SessionState {
  token: string | null;
  expiresAt: string | null;
  user: LoginUserResponse | null;
}

const STORAGE_KEY = 'capstone.session.v1';

function store(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function restore(): SessionState {
  const empty: SessionState = { token: null, expiresAt: null, user: null };
  const raw = (() => {
    try {
      return store()?.getItem(STORAGE_KEY) ?? null;
    } catch {
      return null;
    }
  })();
  if (!raw) return empty;
  try {
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
    const storage = store();
    if (!storage) return;
    if (next.token === null) storage.removeItem(STORAGE_KEY);
    else storage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {}
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
  // Restore after mount so server and initial client markup remain equal.
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
