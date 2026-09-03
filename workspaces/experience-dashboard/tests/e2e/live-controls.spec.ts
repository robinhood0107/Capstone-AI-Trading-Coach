import { readFileSync } from 'node:fs';
import { test, expect, type ConsoleMessage, type Response } from '@playwright/test';

/**
 * 남은 조작 화면의 버튼이 실제 스택에서 살아 있는지 본다.
 *
 * live-compose.spec 은 로그인과 읽기 화면 네 개를 덮는다. 조작이 있는 화면 - 자동운용,
 * 주문 검토, 설정, 보고서 - 은 덮지 않아서, 재설계로 버튼이 사라지거나 라벨이 바뀌어도
 * 아무 테스트도 실패하지 않았다. 그 빈자리를 메운다.
 *
 * 버튼을 눌러 상태를 바꾸지는 않는다. arm/disarm 은 실제 자동운용 통제이고 인증된 상태를
 * 테스트가 흔들면 안 된다. 여기서 보는 것은 셋이다.
 *
 *   1. 화면이 서버 값으로 렌더된다 (5xx 가 없고 heading 이 뜬다)
 *   2. 조작 버튼이 존재한다 (재설계가 지우지 않았다)
 *   3. 막힌 버튼이 사라지지 않고 비활성으로 남는다 - 명세가 요구하는 방식이다.
 *      숨기면 왜 막혔는지 알 수 없다.
 */

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

const CONTROL_SCREENS = [
  ['자동운용', '자동운용'],
  ['주문 검토', '주문 검토'],
] as const;

test('live Compose control screens keep their buttons visible', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();
  const failed: string[] = [];
  page.on('response', (response: Response) => {
    if (new URL(response.url()).pathname.startsWith('/api/') && response.status() >= 500) {
      failed.push(`${response.status()} ${new URL(response.url()).pathname}`);
    }
  });

  // 브라우저에서만 드러나는 결함도 잡는다. 실제로 CSP 가 막는 웹폰트 링크를 typecheck·lint·
  // 단위 테스트·build·e2e 가 전부 통과시켰고 콘솔에만 에러가 남았다 - 사람이 브라우저를 열어야
  // 알 수 있는 상태는 게이트가 아니다. 차단된 외부 자원, 하이드레이션 불일치, 잡히지 않은
  // 예외가 이 그물에 걸린다.
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
  for (const [navigation, heading] of CONTROL_SCREENS) {
    await navRail.getByRole('link', { name: new RegExp(`^(\\d\\d )?${navigation}`) }).click();
    await expect(page.getByRole('heading', { name: heading })).toBeVisible();
  }

  // 자동운용 - 시작과 중단은 controlState 로 하나만 렌더된다(AutomationView:338-356).
  // 그러니 둘 다 요구하지 않고 정확히 하나가 있는지 본다. 어느 쪽인지는 서버가 정한다.
  // 확인하려는 것은 "재설계가 조작 버튼을 지우지 않았다"이지 arm 여부가 아니다.
  await navRail.getByRole('link', { name: /^자동운용/ }).click();
  await expect(page.getByRole('heading', { name: '자동운용' })).toBeVisible();
  const controls = page.getByRole('button', { name: /자동운용 시작|신규 주문 중지/ });
  await expect(controls).toHaveCount(1);
  await expect(page.getByRole('button', { name: '정책 저장' })).toBeVisible();
  // 재기동 지속 안내. 지금 상태가 재기동을 넘기는지 사람이 화면에서 알아야 한다.
  await expect(page.getByRole('note', { name: '자동운용 지속성' })).toBeVisible();

  // 보조 화면도 열린다.
  for (const [navigation, heading] of [
    ['보고서 캡처', '보고서'],
    ['설정', 'Strong LLM'],
  ] as const) {
    await page.getByRole('navigation', { name: '주요 화면' })
      .getByRole('link', { name: new RegExp(`^${navigation}`) })
      .click();
    await expect(page.getByRole('heading', { name: new RegExp(heading) })).toBeVisible();
  }

  expect(failed).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
