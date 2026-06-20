/**
 * Unified API Service - V1/V2 数据格式统一适配层 (P1修复)
 *
 * 核心职责:
 * 1. 统一V1(原型算法)和V2(工业级算法)的数据格式差异
 * 2. 提供类型安全的转换函数 (Transformer)
 * 3. 封装WebSocket实时数据流管理
 * 4. 统一错误处理和重试机制
 *
 * 使用场景:
 * - DigitalTwin 3D可视化需要同时消费V1静态数据 + V2实时WS数据
 * - Dashboard混合展示V1调度结果 + V2 HybridScheduler状态
 * - AGvMonitor统一显示AGV状态 + 交通管制数据
 */

import axios from 'axios';

// ==================== 基础配置 ====================

const unifiedApi = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// ==================== 类型定义 ====================

/** 统一的AGV状态格式 (合并V1+V2字段) */
export interface UnifiedAgvStatus {
  // === V1 字段 (来自 api.ts AgvStatus) ===
  id: string;
  name: string;
  x: number;
  y: number;
  battery: number;
  status: 'idle' | 'moving' | 'charging' | 'executing' | 'waiting' | 'error';
  current_task?: string;
  current_node?: string;
  target_node?: string;
  path?: string[];
  speed?: number;

  // === V2 扩展字段 (来自 v2AlgorithmApi / WebSocket) ===
  vehicle_type?: 'standard' | 'forklift' | 'latent' | 'lift' | 'sorter' | 'towing';
  rotation?: number;              // 朝向角度 (度)
  position_3d?: { x: number; y: number; z: number };  // 3D坐标 (DigitalTwin用)
  destination?: { x: number; y: number };
  load_weight?: number;           // 当前载重(kg)
  max_load_kg?: number;           // 最大载重(车型参数)
  navigation_method?: string;     // 导航方式
  last_update_ts?: number;        // 时间戳(ms)
  trajectory?: Array<{ t: number; x: number; y: number }>;  // 轨迹历史
}

/** 统一的调度结果格式 */
export interface UnifiedScheduleResult {
  id?: string;

  // === V1 字段 ===
  assignments: Array<{
    agv_id: string;
    task_id: string;
    path: string[];
    path_cost: number;
    start_time: number;
    end_time: number;
    wait_times: number[];
  }>;
  conveyor_timeline: Array<{
    task_id: string;
    segment_id: string;
    start_time: number;
    end_time: number;
    cargo_id: string;
  }>;
  total_cost: number;
  makespan: number;
  metrics: Record<string, number>;
  algorithm_runtime_ms: number;
  agv_paths: Record<string, string[]>;

  // === V2 扩展字段 ===
  scheduler_mode?: 'auto' | 'force_theta' | 'force_ecbs';  // 使用的调度模式
  algorithm_used?: string;          // 实际使用的算法名 (Theta*/ECBS/Dijkstra...)
  fallback_triggered?: boolean;     // 是否触发了降级
  fallback_from?: string;           // 从哪个算法降级
  fallback_to?: string;             // 降级到哪个算法
  traffic_control_enabled?: boolean; // 交通管制是否启用
  deadlock_prevention_active?: boolean; // 死锁预防是否激活
}

/** 统一的车辆/车型信息 */
export interface UnifiedVehicleInfo {
  vehicle_type: string;
  display_name: string;             // 中文名称
  icon: string;                     // 图标标识
  color: string;                    // 显示颜色

  // 能力参数
  max_load_kg: number;
  max_speed_ms: number;
  lifting_height_m: number;
  turning_radius_m: number;
  width_m: number;
  length_m: number;
  battery_capacity_kwh: number;

  // 功能特性
  supports_docking: boolean;
  supports_conveyor: boolean;
  narrow_corridor_only: boolean;
  navigation_methods: string[];

  // 统计
  active_count?: number;            // 当前活跃数量
  total_count?: number;             // 总数量
}

/** 统一的交通管制数据 */
export interface UnifiedTrafficData {
  zones: Array<{
    zone_id: string;
    locked_by: string | null;
    lock_type: 'exclusive' | 'shared';
    waiting_agvs: string[];
    congestion_level: 'low' | 'medium' | 'high' | 'critical';
  }>;
  heatmap_data: Array<{
    node_id: string;
    x: number;
    y: number;
    congestion_score: number;
    agv_count: number;
    avg_wait_time: number;
  }>;
  stats: {
    total_zones: number;
    locked_zones: number;
    active_agvs: number;
    avg_congestion: number;
    deadlocks_detected: number;
  };
}

// ==================== V1 → Unified 转换器 ====================

