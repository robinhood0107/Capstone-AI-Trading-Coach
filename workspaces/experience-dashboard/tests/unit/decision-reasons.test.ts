import assert from 'node:assert/strict';
import test from 'node:test';
import { decisionMetricName, displayDecisionInputMetric, groupDecisionReasons } from '../../src/features/order-review/viewModel.ts';
import { decisionReasonLabel } from '../../src/shared/lib/labels.ts';

test('같은 판정 코드를 묶고 관련 규칙을 한국어로 표시한다', () => {
  const rows = groupDecisionReasons([
    { disposition: 'ISSUE', code: 'BALANCE_STALE', ruleId: 'max_position_per_asset', message: 'Required evaluation input is unavailable.' },
    { disposition: 'ISSUE', code: 'BALANCE_STALE', ruleId: 'max_gold_etf_etn_weight', message: 'Required evaluation input is unavailable.' },
    { disposition: 'ISSUE', code: 'RISK_SNAPSHOT_MISSING', ruleId: 'daily_loss_guard', message: 'Missing snapshot.' },
  ]);
  assert.equal(rows.length, 2);
  const stale = rows[0]!;
  assert.equal(stale.headline, '잔고 관측이 오래됨');
  assert.equal(stale.count, 2);
  assert.deepEqual(stale.ruleNames, ['단일 종목 최대 비중', '금 상품 최대 비중']);
  assert.equal(stale.detail, '2개 규칙 (단일 종목 최대 비중, 금 상품 최대 비중)');
  assert.deepEqual(stale.rawMessages, ['Required evaluation input is unavailable.']);
});

test('저장된 판정 입력값은 단위대로 표시하고 없는 값은 만들지 않는다', () => {
  assert.deepEqual(
    displayDecisionInputMetric({ metric: 'current_price_krw', value: 134700, unit: 'KRW', availability: 'AVAILABLE', observedAt: null }),
    { name: '현재가', value: '134,700원' },
  );
  assert.deepEqual(
    displayDecisionInputMetric({ metric: 'owner_position_quantity', value: 36, unit: 'QUANTITY', availability: 'AVAILABLE', observedAt: null }),
    { name: '보유 수량', value: '36주' },
  );
  assert.deepEqual(
    displayDecisionInputMetric({ metric: 'disclosure_risk_score', value: null, unit: null, availability: 'NOT_APPLICABLE', observedAt: null }),
    { name: '공시 위험 점수', value: '이번 주문에 해당하지 않음' },
  );
  assert.equal(decisionMetricName('daily_loss_rate'), '일일 손실률');
  assert.equal(decisionMetricName('new_metric'), 'new_metric');
});

test('해당 없음은 한 줄로 요약하고 새 코드는 원문으로 남긴다', () => {
  const rows = groupDecisionReasons([
    { disposition: 'ABSTENTION', code: 'NOT_APPLICABLE_V1', ruleId: 'ad_leading_room_guard', message: 'N/A' },
    { disposition: 'ABSTENTION', code: 'NOT_APPLICABLE_V1', ruleId: 'disclosure_risk_guard', message: 'N/A' },
    { disposition: 'WARNING', code: 'NEW_SERVER_CODE', message: 'new message' },
  ]);
  assert.equal(rows[0]!.detail, '해당하지 않는 기준 2개');
  assert.deepEqual(rows[0]!.ruleNames, ['광고성 선행매매 점검', '공시 위험 대응']);
  assert.equal(rows[1]!.headline, 'NEW_SERVER_CODE');
  assert.equal(decisionReasonLabel('RISK_DAILY_LOSS_LIMIT'), '일일 손실 한도 초과');
});
