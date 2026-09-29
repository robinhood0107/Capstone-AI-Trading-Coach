import { NextRequest } from 'next/server';
import { readAgentConfig } from '@demo/server/config';
import { json, requireDemoSession } from '@demo/server/http';
import { buildDemoState } from '@demo/server/app-state';
import { getOverlay } from '@demo/server/store';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const identity = requireDemoSession(request);
  if (!identity) return json({ error: 'DEMO_SESSION_REQUIRED' }, 401);
  try {
    const config = readAgentConfig();
    const state = buildDemoState(identity.hash, getOverlay(identity.hash), config, new Date());
    return json(state);
  } catch {
    return json({ error: 'DEMO_STATE_UNAVAILABLE' }, 503);
  }
}
