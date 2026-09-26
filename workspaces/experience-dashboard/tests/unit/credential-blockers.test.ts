import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiFailure } from '../../src/shared/api/envelope';
import {
  CREDENTIAL_BLOCKER_MESSAGE,
  credentialChangeBlocker,
} from '../../src/features/brokerage/credentialBlockers';

function conflict(details?: Record<string, unknown>) {
  return new ApiFailure({ code: 'CONFLICT', message: 'Resource conflict.', details }, 'req_test');
}

test('an armed owner sees why the key cannot change instead of a generic conflict', () => {
  const blocker = credentialChangeBlocker(conflict({ reason: 'AUTOMATION_ARMED' }));
  assert.equal(blocker, 'AUTOMATION_ARMED');
  assert.match(CREDENTIAL_BLOCKER_MESSAGE[blocker!], /자동매매를 먼저 해제/);
  assert.equal(credentialChangeBlocker(conflict({ reason: 'PENDING_EXECUTION' })), 'PENDING_EXECUTION');
  assert.equal(credentialChangeBlocker(conflict({ reason: 'PENDING_RECONCILIATION' })), 'PENDING_RECONCILIATION');
});

test('unknown reasons, other codes and plain errors keep the shared error text', () => {
  assert.equal(credentialChangeBlocker(conflict()), null);
  assert.equal(credentialChangeBlocker(conflict({ reason: 'toString' })), null);
  assert.equal(credentialChangeBlocker(conflict({ reason: 'SOMETHING_NEW' })), null);
  assert.equal(
    credentialChangeBlocker(new ApiFailure({ code: 'VALIDATION_ERROR', message: 'x', details: { reason: 'AUTOMATION_ARMED' } }, 'r')),
    null,
  );
  assert.equal(credentialChangeBlocker(new Error('boom')), null);
});
