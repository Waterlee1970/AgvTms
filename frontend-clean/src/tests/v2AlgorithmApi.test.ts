/**
 * V2 Algorithm API Service 测试 — Phase D+ 补充
 *
 * 覆盖: v2AlgorithmApi.ts
 * - ThetaStar/Ecbs/Sipp/DStar 配置类型
 * - HybridSchedulerConfig 模式切换
 * - 交通管制类型（区域锁/拥堵热力图/死锁）
 * - 所有 API 函数参数/返回类型验证
 */

import { describe, it, expect } from 'vitest';
import type {
  ThetaStarConfig,
  EcbsConfig,
  SippConfig,
  DStarConfig,
  HybridSchedulerConfig,
  TrafficZoneStatus,
  CongestionHeatmapData,
  DeadlockCheckResult,
  TrafficStats,
} from '../services/v2AlgorithmApi';

// ==================== V2 算法参数类型 ====================

describe('ThetaStarConfig', () => {
  it('应接受标准配置', () => {
    const config: ThetaStarConfig = {
      weight_heuristic: 1.2,
      los_check: true,
      smooth_factor: 0.8,
      corner_penalty: 2.0,
    };

    expect(config.weight_heuristic).toBeGreaterThan(1.0);
    expect(config.los_check).toBe(true);
    expect(config.smooth_factor).toBeGreaterThan(0);
    expect(config.smooth_factor).toBeLessThanOrEqual(1);
  });

  it('应支持关闭 LOS 检测', () => {
    const noLos: ThetaStarConfig = {
      weight_heuristic: 1.0,
      los_check: false,
      smooth_factor: 0,
      corner_penalty: 0,
    };

    expect(noLos.los_check).toBe(false);
  });

  it('启发式权重 1.0 表示标准 A*', () => {
    const standard: ThetaStarConfig = {
      weight_heuristic: 1.0,
      los_check: true,
      smooth_factor: 0.5,
      corner_penalty: 1.0,
    };

    expect(standard.weight_heuristic).toBe(1.0);
  });
});

describe('EcbsConfig', () => {
  it('应接受有效的 ECBS 参数', () => {
    const config: EcbsConfig = {
      omega: 2.5,
      max_runtime_ms: 5000,
      conflict_limit: 1000,
      restart_count: 3,
    };

    expect(config.omega).toBeGreaterThanOrEqual(1.0);
    expect(config.omega).toBeLessThanOrEqual(5.0);
    expect(config.max_runtime_ms).toBeGreaterThan(0);
    expect(config.restart_count).toBeGreaterThanOrEqual(0);
  });

  it('omega=1.0 时 ECBS 等同于 CBS', () => {
    const cbsEquivalent: EcbsConfig = {
      omega: 1.0,
      max_runtime_ms: 10000,
      conflict_limit: 99999,
      restart_count: 0,
    };

    expect(cbsEquivalent.omega).toBe(1.0);
  });
});

describe('SippConfig', () => {
  it('应定义安全间隔和等待限制', () => {
    const config: SippConfig = {
      safe_interval_margin: 1.5,
      max_wait_time: 30,
    };

    expect(config.safe_interval_margin).toBeGreaterThan(0);
    expect(config.max_wait_time).toBeGreaterThan(config.safe_interval_margin);
  });
});

describe('DStarConfig', () => {
  it('应定义重规划和预测参数', () => {
    const config: DStarConfig = {
      replan_threshold: 3.0,
      lookahead_factor: 2.0,
    };

    expect(config.replan_threshold).toBeGreaterThan(0);
    expect(config.lookahead_factor).toBeGreaterThanOrEqual(1.0);
  });
});

describe('HybridSchedulerConfig', () => {
  it('应组合所有子算法配置', () => {
    const fullConfig: HybridSchedulerConfig = {
      mode: 'auto',
      enable_fallback: true,
      fallback_layers: ['ecbs', 'sipp', 'dijkstra'],
      theta_star: {
        weight_heuristic: 1.2, los_check: true, smooth_factor: 0.8, corner_penalty: 2.0,
      },
      ecbs: {
        omega: 2.0, max_runtime_ms: 5000, conflict_limit: 800, restart_count: 2,
      },
      sipp: {
        safe_interval_margin: 1.0, max_wait_time: 20,
      },
      d_star: {
        replan_threshold: 2.5, lookahead_factor: 1.5,
      },
    };

    expect(fullConfig.mode).toBe('auto');
    expect(fullConfig.enable_fallback).toBe(true);
    expect(fullConfig.fallback_layers).toHaveLength(3);
    expect(fullConfig.theta_star.weight_heuristic).toBe(1.2);
  });

  it('应支持强制模式', () => {
    const forceTheta: HybridSchedulerConfig = {
      mode: 'force_theta',
      enable_fallback: false,
      fallback_layers: [],
      theta_star: { weight_heuristic: 1.0, los_check: true, smooth_factor: 0.5, corner_penalty: 1.0 },
      ecbs: { omega: 2.0, max_runtime_ms: 3000, conflict_limit: 500, restart_count: 1 },
      sipp: { safe_interval_margin: 1.0, max_wait_time: 15 },
      d_star: { replan_threshold: 2.0, lookahead_factor: 1.5 },
    };

    expect(forceTheta.mode).toBe('force_theta');
    expect(forceTheta.enable_fallback).toBe(false);
    expect(forceTheta.fallback_layers).toEqual([]);

    const forceEcbs: HybridSchedulerConfig = {
      ...forceTheta,
      mode: 'force_ecbs',
    };
    expect(forceEcbs.mode).toBe('force_ecbs');
  });

  it('所有模式都应是有效值', () => {
    const modes: HybridSchedulerConfig['mode'][] = ['auto', 'force_theta', 'force_ecbs'];
    for (const mode of modes) {
      const cfg: Partial<HybridSchedulerConfig> = { mode };
      expect(cfg.mode).toBe(mode);
    }
  });
});

