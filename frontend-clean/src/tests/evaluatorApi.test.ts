/**
 * Evaluator API Service 测试 — Phase D+ 补充
 *
 * 覆盖: evaluatorApi.ts
 * - 类型定义完整性验证
 * - 场景配置构造/验证
 * - 故障注入配置
 * - 评测结果类型
 */

import { describe, it, expect } from 'vitest';
import type {
  AlgorithmInfo,
  ScenarioPreset,
  ScenarioData,
  FaultInjectionConfig,
  CustomScenarioConfig,
  DimensionScore,
  AlgorithmScoreCard,
  ComparisonReportData,
} from '../services/evaluatorApi';

// ==================== 类型定义验证 ====================

describe('AlgorithmInfo 类型', () => {
  it('应接受有效的算法信息', () => {
    const info: AlgorithmInfo = {
      name: 'theta_star',
      display_name: 'Theta* 路径规划',
      category: 'heuristic',
      version: '2.0.1',
      description: '任意角度LOS检测的A*变体',
      capabilities: ['path_planning', 'los_check', 'smoothing'],
      is_available: true,
      required_deps: [],
    };

    expect(info.category).toBe('heuristic');
    expect(info.is_available).toBe(true);
  });

  it('应支持所有算法分类', () => {
    const categories: AlgorithmInfo['category'][] = [
      'heuristic', 'meta_heuristic', 'optimization', 'learning', 'hybrid',
    ];
    for (const cat of categories) {
      const info: AlgorithmInfo = {
        name: `algo-${cat}`,
        display_name: cat,
        category: cat,
        version: '1.0',
        description: '',
        capabilities: [],
        is_available: true,
        required_deps: [],
      };
      expect(info.category).toBe(cat);
    }
  });
});

describe('ScenarioPreset 类型', () => {
  it('应接受预设场景配置', () => {
    const preset: ScenarioPreset = {
      name: 'Standard Warehouse',
      type: 'warehouse',
      difficulty: 'medium',
      grid_size: [20, 30],
      agvs: 5,
      tasks: 15,
      has_conveyor: true,
    };

    expect(preset.grid_size).toEqual([20, 30]);
    expect(preset.difficulty).toBe('medium');
  });

  it('应支持所有难度级别', () => {
    const difficulties: ScenarioPreset['difficulty'][] = ['easy', 'medium', 'hard', 'extreme'];
    for (const diff of difficulties) {
      const preset: ScenarioPreset = {
        name: diff,
        type: 'test',
        difficulty: diff,
        grid_size: [10, 10],
        agvs: 1,
        tasks: 1,
        has_conveyor: false,
      };
      expect(preset.difficulty).toBe(diff);
    }
  });
});

describe('ScenarioData 完整性', () => {
  it('应构建完整场景数据', () => {
    const scenario: ScenarioData = {
      metadata: {
        scenario_id: 'sc-001',
        name: 'Test Scene',
        scenario_type: 'warehouse',
        difficulty: 'hard',
        num_nodes: 25,
        num_edges: 40,
        num_agvs: 6,
        num_tasks: 18,
        tags: ['conveyor', 'multi_agv'],
        has_conveyor: true,
        conveyor_tasks: 6,
        fault_injection: {
          enabled: true,
          fault_type: 'agv_breakdown',
          faulty_agv_indices: [1],
          faulty_segment_indices: [],
          blocked_node_ids: [],
          fault_time: 120,
          fault_duration: 30,
        },
      },
      nodes: [
        { id: 'n1', x: 0, y: 0, type: 'pickup', name: 'Station A' },
        { id: 'n2', x: 10, y: 0, type: 'dropoff', name: 'Station B' },
      ],
      edges: [
        { from: 'n1', to: 'n2', weight: 10, id: 'e1' },
      ],
      tasks: [
        {
          id: 't1',
          pickup_node_id: 'n1',
          dropoff_node_id: 'n2',
          priority: 5,
          status: 'pending',
          task_type: 'agv_only',
          estimated_duration: 60,
          is_urgent: true,
        },
      ],
      agvs: [
        { id: 'a1', current_node_id: 'n1', battery_level: 95, status: 'idle', capacity: 100, speed: 1.5 },
      ],
    };

    expect(scenario.metadata.scenario_id).toBe('sc-001');
    expect(scenario.nodes).toHaveLength(2);
    expect(scenario.tasks[0].is_urgent).toBe(true);
    expect(scenario.agvs[0].speed).toBe(1.5);
  });

  it('应支持无故障注入的场景', () => {
    const scenario: ScenarioData = {
      metadata: {
        scenario_id: 'no-fault',
        name: 'No Fault',
        scenario_type: 'simple',
        difficulty: 'easy',
        num_nodes: 9,
        num_edges: 12,
        num_agvs: 2,
        num_tasks: 4,
        tags: [],
        fault_injection: null,
      },
      nodes: [{ id: 'n1', x: 0, y: 0 }],
      edges: [{ from: 'n1', to: 'n1', weight: 1 }],
      tasks: [{ id: 't1', pickup_node_id: 'n1', dropoff_node_id: 'n1', priority: 1, status: 'pending' }],
      agvs: [{ id: 'a1', status: 'idle' }],
    };

    expect(scenario.metadata.fault_injection).toBeNull();
  });
});

