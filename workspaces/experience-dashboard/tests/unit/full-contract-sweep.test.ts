import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { ApiFailure } from '../../src/shared/api/envelope.ts';
import { disconnectOutcome } from '../../src/shared/api/endpoints.ts';
import { nullableRead } from '../../src/shared/api/nullableRead.ts';

test('DELETE credential reads DISCONNECTING from the server envelope and from a bare body', () => {
  // ResponseEnvelopeAdvice 가 컨트롤러의 { state } 를 감싼 실제 모양.
  assert.deepEqual(
    disconnectOutcome({ success: true, requestId: 'req_x', data: { state: 'DISCONNECTING' }, warnings: [], error: null }),
    { state: 'DISCONNECTING' },
  );
  assert.deepEqual(disconnectOutcome({ state: 'DISCONNECTING' }), { state: 'DISCONNECTING' });
  assert.equal(disconnectOutcome(undefined), undefined); // 204
  assert.equal(disconnectOutcome({ success: true, data: { state: 'REMOVED' } }), undefined);
});

test('nullable v4 reads keep "empty" apart from "failed"', async () => {
  const empty = Promise.reject(new ApiFailure({ code: 'NOT_FOUND', message: 'x' }, 'r'));
  assert.deepEqual(await nullableRead(empty), { data: null });
  const down = Promise.reject(new ApiFailure({ code: 'INTERNAL_ERROR', message: 'x' }, 'r'));
  assert.equal(await nullableRead(down), null);
  const value = Promise.resolve({ data: { v: 1 }, warnings: [], requestId: 'r' });
  assert.deepEqual((await nullableRead(value))?.data, { v: 1 });
});

test('login accepts the server password length while signup keeps its 64 character policy', () => {
  const source = readFileSync(new URL('../../src/shared/ui/LoginCard.tsx', import.meta.url), 'utf8');
  assert.match(source, /maxLength=\{signingUp \? 64 : 1024\}/);
});

test('dashboard client exposes no call the FULL gate closes', () => {
  const source = readFileSync(new URL('../../src/shared/api/endpoints.ts', import.meta.url), 'utf8');
  for (const closed of ["'/api/v2/automation/policy'", "'/api/v2/automation/arm'", '/api/v2/automation/runs', "'/api/v1/rag/ask'"]) {
    assert.ok(!source.includes(closed), closed);
  }
});
