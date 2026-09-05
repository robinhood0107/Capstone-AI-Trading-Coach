import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('production UI does not embed data-shaped decoration or statically import mock fixtures', () => {
  const overview = read('src/features/overview/OverviewView.tsx');
  const client = read('src/shared/api/client.ts');

  assert.doesNotMatch(overview, /HeroChartMotif|const bars = \[/);
  assert.doesNotMatch(client, /^import .*shared\/mock\/transport/m);
  assert.match(client, /process\.env\.NODE_ENV !== 'production'/);
  assert.match(client, /mockModule = require\('@\/shared\/mock\/transport'\)/);
});

test('overview renders the complete stored KIS balance without account-specific literals', () => {
  const overview = read('src/features/overview/OverviewView.tsx');
  const endpoints = read('src/shared/api/endpoints.ts');

  assert.match(endpoints, /\/api\/v1\/brokerage\/mock\/accounts\/\$\{encodeURIComponent\(accountId\)\}\/balances/);
  assert.match(overview, /balance\.positions\.map/);
  assert.match(overview, /자동매매 관리/);
  assert.match(overview, /직접 보유 · 매매 제외/);
  assert.match(overview, /policy\.capitalLimitKrw - stockValue/);
  assert.match(overview, /직접 보유 종목도 운용 한도와 위험 계산에는 포함/);
  assert.doesNotMatch(overview, /000660|006400|549_?000/);
  assert.doesNotMatch(overview, /positions\.items\.slice/);
  assert.match(overview, /InstrumentIdentity/);
  assert.match(endpoints, /\/api\/v1\/instruments\/display/);
});

test('expired decisions and internal evidence codes are not presented as orderable user data', () => {
  const view = read('src/features/order-review/OrderReviewView.tsx');
  const model = read('src/features/order-review/viewModel.ts');
  const signal = read('src/features/model-evaluation/viewModel.ts');

  assert.match(view, /view\.detail\.expired[\s\S]*'불가 · 재평가 필요'/);
  assert.match(view, /reason\.code === 'NOT_APPLICABLE_V1' \? null/);
  assert.match(model, /'이 주문의 평가 대상이 아닌 기준입니다\.'/);
  assert.match(signal, /'현재 운용 판단에는 규칙 baseline과 LSTM만 사용합니다\.'/);
});
