/**
 * API Service - HTTP client for AGV-TMS backend.
 */

import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// ---- Types ----

export interface MapNode {
  id: string;
  name: string;
  x: number;
  y: number;
  type: 'pickup' | 'dropoff' | 'charge' | 'cross' | 'path' | 'conveyor_in' | 'conveyor_out';
  capacity?: number;
}

export interface MapEdge {
  id?: string;
  from_node: string;
  to_node: string;
  distance: number;
  direction: 'bidirectional' | 'forward' | 'backward';
  is_conveyor?: boolean;
  speed_limit?: number;
}

export interface MapGraph {
  nodes: MapNode[];
  edges: MapEdge[];
  name: string;
  version: number;
}

export interface AgvTask {
  id?: string;
  pickup_node: string;
  dropoff_node: string;
  priority: number;
  status?: string;
  assigned_agv?: string;
  create_time?: string;
  deadline?: string;
}

export interface AgvStatus {
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
}

export interface AgvAssignment {
  agv_id: string;
  task_id: string;
  path: string[];
  path_cost: number;
  start_time: number;
  end_time: number;
  wait_times: number[];
}

export interface ConveyorTimelineEntry {
  task_id: string;
  segment_id: string;
  start_time: number;
  end_time: number;
  cargo_id: string;
}

export interface ScheduleMetrics {
  total_makespan: number;
  total_agv_travel_distance: number;
  total_conveyor_energy: number;
  agv_utilization: number;
  task_completion_rate: number;
  avg_task_wait_time: number;
  collision_count: number;
  conveyor_throughput: number;
}

export interface ScheduleResult {
  id?: string;
  assignments: AgvAssignment[];
  conveyor_timeline: ConveyorTimelineEntry[];
  total_cost: number;
  makespan: number;
  metrics: ScheduleMetrics;
  agv_paths: Record<string, string[]>;
  algorithm_runtime_ms: number;
}

export interface ConveyorSegment {
  id: string;
  name: string;
  from_node: string;
  to_node: string;
  speed: number;
  length: number;
  direction: string;
}

export interface AlgorithmConfig {
  aco: {
    num_ants: number;
    alpha: number;
    beta: number;
    evaporation_rate: number;
    iterations: number;
    q0: number;
  };
  sa: {
    initial_temp: number;
    cooling_rate: number;
    iterations: number;
    min_temp: number;
  };
  nlp: {
    solver: string;
    tolerance: number;
    max_iter: number;
    verbose: boolean;
  };
  hybrid: {
    aco_weight: number;
    sa_weight: number;
    nlp_weight: number;
    strategy: string;
  };
}

// ---- API Functions ----

export const runSchedule = (data?: {
  tasks?: AgvTask[];
  agvs?: AgvStatus[];
  conveyor_tasks?: Record<string, unknown>[];
}) =>
  api.post<ScheduleResult>('/schedule/run', data).then(r => r.data);

export const getScheduleResult = (id: string) =>
  api.get<ScheduleResult>(`/schedule/result/${id}`).then(r => r.data);

export const getAgvStatuses = () =>
  api.get<AgvStatus[]>('/agv/status').then(r => r.data);

export const updateAgvStatus = (agvId: string, updates: Record<string, unknown>) =>
  api.put(`/agv/${agvId}/status`, updates).then(r => r.data);

export const getMapGraph = () =>
  api.get<MapGraph>('/map/graph').then(r => r.data);

export const getMapNodes = () =>
  api.get<MapNode[]>('/map/nodes').then(r => r.data);

export const getMapEdges = () =>
  api.get<MapEdge[]>('/map/edges').then(r => r.data);

export const addMapNode = (node: MapNode) =>
  api.post<MapNode>('/map/node', node).then(r => r.data);

export const updateMapNode = (nodeId: string, node: MapNode) =>
  api.put<MapNode>(`/map/node/${nodeId}`, node).then(r => r.data);

export const deleteMapNode = (nodeId: string) =>
  api.delete(`/map/node/${nodeId}`).then(r => r.data);

export const addMapEdge = (edge: MapEdge) =>
  api.post<MapEdge>('/map/edge', edge).then(r => r.data);

export const deleteMapEdge = (edgeId: string) =>
  api.delete(`/map/edge/${edgeId}`).then(r => r.data);

export const getTasks = () =>
  api.get<AgvTask[]>('/tasks').then(r => r.data);

export const createTasks = (tasks: AgvTask[]) =>
  api.post<AgvTask[]>('/tasks', tasks).then(r => r.data);

export const getAlgorithmConfig = () =>
  api.get<AlgorithmConfig>('/algorithm/config').then(r => r.data);

export const updateAlgorithmConfig = (config: AlgorithmConfig) =>
  api.put<AlgorithmConfig>('/algorithm/config', config).then(r => r.data);

export const getMetrics = () =>
  api.get('/metrics').then(r => r.data);

export const resetSystem = () =>
  api.post('/schedule/reset').then(r => r.data);

export const getConveyorSegments = () =>
  api.get<ConveyorSegment[]>('/conveyor/segments').then(r => r.data);
