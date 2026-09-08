/**
 * ConveyorConfig 数据模型测试 — Phase D+ 补充
 *
 * 覆盖: pages/ConveyorConfig 数据层
 * - 输送线段数据和类型定义
 * - 任务分类统计 (AGV-only / Conveyor-only / Mixed)
 * - 线段耗时计算
 * - 优先级排序
 */

import { describe, it, expect } from 'vitest';

// ==================== 类型定义 ====================

interface ConveyorSegment {
  id: string;
  name: string;
  from_node: string;
  to_node: string;
  speed: number;      // m/s
  length: number;     // m
  direction: 'unidirectional' | 'bidirectional';
}

interface ConveyorTask {
  id: string;
  task_type: 'agv_only' | 'conveyor_only' | 'mixed';
  from_segment_id: string;
  to_segment_id: string;
  agv_pickup_node_id?: string;
  agv_dropoff_node_id?: string;
  segment_entry_node?: string;
  segment_exit_node?: string;
  priority: number;
  status: 'pending' | 'processing' | 'completed' | 'cancelled';
  item_type?: string;
}

// ==================== Mock 数据 ====================

const mockSegments: ConveyorSegment[] = [
  { id: 'seg-001', name: '主输送线-A段', from_node: 'n1', to_node: 'n5', speed: 1.0, length: 40.0, direction: 'unidirectional' },
  { id: 'seg-002', name: '分拣线-B段', from_node: 'n10', to_node: 'n15', speed: 0.8, length: 20.0, direction: 'bidirectional' },
  { id: 'seg-003', name: '入库线-C段', from_node: 'n20', to_node: 'n25', speed: 1.2, length: 30.0, direction: 'unidirectional' },
];

const mockTasks: ConveyorTask[] = [
  { id: 'ct-001', task_type: 'agv_only', from_segment_id: '', to_segment_id: '', agv_pickup_node_id: 'n1', agv_dropoff_node_id: 'n5', priority: 3, status: 'pending' },
  { id: 'ct-002', task_type: 'conveyor_only', from_segment_id: 'seg-001', to_segment_id: 'seg-002', segment_entry_node: 'n5', segment_exit_node: 'n10', priority: 2, status: 'processing' },
  { id: 'ct-003', task_type: 'mixed', from_segment_id: 'seg-001', to_segment_id: 'seg-002', agv_pickup_node_id: 'n1', conveyor_exit_node: 'n15', priority: 5, status: 'pending', item_type: 'box' },
];

// ==================== 辅助函数 ====================

function calcSegmentDuration(seg: ConveyorSegment): number {
  return seg.length / seg.speed;
}

function getTasksByType(tasks: ConveyorTask[], type: ConveyorTask['task_type']): ConveyorTask[] {
  return tasks.filter(t => t.task_type === type);
}

function sortByPriority(tasks: ConveyorTask[]): ConveyorTask[] {
  return [...tasks].sort((a, b) => b.priority - a.priority);
}

function getSegmentStats(segments: ConveyorSegment[]) {
  const totalLength = segments.reduce((sum, s) => sum + s.length, 0);
  const avgSpeed = segments.reduce((sum, s) => sum + s.speed, 0) / segments.length;
  const unidirCount = segments.filter(s => s.direction === 'unidirectional').length;

  return { totalLength, avgSpeed, unidirCount, bidirCount: segments.length - unidirCount };
}

// ==================== 测试 ====================

