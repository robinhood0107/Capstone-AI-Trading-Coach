import assert from 'node:assert/strict';
import test from 'node:test';
import { retainDuringRefresh } from '../../src/shared/lib/useResource';
import { formatKrw } from '../../src/shared/lib/format';
import type { ViewState } from '../../src/shared/lib/viewState';

test('transient refresh failure keeps the last data but never keeps revoked access', () => {
  const prior: ViewState<number> = { kind: 'ready', data: 1740000, asOf: '2026-09-07T02:25:00Z' };
  const failure: ViewState<number> = { kind: 'error', code: 'BROKERAGE_UNAVAILABLE', message: 'retry', retryable: true, requestId: null };
  assert.equal(retainDuringRefresh(prior, failure), prior);
  const revoked = { ...failure, code: 'FORBIDDEN', retryable: false };
  assert.equal(retainDuringRefresh(prior, revoked), revoked);
  const next: ViewState<number> = { kind: 'ready', data: 1750000, asOf: null };
  assert.equal(retainDuringRefresh(prior, next), next);
  assert.equal(retainDuringRefresh({ kind: 'loading' }, failure), failure);
});

test('an unknown server error code keeps the last view instead of blanking it', () => {
  const prior: ViewState<number> = { kind: 'ready', data: 1740000, asOf: '2026-09-07T02:25:00Z' };
  // 서버가 앞으로 추가할 코드는 RETRYABLE 집합에 없어 retryable=false 로 들어온다.
  // 그 하나 때문에 화면 전체가 비워지면 시연 중 어떤 신규 코드도 사고가 된다.
  const unknown: ViewState<number> = {
    kind: 'error',
    code: 'SOME_NEW_SERVER_CODE',
    message: 'unmapped',
    retryable: false,
    requestId: 'req_0123456789abcdef',
  };
  assert.equal(retainDuringRefresh(prior, unknown), prior);
  // 저장 계약 위반이나 충돌도 값을 지울 이유가 없다.
  for (const code of ['CONFLICT', 'NOT_FOUND', 'VALIDATION_ERROR', 'RESPONSE_CONTRACT_MISMATCH']) {
    assert.equal(retainDuringRefresh(prior, { ...unknown, code }), prior);
  }
  // 권한 회수만 값을 지운다.
  for (const code of ['UNAUTHORIZED', 'FORBIDDEN']) {
    const revoked: ViewState<number> = { ...unknown, code };
    assert.equal(retainDuringRefresh(prior, revoked), revoked);
  }
});

test('missing prices never render as NaN or an invented zero', () => {
  assert.equal(formatKrw(Number.NaN), '—');
  assert.equal(formatKrw(Number.POSITIVE_INFINITY), '—');
  assert.equal(formatKrw(1740000), '1,740,000원');
});
