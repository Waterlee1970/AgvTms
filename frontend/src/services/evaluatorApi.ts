/**
 * 算法评测 API 服务
 * 对接后端 /api/v2/evaluator/* 接口
 * 
 * 功能:
 *  - 算法列表查询
 *  - 场景生成(预设/自定义)
 *  - 单场景/批量评估
 *  - 带轨迹的可视化评估(调度动画)
 */

import axios from 'axios';

const evalApi = axios.create({
  baseURL: '/api/v2/evaluator',
  timeout: 180000, // 评测可能耗时较长，特别是带轨迹的可视化
  headers: { 'Content-Type': 'application/json' },
});

// ==================== 类型定义 ====================

export interface AlgorithmInfo {
  name: string;
  display_name: string;
  category: 'heuristic' | 'meta_heuristic' | 'optimization' | 'learning' | 'hybrid';
  version: string;
  description: string;
  capabilities: string[];
  is_available: boolean;
  required_deps: string[];
}

export interface ScenarioPreset {
  name: string;
  type: string;
  difficulty: 'easy' | 'medium' | 'hard' | 'extreme';
  grid_size: [number, number];
  agvs: number;
  tasks: number;
  has_conveyor: boolean;
}

export interface ScenarioData {
  metadata: {
    scenario_id: string;
    name: string;
    scenario_type: string;
    difficulty: string;
    num_nodes: number;
    num_edges: number;
    num_agvs: number;
    num_tasks: number;
    tags: string[];
    has_conveyor?: boolean;
    conveyor_tasks?: number;
    fault_injection?: FaultInjectionConfig;
  };
  nodes: Array<{ id: string; x: number; y: number; type?: string; name?: string }>;
  edges: Array<{ from: string; to: string; weight?: number; id?: string }>;
  tasks: Array<{
    id: string;
    pickup_node_id?: string;
    pickup_node?: string;
    dropoff_node_id?: string;
    dropoff_node?: string;
    priority: number;
    status?: string;
    task_type?: string;
    estimated_duration?: number;
    is_urgent?: boolean;
  }>;
  agvs: Array<{
    id: string;
    current_node_id?: string;
    current_node?: string;
    battery_level?: number;
    battery?: number;
    status: string;
    capacity?: number;
    speed?: number;
    fault_config?: unknown;
  }>;
  conveyor_tasks?: Array<{
    id: string;
    from_segment_id: string;
    to_segment_id: string;
    quantity: number;
    priority: number;
    status: string;
    item_type: string;
  }>;
  conveyor_segments?: Array<{
    id: string;
    node_id: string;
    speed_mps: number;
    capacity: number;
    status: string;
  }>;
}

// ==================== 故障配置类型 ====================

export interface FaultInjectionConfig {
  enabled: boolean;
  fault_type: 'agv_breakdown' | 'conveyor_jam' | 'node_blocked' | 'batch_fault';
  faulty_agv_indices: number[];
  faulty_segment_indices: number[];
  blocked_node_ids: string[];
  fault_time: number;
  fault_duration: number;
}

// ==================== 自定义场景配置 ====================

export interface CustomScenarioConfig {
  grid_rows: number;
  grid_cols: number;
  num_agvs: number;
  agv_speed_min: number;
  agv_speed_max: number;
  battery_min: number;
  battery_max: number;
  agv_capacity: number;
  num_tasks: number;
  high_priority_ratio: number;
  task_duration_min: number;
  task_duration_max: number;
  has_conveyor: boolean;
  num_conveyor_tasks: number;
  num_conveyor_segments: number;
  conveyor_speed: number;
  scenario_type: string;
  difficulty: string;
  fault_injection: FaultInjectionConfig | null;
  seed: number;
}

// ==================== 评测结果类型 ====================

export interface DimensionScore {
  category: string;
  name: string;
  score: number;
  max_score: number;
  normalized: number;
  details: Record<string, number>;
}

