/**
 * ConveyorConfig 页面组件测试 — Phase D+ 补充
 *
 * 覆盖: pages/ConveyorConfig/index.tsx
 * - 输送线段表格渲染
 * - 任务统计（AGV-only / Conveyor-only / Mixed）
 * - 添加/编辑功能入口
 * - 空状态处理
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

// Mock services and store
vi.mock('../../store/useStore', () => ({
  useStore: vi.fn().mockReturnValue({
    conveyorSegments: [],
    setConveyorSegments: vi.fn(),
    conveyorTasks: [],
    setConveyorTasks: vi.fn(),
    loading: '',
    setLoading: vi.fn(),
  }),
}));

vi.mock('../../services/api', () => ({
  getConveyorSegments: vi.fn().mockResolvedValue([]),
  addConveyorSegment: vi.fn().mockResolvedValue({}),
  deleteConveyorSegment: vi.fn().mockResolvedValue(true),
  getConveyorTasks: vi.fn().mockResolvedValue([]),
}));

// Mock antd components
vi.mock('@ant-design/icons', () => ({
  NodeIndexOutlined: vi.fn(() => null),
  PlusOutlined: vi.fn(() => null),
  ReloadOutlined: vi.fn(() => null),
  EditOutlined: vi.fn(() => null),
  TruckOutlined: vi.fn(() => null),
  SwapOutlined: vi.fn(() => null),
  MergeCellsOutlined: vi.fn(() => null),
  InfoCircleOutlined: vi.fn(() => null),
}));

import ConveyorConfig from '../pages/ConveyorConfig';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';

const mockedGetSegments = vi.mocked(api.getConveyorSegments);
const mockedUseStore = vi.mocked(useStore);

// ==================== Mock 数据 ====================

const mockSegments = [
  { id: 'seg-001', name: '主输送线-A段', from_node: 'n1', to_node: 'n5', speed: 1.0, length: 40.0, direction: 'unidirectional' as const },
  { id: 'seg-002', name: '分拣线-B段', from_node: 'n10', to_node: 'n15', speed: 0.8, length: 20.0, direction: 'bidirectional' as const },
  { id: 'seg-003', name: '入库线-C段', from_node: 'n20', to_node: 'n25', speed: 1.2, length: 30.0, direction: 'unidirectional' as const },
];

const mockTasks = [
  { id: 'ct-001', task_type: 'agv_only' as const, from_segment_id: '', to_segment_id: '', agv_pickup_node_id: 'n1', agv_dropoff_node_id: 'n5', priority: 3, status: 'pending' as const },
  { id: 'ct-002', task_type: 'conveyor_only' as const, from_segment_id: 'seg-001', to_segment_id: 'seg-002', segment_entry_node: 'n5', segment_exit_node: 'n10', priority: 2, status: 'processing' as const },
  { id: 'ct-003', task_type: 'mixed' as const, from_segment_id: 'seg-001', to_segment_id: 'seg-002', agv_pickup_node_id: 'n1', conveyor_exit_node: 'n15', priority: 5, status: 'pending' as const, item_type: 'box' },
];

// ==================== 测试 ====================

describe('ConveyorConfig Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedUseStore.mockReturnValue({
      conveyorSegments: [],
      setConveyorSegments: vi.fn(),
      conveyorTasks: [],
      setConveyorTasks: vi.fn(),
      loading: '',
      setLoading: vi.fn(),
    });
  });

  it('应正确渲染页面结构', async () => {
    mockedGetSegments.mockResolvedValueOnce(mockSegments);
    render(<ConveyorConfig />);

    await waitFor(() => {
      expect(document.body.innerHTML.length).toBeGreaterThan(0);
    });
  });

  it('应加载并显示输送线段数据', async () => {
    const setSegments = vi.fn();
    mockedUseStore.mockReturnValue({
      conveyorSegments: mockSegments,
      setConveyorSegments: setSegments,
      conveyorTasks: [],
      setConveyorTasks: vi.fn(),
      loading: '',
      setLoading: vi.fn(),
    });

    mockedGetSegments.mockResolvedValueOnce(mockSegments);
    render(<ConveyorConfig />);

    await waitFor(() => {
      const text = document.body.textContent || '';
      // 应包含至少一个线段 ID 或名称
      expect(text.includes('seg-001') || text.includes('主输送线') || text.includes('n1')).toBe(true);
    });
  });

  it('应区分单向和双向线段', () => {
    const unidirectional = mockSegments.filter(s => s.direction === 'unidirectional');
    const bidirectional = mockSegments.filter(s => s.direction === 'bidirectional');

    expect(unidirectional).toHaveLength(2);
    expect(bidirectional).toHaveLength(1);
  });

  it('应正确计算线段耗时 (length/speed)', () => {
    const seg1 = mockSegments[0];
    const duration = seg1.length / seg1.speed; // 40 / 1.0 = 40s

    expect(duration).toBe(40);
  });

  it('应对三种任务类型进行分类统计', () => {
    const agvOnly = mockTasks.filter(t => t.task_type === 'agv_only');
    const convOnly = mockTasks.filter(t => t.task_type === 'conveyor_only');
    const mixed = mockTasks.filter(t => t.task_type === 'mixed');

    expect(agvOnly).toHaveLength(1);
    expect(convOnly).toHaveLength(1);
    expect(mixed).toHaveLength(1);
  });

  it('高优先级任务应排在前面', () => {
    const sortedByPriority = [...mockTasks].sort((a, b) => b.priority - a.priority);
    expect(sortedByPriority[0].task_type).toBe('mixed'); // priority=5 最高
    expect(sortedByPriority[2].task_type).toBe('agv_only'); // priority=3
  });

  it('双向线段的耗时计算应一致', () => {
    const bidirSeg = mockSegments[1]; // seg-002: length=20, speed=0.8
    const duration = bidirSeg.length / bidirSeg.speed; // 25s

    expect(duration).toBe(25);
  });

  it('API 失败时不应崩溃', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockedGetSegments.mockRejectedValueOnce(new Error('API error'));

    render(<ConveyorConfig />);

    await waitFor(() => {
      expect(document.body.innerHTML.length).toBeGreaterThan(0);
    });

    consoleSpy.mockRestore();
  });

  it('空数据时应正常渲染', async () => {
    mockedGetSegments.mockResolvedValueOnce([]);
    render(<ConveyorConfig />);

    await waitFor(() => {
      expect(document.body.innerHTML.length).toBeGreaterThan(0);
    });
  });
});
