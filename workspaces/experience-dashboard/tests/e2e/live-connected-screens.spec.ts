import { readFileSync } from 'node:fs';
import { test, expect, type ConsoleMessage, type Response } from '@playwright/test';

/**
 * 이번에 붙인 화면들이 실제 Spring 에서 실제로 뜨는지 본다.
 *
 * `live-compose` 는 로그인과 읽기 화면 네 개를, `live-controls` 는 조작 버튼의 존재를 덮는다.
 * 새로 붙인 것 — 원칙 만들기·바뀐 기록, 시스템 상태, Kill Switch 조작, 주문 관문, 최근 체결,
 * v3 판단 근거 — 는 어느 쪽도 덮지 않아 재설계나 회귀로 사라져도 아무 테스트도 실패하지 않는다.
 * 그 빈자리를 메운다.
 *
 * **상태를 바꾸지 않는다.** Kill Switch 를 켜거나 주문을 내면 실제 통제와 원장이 움직이므로
 * 여기서는 관문과 버튼이 존재하고 이유가 보이는지까지만 본다.
 *
 * KIS 를 타는 읽기(`balances`, `buyable`)는 모의 계좌가 **1건/초**라
 * (AGENTS.md 'KIS 호출 유량 불변식') 화면을 여러 개 돌면 503 이 정상적으로 섞인다.
 * 그래서 5xx 를 전부 금지하지 않고 **KIS 경유 경로만 예외**로 둔다.
 */

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

/**
 * 서버 오류로 세지 않는 경로.
 *
 * - `balances`, `buyable` — KIS 를 타고 모의 계좌는 1건/초라 화면을 여러 개 돌면 503 이 섞인다.
 * - `fills` — 체결 원장이 없는 계좌는 404 다(`JdbcOrderFillRepository.kt:209`). 빈 상태다.
 */


test('newly connected screens render against the live Spring API', async ({ page }) => {
  test.skip(!passwordFile, 'P1_USER_PASSWORD_FILE must point to the local 0600 demo password file.');
  const password = readFileSync(passwordFile!, 'utf8').trimEnd();

  const serverFailures: string[] = [];
  page.on('response', (response: Response) => {
    const { pathname } = new URL(response.url());
    if (!pathname.startsWith('/api/')) return;
    if (response.status() < 500) return;
    // 유량 제한도 이번 E2E의 연결 성공으로 세지 않는다.
    // 서버 오류는 실제 원인을 확인하고, 호출 간격은 테스트 흐름에서 조절한다.
    serverFailures.push(`${response.status()} ${pathname}`);
  });

  /**
   * 콘솔 에러.
   *
   * "Failed to load resource" 는 경로를 담지 않아 어느 요청인지 알 수 없다. 그 종류는
   * 위 `response` 훅이 이미 상태코드로 판정하므로 여기서 두 번 세지 않는다. 대신
   * **그 밖의 모든 콘솔 에러**(하이드레이션 불일치, React 경고, 잡히지 않은 예외)를 잡는다 —
   * 실제로 이 그물이 중복 key 버그를 잡아냈다.
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

  // ── 내 원칙: 바뀐 기록 (원칙이 있으면 이력, 없으면 원칙 만들기) ──────────────
  await navRail.getByRole('link', { name: /^내 원칙/ }).click();
  await expect(page.getByRole('heading', { name: '내 투자 원칙' })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: '바뀐 기록' }).or(page.getByRole('heading', { name: '원칙 만들기' })),
  ).toBeVisible();

  // ── 자동운용: v3 상태 필드와 Kill Switch 조작 ────────────────────────────────
  await navRail.getByRole('link', { name: /^자동운용/ }).click();
  await expect(page.getByRole('heading', { name: '자동운용 설정' })).toBeVisible();
  for (const label of ['LLM 후보 검토', '시세 이력', '청산 정책 미지정 포지션']) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
  // 청산 기준 — v3 정책이 요구하는 네 값.
  // 이 구역 제목은 주변(예: '종목당 기본 슬롯')과 같은 eyebrow 단락이라 heading role 이 아니다.
  await expect(page.getByText('청산 기준', { exact: true })).toBeVisible();
  for (const label of ['ATR 기간', 'ATR 배수', '최대 보유 기간']) {
    await expect(page.getByLabel(new RegExp(`^${label}`)).first()).toBeVisible();
  }
  // Kill Switch — 켜져 있으면 해제 안내, 꺼져 있으면 켜기 버튼. 정확히 하나다.
  await expect(
    page
      .getByRole('button', { name: '내 주문 즉시 중지' })
      .or(page.getByRole('button', { name: '내 주문 중지 해제' }))
,
  ).toHaveCount(1);
  // v3 실행 목록의 판단 근거 토글
  await expect(page.getByRole('button', { name: '판단 근거 보기' }).first()).toBeVisible();

  // ── 주문 검토: 관문 여섯과 최근 체결 ────────────────────────────────────────
  await page.getByRole('link', { name: /최근 주문 판정 보기/ }).click();
  await expect(page.getByRole('heading', { name: '주문 검토' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '주문 내기' })).toBeVisible();
  // 관문 라벨은 목록 항목 안에서 찾는다. '내용 확인' 은 버튼에도 같은 글자가 있어
  // 화면 전체에서 찾으면 둘이 잡힌다.
  for (const gate of [
    '자동운용 꺼짐',
    'Kill Switch 꺼짐',
    '주문가능금액 충족',
    '원칙 판정 ALLOW',
    '내용 확인',
    '모의계좌 경로',
  ]) {
    await expect(page.getByRole('listitem').filter({ hasText: gate }).first()).toBeVisible();
  }
  // 아직 아무것도 입력하지 않았으므로 확인 단계로 넘어갈 수 없다.
  await expect(page.getByRole('button', { name: '내용 확인' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: '최근 체결' })).toBeVisible();

  // ── 설정: 시스템 상태 ───────────────────────────────────────────────────────
  await page.getByRole('navigation', { name: '도구' }).getByRole('link', { name: '설정' }).click();
  await expect(page.getByRole('heading', { name: '시스템 상태' })).toBeVisible();
  await expect(page.getByText('데이터 신선도')).toBeVisible();

  expect(serverFailures).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
