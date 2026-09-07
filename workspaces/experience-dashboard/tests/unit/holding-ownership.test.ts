import assert from 'node:assert/strict';
import test from 'node:test';
import { holdingOwnership } from '../../src/features/overview/ownership';

test('ownership reflects current quantities, manual additions and closed lots', () => {
  assert.deepEqual(holdingOwnership(18, 18), { managed: 18, manual: 0, mismatch: false });
  assert.deepEqual(holdingOwnership(20, 18), { managed: 18, manual: 2, mismatch: false });
  assert.deepEqual(holdingOwnership(1, 0), { managed: 0, manual: 1, mismatch: false });
  assert.deepEqual(holdingOwnership(0, 0), { managed: 0, manual: 0, mismatch: false });
  assert.deepEqual(holdingOwnership(16, 18), { managed: 16, manual: 0, mismatch: true });
});
