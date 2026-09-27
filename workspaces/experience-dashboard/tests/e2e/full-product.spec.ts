/**
 * FULL 제품 전체 흐름 E2E — 화면과 서버가 서로 다른 말을 하는 부류를 릴리스마다 잡는다.
 *
 * 실행 (배포된 FULL 스택을 가리킨다. 기본값은 없다 - 실수로 운영을 두드리지 않게):
 *   MARS_E2E_BASE_URL=http://localhost:3012 npm run test:e2e:full-product
 *
 * 선택 환경변수
 *   MARS_E2E_OPERATOR_ID / MARS_E2E_OPERATOR_PASSWORD_FILE
 *       운영자(ADMIN) 계정으로도 전 화면과 관리자 콘솔을 확인한다. 비밀번호는 파일에서만 읽는다.
 *   MARS_E2E_ALLOW_OPERATOR_WRITES=1
 *       운영자 계정의 KIS 모의계좌 저장·연결·삭제와 자동운용 정지까지 실행한다. 운영자 데이터를
 *       바꾸므로 **복제 DB(QA 스택)에서만** 켠다. 끄면 운영자 흐름은 읽기와 관리자 콘솔의
 *       테스트 계정 권한 변경만 한다.
 *
 * 매 실행마다 새 일반 사용자를 폼으로 가입시킨다. KIS 값은 형식만 맞춘 가짜 값이라
 * 연결 확인(실제 KIS 잔고조회)은 "KIS가 앱 키 또는 앱 시크릿을 거부했습니다"(409) 또는 KIS 에 닿지
 * 못했다는 503 으로 끝나는 것이 정상이다. Vertex 서비스 계정도 형식만 맞춘 가짜 값이다.
 *
 * 실패 조건: 예상 밖 4xx/5xx API 응답, 화면의 "서버가 예상과 다른 형식",
 * 예상 밖 "이 자료에 접근할 권한이 없습니다", 콘솔 오류, 처리되지 않은 페이지 예외.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { join, resolve } from 'node:path';
import { expect, test, type Page, type Response } from '@playwright/test';

const BASE_URL = process.env.MARS_E2E_BASE_URL;
const OPERATOR_ID = process.env.MARS_E2E_OPERATOR_ID;
const OPERATOR_PASSWORD_FILE = process.env.MARS_E2E_OPERATOR_PASSWORD_FILE;
const OPERATOR_WRITES = process.env.MARS_E2E_ALLOW_OPERATOR_WRITES === '1';

test.skip(!BASE_URL, 'MARS_E2E_BASE_URL 이 없으면 돌지 않는다.');
test.use({ baseURL: BASE_URL, actionTimeout: 20_000 });
test.describe.configure({ mode: 'serial' });

/** src/app 아래 로그인 뒤 화면 전부. 새 page.tsx 를 만들면 여기에도 더한다(아래 테스트가 확인한다). */
const USER_ROUTES = [
  '/',
  '/intro',
  '/rag',
  '/principles',
  '/strategy',
  '/model-evaluation',
  '/backtest',
  '/automation',
  '/order-review',
  '/journal',
  '/report',
  '/settings',
] as const;

const FORBIDDEN_TEXT = ['서버가 예상과 다른 형식', '서버 응답 형식이 계약과 다릅니다'];
const ACCESS_DENIED_TEXT = '이 자료에 접근할 권한이 없습니다';

type ApiFailureRecord = { method: string; path: string; status: number; code: string | null; step: string };
type Expected = { method: string; path: RegExp; status: number };

/** 한 사용자 흐름 동안 API 실패와 콘솔 오류를 모은다. 기대한 업무 거절만 따로 허용한다. */
class Watch {
  step = 'start';
  readonly failures: ApiFailureRecord[] = [];
  readonly consoleErrors: string[] = [];
  readonly expected: Expected[] = [];
  readonly log: string[] = [];

  constructor(private readonly page: Page) {
    page.on('console', (message) => {
      if (message.type() !== 'error') return;
      const text = message.text();
      // 브라우저가 4xx/5xx 응답마다 남기는 줄은 API 목록에서 따로 판정한다.
      if (text.startsWith('Failed to load resource')) return;
      this.consoleErrors.push(`[${this.step}] ${text}`);
    });
    page.on('pageerror', (error) => this.consoleErrors.push(`[${this.step}] pageerror ${error.message}`));
    page.on('response', (response) => void this.record(response));
    // 동의 전의 유효 동의 조회는 v2 계약상 409 EXTERNAL_AI_CONSENT_REQUIRED 다. 화면은 "동의 필요"로 그린다.
    this.expect('GET', /^\/api\/v2\/rag\/consent$/, 409);
  }

