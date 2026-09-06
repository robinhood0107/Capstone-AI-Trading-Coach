import assert from 'node:assert/strict';
import test from 'node:test';
import { mockTransport } from '../../src/shared/mock/transport.ts';
import * as fixtures from '../../src/shared/mock/fixtures.ts';
import type {
  PrincipleCurrent,
  PrincipleHistoryData,
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
