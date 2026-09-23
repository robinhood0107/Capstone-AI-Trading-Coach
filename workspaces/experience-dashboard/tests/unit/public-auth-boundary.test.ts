import assert from 'node:assert/strict';
import test from 'node:test';
import { middleware } from '../../src/middleware';

test('FULL and DEMO reject the password route before the API rewrite; LOCAL retains its private login', () => {
  const previous = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  try {
    process.env.NEXT_PUBLIC_MARS_PRODUCT = 'full';
    assert.equal(middleware().status, 404);
    process.env.NEXT_PUBLIC_MARS_PRODUCT = 'demo';
    assert.equal(middleware().status, 404);
    delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    assert.equal(middleware().status, 200);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previous;
  }
});
