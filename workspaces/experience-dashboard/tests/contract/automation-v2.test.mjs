import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const endpointsUrl = new URL('../../src/shared/api/endpoints.ts', import.meta.url);
const overviewUrl = new URL('../../src/features/overview/OverviewView.tsx', import.meta.url);
const automationUrl = new URL('../../src/features/automation/AutomationView.tsx', import.meta.url);

test('automation policy UI uses the approved v2 read, policy, arm and v1 disarm operations', async () => {
  const endpoints = await readFile(endpointsUrl, 'utf8');
  for (const path of [
    '/api/v2/automation/status',
    '/api/v2/automation/policy',
    '/api/v2/automation/arm',
    '/api/v2/automation/runs',
    '/api/v2/automation/positions',
    '/api/v1/automation/disarm',
  ]) {
    assert.match(endpoints, new RegExp(path.replaceAll('/', '\\/')));
  }
  assert.match(endpoints, /newIdempotencyKey\('automation-policy'\)/);
  assert.match(endpoints, /newIdempotencyKey\('automation-arm-v2'\)/);
  const automation = await readFile(automationUrl, 'utf8');
  // 옛 라벨 "신규 주문 중지"는 계속 금지한다. disarm 은 주문을 막는 것이 아니라 다음 세션
  // 실행을 열지 않는 것이고, 주문 차단은 Kill Switch 다. 두 목적이 한 라벨에 섞이면
  // 사용자가 어느 쪽을 눌렀는지 알 수 없다 - 이게 9/4 에 이 버튼을 지운 이유의 절반이었다.
  assert.doesNotMatch(automation, /신규 주문 중지/);
  // 나머지 절반은 "정지한 뒤 재무장이 막히면 되돌릴 수 없다"였다. 버튼을 없애는 대신
  // 확인 단계에서 지금 다시 켤 수 있는지 보여 주는 방식으로 되돌렸다. 켤 수만 있고 끌 수
  // 없는 화면이 더 위험하다.
  assert.match(automation, /api\.disarmAutomation/);
  assert.match(automation, /자동운용 정지/);
  assert.match(automation, /confirmingDisarm/);
  assert.match(automation, /data\.status\.controlState !== 'ARMED'/);
});

test('overview reads actual automation status instead of inferring it from the kill switch', async () => {
  const overview = await readFile(overviewUrl, 'utf8');
  assert.match(overview, /api\.automationStatusV3\(\)/);
  assert.doesNotMatch(overview, /risk\.killSwitchActive \? '정지됨' : '작동 중'/);
});

test('blocked risk balance is visible and prevents the UI arm call', async () => {
  const automation = await readFile(automationUrl, 'utf8');
  assert.match(automation, /data\.status\.blockers\.length > 0/);
  assert.match(automation, /!data\.status\.canArm/);
  // 자동운용 화면은 v3 를 본다 — 청산 근거(ATR·보유기간·AI 판단)가 v3 에만 있다.
  // 지키려는 것은 "차단 사유가 있으면 arm 을 부르지 않는다"이지 특정 버전이 아니다.
  assert.match(automation, /api\.armAutomationV3/);
  assert.match(automation, /KIS 모의계좌 전용입니다\. 실제 계좌 주문은 실행하지 않습니다\./);
});

test('v3 정책 저장은 v2 가 못 채우는 네 값을 함께 보낸다', async () => {
  const automation = await readFile(automationUrl, 'utf8');
  const endpoints = await readFile(endpointsUrl, 'utf8');
  // v2 로 저장하면 v3 상태가 POLICY_V3_REQUIRED 로 시작을 막고 화면에서 풀 방법이 없다.
  assert.match(automation, /api\.putAutomationPolicyV3/);
  assert.doesNotMatch(automation, /api\.putAutomationPolicyV2/);
  for (const field of ['atrPeriod', 'atrMultiplierMilli', 'maxHoldingSessions', 'modelSellEnabled']) {
    assert.match(automation, new RegExp(field));
  }
  assert.match(endpoints, /newIdempotencyKey\('automation-policy-v3'\)/);
  assert.match(endpoints, /newIdempotencyKey\('automation-arm-v3'\)/);
});
