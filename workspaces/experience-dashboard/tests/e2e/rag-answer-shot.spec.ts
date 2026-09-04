import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';

// 실제 화면이 rag-always-answer 의도대로 그리는지 눈으로 확인하기 위한 캡처다.
// 실제 Vertex 호출을 쓰므로 P1_RAG_LIVE_QUERY=1 일 때만 돈다.
const passwordFile = process.env.P1_USER_PASSWORD_FILE;

test('capture the RAG answer screen', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  test.skip(process.env.P1_RAG_LIVE_QUERY !== '1', 'live provider call must be approved.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();

  await page.goto('/');
  await page.getByLabel('아이디').fill('demo-user');
  await page.getByLabel('비밀번호').fill(password);
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/auth/login'),
    page.getByRole('button', { name: '로그인' }).click(),
  ]);

  // goto 는 새로고침이라 메모리에만 있는 토큰을 잃는다. 제품과 같은 경로로 이동한다.
  await page
    .getByRole('navigation', { name: '주요 화면' })
    .getByRole('link', { name: /^(\d\d )?금융 가이드/ })
    .click();
  await expect(page.getByRole('heading', { name: '금융 가이드' })).toBeVisible();

  const grant = page.getByRole('button', { name: '동의하고 시작' });
  if (await grant.isVisible().catch(() => false)) {
    await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v2/rag/consents'),
      grant.click(),
    ]);
  }

  await page.getByLabel('질문').fill('최대낙폭(MDD)과 샤프지수는 각각 무엇을 말해주나요?');
  await Promise.all([
    page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v2/rag/ask'),
    page.getByRole('button', { name: '물어보기' }).click(),
  ]);

  const explanation = page.getByLabel('생성된 설명');
  await expect(explanation).toBeVisible({ timeout: 30_000 });
  await expect(explanation).toContainText(/\S+/);
  await page.getByText('근거와 출처 보기').click();
  await page.screenshot({ path: 'test-results/rag-answer.png', fullPage: true });
});
