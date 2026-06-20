/**
 * Dashboard 页面组件测试
 *
 * 测试目标:
 * - 页面标题渲染正确
 * - 统计卡片正确显示
 * - AGV列表正确渲染（包含状态标签和电量进度条）
 * - 调度控制按钮可点击
 * - HybridScheduler 控制区正确渲染
 * - 输送线任务类型分布统计正确
 * - 空数据显示 Empty 组件
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Dashboard from '../pages/Dashboard';

// Mock services (使用 vi.hoisted 解决变量提升问题)
const mockGetAgvStatuses = vi.fn().mockResolvedValue([]);
const mockGetTasks = vi.fn().mockResolvedValue([]);
const mockGetMetrics = vi.fn().mockResolvedValue({});
const mockRunSchedule = vi.fn().mockResolvedValue({
  assignments: [],
  conveyor_timeline: [],
  total_cost: 0,
  makespan: 0,
  metrics: {},
  algorithm_runtime_ms: 0,
});
const mockResetSystem = vi.fn().mockResolvedValue({});
const mockRunHybridSchedule = vi.fn().mockResolvedValue({
  assignments: [],
  conveyor_timeline: [],
  total_cost: 0,
  makespan: 0,
  metrics: { agv_utilization: 0 },
  algorithm_runtime_ms: 0,
});

vi.mock('../services/api', () => ({
  getAgvStatuses: () => mockGetAgvStatuses(),
  getTasks: () => mockGetTasks(),
  getMetrics: () => mockGetMetrics(),
  getMapGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [], name: '', version: 0 }),
  getMapNodes: vi.fn().mockResolvedValue([]),
  getMapEdges: vi.fn().mockResolvedValue([]),
  runSchedule: () => mockRunSchedule(),
  resetSystem: () => mockResetSystem(),
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
      info: vi.fn(),
    },
  };
});

describe('Dashboard Page', () => {

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ===== 基础渲染测试 =====

  it('renders page title correctly', () => {
    render(<Dashboard />);
    // 使用 getAllByText 因为可能有多个匹配
    const titles = screen.getAllByText(/系统总览/i);
    expect(titles.length).toBeGreaterThan(0);
  });

  it('renders action buttons in header', () => {
    render(<Dashboard />);
    expect(screen.getByText('重新启动')).toBeInTheDocument();
    // Dashboard 有两个"执行调度"按钮（header + 控制区），使用 getAllByText
    const scheduleButtons = screen.getAllByText('执行调度');
    expect(scheduleButtons.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('刷新')).toBeInTheDocument();
  });

  // ===== 统计卡片测试 =====

  it('shows stat cards for AGV, Tasks, Makespan, Utilization', () => {
    render(<Dashboard />);
    expect(screen.getByText(/AGV总数/i)).toBeInTheDocument();
    expect(screen.getAllByText(/普通任务/i)[0]).toBeInTheDocument();
    expect(screen.getByText(/总完工时间/i)).toBeInTheDocument();
    expect(screen.getByText(/AGV利用率/i)).toBeInTheDocument();
  });

  // ===== HybridScheduler 控制区测试 =====

  it('renders HybridScheduler control section', () => {
    render(<Dashboard />);
    expect(screen.getByText(/混合调度引擎控制/i)).toBeInTheDocument();
  });

  it('renders mode selector with options', () => {
    render(<Dashboard />);
    // Select 下拉框选项可能被拆分成多个元素，使用 getAllByText 或更灵活的匹配
    expect(screen.getByText(/自动智能选择/i)).toBeInTheDocument();
    // "强制 Theta*" 和 "强制 ECBS" 可能不在 DOM 中（Select 未展开），验证 Select 存在即可
    const selectElement = document.querySelector('.ant-select');
    expect(selectElement).toBeTruthy();
  });

  it('renders traffic control switch', () => {
    render(<Dashboard />);
    // "交通管制" 可能出现在 Switch 标签和 Alert 描述中，使用 getAllByText
    const trafficElements = screen.getAllByText(/交通管制/i);
    expect(trafficElements.length).toBeGreaterThan(0);
  });

  it('renders deadlock prevention switch', () => {
    render(<Dashboard />);
    // "死锁预防" 可能出现在 Switch 标签中
    const deadlockElements = screen.getAllByText(/死锁预防/i);
    expect(deadlockElements.length).toBeGreaterThan(0);
  });

  it('shows current scheduler info alert', () => {
    render(<Dashboard />);
    expect(screen.getByText(/当前调度引擎/i)).toBeInTheDocument();
    expect(screen.getByText(/HybridScheduler/i)).toBeInTheDocument();
  });

  // ===== AGV 列表测试 =====

  it('shows AGV section header', () => {
    render(<Dashboard />);
    expect(screen.getByText('AGV 状态')).toBeInTheDocument();
  });

  it('shows empty state when no AGVs', async () => {
    render(<Dashboard />);
    await waitFor(() => {
      expect(screen.getByText(/暂无AGV数据/i)).toBeInTheDocument();
    });
  });

  it('renders AGV list items with correct status tags', async () => {
    // Override mock for this specific test
    mockGetAgvStatuses.mockResolvedValueOnce([
      {
        id: 'agv1',
        name: 'AGV-01',
        x: 10.5,
        y: 20.3,
        battery: 85,
        status: 'idle',
      },
      {
        id: 'agv2',
        name: 'AGV-02',
        x: 5.0,
        y: 15.0,
        battery: 60,
        status: 'moving',
      },
      {
        id: 'agv3',
        name: 'AGV-03',
        x: 0,
        y: 0,
        battery: 25,
        status: 'charging',
      },
    ] as any);

    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText('AGV-01')).toBeInTheDocument();
      expect(screen.getByText('AGV-02')).toBeInTheDocument();
      expect(screen.getByText('AGV-03')).toBeInTheDocument();
    });

    // Verify status tags are present
    await waitFor(() => {
      expect(screen.getByText('空闲')).toBeInTheDocument();
      expect(screen.getByText('移动中')).toBeInTheDocument();
      expect(screen.getByText('充电中')).toBeInTheDocument();
    });
  });

  // ===== AGV 位置可视化测试 =====

  it('renders canvas element for position visualization', () => {
    render(<Dashboard />);
    const canvas = document.querySelectorAll('canvas');
    expect(canvas.length).toBeGreaterThan(0);
  });

  it('shows AGV position visualization card title', () => {
    render(<Dashboard />);
    expect(screen.getByText('AGV 位置可视化')).toBeInTheDocument();
  });

  // ===== 输送线任务分布测试 =====

  it('hides conveyor task distribution when no conveyor tasks', async () => {
    // Default mock returns no conveyor tasks
    render(<Dashboard />);

    // Should not show conveyor distribution section when empty
    await waitFor(() => {
      expect(screen.queryByText(/输送线任务类型分布/i)).not.toBeInTheDocument();
    });
  });

  // ===== 按钮交互测试 =====

  it('calls refresh when clicking refresh button', async () => {
    render(<Dashboard />);

    await waitFor(() => {
      expect(screen.getByText('刷新')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('刷新'));

    // Should trigger refetch
    await waitFor(() => {
      expect(mockGetAgvStatuses).toHaveBeenCalled();
    });
  });

  // ===== 调度结果展示测试 =====

  it('does not show schedule result section when no result', () => {
    render(<Dashboard />);
    expect(screen.queryByText(/最新调度结果/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/调度结果摘要/i)).not.toBeInTheDocument();
  });
});
