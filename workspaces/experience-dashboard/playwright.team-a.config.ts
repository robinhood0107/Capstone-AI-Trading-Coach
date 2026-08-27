import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'team-a-backend-acceptance.spec.ts',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: [['./tests/e2e/team-a-safe-reporter.ts']],
  use: {
    baseURL: process.env.P1_DASHBOARD_URL ?? 'http://127.0.0.1:3000',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    ...devices['Desktop Chrome'],
    launchOptions: process.env.P1_CHROMIUM_PATH
      ? { executablePath: process.env.P1_CHROMIUM_PATH }
      : undefined,
  },
});
