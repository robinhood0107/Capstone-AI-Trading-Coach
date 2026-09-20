import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';

const routes = [
  { route: '/', heading: '오늘 상태', apis: ['/api/v1/risk/portfolio', '/api/v3/automation/status'], db: ['read_portfolio_risk_snapshot', 'p1_read_automation_status_v3'] },
  { route: '/intro', heading: '여기까지가 소개입니다', apis: [], db: [] },
  { route: '/rag', heading: '금융 가이드', apis: ['/api/v2/rag/world-news', '/api/v2/rag/corpus-status', '/api/v2/rag/consent', '/api/v2/rag/history', '/api/v1/rag/sources'], db: ['read_rag_v2_corpus_status', 'read_world_news_documents_v2', 'read_world_news_collection_status_v2'] },
  { route: '/principles', heading: '내 투자 원칙', apis: ['/api/v1/principle-presets', '/api/v1/principles', '/api/v3/automation/status'], db: ['list_principle_presets', 'list_owned_principles'] },
  { route: '/strategy', heading: '모델 비교', apis: ['/api/v1/dashboard/model-evaluations/latest', '/api/v1/dashboard/model-evaluations/{runId}'], db: ['latest_dashboard_artifact_run_authorized', 'read_dashboard_artifact_view_authorized'] },
  { route: '/model-evaluation', heading: '모델 비교', apis: ['/api/v1/dashboard/model-evaluations/latest', '/api/v1/dashboard/model-evaluations/{runId}'], db: ['latest_dashboard_artifact_run_authorized', 'read_dashboard_artifact_view_authorized'] },
  { route: '/backtest', heading: '백테스트 리포트', apis: ['/api/v1/dashboard/backtests/latest', '/api/v1/dashboard/backtests/{runId}'], db: ['latest_dashboard_artifact_run_authorized', 'read_dashboard_artifact_view_authorized'] },
  { route: '/automation', heading: '자동운용 설정', apis: ['/api/v3/automation/status', '/api/v3/automation/runs', '/api/v3/automation/positions', '/api/v2/risk/kill-switch'], db: ['p1_read_automation_status_v3', 'p1_list_automation_runs_v3', 'p1_list_automation_positions_v3'] },
  { route: '/order-review', heading: '주문 검토', apis: ['/api/v1/dashboard/risk-results/recent', '/api/v3/automation/status', '/api/v1/instruments/display'], db: ['recent_dashboard_risk_results_authorized', 'p1_read_automation_status_v3'] },
  { route: '/journal', heading: '학습일지', apis: ['/api/v1/journals'], db: ['list_journals_authorized'] },
  { route: '/report', heading: '보고서 캡처', apis: ['/api/v1/dashboard/performance-reports/latest', '/api/v1/dashboard/risk-results/recent', '/api/v1/dashboard/backtests/latest'], db: ['read_latest_owner_performance_report_authorized_v1', 'recent_dashboard_risk_results_authorized'] },
  { route: '/settings', heading: 'Strong LLM', apis: ['/api/v2/rag/corpus-status'], db: ['read_rag_v2_corpus_status'] },
] as const;

// 장애 주입은 QA 빌드(`--build-arg QA_FAULT_INJECTION=1`)에서만 동작한다. 프로덕션
// 번들에는 스위치를 남기지 않기 때문이다. 어느 빌드를 보고 있는지 실행하는 쪽이 밝히게
// 하고, 프로덕션 빌드에서는 503 복구 칸을 FAIL 이 아니라 NOT_RUN 으로 적는다 - 그렇지
// 않으면 "올바르게 막혀 있다"가 실패로 보고된다.
const faultInjectionBuild = process.env.P1_QA_FAULT_INJECTION === '1';

