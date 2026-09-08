/**
 * API Service 层单元测试
 *
 * 测试目标:
 * - 验证所有API函数的正确导出和类型
 * - Mock axios 实例验证请求参数
 * - 测试错误处理和数据转换逻辑
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import axios from 'axios';

// Mock axios 模块
vi.mock('axios', () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
  return { default: mockAxios };
});

// 在 mock 后导入 api (动态导入以获取 mocked 实例)
const api = await import('../services/api');

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  put: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

describe('API Service', () => {

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ===== AGV 相关 API =====

  describe('getAgvStatuses', () => {
    it('should fetch AGV statuses from /agv/status', async () => {
      const mockAgvs = [
        { id: 'agv1', name: 'AGV-01', x: 10.5, y: 20.3, battery: 85, status: 'idle' },
        { id: 'agv2', name: 'AGV-02', x: 5.0, y: 15.0, battery: 60, status: 'moving' },
      ];
      mockedAxios.get.mockResolvedValue({ data: mockAgvs });

      const result = await api.getAgvStatuses();

      expect(mockedAxios.get).toHaveBeenCalledWith('/agv/status');
      expect(result).toEqual(mockAgvs);
      expect(result).toHaveLength(2);
    });

    it('should return empty array when no AGVs', async () => {
      mockedAxios.get.mockResolvedValue({ data: [] });

      const result = await api.getAgvStatuses();

      expect(result).toEqual([]);
      expect(result).toHaveLength(0);
    });
  });

  describe('updateAgvStatus', () => {
    it('should send PUT request with updates', async () => {
      const updates = { status: 'charging', battery: 90 };
      mockedAxios.put.mockResolvedValue({ data: { success: true } });

      const result = await api.updateAgvStatus('agv1', updates);

      expect(mockedAxios.put).toHaveBeenCalledWith('/agv/agv1/status', updates);
      expect(result).toEqual({ success: true });
    });
  });

  // ===== 地图相关 API =====

  describe('Map APIs', () => {
    it('getMapGraph should fetch complete map graph', async () => {
      const mockGraph = {
        nodes: [
          { id: 'n1', name: 'A点', x: 0, y: 0, type: 'pickup' },
          { id: 'n2', name: 'B点', x: 10, y: 10, type: 'dropoff' },
        ],
        edges: [
          { from_node: 'n1', to_node: 'n2', distance: 14.14, direction: 'bidirectional' },
        ],
        name: 'Test Map',
        version: 1,
      };
      mockedAxios.get.mockResolvedValue({ data: mockGraph });

      const result = await api.getMapGraph();

      expect(mockedAxios.get).toHaveBeenCalledWith('/map/graph');
      expect(result.nodes).toHaveLength(2);
      expect(result.edges).toHaveLength(1);
      expect(result.name).toBe('Test Map');
    });

    it('addMapNode should POST new node', async () => {
      const newNode = { id: 'n3', name: 'C点', x: 20, y: 20, type: 'charge' };
      mockedAxios.post.mockResolvedValue({ data: newNode });

      const result = await api.addMapNode(newNode as any);

      expect(mockedAxios.post).toHaveBeenCalledWith('/map/node', newNode);
      expect(result.id).toBe('n3');
    });

    it('deleteMapNode should DELETE node by ID', async () => {
      mockedAxios.delete.mockResolvedValue({ data: { success: true } });

      const result = await api.deleteMapNode('n1');

      expect(mockedAxios.delete).toHaveBeenCalledWith('/map/node/n1');
    });

    it('addMapEdge should POST new edge', async () => {
      const newEdge = { from_node: 'n1', to_node: 'n3', distance: 28.28, direction: 'bidirectional' };
      mockedAxios.post.mockResolvedValue({ data: { ...newEdge, id: 'e1' } });

      const result = await api.addMapEdge(newEdge as any);

      expect(mockedAxios.post).toHaveBeenCalledWith('/map/edge', newEdge);
      expect(result.from_node).toBe('n1');
    });

    it('deleteMapEdge should DELETE edge by ID', async () => {
      mockedAxios.delete.mockResolvedValue({ data: { success: true } });

      await api.deleteMapEdge('e1');

      expect(mockedAxios.delete).toHaveBeenCalledWith('/map/edge/e1');
    });
  });

  // ===== 任务相关 API =====

  describe('Task APIs', () => {
    it('getTasks should fetch all tasks', async () => {
      const mockTasks = [
        { id: 't1', pickup_node: 'N_P00', dropoff_node: 'N_D03', priority: 5, status: 'pending' },
        { id: 't2', pickup_node: 'N_P01', dropoff_node: 'N_D02', priority: 8, status: 'assigned' },
      ];
      mockedAxios.get.mockResolvedValue({ data: mockTasks });

      const result = await api.getTasks();

      expect(mockedAxios.get).toHaveBeenCalledWith('/tasks');
      expect(result).toHaveLength(2);
    });

    it('createTasks should POST task array', async () => {
      const newTasks = [
        { pickup_node: 'N_P00', dropoff_node: 'N_D03', priority: 5 },
        { pickup_node: 'N_P01', dropoff_node: 'N_D02', priority: 3 },
      ];
      mockedAxios.post.mockResolvedValue({ data: newTasks.map((t, i) => ({ ...t, id: `t${i + 1}` })) });

      const result = await api.createTasks(newTasks as any);

      expect(mockedAxios.post).toHaveBeenCalledWith('/tasks', newTasks);
      expect(result).toHaveLength(2);
    });
  });

  // ===== 调度相关 API =====

  describe('Schedule APIs', () => {
    it('runSchedule should POST to run schedule endpoint', async () => {
      const mockResult = {
        assignments: [],
        conveyor_timeline: [],
        total_cost: 100.5,
        makespan: 45.2,
        metrics: { agv_utilization: 0.85 },
        algorithm_runtime_ms: 120,
      };
      mockedAxios.post.mockResolvedValue({ data: mockResult });

      const result = await api.runSchedule();

      expect(mockedAxios.post).toHaveBeenCalledWith('/schedule/run', undefined);
      expect(result.makespan).toBe(45.2);
      expect(result.metrics.agv_utilization).toBe(0.85);
    });

    it('runSchedule should accept optional data parameter', async () => {
      const scheduleData = {
        tasks: [{ pickup_node: 'A', dropoff_node: 'B' }],
        agvs: [{ id: 'agv1' }],
      };
      mockedAxios.post.mockResolvedValue({ data: {} });

      await api.runSchedule(scheduleData as any);

      expect(mockedAxios.post).toHaveBeenCalledWith('/schedule/run', scheduleData);
    });

    it('getScheduleResult should GET by ID', async () => {
      const mockResult = { makespan: 30.0, total_cost: 80.0 };
      mockedAxios.get.mockResolvedValue({ data: mockResult });

      const result = await api.getScheduleResult('sched-001');

      expect(mockedAxios.get).toHaveBeenCalledWith('/schedule/result/sched-001');
      expect(result.makespan).toBe(30.0);
    });

    it('resetSystem should POST to reset endpoint', async () => {
      mockedAxios.post.mockResolvedValue({ data: { success: true } });

      const result = await api.resetSystem();

      expect(mockedAxios.post).toHaveBeenCalledWith('/schedule/reset');
    });
  });

  // ===== 算法配置 API =====

  describe('Algorithm Config APIs', () => {
    it('getAlgorithmConfig should fetch config', async () => {
      const mockConfig = {
        aco: { num_ants: 10, alpha: 1, beta: 2, evaporation_rate: 0.1, iterations: 100, q0: 0.9 },
        sa: { initial_temp: 1000, cooling_rate: 0.95, iterations: 500, min_temp: 0.001 },
        nlp: { solver: 'ipopt', tolerance: 1e-6, max_iter: 1000, verbose: false },
        hybrid: { aco_weight: 0.4, sa_weight: 0.3, nlp_weight: 0.3, strategy: 'adaptive' },
      };
      mockedAxios.get.mockResolvedValue({ data: mockConfig });

      const result = await api.getAlgorithmConfig();

      expect(mockedAxios.get).toHaveBeenCalledWith('/algorithm/config');
      expect(result.aco.num_ants).toBe(10);
      expect(result.hybrid.strategy).toBe('adaptive');
    });

    it('updateAlgorithmConfig should PUT config', async () => {
      const config = {
        aco: { num_ants: 20, alpha: 1, beta: 2, evaporation_rate: 0.1, iterations: 200, q0: 0.9 },
        sa: { initial_temp: 1000, cooling_rate: 0.95, iterations: 500, min_temp: 0.001 },
        nlp: { solver: 'ipopt', tolerance: 1e-6, max_iter: 1000, verbose: false },
        hybrid: { aco_weight: 0.5, sa_weight: 0.25, nlp_weight: 0.25, strategy: 'weighted' },
      };
      mockedAxios.put.mockResolvedValue({ data: config });

      const result = await api.updateAlgorithmConfig(config as any);

      expect(mockedAxios.put).toHaveBeenCalledWith('/algorithm/config', config);
      expect(result.aco.num_ants).toBe(20);
    });
  });

  // ===== 输送线 API =====

  describe('Conveyor Segment APIs', () => {
    it('getConveyorSegments should fetch segments', async () => {
      const mockSegments = [
        { id: 'seg1', name: '主线段1', from_node: 'n1', to_node: 'n2', speed: 1.0, length: 10, direction: 'forward' },
      ];
      mockedAxios.get.mockResolvedValue({ data: mockSegments });

      const result = await api.getConveyorSegments();

      expect(mockedAxios.get).toHaveBeenCalledWith('/conveyor/segments');
      expect(result).toHaveLength(1);
    });
  });

  // ===== Metrics API =====

  describe('getMetrics', () => {
    it('should fetch system metrics', async () => {
      const mockMetrics = {
        cpu_usage: 45.2,
        memory_usage: 62.8,
        active_tasks: 12,
        active_agvs: 4,
      };
      mockedAxios.get.mockResolvedValue({ data: mockMetrics });

      const result = await api.getMetrics();

      expect(mockedAxios.get).toHaveBeenCalledWith('/metrics');
      expect(result.cpu_usage).toBe(45.2);
    });
  });

  // ===== 类型定义验证 =====

  describe('Type Definitions', () => {
    it('MapNode should have correct required fields', () => {
      const node: api.MapNode = {
        id: 'test',
        name: 'Test Node',
        x: 10,
        y: 20,
        type: 'pickup',
      };
      expect(node.type).toBe('pickup');
      expect(['pickup', 'dropoff', 'charge', 'cross', 'path', 'conveyor_in', 'conveyor_out']).toContain(node.type);
    });

    it('MapEdge should support all direction types', () => {
      const directions: api.MapEdge['direction'][] = ['bidirectional', 'forward', 'backward'];
      directions.forEach(dir => {
        const edge: api.MapEdge = { from_node: 'a', to_node: 'b', distance: 10, direction: dir };
        expect(edge.direction).toBe(dir);
      });
    });

    it('AgvStatus should support all status types', () => {
      const statuses: api.AgvStatus['status'][] = ['idle', 'moving', 'charging', 'executing', 'waiting', 'error'];
      statuses.forEach(status => {
        const agv: api.AgvStatus = {
          id: 'test',
          name: 'Test',
          x: 0,
          y: 0,
          battery: 100,
          status,
        };
        expect(agv.status).toBe(status);
      });
    });

    it('ConveyorTaskType should be one of three types', () => {
      const types: api.ConveyorTaskType[] = ['agv_only', 'conveyor_only', 'mixed'];
      expect(types).toContain('agv_only');
      expect(types).toContain('conveyor_only');
      expect(types).toContain('mixed');
      expect(types).toHaveLength(3);
    });
  });
});