/** 将V1 AgvStatus转换为UnifiedAgvStatus */
export function transformV1ToUnified(agv: import('./api').AgvStatus): UnifiedAgvStatus {
  return {
    id: agv.id,
    name: agv.name,
    x: agv.x,
    y: agv.y,
    battery: agv.battery,
    status: agv.status,
    current_task: agv.current_task,
    current_node: agv.current_node,
    target_node: agv.target_node,
    path: agv.path,
    speed: agv.speed,
    rotation: 0,  // V1无朝向，默认0
    vehicle_type: 'standard',  // 默认标准AGV
    last_update_ts: Date.now(),
  };
}

/** 将V1 ScheduleResult转换为UnifiedScheduleResult */
export function transformScheduleResult(result: import('./api').ScheduleResult): UnifiedScheduleResult {
  return {
    ...result,
    scheduler_mode: undefined,  // V1无此字段
    algorithm_used: 'ACO+SA+NLP',  // V1默认使用这三种算法组合
    fallback_triggered: false,
  };
}

// ==================== V2 → Unified 转换器 ====================

/** 将V2 WebSocket AGV数据转换为UnifiedAgvStatus */
export function transformWsToUnified(wsData: any): UnifiedAgvStatus {
  return {
    id: wsData.agvId || wsData.id,
    name: wsData.name || wsData.agvId,
    x: wsData.position?.x ?? wsData.x ?? 0,
    y: wsData.position?.y ?? wsData.y ?? 0,
    z: wsData.position?.z ?? 0,
    battery: wsData.batteryLevel ?? wsData.battery ?? 100,
    status: mapV2Status(wsData.state ?? wsData.status),
    speed: wsData.speed ?? 0,
    rotation: wsData.rotation ?? 0,

    // V2扩展字段
    vehicle_type: wsData.vehicleType || 'standard',
    current_task: wsData.currentTask,
    position_3d: wsData.position,
    destination: wsData.destination,
    load_weight: wsData.loadWeight,
    navigation_method: wsData.navigationMethod,
    last_update_ts: Date.now(),
    trajectory: wsData.trajectory?.points?.map((p: any) => ({
      t: p.t,
      x: p.pos?.x,
      y: p.pos?.y,
    })),
  };
}

/** 映射V2状态枚举到V1格式 */
function mapV2Status(v2status: string): UnifiedAgvStatus['status'] {
  const mapping: Record<string, UnifiedAgvStatus['status']> = {
    'idle': 'idle',
    'moving': 'moving',
    'charging': 'charging',
    'executing': 'executing',
    'loading': 'executing',       // V2的loading映射为V1的executing
    'error': 'error',
    'offline': 'idle',            // 离线视为空闲
    'waiting': 'waiting',
    'parked': 'idle',
  };
  return mapping[v2status] || 'idle';
}

/** 将后端VehicleCapability转换为UnifiedVehicleInfo */
export function transformVehicleType(capability: any): UnifiedVehicleInfo {
  const typeLabels: Record<string, { label: string; icon: string; color: string }> = {
    standard: { label: '标准搬运AGV', icon: 'TruckOutlined', color: '#1890ff' },
    forklift: { label: '叉车AGV', icon: 'ToolOutlined', color: '#fa8c16' },
    latent: { label: '潜伏AGV', icon: 'EyeInvisibleOutlined', color: '#722ed1' },
    lift: { label: '顶升AGV', icon: 'VerticalAlignTopOutlined', color: '#52c41a' },
    sorter: { label: '分拣AGV', icon: 'UnorderedListOutlined', color: '#eb2f96' },
    towing: { label: '牵引AGV', icon: 'PullRequestOutlined', color: '#13c2c2' },
    custom: { label: '自定义AGV', icon: 'SettingOutlined', color: '#666' },
  };

  const info = typeLabels[capability.type] || typeLabels.standard;

  return {
    vehicle_type: capability.type,
    display_name: info.label,
    icon: info.icon,
    color: info.color,
    max_load_kg: capability.max_load_kg,
    max_speed_ms: capability.max_speed_ms,
    lifting_height_m: capability.lifting_height_m || 0,
    turning_radius_m: capability.turning_radius_m,
    width_m: capability.width_m,
    length_m: capability.length_m,
    battery_capacity_kwh: capability.battery_capacity_kwh || 2.0,
    supports_docking: capability.supports_docking,
    supports_conveyor: capability.supports_conveyor,
    narrow_corridor_only: capability.narrow_corridor_only,
    navigation_methods: capability.navigation_methods || ['qr_code'],
  };
}

// ==================== 统一API函数 ====================

/** 获取所有AGV的统一状态列表 */
export const getUnifiedAgvStatuses = async (): Promise<UnifiedAgvStatus[]> => {
  try {
    const { getAgvStatuses } = await import('./api');
    const v1data = await getAgvStatuses();
    return v1data.map(transformV1ToUnified);
  } catch (e) {
    console.error('Failed to fetch unified AGV statuses:', e);
    throw e;
  }
}