test('provider-free full 12-screen QA matrix', async ({ page, browser }) => {
  test.setTimeout(300_000);
  const output = process.env.P1_QA_OUTPUT;
  if (!output) throw new Error('P1_QA_OUTPUT is required');
  mkdirSync(output, { recursive: true });

  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const externalOrigins = new Set<string>();
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) externalOrigins.add(url.origin);
  });

  await page.goto('/');
  if (await page.getByLabel('아이디').isVisible().catch(() => false)) {
    await page.getByLabel('아이디').fill('demo-user');
    // fixture 비밀번호를 박아 두면 실제 스택에서는 로그인하지 못해 12화면 전부를
    // 검증할 수 없다. 다른 spec 과 같이 파일에서 읽는다.
    const passwordFile = process.env.P1_USER_PASSWORD_FILE;
    const password = passwordFile
      ? readFileSync(passwordFile, 'utf8').trim()
      : 'fixture-password';
    await page.getByLabel('비밀번호').fill(password);
    await page.getByRole('button', { name: '로그인' }).click();
    await page.getByRole('heading', { name: '오늘 상태' }).waitFor();
  }

  const matrix: Record<string, unknown>[] = [];
  for (const item of routes) {
    const entry: Record<string, unknown> = {
      route: item.route,
      apiCalls: item.apis,
      dbFunctions: item.db,
      login: item.route === '/intro' ? 'NOT_REQUIRED' : 'PASS',
      actualResponse: 'PROVIDER_FREE_MOCK_FIXTURE',
      normalScreen: 'FAIL',
      normalEmpty: 'NOT_RUN_FIXED_NONEMPTY_FIXTURE',
      first503:
        item.apis.length === 0
          ? 'NOT_APPLICABLE_NO_API'
          : faultInjectionBuild
            ? 'FAIL'
            : 'NOT_RUN_PRODUCTION_BUILD_NO_FAULT_SWITCH',
      lastKnownGoodAfter503:
        item.apis.length === 0 ? 'NOT_APPLICABLE_NO_API' : 'NOT_RUN_NO_SCREEN_REFRESH_CONTROL',
      retry:
        item.apis.length === 0
          ? 'NOT_APPLICABLE_NO_API'
          : faultInjectionBuild
            ? 'FAIL'
            : 'NOT_RUN_PRODUCTION_BUILD_NO_FAULT_SWITCH',
      recovery:
        item.apis.length === 0
          ? 'NOT_APPLICABLE_NO_API'
          : faultInjectionBuild
            ? 'FAIL'
            : 'NOT_RUN_PRODUCTION_BUILD_NO_FAULT_SWITCH',
      loadingStuck: 'FAIL',
      consoleError: 'PASS',
      apiValueMatch: 'PASS_MOCK_CONTRACT',
      status: 'FAIL',
    };
    try {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(item.route);
      // networkidle 은 유휴가 오지 않으면 영원히 기다린다. 앞선 spec 이 남긴 연결이나
      // 폴링 하나로 300 초 타임아웃이 나고, 그러면 브라우저가 닫혀 **이후 화면 전부가**
      // 연쇄 실패한다 - 실제로 통과하는 화면까지 FAIL 로 보고된다.
      // 유휴는 편의일 뿐 검증 대상이 아니므로, 짧게 기다리고 안 오면 그냥 진행한다.
      // 화면이 실제로 떴는지는 바로 아래 heading 대기가 판정한다.
      await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
      await page.getByText(item.heading, { exact: false }).first().waitFor({ timeout: 10_000 });
      // AsyncBoundary 의 loading 은 aria-busy 가 아니라 data-loading 스켈레톤이다.
      // 그걸 세지 않으면 아직 불러오는 중인 화면을 PASS 로 보고하게 된다 - 실제로
      // /rag 의 세계 뉴스 패널이 스켈레톤인 채로 캡처돼 값 대조가 FAIL 났다.
      const skeletons = page.locator('[data-loading="true"]');
      await expect(skeletons).toHaveCount(0, { timeout: 20_000 }).catch(() => undefined);
      await page.waitForTimeout(300);
      const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(100);
      const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      const busy = (await page.locator('[aria-busy="true"]').count()) + (await skeletons.count());
      const screenshot = join(output, `${item.route === '/' ? 'root' : item.route.slice(1)}.png`);
      await page.screenshot({ path: screenshot, fullPage: true });
      entry.normalScreen = 'PASS';
      entry.loadingStuck = busy === 0 ? 'PASS' : 'FAIL';
      entry.desktopOverflow = desktopOverflow ? 'FAIL' : 'PASS';
      entry.mobileOverflow = mobileOverflow ? 'FAIL' : 'PASS';
      entry.evidence = screenshot;
      entry.status = busy === 0 && !desktopOverflow && !mobileOverflow ? 'PASS' : 'FAIL';
      if (item.route === '/rag') {
        // 검증 대상은 "발행 시각을 얼마나 믿을 수 있는지 화면이 말하는가"다.
        // 특정 상태 하나를 강제하면 수집된 문서 구성이 바뀔 때마다 깨진다.
        //
        // 화면이 그 말을 하는 방식은 2026-09-16 에 바뀌었다 - 카드마다 같은 문장을
        // 반복하던 것을 시각 앞의 `약` 한 글자와 목록 아래 설명 한 줄로 옮겼다.
        // 성질은 같고 문구만 다르므로 단정도 함께 옮긴다. 대신 하나를 더 본다:
        // **불확실한 시각이 있으면 그 설명이 반드시 화면에 있어야 한다.**
        const newsVisible = await page.getByText('오늘의 세계 뉴스').isVisible();
        const timeShown = await page
          .getByText(/방금|\d+(분|시간|일|주|개월) 전/)
          .first()
          .isVisible()
          .catch(() => false);
        const hasUncertain = await page
          .getByText(/약 (방금|\d+(분|시간|일|주|개월) 전)/)
          .first()
          .isVisible()
          .catch(() => false);
        const explains = hasUncertain
          ? await page.getByText(/발행 시각을 확인하지 못해/).isVisible().catch(() => false)
          : true;
        entry.apiValueMatch = newsVisible && timeShown && explains ? 'PASS' : 'FAIL';
      }
      if (item.route === '/report') {
        entry.apiValueMatch =
          (await page.getByText('누적 성과 보고서').isVisible()) &&
          (await page.getByText(/두 기준을 모두 통과한 후보 없음/).isVisible())
            ? 'PASS'
            : 'FAIL';
      }
      if (item.apis.length > 0 && faultInjectionBuild) {
        const failureContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
        const failurePage = await failureContext.newPage();
        failurePage.on('pageerror', (error) => pageErrors.push(`${item.route}: ${error.message}`));
        try {
          // 실제 스택에서는 가짜 토큰이 401 이라 화면이 오류로 먼저 닫힌다.
          // 로그인해서 얻은 진짜 세션을 그대로 옮겨 심는다.
          await failurePage.addInitScript(
            ({ fault, storedSession }) => {
              if (storedSession) {
                window.sessionStorage.setItem('capstone.session.v1', storedSession);
              }
              window.sessionStorage.setItem('p1-qa-api-fault', JSON.stringify(fault));
            },
            {
              fault: { path: item.apis[0], remaining: item.route === '/automation' ? 2 : 1 },
              storedSession: await page.evaluate(() =>
                window.sessionStorage.getItem('capstone.session.v1'),
              ),
            },
          );
          await failurePage.goto(item.route);
          const retry = failurePage.getByRole('button', { name: /다시 조회|다시 시도|재시도/ }).first();
          await retry.waitFor({ timeout: 10_000 });
          entry.first503 = 'PASS_SCREEN_INJECTED';
          await retry.click();
          await failurePage
            .getByRole('heading', { name: item.heading, exact: false })
            .first()
            .waitFor({ timeout: 10_000 });
          entry.retry = 'PASS_SCREEN_ACTION';
          entry.recovery = 'PASS_SCREEN_RESPONSE';
        } finally {
          await failureContext.close();
        }
      }
    } catch (error) {
      entry.error = error instanceof Error ? error.message : String(error);
    }
    matrix.push(entry);
  }

  const result = {
    browser: 'Playwright Chromium',
    browserPlugin: 'NOT_AVAILABLE',
    mode: 'PROVIDER_FREE_MOCK_FIXTURE',
    build: faultInjectionBuild ? 'QA_FAULT_INJECTION' : 'PRODUCTION_NO_FAULT_SWITCH',
    routes: matrix,
    consoleErrors,
    pageErrors,
    externalOrigins: [...externalOrigins],
    sharedFailureEvidence: [
      'tests/unit/refresh-stability.test.ts',
      'tests/contract/qa-regressions.test.mjs',
    ],
    providerReadOnlySmoke: 'NOT_RUN_EXTERNAL_APPROVAL_REQUIRED',
    passed:
      matrix.every(
        (entry) =>
          entry.status === 'PASS' &&
          entry.apiValueMatch !== 'FAIL' &&
          (entry.first503 === 'NOT_APPLICABLE_NO_API' ||
            entry.first503 === 'NOT_RUN_PRODUCTION_BUILD_NO_FAULT_SWITCH' ||
            (entry.first503 === 'PASS_SCREEN_INJECTED' &&
              entry.retry === 'PASS_SCREEN_ACTION' &&
              entry.recovery === 'PASS_SCREEN_RESPONSE')),
      ) &&
      consoleErrors.length === 0 &&
      pageErrors.length === 0 &&
      externalOrigins.size === 0,
  };
  writeFileSync(join(output, 'result.json'), `${JSON.stringify(result, null, 2)}\n`);
  if (!result.passed) throw new Error('P1 full-screen QA failed; inspect result.json');
});
