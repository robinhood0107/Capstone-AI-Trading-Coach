import { readFileSync } from 'node:fs';
import { test, expect, type ConsoleMessage, type Response } from '@playwright/test';
import type { LoginResponse } from '../../src/shared/api/wire';
import { mockTransport } from '../../src/shared/mock/transport';

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

/**
 * KIS 유량 제한으로 503 이 정상인 경로.
 *
 * 모의 계좌 REST 는 **1건/초**다(CONTRIBUTING.md 'KIS 호출 유량 불변식'). 스펙 여러 개가 잇달아
 * `/` 를 열면 초당 하나뿐인 슬롯을 나눠 쓰게 되어 두 번째부터 503 이 온다. limiter 는
 * 설계상 fail-close 이고 유량 초과는 자동 재시도하지 않는다.
 *
 * 이 단정이 지키려는 것은 "우리 코드가 서버 오류를 만들지 않는다"이므로, 문서화된 rate
 * limiter 가 낸 503 은 세지 않는다. 그 밖의 5xx 는 그대로 실패로 남는다.
 */


test('live Compose control screens keep their buttons visible', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();
  const failed: string[] = [];
  page.on('response', (response: Response) => {
    const { pathname } = new URL(response.url());
    if (!pathname.startsWith('/api/')) return;
    if (response.status() < 500) return;
    // 유량 제한도 이번 E2E의 연결 성공으로 세지 않는다.
    // 서버 오류는 실제 원인을 확인하고, 호출 간격은 테스트 흐름에서 조절한다.
    failed.push(`${response.status()} ${pathname}`);
  });

  /**
   * "Failed to load resource" 는 경로를 담지 않아 어느 요청인지 알 수 없다. 그 종류는 위
   * `response` 훅이 이미 상태코드로 판정하므로 여기서 두 번 세지 않는다. 그 밖의 콘솔
   * 에러(하이드레이션 불일치, React 경고, 잡히지 않은 예외)는 그대로 실패로 남는다.
   */
  const consoleErrors: string[] = [];
  page.on('console', (message: ConsoleMessage) => {
    if (message.type() !== 'error') return;
    if (message.text().startsWith('Failed to load resource')) return;
    consoleErrors.push(message.text());
  });

  await page.goto('/');
  await page.getByLabel('아이디').fill('demo-user');
  await page.getByLabel('비밀번호').fill(password);
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/auth/login'),
    page.getByRole('button', { name: '로그인' }).click(),
  ]);
  await expect(page.getByRole('heading', { name: '오늘 상태' })).toBeVisible();

  const navRail = page.getByRole('navigation', { name: '주요 화면' });
  await navRail.getByRole('link', { name: /^자동운용/ }).click();
  await expect(page.getByRole('heading', { name: '자동운용 설정' })).toBeVisible();
  await expect(page.getByRole('button', { name: '신규 주문 중지' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '정책 저장' })).toBeVisible();
  await expect(page.getByRole('note', { name: '자동운용 지속성' })).toBeVisible();
  await page.getByRole('link', { name: /최근 주문 판정 보기/ }).click();
  await expect(page.getByRole('heading', { name: '주문 검토' })).toBeVisible();

  for (const [navigation, heading] of [
    ['보고서', '보고서 캡처'],
    ['설정', 'Strong LLM'],
  ] as const) {
    await page.getByRole('navigation', { name: '도구' })
      .getByRole('link', { name: new RegExp(`^${navigation}`) })
      .click();
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }

  expect(failed).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

const automationStatusPath = '/api/v3/automation/status';
const personalStopPath = '/api/v2/risk/kill-switch';
const requestId = 'req_00000000000000000000000000000226';
const unavailable = { code: 'INTERNAL_ERROR', message: 'Synthetic service outage' };

