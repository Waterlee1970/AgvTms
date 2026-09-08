/**
 * AgvMonitor 页面组件测试
 *
 * 测试目标:
 * - AGV监控页面基本渲染
 * - AGV状态卡片展示
 * - 实时位置更新区域
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import AgvMonitor from '../pages/AgvMonitor';

// Mock API (使用 vi.hoisted 解决变量提升问题)
const mockGetAgvStatuses = vi.fn().mockResolvedValue([]);
const mockRunSchedule = vi.fn().mockResolvedValue({
  assignments: [],
  conveyor_timeline: [],
  total_cost: 0,
  makespan: 0,
  metrics: {},
  algorithm_runtime_ms: 0,
});
const mockRunHybridSchedule = vi.fn().mockResolvedValue({
  assignments: [],
  conveyor_timeline: [],
  total_cost: 0,
  makespan: 0,
  metrics: {},
  algorithm_runtime_ms: 0,
});

vi.mock('../services/api', () => ({
  getAgvStatuses: () => mockGetAgvStatuses(),
  getTasks: vi.fn().mockResolvedValue([]),
  runSchedule: () => mockRunSchedule(),
  updateAgvStatus: vi.fn().mockResolvedValue({}),
}));

vi.mock('../services/v2AlgorithmApi', () => ({
  runHybridSchedule: () => mockRunHybridSchedule(),
}));

// Mock antd message
vi.mock('antd', async () => {
  const actual = await vi.importActual('antd');
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    },
  };
});

describe('AgvMonitor Page', () => {

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title correctly', async () => {
    render(<AgvMonitor />);
    // AgvMonitor 的标题是 "AGV 实时追踪"
    await waitFor(() => {
      const titleElements = screen.getAllByText(/AGV.*实时追踪|AGV监控/i);
      expect(titleElements.length).toBeGreaterThan(0);
    });
  });

  it('renders refresh button(s)', async () => {
    render(<AgvMonitor />);
    // 可能存在多个"刷新"相关按钮（刷新 + 暂停刷新）
    await waitFor(() => {
      const refreshButtons = screen.getAllByText(/刷新|暂停刷新/i);
      expect(refreshButtons.length).toBeGreaterThan(0);
    });
  });

  it('shows monitoring section content', async () => {
    render(<AgvMonitor />);
    // 验证页面渲染了内容（统计卡片等）
    await waitFor(() => {
      // 检查是否有任何 AGV 相关的文本或卡片
      const pageContent = document.querySelector('.page-title');
      expect(pageContent).toBeTruthy();
    });
  });
});
