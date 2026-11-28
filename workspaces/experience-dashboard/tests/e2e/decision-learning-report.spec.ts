import { expect, test } from '@playwright/test';
import type { DashboardBacktestView, DashboardEnvelope, LoginResponse, OwnerPerformanceReport } from '../../src/shared/api/wire';
import { mockTransport } from '../../src/shared/mock/transport';

const requestId = 'req_00000000000000000000000000000233';
const reportPath = '/api/v1/dashboard/performance-reports/latest';

test.use({ viewport: { width: 390, height: 900 } });

test.beforeEach(async ({ page }) => {
  const auth = await mockTransport<LoginResponse>('/api/v1/auth/login', 'POST', undefined, requestId);
  await page.addInitScript(({ accessToken, expiresAt, user }) => {
    sessionStorage.setItem('capstone.session.v1', JSON.stringify({ token: accessToken, expiresAt, user }));
  }, auth.data!);

  await page.route('**/api/**', async (route) => {
    const { pathname, search } = new URL(route.request().url());
    if (pathname === '/api/v1/auth/refresh') return route.fulfill({ json: auth });
    expect(route.request().method()).toBe('GET');
    const json = await mockTransport<DashboardEnvelope<DashboardBacktestView>>(pathname + search, 'GET', undefined, requestId);
    if (pathname.startsWith('/api/v1/dashboard/backtests/') && !pathname.endsWith('/latest')) {
      const view = json.data!.view!;
      const strategies = view.strategies.map((row) => ({
        ...row, metrics: { ...row.metrics, cagr: null, mdd: null, sharpe: null, cvar95: null },
      }));
      return route.fulfill({ json: { ...json, data: { ...json.data, view: { ...view, strategies } } } });
    }
    return route.fulfill({ status: json.success ? 200 : 404, json });
  });
});

test('first generation missing stays distinct from a failed read', async ({ page }, info) => {
  let missing = true;
  await page.route('**' + reportPath, async (route) => {
    const code = missing ? 'NOT_FOUND' : 'INTERNAL_ERROR';
    await route.fulfill({
      status: missing ? 404 : 503,
      json: { success: false, data: null, requestId, warnings: [], error: { code, message: code } },
    });
  });
  await page.goto('/report');
  const empty = page.getByText('생성된 누적 성과 보고서가 없습니다');
  await expect(empty).toBeVisible();
  await expect(page.getByText(/불러오기 실패 · NOT_FOUND/)).toHaveCount(0);
  await empty.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('first-report-390.png') });

  missing = false;
  await page.reload();
  await expect(page.getByText('불러오기 실패 · INTERNAL_ERROR')).toBeVisible();
  await expect(page.getByText('생성된 누적 성과 보고서가 없습니다')).toHaveCount(0);
});

test('390px comparison stays within the viewport and failed refresh keeps the old generation', async ({ page }, info) => {
  await page.route('**' + reportPath, async (route) => {
    const json = await mockTransport<OwnerPerformanceReport>(reportPath, 'GET', undefined, requestId);
    return route.fulfill({ json: { ...json, data: {
      ...json.data,
      lastRefreshStatus: 'FAILED_LAST_SUCCESS_PRESERVED',
      lastFailureCode: 'MATERIALIZATION_FAILED',
      lastFailureAt: '2026-09-09T06:00:00Z',
    } } });
  });
  await page.goto('/report');
  const warning = page.getByText(/최근 갱신 실패.*마지막 성공본/);
  await expect(warning).toBeVisible();
  await expect(page.getByText('2026-09-08', { exact: true })).toBeVisible();
  await warning.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('preserved-report-390.png') });

  const comparison = page.locator('[data-contract="report/scenario-capture"] table');
  await expect(comparison.getByRole('row')).toHaveCount(4);
  await expect(comparison.getByRole('columnheader', { name: 'CVaR 95' })).toBeVisible();
  await expect(comparison.getByText('값 없음')).toHaveCount(12);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await comparison.scrollIntoViewIfNeeded();
  const bounds = await comparison.boundingBox();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: info.outputPath('comparison-390.png') });

  await page.reload();
  await expect(warning).toBeVisible();
  await expect(comparison.getByRole('row')).toHaveCount(4);
});
