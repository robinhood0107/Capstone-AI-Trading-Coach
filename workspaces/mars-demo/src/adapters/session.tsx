'use client';

import { createContext, useContext, type ReactNode } from 'react';
import type { LoginUserResponse } from '@/shared/api/wire';

const AuthenticatedContext = createContext(false);

export function DemoAuthProvider({
  authenticated,
  children,
}: {
  authenticated: boolean;
  children: ReactNode;
}) {
  return <AuthenticatedContext.Provider value={authenticated}>{children}</AuthenticatedContext.Provider>;
}

export function useSession() {
  const authenticated = useContext(AuthenticatedContext);
  const user: LoginUserResponse | null = authenticated
    ? { userId: 'visitor-session', username: '투자자', role: 'USER' }
    : null;
  return { authenticated, restoring: false, restoreError: false, user };
}

export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null;
  } catch {
    return null;
  }
}

export const session = {
  async ensureToken(): Promise<string | null> {
    return 'visitor-session';
  },
  token(): string | null {
    return null;
  },
  user(): LoginUserResponse | null {
    return { userId: 'visitor-session', username: '투자자', role: 'USER' };
  },
  isAuthenticated(): boolean {
    return true;
  },
  restoring(): boolean {
    return false;
  },
  restoreError(): boolean {
    return false;
  },
  restoreOnMount(): void {},
  set(token: string, expiresAt: string, user: LoginUserResponse): void {
    void token;
    void expiresAt;
    void user;
  },
  async refresh(): Promise<string | null> {
    return null;
  },
  clear(): void {
    if (typeof window === 'undefined') return;
    void fetch('/api/demo/logout', { method: 'POST', credentials: 'same-origin', cache: 'no-store' })
      .catch(() => undefined)
      .finally(() => window.location.assign('/'));
  },
};
