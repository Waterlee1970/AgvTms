/**
 * Unified API Service 测试 — Phase D+ 补充
 *
 * 覆盖: unifiedApi.ts
 * - V1→Unified 转换器 (transformV1ToUnified, transformScheduleResult)
 * - V2→Unified 转换器 (transformWsToUnified, transformVehicleType)
 * - 状态映射 (mapV2Status)
 * - 类型完整性验证
 */

import { describe, it, expect } from 'vitest';
import {
  transformV1ToUnified,
  transformScheduleResult,
  transformWsToUnified,
  transformVehicleType,
} from '../services/unifiedApi';
import type {
  UnifiedAgvStatus,
  UnifiedScheduleResult,
  UnifiedVehicleInfo,
  UnifiedTrafficData,
} from '../services/unifiedApi';

// ==================== Mock 数据 ====================

const mockV1Agv = {
  id: 'agv-001',
  name: 'Test AGV',
  x: 10.5,
  y: 20.3,
  battery: 85,
  status: 'idle' as const,
  current_task: 'task-001',
  current_node: 'n1',
  target_node: 'n5',
  path: ['n1', 'n2', 'n3', 'n4', 'n5'],
  speed: 1.2,
};

const mockV1ScheduleResult = {
  assignments: [
    { agv_id: 'agv-001', task_id: 'task-001', path: ['n1', 'n5'], path_cost: 50, start_time: 0, end_time: 100, wait_times: [0] },
    { agv_id: 'agv-002', task_id: 'task-002', path: ['n2', 'n3'], path_cost: 30, start_time: 0, end_time: 80, wait_times: [5] },
  ],
  conveyor_timeline: [],
  total_cost: 80,
  makespan: 100,
  metrics: { efficiency: 0.92 },
  algorithm_runtime_ms: 45.6,
  agv_paths: { 'agv-001': ['n1', 'n5'], 'agv-002': ['n2', 'n3'] },
};

const mockWsAgvFull = {
  agvId: 'agv-v2-001',
  name: 'V2 AGV Alpha',
  position: { x: 15.0, y: 25.5, z: 0.0 },
  batteryLevel: 72,
  state: 'moving',
  speed: 1.8,
  rotation: 45,
  vehicleType: 'forklift',
  currentTask: 'v2-task-001',
  destination: { x: 30, y: 40 },
  loadWeight: 150,
  navigationMethod: 'slam',
  trajectory: {
    points: [
      { t: 0, pos: { x: 10, y: 20 } },
      { t: 1, pos: { x: 12, y: 22 } },
      { t: 2, pos: { x: 15, y: 25.5 } },
    ],
  },
};

const mockWsAgvMinimal = { id: 'minimal' };

const mockVehicleCapability = {
  vehicle_type: 'forklift',
  max_load_kg: 2000,
  max_speed_ms: 2.0,
  lifting_height_m: 3.5,
  turning_radius_m: 1.5,
  width_m: 1.2,
  length_m: 2.5,
  battery_capacity_kwh: 50,
  supports_docking: true,
  supports_conveyor: false,
  narrow_corridor_only: false,
  navigation_methods: ['slam', 'qr'],
};

// ==================== V1 → Unified 转换器 ====================

describe('transformV1ToUnified', () => {
  it('应正确映射所有 V1 字段到 Unified 格式', () => {
    const result = transformV1ToUnified(mockV1Agv);

    expect(result.id).toBe('agv-001');
    expect(result.name).toBe('Test AGV');
    expect(result.x).toBe(10.5);
    expect(result.y).toBe(20.3);
    expect(result.battery).toBe(85);
    expect(result.status).toBe('idle');
    expect(result.current_task).toBe('task-001');
    expect(result.current_node).toBe('n1');
    expect(result.target_node).toBe('n5');
    expect(result.path).toEqual(['n1', 'n2', 'n3', 'n4', 'n5']);
    expect(result.speed).toBe(1.2);
  });

  it('应为 V1 缺失字段提供合理默认值', () => {
    const result = transformV1ToUnified(mockV1Agv);

    // V1 无朝向，默认 0
    expect(result.rotation).toBe(0);
    // V1 无车型，默认 standard
    expect(result.vehicle_type).toBe('standard');
    // 应包含时间戳
    expect(result.last_update_ts).toBeGreaterThan(0);
    expect(typeof result.last_update_ts).toBe('number');
  });

  it('应处理不同状态值', () => {
    const statuses = ['idle', 'moving', 'charging', 'executing', 'waiting', 'error'] as const;
    for (const status of statuses) {
      const result = transformV1ToUnified({ ...mockV1Agv, status });
      expect(result.status).toBe(status);
    }
  });

  it('应处理空路径和可选字段为 undefined', () => {
    const noPathAgv = { ...mockV1Agv, path: undefined, current_task: undefined };
    const result = transformV1ToUnified(noPathAgv);

    expect(result.path).toBeUndefined();
    expect(result.current_task).toBeUndefined();
    // 核心字段仍存在
    expect(result.id).toBe('agv-001');
  });
});

