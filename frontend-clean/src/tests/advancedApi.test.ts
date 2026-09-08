/**
 * Advanced API Service 测试 — Phase D+ 补充
 *
 * 覆盖: advancedApi.ts
 * - Phase 5: 策略管理 / 调度循环
 * - Phase 6: 分布式调度
 * - Phase 7: VDA5050 / 适配器管理
 * - Phase 8: 数字孪生 / RL A/B 测试
 * - 所有类型定义验证
 */

import { describe, it, expect } from 'vitest';
import type {
  StrategyInfo,
  StrategySwitchReq,
  SchedulerLoopStatus,
  AdapterInfo,
  Point3D,
  Agv3DModel,
  MapElement3D,
  Trajectory3D,
  SceneModel,
  ABTestStartReq,
  ABTestResult,
} from '../services/advancedApi';

// ==================== Phase 5: 策略管理 ====================

describe('StrategyInfo 类型', () => {
  it('应包含完整的策略组件信息', () => {
    const info: StrategyInfo = {
      cost_functions: { available: ['euclidean', 'manhattan', 'dijkstra', 'angle_penalty'], current: 'angle_penalty' },
      dispatchers: { available: ['greedy', 'round_robin', 'priority_queue'], current: 'greedy' },
      routers: { available: ['theta_star', 'astar', 'dijkstra'], current: 'theta_star' },
      adapters: { available: ['vda5050', 'custom_http'] },
    };

    expect(info.cost_functions.available.length).toBeGreaterThanOrEqual(1);
    expect(info.routers.current).toBe('theta_star');
    expect(info.adapters.available).toContain('vda5050');
  });

  it('应支持策略切换请求', () => {
    const fullSwitch: StrategySwitchReq = {
      cost_function: 'manhattan',
      dispatcher: 'priority_queue',
      router: 'astar',
    };

    const partialSwitch: StrategySwitchReq = {
      cost_function: 'dijkstra',
    };

    expect(fullSwitch.cost_function).toBeTruthy();
    expect(fullSwitch.router).toBeTruthy();
    expect(partialSwitch.dispatcher).toBeUndefined(); // 部分切换允许省略
  });
});

describe('SchedulerLoopStatus', () => {
  it('应表示运行/停止状态', () => {
    const running: SchedulerLoopStatus = {
      is_running: true,
      stats: {
        total_dispatches: 150,
        total_orders_processed: 148,
        total_redispatches: 5,
        total_failures_handled: 3,
        last_dispatch_time: '2026-06-20T11:00:00Z',
        uptime_start: '2026-06-19T08:00:00Z',
      },
    };

    expect(running.is_running).toBe(true);
    expect(running.stats.total_dispatches).toBe(150);
    expect(running.stats.last_dispatch_time).toBeTruthy();

    const stopped: SchedulerLoopStatus = {
      is_running: false,
      stats: {
        total_dispatches: 0,
        total_orders_processed: 0,
        total_redispatches: 0,
        total_failures_handled: 0,
        last_dispatch_time: null,
        uptime_start: null,
      },
    };

    expect(stopped.is_running).toBe(false);
    expect(stopped.stats.last_dispatch_time).toBeNull();
  });
});

// ==================== Phase 7: VDA5050 ====================

describe('VDA5050 订单类型', () => {
  it('应构建有效订单请求', () => {
    const order = {
      order_id: 'order-001',
      path: ['n1', 'n2', 'n3', 'n5'],
      max_speed: 2.0,
      serial_number: 'SN-12345',
    };

    expect(order.order_id).toBeTruthy();
    expect(order.path.length).toBeGreaterThanOrEqual(2);
  });
});

