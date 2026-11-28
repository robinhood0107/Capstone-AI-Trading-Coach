import { expect, test, type Page } from '@playwright/test';
import type { DashboardEnvelope, DashboardRiskResultView, DecisionProjection, JournalEntry, LoginResponse, PrincipleCurrent, PrincipleVersion } from '../../src/shared/api/wire';
import { decisions, presetList, principle as sample } from '../../src/shared/mock/fixtures';
import { mockTransport } from '../../src/shared/mock/transport';

const requestId = 'req_00000000000000000000000000000233';
const recentPath = '/api/v1/dashboard/risk-results/recent';
const principlePath = '/api/v1/principles';
const ruleId = 'max_single_order_amount';
const orderLimit = '1회 주문 최대 금액 값';
const decisionId = 'dec_demo_block_000001';
const decisionPath = '/api/v1/decisions/' + decisionId;
const date = '2026-09-08T06:00:00Z';
test.setTimeout(75_000);

const conservative = presetList.items.find(({ presetId }) => presetId === 'conservative')!;

async function routeLearningApi(page: Page) {
  const auth = await mockTransport<LoginResponse>('/api/v1/auth/login', 'POST', undefined, requestId);
  const state = { inputsFailed: false, decisionFailed: false, principleFailure: 0, hidePrinciple: false, principleReads: [] as string[] };
  let principle: PrincipleCurrent | null = null;
  const other = { ...sample, principleId: 'prc_' + '9'.repeat(32), title: '별도 원칙' };
  let evaluated: DecisionProjection | null = null;
  let journal: JournalEntry | null = null;
  const versions: PrincipleVersion[] = [];
  const ok = (data: unknown) => ({ json: { success: true, data, requestId, warnings: [], error: null } });
  const outage = {
    status: 503, json: { success: false, data: null, requestId, warnings: [], error: { code: 'INTERNAL_ERROR', message: 'Synthetic outage' } },
  };

  await page.addInitScript(({ accessToken, expiresAt, user }) => {
    sessionStorage.setItem('capstone.session.v1', JSON.stringify({ token: accessToken, expiresAt, user }));
  }, auth.data!);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const { pathname, search } = new URL(request.url());
    const method = request.method();
    if (pathname === '/api/v1/auth/refresh') return route.fulfill({ json: auth });
    if (method !== 'GET') {
      if (pathname === principlePath && method === 'POST') {
        const body = request.postDataJSON();
        expect(body).toEqual({ title: '합성 원칙', presetId: 'conservative', mode: 'GUIDE' });
        principle = { ...sample, ...body, version: 1, rules: conservative.defaultRules };
        versions.unshift({ ...principle!, changedFields: [] });
        return route.fulfill(ok(principle));
      }
      if (principle && pathname === principlePath + '/' + principle.principleId && method === 'PUT') {
        const body = request.postDataJSON();
        expect(body.expectedVersion).toBe(1);
        expect(body.rules.find((rule: { ruleId: string }) => rule.ruleId === ruleId).threshold).toBe(400000);
        principle = { ...principle, rules: body.rules, version: 2 };
        versions.unshift({ ...principle, changedFields: ['rules.max_single_order_amount.threshold'] });
        return route.fulfill(ok(principle));
      }
      if (pathname === '/api/v1/decisions/evaluate-order' && method === 'POST') {
        expect(request.postDataJSON()).toMatchObject({
          principleId: principle?.principleId,
          orderIntent: { symbol: '005930', quantity: 10, estimatedPrice: 50000, estimatedAmount: 500000 },
        });
        const basis = decisions[decisionId]!;
        const validUntil = new Date(Date.now() + 60_000).toISOString();
        evaluated = { ...basis, createdAt: date, mode: principle!.mode, principleVersion: 1, validUntil,
          riskDecision: { ...basis.riskDecision, readinessPolicyVersion: 's2-2-readiness-v1', mode: principle!.mode, principleVersion: 1, validUntil,
            violations: [{ ...basis.riskDecision.violations[0]!, metricValue: 500000, threshold: 300000 }],
            issues: [], warnings: [],
            abstentions: [{ code: 'OPTIONAL_EVIDENCE_MISSING', component: 'negative_news_guard',
              disposition: 'ABSTAIN', message: 'Synthetic news evidence is unavailable.' }],
            riskItems: [{ metric: 'daily_loss_rate', value: -0.012, severity: 'INFO', source: 'PORTFOLIO' }],
          },
        };
        return route.fulfill(ok(evaluated));
      }
      if (pathname === '/api/v1/journals' && method === 'POST') {
        const body = request.postDataJSON();
        expect(body.links).toEqual({ decisionId });
        journal = { ...body, contractId: 'journal.v1', journalId: 'jrn_' + '1'.repeat(32), ownerScope: 'OWNER',
          links: { decisionId, backtestRunId: null, ragAnswerId: null, orderId: null, automationRunId: null },
          version: 1, createdAt: date, updatedAt: date,
        };
        return route.fulfill(ok(journal));
      }
      throw new Error('Unexpected write ' + method + ' ' + pathname);
    }

    if (pathname === principlePath) {
      const items = evaluated ? [other, ...(state.hidePrinciple ? [] : [principle])] : principle ? [principle] : [];
      return route.fulfill(ok({ items, nextCursor: null }));
    }
    if (pathname === principlePath + '/' + other.principleId) return route.fulfill(ok(other));
    if (pathname === principlePath + '/' + principle?.principleId) {
      state.principleReads.push(pathname);
      if (state.principleFailure) {
        const code = state.principleFailure === 403 ? 'FORBIDDEN' : 'NOT_FOUND';
        return route.fulfill({ status: state.principleFailure, json: { ...outage.json, error: { code, message: code } } });
      }
      return route.fulfill(ok(principle));
    }
    if (pathname === principlePath + '/' + principle?.principleId + '/versions') {
      return route.fulfill(ok({ items: versions, nextCursor: null }));
    }
    if (pathname === '/api/v1/journals') return route.fulfill(ok({ items: journal ? [journal] : [], nextCursor: null }));
    if (pathname === recentPath) return route.fulfill(ok({ items: evaluated ? [
      { decisionId, action: 'BLOCK', symbol: '005930', asOf: date, validUntil: evaluated.validUntil },
    ] : [] }));
    if (pathname === '/api/v1/dashboard/risk-results/' + decisionId) {
      const { data } = await mockTransport<DashboardEnvelope<DashboardRiskResultView>>(pathname, 'GET', undefined, requestId);
      return route.fulfill(ok({ ...data, view: {
        ...data!.view!,
        reasons: ['1회 주문 금액이 기준을 넘었습니다.'],
        principles: ['1회 주문 최대 금액'],
        riskItems: [{ code: 'ORDER_AMOUNT_EXCEEDED', severity: 'BLOCK', summary: '주문 금액이 기준을 넘었습니다.' }],
      } }));
    }
    if (pathname === decisionPath) return route.fulfill(state.decisionFailed ? outage : ok(evaluated));
    if (pathname === decisionPath + '/inputs') {
      return route.fulfill(state.inputsFailed ? outage : ok({ decisionId, items: [
        { metric: 'order_amount_krw', value: 500000, unit: 'KRW', availability: 'AVAILABLE', observedAt: date },
        { metric: 'negative_news_score', value: null, unit: null, availability: 'MISSING', observedAt: null },
      ] }));
    }

    const json = await mockTransport(pathname + search, 'GET', undefined, requestId);
    return route.fulfill({ status: json.success ? 200 : 404, json });
  });
  return state;
}

