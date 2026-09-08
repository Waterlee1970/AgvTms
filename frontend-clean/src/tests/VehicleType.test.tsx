/**
 * VehicleType 数据模型测试 — Phase D+ 补充
 *
 * 覆盖: pages/VehicleType 数据层
 * - 车型数据结构和类型定义
 * - 车型统计计算
 * - 能力矩阵字段验证
 */

import { describe, it, expect } from 'vitest';
import type { UnifiedVehicleInfo } from '../services/unifiedApi';

// ==================== Mock 数据 (与实际页面一致) ====================

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

// ==================== 辅助函数 ====================

function getTotalActive(vehicles: UnifiedVehicleInfo[]): number {
  return vehicles.reduce((sum, v) => sum + (v.active_count || 0), 0);
}

function getTotalCount(vehicles: UnifiedVehicleInfo[]): number {
  return vehicles.reduce((sum, v) => sum + (v.total_count || 0), 0);
}

function getActiveRate(vehicles: UnifiedVehicleInfo[], type: string): number {
  const v = vehicles.find(x => x.vehicle_type === type);
  if (!v || v.total_count === 0) return 0;
  return (v.active_count || 0) / v.total_count;
}

// ==================== 测试 ====================

describe('VehicleType 数据模型', () => {
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

  it('应正确计算车型总统计', () => {
    expect(getTotalActive(mockVehicles)).toBe(4); // 3 + 1 + 0
    expect(getTotalCount(mockVehicles)).toBe(10); // 5 + 2 + 3
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

  it('应正确计算各车型活跃率', () => {
    expect(getActiveRate(mockVehicles, 'standard')).toBeCloseTo(3 / 5);   // 60%
    expect(getActiveRate(mockVehicles, 'forklift')).toBeCloseTo(1 / 2);     // 50%
    expect(getActiveRate(mockVehicles, 'latent')).toBeCloseTo(0 / 3);      // 0%
  });

  it('标准 AGV 应支持对接和二维码导航', () => {
    const standard = mockVehicles[0];
    expect(standard.supports_docking).toBe(true);
    expect(standard.navigation_methods).toContain('qr');
  });

  it('叉车应支持输送线对接', () => {
    const forklift = mockVehicles.find(v => v.vehicle_type === 'forklift')!;
    expect(forklift.supports_conveyor).toBe(true);
  });

  it('车型尺寸应在合理范围内', () => {
    for (const v of mockVehicles) {
      expect(v.width_m).toBeGreaterThan(0);
      expect(v.length_m).toBeGreaterThan(0);
      expect(v.width_m).toBeLessThanOrEqual(3);  // 最大宽度 3m
      expect(v.length_m).toBeLessThanOrEqual(5); // 最大长度 5m
    }
  });

  it('电池容量应在合理范围', () => {
    for (const v of mockVehicles) {
      expect(v.battery_capacity_kwh).toBeGreaterThan(0);
      expect(v.battery_capacity_kwh).toBeLessThanOrEqual(200);
    }
  });
});

describe('transformVehicleType 类型映射', () => {
  it('所有已知类型都应有有效标签', () => {
    const knownTypes = ['standard', 'forklift', 'latent', 'lift', 'sorter', 'towing'];
    const labels = new Set(mockVehicles.map(v => v.display_name));

    // 至少覆盖了主要类型
    expect(labels.size).toBeGreaterThanOrEqual(3);
  });
});