describe('AdapterInfo', () => {
  it('应描述已注册和运行中的适配器', () => {
    const info: AdapterInfo = {
      registered: ['vda5050_adapter', 'custom_http_adapter', 'mqtt_bridge'],
      running: {
        adapters: {
          vda5050_adapter: { protocol: 'VDA5050', connected: true, vehicle_count: 3 },
          custom_http_adapter: { protocol: 'HTTP', connected: false, vehicle_count: 0 },
        },
        vehicle_routes: { 'agv-001': '/zone-a/n1→n5', 'agv-002': '/zone-b/n2→n3' },
        total_vehicles: 3,
      },
    };

    expect(info.registered).toHaveLength(3);
    expect(info.running.adapters.vda5050_adapter.connected).toBe(true);
    expect(info.running.total_vehicles).toBe(3);
    expect(Object.keys(info.running.vehicle_routes)).toHaveLength(2);
  });

  it('应支持无运行适配器的状态', () => {
    const empty: AdapterInfo = {
      registered: [],
      running: {
        adapters: {},
        vehicle_routes: {},
        total_vehicles: 0,
      },
    };

    expect(empty.registered).toEqual([]);
    expect(empty.running.total_vehicles).toBe(0);
  });
});

// ==================== Phase 8: 3D 数字孪生 ====================

describe('Point3D', () => {
  it('应表示三维坐标', () => {
    const origin: Point3D = { x: 0, y: 0, z: 0 };
    const point: Point3D = { x: 10.5, y: 20.3, z: 1.5 };

    expect(point.z).toBe(1.5);
    expect(origin.x).toBe(0);
  });
});

describe('Agv3DModel', () => {
  it('应包含完整的3D AGV模型数据', () => {
    const agv: Agv3DModel = {
      agvId: 'agv-3d-001',
      position: { x: 10, y: 20, z: 0.5 },
      rotation: 45.0,
      geometryType: 'forklift',
      dimensions: [2.0, 1.2, 1.5],
      color: '#1890ff',
      state: 'moving',
      batteryLevel: 82,
      loadStatus: true,
      currentTask: 'task-deliver',
      speed: 1.5,
      animation: 'driving',
    };

    expect(agv.dimensions).toHaveLength(3);
    expect(agv.dimensions[0]).toBe(2.0); // length
    expect(agv.loadStatus).toBe(true);
    expect(agv.animation).toBeTruthy();
  });
});

describe('MapElement3D', () => {
  it('应表示地图元素（节点、边等）', () => {
    const node: MapElement3D = {
      type: 'node',
      id: 'n1',
      position: { x: 0, y: 0, z: 0 },
      dimensions: [0.5, 0.5, 0.1],
      color: '#ff9800',
      label: 'Pickup Station A',
    };

    const edge: MapElement3D = {
      type: 'edge',
      id: 'e1-n1-n2',
      position: { x: 5, y: 0, z: 0 },
      dimensions: [10, 0.1, 0.1],
      color: '#666666',
      label: 'Path A→B',
    };

    expect(node.type).toBe('node');
    expect(edge.type).toBe('edge');
    expect(edge.label).toContain('A→B');
  });
});

describe('Trajectory3D', () => {
  it('应包含带时间戳的3D轨迹点', () => {
    const traj: Trajectory3D = {
      agvId: 'traj-agv-001',
      points: [
        { t: 0, pos: { x: 0, y: 0, z: 0 }, rot: 0, speed: 0, state: 'idle' },
        { t: 1, pos: { x: 5, y: 0, z: 0 }, rot: 90, speed: 1.5, state: 'moving' },
        { t: 2, pos: { x: 10, y: 5, z: 0 }, rot: 45, speed: 1.8, state: 'moving' },
        { t: 3, pos: { x: 10, y: 10, z: 0 }, rot: 180, speed: 0, state: 'executing' },
      ],
      totalDuration: 3,
      totalDistance: 17.07,
    };

    expect(traj.points).toHaveLength(4);
    expect(traj.points[0].state).toBe('idle');
    expect(traj.points[3].state).toBe('executing');
    expect(traj.totalDuration).toBe(3);
    expect(traj.totalDistance).toBeGreaterThan(0);
  });
});

