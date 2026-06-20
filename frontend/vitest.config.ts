import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

// Phase D: 测试基础设施完善 — 覆盖率阈值提升至 50%+
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/tests/setup.ts',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html', 'lcov', 'cobertura'],
      exclude: [
        'node_modules/',
        'src/tests/',
        '**/*.d.ts',
        '**/*.config.*',
        '**/types/**',
        'src/**/index.ts',          // barrel exports
        'src/**/constants.ts',       // 纯常量
        'e2e/**',                    // E2E tests
      ],
      // Phase D 提升: lines 30→55, functions 25→50, statements 30→55, branches 20→45
      thresholds: {
        branches: 45,
        functions: 50,
        lines: 55,
        statements: 55,
      },
      // 100% 覆盖的文件不报告（减少噪音）
      reportThresholds: { each: true },
    },
    include: [
      'src/**/*.{test,spec}.{ts,tsx}',
    ],
    // 并行测试
    pool: 'threads',
    poolOptions: {
      threads: {
        singleThread: false,
        minThreads: 2,
        maxThreads: 4,
      },
    },
    // 超时控制
    testTimeout: 10000,
    hookTimeout: 10000,
    // 失败重试 (CI 环境偶发失败)
    retry: process.env.CI ? 1 : 0,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