/** 执行统一调度 (自动选择V1/V2) */
export const runUnifiedSchedule = async (
  params: { force_v2?: boolean; hybrid_mode?: string } = {}
): Promise<UnifiedScheduleResult> => {
  if (params.force_v2) {
    try {
      const { runHybridSchedule } = await import('./v2AlgorithmApi');
      const result = await runHybridSchedule({ force_mode: params.hybrid_mode });
      return result as any;
    } catch (e) {
      console.warn('V2 schedule failed, fallback to V1:', e);
    }
  }

  // Fallback to V1
  const { runSchedule } = await import('./api');
  const result = await runSchedule();
  return transformScheduleResult(result);
};

/** 获取所有车型信息 */
export const getUnifiedVehicleTypes = async (): Promise<UnifiedVehicleInfo[]> => {
  try {
    const response = await unifiedApi.get<any[]>('/vehicles/types');
    return (response.data || []).map(transformVehicleType);
  } catch (e) {
    // 如果后端没有/vehicles端点，返回默认值
    console.warn('Failed to fetch vehicle types, using defaults:', e);
    return getDefaultVehicleTypes();
  }
};

/** 获取统一交通管制数据 */
export const getUnifiedTrafficData = async (): Promise<UnifiedTrafficData> => {
  try {
    const [zonesRes, congestionRes, statsRes] = await Promise.all([
      import('./v2AlgorithmApi').then(m => m.getTrafficZones()).catch(() => []),
      import('./v2AlgorithmApi').then(m => m.getCongestionHeatmap()).catch(() => []),
      import('./v2AlgorithmApi').then(m => m.getTrafficStats()).catch(null),
    ]);

    return {
      zones: zonesRes || [],
      heatmap_data: congestionRes || [],
      stats: statsRes || {
        total_zones: 0,
        locked_zones: 0,
        active_agvs: 0,
        avg_congestion: 0,
        deadlocks_detected: 0,
      },
    };
  } catch (e) {
    console.error('Failed to fetch traffic data:', e);
    return {
      zones: [],
      heatmap_data: [],
      stats: {
        total_zones: 0,
        locked_zones: 0,
        active_agvs: 0,
        avg_congestion: 0,
        deadlocks_detected: 0,
      },
    };
  }
};

// ==================== WebSocket 管理器 (DigitalTwin专用) ====================

export interface WsAgvMessage {
  type: 'agv_status_update' | 'scene_snapshot' | 'heartbeat' | 'error';
  timestamp: number;
  data: any;
}

export class DigitalTwinWsManager {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners: Map<string, Set<(data: any) => void>> = new Map();
  private url: string;
  private autoReconnect: boolean;
  private reconnectDelay: number;

  constructor(url?: string, options?: { autoReconnect?: boolean; reconnectDelay?: number }) {
    this.url = url || `ws://${window.location.host}/api/v2/digital-twin/ws/agv-status`;
    this.autoReconnect = options?.autoReconnect ?? true;
    this.reconnectDelay = options?.reconnectDelay ?? 3000;
  }

  /** 连接WebSocket */
  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
          console.log('[DigitalTwinWS] Connected:', this.url);
          resolve();
        };

        this.ws.onmessage = (event) => {
          try {
            const message: WsAgvMessage = JSON.parse(event.data);
            this.emit(message.type, message.data);

            // 特殊处理: agv_status_update 同时触发通用更新事件
            if (message.type === 'agv_status_update') {
              this.emit('update', transformWsToUnified(message.data));
            }
          } catch (e) {
            console.error('[DigitalTwinWS] Parse error:', e);
          }
        };

        this.ws.onerror = (event) => {
          console.error('[DigitalTwinWS] Error:', event);
          reject(event);
        };

        this.ws.onclose = () => {
          console.log('[DigitalTwinWS] Disconnected');
          this.emit('disconnected', null);

          if (this.autoReconnect) {
            this.scheduleReconnect();
          }
        };
      } catch (e) {
        reject(e);
      }
    });
  }

  /** 断开连接 */
  disconnect() {
    this.autoReconnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  /** 订阅事件 */
  on(event: string, callback: (data: any) => void): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);

    // 返回取消订阅函数
    return () => {
      this.listeners.get(event)?.delete(callback);
    };
  }

  /** 触发事件 */
  private emit(event: string, data: any) {
    this.listeners.get(event)?.forEach(cb => {
      try {
        cb(data);
      } catch (e) {
        console.error(`[DigitalTwinWS] Error in listener for ${event}:`, e);
      }
    });
  }

  /** 计划重连 */
  private scheduleReconnect() {
    if (this.reconnectTimer) return;

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      console.log(`[DigitalTwinWS] Reconnecting in ${this.reconnectDelay}ms...`);
      this.connect().catch(e => {
        console.error('[DigitalTwinWS] Reconnect failed:', e);
      });
    }, this.reconnectDelay);
  }

  /** 获取连接状态 */
  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}

