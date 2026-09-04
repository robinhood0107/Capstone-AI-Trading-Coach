import { readFileSync } from 'node:fs';
import { test, expect, type ConsoleMessage, type Response } from '@playwright/test';

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

test('live Compose control screens keep their buttons visible', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();
  const failed: string[] = [];
  page.on('response', (response: Response) => {
    if (new URL(response.url()).pathname.startsWith('/api/') && response.status() >= 500) {
      failed.push(`${response.status()} ${new URL(response.url()).pathname}`);
    }
  });

  const consoleErrors: string[] = [];
  page.on('console', (message: ConsoleMessage) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
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
