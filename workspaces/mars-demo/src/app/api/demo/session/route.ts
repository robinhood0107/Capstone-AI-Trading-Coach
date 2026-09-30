import { NextRequest, NextResponse } from 'next/server';
import { DEMO_SESSION_COOKIE, DEMO_SESSION_TTL_SECONDS, issueDemoSession, sessionHash } from '@demo/server/session';
import { json, requireSameOrigin } from '@demo/server/http';
import { defaultOverlay, reserveSessionIssue, saveOverlay } from '@demo/server/store';
import { initialJournalEntries } from '@demo/server/seed-journals';
import { showcaseMetadata } from '@demo/server/ledger';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  if (!reserveSessionIssue(new Date(), 30)) return json({ error: 'SESSION_ISSUE_RATE_LIMIT' }, 429);
  try {
    const { token, session } = issueDemoSession();
    const overlay = defaultOverlay();
    overlay.autoArmed = true;
    const firstSession = showcaseMetadata().sourceRange.start;
    overlay.automationPolicy = {
      version: 1,
      capitalLimitKrw: 9_000_000,
      capitalPolicyVersion: 1,
      reinvestRealizedPnl: true,
      capitalPolicyEffectiveFromSession: firstSession,
      capitalPolicyTransitionStartedAt: `${firstSession}T08:50:00+09:00`,
    };
    const hash = sessionHash(session.id);
    overlay.journalEntries = initialJournalEntries(hash);
    overlay.journalSeedVersion = 'v2';
    saveOverlay(hash, overlay);
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
