import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('production defaults to live API mode', () => {
  const source = read('src/shared/api/client.ts');
  assert.match(source, /NEXT_PUBLIC_API_MODE === 'mock' \? 'mock' : 'live'/);
});

test('product views omit LightGBM and select current dashboard records', () => {
  const model = read('src/features/model-evaluation/viewModel.ts');
  const report = read('src/features/report/ReportView.tsx');
  const orderReview = read('src/features/order-review/OrderReviewView.tsx');
  assert.doesNotMatch(model, /LIGHTGBM|LightGBM/);
  assert.match(model, /model\.modelId in DISPLAY/);
  assert.match(report, /dashboardRecentRiskResults/);
  assert.match(orderReview, /dashboardRecentRiskResults/);
  assert.doesNotMatch(report, /IdInput/);
});

test('financial Agent renders full answers, history, and hides source identifiers', () => {
  const view = read('src/features/rag-source/RagGuideView.tsx');
  assert.match(view, /whitespace-pre-line[^>]*>\{view\.answer\}/);
  assert.match(view, /loadRecentQuestions/);
  assert.doesNotMatch(view, />\{source\.sourceId\}</);
  assert.match(read('src/features/rag-source/viewModel.ts'), /\^rag_\[0-9a-f\]\{32\}\$/);
});

test('user automation screen has no stop action and top status reads automation state', () => {
  const automation = read('src/features/automation/AutomationView.tsx');
  const status = read('src/shared/ui/StatusBar.tsx');
  assert.doesNotMatch(automation, /신규 주문 중지/);
  assert.match(status, /automationStatusV2/);
  assert.match(status, /controlState === 'DISARMED'/);
});

test('settings do not replace missing usage with zero', () => {
  const settings = read('src/features/strong-llm/StrongLlmSettingsView.tsx');
  assert.doesNotMatch(settings, /usedToday \?\? 0|remaining \?\? 0/);
  assert.match(settings, /집계 없음/);
});

test('home and evaluation views distinguish missing values from loading', () => {
  const overview = read('src/features/overview/OverviewView.tsx');
  const evaluation = read('src/features/model-evaluation/ModelEvaluationView.tsx');
  const policy = read('src/features/automation/policy.ts');
  assert.doesNotMatch(overview, />확인 중</);
  assert.match(overview, /KIS Mock 계좌 연결 필요/);
  assert.match(evaluation, /Guide 포트폴리오 평가액/);
  assert.doesNotMatch(evaluation, /timeline\.slice\(0, 40\)/);
  assert.match(policy, /켜짐 · 장 시작 대기/);
});
