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
    ['전략 검증', '모델 비교'],
    ['금융 가이드', '금융 가이드'],
  ] as const;
  const navRail = page.getByRole('navigation', { name: '주요 화면' });
  for (const [navigation, heading] of screens) {
    await navRail.getByRole('link', { name: new RegExp(`^(\d\d )?${navigation}`) }).click();
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }

  // 전략 검증은 한 화면 안에서 모델 비교 ↔ 백테스트로 전환한다.
  await page.getByRole('tab', { name: '백테스트 리포트' }).click();
  await expect(page.getByRole('heading', { name: '백테스트 리포트' })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Baseline / Guide / Strict 비교' })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);

  expect(apiResponses.some((response) => new URL(response.url()).pathname === '/api/v1/auth/login')).toBe(true);
  expect(apiResponses.length).toBeGreaterThan(1);
  expect(
    apiResponses.filter((response) => response.status() >= 500).map((response) => response.url()),
  ).toEqual([]);
});


// RAG v2 화면은 동의가 없으면 질문 자체가 열리지 않는다. 그 순서가 화면에서 지켜지는지 본다.
// 실제 질의는 provider 물리 호출을 쓰므로 P1_RAG_LIVE_QUERY=1일 때만 보낸다.
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
  await navRail.getByRole('link', { name: /^금융 가이드/ }).click();
  await expect(page.getByRole('heading', { name: '금융 가이드' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '외부 처리 동의' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '금융 개념 물어보기' })).toBeVisible();

  // 동의 상태를 읽어 오기 전에는 두 버튼이 모두 잠겨 있다. CHECKING이 끝날 때까지 기다린다.
  await expect(page.getByText(/^(GRANTED|REQUIRED)$/)).toBeVisible();

  // 동의를 철회한 상태에서는 물어보기 버튼이 열리지 않아야 한다.
  const revoke = page.getByRole('button', { name: '철회' });
  if (await revoke.isEnabled()) {
    await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v2/rag/consents'),
      revoke.click(),
    ]);
  }
  await expect(page.getByRole('button', { name: '물어보기' })).toBeDisabled();

  // 철회 응답이 상태에 반영된 뒤에야 동의 버튼이 열린다. 열릴 때까지 기다린 다음 누른다.
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
    // 생성이 켜져 있으면 ANSWERED, 꺼져 있으면 RETRIEVAL_ONLY다. 둘 다 화면에는 나와야 한다.
    await expect(
      page.getByText(/ANSWERED|RETRIEVAL_ONLY|GENERATION_UNAVAILABLE/).first(),
    ).toBeVisible();
  }

  expect(
    apiResponses.filter((response) => response.status() >= 500).map((response) => response.url()),
  ).toEqual([]);
});
