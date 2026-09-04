import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const endpointsUrl = new URL('../../src/shared/api/endpoints.ts', import.meta.url);
const overviewUrl = new URL('../../src/features/overview/OverviewView.tsx', import.meta.url);
const automationUrl = new URL('../../src/features/automation/AutomationView.tsx', import.meta.url);

test('automation policy UI uses the approved v2 read, policy, and arm operations', async () => {
  const endpoints = await readFile(endpointsUrl, 'utf8');
  for (const path of [
    '/api/v2/automation/status',
    '/api/v2/automation/policy',
    '/api/v2/automation/arm',
    '/api/v2/automation/runs',
    '/api/v2/automation/positions',
  ]) {
    assert.match(endpoints, new RegExp(path.replaceAll('/', '\\/')));
  }
  assert.match(endpoints, /newIdempotencyKey\('automation-policy'\)/);
  assert.match(endpoints, /newIdempotencyKey\('automation-arm-v2'\)/);
  const automation = await readFile(automationUrl, 'utf8');
  assert.doesNotMatch(automation, /신규 주문 중지/);
  assert.doesNotMatch(automation, /api\.disarmAutomation/);
});

test('overview reads actual automation status instead of inferring it from the kill switch', async () => {
  const overview = await readFile(overviewUrl, 'utf8');
  assert.match(overview, /api\.automationStatusV2\(\)/);
  assert.doesNotMatch(overview, /risk\.killSwitchActive \? '정지됨' : '작동 중'/);
});

test('blocked risk balance is visible and prevents the UI arm call', async () => {
  const automation = await readFile(automationUrl, 'utf8');
  assert.match(automation, /data\.status\.blockers\.length > 0/);
  assert.match(automation, /!data\.status\.canArm/);
  assert.match(automation, /api\.armAutomationV2/);
  assert.match(automation, /KIS 모의계좌 전용입니다\. 실제 계좌 주문은 실행하지 않습니다\./);
});