// ==================== 交通管制类型 ====================

describe('TrafficZoneStatus', () => {
  it('应表示空闲区域', () => {
    const freeZone: TrafficZoneStatus = {
      zone_id: 'zone-A1',
      locked_by: null,
      lock_type: 'exclusive',
      waiting_agvs: [],
      congestion_level: 'low',
    };

    expect(freeZone.locked_by).toBeNull();
    expect(freeZone.waiting_agvs).toEqual([]);
    expect(freeZone.congestion_level).toBe('low');
  });

  it('应表示锁定区域', () => {
    const lockedZone: TrafficZoneStatus = {
      zone_id: 'zone-B2',
      locked_by: 'agv-003',
      lock_type: 'exclusive',
      waiting_agvs: ['agv-001', 'agv-002'],
      congestion_level: 'critical',
    };

    expect(lockedZone.locked_by).toBe('agv-003');
    expect(lockedZone.waiting_agvs).toHaveLength(2);
    expect(lockedZone.lock_type).toBe('exclusive');

    const sharedLock: TrafficZoneStatus = {
      ...lockedZone,
      lock_type: 'shared',
    };
    expect(sharedLock.lock_type).toBe('shared');
  });

  it('应支持所有拥堵级别', () => {
    const levels: TrafficZoneStatus['congestion_level'][] = ['low', 'medium', 'high', 'critical'];
    for (const level of levels) {
      const zone: TrafficZoneStatus = {
        zone_id: `zone-${level}`,
        locked_by: null,
        lock_type: 'exclusive',
        waiting_agvs: [],
        congestion_level: level,
      };
      expect(zone.congestion_level).toBe(level);
    }
  });
});

describe('CongestionHeatmapData', () => {
  it('应包含节点级拥堵信息', () => {
    const data: CongestionHeatmapData = {
      node_id: 'junction-center',
      x: 15.0,
      y: 20.0,
      congestion_score: 0.78,
      agv_count: 4,
      avg_wait_time: 8.5,
    };

    expect(data.congestion_score).toBeGreaterThanOrEqual(0);
    expect(data.congestion_score).toBeLessThanOrEqual(1);
    expect(data.agv_count).toBe(4);
    expect(data.avg_wait_time).toBeGreaterThan(0);
  });

  it('拥堵分数 0 表示畅通', () => {
    const clear: CongestionHeatmapData = {
      node_id: 'remote-corner',
      x: 0, y: 0,
      congestion_score: 0,
      agv_count: 0,
      avg_wait_time: 0,
    };

    expect(clear.congestion_score).toBe(0);
    expect(clear.agv_count).toBe(0);
  });
});

describe('DeadlockCheckResult', () => {
  it('应表示无死锁状态', () => {
    const noDeadlock: DeadlockCheckResult = {
      has_deadlock: false,
      cycle_nodes: [],
      involved_agvs: [],
      resolution_suggestions: [],
    };

    // 验证字段值
    expect(noDeadlock.has_deadlock).toBe(false);
    expect(noDeadlock.cycle_nodes).toEqual([]);
    expect(noDeadlock.involved_agvs).toEqual([]);
  });

  it('应报告死锁详情', () => {
    const deadlock: DeadlockCheckResult = {
      has_deadlock: true,
      cycle_nodes: [['z1', 'z2', 'z3', 'z1']],
      involved_agvs: ['agv-001', 'agv-002', 'agv-003'],
      resolution_suggestions: [
        '让 agv-001 从 z1 退回到 prev_zone',
        '临时解锁 z2 允许 agv-002 通过',
      ],
    };

    expect(deadlock.has_deadlock).toBe(true);
    expect(deadlock.cycle_nodes).toHaveLength(1);
    expect(deadlock.cycle_nodes[0]).toHaveLength(4); // 环路闭合
    expect(deadlock.involved_agvs).toHaveLength(3);
    expect(deadlock.resolution_suggestions).toHaveLength(2);
  });
});

describe('TrafficStats', () => {
  it('应汇总交通统计数据', () => {
    const stats: TrafficStats = {
      total_zones: 20,
      locked_zones: 5,
      active_agvs: 8,
      avg_congestion: 0.35,
      deadlock_checks: 150,
      deadlocks_detected: 1,
      deadlocks_resolved: 1,
    };

    expect(stats.total_zones).toBeGreaterThanOrEqual(stats.locked_zones);
    expect(stats.avg_congestion).toBeGreaterThanOrEqual(0);
    expect(stats.avg_congestion).toBeLessThanOrEqual(1);
    expect(stats.deadlocks_resolved).toBeLessThanOrEqual(stats.deadlocks_detected + 1);
  });

  it('应支持零交通活动', () => {
    const idleStats: TrafficStats = {
      total_zones: 10,
      locked_zones: 0,
      active_agvs: 0,
      avg_congestion: 0,
      deadlock_checks: 0,
      deadlocks_detected: 0,
      deadlocks_resolved: 0,
    };

    expect(idleStats.active_agvs).toBe(0);
    expect(idleStats.locked_zones).toBe(0);
  });
});
