import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  // Team A 인수 스펙은 `./capstone team-a acceptance` 안에서만 돈다. 그 래퍼가
  // provider-free 모드를 강제하고 fixture 를 seed 했다가 restore 한다. 래퍼 밖에서 돌면
  // seed 없이 실패하면서 finally 의 disarmAutomation 만 남고, roll_schedule 은 control 이
  // ARMED 여야 돌기 때문에 다음 세션이 조용히 사라진다. 전용 config 로만 실행한다.
  testIgnore: 'team-a-backend-acceptance.spec.ts',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: process.env.P1_DASHBOARD_URL ?? 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Desktop Chrome'],
    launchOptions: process.env.P1_CHROMIUM_PATH
      ? { executablePath: process.env.P1_CHROMIUM_PATH }
      : undefined,
  },
});
