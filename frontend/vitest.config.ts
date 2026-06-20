import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

// Phase D+: 测试覆盖完善 — Vitest 4 兼容配置
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/tests/setup.ts',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html', 'lcov'],
      exclude: [
        'node_modules/',
        'src/tests/',
        '**/*.d.ts',
        '**/*.config.*',
        '**/types/**',
        'src/**/index.ts',
        'src/**/constants.ts',
        'e2e/**',
      ],
      thresholds: {
        branches: 45,
        functions: 50,
        lines: 55,
        statements: 55,
      },
    },
    include: [
      'src/**/*.{test,spec}.{ts,tsx}',
    ],
    // Vitest 4: poolOptions → top-level
    pool: 'threads',
    singleThread: false,
    testTimeout: 10000,
    hookTimeout: 10000,
    retry: process.env.CI ? 1 : 0,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
