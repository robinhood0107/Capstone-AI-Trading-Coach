import assert from 'node:assert/strict';
import test from 'node:test';
import { newRequestId } from '../../src/shared/api/client.ts';

test('dashboard request IDs satisfy the canonical HTTP and RAG ledger contract', () => {
  const values = Array.from({ length: 32 }, () => newRequestId());

  assert.equal(new Set(values).size, values.length);
  for (const value of values) {
    assert.match(value, /^req_[A-Za-z0-9_-]{12,96}$/);
    assert.match(value, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/);
  }
});
