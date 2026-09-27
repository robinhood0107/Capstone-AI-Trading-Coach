import assert from 'node:assert/strict';
import test from 'node:test';
import { apiFetchBare } from '../../src/shared/api/client.ts';
import { ApiFailure } from '../../src/shared/api/envelope.ts';
import { session } from '../../src/shared/api/session.ts';

function respond(status: number, body: unknown) {
  globalThis.fetch = (async () =>
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })) as typeof fetch;
}

test('bodyless endpoints surface the nested server error code, message and details', async () => {
  const original = globalThis.fetch;
  session.set('token', new Date(Date.now() + 60_000).toISOString(), {
    userId: 'usr_test',
    username: 'tester',
    role: 'USER',
  } as never);
  try {
    // 서버 공통 실패 모양: { success:false, error:{ code, message, details }, requestId }
    respond(409, {
      success: false,
      data: null,
      warnings: [],
      error: { code: 'CONFLICT', message: 'Resource conflict.', details: { reason: 'AUTOMATION_ARMED' } },
      requestId: 'req_server',
    });
    await assert.rejects(apiFetchBare('/api/v1/brokerage/mock/credential', { method: 'PUT', body: {} }), (error) => {
      assert.ok(error instanceof ApiFailure);
      assert.equal(error.code, 'CONFLICT');
      assert.equal(error.requestId, 'req_server');
      assert.deepEqual(error.details, { reason: 'AUTOMATION_ARMED' });
      return true;
    });

    // 봉투가 아닌 5xx 는 형식 불일치가 아니라 서버 오류로 분류한다.
    respond(500, { timestamp: 'x', path: '/y' });
    await assert.rejects(apiFetchBare('/api/v1/brokerage/mock/credential/connect', { method: 'POST' }), (error) => {
      assert.ok(error instanceof ApiFailure);
      assert.equal(error.code, 'INTERNAL_ERROR');
      return true;
    });

    respond(204, undefined);
    assert.equal(await apiFetchBare('/api/v1/brokerage/mock/credential', { method: 'DELETE' }), undefined);
  } finally {
    globalThis.fetch = original;
    session.clear();
  }
});

test('KIS 대사 불가 로컬 종료 상태는 재시도 대신 구체적인 안내를 보여 준다', () => {
  const failure =
    new ApiFailure(
      {
        code: 'ORDER_RECONCILIATION_NOT_APPLICABLE',
        message: 'This locally retired order has no verified KIS result to reconcile.',
      },
      'req_local_retirement',
    );
  assert.equal(failure.userMessage, '이 주문은 로컬 이력 종료 상태라 KIS 대사 대상이 아닙니다. KIS 결과는 확인되지 않았습니다.');
  assert.equal(failure.retryable, false);
});
