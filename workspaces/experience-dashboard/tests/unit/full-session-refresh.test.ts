import assert from 'node:assert/strict';
import test from 'node:test';

test('FULL restores one access token per concurrent request and clears on invalid refresh', async () => {
  const previousProduct = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  process.env.NEXT_PUBLIC_MARS_PRODUCT = 'full';
  Object.assign(globalThis, { window: {} });
  let calls = 0;
  globalThis.fetch = async (_input, init) => {
    calls++;
    assert.equal(init?.credentials, 'same-origin');
    return new Response(JSON.stringify({
      success: true,
      requestId: 'req_0123456789abcdef0123456789abcdef',
      error: null,
      warnings: [],
      data: {
        accessToken: 'fixture-access-token',
        tokenType: 'Bearer',
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        user: { userId: 'usr_fixture_user', username: 'fixture', role: 'USER' },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const { session } = await import('../../src/shared/api/session.ts');
    const [first, second] = await Promise.all([session.ensureToken(), session.ensureToken()]);
    assert.equal(first, 'fixture-access-token');
    assert.equal(second, 'fixture-access-token');
    assert.equal(calls, 1);
    assert.equal(session.isAuthenticated(), true);

    session.clear();
    globalThis.fetch = async () => new Response(null, { status: 401 });
    assert.equal(await session.ensureToken(), null);
    assert.equal(session.isAuthenticated(), false);
  } finally {
    globalThis.fetch = previousFetch;
    Object.assign(globalThis, { window: previousWindow });
    if (previousProduct === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previousProduct;
  }
});
