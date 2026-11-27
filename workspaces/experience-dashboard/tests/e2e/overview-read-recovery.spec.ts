import { expect, test, type Page } from '@playwright/test';
import type { AutomationRunV3, LoginResponse } from '../../src/shared/api/wire';
import { automationPositionsV3, automationRunsV3, mockBalance } from '../../src/shared/mock/fixtures';
import { mockTransport } from '../../src/shared/mock/transport';

const balancePath = `/api/v1/brokerage/mock/accounts/${mockBalance.accountId}/balances`;
const positionsPath = '/api/v3/automation/positions';
const runsPath = '/api/v3/automation/runs';
const requestId = 'req_00000000000000000000000000000227';
const partialRun: AutomationRunV3 = {
  ...automationRunsV3[0]!,
  state: 'PENDING_RECONCILIATION',
  orderQuantity: 45,
  filledQuantity: 27,
  leavesQuantity: 18,
};

async function openOverview(page: Page) {
  const auth = await mockTransport<LoginResponse>('/api/v1/auth/login', 'POST', undefined, requestId);
  const responses = {
    failedPath: '',
    balance: mockBalance,
    positions: automationPositionsV3,
    runs: [partialRun],
  };

  await page.addInitScript(({ accessToken, expiresAt, user }) => {
    sessionStorage.setItem('capstone.session.v1', JSON.stringify({ token: accessToken, expiresAt, user }));
  }, auth.data!);

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const { pathname, search } = new URL(request.url());
    if (pathname === '/api/v1/auth/refresh') return route.fulfill({ json: auth });
    expect(request.method()).toBe('GET');

    if (pathname === responses.failedPath) {
      return route.fulfill({
        status: 503,
        json: {
          success: false, data: null, requestId, warnings: [],
          error: { code: 'INTERNAL_ERROR', message: 'Synthetic read outage' },
        },
      });
    }

    const json = await mockTransport(pathname + search, 'GET', undefined, requestId);
    if (pathname === balancePath) {
      return route.fulfill({ json: { ...json, data: responses.balance } });
    }
    if (pathname === positionsPath) {
      return route.fulfill({ json: { ...json, data: { items: responses.positions } } });
    }
    if (pathname === runsPath) {
      expect(search).toBe('?size=5');
      return route.fulfill({ json: { ...json, data: { items: responses.runs, nextCursor: null } } });
    }
    return route.fulfill({ status: json.success ? 200 : 404, json });
  });

  await page.goto('/');
  await expect(page.getByText('자동매매 5주 · 직접 보유 35주')).toBeVisible();
  await expect(page.getByText('27주 체결 · 18주 대기')).toBeVisible();
  return responses;
}

async function refocus(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
}

test('balance failure keeps automation visible and a recovered empty balance shows zero holdings', async ({ page }) => {
  const responses = await openOverview(page);
  responses.failedPath = balancePath;
  await refocus(page);
  await expect(page.getByText('계좌 상세 잔고를 불러오지 못했습니다.')).toBeVisible();
  await expect(page.getByText('위험 요약에 저장된 평가금액입니다.')).toBeVisible();
  await expect(page.getByText('보유 종목이 없습니다.', { exact: true })).toHaveCount(0);
  await expect(page.getByText('27주 체결 · 18주 대기')).toBeVisible();
  await expect(page.getByText('보유 중', { exact: true })).toBeVisible();

  responses.failedPath = '';
  responses.balance = { ...mockBalance, positions: [] };
  await refocus(page);
  await expect(page.getByText('보유 종목이 없습니다.', { exact: true })).toBeVisible();
  await expect(page.getByText('0개', { exact: true })).toBeVisible();
  await expect(page.getByText('계좌 상세 잔고를 불러오지 못했습니다.')).toHaveCount(0);
  await expect(page.getByText('위험 요약에 저장된 평가금액입니다.')).toHaveCount(0);
});

test('managed holdings failure keeps balances visible and marks ownership unknown until recovery', async ({ page }) => {
  const responses = await openOverview(page);
  responses.failedPath = positionsPath;
  await refocus(page);
  await expect(page.getByText('자동매매 보유 내역을 불러오지 못했습니다.')).toBeVisible();
  await expect(page.getByText('자동매매 관리 여부를 확인할 수 없습니다.')).toHaveCount(2);
  await expect(page.getByText('40주 · 평가', { exact: false })).toBeVisible();
  await expect(page.getByText('직접 보유 · 매매 제외', { exact: true })).toHaveCount(0);
  await expect(page.getByText('자동매매가 관리하는 포지션이 없습니다.')).toHaveCount(0);

  responses.failedPath = '';
  responses.positions = [];
  await refocus(page);
  await expect(page.getByText('자동매매가 관리하는 포지션이 없습니다.')).toBeVisible();
  await expect(page.getByText('직접 보유 · 매매 제외', { exact: true })).toHaveCount(2);

  responses.positions = automationPositionsV3;
  await refocus(page);
  await expect(page.getByText('자동매매 5주 · 직접 보유 35주')).toBeVisible();
  await expect(page.getByText('자동매매 보유 내역을 불러오지 못했습니다.')).toHaveCount(0);
});

test('a partial fill is polled again and a 503 remains distinct from an empty page', async ({ page }) => {
  const responses = await openOverview(page);
  responses.failedPath = runsPath;
  const poll = page.waitForResponse((response) =>
    new URL(response.url()).pathname === runsPath && response.status() === 503,
  );
  await poll;
  await expect(page.getByText('최근 주문 진행 상태를 불러오지 못했습니다.')).toBeVisible();
  await expect(page.getByText('최근 조회한 주문 중 진행 중인 주문이 없습니다.')).toHaveCount(0);
  await expect(page.getByText('27주 체결 · 18주 대기')).toHaveCount(0);
  await expect(page.getByText('자동매매 5주 · 직접 보유 35주')).toBeVisible();

  responses.failedPath = '';
  responses.runs = [{ ...partialRun, filledQuantity: 31, leavesQuantity: 14 }];
  await refocus(page);
  await expect(page.getByText('31주 체결 · 14주 대기')).toBeVisible();
  await expect(page.getByText('최근 주문 진행 상태를 불러오지 못했습니다.')).toHaveCount(0);

  responses.runs = [];
  await refocus(page);
  await expect(page.getByText('최근 조회한 주문 중 진행 중인 주문이 없습니다.')).toBeVisible();
  await expect(page.getByText('31주 체결 · 14주 대기')).toHaveCount(0);
});
