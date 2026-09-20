import assert from 'node:assert/strict';
import test from 'node:test';

import { reasonLabel, summarizeNoOrder } from '../../src/features/automation/CandidateFunnel.tsx';
import type { AutomationStageOutcome } from '../../src/shared/api/wire.ts';

function outcome(
  stage: AutomationStageOutcome['stage'],
  symbol: string,
  result: AutomationStageOutcome['outcome'],
  reasonCode: string | null = null,
): AutomationStageOutcome {
  return { stage, symbol, outcome: result, reasonCode, reasonDetail: null };
}

test('무주문 원인은 마지막으로 전멸한 단계를 가리킨다', () => {
  // 2026-09-09 실행의 모양: 규칙 12 -> LSTM 7 -> 안전필터 0.
  const outcomes: AutomationStageOutcome[] = [
    outcome('RULE_BUY', '005930', 'PASS'),
    outcome('RULE_BUY', '000660', 'PASS'),
    outcome('LSTM_VETO', '005930', 'PASS'),
    outcome('LSTM_VETO', '000660', 'PASS'),
    outcome('QUOTE_SAFETY', '005930', 'DROPPED', 'MANAGEMENT_ISSUE'),
    outcome('QUOTE_SAFETY', '000660', 'DROPPED', 'MANAGEMENT_ISSUE'),
  ];

  const summary = summarizeNoOrder(outcomes);

  assert.ok(summary);
  assert.match(summary, /실시간 안전 확인/);
  assert.match(summary, /2종목/);
  assert.match(summary, /관리종목 지정/);
});

test('한 종목이라도 통과한 단계는 원인으로 지목하지 않는다', () => {
  const outcomes: AutomationStageOutcome[] = [
    outcome('RULE_BUY', '005930', 'PASS'),
    outcome('RULE_BUY', '000660', 'DROPPED', 'RULE_NOT_BUY'),
    outcome('RISK_ENGINE', '005930', 'DROPPED', 'SIZING_BELOW_ONE_SHARE'),
  ];

  const summary = summarizeNoOrder(outcomes);

  assert.ok(summary);
  assert.match(summary, /위험 검증/);
});

test('세션 전체를 막는 단계는 종목 수를 말하지 않는다', () => {
  const summary = summarizeNoOrder([
    outcome('OBSERVATION', '000000', 'DROPPED', 'OBSERVATION_NOT_PUBLISHED'),
  ]);

  assert.ok(summary);
  assert.match(summary, /관측 적재/);
  assert.doesNotMatch(summary, /종목이 모두 제외/);
});

test('탈락이 없으면 원인을 지어내지 않는다', () => {
  assert.equal(summarizeNoOrder([outcome('RULE_BUY', '005930', 'PASS')]), null);
});

test('모르는 사유 코드는 감추지 않고 코드를 그대로 보여준다', () => {
  assert.equal(reasonLabel('MANAGEMENT_ISSUE'), '관리종목 지정');
  assert.equal(reasonLabel('SOMETHING_NEW'), 'SOMETHING_NEW');
});
