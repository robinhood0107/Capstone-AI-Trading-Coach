import { readFileSync } from 'node:fs';
import { test, expect, type Response } from '@playwright/test';

const passwordFile = process.env.P1_USER_PASSWORD_FILE;

/**
 * KIS 유량 제한으로 503 이 정상인 경로.
 *
 * 모의 계좌 REST 는 **1건/초**다(CONTRIBUTING.md 'KIS 호출 유량 불변식'). 스펙 여러 개가 잇달아
 * `/` 를 열면 초당 하나뿐인 슬롯을 나눠 쓰게 되어 두 번째부터 503 이 온다. limiter 는
 * 설계상 fail-close 이고 유량 초과는 자동 재시도하지 않는다.
 *
 * 이 단정이 지키려는 것은 "우리 코드가 서버 오류를 만들지 않는다"이므로, 문서화된 rate
 * limiter 가 낸 503 은 세지 않는다. 그 밖의 5xx 는 그대로 실패로 남는다.
 */
const KIS_METERED = /\/api\/v1\/brokerage\/mock\/accounts\/[^/]+\/(balances|buyable|fills)/;

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
    if (navigation === '금융 Agent') {
      await expect(page.getByRole('heading', { name: '금융 지식 라이브러리' })).toBeVisible();
      await expect(page.getByText('KIS Open API 소개')).toHaveCount(0);
      await expect(page.getByText('ECOS StatisticSearch DevGuide locator')).toHaveCount(0);
    }
  }

  await expect(page.getByText('LightGBM')).toHaveCount(0);
  await page.getByRole('tab', { name: '백테스트 리포트' }).click();
  await expect(page.getByRole('heading', { name: '백테스트 리포트' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Baseline / Guide / Strict 비교' })).toBeVisible();
  await expect(page.getByText('이 기간 최고 관측값')).toBeVisible();
  await expect(page.getByText(/비용 반영 수익률입니다. 세 시나리오를 같은 DB 입력과 조건으로 계산했습니다./)).toBeVisible();
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
    apiResponses
      .filter((response) => response.status() >= 500)
      .filter((response) => !KIS_METERED.test(new URL(response.url()).pathname))
      .map((response) => response.url()),
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
  await expect(page.getByRole('heading', { name: '금융 지식 라이브러리' })).toBeVisible();
  await expect(page.getByText('132030 금선물 ETF의 선물·환헤지·롤오버 경계')).toBeVisible();
  await expect(page.getByText('KIS Open API 소개')).toHaveCount(0);

  // 저장된 답변이 실제로 펼쳐지고 본문이 보이는지를 본다. 특정 질문 문자열에 묶으면
  // 그 답변은 어디에서도 seed 되지 않으므로, 사람이 한 번 물어본 이력에 의존하게 된다.
  //
  // 이력은 계정 소유 데이터라 0 건일 수 있다. 그때 화면 전체의 첫 details 를 집으면
  // 라이브러리의 "연구 근거 더 보기"를 저장된 답변으로 착각해 엉뚱하게 실패한다.
  // 0 건이면 빈 상태 문구를, 있으면 펼쳐진 본문을 검증한다 - 둘 다 화면의 계약이다.
  // 이력은 목록 1 회 + 상세 5 회를 이어서 부른다. 기다리지 않으면 스켈레톤 상태를
  // "이력 0 건"으로 잘못 읽는다.
  await expect(page.locator('[data-loading="true"]')).toHaveCount(0, { timeout: 20_000 });
  const historyPanel = page.locator('section', { has: page.getByRole('heading', { name: '최근 질문' }) });
  if ((await historyPanel.count()) === 0) {
    await expect(page.getByText('아직 저장된 질문이 없습니다.')).toBeVisible();
  } else {
    const savedQuestion = historyPanel.locator('details').first();
    await expect(savedQuestion).toBeVisible();
    await savedQuestion.locator('summary').click();
    await expect(savedQuestion.locator('p').first()).toBeVisible();
    expect((await savedQuestion.locator('p').first().textContent())?.length ?? 0).toBeGreaterThan(20);
  }

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
    apiResponses
      .filter((response) => response.status() >= 500)
      .filter((response) => !KIS_METERED.test(new URL(response.url()).pathname))
      .map((response) => response.url()),
  ).toEqual([]);
});
