/**
 * Zustand Store 状态管理测试
 *
 * 测试目标:
 * - 验证 Store 初始状态
 * - 验证所有 setter 函数正确更新状态
 * - 验证状态隔离性
 * - 验证 loading 状态的独立 key 管理
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useStore } from '../store/useStore';

describe('Zustand Store', () => {

  // 每个 test 重置 store 状态
  beforeEach(() => {
    useStore.setState({
      agvs: [],
      tasks: [],
      conveyorTasks: [],
      mapNodes: [],
      mapEdges: [],
      conveyorSegments: [],
      scheduleResult: null,
      algorithmConfig: null,
      loading: {},
      metrics: null,
    });
  });

  // ===== AGV 状态 =====

  describe('AGV State', () => {
    it('should initialize with empty agvs array', () => {
      const state = useStore.getState();
      expect(state.agvs).toEqual([]);
      expect(state.agvs).toHaveLength(0);
    });

    it('setAgvs should update agvs array', () => {
      const mockAgvs = [
        { id: 'agv1', name: 'AGV-01', x: 10, y: 20, battery: 85, status: 'idle' as const },
        { id: 'agv2', name: 'AGV-02', x: 5, y: 15, battery: 60, status: 'moving' as const },
      ];

      useStore.getState().setAgvs(mockAgvs);

      expect(useStore.getState().agvs).toEqual(mockAgvs);
      expect(useStore.getState().agvs).toHaveLength(2);
    });

    it('setAgvs should replace entire array (not merge)', () => {
      useStore.getState().setAgvs([{ id: 'a1' } as any]);
      useStore.getState().setAgvs([{ id: 'a2' } as any]);

      expect(useStore.getState().agvs).toHaveLength(1);
      expect(useStore.getState().agvs[0].id).toBe('a2');
    });
  });

  // ===== Task 状态 =====

  describe('Task State', () => {
    it('should initialize with empty tasks array', () => {
      expect(useStore.getState().tasks).toEqual([]);
    });

    it('setTasks should update tasks', () => {
      const mockTasks = [
        { id: 't1', pickup_node: 'N_P00', dropoff_node: 'N_D03', priority: 5, status: 'pending' },
      ];

      useStore.getState().setTasks(mockTasks as any);

      expect(useStore.getState().tasks).toEqual(mockTasks);
    });
  });

  // ===== ConveyorTask 状态 =====

  describe('ConveyorTask State', () => {
    it('should initialize with empty conveyorTasks', () => {
      expect(useStore.getState().conveyorTasks).toEqual([]);
    });

    it('setConveyorTasks should update all three types', () => {
      const mockCT = [
        { id: 'ct1', task_type: 'agv_only' as const, quantity: 10, priority: 5, status: 'pending' },
        { id: 'ct2', task_type: 'conveyor_only' as const, quantity: 5, priority: 3, status: 'completed' },
        { id: 'ct3', task_type: 'mixed' as const, quantity: 8, priority: 8, status: 'in_progress' },
      ];

      useStore.getState().setConveyorTasks(mockCT as any);

      const ct = useStore.getState().conveyorTasks;
      expect(ct).toHaveLength(3);
      expect(ct.find(t => t.task_type === 'agv_only')?.task_type).toBe('agv_only');
      expect(ct.find(t => t.task_type === 'conveyor_only')?.task_type).toBe('conveyor_only');
      expect(ct.find(t => t.task_type === 'mixed')?.task_type).toBe('mixed');
    });
  });

  // ===== Map 状态 =====

  describe('Map State', () => {
    it('should initialize with empty mapNodes and mapEdges', () => {
      const state = useStore.getState();
      expect(state.mapNodes).toEqual([]);
      expect(state.mapEdges).toEqual([]);
    });

    it('setMapNodes should update nodes independently', () => {
      const nodes = [
        { id: 'n1', name: 'A点', x: 0, y: 0, type: 'pickup' as const },
        { id: 'n2', name: 'B点', x: 10, y: 10, type: 'dropoff' as const },
      ];

      useStore.getState().setMapNodes(nodes as any);

      expect(useStore.getState().mapNodes).toHaveLength(2);
      expect(useStore.getState().mapEdges).toHaveLength(0); // edges 不受影响
    });

    it('setMapEdges should update edges independently', () => {
      const edges = [
        { from_node: 'n1', to_node: 'n2', distance: 14.14, direction: 'bidirectional' as const },
      ];

      useStore.getState().setMapEdges(edges as any);

      expect(useStore.getState().mapEdges).toHaveLength(1);
      expect(useStore.getState().mapNodes).toHaveLength(0); // nodes 不受影响
    });
  });

  // ===== ConveyorSegment 状态 =====

  describe('ConveyorSegment State', () => {
    it('should initialize empty', () => {
      expect(useStore.getState().conveyorSegments).toEqual([]);
    });

    it('setConveyorSegments should update segments', () => {
      const segs = [
        { id: 's1', name: '主传送带', speed: 1.0, length: 50 } as any,
      ];

      useStore.getState().setConveyorSegments(segs);

      expect(useStore.getState().conveyorSegments).toEqual(segs);
    });
  });

  // ===== ScheduleResult 状态 =====

  describe('ScheduleResult State', () => {
    it('should initialize as null', () => {
      expect(useStore.getState().scheduleResult).toBeNull();
    });

    it('setScheduleResult should set and clear result', () => {
      const mockResult = {
        assignments: [{ agv_id: 'agv1', task_id: 't1', path: ['n1', 'n2'], path_cost: 14.14 }],
        makespan: 45.2,
        total_cost: 100.5,
        metrics: { agv_utilization: 0.85 },
        algorithm_runtime_ms: 120,
      } as any;

      useStore.getState().setScheduleResult(mockResult);
      expect(useStore.getState().scheduleResult).toEqual(mockResult);
      expect(useStore.getState().scheduleResult?.makespan).toBe(45.2);

      useStore.getState().setScheduleResult(null);
      expect(useStore.getState().scheduleResult).toBeNull();
    });
  });

  // ===== AlgorithmConfig 状态 =====

  describe('AlgorithmConfig State', () => {
    it('should initialize as null', () => {
      expect(useStore.getState().algorithmConfig).toBeNull();
    });

    it('setAlgorithmConfig should set config', () => {
      const mockConfig = {
        aco: { num_ants: 10, alpha: 1, beta: 2, evaporation_rate: 0.1, iterations: 100, q0: 0.9 },
        sa: { initial_temp: 1000, cooling_rate: 0.95, iterations: 500, min_temp: 0.001 },
        nlp: { solver: 'ipopt', tolerance: 1e-6, max_iter: 1000, verbose: false },
        hybrid: { aco_weight: 0.4, sa_weight: 0.3, nlp_weight: 0.3, strategy: 'adaptive' },
      };

      useStore.getState().setAlgorithmConfig(mockConfig as any);

      expect(useStore.getState().algorithmConfig).toEqual(mockConfig);
      expect(useStore.getState().algorithmConfig?.aco.num_ants).toBe(10);
    });
  });

  // ===== Loading 状态 =====

  describe('Loading State', () => {
    it('should initialize as empty object', () => {
      expect(useStore.getState().loading).toEqual({});
    });

    it('setLoading should set independent keys', () => {
      useStore.getState().setLoading('dashboard', true);
      useStore.getState().setLoading('tasks', true);

      expect(useStore.getState().loading.dashboard).toBe(true);
      expect(useStore.getState().loading.tasks).toBe(true);
      expect(useStore.getState().loading.agv).toBeUndefined();
    });

    it('setLoading can toggle same key', () => {
      useStore.getState().setLoading('dashboard', true);
      expect(useStore.getState().loading.dashboard).toBe(true);

      useStore.getState().setLoading('dashboard', false);
      expect(useStore.getState().loading.dashboard).toBe(false);
    });

    it('multiple loading keys should not interfere', () => {
      useStore.setState({ loading: { dashboard: true, tasks: false, agv: true } });

      useStore.getState().setLoading('tasks', true);

      expect(useStore.getState().loading.dashboard).toBe(true); // 未变
      expect(useStore.getState().loading.tasks).toBe(true);     // 已更新
      expect(useStore.getState().loading.agv).toBe(true);       // 未变
    });
  });

  // ===== Metrics 状态 =====

  describe('Metrics State', () => {
    it('should initialize as null', () => {
      expect(useStore.getState().metrics).toBeNull();
    });

    it('setMetrics should set metrics object', () => {
      const mockMetrics = {
        cpu_usage: 45.2,
        memory_usage: 62.8,
        active_agvs: 4,
        active_tasks: 12,
      };

      useStore.getState().setMetrics(mockMetrics);

      expect(useStore.getState().metrics).toEqual(mockMetrics);
    });
  });

  // ===== 状态独立性验证 =====

  describe('State Isolation', () => {
    it('updating one state slice should not affect others', () => {
      // 设置初始数据
      useStore.getState().setAgvs([{ id: 'a1' }] as any);
      useStore.getState().setTasks([{ id: 't1' }] as any);
      useStore.getState().setMetrics({ test: 1 } as any);

      // 更新 agvs
      useStore.getState().setAgvs([{ id: 'a2' }] as any);

      // 验证其他状态未变
      expect(useStore.getState().agvs).toHaveLength(1);
      expect(useStore.getState().agvs[0].id).toBe('a2');
      expect(useStore.getState().tasks).toHaveLength(1);
      expect(useStore.getState().tasks[0].id).toBe('t1');
      expect(useStore.getState().metrics).toEqual({ test: 1 });
    });
  });
});
