// AGV-TMS Playwright E2E Test Configuration
//
// Phase D: E2E 测试框架集成
// 运行方式:
//   npx playwright install     // 首次安装浏览器
//   npx playwright test         // 运行全部 E2E 测试
//   npx playwright test --ui    // UI 模式调试

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  failFast: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        headless: true,
        locale: 'zh-CN',
        timezoneId: 'Asia/Shanghai',
        actionTimeout: 10_000,
        navigationTimeout: 30_000,
      },
    },
    {
      name: 'mobile-webkit',
      use: {
        ...devices['iPhone 13'],
        headless: true,
      },
    },
  ],
  webServer: {
    command: 'npm run dev -- --port 5173',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  outputDir: './test-results',
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: './playwright-report' }],
  ],
});
