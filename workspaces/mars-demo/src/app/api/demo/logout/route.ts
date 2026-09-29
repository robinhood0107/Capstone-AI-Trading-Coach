import { NextRequest, NextResponse } from 'next/server';
import { DEMO_SESSION_COOKIE } from '@demo/server/session';
import { json, requireDemoSession, requireSameOrigin } from '@demo/server/http';

export const runtime = 'nodejs';

export async function POST(request: NextRequest) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  if (!requireDemoSession(request)) return json({ error: 'DEMO_SESSION_REQUIRED' }, 401);
  const response = NextResponse.json({ ok: true }, { headers: { 'Cache-Control': 'no-store' } });
  response.cookies.set(DEMO_SESSION_COOKIE, '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
    expires: new Date(0),
  });
  return response;
}
