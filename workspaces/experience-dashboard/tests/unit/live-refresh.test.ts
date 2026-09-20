import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { LIVE_REFRESH_MS, SLOW_REFRESH_MS } from '../../src/shared/lib/liveRefresh.ts';

function source(relative: string): string {
  return readFileSync(fileURLToPath(new URL(`../../src/${relative}`, import.meta.url)), 'utf8');
}

/** `useResource(...)` 호출 본문만 괄호를 세어 잘라낸다. 인자가 여러 줄에 걸쳐 있다. */
function resourceCalls(text: string): string[] {
  const bodies: string[] = [];
  const needle = 'useResource(';
  let from = 0;
  for (;;) {
    const start = text.indexOf(needle, from);
    if (start < 0) break;
    let index = start + needle.length;
    let depth = 1;
    while (index < text.length && depth > 0) {
      if (text[index] === '(') depth += 1;
      else if (text[index] === ')') depth -= 1;
      index += 1;
    }
    bodies.push(text.slice(start + needle.length, index - 1));
    from = index;
  }
  return bodies;
}

test('거래가 살아 있는 화면은 스스로 갱신한다', () => {
  // `useResource` 에는 처음부터 폴링이 있었는데 **아무 화면도 인자를 넘기지 않았다.**
  // 그래서 2026-09-16 에 45주 중 27주가 체결되는 동안 자동운용 화면은 그대로였다.
  for (const relative of [
    'features/automation/AutomationView.tsx',
    'features/order-review/OrderReviewView.tsx',
    'features/system/SystemHealthView.tsx',
  ]) {
    const text = source(relative);
    assert.ok(
      text.includes('LIVE_REFRESH_MS'),
      `${relative}: 살아 있는 화면인데 갱신 주기가 없다`,
    );
  }

  // 현황과 최근 체결은 예전부터 5초로 돌고 있었다. 되돌아가지 않게 못박는다.
  for (const relative of ['features/overview/OverviewView.tsx', 'features/order-review/FillsPanel.tsx']) {
    const calls = resourceCalls(source(relative));
    assert.ok(
      calls.some((body) => /,\s*5_?000\s*$/.test(body.trim())),
      `${relative}: 5초 갱신이 사라졌다`,
    );
  }
});

test('하루 단위 화면까지 15초로 때리지 않는다', () => {
  // 백테스트·모델 비교·원칙은 장전 배치가 만든 값이다. 자주 물어도 같은 답이 온다.
  for (const relative of [
    'features/backtest-report/BacktestReportView.tsx',
    'features/model-evaluation/ModelEvaluationView.tsx',
    'features/principles/PrinciplesView.tsx',
  ]) {
    assert.ok(
      !source(relative).includes('LIVE_REFRESH_MS'),
      `${relative}: 정적인 화면에 실시간 주기가 붙었다`,
    );
  }
});

test('갱신 주기는 사람이 읽을 수 있는 값이어야 한다', () => {
  // 너무 짧으면 읽는 속도보다 화면이 먼저 바뀌고, 너무 길면 체결을 놓친다.
  assert.ok(LIVE_REFRESH_MS >= 5_000 && LIVE_REFRESH_MS <= 30_000);
  assert.ok(SLOW_REFRESH_MS >= LIVE_REFRESH_MS);
});

test('주문 진행률은 서버가 주는 값만 쓴다', () => {
  // 화면이 수량을 스스로 계산하기 시작하면 장부와 화면이 갈린다.
  const text = source('features/automation/OrderProgress.tsx');
  for (const field of [
    'orderQuantity',
    'filledQuantity',
    'leavesQuantity',
    'limitPriceKrw',
    'selectedSide',
  ]) {
    assert.ok(text.includes(field), `진행률이 ${field} 를 쓰지 않는다`);
  }
  // 체결 수량을 화면에서 만들어 내지 않는다.
  assert.ok(!/filled\s*=\s*ordered\s*[-+]/.test(text));
});

test('패널의 계약 표기가 DOM 까지 간다', () => {
  // 39개 호출부가 `contract=` 를 넘기는데 컴포넌트가 받지도 않아 전부 버려지고 있었다.
  const text = source('shared/ui/Panel.tsx');
  assert.ok(text.includes('data-contract={contract}'));
  assert.ok(/export function Panel\(\{[^}]*contract/.test(text));
});