describe('FaultInjectionConfig', () => {
  it('应支持所有故障类型', () => {
    const faultTypes: FaultInjectionConfig['fault_type'][] = [
      'agv_breakdown', 'conveyor_jam', 'node_blocked', 'batch_fault',
    ];
    for (const ft of faultTypes) {
      const config: FaultInjectionConfig = {
        enabled: true,
        fault_type: ft,
        faulty_agv_indices: [0],
        faulty_segment_indices: [],
        blocked_node_ids: [],
        fault_time: 60,
        fault_duration: 20,
      };
      expect(config.fault_type).toBe(ft);
    }
  });

  it('禁用时应有合理的默认值', () => {
    const disabled: FaultInjectionConfig = {
      enabled: false,
      fault_type: 'agv_breakdown',
      faulty_agv_indices: [],
      faulty_segment_indices: [],
      blocked_node_ids: [],
      fault_time: 0,
      fault_duration: 0,
    };

    expect(disabled.enabled).toBe(false);
    expect(disabled.faulty_agv_indices).toEqual([]);
  });
});

describe('CustomScenarioConfig', () => {
  it('应构建有效自定义场景配置', () => {
    const config: CustomScenarioConfig = {
      grid_rows: 25,
      grid_cols: 35,
      num_agvs: 8,
      agv_speed_min: 0.5,
      agv_speed_max: 2.5,
      battery_min: 20,
      battery_max: 100,
      agv_capacity: 200,
      num_tasks: 24,
      high_priority_ratio: 0.2,
      task_duration_min: 30,
      task_duration_max: 300,
      has_conveyor: true,
      num_conveyor_tasks: 8,
      num_conveyor_segments: 4,
      conveyor_speed: 1.0,
      mixed_ratio: 0.25,
      agv_only_ratio: 0.375,
      conveyor_only_ratio: 0.375,
      scenario_type: 'factory_floor',
      difficulty: 'extreme',
      fault_injection: {
        enabled: true,
        fault_type: 'batch_fault',
        faulty_agv_indices: [0, 2],
        faulty_segment_indices: [1],
        blocked_node_ids: ['n5'],
        fault_time: 180,
        fault_duration: 60,
      },
      seed: 42,
    };

    expect(config.num_agvs).toBe(8);
    expect(config.has_conveyor).toBe(true);
    expect(config.mixed_ratio + config.agv_only_ratio + config.conveyor_only_ratio).toBeCloseTo(1.0);
    expect(config.seed).toBe(42);
  });

  it('混合任务占比之和应接近 1.0', () => {
    const config: CustomScenarioConfig = {
      grid_rows: 10, grid_cols: 10, num_agvs: 2,
      agv_speed_min: 1, agv_speed_max: 2, battery_min: 50, battery_max: 100,
      agv_capacity: 100, num_tasks: 5, high_priority_ratio: 0.1,
      task_duration_min: 30, task_duration_max: 120,
      has_conveyor: false, num_conveyor_tasks: 0, num_conveyor_segments: 0,
      conveyor_speed: 0, scenario_type: 'simple', difficulty: 'easy',
      fault_injection: null, seed: 1,
    };

    // 默认值验证
    const total = (config.mixed_ratio ?? 0.25) +
                   (config.agv_only_ratio ?? 0.375) +
                   (config.conveyor_only_ratio ?? 0.375);
    expect(total).toBeGreaterThan(0.9);
    expect(total).toBeLessThanOrEqual(1.05); // 允许小误差
  });
});

describe('评测结果类型', () => {
  it('DimensionScore 应包含完整评分信息', () => {
    const score: DimensionScore = {
      category: 'efficiency',
      name: 'Task Completion Rate',
      score: 92,
      max_score: 100,
      normalized: 0.92,
      details: {
        completed: 23,
        total: 25,
        on_time_rate: 0.87,
      },
    };

    expect(score.normalized).toBe(score.score / score.max_score);
    expect(score.details.completed).toBe(23);
  });

  it('AlgorithmScoreCard 应包含评级系统', () => {
    const grades: AlgorithmScoreCard['grade'][] = ['A+', 'A', 'B+', 'B', 'C', 'D'];
    for (const grade of grades) {
      const card: AlgorithmScoreCard = {
        algorithm_name: 'test-algo',
        display_name: 'Test Algo',
        scenario: 'sc-001',
        scenario_type: 'warehouse',
        total_score: grade === 'A+' ? 98 : grade === 'D' ? 35 : 70,
        rank: 1,
        grade,
        dimensions: {},
        raw_metrics: {},
        metadata: {},
        timestamp: new Date().toISOString(),
      };
      expect(card.grade).toBe(grade);
    }
  });

  it('ComparisonReportData 应包含对比结果', () => {
    const report: ComparisonReportData = {
      scenario: 'benchmark-scene',
      type: 'comparison',
      metadata: { runner_version: '2.0' },
      results: {
        theta_star: {
          algorithm_name: 'theta_star',
          display_name: 'Theta*',
          scenario: 'benchmark-scene',
          scenario_type: 'warehouse',
          total_score: 88,
          rank: 1,
          grade: 'A',
          dimensions: {},
          raw_metrics: {},
          metadata: {},
          timestamp: new Date().toISOString(),
        },
        aco: {
          algorithm_name: 'aco',
          display_name: 'ACO',
          scenario: 'benchmark-scene',
          scenario_type: 'warehouse',
          total_score: 72,
          rank: 2,
          grade: 'B',
          dimensions: {},
          raw_metrics: {},
          metadata: {},
          timestamp: new Date().toISOString(),
        },
      },
      rankings: [['theta_star', 88], ['aco', 72]],
      winner: 'theta_star',
      summary: 'Theta* outperforms ACO by 22%',
      timestamp: new Date().toISOString(),
      execution_time_ms: 1250,
    };

    expect(report.results.theta_star.rank).toBe(1);
    expect(report.winner).toBe('theta_star');
    expect(report.execution_time_ms).toBeGreaterThan(0);
    expect(report.rankings[0][0]).toBe('theta_star');
  });
});