export interface AlgorithmScoreCard {
  algorithm_name: string;
  display_name: string;
  scenario: string;
  scenario_type: string;
  total_score: number;
  rank: number;
  grade: 'A+' | 'A' | 'B+' | 'B' | 'C' | 'D';
  dimensions: Record<string, DimensionScore>;
  raw_metrics: Record<string, unknown>;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export interface ComparisonReportData {
  scenario: string;
  type: string;
  metadata: Record<string, unknown>;
  results: Record<string, AlgorithmScoreCard>;
  rankings: Array<[string, number]>;
  winner: string;
  summary: string;
  timestamp: string;
  execution_time_ms: number;
}

export interface RadarChartData {
  labels: string[];
  datasets: Array<{
    name: string;
    values: number[];
    score: number;
  }>;
}

// ==================== 轨迹数据类型 (可视化用) ====================

export interface AgvTrajectoryState {
  agv_id: string;
  node_id: string;
  position: [number, number];
  state: string; // idle/moving/busy/fault/charging
  battery: number;
  current_task_id: string | null;
  path: string[];
  progress: number;
}

export interface TaskTrajectoryState {
  task_id: string;
  status: string; // pending/assigned/in_progress/completed
  pickup_node_id: string;
  dropoff_node_id: string;
  assigned_agv_id: string | null;
  priority: number;
  progress: number;
}

export interface ConveyorSnapshotState {
  segment_id: string;
  node_id: string;
  status: string;
  speed_mps: number;
  capacity: number;
  current_load: number;
}

export interface TimeStepData {
  step: number;
  simulation_time: number;
  agvs: AgvTrajectoryState[];
  tasks: TaskTrajectoryState[];
  conveyors: ConveyorSnapshotState[];
  active_faults: Array<{
    type: string;
    entity_id: string;
    since_step: number;
    estimated_recovery: number;
  }>;
  metrics: {
    completion_rate: number;
    active_agvs: number;
    idle_agvs: number;
    fault_agvs: number;
    total_distance: number;
    avg_battery: number;
  };
}

export interface SimulationEvent {
  time_step: number;
  event_type: string;
  entity_id: string;
  description: string;
  position: [number, number] | null;
  extra?: Record<string, unknown>;
}

export interface TrajectoryData {
  scenario_name: string;
  algorithm_name: string;
  total_steps: number;
  time_per_step: number;
  snapshots: TimeStepData[];
  events: SimulationEvent[];
  summary: {
    total_simulation_time: number;
    total_tasks: number;
    completed_tasks: number;
    completion_rate: number;
    total_distance: number;
    total_events: number;
    fault_injected: boolean;
    peak_active_agvs: number;
  };
}

export interface VisualizationResult {
  success: boolean;
  report: ComparisonReportData;
  radar_data: RadarChartData;
  trajectory: TrajectoryData;
  visualization_algo: string;
}

export interface BatchEvaluationSummary {
  task_id: string;
  status: 'completed' | 'running' | 'failed';
  summary: {
    num_scenarios_tested: number;
    overall_rankings: Record<string, number>;
    best_by_scenario_type: Record<string, string>;
    recommendations: string[];
    summary: string;
  };
  overall_rankings: Record<string, number>;
  best_by_type: Record<string, string>;
  recommendations: string[];
  report_markdown: string;
  num_scenarios: number;
}

// ==================== 场景生成响应 ====================

export interface GenerateScenarioResponse {
  success: boolean;
  scenario: ScenarioData;
  metadata: {
    id: string;
    name: string;
    type: string;
    difficulty: string;
    nodes: number;
    edges: number;
    agvs: number;
    tasks: number;
    has_conveyor?: boolean;
    conveyor_tasks?: number;
    fault_injection?: FaultInjectionConfig;
  };
}

// ==================== API 函数 ====================

/** 获取所有注册算法列表 */
export const getAlgorithms = () =>
  evalApi.get<{ algorithms: AlgorithmInfo[]; total: number; available: number }>('/algorithms')
    .then(r => r.data);

/** 获取场景预设列表 */
export const getPresetList = () =>
  evalApi.get<{ presets: ScenarioPreset[]; total: number }>('/presets')
    .then(r => r.data);

/**
 * 基于预设模板生成测试场景
 */
export const generateScenario = (
  presetName: string,
  seed?: number,
  overrides?: Record<string, unknown>
) =>
  evalApi.post('/scenarios/generate', {
    preset_name: presetName,
    seed: seed ?? 42,
    overrides: overrides ?? {},
  }).then(r => r.data as GenerateScenarioResponse);

/**
 * 根据交互式参数生成自定义测试场景
 * 支持完整的AGV/TMS/任务/故障参数控制
 */
export const generateCustomScenario = (config: CustomScenarioConfig) =>
  evalApi.post('/scenarios/custom', config)
    .then(r => r.data as GenerateScenarioResponse);

/** 单场景多算法对比评估 */
export const evaluateSingle = (
  scenarioData: ScenarioData,
  algorithmNames?: string[]
) =>
  evalApi.post('/evaluate/single', {
    scenario_data: scenarioData,
    algorithm_names: algorithmNames,
  }).then(r => r.data as {
    success: boolean;
    task_id: string;
    report: ComparisonReportData;
    radar_data: RadarChartData;
    markdown: string;
  });

/**
 * 带轨迹的可视化评估
 * 返回完整的调度仿真轨迹用于前端Canvas动画
 */
export const evaluateWithVisualization = (
  scenarioData: ScenarioData,
  algorithmNames?: string[],
  simulationSteps?: number,
  faultConfig?: FaultInjectionConfig
) =>
  evalApi.post('/evaluate/visualize', {
    scenario_data: scenarioData,
    algorithm_names: algorithmNames,
    simulation_steps: simulationSteps ?? 150,
    fault_config: faultConfig ?? undefined,
  }).then(r => r.data as VisualizationResult & { task_id?: string });

/** 批量多场景多算法评估 */
export const evaluateBatch = (
  presetNames?: string[],
  algorithmNames?: string[],
  seed?: number,
  variantsPerType?: number
) =>
  evalApi.post('/evaluate/batch', {
    preset_names: presetNames,
    algorithm_names: algorithmNames,
    seed: seed ?? 42,
    variants_per_type: variantsPerType ?? 1,
  }).then(r => r.data as BatchEvaluationSummary);

/** 获取评测进度 */
export const getEvaluationProgress = (taskId: string) =>
  evalApi.get(`/evaluate/progress/${taskId}`).then(r => r.data as {
    task_id: string;
    stage: string;       // parsing | evaluating | simulating | comparing | completed | error
    message: string;
    percent: number;
    timestamp: number;
    data?: Record<string, unknown>;
  });

/** 获取最新评估报告 */
export const getLatestReport = () =>
  evalApi.get('/reports/latest').then(r => r.data);
