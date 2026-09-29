import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { NextRequest } from 'next/server';

export const DEMO_SESSION_COOKIE = 'mars_demo_visitor';
export const DEMO_SESSION_TTL_SECONDS = 8 * 60 * 60;

function signingKey(): Buffer {
  const secretPath = process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE;
  if (secretPath) {
    const key = readFileSync(secretPath);
    if (key.byteLength < 32) throw new Error('DEMO session signing key must be at least 32 bytes');
    return key;
  }
  if (process.env.NODE_ENV === 'development') {
    return Buffer.from('local-development-only-mars-demo-session-key');
  }
  throw new Error('DEMO session signing key is unavailable');
}

function signature(payload: string): string {
  return createHmac('sha256', signingKey()).update(payload).digest('base64url');
}

export interface DemoSession {
  id: string;
  expiresAt: number;
}

export function issueDemoSession(now = Date.now()): { token: string; session: DemoSession } {
  const id = randomBytes(32).toString('base64url');
  const expiresAt = Math.floor(now / 1000) + DEMO_SESSION_TTL_SECONDS;
  const payload = `${id}.${expiresAt}`;
  return { token: `${payload}.${signature(payload)}`, session: { id, expiresAt } };
}

export function verifyDemoSession(token: string | undefined, now = Date.now()): DemoSession | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 3 || !/^[A-Za-z0-9_-]{40,50}$/.test(parts[0]) || !/^\d{10}$/.test(parts[1])) return null;
  const expiresAt = Number(parts[1]);
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= Math.floor(now / 1000)) return null;
  const payload = `${parts[0]}.${parts[1]}`;
  let provided: Buffer;
  let expected: Buffer;
  try {
    provided = Buffer.from(parts[2], 'base64url');
    expected = Buffer.from(signature(payload), 'base64url');
  } catch {
    return null;
  }
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) return null;
  return { id: parts[0], expiresAt };
}

export function sessionHash(sessionId: string): string {
  return createHash('sha256').update(sessionId).digest('hex');
}

export function sessionFromRequest(request: NextRequest, now = Date.now()): DemoSession | null {
  return verifyDemoSession(request.cookies.get(DEMO_SESSION_COOKIE)?.value, now);
}

export function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get('origin');
  if (!origin) return request.headers.get('sec-fetch-site') === 'same-origin';
  try {
    const parsed = new URL(origin);
    const host = request.headers.get('host');
    const forwardedProtocol = request.headers.get('x-forwarded-proto')?.split(',')[0]?.trim();
    const protocol = forwardedProtocol ? `${forwardedProtocol.replace(/:$/, '')}:` : request.nextUrl.protocol;
    return Boolean(host) && parsed.host.toLowerCase() === host?.toLowerCase() && parsed.protocol === protocol;
  } catch {
    return false;
  }
}
