'use client';

import { useEffect, useState } from 'react';
import { isEnvelope } from './envelope';
import type { LoginResponse, LoginUserResponse } from './wire';

// FULL access tokens stay in memory. A same-origin HttpOnly cookie restores them after reload.
// The private LOCAL product keeps its existing tab-scoped session behavior.
interface SessionState {
  token: string | null;
  expiresAt: string | null;
  user: LoginUserResponse | null;
}

const LOCAL_SESSION_KEY = 'capstone.session.v1';

function localStore(): Storage | null {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'full') return null;
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
let restoreStarted = false;
let restorePending = process.env.NEXT_PUBLIC_MARS_PRODUCT === 'full' && process.env.NEXT_PUBLIC_API_MODE !== 'mock';
let restoreError = false;
let refreshPromise: Promise<string | null> | null = null;

function emit(): void {
  listeners.forEach((listener) => listener());
}

export const session = {
  set(token: string, expiresAt: string, user: LoginUserResponse): void {
    state.token = token;
    state.expiresAt = expiresAt;
    state.user = user;
    persist(state);
    restorePending = false;
    restoreError = false;
    emit();
  },
  clear(): void {
    state.token = null;
    state.expiresAt = null;
    state.user = null;
    persist(state);
    restorePending = false;
    restoreError = false;
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
  restoring(): boolean { return restorePending; },
  restoreError(): boolean { return restoreError; },
  async refresh(): Promise<string | null> {
    if (process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full' || process.env.NEXT_PUBLIC_API_MODE === 'mock' || typeof window === 'undefined') return null;
    if (refreshPromise) return refreshPromise;
    restorePending = true;
    restoreError = false;
    emit();
    refreshPromise = (async () => {
      try {
        const response = await fetch('/api/v1/auth/refresh', {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: AbortSignal.timeout(15_000),
        });
        if (response.status === 401) {
          session.clear();
          return null;
        }
        if (!response.ok) throw new Error('Session refresh unavailable');
        const payload: unknown = await response.json();
        if (!isEnvelope(payload) || !payload.success || !payload.data) {
          throw new Error('Session refresh response invalid');
        }
        const data = payload.data as Partial<LoginResponse>;
        if (typeof data.accessToken !== 'string' || typeof data.expiresAt !== 'string' ||
            !data.user || typeof data.user.userId !== 'string' ||
            (data.user.role !== 'USER' && data.user.role !== 'ADMIN')) {
          throw new Error('Session refresh response invalid');
        }
        session.set(data.accessToken, data.expiresAt, data.user as LoginUserResponse);
        return data.accessToken;
      } catch (error) {
        restorePending = false;
        restoreError = true;
        emit();
        throw error;
      } finally {
        refreshPromise = null;
      }
    })();
    return refreshPromise;
  },
  async ensureToken(): Promise<string | null> {
    const current = session.token();
    if (current) return current;
    if (process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full') return null;
    return session.refresh();
  },
  restoreOnMount(): void {
    if (restoreStarted || process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full' || process.env.NEXT_PUBLIC_API_MODE === 'mock') return;
    restoreStarted = true;
    void session.refresh().catch(() => { /* AppShell shows a retry control. */ });
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
    restoring: process.env.NEXT_PUBLIC_MARS_PRODUCT === 'full' && process.env.NEXT_PUBLIC_API_MODE !== 'mock',
    restoreError: false,
  });

  useEffect(() => {
    const sync = () =>
      setSnapshot({ authenticated: session.isAuthenticated(), user: session.user(),
        restoring: session.restoring(), restoreError: session.restoreError() });
    const unsubscribe = session.subscribe(sync);
    sync();
    return unsubscribe;
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
