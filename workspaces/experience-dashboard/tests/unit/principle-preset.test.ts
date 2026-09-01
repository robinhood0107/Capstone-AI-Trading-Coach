import assert from 'node:assert/strict';
import test from 'node:test';
import { matchesPreset } from '../../src/features/principles/preset.ts';
import type { PrincipleRule } from '../../src/shared/api/wire.ts';

const first: PrincipleRule = {
  ruleId: 'daily_loss_guard',
  ruleType: 'THRESHOLD',
  metric: 'DAILY_PNL_RATE',
  operator: '>=',
  threshold: -0.03,
  severity: 'BLOCK',
  enabled: true,
  evidenceRequirement: 'REQUIRED',
};
const second: PrincipleRule = {
  ruleId: 'max_daily_orders',
  ruleType: 'THRESHOLD',
  metric: 'DAILY_ORDER_COUNT',
  operator: '<=',
  threshold: 3,
  severity: 'BLOCK',
  enabled: true,
  evidenceRequirement: 'REQUIRED',
};

test('preset matching compares the complete rule set without depending on order', () => {
  const reorderedFields: PrincipleRule = {
    evidenceRequirement: first.evidenceRequirement,
    enabled: first.enabled,
    severity: first.severity,
    threshold: first.threshold,
    operator: first.operator,
    metric: first.metric,
    ruleType: first.ruleType,
    ruleId: first.ruleId,
  };

  assert.equal(matchesPreset([reorderedFields, second], [second, first]), true);
  assert.equal(matchesPreset([first], [first, second]), false);
  assert.equal(matchesPreset([{ ...first, threshold: -0.05 }, second], [first, second]), false);
  assert.equal(matchesPreset([{ ...first, enabled: false }, second], [first, second]), false);
});
