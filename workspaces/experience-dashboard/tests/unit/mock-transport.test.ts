import assert from 'node:assert/strict';
import test from 'node:test';
import { mockBareTransport, mockTransport } from '../../src/shared/mock/transport.ts';
import * as fixtures from '../../src/shared/mock/fixtures.ts';
import type {
  AutomationPositionV3,
  AutomationRunDetailV3,
  AutomationStatusV2,
  AutomationStatusV3,
  PrincipleCurrent,
  PrincipleHistoryData,
  RagV2HistoryPage,
  SignalV3Runtime,
} from '../../src/shared/api/wire.ts';

const REQUEST_ID = 'req_test_0000000000000000000000000000';
const PRINCIPLE_ID = fixtures.principle.principleId;

/**
 * mock 경로는 화면이 실제로 부르는 경로와 같아야 한다.
 *
 * 어긋나 있으면 실패가 조용하다 — 화면이 NOT_FOUND 하나만 받고, 픽스처는 아무도 읽지 않는
 * 죽은 데이터가 된다. 실제로 신호 패널이 그 상태였다.
 */
test('신호는 화면이 부르는 v3 경로로 온다', async () => {
  const symbol = Object.keys(fixtures.signals)[0];
  assert.ok(symbol, '픽스처에 신호가 하나는 있어야 한다');

  const envelope = await mockTransport<SignalV3Runtime>(
    `/api/v3/signals/${symbol}`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(envelope.success, true);

  // 백엔드에 v2 도 남아 있으므로 계속 받아 준다.
  const legacy = await mockTransport<SignalV3Runtime>(
    `/api/v2/signals/${symbol}`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(legacy.success, true);
});

test('없는 종목은 NOT_FOUND 다', async () => {
  const envelope = await mockTransport(
    '/api/v3/signals/000000',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(envelope.success, false);
});

test('원칙 버전 이력은 최신이 앞에 오고 첫 버전은 바뀐 항목이 없다', async () => {
  const envelope = await mockTransport<PrincipleHistoryData>(
    `/api/v1/principles/${PRINCIPLE_ID}/versions`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(envelope.success, true);
  const items = envelope.data!.items;
  assert.ok(items.length >= 2);

  const versions = items.map((item) => item.version);
  assert.deepEqual(versions, [...versions].sort((a, b) => b - a), '최신 버전이 앞이어야 한다');
  assert.deepEqual(items.at(-1)!.changedFields, [], '첫 버전에는 바뀐 항목이 없다');
  assert.equal(items[0]!.version, fixtures.principle.version, '맨 앞이 지금 적용 중인 버전이다');
});

test('원칙 생성은 고른 preset 의 기본값으로 v1 을 만든다', async () => {
  const preset = fixtures.presetList.items[0]!;
  const envelope = await mockTransport<PrincipleCurrent>(
    '/api/v1/principles',
    'POST',
    { title: '테스트 원칙', presetId: preset.presetId },
    REQUEST_ID,
  );
  assert.equal(envelope.success, true);
  assert.equal(envelope.data!.title, '테스트 원칙');
  assert.equal(envelope.data!.presetId, preset.presetId);
  assert.equal(envelope.data!.version, 1);
  assert.deepEqual(envelope.data!.rules, preset.defaultRules);
});

test('RAG 질문 기록은 쌓이고 지우면 사라진다', async () => {
  await mockBareTransport('/api/v2/rag/consents', 'POST', { action: 'GRANT' }, REQUEST_ID);

  const before = (await mockBareTransport<RagV2HistoryPage>(
    '/api/v2/rag/history',
    'GET',
    undefined,
    REQUEST_ID,
  )).items.length;

  const answer = await mockBareTransport<{ answerId: string | null }>(
    '/api/v2/rag/ask',
    'POST',
    { question: '금 ETF의 롤오버 위험은 무엇인가요?' },
    REQUEST_ID,
  );
  assert.ok(answer.answerId, '답변이 만들어져야 기록에 쌓인다');
  // 화면(`loadRecentQuestions`)이 받아 주는 형태여야 한다. 아니면 기록이 늘 비어 보인다.
  assert.match(answer.answerId, /^rag_[0-9a-f]{32}$/);

  const after = await mockBareTransport<RagV2HistoryPage>(
    '/api/v2/rag/history',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(after.items.length, before + 1);

  await mockBareTransport(
    `/api/v2/rag/history/${answer.answerId}`,
    'DELETE',
    undefined,
    REQUEST_ID,
  );
  const removed = await mockBareTransport<RagV2HistoryPage>(
    '/api/v2/rag/history',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(removed.items.length, before);
});

test('RAG 피드백은 helpful 이 boolean 일 때만 받는다', async () => {
  const answerId = `rag_${'a'.repeat(32)}`;
  const okEnvelope = await mockTransport(
    `/api/v1/rag/answers/${answerId}/feedback`,
    'POST',
    { helpful: true },
    REQUEST_ID,
  );
  assert.equal(okEnvelope.success, true);

  const missing = await mockTransport(
    `/api/v1/rag/answers/${answerId}/feedback`,
    'POST',
    {},
    REQUEST_ID,
  );
  assert.equal(missing.success, false);
});

test('개인 중지를 전역 API로 해제할 수 없다', async () => {
  const before = await mockTransport<{ active: boolean }>(
    '/api/v2/risk/kill-switch',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(before.data!.active, false);

  const stopped = await mockTransport<{ active: boolean; reasonClass: string }>(
    '/api/v2/risk/kill-switch',
    'POST',
    { active: true },
    REQUEST_ID,
  );
  assert.equal(stopped.success, true);
  assert.equal(stopped.data!.active, true);
  assert.equal(stopped.data!.reasonClass, 'USER_MANUAL_STOP');

  // 해제는 ADMIN 만 된다(KillSwitchTransitionPolicy.kt:22). mock 은 USER 다.
  const resume = await mockTransport(
    '/api/v1/risk/kill-switch',
    'POST',
    { active: false },
    REQUEST_ID,
  );
  assert.equal(resume.success, false);
  assert.equal(resume.error?.code, 'FORBIDDEN');

  const after = await mockTransport<{ active: boolean }>(
    '/api/v2/risk/kill-switch',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(after.data!.active, true, '거부된 해제가 상태를 바꾸면 안 된다');

  // 같은 사실이 화면 두 곳에서 어긋나면 안 된다. 자동운용 상태도 같은 값을 봐야 한다.
  const status = await mockTransport<AutomationStatusV2>(
    '/api/v2/automation/status',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(status.data!.killSwitchActive, true);
  assert.equal(status.data!.canArm, false, 'Kill Switch 가 켜져 있으면 시작할 수 없다');
  assert.ok(status.data!.blockers.includes('KILL_SWITCH_ACTIVE'));
});

test('주문은 평가 → 제출 → 취소까지 한 바퀴 돈다', async () => {
  const accountId = fixtures.mockBalance.accountId;

  const buyable = await mockTransport<{ buyableQuantity: number }>(
    `/api/v1/brokerage/mock/accounts/${accountId}/buyable?symbol=005930&price=71000`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(buyable.success, true);
  assert.ok(buyable.data!.buyableQuantity > 0);

  // 서버는 symbol 과 price 를 둘 다 요구한다. 하나만 보내면 mock 도 거절해야 live 와 어긋나지 않는다.
  const noPrice = await mockTransport(
    `/api/v1/brokerage/mock/accounts/${accountId}/buyable?symbol=005930`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(noPrice.success, false);

  const evaluated = await mockTransport<{ decisionId: string; riskDecision: { decision: string } }>(
    '/api/v1/decisions/evaluate-order',
    'POST',
    { orderIntent: { symbol: '005930', quantity: 1, estimatedAmount: 71_000 } },
    REQUEST_ID,
  );
  assert.equal(evaluated.success, true);
  assert.equal(evaluated.data!.riskDecision.decision, 'ALLOW');

  const submitted = await mockTransport<{ orderId: string; status: string }>(
    '/api/v1/brokerage/mock/orders',
    'POST',
    {
      decisionId: evaluated.data!.decisionId,
      orderIntent: { symbol: '005930', quantity: 1 },
      userAcknowledgement: { warningsAccepted: true },
    },
    REQUEST_ID,
  );
  assert.equal(submitted.success, true);
  assert.equal(submitted.data!.status, 'SUBMITTED');

  const orderId = submitted.data!.orderId;
  const cancelled = await mockTransport<{ status: string }>(
    `/api/v1/brokerage/orders/${orderId}/cancel`,
    'POST',
    undefined,
    REQUEST_ID,
  );
  assert.equal(cancelled.data!.status, 'CANCELLED');

  // 두 번째 취소는 막힌다 — 이미 취소된 주문이다.
  const again = await mockTransport(
    `/api/v1/brokerage/orders/${orderId}/cancel`,
    'POST',
    undefined,
    REQUEST_ID,
  );
  assert.equal(again.success, false);
  assert.equal(again.error?.code, 'CONFLICT');
});

test('판정 근거 없는 주문은 받지 않는다', async () => {
  const envelope = await mockTransport(
    '/api/v1/brokerage/mock/orders',
    'POST',
    { orderIntent: { symbol: '005930', quantity: 1 }, userAcknowledgement: { warningsAccepted: true } },
    REQUEST_ID,
  );
  assert.equal(envelope.success, false);
});

test('한도를 넘는 금액은 BLOCK 으로 판정된다', async () => {
  const limit =
    fixtures.principle.rules.find((rule) => rule.ruleId === 'max_single_order_amount')?.threshold ?? 0;
  assert.ok(limit > 0);
  const evaluated = await mockTransport<{ riskDecision: { decision: string; canSubmitOrder: boolean } }>(
    '/api/v1/decisions/evaluate-order',
    'POST',
    { orderIntent: { symbol: '005930', quantity: 1, estimatedAmount: limit * 2 } },
    REQUEST_ID,
  );
  assert.equal(evaluated.data!.riskDecision.decision, 'BLOCK');
  assert.equal(evaluated.data!.riskDecision.canSubmitOrder, false);
});

test('v3 자동운용은 v2 가 못 담는 것을 담는다', async () => {
  const status = await mockTransport<AutomationStatusV3>(
    '/api/v3/automation/status',
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(status.data!.contractId, 'automation-status.v3');
  assert.equal(typeof status.data!.aiJudgementEnabled, 'boolean');
  assert.ok(['EMPTY', 'PARTIAL', 'READY', 'CATCHUP_REQUIRED'].includes(status.data!.marketHistoryStatus));

  const positions = await mockTransport<{ items: AutomationPositionV3[] }>(
    '/api/v3/automation/positions',
    'GET',
    undefined,
    REQUEST_ID,
  );
  const position = positions.data!.items[0]!;
  // v2 에는 없는 것들. 이것 때문에 자동운용 화면이 v3 를 본다.
  assert.equal(typeof position.peakPriceKrw, 'number');
  assert.equal(typeof position.maxHoldingSessions, 'number');
  assert.equal(typeof position.atrMultiplierMilli, 'number');
});

test('실행 상세는 AI 가 읽은 근거를 그대로 준다', async () => {
  const runs = await mockTransport<{ items: { runId: string; evidenceCount: number }[] }>(
    '/api/v3/automation/runs',
    'GET',
    undefined,
    REQUEST_ID,
  );
  const judged = runs.data!.items.find((run) => run.evidenceCount > 0);
  assert.ok(judged, '근거가 있는 실행이 하나는 있어야 한다');

  const detail = await mockTransport<AutomationRunDetailV3>(
    `/api/v3/automation/runs/${judged.runId}`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  const screening = detail.data!.candidateScreenings[0]!;
  assert.ok(screening.evidence.length > 0);
  for (const item of screening.evidence) {
    // 인용문은 240자를 넘지 않고 출처가 반드시 붙는다(계약).
    assert.ok(item.boundedQuote.length > 0 && item.boundedQuote.length <= 240);
    assert.ok(['OFFICIAL_PRIMARY', 'REGISTERED_INDEPENDENT'].includes(item.sourceType));
    assert.equal(item.verified, true);
  }

  // 근거 없이 끝난 실행은 빈 목록이지 404 가 아니다.
  const skipped = runs.data!.items.find((run) => run.evidenceCount === 0);
  assert.ok(skipped);
  const empty = await mockTransport<AutomationRunDetailV3>(
    `/api/v3/automation/runs/${skipped.runId}`,
    'GET',
    undefined,
    REQUEST_ID,
  );
  assert.equal(empty.success, true);
  assert.deepEqual(empty.data!.candidateScreenings, []);
});

test('v3 정책은 ATR·보유기간·모델매도가 다 와야 저장된다', async () => {
  const before = await mockTransport<AutomationStatusV3>(
    '/api/v3/automation/status',
    'GET',
    undefined,
    REQUEST_ID,
  );
  const policy = before.data!.policy!;

  // v2 모양으로만 보내면 거절한다. 그래야 v3 화면이 막다른 길로 가지 않는다.
  const v2Shape = await mockTransport(
    '/api/v3/automation/policy',
    'PUT',
    {
      expectedVersion: policy.version,
      capitalLimitKrw: policy.capitalLimitKrw,
      stopLossBps: policy.stopLossBps,
      takeProfitBps: policy.takeProfitBps,
    },
    REQUEST_ID,
  );
  assert.equal(v2Shape.success, false);

  const saved = await mockTransport<{ version: number; atrPeriod: number }>(
    '/api/v3/automation/policy',
    'PUT',
    {
      expectedVersion: policy.version,
      capitalLimitKrw: policy.capitalLimitKrw,
      stopLossBps: policy.stopLossBps,
      takeProfitBps: policy.takeProfitBps,
      atrPeriod: 20,
      atrMultiplierMilli: 3000,
      maxHoldingSessions: 40,
      modelSellEnabled: false,
    },
    REQUEST_ID,
  );
  assert.equal(saved.success, true);
  assert.equal(saved.data!.atrPeriod, 20);
  assert.equal(saved.data!.version, policy.version + 1, '저장하면 버전이 오른다');

  // 낡은 버전으로 다시 저장하면 덮어쓰지 않는다.
  const stale = await mockTransport(
    '/api/v3/automation/policy',
    'PUT',
    {
      expectedVersion: policy.version,
      capitalLimitKrw: policy.capitalLimitKrw,
      stopLossBps: policy.stopLossBps,
      takeProfitBps: policy.takeProfitBps,
      atrPeriod: 14,
      atrMultiplierMilli: 2500,
      maxHoldingSessions: 60,
      modelSellEnabled: true,
    },
    REQUEST_ID,
  );
  assert.equal(stale.success, false);
  assert.equal(stale.error?.code, 'CONFLICT');
});

test('제목이나 preset 이 빠지면 만들지 않는다', async () => {
  const noTitle = await mockTransport('/api/v1/principles', 'POST', { presetId: 'balanced' }, REQUEST_ID);
  assert.equal(noTitle.success, false);

  const unknownPreset = await mockTransport(
    '/api/v1/principles',
    'POST',
    { title: 'x', presetId: 'nope' },
    REQUEST_ID,
  );
  assert.equal(unknownPreset.success, false);
});

test('개인 중지는 USER가 정지와 해제를 하고 전역 변경은 거부된다', async () => {
  const global = await mockTransport('/api/v1/risk/kill-switch', 'POST', { active: true }, REQUEST_ID);
  assert.equal(global.success, false);
  for (const active of [true, false]) {
    const changed = await mockTransport('/api/v2/risk/kill-switch', 'POST', { active }, REQUEST_ID);
    assert.equal(changed.success, true);
    const state = await mockTransport('/api/v2/risk/kill-switch', 'GET', undefined, REQUEST_ID);
    assert.equal(state.success, true);
    if (state.success) assert.equal((state.data as { active: boolean }).active, active);
  }
});