  private async record(response: Response) {
    const url = new URL(response.url());
    if (!url.pathname.startsWith('/api/') || response.status() < 400 || this.step === 'after-logout') return;
    let code: string | null = null;
    try {
      const body = (await response.json()) as { error?: { code?: string }; code?: string };
      code = body.error?.code ?? body.code ?? null;
    } catch {
      code = null;
    }
    const method = response.request().method();
    const allowed = this.expected.some(
      (item) => item.method === method && item.path.test(url.pathname) && item.status === response.status(),
    );
    const entry = { method, path: url.pathname, status: response.status(), code, step: this.step };
    this.log.push(`${allowed ? 'EXPECTED' : 'UNEXPECTED'} ${method} ${url.pathname} ${response.status()} ${code ?? '-'} @${this.step}`);
    if (!allowed) this.failures.push(entry);
  }

  expect(method: string, path: RegExp, status: number) {
    this.expected.push({ method, path, status });
  }

  async assertClean() {
    await this.page.waitForTimeout(300);
    expect.soft(this.failures, `예상 밖 API 실패\n${this.log.join('\n')}`).toEqual([]);
    expect.soft(this.consoleErrors, '콘솔 오류').toEqual([]);
  }
}

function fakeCredential() {
  const alnum = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  const base64 = `${alnum}+/`;
  const pick = (alphabet: string, length: number) =>
    Array.from(randomBytes(length), (byte) => alphabet[byte % alphabet.length]).join('');
  // 실제 KIS 발급값과 같은 모양(appKey 36자, appSecret 180자, 계좌 10자리)의 명백한 가짜 값.
  return { appKey: `PSqa${pick(alnum, 32)}`, appSecret: pick(base64, 180), accountNo: `5${pick('0123456789', 9)}` };
}

async function visible(page: Page, name: string | RegExp, timeout = 20_000) {
  await expect(page.getByText(name).first()).toBeVisible({ timeout });
}

/**
 * FULL 의 bearer 토큰은 JS 메모리에만 있어 새로고침하면 사라진다(session.ts). 그래서 로그인 뒤에는
 * page.goto 를 쓰지 않고 화면 안의 링크(없으면 Next 라우터)로 옮겨 다닌다.
 */
async function go(page: Page, route: string) {
  const link = page.locator(`a[href="${route}"]`).first();
  if (await link.count()) {
    await link.click();
  } else {
    await page.evaluate((target) => {
      const next = (window as unknown as { next?: { router?: { push(path: string): void } } }).next;
      if (!next?.router) throw new Error(`no in-app route to ${target}`);
      next.router.push(target);
    }, route);
  }
  await page.waitForURL((url) => url.pathname === route, { timeout: 20_000 });
}

async function settle(page: Page) {
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => undefined);
}

async function assertNoContractText(page: Page, route: string, allowAccessDenied = false) {
  const body = await page.locator('body').innerText();
  for (const text of FORBIDDEN_TEXT) expect.soft(body, `${route}: "${text}"`).not.toContain(text);
  if (!allowAccessDenied) expect.soft(body, `${route}: 권한 없음 문구`).not.toContain(ACCESS_DENIED_TEXT);
  expect.soft(body, `${route}: 로그아웃되면 안 된다`).not.toContain('로그인이 필요합니다');
}

async function visitAll(page: Page, watch: Watch, routes: readonly string[]) {
  for (const route of routes) {
    watch.step = `visit ${route}`;
    await go(page, route);
    await settle(page);
    await expect(page.locator('#full-auth-identifier'), `${route}: 로그인 폼으로 튕기면 안 된다`).toHaveCount(0);
    await assertNoContractText(page, route);
  }
}

async function loginWithForm(page: Page, identifier: string, password: string) {
  await page.goto('/');
  await page.getByRole('tab', { name: '로그인' }).click();
  await page.locator('#full-auth-identifier').fill(identifier);
  await page.locator('#full-auth-password').fill(password);
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await expect(page.locator('#full-auth-identifier')).toHaveCount(0, { timeout: 20_000 });
}