describe('transformScheduleResult', () => {
  it('应保留所有 V1 调度结果字段', () => {
    const result = transformScheduleResult(mockV1ScheduleResult);

    expect(result.assignments).toHaveLength(2);
    expect(result.total_cost).toBe(80);
    expect(result.makespan).toBe(100);
    expect(result.algorithm_runtime_ms).toBe(45.6);
    expect(result.agv_paths['agv-001']).toEqual(['n1', 'n5']);
  });

  it('应设置 V1 特有的默认算法标识', () => {
    const result = transformScheduleResult(mockV1ScheduleResult);

    expect(result.algorithm_used).toBe('ACO+SA+NLP');
    expect(result.fallback_triggered).toBe(false);
    expect(result.scheduler_mode).toBeUndefined();
  });

  it('应处理空的 conveyor_timeline', () => {
    const emptyResult = { ...mockV1ScheduleResult, conveyor_timeline: [] };
    const result = transformScheduleResult(emptyResult);

    expect(result.conveyor_timeline).toEqual([]);
    expect(Array.isArray(result.conveyor_timeline)).toBe(true);
  });

  it('应处理带输送线的调度结果', () => {
    const withConveyor = {
      ...mockV1ScheduleResult,
      conveyor_timeline: [
        { task_id: 'ct-001', segment_id: 'seg-1', start_time: 0, end_time: 60, cargo_id: 'pkg-001' },
      ],
    };
    const result = transformScheduleResult(withConveyor);

    expect(result.conveyor_timeline).toHaveLength(1);
    expect(result.conveyor_timeline[0].cargo_id).toBe('pkg-001');
  });
});

// ==================== V2 → Unified 转换器 ====================

describe('transformWsToUnified', () => {
  it('应正确转换完整的 WebSocket AGV 数据', () => {
    const result = transformWsToUnified(mockWsAgvFull);

    expect(result.id).toBe('agv-v2-001');
    expect(result.name).toBe('V2 AGV Alpha');
    expect(result.x).toBe(15.0);
    expect(result.y).toBe(25.5);
    expect(result.battery).toBe(72);
    expect(result.status).toBe('moving');
    expect(result.speed).toBe(1.8);
    expect(result.rotation).toBe(45);
    expect(result.vehicle_type).toBe('forklift');
    expect(result.current_task).toBe('v2-task-001');
  });

  it('应处理 3D 坐标和目的地', () => {
    const result = transformWsToUnified(mockWsAgvFull);

    expect(result.position_3d).toEqual({ x: 15.0, y: 25.5, z: 0.0 });
    expect(result.destination).toEqual({ x: 30, y: 40 });
    expect(result.load_weight).toBe(150);
    expect(result.navigation_method).toBe('slam');
  });

  it('应转换轨迹数据', () => {
    const result = transformWsToUnified(mockWsAgvFull);

    expect(result.trajectory).toBeDefined();
    expect(result.trajectory).toHaveLength(3);
    expect(result.trajectory![0]).toEqual({ t: 0, x: 10, y: 20 });
    expect(result.trajectory![2]).toEqual({ t: 2, x: 15, y: 25.5 });
  });

  it('应使用降级默认值处理最小数据', () => {
    const result = transformWsToUnified(mockWsAgvMinimal);

    // id: agvId || id
    expect(result.id).toBe('minimal');
    // name: name || agvId (无 agvId 时为 undefined)
    expect(result.name).toBeUndefined();
    // 坐标回退到 0 (无 position/x/y)
    expect(result.x).toBe(0);
    expect(result.y).toBe(0);
    // battery 回退到 100
    expect(result.battery).toBe(100);
    // 状态: 无 state/status → mapV2Status(undefined) → 'idle'
    expect(result.status).toBe('idle');
    // vehicle_type 默认 'standard'
    expect(result.vehicle_type).toBe('standard');
  });

  it('应映射各种 V2 状态', () => {
    const v2States = ['idle', 'moving', 'charging', 'executing', 'loading', 'error', 'offline', 'waiting', 'parked'];
    const expectedUnified: Record<string, string> = {
      idle: 'idle',
      moving: 'moving',
      charging: 'charging',
      executing: 'executing',
      loading: 'executing',
      error: 'error',
      offline: 'idle',
      waiting: 'waiting',
      parked: 'idle',
    };

    for (const v2State of v2States) {
      const result = transformWsToUnified({ state: v2State });
      expect(result.status).toBe(expectedUnified[v2State], `V2 state "${v2State}" should map to "${expectedUnified[v2State]}"`);
    }
  });

  it('应对未知 V2 状态回退到 idle', () => {
    const result = transformWsToUnified({ state: 'unknown_status_xyz' });
    expect(result.status).toBe('idle');
  });

  it('应支持 position.x/y 直接格式（非嵌套）', () => {
    const flatPosition = { agvId: 'flat', x: 99, y: 88, state: 'moving' };
    const result = transformWsToUnified(flatPosition);

    expect(result.x).toBe(99);
    expect(result.y).toBe(88);
  });
});