for (const failedRead of [automationStatusPath, personalStopPath]) {
  test(`personal stop works when GET ${failedRead} returns 503`, async ({ page }) => {
    let outage = true;
    let stopActive = false;
    let denyRead = false;
    let attempts = 0;
    let releaseWrite = () => {};
    const pendingWrite = new Promise<void>((resolve) => { releaseWrite = resolve; });

    const auth = await mockTransport<LoginResponse>('/api/v1/auth/login', 'POST', undefined, requestId);
    await page.addInitScript(({ accessToken, expiresAt, user }) => {
      sessionStorage.setItem('capstone.session.v1', JSON.stringify({ token: accessToken, expiresAt, user }));
    }, auth.data!);

    // FULL restores via the refresh endpoint, while LOCAL restores from sessionStorage.
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const { pathname, search } = new URL(request.url());
      const method = request.method();
      const failure = (status: number, error: { code: string; message: string }) =>
        route.fulfill({ status, json: { success: false, requestId, warnings: [], data: null, error } });

      if (pathname === personalStopPath && method === 'POST') {
        attempts += 1;
        expect(request.headers()['x-idempotency-key']).toMatch(/^kill-switch\./);
        const active = attempts <= 2;
        expect(request.postDataJSON()).toEqual({ active });
        if (attempts === 1) return failure(503, unavailable);
        await pendingWrite;
        stopActive = active;
      }

      if (method === 'GET' && pathname === failedRead && outage) {
        return failure(503, unavailable);
      }

      if (pathname === personalStopPath) {
        if (denyRead) return failure(403, { code: 'FORBIDDEN', message: 'Synthetic access denial' });
        return route.fulfill({
          json: {
            success: true, requestId, warnings: [], error: null,
            data: {
              active: stopActive, globalActive: false, effectiveActive: stopActive,
              changedAt: '2026-11-27T12:00:00Z',
              reasonClass: stopActive ? 'USER_MANUAL_STOP' : 'INITIAL_STATE',
            },
          },
        });
      }

      if (pathname === '/api/v1/auth/refresh') return route.fulfill({ json: auth });
      const json = await mockTransport(pathname + search, method, undefined, requestId);
      return route.fulfill({ status: json.success ? 200 : 404, json });
    });

    await page.goto('/automation');
    const stop = page.getByRole('button', { name: '내 주문 즉시 중지' });
    const resume = page.getByRole('button', { name: '내 주문 중지 해제' });
    const applied = page.getByText('내 주문 중지를 적용했습니다.', { exact: true });
    await expect(page.getByText('불러오기 실패 · INTERNAL_ERROR', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '현재 자동운용 상태' })).toHaveCount(
      failedRead === automationStatusPath ? 0 : 1,
    );
    await expect(stop).toBeEnabled();
    await expect(resume).toHaveCount(0);

    await stop.click();
    await expect(page.getByRole('alert').filter({ hasText: '요청이 안전하게 중단됐습니다.' })).toBeVisible();
    await expect(applied).toHaveCount(0);
    await expect(page.getByText('내 주문 중지 · 작동 중')).toHaveCount(0);

    await stop.click();
    await expect.poll(() => attempts).toBe(2);
    await expect(stop).toBeDisabled();
    await stop.dispatchEvent('click');
    await expect(applied).toHaveCount(0);
    await expect(resume).toHaveCount(0);
    releaseWrite();
    await expect(applied).toBeVisible();
    if (failedRead === personalStopPath) await expect(resume).toHaveCount(0);

    outage = false;
    const focus = () => page.evaluate(() => window.dispatchEvent(new Event('focus')));
    const recovered = page.waitForResponse((response) =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === failedRead && response.ok());
    await focus();
    await recovered;
    await expect(page.getByRole('heading', { name: '현재 자동운용 상태' })).toBeVisible();
    await expect(resume).toBeEnabled();

    if (failedRead === personalStopPath) {
      outage = true;
      await focus();
      await expect(resume).toHaveCount(0);
      await expect(stop).toBeEnabled();
      outage = false;
      denyRead = true;
      await focus();
      await expect(page.getByText('불러오기 실패 · FORBIDDEN')).toBeVisible();
      await expect(resume).toHaveCount(0);
      denyRead = false;
    }

    await page.reload();
    await expect(page.getByText('내 주문 중지 · 작동 중')).toBeVisible();
    await expect(resume).toBeEnabled();
    await expect(applied).toHaveCount(0);
    expect(attempts).toBe(2);
    await resume.click();
    await expect(page.getByText('내 주문 중지 · 꺼짐', { exact: true })).toBeVisible();
    expect(attempts).toBe(3);
  });
}
