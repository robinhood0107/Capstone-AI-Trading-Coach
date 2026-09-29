import { NextRequest, NextResponse } from 'next/server';
import { DEMO_SESSION_COOKIE, DEMO_SESSION_TTL_SECONDS, issueDemoSession } from '@demo/server/session';
import { json, requireSameOrigin } from '@demo/server/http';
import { reserveSessionIssue } from '@demo/server/store';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  if (!reserveSessionIssue(new Date(), 30)) return json({ error: 'SESSION_ISSUE_RATE_LIMIT' }, 429);
  try {
    const { token, session } = issueDemoSession();
    const response = NextResponse.json({ ok: true, expiresAt: new Date(session.expiresAt * 1_000).toISOString() }, {
      headers: { 'Cache-Control': 'no-store, max-age=0' },
    });
    response.cookies.set(DEMO_SESSION_COOKIE, token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      path: '/',
      maxAge: DEMO_SESSION_TTL_SECONDS,
      expires: new Date(session.expiresAt * 1_000),
    });
    return response;
  } catch {
    return json({ error: 'DEMO_SESSION_UNAVAILABLE' }, 503);
  }
}