// ==================== 默认车型数据 ====================

function getDefaultVehicleTypes(): UnifiedVehicleInfo[] {
  return [
    {
      vehicle_type: 'standard',
      display_name: '标准搬运AGV',
      icon: 'TruckOutlined',
      color: '#1890ff',
      max_load_kg: 100,
      max_speed_ms: 1.5,
      lifting_height_m: 0,
      turning_radius_m: 0.5,
      width_m: 0.8,
      length_m: 1.2,
      battery_capacity_kwh: 2.0,
      supports_docking: true,
      supports_conveyor: true,
      narrow_corridor_only: false,
      navigation_methods: ['qr_code'],
      active_count: 15,
      total_count: 20,
    },
    {
      vehicle_type: 'forklift',
      display_name: '叉车AGV',
      icon: 'ToolOutlined',
      color: '#fa8c16',
      max_load_kg: 1000,
      max_speed_ms: 1.0,
      lifting_height_m: 2.0,
      turning_radius_m: 1.5,
      width_m: 1.2,
      length_m: 2.0,
      battery_capacity_kwh: 4.0,
      supports_docking: false,
      supports_conveyor: false,
      narrow_corridor_only: false,
      navigation_methods: ['laser', 'slam'],
      active_count: 3,
      total_count: 5,
    },
    {
      vehicle_type: 'latent',
      display_name: '潜伏AGV',
      icon: 'EyeInvisibleOutlined',
      color: '#722ed1',
      max_load_kg: 300,
      max_speed_ms: 2.0,
      lifting_height_m: 0,
      turning_radius_m: 0.3,
      width_m: 0.6,
      length_m: 0.9,
      battery_capacity_kwh: 1.5,
      supports_docking: true,
      supports_conveyor: true,
      narrow_corridor_only: true,
      navigation_methods: ['qr_code', 'magnetic'],
      active_count: 25,
      total_count: 30,
    },
    {
      vehicle_type: 'lift',
      display_name: '顶升AGV',
      icon: 'VerticalAlignTopOutlined',
      color: '#52c41a',
      max_load_kg: 500,
      max_speed_ms: 1.5,
      lifting_height_m: 0.15,
      turning_radius_m: 0.5,
      width_m: 0.9,
      length_m: 1.3,
      battery_capacity_kwh: 2.5,
      supports_docking: true,
      supports_conveyor: true,
      narrow_corridor_only: false,
      navigation_methods: ['qr_code', 'laser'],
      active_count: 10,
      total_count: 12,
    },
    {
      vehicle_type: 'sorter',
      display_name: '分拣AGV',
      icon: 'UnorderedListOutlined',
      color: '#eb2f96',
      max_load_kg: 50,
      max_speed_ms: 2.5,
      lifting_height_m: 0,
      turning_radius_m: 0.4,
      width_m: 0.7,
      length_m: 1.0,
      battery_capacity_kwh: 1.2,
      supports_docking: true,
      supports_conveyor: true,
      narrow_corridor_only: false,
      navigation_methods: ['qr_code', 'slam'],
      active_count: 20,
      total_count: 25,
    },
    {
      vehicle_type: 'towing',
      display_name: '牵引AGV',
      icon: 'PullRequestOutlined',
      color: '#13c2c2',
      max_load_kg: 2000,
      max_speed_ms: 1.2,
      lifting_height_m: 0,
      turning_radius_m: 1.0,
      width_m: 1.0,
      length_m: 1.5,
      battery_capacity_kwh: 5.0,
      supports_docking: false,
      supports_conveyor: false,
      narrow_corridor_only: false,
      navigation_methods: ['magnetic', 'laser'],
      active_count: 2,
      total_count: 3,
    },
  ];
}

// 导出单例
export const digitalTwinWsManager = new DigitalTwinWsManager();

// ==================== 辅助工具 ====================

/** 批量转换V1 AGV列表 */
export function batchTransformV1Agvs(agvs: import('./api').AgvStatus[]): UnifiedAgvStatus[] {
  return agvs.map(transformV1ToUnified);
}

/** 合并V1静态数据和V2动态更新 */
export function mergeAgvData(
  staticData: UnifiedAgvStatus[],
  dynamicUpdate: Partial<UnifiedAgvStatus>
): UnifiedAgvStatus[] {
  return staticData.map(agv =>
    agv.id === dynamicUpdate.id
      ? { ...agv, ...dynamicUpdate, last_update_ts: Date.now() }
      : agv
  );
}