for (const width of [390, 1440]) {
  test('register, review, revise and read a linked journal at ' + width + 'px', async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await routeLearningApi(page);
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.goto('/order-review');
    await expect(page.getByText('저장된 주문 판정이 없습니다.', { exact: false })).toBeVisible();
    await page.getByRole('link', { name: '원칙 만들기' }).click();
    await page.getByLabel('이름', { exact: true }).fill('합성 원칙');
    await page.getByRole('button', { name: '보수형', exact: true }).click();
    await page.getByRole('button', { name: '이 원칙으로 시작하기' }).click();
    await expect(page.getByLabel(orderLimit)).toHaveValue('300000');

    await page.goto('/order-review');
    await page.getByLabel('종목코드').fill('005930');
    await page.getByLabel('수량 (주)').fill('10');
    await page.getByLabel('예상 단가 (원)').fill('50000');
    await page.getByRole('button', { name: '원칙으로 평가' }).click();
    await page.getByRole('link', { name: '판정 근거 보기' }).click();
    const evidence = page.locator('[data-contract="decision-evidence"]');
    await expect(evidence.getByText(ruleId, { exact: true })).toBeVisible();
    await expect(evidence.getByRole('cell', { name: '500,000원' })).toBeVisible();
    await expect(evidence.getByRole('cell', { name: '300,000원' })).toBeVisible();
    await expect(evidence.getByText('s2-2-readiness-v1')).toBeVisible();
    await expect(evidence.getByText('KIS 모의계좌')).toBeVisible();
    await expect(evidence.getByText('OPTIONAL_EVIDENCE_MISSING', { exact: true })).toBeVisible();
    await expect(evidence.getByText('일일 손실률 출처 PORTFOLIO')).toBeVisible();
    await expect(evidence.getByText('관측 없음')).toBeVisible();
    await expect(page.getByRole('button', { name: '내용 확인' })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await evidence.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('decision-' + width + '.png') });

    await page.getByRole('link', { name: '현재 원칙 수정' }).click();
    await expect(page.getByRole('heading', { name: '합성 원칙' })).toBeVisible();
    await page.getByLabel(orderLimit).fill('400000');
    await page.getByRole('button', { name: '변경 사항 저장' }).click();
    await expect(page.getByText('v2', { exact: true }).first()).toBeVisible();
    await page.reload();
    await expect(page.getByLabel(orderLimit)).toHaveValue('400000');
    await expect(page.getByText('당시 원칙 v1', { exact: false })).toBeVisible();
    await expect(page.getByText('편집 중인 버전')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.getByLabel(orderLimit).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('principles-' + width + '.png') });

    await page.goto('/order-review');
    await expect(evidence.getByText('v1', { exact: true })).toBeVisible();
    await expect(evidence.getByRole('cell', { name: '300,000원' })).toBeVisible();
    state.inputsFailed = true;
    await page.reload();
    await expect(evidence.getByText('당시 입력값을 불러오지 못했습니다.')).toBeVisible();
    await expect(evidence.getByRole('cell', { name: '300,000원' })).toBeVisible();
    state.inputsFailed = false;
    await page.reload();
    await evidence.getByRole('button', { name: '학습일지로 남기기' }).click();
    await expect(page.getByText('첫 학습일지를 작성해 보세요.')).toBeVisible();
    await expect(page.getByLabel('학습일지 내용')).toHaveValue(/max_single_order_amount.*500,000원.*300,000원/);
    await page.getByRole('button', { name: '기록 저장' }).click();
    await expect(page.getByText('저장했습니다.', { exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole('button', { name: /주문 판정 BLOCK/ }).click();
    await expect(evidence.getByText('v1', { exact: true })).toBeVisible();
    await expect(evidence.getByRole('cell', { name: '300,000원' })).toBeVisible();
    await evidence.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('journal-' + width + '.png') });

    await page.goto('/report');
    await expect(evidence.getByRole('cell', { name: '300,000원' })).toBeVisible();
    state.decisionFailed = true;
    await page.reload();
    await expect(evidence.getByText(/판정 상세.*불러오지 못했습니다/)).toBeVisible();
    await expect(evidence.getByText('ORDER_AMOUNT_EXCEEDED')).toBeVisible();

    await page.goto('/principles#principle-rules');
    await expect(page.getByRole('heading', { name: '합성 원칙' })).toBeVisible();
    // First the detail disappears after list lookup; then only another owner's list is returned.
    for (const [status, hidden] of [[404, false], [403, false], [404, true]] as const) {
      state.principleFailure = status;
      state.hidePrinciple = hidden;
      state.principleReads.length = 0;
      await page.reload();
      await expect(page.getByText('판정에 쓰인 원칙을 확인하지 못했습니다.')).toBeVisible();
      await expect(page.getByRole('heading', { name: '별도 원칙' })).toBeVisible();
      await expect(page.getByRole('button', { name: '변경 사항 저장' })).toBeDisabled();
      expect(state.principleReads.length > 0).toBe(!hidden);
    }
    expect(pageErrors).toEqual([]);
  });
}
