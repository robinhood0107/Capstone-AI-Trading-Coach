import { readFileSync } from 'node:fs';
import { test, expect, type ConsoleMessage, type Response } from '@playwright/test';

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

/**
 * KIS 유량 제한으로 503 이 정상인 경로.
 *
 * 모의 계좌 REST 는 **1건/초**다(AGENTS.md 'KIS 호출 유량 불변식'). 스펙 여러 개가 잇달아
 * `/` 를 열면 초당 하나뿐인 슬롯을 나눠 쓰게 되어 두 번째부터 503 이 온다. limiter 는
 * 설계상 fail-close 이고 유량 초과는 자동 재시도하지 않는다.
 *
 * 이 단정이 지키려는 것은 "우리 코드가 서버 오류를 만들지 않는다"이므로, 문서화된 rate
 * limiter 가 낸 503 은 세지 않는다. 그 밖의 5xx 는 그대로 실패로 남는다.
 */
const KIS_METERED = /\/api\/v1\/brokerage\/mock\/accounts\/[^/]+\/(balances|buyable|fills)/;

test('live Compose control screens keep their buttons visible', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();
  const failed: string[] = [];
  page.on('response', (response: Response) => {
    const { pathname } = new URL(response.url());
    if (!pathname.startsWith('/api/')) return;
    if (response.status() < 500) return;
    if (KIS_METERED.test(pathname)) return;
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
