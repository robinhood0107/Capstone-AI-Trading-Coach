import { NextResponse, type NextRequest } from 'next/server';
import { sessionHash } from './session';
import { isSameOrigin, sessionFromRequest } from './session';

export function json(data: unknown, status = 200): NextResponse {
  return NextResponse.json(data, {
    status,
    headers: { 'Cache-Control': 'no-store, max-age=0' },
  });
}

export function requireSameOrigin(request: NextRequest): NextResponse | null {
  return isSameOrigin(request) ? null : json({ error: 'CROSS_ORIGIN_FORBIDDEN' }, 403);
}

export function requireDemoSession(request: NextRequest) {
  const session = sessionFromRequest(request);
  return session ? { session, hash: sessionFromRequestHash(session.id) } : null;
}

function sessionFromRequestHash(id: string): string {
  // Route handlers receive only this irreversible storage key after validation.
  return sessionHash(id);
}

export async function readBoundedJson(
  request: NextRequest,
  maxBytes: number,
): Promise<{ ok: true; value: unknown } | { ok: false; reason: 'TOO_LARGE' | 'INVALID_JSON' }> {
  const length = Number(request.headers.get('content-length') ?? '0');
  if (length > maxBytes) return { ok: false, reason: 'TOO_LARGE' };
  if (!request.body) return { ok: false, reason: 'INVALID_JSON' };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        return { ok: false, reason: 'TOO_LARGE' };
      }
      chunks.push(value);
    }
  } catch {
    return { ok: false, reason: 'INVALID_JSON' };
  }
  try {
    const bytes = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
    return { ok: true, value: JSON.parse(bytes.toString('utf8')) as unknown };
  } catch {
    return { ok: false, reason: 'INVALID_JSON' };
  }
}
