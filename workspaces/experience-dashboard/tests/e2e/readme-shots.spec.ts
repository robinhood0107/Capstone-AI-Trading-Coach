import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';

// README 에 넣을 데스크톱 화면을 찍는다. p1-full-qa 는 모바일 폭으로 마무리하므로
// 그 산출물은 문서에 쓰기 어렵다. 여기서는 1440 폭 그대로 남긴다.
const shots = [
  { route: '/backtest', heading: '백테스트 리포트', name: 'backtest' },
  { route: '/automation', heading: '자동운용 설정', name: 'automation' },
  { route: '/order-review', heading: '주문 검토', name: 'order-review' },
  { route: '/report', heading: '보고서 캡처', name: 'report' },
  { route: '/', heading: '오늘 상태', name: 'overview' },
  { route: '/rag', heading: '금융 가이드', name: 'rag' },
];

test('capture desktop screenshots for the README', async ({ page }) => {
  test.setTimeout(240_000);
  const output = process.env.P1_SHOT_OUTPUT;
  test.skip(!output, 'P1_SHOT_OUTPUT must point to a writable directory.');
  mkdirSync(output!, { recursive: true });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  // 로그인 폼은 소개 랜딩 아래쪽에 있다. 화면에 들어올 때까지 기다린 뒤 채운다.
  const account = page.getByLabel('아이디').first();
  await account.waitFor({ state: 'attached', timeout: 20_000 });
  await account.scrollIntoViewIfNeeded();
  await account.fill('demo-user');
  const passwordFile = process.env.P1_USER_PASSWORD_FILE;
  const password = passwordFile ? readFileSync(passwordFile, 'utf8').trim() : 'fixture-password';
  await page.getByLabel('비밀번호').first().fill(password);
  await page.getByRole('button', { name: '로그인', exact: true }).first().click();
  await page.getByText('오늘 상태', { exact: false }).first().waitFor({ timeout: 30_000 });

  for (const shot of shots) {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(shot.route);
    await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
    await page.getByText(shot.heading, { exact: false }).first().waitFor({ timeout: 15_000 });
    await expect(page.locator('[data-loading="true"]')).toHaveCount(0, { timeout: 20_000 }).catch(() => undefined);
    await page.waitForTimeout(600);
    await page.screenshot({ path: join(output!, `${shot.name}.png`), fullPage: true });
  }
});