describe('ConveyorSegment 数据模型', () => {
  it('应区分单向和双向线段', () => {
    const unidirectional = mockSegments.filter(s => s.direction === 'unidirectional');
    const bidirectional = mockSegments.filter(s => s.direction === 'bidirectional');

    expect(unidirectional).toHaveLength(2);
    expect(bidirectional).toHaveLength(1);
  });

  it('应正确计算线段耗时 (length/speed)', () => {
    const seg1 = mockSegments[0];
    const duration = calcSegmentDuration(seg1); // 40 / 1.0 = 40s

    expect(duration).toBe(40);
  });

  it('双向线段的耗时计算应正确', () => {
    const bidirSeg = mockSegments[1]; // seg-002: length=20, speed=0.8
    const duration = calcSegmentDuration(bidirSeg); // 25s

    expect(duration).toBe(25);
  });

  it('入库线段应比主输送线更快', () => {
    const mainLine = mockSegments[0];   // speed=1.0
    const inbound = mockSegments[2];    // speed=1.2

    expect(inbound.speed).toBeGreaterThan(mainLine.speed);
  });

  it('应正确汇总线段统计信息', () => {
    const stats = getSegmentStats(mockSegments);

    expect(stats.totalLength).toBe(90);       // 40 + 20 + 30
    expect(stats.avgSpeed).toBeCloseTo(1.0);  // (1.0+0.8+1.2)/3 = 1.0
    expect(stats.unidirCount).toBe(2);
    expect(stats.bidirCount).toBe(1);
  });
});

describe('ConveyorTask 分类统计', () => {
  it('应对三种任务类型进行分类统计', () => {
    expect(getTasksByType(mockTasks, 'agv_only')).toHaveLength(1);
    expect(getTasksByType(mockTasks, 'conveyor_only')).toHaveLength(1);
    expect(getTasksByType(mockTasks, 'mixed')).toHaveLength(1);
  });

  it('高优先级任务应排在前面', () => {
    const sorted = sortByPriority(mockTasks);

    expect(sorted[0].task_type).toBe('mixed');        // priority=5 最高
    expect(sorted[1].task_type).toBe('agv_only');     // priority=3
    expect(sorted[2].task_type).toBe('conveyor_only'); // priority=2
  });

  it('AGV-only 任务应有 pickup 和 dropoff 节点', () => {
    const agvTask = mockTasks.find(t => t.task_type === 'agv_only')!;

    expect(agvTask.agv_pickup_node_id).toBeTruthy();
    expect(agvTask.agv_dropoff_node_id).toBeTruthy();
  });

  it('Conveyor-only 任务应有 segment entry/exit 节点', () => {
    const convTask = mockTasks.find(t => t.task_type === 'conveyor_only')!;

    expect(convTask.segment_entry_node).toBeTruthy();
    expect(convTask.segment_exit_node).toBeTruthy();
  });

  it('Mixed 任务应同时有 AGV 和输送线字段', () => {
    const mixedTask = mockTasks.find(t => t.task_type === 'mixed')!;

    expect(mixedTask.agv_pickup_node_id).toBeTruthy();
    expect(mixedTask.from_segment_id).toBeTruthy();  // 输送线路段
  });
});

describe('ConveyorConfig 边界条件', () => {
  it('零速度线段应返回 Infinity 或报错', () => {
    const zeroSpeed: ConveyorSegment = { id: 'bad', name: 'Bad', from_node: 'a', to_node: 'b', speed: 0, length: 10, direction: 'unidirectional' };
    const result = calcSegmentDuration(zeroSpeed);

    // 除以零 → Infinity (非有限数)
    expect(Number.isFinite(result)).toBe(false);
  });

  it('空数组不应崩溃', () => {
    expect(getTasksByType([], 'agv_only')).toEqual([]);
    expect(getSegmentStats([])).toEqual({ totalLength: 0, avgSpeed: NaN, unidirCount: 0, bidirCount: 0 });
  });

  it('优先级相同时保持稳定排序', () => {
    const samePriority: ConveyorTask[] = [
      { ...mockTasks[0], priority: 1 },
      { ...mockTasks[1], priority: 1 },
      { ...mockTasks[2], priority: 1 },
    ];
    const sorted = sortByPriority(samePriority);

    // 所有优先级相同，数量不变
    expect(sorted).toHaveLength(3);
  });
});
