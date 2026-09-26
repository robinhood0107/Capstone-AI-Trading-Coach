import assert from 'node:assert/strict';
import test from 'node:test';
import { NextRequest } from 'next/server';
import { middleware } from '../../src/middleware';

function request(path: string, method = 'GET') {
  return new NextRequest(`http://localhost${path}`, { method });
}

test('DEMO exposes only its public page and bounded ask before the API rewrite', () => {
  const previous = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  try {
    process.env.NEXT_PUBLIC_MARS_PRODUCT = 'demo';
    assert.equal(middleware(request('/')).status, 200);
    assert.equal(middleware(request('/api/v1/demo/agent/ask', 'POST')).status, 200);
    for (const path of ['/settings', '/automation', '/rag', '/api/v1/brokerage/mock/credential']) {
      assert.equal(middleware(request(path)).status, 404, path);
    }
    assert.equal(middleware(request('/api/v1/auth/login', 'POST')).status, 404);
    assert.equal(middleware(request('/api/v1/demo/agent/ask')).status, 404);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previous;
  }
});

test('FULL enables account login while keeping DEMO APIs private; LOCAL keeps its private login', () => {
  const previous = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  try {
    process.env.NEXT_PUBLIC_MARS_PRODUCT = 'full';
    assert.equal(middleware(request('/api/v1/auth/login', 'POST')).status, 200);
    assert.equal(middleware(request('/api/v1/auth/signup', 'POST')).status, 200);
    assert.equal(middleware(request('/api/v1/auth/identities', 'GET')).status, 200);
    assert.equal(middleware(request('/api/v1/demo/agent/ask', 'POST')).status, 404);
    assert.equal(middleware(request('/api/v1/auth/oidc/start/google')).status, 200);
    assert.equal(middleware(request('/api/v1/auth/oidc/start/kakao')).status, 200);
    delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    assert.equal(middleware(request('/api/v1/auth/login', 'POST')).status, 200);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previous;
  }
});