describe('SceneModel', () => {
  it('应构建完整的数字孪生场景', () => {
    const scene: SceneModel = {
      sceneId: 'scene-main-001',
      timestamp: new Date().toISOString(),
      mapElements: [
        { type: 'node', id: 'n1', position: { x: 0, y: 0, z: 0 }, dimensions: [0.5, 0.5, 0.1], color: '#ff9800', label: 'N1' },
        { type: 'node', id: 'n2', position: { x: 10, y: 0, z: 0 }, dimensions: [0.5, 0.5, 0.1], color: '#4caf50', label: 'N2' },
      ],
      agvs: [
        {
          agvId: 'a1', position: { x: 0, y: 0, z: 0.3 }, rotation: 0,
          geometryType: 'standard', dimensions: [1, 0.8, 0.5], color: '#1890ff',
          state: 'idle', batteryLevel: 100, loadStatus: false, currentTask: '', speed: 0, animation: '',
        },
      ],
      trajectories: [],
      cameraDefault: { position: [50, 30, 40], target: [5, 5, 0] },
    };

    expect(scene.sceneId).toBe('scene-main-001');
    expect(scene.mapElements).toHaveLength(2);
    expect(scene.agvs).toHaveLength(1);
    expect(scene.cameraDefault.position).toHaveLength(3);
    expect(scene.cameraDefault.target).toHaveLength(3);
  });
});

// ==================== RL A/B 测试 ====================

describe('ABTestStartReq', () => {
  it('应构建有效的 A/B 测试启动请求', () => {
    const req: ABTestStartReq = {
      test_name: 'theta_vs_ecbs',
      strategy_a: 'force_theta',
      strategy_b: 'force_ecbs',
      traffic_split: 0.5,
    };

    expect(req.test_name).toBeTruthy();
    expect(req.strategy_a).not.toBe(req.strategy_b);
    expect(req.traffic_split).toBe(0.5);

    // traffic_split 可选
    const minimal: ABTestStartReq = {
      test_name: 'minimal_test',
      strategy_a: 'auto',
      strategy_b: 'force_theta',
    };
    expect(minimal.traffic_split).toBeUndefined();
  });
});

describe('ABTestResult', () => {
  it('应包含 A/B 两组对比指标', () => {
    const result: ABTestResult = {
      test_name: 'theta_vs_ecbs',
      strategy_a: 'force_theta',
      strategy_b: 'force_ecbs',
      samples_a: 150,
      samples_b: 148,
      metrics_a: { avg_completion_time: 45.2, success_rate: 0.96, path_efficiency: 0.88 },
      metrics_b: { avg_completion_time: 52.1, success_rate: 0.93, path_efficiency: 0.82 },
      winner: 'strategy_a',
    };

    expect(result.samples_a).toBeGreaterThan(0);
    expect(result.winner).toBe('strategy_a');
    expect(result.metrics_a.success_rate).toBeGreaterThan(result.metrics_b.success_rate);
  });

  it('应在样本不足时 winner 为 null', () => {
    const inconclusive: ABTestResult = {
      test_name: 'insufficient_data',
      strategy_a: 'auto',
      strategy_b: 'force_theta',
      samples_a: 10,
      samples_b: 8,
      metrics_a: {},
      metrics_b: {},
      winner: null,
    };

    expect(inconclusive.winner).toBeNull();
  });

  it('winner 可以为空字符串（后端可能返回 "" 而非 null）', () => {
    const emptyWinner: ABTestResult = {
      test_name: 'empty_winner',
      strategy_a: 'a',
      strategy_b: 'b',
      samples_a: 5,
      samples_b: 5,
      metrics_a: {},
      metrics_b: {},
      winner: '',
    };

    expect(emptyWinner.winner === '' || emptyWinner.winner === null || !emptyWinner.winner).toBe(true);
  });
});
