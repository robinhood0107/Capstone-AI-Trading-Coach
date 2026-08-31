import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AUTOMATION_PRESETS,
  bpsToPercent,
  percentToBps,
  presetFor,
  slotBudgetKrw,
  validateAutomationPolicy,
} from '../../src/features/automation/policy.ts';

test('automation presets lock the researched 3/5, 5/10 and 8/15 pairs', () => {
  assert.deepEqual(
    AUTOMATION_PRESETS.map((preset) => [preset.presetId, preset.stopLossBps, preset.takeProfitBps]),
    [
      ['conservative', 300, 500],
      ['balanced', 500, 1000],
      ['aggressive', 800, 1500],
    ],
  );
  assert.equal(presetFor(300, 500), 'conservative');
  assert.equal(presetFor(600, 1100), 'custom');
});

test('policy validation enforces money increments and take profit above stop loss', () => {
  assert.deepEqual(
    validateAutomationPolicy({
      capitalLimitKrw: 1_000_000,
      stopLossBps: 500,
      takeProfitBps: 1000,
    }),
    [],
  );
  assert.ok(
    validateAutomationPolicy({
      capitalLimitKrw: 1_000_001,
      stopLossBps: 500,
      takeProfitBps: 500,
    }).length >= 2,
  );
});

test('display conversion and five-position slot budget remain deterministic', () => {
  assert.equal(bpsToPercent(800), 8);
  assert.equal(percentToBps(15), 1500);
  assert.equal(slotBudgetKrw(1_000_000), 200_000);
});
