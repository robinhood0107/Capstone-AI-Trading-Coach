import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';

// 금융 Agent 화면은 세계 뉴스 목록이 대부분을 차지해 전체 캡처가 기능을 보여주지 못한다.
// 질문과 저장된 답변 카드만 잘라 찍는다.
test('capture the finance agent question and answer cards', async ({ page }) => {
  test.setTimeout(180_000);
  const output = process.env.P1_SHOT_OUTPUT;
  test.skip(!output, 'P1_SHOT_OUTPUT must point to a writable directory.');
  mkdirSync(output!, { recursive: true });

  await page.setViewportSize({ width: 1280, height: 1100 });
  await page.goto('/');
  const account = page.getByLabel('아이디').first();
  await account.waitFor({ state: 'attached', timeout: 20_000 });
  await account.scrollIntoViewIfNeeded();
  await account.fill('demo-user');
  const passwordFile = process.env.P1_USER_PASSWORD_FILE;
  const password = passwordFile ? readFileSync(passwordFile, 'utf8').trim() : 'fixture-password';
  await page.getByLabel('비밀번호').first().fill(password);
  await page.getByRole('button', { name: '로그인', exact: true }).first().click();
  await page.getByText('오늘 상태', { exact: false }).first().waitFor({ timeout: 30_000 });

  await page.goto('/rag');
  await expect(page.getByRole('heading', { name: '최근 질문' })).toBeVisible({ timeout: 20_000 });

  // 저장된 답변 두 개를 펼쳐 실제 근거가 보이게 한다.
  const items = page.locator('details');
  const count = Math.min(await items.count(), 2);
  for (let index = 0; index < count; index += 1) {
    await items.nth(index).locator('summary').click();
  }
  await page.waitForTimeout(500);

  const card = page.getByRole('heading', { name: '최근 질문' }).locator('xpath=ancestor::section[1]');
  await expect(card).toBeVisible();
  await card.screenshot({ path: join(output!, 'agent-answers.png') });

  const ask = page.getByRole('heading', { name: '금융 개념 물어보기' }).locator('xpath=ancestor::section[1]');
  if (await ask.isVisible().catch(() => false)) {
    await ask.screenshot({ path: join(output!, 'agent-ask.png') });
  }
});
