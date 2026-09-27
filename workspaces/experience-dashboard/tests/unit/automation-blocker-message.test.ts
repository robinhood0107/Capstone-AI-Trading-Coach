import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiFailure } from '../../src/shared/api/envelope';
import { automationBlockerMessage } from '../../src/features/automation/policy';

function conflict(details?: Record<string, unknown>) {
  return new ApiFailure({ code: 'CONFLICT', message: 'Resource conflict.', details }, 'req_test');
}

test('arm rejected for the AI provider says why and how to proceed instead of a generic conflict', () => {
  const text = automationBlockerMessage(conflict({ blocker: 'AI_PROVIDER_NOT_READY' }));
  assert.ok(text);
  assert.match(text, /AI 검토 제공자가 준비되지 않았습니다/);
  // 사용자가 스스로 풀 수 있는 두 길(자기 키 등록, AI 검토 끄기)을 함께 말한다.
  assert.match(text, /Vertex 서비스 계정을 등록/);
  assert.match(text, /AI 검토를 끄면/);
  assert.doesNotMatch(text, /다른 변경과 충돌/);
});

test('every automation write blocker the server can send has a Korean label', () => {
  for (const blocker of [
    'BLOCKED_INCOMPLETE_RISK_BALANCE',
    'LEGACY_POSITION_PRESENT',
    'MARKET_DATA_CATCHUP_REQUIRED',
    'AI_PROVIDER_NOT_READY',
    'AUTOMATION_CAPACITY_REACHED',
    'ACCOUNT_NOT_CONFIGURED',
  ]) {
    const text = automationBlockerMessage(conflict({ blocker }));
    assert.ok(text, blocker);
    assert.match(text, /[가-힣]/);
  }
});

test('plain version conflicts and unknown values keep the caller text', () => {
  assert.equal(automationBlockerMessage(conflict()), null);
  assert.equal(automationBlockerMessage(conflict({ blocker: 'toString' })), null);
  assert.equal(automationBlockerMessage(conflict({ blocker: 'SOMETHING_NEW' })), null);
  assert.equal(
    automationBlockerMessage(
      new ApiFailure({ code: 'VALIDATION_ERROR', message: 'x', details: { blocker: 'AI_PROVIDER_NOT_READY' } }, 'r'),
    ),
    null,
  );
  assert.equal(automationBlockerMessage(new Error('boom')), null);
  assert.equal(automationBlockerMessage(null), null);
});