async function logout(page: Page, watch: Watch) {
  watch.step = 'logout';
  await go(page, '/');
  await settle(page);
  // 로그아웃이 세션을 끊는 순간 이미 날아간 조회는 401 이나(현황 조회는 actor 범위를 잃어) 503 으로
  // 끝난다. 끊긴 뒤의 새 요청이 401 인 것은 따로 확인했다. 떠나는 화면의 경합이라 여기서부터는 세지 않는다.
  watch.step = 'after-logout';
  await page.getByRole('button', { name: /로그아웃/ }).click();
  await expect(page.locator('#full-auth-identifier')).toBeVisible({ timeout: 20_000 });
}

/** 저장 → 연결 확인(가짜 키라 503) → 삭제. 서버 오류·401 로 끝나면 실패다. */
async function credentialRoundTrip(page: Page, watch: Watch) {
  watch.step = 'credential save';
  await go(page, '/settings');
  await settle(page);
  const credential = fakeCredential();
  await page.getByLabel('KIS_MOCK App Key').fill(credential.appKey);
  await page.getByLabel('KIS_MOCK App Secret').fill(credential.appSecret);
  await page.getByLabel(/모의계좌번호/).fill(credential.accountNo);
  await page.getByRole('button', { name: /모의계좌 정보 (저장|교체)/ }).click();
  await visible(page, '암호화해 저장했습니다');
  await visible(page, `계좌 끝 4자리 ${credential.accountNo.slice(-4)}`);

  watch.step = 'credential connect';
  // 연결 확인은 실제 KIS 잔고조회다. 가짜 키는 KIS 가 거부하고(409 APP_KEY_REJECTED), KIS 에 닿지 못하면
  // 503 이다. 어느 쪽이든 이유가 한국어로 보이고 "저장됨"에 머문다 - 일반 충돌 문구는 실패다.
  watch.expect('POST', /^\/api\/v1\/brokerage\/mock\/credential\/connect$/, 409);
  watch.expect('POST', /^\/api\/v1\/brokerage\/mock\/credential\/connect$/, 503);
  await page.getByRole('button', { name: /읽기 연결 (다시 )?확인/ }).click();
  await visible(
    page,
    /KIS가 앱 키 또는 앱 시크릿을 거부했습니다|KIS가 이 계좌의 잔고조회를 거부했습니다|KIS 모의투자 서버에 연결하지 못했습니다|KIS 호출 한도에 걸렸습니다|증권 연동 서비스에 연결하지 못했습니다/,
    60_000,
  );
  await expect(page.getByText('다른 변경과 충돌했습니다')).toHaveCount(0);
  await visible(page, '연결 확인 전 · 자동주문 시작 전');

  watch.step = 'credential delete';
  await page.getByRole('button', { name: '연결 해제' }).click();
  await visible(page, '모의계좌 연결 정보를 삭제했습니다.');
  await visible(page, '등록된 모의계좌 정보가 없습니다.');
}

/** 형식만 맞춘 가짜 서비스 계정 JSON. Google 이 받아 주지 않는 키라 과금·호출이 생기지 않는다. */
function fakeServiceAccountJson() {
  const keyId = randomBytes(8).toString('hex');
  const { privateKey } = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
    publicKeyEncoding: { type: 'spki', format: 'pem' },
  });
  return {
    keyId,
    json: JSON.stringify({
      type: 'service_account',
      project_id: 'mars-e2e-dummy',
      private_key_id: keyId,
      private_key: privateKey,
      client_email: 'e2e@mars-e2e-dummy.iam.gserviceaccount.com',
      client_id: '0',
      token_uri: 'https://oauth2.googleapis.com/token',
    }),
  };
}

/**
 * 설정의 "내 Vertex 키": 등록 → 등록됨(키 ID 끝 네 글자) → AI 검토가 내 키로 불린다는 문장 → 삭제 →
 * 공용 경로 문장. 키 원문은 다시 보이지 않는다.
 */
