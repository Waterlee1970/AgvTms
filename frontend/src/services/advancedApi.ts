/**
 * 高级功能 API 服务 — 对接 Phase 5-8 后端接口
 *
 * Phase 5: 策略切换 + 调度循环控制
 * Phase 6: 分布式调度 + 消息队列
 * Phase 7: VDA5050 完整 + 适配器管理
 * Phase 8: 3D 数字孪生 + RL A/B 测试
 */

import axios from 'axios';

const advancedApi = axios.create({
  baseURL: '/api/v2/advanced',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// ==================== Phase 5: 策略管理 ====================

export interface StrategyInfo {
  cost_functions: { available: string[]; current: string };
  dispatchers: { available: string[]; current: string };
  routers: { available: string[]; current: string };
  adapters: { available: string[] };
}

export interface StrategySwitchReq {
  cost_function?: string;
  dispatcher?: string;
  router?: string;
}

export const getStrategyInfo = () =>
  advancedApi.get<StrategyInfo>('/strategy').then(r => r.data);

export const setStrategy = (req: StrategySwitchReq) =>
  advancedApi.put<{ success: boolean; updated: Record<string, string> }>('/strategy', req).then(r => r.data);

// ==================== Phase 5: 调度循环 ====================

export interface SchedulerLoopStatus {
  is_running: boolean;
  stats: {
    total_dispatches: number;
    total_orders_processed: number;
    total_redispatches: number;
    total_failures_handled: number;
    last_dispatch_time: string | null;
    uptime_start: string | null;
  };
}

export const startSchedulerLoop = () =>
  advancedApi.post<{ success: boolean; message: string }>('/scheduler-loop/start').then(r => r.data);

export const stopSchedulerLoop = () =>
  advancedApi.post<{ success: boolean; message: string }>('/scheduler-loop/stop').then(r => r.data);

export const getSchedulerLoopStatus = () =>
  advancedApi.get<SchedulerLoopStatus>('/scheduler-loop/status').then(r => r.data);

// ==================== Phase 6: 分布式调度 ====================

export const submitDistributedOrder = (orderData: Record<string, unknown>) =>
  advancedApi.post<{ success: boolean; message_id: string }>('/distributed/submit-order', orderData).then(r => r.data);

export const getDistributedResults = (count = 10) =>
  advancedApi.get<{ results: Record<string, unknown>[]; count: number }>(`/distributed/results?count=${count}`).then(r => r.data);

export const startDistributedWorker = () =>
  advancedApi.post<{ success: boolean; message: string }>('/distributed/worker/start').then(r => r.data);

export const stopDistributedWorker = () =>
  advancedApi.post<{ success: boolean; message: string }>('/distributed/worker/stop').then(r => r.data);

// ==================== Phase 7: VDA5050 ====================

export interface Vda5050OrderReq {
  order_id: string;
  path: string[];
  max_speed?: number;
  serial_number?: string;
}

export const buildVda5050Order = (req: Vda5050OrderReq) =>
  advancedApi.post('/vda5050/build-order', req).then(r => r.data);

export const getVda5050Schema = () =>
  advancedApi.get('/vda5050/schema').then(r => r.data);

// ==================== Phase 7: 适配器管理 ====================

export interface AdapterInfo {
  registered: string[];
  running: {
    adapters: Record<string, { protocol: string; connected: boolean; vehicle_count: number }>;
    vehicle_routes: Record<string, string>;
    total_vehicles: number;
  };
}

export const getAdapters = () =>
  advancedApi.get<AdapterInfo>('/adapters').then(r => r.data);

export const startAdapter = (adapterName: string, config: Record<string, unknown> = {}) =>
  advancedApi.post<{ success: boolean; adapter: string; vehicle_count: number }>('/adapters/start', {
    adapter_name: adapterName, config,
  }).then(r => r.data);

export const stopAdapter = (adapterName: string) =>
  advancedApi.delete<{ success: boolean; message: string }>(`/adapters/${adapterName}`).then(r => r.data);

// ==================== Phase 8: 3D 数字孪生 ====================

export interface Point3D { x: number; y: number; z: number }

export interface Agv3DModel {
  agvId: string;
  position: Point3D;
  rotation: number;
  geometryType: string;
  dimensions: [number, number, number];
  color: string;
  state: string;
  batteryLevel: number;
  loadStatus: boolean;
  currentTask: string;
  speed: number;
  animation: string;
}

export interface MapElement3D {
  type: string;
  id: string;
  position: Point3D;
  dimensions: [number, number, number];
  color: string;
  label: string;
}

export interface Trajectory3D {
  agvId: string;
  points: Array<{ t: number; pos: Point3D; rot: number; speed: number; state: string }>;
  totalDuration: number;
  totalDistance: number;
}

export interface SceneModel {
  sceneId: string;
  timestamp: string;
  mapElements: MapElement3D[];
  agvs: Agv3DModel[];
  trajectories: Trajectory3D[];
  cameraDefault: { position: [number, number, number]; target: [number, number, number] };
}

export const get3DScene = () =>
  advancedApi.get<SceneModel>('/digital-twin/scene').then(r => r.data);

export const getAgvTrajectory3D = (agvId: string) =>
  advancedApi.get(`/digital-twin/trajectory/${agvId}`).then(r => r.data);

// ==================== Phase 8: RL A/B 测试 ====================

export interface ABTestStartReq {
  test_name: string;
  strategy_a: string;
  strategy_b: string;
  traffic_split?: number;
}

export interface ABTestResult {
  test_name: string;
  strategy_a: string;
  strategy_b: string;
  samples_a: number;
  samples_b: number;
  metrics_a: Record<string, number>;
  metrics_b: Record<string, number>;
  winner?: string | null;  // 未达 min_samples 时为 null/""
}

export const startABTest = (req: ABTestStartReq) =>
  advancedApi.post<{ success: boolean; message: string }>('/ab-test/start', req).then(r => r.data);

export const getABTestResult = (testName: string) =>
  advancedApi.get<ABTestResult>(`/ab-test/${testName}`).then(r => r.data);

export const getRLModelStatus = () =>
  advancedApi.get<{ loaded: boolean; model_type: string; model_path: string | null }>('/rl/model-status').then(r => r.data);
