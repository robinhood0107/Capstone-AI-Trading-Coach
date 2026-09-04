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
    ['금융 Agent', '금융 가이드'],
    ['내 원칙', '내 투자 원칙'],
    ['전략 검증', '모델 비교'],
  ] as const;
  const navRail = page.getByRole('navigation', { name: '주요 화면' });
  for (const [navigation, heading] of screens) {
    await navRail.getByRole('link', { name: new RegExp(`^${navigation}`) }).click();
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }

  await expect(page.getByText('LightGBM')).toHaveCount(0);
  await page.getByRole('tab', { name: '백테스트 리포트' }).click();
  await expect(page.getByRole('heading', { name: '백테스트 리포트' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Baseline / Guide / Strict 비교' })).toBeVisible();
  await expect(page.getByText('demo_s8_fake_e2e_0001')).toHaveCount(0);

  await navRail.getByRole('link', { name: /^자동운용/ }).click();
  await expect(page.getByRole('heading', { name: '자동운용 설정' })).toBeVisible();
  await page.getByRole('link', { name: /최근 주문 판정 보기/ }).click();
  await expect(page.getByRole('heading', { name: '주문 검토' })).toBeVisible();

  await navRail.getByRole('link', { name: /^학습일지/ }).click();
  await expect(page.getByRole('heading', { name: '학습일지' })).toBeVisible();

  const tools = page.getByRole('navigation', { name: '도구' });
  await tools.getByRole('link', { name: '보고서' }).click();
  await expect(page.getByRole('heading', { name: '보고서 캡처' })).toBeVisible();
  await tools.getByRole('link', { name: '설정' }).click();
  await expect(page.getByRole('heading', { name: 'Strong LLM' })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);

  expect(apiResponses.some((response) => new URL(response.url()).pathname === '/api/v1/auth/login')).toBe(true);
  expect(apiResponses.length).toBeGreaterThan(1);
  expect(
    apiResponses.filter((response) => response.status() >= 500).map((response) => response.url()),
  ).toEqual([]);
});
test('RAG v2 screen gates the question behind consent and renders citations', async ({ page }) => {
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

  const navRail = page.getByRole('navigation', { name: '주요 화면' });
  await navRail.getByRole('link', { name: /^금융 Agent/ }).click();
  await expect(page.getByRole('heading', { name: '금융 가이드' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '외부 처리 동의' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '금융 개념 물어보기' })).toBeVisible();

  await expect(page.getByText(/^(동의 완료|동의 필요)$/)).toBeVisible();

  const revoke = page.getByRole('button', { name: '철회' });
  if (await revoke.isEnabled()) {
    await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v2/rag/consents'),
      revoke.click(),
    ]);
  }
  await expect(page.getByRole('button', { name: '물어보기' })).toBeDisabled();

  const grant = page.getByRole('button', { name: '동의' });
  await expect(grant).toBeEnabled();
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v2/rag/consents'),
    grant.click(),
  ]);

  if (process.env.P1_RAG_LIVE_QUERY === '1') {
    await page.getByLabel('질문').fill('MDD와 Sharpe는 각각 무엇을 말해주나요?');
    await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v2/rag/ask'),
      page.getByRole('button', { name: '물어보기' }).click(),
    ]);
    const explanation = page.getByLabel('생성된 설명');
    await expect(explanation).toBeVisible({ timeout: 20_000 });
    await expect(explanation).toContainText(/\S+/);
  }

  expect(
    apiResponses.filter((response) => response.status() >= 500).map((response) => response.url()),
  ).toEqual([]);
});
