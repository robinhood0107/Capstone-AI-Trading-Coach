import assert from 'node:assert/strict';
import test from 'node:test';

import { formatKrw, formatRatio, formatSignedRatio } from '../../src/shared/lib/format.ts';

test('ratios use decimal wire values', () => {
  assert.equal(formatRatio(0.03), '3.00%');
  assert.equal(formatSignedRatio(0.0123), '+1.23%');
});

test('KRW is rendered as a truncated integer', () => {
  assert.equal(formatKrw(1234567.89), '1,234,567원');
});
