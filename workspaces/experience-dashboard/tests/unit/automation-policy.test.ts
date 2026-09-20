import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AUTOMATION_PRESETS,
  bpsToPercent,
  percentToBps,
  presetFor,
  slotBudgetKrw,
  validateAutomationPolicy,
  validateAutomationPolicyV3,
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
  assert.equal(slotBudgetKrw(1_000_000, 5), 200_000);
  // 상한은 사용자가 고른다. 5 를 박아 두면 화면이 실제의 두세 배를 말한다.
  assert.equal(slotBudgetKrw(1_000_000, 10), 100_000);
  assert.equal(slotBudgetKrw(1_000_000, 20), 50_000);
});

test('동시 보유 상한과 거래당 위험은 서버와 같은 범위로 막는다', () => {
  const base = { atrPeriod: 22, atrMultiplierMilli: 3000, maxHoldingSessions: 60 };

  assert.deepEqual(
    validateAutomationPolicyV3({ ...base, maxOpenPositions: 10, riskPerTradeBps: 100 }),
    [],
  );
  assert.ok(
    validateAutomationPolicyV3({ ...base, maxOpenPositions: 0, riskPerTradeBps: 100 }).some((m) =>
      m.includes('동시 보유 상한'),
    ),
  );
  assert.ok(
    validateAutomationPolicyV3({ ...base, maxOpenPositions: 21, riskPerTradeBps: 100 }).some((m) =>
      m.includes('동시 보유 상한'),
    ),
  );
  assert.ok(
    validateAutomationPolicyV3({ ...base, maxOpenPositions: 10, riskPerTradeBps: 5 }).some((m) =>
      m.includes('거래당 위험'),
    ),
  );
  assert.ok(
    validateAutomationPolicyV3({ ...base, maxOpenPositions: 10, riskPerTradeBps: 400 }).some((m) =>
      m.includes('거래당 위험'),
    ),
  );
});