async function ownVertexRoundTrip(page: Page, watch: Watch) {
  watch.step = 'own vertex save';
  await go(page, '/settings');
  await settle(page);
  await visible(page, '내 Vertex 키 · 자동매매 AI 검토');
  await visible(page, '내 Vertex 서비스 계정이 없습니다.');
  const sa = fakeServiceAccountJson();
  await page.getByLabel(/Vertex 서비스 계정 키 JSON/).fill(sa.json);
  await page.getByRole('button', { name: '저장', exact: true }).last().click();
  await visible(page, '저장했습니다. 다음 자동매매 시작(무장)부터 적용됩니다.');
  await visible(page, `내 Vertex 서비스 계정 등록됨 (키 ID …${sa.keyId.slice(-4)})`);
  await visible(page, 'AI 검토는 내 Vertex 서비스 계정(내 Google Cloud 프로젝트)으로 호출됩니다.');
  await expect(page.getByText(sa.json.slice(20, 60))).toHaveCount(0);

  watch.step = 'own vertex reject api key';
  await page.getByLabel(/Vertex 서비스 계정 키 JSON/).fill('AIzaSyFAKEFAKEFAKEFAKEFAKE');
  await visible(page, /JSON 형식이 아닙니다/);
  await page.getByLabel(/Vertex 서비스 계정 키 JSON/).fill('');

  watch.step = 'own vertex delete';
  await page.getByLabel('등록한 서비스 계정 지우기').check();
  await page.getByRole('button', { name: '저장', exact: true }).last().click();
  await visible(page, '저장했습니다. 다음 자동매매 시작(무장)부터 적용됩니다.');
  await visible(page, '내 Vertex 서비스 계정이 없습니다.');
  await visible(page, /AI 검토는 서비스 운영자의 공용 Vertex로 호출됩니다|공용 Vertex 사용이 꺼져 있/);
  await visible(page, /AI 검토 호출 · 오늘 내 키 \d+회 \/ 공용 \d+회/);
}

test('every app page is covered by this spec', () => {
  // src/app/**/page.tsx 를 읽어 목록과 맞춘다. 새 화면이 E2E 밖에 남지 않게 한다.
  const root = resolve('src/app');
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (name === 'page.tsx') found.push(`/${dir.slice(root.length + 1).replace(/\\/g, '/')}`.replace(/\/$/, '') || '/');
    }
  };
  walk(root);
  const covered = new Set<string>([...USER_ROUTES, '/admin', '/auth/complete']);
  expect(found.filter((route) => !covered.has(route))).toEqual([]);
});