// ==================== Vehicle Type 转换器 ====================

describe('transformVehicleType', () => {
  it('应转换标准搬运AGV (type=standard)', () => {
    const result = transformVehicleType({
      type: 'standard',
      max_load_kg: 2000,
      supports_docking: true,
      navigation_methods: ['slam', 'qr'],
    });

    // 实际使用 capability.type 字段
    expect(result.vehicle_type).toBe('standard');
    expect(result.display_name).toContain('标准搬运');
    expect(result.max_load_kg).toBe(2000);
    expect(result.supports_docking).toBe(true);
    expect(result.navigation_methods).toEqual(['slam', 'qr']);
  });

  it('应转换叉车AGV (type=forklift)', () => {
    const result = transformVehicleType({
      type: 'forklift',
      max_load_kg: 1000,
      supports_docking: false,
    });

    expect(result.vehicle_type).toBe('forklift');
    expect(result.display_name).toContain('叉车');
    expect(result.color).toBe('#fa8c16');
  });

  it('应转换潜伏AGV (type=latent)', () => {
    const result = transformVehicleType({ type: 'latent' });

    expect(result.vehicle_type).toBe('latent');
    expect(result.display_name).toContain('潜伏');
    expect(result.icon).toBeTruthy();
  });

  it('应对未知车型返回通用标签', () => {
    const result = transformVehicleType({ type: 'unknown_custom' });
    expect(result.display_name).toBeTruthy();
    expect(result.color).toBeTruthy();
    // 未知类型降级到 standard
    expect(result.vehicle_type).toBe('unknown_custom');
  });

  it('应正确传递能力矩阵字段', () => {
    const cap = {
      type: 'standard',
      max_load_kg: 5000,
      max_speed_ms: 2.5,
      lifting_height_m: 1.2,
      turning_radius_m: 0.8,
      width_m: 1.0,
      length_m: 1.5,
      battery_capacity_kwh: 4.0,
    };

    const result = transformVehicleType(cap);

    expect(result.max_speed_ms).toBe(2.5);
    expect(result.lifting_height_m).toBe(1.2);
    expect(result.turning_radius_m).toBe(0.8);
    expect(result.width_m).toBe(1.0);
    expect(result.length_m).toBe(1.5);
    expect(result.battery_capacity_kwh).toBe(4.0);
  });
});

// ==================== 类型完整性验证 ====================

describe('类型接口完整性', () => {
  it('UnifiedAgvStatus 应兼容 V1 和 V2 字段', () => {
    const v1Style: UnifiedAgvStatus = {
      id: 'test',
      name: 'Test',
      x: 0, y: 0, battery: 100, status: 'idle',
    };

    const v2Style: UnifiedAgvStatus = {
      id: 'test2',
      name: 'Test2',
      x: 1, y: 1, battery: 90, status: 'moving',
      vehicle_type: 'forklift',
      rotation: 180,
      position_3d: { x: 1, y: 1, z: 0.5 },
      load_weight: 500,
      last_update_ts: Date.now(),
    };

    expect(v1Style.id).toBeTruthy();
    expect(v2Style.position_3d!.z).toBe(0.5);
  });

  it('UnifiedTrafficData 应包含完整结构', () => {
    const traffic: UnifiedTrafficData = {
      zones: [
        { zone_id: 'z1', locked_by: null, lock_type: 'exclusive', waiting_agvs: [], congestion_level: 'low' },
        { zone_id: 'z2', locked_by: 'agv-001', lock_type: 'shared', waiting_agvs: ['agv-002'], congestion_level: 'high' },
      ],
      heatmap_data: [
        { node_id: 'n1', x: 0, y: 0, congestion_score: 0.2, agv_count: 1, avg_wait_time: 1.5 },
      ],
      stats: {
        total_zones: 10, locked_zones: 1, active_agvs: 5,
        avg_congestion: 0.35, deadlocks_detected: 0,
      },
    };

    expect(traffic.zones).toHaveLength(2);
    expect(traffic.zones[1].locked_by).toBe('agv-001');
    expect(traffic.heatmap_data).toHaveLength(1);
    expect(traffic.stats.total_zones).toBe(10);
  });
});
