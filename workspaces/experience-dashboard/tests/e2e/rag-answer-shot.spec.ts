import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

test('capture the latest persisted RAG answer', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();

  await page.goto('/');
  await page.getByLabel('아이디').fill('demo-user');
  await page.getByLabel('비밀번호').fill(password);
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/auth/login'),
    page.getByRole('button', { name: '로그인' }).click(),
  ]);

  await page
    .getByRole('navigation', { name: '주요 화면' })
    .getByRole('link', { name: /^금융 Agent/ })
    .click();
  await expect(page.getByRole('heading', { name: '금융 가이드' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '최근 질문' })).toBeVisible();
  const first = page.locator('details').first();
  await expect(first).toBeVisible();
  await first.locator('summary').click();
  await expect(first.locator('p')).toContainText(/\S+/);
  await page.screenshot({ path: 'test-results/rag-answer.png', fullPage: true });
});
