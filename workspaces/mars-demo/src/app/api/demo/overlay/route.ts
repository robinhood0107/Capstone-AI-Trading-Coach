import { NextRequest } from 'next/server';
import { json, readBoundedJson, requireDemoSession, requireSameOrigin } from '@demo/server/http';
import { applyOverlayAction, OverlayError, readSessionOverlay } from '@demo/server/overlay';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const identity = requireDemoSession(request);
  if (!identity) return json({ error: 'DEMO_SESSION_REQUIRED' }, 401);
  return json({ overlay: readSessionOverlay(identity.hash) });
}

export async function POST(request: NextRequest) {
  const originError = requireSameOrigin(request);
  if (originError) return originError;
  const identity = requireDemoSession(request);
  if (!identity) return json({ error: 'DEMO_SESSION_REQUIRED' }, 401);
  const body = await readBoundedJson(request, 16_384);
  if (!body.ok) return json({ error: body.reason }, body.reason === 'TOO_LARGE' ? 413 : 400);
  if (!body.value || typeof body.value !== 'object' || Array.isArray(body.value)) return json({ error: 'INVALID_REQUEST' }, 400);
  try {
    const result = applyOverlayAction(identity.hash, body.value as Record<string, unknown> & { action?: unknown }, new Date());
    return json(result);
  } catch (error) {
    if (error instanceof OverlayError) return json({ error: error.code }, error.status);
    return json({ error: 'DEMO_OVERLAY_UNAVAILABLE' }, 503);
  }
}
