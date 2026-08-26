import { readFileSync } from 'node:fs';
import { test, expect, type Response } from '@playwright/test';

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

test('live Compose login and primary screens use the Spring API', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();
  const apiResponses: Response[] = [];
  page.on('response', (response) => {
    if (new URL(response.url()).pathname.startsWith('/api/')) apiResponses.push(response);
  });

  await page.goto('/');
  await page.getByLabel('아이디').fill('demo-user');
  await page.getByLabel('비밀번호').fill(password);
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/auth/login'),
    page.getByRole('button', { name: '로그인' }).click(),
  ]);
  await expect(page.getByRole('heading', { name: '오늘 상태' })).toBeVisible();

  const screens = [
    ['내 원칙', '내 투자 원칙'],
    ['주문 검토', '주문 검토'],
    ['모델 비교', '모델 비교'],
    ['백테스트 리포트', '백테스트 리포트'],
    ['금융 가이드', '금융 가이드'],
  ] as const;
  const navRail = page.getByRole('navigation', { name: '주요 화면' });
  for (const [navigation, heading] of screens) {
    await navRail.getByRole('link', { name: new RegExp(`^${navigation}`) }).click();
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }

  expect(apiResponses.some((response) => new URL(response.url()).pathname === '/api/v1/auth/login')).toBe(true);
  expect(apiResponses.length).toBeGreaterThan(1);
  expect(
    apiResponses.filter((response) => response.status() >= 500).map((response) => response.url()),
  ).toEqual([]);
});
