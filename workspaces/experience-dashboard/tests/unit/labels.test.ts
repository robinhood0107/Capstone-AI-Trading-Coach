import assert from 'node:assert/strict';
import test from 'node:test';

import {
  onOffLabel,
  orderStatusLabel,
  riskCodeLabel,
  severityLabel,
} from '../../src/shared/lib/labels.ts';

test('주문 상태는 한국어로 나오고 지금 무슨 뜻인지 함께 말한다', () => {
  // "SUBMITTED"를 "제출됨"으로 바꾸기만 하면 처음 보는 사람에게는 여전히 정보가 아니다.
  const submitted = orderStatusLabel('SUBMITTED');
  assert.equal(submitted.label, '접수됨');
  assert.match(submitted.meaning ?? '', /체결을 기다리는/);

  const partial = orderStatusLabel('PARTIALLY_FILLED');
  assert.equal(partial.label, '일부 체결');
  assert.match(partial.meaning ?? '', /체결된 만큼은 이미 장부에/);
});

test('모르는 코드는 빈칸이 아니라 코드 그대로 보여 준다', () => {
  // 빈칸은 "값이 없다"는 거짓말이다. 서버가 새 상태를 추가해도 화면이 비지 않아야 한다.
  assert.equal(orderStatusLabel('SOME_NEW_STATE').label, 'SOME_NEW_STATE');
  assert.equal(riskCodeLabel('FUTURE_RULE').label, 'FUTURE_RULE');
  assert.equal(severityLabel('UNKNOWN').label, 'UNKNOWN');
});

test('값이 아예 없을 때만 미상이다', () => {
  assert.equal(orderStatusLabel(null).label, '미상');
  assert.equal(orderStatusLabel(undefined).label, '미상');
  assert.equal(orderStatusLabel('').label, '미상');
});

test('심각도와 위험 코드도 사람 말로 나온다', () => {
  assert.equal(severityLabel('BLOCK').label, '차단');
  assert.match(severityLabel('BLOCK').meaning ?? '', /주문을 내지 않습니다/);
  assert.equal(riskCodeLabel('NO_REMAINING_RETURN').label, '남은 기대수익 없음');
  assert.match(riskCodeLabel('NO_REMAINING_RETURN').meaning ?? '', /수수료를 빼고/);
});

test('긴급 정지는 ACTIVE/OFF 가 아니라 켜짐/꺼짐이다', () => {
  assert.equal(onOffLabel(true).label, '켜짐');
  assert.equal(onOffLabel(false).label, '꺼짐');
  assert.match(onOffLabel(true).meaning ?? '', /멈춰 있습니다/);
});

test('사전에 영문 코드가 라벨로 새어 들어가지 않았다', () => {
  // 사전을 채우다 보면 label 에 코드를 그대로 복사해 두기 쉽다.
  for (const code of [
    'SUBMITTED',
    'PARTIALLY_FILLED',
    'FILLED',
    'CANCELLED',
    'REJECTED',
    'ACCEPTED',
    'EXPIRED',
    'DRAFT',
  ]) {
    assert.notEqual(orderStatusLabel(code).label, code, `${code} 가 번역되지 않았다`);
  }
  for (const code of ['BLOCK', 'WARN', 'INFO', 'ALLOW']) {
    assert.notEqual(severityLabel(code).label, code, `${code} 가 번역되지 않았다`);
  }
});