test('new USER: signup form, every page, every write, logout', async ({ page }) => {
  test.setTimeout(420_000);
  const watch = new Watch(page);
  page.on('dialog', (dialog) => void dialog.accept());
  const email = `e2e-${Date.now()}-${randomBytes(3).toString('hex')}@example.test`;
  const password = `E2e-${randomBytes(12).toString('hex')}`;

  watch.step = 'signup';
  await page.goto('/');
  await page.getByRole('tab', { name: '회원가입' }).click();
  await page.locator('#full-auth-identifier').fill(email);
  await page.locator('#full-auth-password').fill(password);
  await page.getByRole('button', { name: '가입하기' }).click();
  await expect(page.locator('#full-auth-identifier')).toHaveCount(0, { timeout: 20_000 });
  // 새 계정은 원칙·실행 기록이 없다. 그 빈 상태가 404 로 오는 읽기는 화면이 빈 상태로 그린다.
  watch.expect('GET', /^\/api\/v1\/dashboard\/(risk-results\/latest|performance-reports\/latest|model-evaluations\/latest|backtests\/latest)$/, 404);

  await visitAll(page, watch, USER_ROUTES);

  watch.step = 'admin page denied';
  watch.expect('GET', /^\/api\/v1\/admin\//, 403);
  await go(page, '/admin');
  await settle(page);
  await assertNoContractText(page, '/admin', true);

  watch.step = 'principle create';
  await go(page, '/principles');
  await settle(page);
  await page.locator('#principle-title').fill(`E2E 원칙 ${Date.now()}`);
  await page.getByRole('button', { name: '균형형', exact: true }).click();
  await page.getByRole('button', { name: '이 원칙으로 시작하기' }).click();
  await visible(page, '현재 버전');

  watch.step = 'principle edit';
  const firstValue = page.getByLabel(/ 값$/).first();
  if (await firstValue.count()) {
    const current = Number(await firstValue.inputValue());
    await firstValue.fill(String(Number.isFinite(current) ? current + 1 : 1));
    await page.getByRole('button', { name: '변경 사항 저장' }).click();
    await visible(page, 'v2');
  }

  watch.step = 'journal create';
  await go(page, '/journal');
  await settle(page);
  const journalTitle = `E2E 기록 ${Date.now()}`;
  await page.getByLabel('학습일지 제목').fill(journalTitle);
  await page.getByLabel('학습일지 내용').fill('화면과 서버 계약을 확인한다.');
  await page.getByLabel('학습일지 태그').fill('e2e, 계약');
  await page.getByRole('button', { name: '기록 저장' }).click();
  await visible(page, journalTitle);

  watch.step = 'journal edit+delete';
  await page.getByText(journalTitle).first().click();
  await page.getByLabel('학습일지 내용').fill('수정한 내용');
  await page.getByRole('button', { name: '수정 저장' }).click();
  // 저장이 끝나면 선택이 풀린다. 다시 골라 삭제한다.
  await expect(page.getByRole('button', { name: '기록 저장' })).toBeVisible();
  await page.getByRole('button', { name: new RegExp(`${journalTitle} 수정한 내용`) }).first().click();
  const deleteButton = page.getByRole('button', { name: '삭제', exact: true });
  await expect(deleteButton).toBeEnabled();
  await deleteButton.click();
  await page.getByRole('button', { name: '삭제 확인' }).click();
  await visible(page, '삭제했습니다.');

  watch.step = 'kill switch';
  await go(page, '/automation');
  await settle(page);
  await page.getByRole('button', { name: '내 주문 즉시 중지' }).click();
  await visible(page, '내 주문 중지 해제');
  await page.getByRole('button', { name: '내 주문 중지 해제' }).click();
  await visible(page, '내 주문 즉시 중지');
  // 전역 중지는 관리자 전용이다. 일반 사용자에게 버튼이 보이면 안 된다.
  await expect(page.getByRole('button', { name: '전체 주문 즉시 중지' })).toHaveCount(0);

  watch.step = 'automation policy';
  const stopLoss = page.getByLabel('손절률');
  await expect(stopLoss).toBeEditable();
  // 새 계정은 정책이 없다. 빠른 선택값과 금액을 채워 저장한다.
  await page.getByRole('button', { name: /^균형 -5%/ }).click();
  await page.getByLabel(/최대 자동운용 금액/).fill('1000000');
  await page.getByRole('button', { name: '정책 저장' }).click();
  await visible(page, '자동운용 정책을 새 버전으로 저장했습니다.');

  watch.step = 'automation reinvestment policy';
  const reinvest = page.getByLabel('전환 이후 확정 순손익을 다음 세션 운용자금에 반영한다');
  await expect(reinvest).toBeEnabled();
  await reinvest.setChecked(false);
  await page.getByRole('button', { name: '재투자 설정 저장' }).click();
  await visible(page, '재투자 설정을 다음 거래 세션 정책으로 저장했습니다.');
  await expect(page.getByText(/^v1 ·/)).toBeVisible();
  await reinvest.setChecked(true);
  await page.getByRole('button', { name: '재투자 설정 저장' }).click();
  await visible(page, '재투자 설정을 다음 거래 세션 정책으로 저장했습니다.');
  await expect(page.getByText(/^v2 ·/)).toBeVisible();
  // 인증된 KIS 모의계좌가 없으므로 시작은 막혀 있어야 한다(첫 업무 거절).
  const start = page.getByRole('button', { name: '자동운용 시작' });
  await expect(start).toBeDisabled();

  await credentialRoundTrip(page, watch);
  await ownVertexRoundTrip(page, watch);

  watch.step = 'rag consent';
  await go(page, '/rag');
  await settle(page);
  const grant = page.getByRole('button', { name: '동의', exact: true });
  await expect(grant).toBeEnabled();
  await grant.click();
  await visible(page, '동의 완료');
  watch.step = 'rag ask';
  // Vertex 가 닫혀 있으면 서버는 503 RAG_UNAVAILABLE 을 돌려준다. 그 경우도 화면은 형식 오류 없이 끝나야 한다.
  watch.expect('POST', /^\/api\/v2\/rag\/ask$/, 503);
  await page.locator('#rag-question').fill('분산 투자는 왜 위험을 줄이나요?');
  await page.getByRole('button', { name: '물어보기' }).click();
  await expect(page.getByRole('button', { name: '찾는 중' })).toHaveCount(0, { timeout: 95_000 });
  await assertNoContractText(page, '/rag ask');
  const revoke = page.getByRole('button', { name: '철회' });
  await expect(revoke).toBeEnabled();
  await revoke.click();
  await visible(page, '동의 필요');

  await logout(page, watch);
  test.info().annotations.push({ type: 'api-log', description: watch.log.join('\n') || '(no 4xx/5xx)' });
  test.info().annotations.push({ type: 'test-user', description: email });
  await watch.assertClean();
});

test('operator ADMIN: login form, every page, admin console, automation guard', async ({ page, browser }) => {
  test.skip(!OPERATOR_ID || !OPERATOR_PASSWORD_FILE, '운영자 계정 변수가 없다.');
  test.setTimeout(420_000);
  const watch = new Watch(page);
  page.on('dialog', (dialog) => void dialog.accept());

  // 관리자 콘솔 동작은 운영자가 아니라 이번에 만든 테스트 계정에만 건다.
  const targetContext = await browser.newContext({ baseURL: BASE_URL });
  const target = await targetContext.newPage();
  const targetEmail = `e2e-target-${Date.now()}@example.test`;
  await target.goto('/');
  await target.getByRole('tab', { name: '회원가입' }).click();
  await target.locator('#full-auth-identifier').fill(targetEmail);
  await target.locator('#full-auth-password').fill(`E2e-${randomBytes(12).toString('hex')}`);
  await target.getByRole('button', { name: '가입하기' }).click();
  await expect(target.locator('#full-auth-identifier')).toHaveCount(0, { timeout: 20_000 });
  await targetContext.close();

  watch.step = 'operator login';
  await loginWithForm(page, OPERATOR_ID!, readFileSync(OPERATOR_PASSWORD_FILE!, 'utf8').trim());
  watch.expect('GET', /^\/api\/v1\/dashboard\/(risk-results\/latest|performance-reports\/latest)$/, 404);
  await visitAll(page, watch, [...USER_ROUTES, '/admin']);

  watch.step = 'automation account history integrity';
  await go(page, '/automation');
  await settle(page);
  await visible(page, '1 / 10');
  await visible(page, '다른 계좌 미대사');
  await visible(page, '격리된 과거 포지션');
  await visible(page, '내부 모의 계좌 이력');
  await expect(page.getByText('0건').first()).toBeVisible();
  await expect(page.getByText('열림 5 · 종료 6 · 실행 70')).toBeVisible();
  await expect(page.getByText(/포지션 0 · 주문 1 · 실행 0/)).toBeVisible();
  await visible(page, /미대사 주문.*확인/);
  await expect(page.getByRole('button', { name: '자동운용 시작' })).toBeDisabled();

  watch.step = 'admin console';
  await go(page, '/admin');
  await settle(page);
  await page.getByPlaceholder('이메일·아이디 검색').fill(targetEmail);
  await page.getByRole('button', { name: '검색' }).click();
  // AI 사용량 표에도 같은 이메일이 나온다. 계정 표 안에서만 찾는다.
  const row = page.locator('section[aria-labelledby="admin-users"] tr', { hasText: targetEmail });
  await expect(row).toHaveCount(1, { timeout: 20_000 });
  await row.getByRole('button', { name: '관리자 지정' }).click();
  await expect(row.getByRole('button', { name: '관리자 해제' })).toBeVisible({ timeout: 20_000 });
  await row.getByRole('button', { name: '관리자 해제' }).click();
  await expect(row.getByRole('button', { name: '관리자 지정' })).toBeVisible({ timeout: 20_000 });
  await row.getByRole('button', { name: '정지', exact: true }).click();
  await expect(row.getByRole('button', { name: '정지 해제' })).toBeVisible({ timeout: 20_000 });
  await row.getByRole('button', { name: '정지 해제' }).click();
  await expect(row.getByRole('button', { name: '관리자 지정' })).toBeVisible({ timeout: 20_000 });

  watch.step = 'admin ai review panel';
  // 관리자는 공용 Vertex 상태와 사용자별 사용량을 보고, 사용자 키는 등록 여부만 본다.
  await visible(page, 'AI 검토 (Vertex)');
  await expect(page.locator('[data-testid="admin-ai-usage-row"]').first()).toBeVisible({ timeout: 20_000 });
  const sharedToggle = page.getByTestId('admin-shared-vertex-toggle');
  await expect(sharedToggle).toBeEnabled();
  const wasOn = await sharedToggle.isChecked();
  await sharedToggle.click();
  await visible(page, wasOn ? '공용 Vertex 사용을 껐습니다.' : '공용 Vertex 사용을 허용했습니다.');
  await sharedToggle.click();
  await visible(page, wasOn ? '공용 Vertex 사용을 허용했습니다.' : '공용 Vertex 사용을 껐습니다.');
  await expect(sharedToggle).toBeChecked({ checked: wasOn });

  watch.step = 'admin service limits';
  const limitsPanel = page.locator('section[aria-labelledby="admin-limits"]');
  const signupLimit = limitsPanel.getByLabel(/가입자 상한/);
  const automationLimit = limitsPanel.getByLabel(/동시 자동운용 상한/);
  const signupLimitBefore = await signupLimit.inputValue();
  const automationLimitBefore = await automationLimit.inputValue();
  await limitsPanel.getByRole('button', { name: '저장', exact: true }).click();
  await visible(page, '상한을 저장했습니다. 이미 가입했거나 자동운용 중인 사용자는 영향을 받지 않습니다.');
  await expect(signupLimit).toHaveValue(signupLimitBefore);
  await expect(automationLimit).toHaveValue(automationLimitBefore);

  watch.step = 'admin global kill switch';
  await go(page, '/automation');
  await settle(page);
  const globalStop = page.getByRole('button', { name: '전체 주문 즉시 중지' });
  await expect(globalStop).toBeEnabled();
  await globalStop.click();
  const globalResume = page.getByRole('button', { name: '전역 중지 해제' });
  await expect(globalResume).toBeVisible();
  await expect(page.getByText(/시스템 전체 중지 작동 중/)).toBeVisible();
  await globalResume.click();
  await expect(page.getByRole('button', { name: '전체 주문 즉시 중지' })).toBeVisible();

  if (OPERATOR_WRITES) {
    watch.step = 'operator automation guard';
    await go(page, '/automation');
    await settle(page);
    await expect(page.getByRole('button', { name: /^자동운용 (정지|시작)$/ }).first()).toBeVisible({ timeout: 20_000 });
    const armed = await page.getByRole('button', { name: '자동운용 정지' }).count();
    if (armed) {
      // 자동운용이 켜진 채로 키를 바꾸려 하면 이유와 해제 경로가 보여야 한다.
      watch.expect('PUT', /^\/api\/v1\/brokerage\/mock\/credential$/, 409);
      await go(page, '/settings');
      await settle(page);
      const credential = fakeCredential();
      await page.getByLabel('KIS_MOCK App Key').fill(credential.appKey);
      await page.getByLabel('KIS_MOCK App Secret').fill(credential.appSecret);
      await page.getByLabel(/모의계좌번호/).fill(credential.accountNo);
      await page.getByRole('button', { name: /모의계좌 정보 (저장|교체)/ }).click();
      await visible(page, '자동매매가 켜져 있어 계좌 정보를 바꿀 수 없습니다. 자동매매를 먼저 해제하세요.');
      await page.getByRole('link', { name: '자동매매 화면에서 해제하기' }).click();
      await page.waitForURL(/\/automation$/);
      await settle(page);
      await page.getByRole('button', { name: '자동운용 정지' }).click();
      await page.getByRole('button', { name: '정지 확인' }).click();
      await visible(page, '자동운용을 정지했습니다.');
    }

    watch.step = 'operator unlinked-order arm guard';
    // 005930 미대사 주문은 운영자가 격리 후 확인 전 차단하기로 했다. 정지 예약/재시작은
    // DB 통합 테스트에서 확인하고, 브라우저에서는 시작 버튼이 이 실제 blocker 를 설명하는지 본다.
    await go(page, '/automation');
    await settle(page);
    await visible(page, /미대사 주문.*확인/);
    await expect(page.getByRole('button', { name: '자동운용 시작' })).toBeDisabled();
    await ownVertexRoundTrip(page, watch);
  }

  await logout(page, watch);
  test.info().annotations.push({ type: 'api-log', description: watch.log.join('\n') || '(no 4xx/5xx)' });
  await watch.assertClean();
});
