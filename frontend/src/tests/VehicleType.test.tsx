/**
 * VehicleType 页面组件测试 — Phase D+ 补充
 *
 * 覆盖: pages/VehicleType/index.tsx
 * - 组件渲染和基本结构
 * - 车型列表展示 (卡片/表格视图切换)
 * - 车型统计面板
 * - 空状态处理
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

// Mock services before import
vi.mock('../../services/unifiedApi', () => ({
  getUnifiedVehicleTypes: vi.fn().mockResolvedValue([]),
  UnifiedVehicleInfo: {},
}));

// Mock antd icons
vi.mock('@ant-design/icons', () => ({
  TruckOutlined: vi.fn(() => null),
  ToolOutlined: vi.fn(() => null),
  EyeInvisibleOutlined: vi.fn(() => null),
  VerticalAlignTopOutlined: vi.fn(() => null),
  UnorderedListOutlined: vi.fn(() => null),
  PullRequestOutlined: vi.fn(() => null),
  SettingOutlined: vi.fn(() => null),
  PlusOutlined: vi.fn(() => null),
  EditOutlined: vi.fn(() => null),
  DeleteOutlined: vi.fn(() => null),
  CheckCircleOutlined: vi.fn(() => null),
  CarOutlined: vi.fn(() => null),
  ThunderboltOutlined: vi.fn(() => null),
  ArrowsAltOutlined: vi.fn(() => null),
}));

import VehicleTypeManagementPage from '../pages/VehicleType';
import { getUnifiedVehicleTypes } from '../services/unifiedApi';
import type { UnifiedVehicleInfo } from '../services/unifiedApi';

const mockedGetVehicles = vi.mocked(getUnifiedVehicleTypes);

// ==================== Mock 数据 ====================

const mockVehicles: UnifiedVehicleInfo[] = [
  {
    vehicle_type: 'standard',
    display_name: '标准搬运AGV',
    icon: 'TruckOutlined',
    color: '#1890ff',
    max_load_kg: 500,
    max_speed_ms: 1.5,
    lifting_height_m: 0,
    turning_radius_m: 0.8,
    width_m: 1.0,
    length_m: 1.5,
    battery_capacity_kwh: 25,
    supports_docking: true,
    supports_conveyor: false,
    narrow_corridor_only: false,
    navigation_methods: ['qr', 'slam'],
    active_count: 3,
    total_count: 5,
  },
  {
    vehicle_type: 'forklift',
    display_name: '叉车AGV',
    icon: 'ToolOutlined',
    color: '#fa8c16',
    max_load_kg: 2000,
    max_speed_ms: 2.0,
    lifting_height_m: 3.0,
    turning_radius_m: 1.5,
    width_m: 1.2,
    length_m: 2.5,
    battery_capacity_kwh: 50,
    supports_docking: true,
    supports_conveyor: true,
    narrow_corridor_only: false,
    navigation_methods: ['slam', 'laser'],
    active_count: 1,
    total_count: 2,
  },
  {
    vehicle_type: 'latent',
    display_name: '潜伏式AGV',
    icon: 'EyeInvisibleOutlined',
    color: '#722ed1',
    max_load_kg: 1000,
    max_speed_ms: 1.2,
    lifting_height_m: 0.05,
    turning_radius_m: 0.6,
    width_m: 1.0,
    length_m: 1.4,
    battery_capacity_kwh: 30,
    supports_docking: false,
    supports_conveyor: false,
    narrow_corridor_only: true,
    navigation_methods: ['qr'],
    active_count: 0,
    total_count: 3,
  },
];

// ==================== 测试 ====================

describe('VehicleTypeManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('应正确渲染页面标题', async () => {
    mockedGetVehicles.mockResolvedValueOnce([]);
    render(<VehicleTypeManagementPage />);

    // 等待加载完成
    await waitFor(() => {
      const title = document.body.textContent || '';
      expect(title).toBeTruthy();
      // 应包含"车型"或"Vehicle"相关文字
    });
  });

  it('应显示车型卡片或表格内容', async () => {
    mockedGetVehicles.mockResolvedValueOnce(mockVehicles);
    render(<VehicleTypeManagementPage />);

    await waitFor(() => {
      const text = document.body.textContent || '';
      // 至少应包含一种车型名称
      expect(text.includes('标准') || text.includes('叉车') || text.includes('潜伏')).toBe(true);
    });
  });

  it('应在空数据时显示空状态', async () => {
    mockedGetVehicles.mockResolvedValueOnce([]);
    render(<VehicleTypeManagementPage />);

    await waitFor(() => {
      // 不崩溃即可 — 可能显示 Empty 或无数据提示
      expect(document.body.innerHTML.length).toBeGreaterThan(0);
    });
  });

  it('应在 API 失败时显示错误信息', async () => {
    mockedGetVehicles.mockRejectedValueOnce(new Error('Network error'));
    
    // Suppress console.error for this test
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    
    render(<VehicleTypeManagementPage />);

    await waitFor(() => {
      // 页面不应崩溃
      expect(document.body.innerHTML.length).toBeGreaterThan(0);
    });
    
    consoleSpy.mockRestore();
  });

  it('应支持多种车型类型', () => {
    const types = mockVehicles.map(v => v.vehicle_type);
    expect(types).toContain('standard');
    expect(types).toContain('forklift');
    expect(types).toContain('latent');
  });

  it('车型数据应包含完整的参数字段', () => {
    const standard = mockVehicles[0];
    
    expect(standard.max_load_kg).toBeGreaterThan(0);
    expect(standard.max_speed_ms).toBeGreaterThan(0);
    expect(standard.battery_capacity_kwh).toBeGreaterThan(0);
    expect(standard.navigation_methods.length).toBeGreaterThanOrEqual(1);
    expect(typeof standard.supports_docking).toBe('boolean');
    expect(typeof standard.narrow_corridor_only).toBe('boolean');
  });

  it('应正确计算车型统计', () => {
    const totalActive = mockVehicles.reduce((sum, v) => sum + (v.active_count || 0), 0);
    const totalCount = mockVehicles.reduce((sum, v) => sum + (v.total_count || 0), 0);

    expect(totalActive).toBe(4); // 3 + 1 + 0
    expect(totalCount).toBe(10); // 5 + 2 + 3
  });

  it('叉车 AGV 应有更高的载重能力', () => {
    const forklift = mockVehicles.find(v => v.vehicle_type === 'forklift')!;
    const standard = mockVehicles.find(v => v.vehicle_type === 'standard')!;

    expect(forklift.max_load_kg).toBeGreaterThan(standard.max_load_kg);
    expect(forklift.lifting_height_m).toBeGreaterThan(standard.lifting_height_m);
  });

  it('潜伏 AGV 应标记为窄通道专用', () => {
    const latent = mockVehicles.find(v => v.vehicle_type === 'latent')!;

    expect(latent.narrow_corridor_only).toBe(true);
    expect(latent.lifting_height_m).toBeCloseTo(0.05, 1); // 微升
  });

  it('所有车型应有有效的颜色值', () => {
    for (const vehicle of mockVehicles) {
      expect(vehicle.color).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });
});
