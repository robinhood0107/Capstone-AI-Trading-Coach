import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
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
