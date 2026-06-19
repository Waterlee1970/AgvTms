/**
 * V2 工业级算法 API 服务
 *
 * 对接后端 HybridScheduler + Theta* + ECBS/SIPP/D* 算法栈
 * 前端-内核一致性校准 P0 修复
 */

import axios from 'axios';

const v2Api = axios.create({
  baseURL: '/api/v2/advanced',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// ==================== V2 算法参数类型定义 ====================

export interface ThetaStarConfig {
  weight_heuristic: number;    // 启发式权重 (1.0=标准, >1.0贪心)
  los_check: boolean;           // Line-of-Sight 检查 (任意角度路径)
  smooth_factor: number;        // 路径平滑因子 (0~1)
  corner_penalty: number;       // 转弯惩罚权重
}

export interface EcbsConfig {
  omega: number;                // 冲突代价权重 (1.0~5.0)
  max_runtime_ms: number;       // 最大运行时间 (ms)
  conflict_limit: number;       // 冲突搜索上限
  restart_count: number;        // 重启次数
}

export interface SippConfig {
  safe_interval_margin: number; // 安全间隔边距 (秒)
  max_wait_time: number;        // 最大等待时间 (秒)
}

export interface DStarConfig {
  replan_threshold: number;     // 重规划阈值 (距离变化量)
  lookahead_factor: number;     // 预测距离因子
}

export interface HybridSchedulerConfig {
  mode: 'auto' | 'force_theta' | 'force_ecbs';  // 调度模式
  enable_fallback: boolean;      // 是否启用弹性降层
  fallback_layers: string[];     // 启用的降层列表
  theta_star: ThetaStarConfig;
  ecbs: EcbsConfig;
  sipp: SippConfig;
  d_star: DStarConfig;
}

// ==================== 交通管制系统 ====================

export interface TrafficZoneStatus {
  zone_id: string;
  locked_by: string | null;
  lock_type: 'exclusive' | 'shared';
  waiting_agvs: string[];
  congestion_level: 'low' | 'medium' | 'high' | 'critical';
}

export interface CongestionHeatmapData {
  node_id: string;
  x: number;
  y: number;
  congestion_score: number;   // 0~1
  agv_count: number;
  avg_wait_time: number;
}

export interface DeadlockCheckResult {
  has_deadlock: boolean;
  cycle_nodes: string[][];
  involved_agvs: string[];
  resolution_suggestions: string[];
}

export interface TrafficStats {
  total_zones: number;
  locked_zones: number;
  active_agvs: number;
  avg_congestion: number;
  deadlock_checks: number;
  deadlocks_detected: number;
  deadlocks_resolved: number;
}

// ==================== API 函数 ====================

/** 获取当前 HybridScheduler 配置 */
export const getHybridSchedulerConfig = () =>
  v2Api.get<HybridSchedulerConfig>('/hybrid-scheduler/config').then(r => r.data);

/** 更新 HybridScheduler 配置 */
export const updateHybridSchedulerConfig = (config: Partial<HybridSchedulerConfig>) =>
  v2Api.put<{ success: boolean; updated: HybridSchedulerConfig }>('/hybrid-scheduler/config', config).then(r => r.data);

/** 执行混合调度 (替代旧版 runSchedule) */
export const runHybridSchedule = (params?: { tasks?: any[]; agvs?: any[]; force_mode?: string }) =>
  v2Api.post<any>('/hybrid-scheduler/run', params || {}).then(r => r.data);

/** 获取降层历史 */
export const getFallbackHistory = () =>
  v2Api.get<any[]>('/hybrid-scheduler/fallback-history').then(r => r.data);

// ==================== 交通管制 API ====================

/** 获取所有区域锁状态 */
export const getTrafficZones = () =>
  v2Api.get<TrafficZoneStatus[]>('/traffic/zones').then(r => r.data);

/** 获取拥堵热力图数据 */
export const getCongestionHeatmap = () =>
  v2Api.get<CongestionHeatmapData[]>('/traffic/congestion').then(r => r.data);

/** 检测死锁 */
export const checkDeadlock = () =>
  v2Api.get<DeadlockCheckResult>('/traffic/deadlock/check').then(r => r.data);

/** 解决死锁 */
export const resolveDeadlock = (strategy?: string) =>
  v2Api.post<{ success: boolean; resolved: boolean }>('/traffic/deadlock/resolve', { strategy }).then(r => r.data);

/** 获取交通统计 */
export const getTrafficStats = () =>
  v2Api.get<TrafficStats>('/traffic/stats').then(r => r.data);

/** 强制释放所有锁 (紧急操作) */
export const releaseAllLocks = () =>
  v2Api.post<{ success: boolean; released_count: number }>('/traffic/release-all').then(r => r.data);
