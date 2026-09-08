/**
 * 算法评测与可视化页面 - 完整版
 * 
 * 功能模块:
 *  1. [场景配置] 交互式样本参数设置 (AGV/TMS/任务/故障) — 分为两大类
 *     - 类别A: 纯AGV算法评测（不含输送线）
 *     - 类别B: AGV+TMS混合模式评测（含输送线协同任务）
 *  2. [算法评测] 多算法对比评估
 *  3. [调度可视化] Canvas实时调度过程动画
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Card, Table, Button, Select, Tag, Space, Row, Col,
  Statistic, Progress, Tabs, Alert, Spin, Collapse, Badge,
  Tooltip, Switch, Slider, InputNumber, Divider, Typography,
  message, Empty, Modal, Descriptions, Timeline, Radio, Form,
  Popover, Input, ConfigProvider
} from 'antd';
import {
  PlayCircleOutlined, ThunderboltOutlined, TrophyOutlined,
  BarChartOutlined, RadarChartOutlined, DownloadOutlined,
  ReloadOutlined, ExperimentOutlined, CheckCircleOutlined,
  CloseCircleOutlined, LoadingOutlined,
  InfoCircleOutlined, StarOutlined, FireOutlined,
  LineChartOutlined, DashboardOutlined, TeamOutlined,
  AimOutlined, ClockCircleOutlined, SafetyCertificateOutlined,
  SettingOutlined, EyeOutlined, PauseCircleOutlined,
  FastForwardOutlined, FastBackwardOutlined, UndoOutlined,
  WarningOutlined, ToolOutlined, CarOutlined, InboxOutlined,
  ApiOutlined, BugOutlined, AlertOutlined, StepForwardOutlined,
  FieldTimeOutlined, MergeCellsOutlined, SwapOutlined, TruckOutlined,
} from '@ant-design/icons';
import {
  getAlgorithms,
  getPresetList,
  generateScenario,
  generateCustomScenario,
  evaluateSingle,
  evaluateBatch,
  evaluateWithVisualization,
  getEvaluationProgress,
  type AlgorithmInfo,
  type ScenarioPreset,
  type ScenarioData,
  type ComparisonReportData,
  type RadarChartData,
  type AlgorithmScoreCard,
  type BatchEvaluationSummary,
  type CustomScenarioConfig,
  type FaultInjectionConfig,
  type TrajectoryData,
  type TimeStepData,
  type VisualizationResult,
} from '@/services/evaluatorApi';

const { Title, Text, Paragraph } = Typography;
const { Panel } = Collapse;
const { TabPane } = Tabs;

// ==================== 常量 ====================

/** 评测模式大类 */
export type EvalCategory = 'agv_only' | 'agv_tms_mixed';

const EVAL_CATEGORY_CONFIG: Record<EvalCategory, {
  label: string;
  icon: React.ReactNode;
  color: string;
  description: string;
  scenarioTypes: string[];
  defaultPresets: string[];
  hasConveyor: boolean;
}> = {
  agv_only: {
    label: '纯AGV算法',
    icon: <CarOutlined />,
    color: '#1890ff',
    description: '仅AGV路径规划与任务分配，不含输送线系统。适用于标准仓库、医院配送等纯移动机器人调度场景。',
    scenarioTypes: ['warehouse', 'hospital'],
    defaultPresets: ['small_warehouse', 'medium_warehouse', 'hospital_delivery'],
    hasConveyor: false,
  },
  agv_tms_mixed: {
    label: 'AGV+TMS混合模式',
    icon: <MergeCellsOutlined />,
    color: '#eb2f96',
    description: 'AGV与输送线(TMS)协同调度，包含三种任务类型(纯AGV/纯输送线/混合长程)。适用于工厂、港口等复杂物流场景。',
    scenarioTypes: ['factory', 'port', 'mixed'],
    defaultPresets: ['large_warehouse', 'factory_floor', 'port_logistics', 'mixed_scenario', 'stress_test'],
    hasConveyor: true,
  },
};

const GRADE_COLORS: Record<string, string> = {
  'A+': '#52c41a', 'A': '#73d13d', 'B+': '#1890ff',
  'B': '#40a9ff', 'C': '#faad14', 'D': '#ff4d4f',
};

const CATEGORY_MAP: Record<string, { label: string; color: string }> = {
  heuristic: { label: '启发式', color: '#1890ff' },
  meta_heuristic: { label: '元启发式', color: '#722ed1' },
  optimization: { label: '优化算法', color: '#13c2c2' },
  learning: { label: '强化学习', color: '#eb2f96' },
  hybrid: { label: '混合架构', color: '#fa8c16' },
};

const DIMENSION_LABELS: Record<string, string> = {
  efficiency: '效率指标',
  quality: '质量指标',
  resource: '资源利用',
  realtime: '实时性能',
  robustness: '鲁棒性',
};

// 默认自定义场景配置
const DEFAULT_CUSTOM_CONFIG: CustomScenarioConfig = {
  grid_rows: 15,
  grid_cols: 20,
  num_agvs: 15,
  agv_speed_min: 1.0,
  agv_speed_max: 3.0,
  battery_min: 30,
  battery_max: 100,
  agv_capacity: 1.0,
  num_tasks: 40,
  high_priority_ratio: 0.2,
  task_duration_min: 30,
  task_duration_max: 180,
  has_conveyor: false,
  num_conveyor_tasks: 10,
  num_conveyor_segments: 4,
  conveyor_speed: 0.5,
  mixed_ratio: 0.25,
  agv_only_ratio: 0.375,
  conveyor_only_ratio: 0.375,
  scenario_type: 'warehouse',
  difficulty: 'medium',
  fault_injection: null,
  seed: 42,
};

// ==================== 雷达图组件 (纯Canvas) ====================

interface RadarChartProps {
  data: RadarChartData;
  width?: number;
  height?: number;
}

const RadarChart: React.FC<RadarChartProps> = ({ data, width = 450, height = 380 }) => {
  const canvasRef = React.useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data.datasets.length) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const centerX = width / 2;
    const centerY = height / 2 + 10;
    const radius = Math.min(centerX, centerY) - 50;
    const labels = data.labels;
    const numAxes = labels.length;
    const angleStep = (Math.PI * 2) / numAxes;

    ctx.clearRect(0, 0, width, height);

    // 背景网格
    ctx.strokeStyle = 'rgba(24,144,255,0.15)';
    ctx.lineWidth = 1;
    for (let level = 1; level <= 5; level++) {
      const r = (radius / 5) * level;
      ctx.beginPath();
      for (let i = 0; i <= numAxes; i++) {
        const angle = i * angleStep - Math.PI / 2;
        const x = centerX + Math.cos(angle) * r;
        const y = centerY + Math.sin(angle) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.stroke();
    }

    // 轴线
    ctx.strokeStyle = 'rgba(24,144,255,0.25)';
    for (let i = 0; i < numAxes; i++) {
      const angle = i * angleStep - Math.PI / 2;
      ctx.beginPath();
      ctx.moveTo(centerX, centerY);
      ctx.lineTo(centerX + Math.cos(angle) * radius, centerY + Math.sin(angle) * radius);
      ctx.stroke();

      const labelX = centerX + Math.cos(angle) * (radius + 25);
      const labelY = centerY + Math.sin(angle) * (radius + 25);
      ctx.fillStyle = '#b0b0b0';
      ctx.font = '12px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(labels[i], labelX, labelY);
    }

    // 数据区域
    const colors = ['#1890ff', '#52c41a', '#faad14', '#eb2f96', '#722ed1'];

    data.datasets.forEach((dataset, di) => {
      const values = dataset.values;
      ctx.beginPath();
      ctx.fillStyle = colors[di % colors.length] + '25';
      ctx.strokeStyle = colors[di % colors.length];
      ctx.lineWidth = 2;

      for (let i = 0; i <= numAxes; i++) {
        const idx = i % numAxes;
        const val = values[idx] ?? 0;
        const r = (val / 100) * radius;
        const angle = idx * angleStep - Math.PI / 2;
        const x = centerX + Math.cos(angle) * r;
        const y = centerY + Math.sin(angle) * r;

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // 数据点
      for (let i = 0; i < numAxes; i++) {
        const val = values[i] ?? 0;
        const r = (val / 100) * radius;
        const angle = i * angleStep - Math.PI / 2;
        const x = centerX + Math.cos(angle) * r;
        const y = centerY + Math.sin(angle) * r;

        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = colors[di % colors.length];
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    });

    // 图例
    const legendY = height - 18;
    let legendX = 20;
    ctx.font = '11px -apple-system, sans-serif';
    data.datasets.forEach((dataset, di) => {
      const color = colors[di % colors.length];
      ctx.fillStyle = color;
      ctx.fillRect(legendX, legendY - 6, 14, 14);
      ctx.strokeStyle = '#fff';
      ctx.strokeRect(legendX, legendY - 6, 14, 14);
      ctx.fillStyle = '#ccc';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${dataset.name} (${dataset.score.toFixed(1)}分)`, legendX + 20, legendY + 2);
      legendX += ctx.measureText(`${dataset.name} (${dataset.score.toFixed(1)}分)`).width + 35;
    });
  }, [data, width, height]);

  return <canvas ref={canvasRef} width={width} height={height} style={{ maxWidth: '100%' }} />;
};

// ==================== 调度可视化组件 (Canvas调度动画) ====================

interface SchedulingVisualizerProps {
  scenario: ScenarioData | null;
  trajectory: TrajectoryData | null;
  selectedAlgorithm: string;
  width?: number;
  height?: number;
}

interface VisualizerControlState {
  isPlaying: boolean;
  currentStep: number;
  playSpeed: number;
  showPaths: boolean;
  showTaskLines: boolean;
  showGrid: boolean;
  selectedAgvId: string | null;
}

const SchedulingVisualizer: React.FC<SchedulingVisualizerProps> = ({
  scenario,
  trajectory,
  selectedAlgorithm,
  width = 900,
  height = 550,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number>(0);
  const lastTimeRef = useRef<number>(0);
  const hasAutoPlayed = useRef(false);

  const [ctrl, setCtrl] = useState<VisualizerControlState>({
    isPlaying: false,
    currentStep: 0,
    playSpeed: 2,
    showPaths: true,
    showTaskLines: true,
    showGrid: true,
    selectedAgvId: null,
  });

  // 自动播放：首次加载 trajectory 时自动开始播放
  useEffect(() => {
    if (trajectory && trajectory.total_steps > 0 && !hasAutoPlayed.current) {
      hasAutoPlayed.current = true;
      setTimeout(() => {
        setCtrl(prev => ({ ...prev, isPlaying: true, currentStep: 0 }));
      }, 300);
    }
  }, [trajectory?.total_steps]);

  const currentSnapshot = trajectory?.snapshots[ctrl.currentStep] || null;

  // 播放控制
  useEffect(() => {
    if (!ctrl.isPlaying || !trajectory || ctrl.currentStep >= trajectory.total_steps - 1) {
      if (ctrl.currentStep >= trajectory?.total_steps! - 1 && ctrl.isPlaying) {
        setCtrl(prev => ({ ...prev, isPlaying: false }));
      }
      return;
    }

    const animate = (timestamp: number) => {
      if (!lastTimeRef.current) lastTimeRef.current = timestamp;
      const elapsed = timestamp - lastTimeRef.current;

      // 根据播放速度调整帧率
      const interval = 150 / ctrl.playSpeed;
      if (elapsed >= interval) {
        lastTimeRef.current = timestamp;
        setCtrl(prev => ({
          ...prev,
          currentStep: Math.min(prev.currentStep + 1, (trajectory?.total_steps ?? 1) - 1),
        }));
      }

      animFrameRef.current = requestAnimationFrame(animate);
    };

    animFrameRef.current = requestAnimationFrame(animate);

    return () => cancelAnimationFrame(animFrameRef.current);
  }, [ctrl.isPlaying, trajectory?.total_steps, ctrl.playSpeed]);

  // Canvas绘制
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // 清空
    ctx.fillStyle = '#0f1419';
    ctx.fillRect(0, 0, width, height);

    // 场景数据未就绪时显示提示
    if (!scenario) {
      ctx.fillStyle = '#666';
      ctx.font = '14px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('正在加载场景数据...', width / 2, height / 2);
      return;
    }

    // 计算地图边界和缩放
    const nodes = scenario.nodes || [];
    if (nodes.length === 0) {
      ctx.fillStyle = '#666';
      ctx.font = '14px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('场景无节点数据', width / 2, height / 2);
      return;
    }

    // 构建节点映射（必须在绘制边之前完成）
    const nodeMap = new Map(nodes.map(n => [n.id, n]));

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    nodes.forEach(n => {
      minX = Math.min(minX, n.x);
      maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y);
      maxY = Math.max(maxY, n.y);
    });

    const padding = 50;
    const mapW = maxX - minX || 1;
    const mapH = maxY - minY || 1;
    const scale = Math.min((width - padding * 2) / mapW, (height - padding * 2) / mapH) * 0.85;
    const offsetX = padding + ((width - padding * 2) - mapW * scale) / 2 - minX * scale;
    const offsetY = padding + ((height - padding * 2) - mapH * scale) / 2 - minY * scale;

    const toScreen = (x: number, y: number) => ({ sx: x * scale + offsetX, sy: y * scale + offsetY });

    // 绘制网格背景
    if (ctrl.showGrid) {
      ctx.strokeStyle = 'rgba(255,255,255,0.03)';
      ctx.lineWidth = 0.5;
      for (let gx = Math.floor(minX); gx <= Math.ceil(maxX); gx++) {
        const p = toScreen(gx, 0);
        ctx.beginPath(); ctx.moveTo(p.sx, 0); ctx.lineTo(p.sx, height); ctx.stroke();
      }
      for (let gy = Math.floor(minY); gy <= Math.ceil(maxY); gy++) {
        const p = toScreen(0, gy);
        ctx.beginPath(); ctx.moveTo(0, p.sy); ctx.lineTo(width, p.sy); ctx.stroke();
      }
    }

    // 节点类型颜色映射
    const nodeTypeColors: Record<string, string> = {
      shelf: '#4a5568', workstation: '#4a5568', buffer: '#4a5568',
      perimeter: '#2d3748', gate: '#2d3748', quay_crane: '#2d3748',
      aisle: '#718096', corridor: '#718096', roadway: '#718096',
      charging: '#f6ad55', supply_center: '#48bb78',
      bottleneck: '#fc8181', zone_a: '#4299e1', zone_b: '#9f7aea',
      conveyor_path: '#90cdf4', sortation: '#68d391', staging: '#f6ad55',
      receiving_door: '#68d391', shipping_door: '#e53e3e',
      yard_block: '#667eea', pharmacy: '#68d391', laboratory: '#9f7aea',
      ward: '#63b3ed', surgery: '#fc8181', floor: '#4a5568',
    };

    // 绘制边
    ctx.strokeStyle = '#3a4556';
    ctx.lineWidth = 1;
    (scenario.edges || []).forEach(e => {
      const fromNode = nodeMap.get(e.from);
      const toNode = nodeMap.get(e.to);
      if (fromNode && toNode) {
        const f = toScreen(fromNode.x, fromNode.y);
        const t = toScreen(toNode.x, toNode.y);
        ctx.beginPath(); ctx.moveTo(f.sx, f.sy); ctx.lineTo(t.sx, t.sy); ctx.stroke();
      }
    });

    // 绘制输送线路段
    (scenario.conveyor_segments || []).forEach(seg => {
      const n = nodeMap.get(seg.node_id);
      if (n) {
        const p = toScreen(n.x, n.y);
        const isJammed = seg.status === 'jammed';

        ctx.fillStyle = isJammed ? 'rgba(229,62,62,0.25)' : 'rgba(237,137,54,0.15)';
        ctx.strokeStyle = isJammed ? '#e53e3e' : '#ed8936';
        ctx.lineWidth = 4;

        const hw = 12, hh = 6;
        ctx.fillRect(p.sx - hw / 2, p.sy - hh / 2, hw, hh);
        ctx.strokeRect(p.sx - hw / 2, p.sy - hh / 2, hw, hh);

        // 标签
        ctx.fillStyle = '#ed8936';
        ctx.font = '9px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(seg.id.replace('CONV_SEG_', 'C'), p.sx, p.sy - 8);
      }
    });

    // 绘制节点
    nodes.forEach(n => {
      const p = toScreen(n.x, n.y);
      const color = nodeTypeColors[n.type || ''] || '#4a5568';
      const radius = n.type === 'bottleneck' ? 5 : 3.5;

      ctx.beginPath();
      ctx.arc(p.sx, p.sy, radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();

      // 特殊节点标签
      if (['charging', 'bottleneck', 'supply_center'].includes(n.type || '')) {
        ctx.fillStyle = '#aaa';
        ctx.font = '8px -apple-system';
        ctx.textAlign = 'center';
        ctx.fillText(n.id.replace('N_', ''), p.sx, p.sy - 7);
      }
    });

    // 绘制任务连线 (pickup -> dropoff)
    if (ctrl.showTaskLines && currentSnapshot) {
      (currentSnapshot.tasks || []).forEach(task => {
        if (task.status === 'pending') return;

        const pickupNode = nodeMap.get(task.pickup_node_id);
        const dropoffNode = nodeMap.get(task.dropoff_node_id);
        if (!pickupNode || !dropoffNode) return;

        const ps = toScreen(pickupNode.x, pickupNode.y);
        const ds = toScreen(dropoffNode.x, dropoffNode.y);

        // 颜色按优先级
        const lineColor = task.priority >= 9 ? '#ff4d4f'
          : task.priority >= 7 ? '#faad14'
            : task.priority >= 5 ? '#1890ff'
              : '#52c41a';

        ctx.strokeStyle = lineColor + '60';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(ps.sx, ps.sy); ctx.lineTo(ds.sx, ds.sy); ctx.stroke();
        ctx.setLineDash([]);

        // 取货点标记
        ctx.beginPath(); ctx.arc(ps.sx, ps.sy, 5, 0, Math.PI * 2);
        ctx.fillStyle = lineColor + '40'; ctx.fill();
        ctx.strokeStyle = lineColor; ctx.lineWidth = 1; ctx.stroke();

        // 卸货点标记
        ctx.beginPath(); ctx.arc(ds.sx, ds.sy, 5, 0, Math.PI * 2);
        ctx.fillStyle = lineColor + '40'; ctx.fill();
        ctx.strokeStyle = lineColor; ctx.lineWidth = 1; ctx.stroke();
      });
    }

    // 绘制AGV路径
    if (ctrl.showPaths && currentSnapshot) {
      (currentSnapshot.agvs || []).forEach(agv => {
        if (!agv.path || agv.path.length < 2) return;

        ctx.strokeStyle = getAgvColor(agv.state) + '35';
        ctx.lineWidth = 2;
        ctx.beginPath();
        agv.path.forEach((nodeId, i) => {
          const node = nodeMap.get(nodeId);
          if (node) {
            const sp = toScreen(node.x, node.y);
            if (i === 0) ctx.moveTo(sp.sx, sp.sy);
            else ctx.lineTo(sp.sx, sp.sy);
          }
        });
        ctx.stroke();
      });
    }

    // 绘制AGV
    if (currentSnapshot) {
      (currentSnapshot.agvs || []).forEach(agv => {
        const pos = agv.position;
        if (!pos) return;

        const p = toScreen(pos[0], pos[1]);
        const baseColor = getAgvColor(agv.state);
        const isSelected = ctrl.selectedAgvId === agv.agv_id;
        const radius = isSelected ? 11 : 8.5;

        // AGV光晕(选中或移动中)
        if (isSelected || agv.state === 'moving') {
          ctx.beginPath();
          ctx.arc(p.sx, p.sy, radius + 4, 0, Math.PI * 2);
          ctx.fillStyle = baseColor + '20';
          ctx.fill();
        }

        // AGV本体
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, radius, 0, Math.PI * 2);
        ctx.fillStyle = baseColor;
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = isSelected ? 2 : 1.2;
        ctx.stroke();

        // 方向箭头(移动中)
        if (agv.state === 'moving' && agv.path && agv.path.length > 1) {
          const nextNodeId = agv.path[1] || agv.path[0];
          const nextNode = nodeMap.get(nextNodeId);
          if (nextNode) {
            const np = toScreen(nextNode.x, nextNode.y);
            const angle = Math.atan2(np.sy - p.sy, np.sx - p.sx);
            ctx.save();
            ctx.translate(p.sx, p.sy);
            ctx.rotate(angle);
            ctx.fillStyle = '#fff';
            ctx.beginPath();
            ctx.moveTo(radius - 2, 0);
            ctx.lineTo(radius - 6, -3);
            ctx.lineTo(radius - 6, 3);
            ctx.closePath();
            ctx.fill();
            ctx.restore();
          }
        }

        // 故障AGV闪烁效果
        if (agv.state === 'fault') {
          const flash = Math.sin(Date.now() / 200) > 0;
          if (flash) {
            ctx.beginPath();
            ctx.arc(p.sx, p.sy, radius + 6, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(255,77,79,0.6)';
            ctx.lineWidth = 2;
            ctx.stroke();
            
            // ⚠图标
            ctx.fillStyle = '#ff4d4f';
            ctx.font = 'bold 10px sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('!', p.sx, p.sy - radius - 8);
          }
        }

        // AGV ID标签
        ctx.fillStyle = '#fff';
        ctx.font = `${isSelected ? 'bold ' : ''}9px -apple-system`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const label = agv.agv_id.replace('AGV-', '');
        ctx.fillText(label, p.sx, p.sy);

        // 当前任务指示
        if (agv.current_task_id && agv.state !== 'idle') {
          ctx.fillStyle = '#faad14';
          ctx.font = '8px sans-serif';
          ctx.fillText('▶', p.sx + radius + 5, p.sy - 3);
        }
      });
    }

    // 绘制活跃故障区域
    if (currentSnapshot) {
      (currentSnapshot.active_faults || []).forEach(fault => {
        // 尝试找到故障实体位置
        let faultPos = null;
        
        if (fault.entity_id.startsWith('AGV')) {
          const faultAgv = (currentSnapshot.agvs || []).find(a => a.agv_id === fault.entity_id);
          if (faultAgv?.position) {
            faultPos = toScreen(faultAgv.position[0], faultAgv.position[1]);
          }
        } else if (fault.entity_id.startsWith('CONV_SEG')) {
          const seg = (scenario.conveyor_segments || []).find(s => s.id === fault.entity_id);
          const segNode = seg ? nodeMap.get(seg.node_id) : null;
          if (segNode) {
            faultPos = toScreen(segNode.x, segNode.y);
          }
        }

        if (faultPos) {
          // 故障范围圆
          ctx.beginPath();
          ctx.arc(faultPos.sx, faultPos.sy, 18, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,77,79,0.08)';
          ctx.fill();
          ctx.strokeStyle = 'rgba(255,77,79,0.5)';
          ctx.lineWidth = 1.5;
          ctx.setLineDash([4, 4]);
          ctx.stroke();
          ctx.setLineDash([]);

          // 故障计时器
          const remaining = (fault.estimated_recovery - ctrl.currentStep);
          if (remaining > 0) {
            ctx.fillStyle = '#ff4d4f';
            ctx.font = 'bold 9px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(`${remaining}s`, faultPos.sx, faultPos.sy + 26);
          }
        }
      });
    }

    // 信息面板背景
    ctx.fillStyle = 'rgba(15,20,25,0.85)';
    ctx.fillRect(width - 180, 10, 170, currentSnapshot ? 160 : 80);
    ctx.strokeStyle = 'rgba(24,144,255,0.2)';
    ctx.lineWidth = 1;
    ctx.strokeRect(width - 180, 10, 170, currentSnapshot ? 160 : 80);

    // 信息文字
    ctx.fillStyle = '#888';
    ctx.font = '11px -apple-system';
    ctx.textAlign = 'left';
    
    let infoY = 28;
    const drawInfo = (label: string, value: string, color?: string) => {
      ctx.fillStyle = '#888';
      ctx.fillText(label, width - 170, infoY);
      ctx.fillStyle = color || '#e0e0e0';
      ctx.textAlign = 'right';
      ctx.fillText(value, width - 16, infoY);
      ctx.textAlign = 'left';
      infoY += 17;
    };

    if (trajectory) {
      drawInfo(`步骤`, `${ctrl.currentStep + 1}/${trajectory.total_steps}`);
      drawInfo(`时间`, `${currentSnapshot?.simulation_time.toFixed(1) || '0'}s`);
      
      if (currentSnapshot) {
        const m = currentSnapshot.metrics;
        drawInfo(`完成率`, `${m.completion_rate.toFixed(1)}%`,
          m.completion_rate > 70 ? '#52c41a' : m.completion_rate > 30 ? '#faad14' : '#ff4d4f');
        drawInfo(`运行中AGV`, String(m.active_agvs), '#1890ff');
        drawInfo(`空闲AGV`, String(m.idle_agvs), '#888');
        if (m.fault_agvs > 0) {
          drawInfo(`故障AGV`, String(m.fault_agvs), '#ff4d4f');
        }
        drawInfo(`总距离`, `${m.total_distance.toFixed(0)}m`);
        drawInfo(`均电量`, `${m.avg_battery.toFixed(0)}%`,
          m.avg_battery < 30 ? '#ff4d4f' : m.avg_battery < 60 ? '#faad14' : '#52c41a');
      }
    }

    // 无数据提示
    if (!trajectory) {
      ctx.fillStyle = '#666';
      ctx.font = '14px -apple-system';
      ctx.textAlign = 'center';
      ctx.fillText('请先选择场景和算法并运行"可视化评测"', width / 2, height / 2);
    }

  }, [scenario, trajectory, ctrl.currentStep, ctrl.showPaths, ctrl.showTaskLines, ctrl.showGrid, ctrl.selectedAgvId, width, height]);

  // 辅助函数：获取AGV颜色
  const getAgvColor = (state: string): string => {
    switch (state) {
      case 'idle': return '#52c41a';
      case 'moving': return '#1890ff';
      case 'busy':
      case 'executing': return '#faad14';
      case 'fault': return '#ff4d4f';
      case 'charging': return '#722ed1';
      default: return '#888';
    }
  };

  // 控制函数
  const togglePlay = () => setCtrl(prev => ({ ...prev, isPlaying: !prev.isPlaying }));
  const stepForward = () => setCtrl(prev => ({
    ...prev,
    currentStep: Math.min(prev.currentStep + 1, (trajectory?.total_steps ?? 1) - 1),
  }));
  const stepBack = () => setCtrl(prev => ({ ...prev, currentStep: Math.max(0, prev.currentStep - 1) }));
  const reset = () => setCtrl(prev => ({ ...prev, currentStep: 0, isPlaying: false }));

  // 图例
  const legendItems = [
    { state: '空闲', color: '#52c41a', icon: '●' },
    { state: '移动中', color: '#1890ff', icon: '●' },
    { state: '执行中', color: '#faad14', icon: '●' },
    { state: '充电', color: '#722ed1', icon: '●' },
    { state: '故障', color: '#ff4d4f', icon: '●' },
  ];

  return (
    <div>
      {/* Canvas画布 */}
      <div style={{
        background: '#0f1419',
        borderRadius: 8,
        border: '1px solid #1e293b',
        overflow: 'hidden',
        position: 'relative',
      }}>
        <canvas
          ref={canvasRef}
          width={width}
          height={height}
          style={{ display: 'block', width: '100%', maxWidth: '100%' }}
        />

        {/* 控制栏 */}
        <div style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          padding: '10px 16px',
          background: 'linear-gradient(transparent, rgba(15,20,25,0.95))',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}>
          {/* 播放按钮 */}
          <Space size={4}>
            <Button
              size="small"
              icon={<UndoOutlined />}
              onClick={reset}
              title="重置"
            />
            <Button
              size="small"
              icon={<FastBackwardOutlined />}
              onClick={stepBack}
              title="上一步"
            />
            <Button
              type="primary"
              size="small"
              icon={ctrl.isPlaying ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
              onClick={togglePlay}
              title={ctrl.isPlaying ? '暂停' : '播放'}
              style={{ width: 36 }}
            />
            <Button
              size="small"
              icon={<FastForwardOutlined />}
              onClick={stepForward}
              title="下一步"
            />
          </Space>

          {/* 进度条 */}
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: '#888', fontSize: 11, whiteSpace: 'nowrap' }}>
              {ctrl.currentStep}/{trajectory?.total_steps || 0}
            </Text>
            <Slider
              min={0}
              max={(trajectory?.total_steps || 1) - 1}
              value={ctrl.currentStep}
              onChange={v => setCtrl(prev => ({ ...prev, currentStep: v, isPlaying: false }))}
              style={{ margin: 0, flex: 1 }}
              tooltip={{ formatter: v => `步骤 ${v}` }}
            />
          </div>

          {/* 速度 */}
          <Select
            size="small"
            value={ctrl.playSpeed}
            onChange={v => setCtrl(prev => ({ ...prev, playSpeed: v }))}
            style={{ width: 72 }}
          >
            <Select.Option value={0.25}>0.25x</Select.Option>
            <Select.Option value={0.5}>0.5x</Select.Option>
            <Select.Option value={1}>1x</Select.Option>
            <Select.Option value={2}>2x</Select.Option>
            <Select.Option value={5}>5x</Select.Option>
          </Select>

          {/* 显示选项 */}
          <Switch
            size="small"
            checked={ctrl.showPaths}
            onChange={c => setCtrl(prev => ({ ...prev, showPaths: c }))}
            checkedChildren="路径"
            unCheckedChildren="路径"
          />
          <Switch
            size="small"
            checked={ctrl.showTaskLines}
            onChange={c => setCtrl(prev => ({ ...prev, showTaskLines: c }))}
            checkedChildren="任务线"
            unCheckedChildren="任务线"
          />

          {/* 算法标签 */}
          {selectedAlgorithm && (
            <Tag color="blue" style={{ marginLeft: 8 }}>{selectedAlgorithm}</Tag>
          )}
        </div>
      </div>

      {/* 底部信息栏 */}
      <Row gutter={[12, 8]} style={{ marginTop: 10 }}>
        {/* 图例 */}
        <Col span={12}>
          <div style={{
            background: '#111827',
            borderRadius: 6,
            padding: '8px 14px',
            border: '1px solid #1e293b',
            display: 'flex',
            gap: 16,
            alignItems: 'center',
          }}>
            <Text type="secondary" style={{ fontSize: 11 }}>图例:</Text>
            {legendItems.map(item => (
              <span key={item.state} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
                <span style={{ color: item.color }}>{item.icon}</span>
                <Text type="secondary">{item.state}</Text>
              </span>
            ))}
          </div>
        </Col>

        {/* 当前事件 */}
        <Col span={12}>
          {trajectory && currentSnapshot && (
            <div style={{
              background: '#111827',
              borderRadius: 6,
              padding: '8px 14px',
              border: '1px solid #1e293b',
              maxHeight: 42,
              overflow: 'hidden',
            }}>
              {(trajectory.events || [])
                .filter(e => e.time_step <= ctrl.currentStep)
                .slice(-1)
                .map((ev, i) => (
                  <Text key={i} style={{ fontSize: 11 }}>
                    <Tag
                      style={{ fontSize: 10, marginRight: 6 }}
                      color={
                        ev.event_type.includes('fault') ? 'red'
                          : ev.event_type.includes('complete') ? 'green'
                            : ev.event_type.includes('assigned') ? 'blue'
                              : 'default'
                      }
                    >
                      步{ev.time_step}
                    </Tag>
                    <Text style={{ color: '#ccc' }}>{ev.description}</Text>
                  </Text>
                ))
              }
            </div>
          )}
        </Col>
      </Row>
    </div>
  );
};

// ==================== 主页面组件 ====================

const AlgorithmBenchmark: React.FC = () => {
  // 全局状态
  const [algorithms, setAlgorithms] = useState<AlgorithmInfo[]>([]);
  const [presets, setPresets] = useState<ScenarioPreset[]>([]);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [visualizing, setVisualizing] = useState(false);
  const [progressInfo, setProgressInfo] = useState<{
    stage: string;
    message: string;
    percent: number;
  } | null>(null);

  // 取消控制器 - 用于中止运行中的评测请求
  const abortControllerRef = useRef<AbortController | null>(null);

  // 场景配置状态
  const [evalCategory, setEvalCategory] = useState<EvalCategory>('agv_only'); // 评测类别
  const [selectedPresets, setSelectedPresets] = useState<string[]>(['medium_warehouse']);
  const [selectedAlgos, setSelectedAlgos] = useState<string[]>([]);
  const [seed, setSeed] = useState(42);
  const [variants, setVariants] = useState(1);
  const [evalMode, setEvalMode] = useState<'single' | 'batch'>('single');

  // 自定义场景配置
  const [customConfig, setCustomConfig] = useState<CustomScenarioConfig>({ ...DEFAULT_CUSTOM_CONFIG });
  const [useCustomConfig, setUseCustomConfig] = useState(false);
  const [faultEnabled, setFaultEnabled] = useState(false);

  // 结果状态
  const [currentReport, setCurrentReport] = useState<ComparisonReportData | null>(null);
  const [radarData, setRadarData] = useState<RadarChartData | null>(null);
  const [batchResult, setBatchResult] = useState<BatchEvaluationSummary | null>(null);
  const [currentScenario, setCurrentScenario] = useState<ScenarioData | null>(null);

  // 可视化状态
  const [vizResult, setVizResult] = useState<VisualizationResult | null>(null);
  const [activeMainTab, setActiveMainTab] = useState<string>('config');
  const [initError, setInitError] = useState<string | null>(null);

  // Fallback算法数据（当后端不可用时使用）
  const FALLBACK_ALGORITHMS: AlgorithmInfo[] = [
    { name: 'mopso', display_name: 'MOPSO多目标粒子群', category: 'meta_heuristic', version: '2.1', description: '多目标粒子群优化算法，适用于复杂调度场景', capabilities: ['multi_objective', 'conveyor_aware', 'fault_tolerant'], is_available: true, required_deps: [] },
    { name: 'genetic_algorithm', display_name: '遗传算法GA', category: 'meta_heuristic', version: '3.0', description: '基于遗传算法的智能调度方案', capabilities: ['multi_objective', 'priority_aware'], is_available: true, required_deps: [] },
    { name: 'greedy', display_name: '贪心算法', category: 'heuristic', version: '1.5', description: '快速贪心分配策略，适合实时响应', capabilities: ['fast', 'real_time'], is_available: true, required_deps: [] },
    { name: 'round_robin', display_name: '轮询分配', category: 'heuristic', version: '1.0', description: '简单的轮询任务分配算法', capabilities: ['simple', 'fair'], is_available: true, required_deps: [] },
    { name: 'ant_colony', display_name: '蚁群优化ACO', category: 'meta_heuristic', version: '2.0', description: '蚁群路径规划与任务分配联合优化', capabilities: ['path_optimization', 'load_balance'], is_available: true, required_deps: [] },
    { name: 'reinforcement_learning', display_name: '强化学习RL', category: 'learning', version: '1.8', description: '基于DQN的深度强化学习调度器(实验性)', capabilities: ['adaptive', 'learning'], is_available: false, required_deps: ['torch', 'gym'] },
    { name: 'simulated_annealing', display_name: '模拟退火SA', category: 'optimization', version: '1.6', description: '模拟退火全局搜索优化算法', capabilities: ['global_search'], is_available: true, required_deps: [] },
  ];

  const FALLBACK_PRESETS: ScenarioPreset[] = [
    { name: 'small_warehouse', type: 'warehouse', difficulty: 'easy', grid_size: [10, 12], agvs: 5, tasks: 15, has_conveyor: false },
    { name: 'medium_warehouse', type: 'warehouse', difficulty: 'medium', grid_size: [15, 20], agvs: 15, tasks: 40, has_conveyor: false },
    { name: 'large_warehouse', type: 'warehouse', difficulty: 'hard', grid_size: [25, 30], agvs: 30, tasks: 80, has_conveyor: true },
    { name: 'factory_floor', type: 'factory', difficulty: 'hard', grid_size: [20, 25], agvs: 20, tasks: 50, has_conveyor: true },
    { name: 'port_logistics', type: 'port', difficulty: 'extreme', grid_size: [30, 40], agvs: 40, tasks: 100, has_conveyor: true },
    { name: 'mixed_scenario', type: 'mixed', difficulty: 'medium', grid_size: [18, 22], agvs: 18, tasks: 45, has_conveyor: true },
    { name: 'hospital_delivery', type: 'hospital', difficulty: 'easy', grid_size: [12, 15], agvs: 8, tasks: 20, has_conveyor: false },
    { name: 'stress_test', type: 'warehouse', difficulty: 'extreme', grid_size: [20, 20], agvs: 25, tasks: 120, has_conveyor: true },
  ];

  // 初始化加载
  useEffect(() => {
    loadData();
  }, []);

  // 当评测类别切换时，自动调整配置
  useEffect(() => {
    const catConfig = EVAL_CATEGORY_CONFIG[evalCategory];
    // 自动切换 has_conveyor
    setCustomConfig(prev => ({
      ...prev,
      has_conveyor: catConfig.hasConveyor,
      scenario_type: catConfig.scenarioTypes[0],
      num_conveyor_tasks: catConfig.hasConveyor ? 12 : 0,
      num_conveyor_segments: catConfig.hasConveyor ? 4 : 0,
    }));
    // 切换到该类别推荐的默认预设
    setSelectedPresets(catConfig.defaultPresets.slice(0, 1));
  }, [evalCategory]);

  const loadData = async () => {
    try {
      const [algoRes, presetRes] = await Promise.all([
        getAlgorithms(),
        getPresetList(),
      ]);
      
      if (algoRes?.algorithms?.length > 0) {
        setAlgorithms(algoRes.algorithms);
        // 优先选可用算法，若全部不可用则不预选任何（让用户自己选择）
        const availableNames = algoRes.algorithms.filter((a: AlgorithmInfo) => a.is_available).map((a: AlgorithmInfo) => a.name);
        setSelectedAlgos(availableNames.length > 0 ? availableNames : []);
      } else {
        throw new Error('Empty algorithm list');
      }
      
      if (presetRes?.presets?.length > 0) {
        setPresets(presetRes.presets);
      } else {
        throw new Error('Empty preset list');
      }
      
      setInitError(null);
    } catch (e: any) {
      console.warn('API加载失败，使用内置数据:', e.message);
      // 使用fallback数据
      setAlgorithms(FALLBACK_ALGORITHMS);
      setPresets(FALLBACK_PRESETS);
      const availableNames = FALLBACK_ALGORITHMS.filter(a => a.is_available).map(a => a.name);
      setSelectedAlgos(availableNames);
      setInitError(`后端服务未连接 (${e.message || 'Network Error'})，当前使用演示模式`);
      message.warning('后端未连接，已切换到演示模式（可正常浏览界面）');
    }
  };

  // 进度轮询函数
  const pollProgress = useCallback(async (taskId: string) => {
    if (!taskId) return;
    try {
      const progress = await getEvaluationProgress(taskId);
      if (progress) {
        setProgressInfo({
          stage: progress.stage,
          message: progress.message,
          percent: progress.percent,
        });
      }
    } catch {
      // 忽略轮询错误
    }
  }, []);

  // 运行单场景评估
  const runSingleEvaluation = useCallback(async () => {
    if (!selectedPresets[0]) { message.warning('请先选择场景'); return; }

    // 创建新的 AbortController
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setEvaluating(true);
    setCurrentReport(null);
    setRadarData(null);
    setProgressInfo({ stage: 'starting', message: '正在初始化...', percent: 0 });

    try {
      setProgressInfo({ stage: 'generating', message: '正在生成测试场景...', percent: 3 });

      let genResult;
      if (useCustomConfig) {
        genResult = await generateCustomScenario(customConfig);
      } else {
        genResult = await generateScenario(selectedPresets[0], seed);
      }
      setCurrentScenario(genResult.scenario);

      setProgressInfo({
        stage: 'evaluating',
        message: `场景已生成(${genResult.metadata.agvs}辆AGV, ${genResult.metadata.tasks}个任务)，正在提交算法评估...`,
        percent: 8,
      });

      // 启动进度轮询（带取消信号）
      const evalResult = await evaluateSingle(genResult.scenario, selectedAlgos, controller.signal);

      // evaluateSingle 是异步后台执行，返回值只有 {success, task_id}
      // 需要通过轮询获取最终 report
      if (evalResult && evalResult.task_id) {
        let pollCount = 0;
        const maxPolls = 300; // 最多等 4 分钟 (800ms * 300, v1_hybrid+rl_dqn 在大场景可能各需30秒)
        let lastStage = '';

        while (pollCount < maxPolls) {
          if (controller.signal.aborted) {
            message.info('评测已停止');
            return;
          }

          const progress: any = await getEvaluationProgress(evalResult.task_id);

          // 进度接口返回的是 _progress_store[task_id] 整个字典
          // completed 时后端会把 result 写入 _progress_store[task_id]["result"] (顶层key)
          const currentStage = progress?.stage || 'unknown';

          // 构造友好消息：evaluating 时显示算法完成进度
          let displayMsg = progress?.message || '评测中...';
          if (currentStage === 'evaluating' && progress?.data?.completed != null) {
            displayMsg = `算法评估中 (${progress.data.completed}/${progress.data.total}) — ${progress.data.current || ''}`;
          }

          setProgressInfo({
            stage: currentStage,
            message: displayMsg,
            percent: progress?.percent ?? Math.min(10 + pollCount * 2, 95),
          });

          // 任务完成 → 从顶层 result 字段取完整报告
          if (currentStage === 'completed') {
            const fullResult = progress?.result;   // result 在 progress 对象顶层，不在 data 子对象
            if (fullResult?.report) {
              setProgressInfo({ stage: 'completed', message: '评估完成！', percent: 100 });
              setCurrentReport(fullResult.report);
              setRadarData(fullResult.radar_data);

              const winnerName = fullResult.report?.winner || '未知';
              message.success(`评估完成！最佳算法: ${winnerName}`);
              setActiveMainTab('evaluation');
              setTimeout(() => setProgressInfo(null), 2000);
              return; // 成功退出
            }
            // 有 completed 标志但 result 还没写入（竞态），多等一轮
            console.warn('[Benchmark] stage=completed but result not ready, retrying...');
          } else if (currentStage === 'error') {
            throw new Error(progress?.message || progress?.data?.error || '评估过程出错');
          }

          // 阶段变化日志
          if (currentStage !== lastStage && currentStage !== 'unknown') {
            console.log(`[Benchmark] progress: ${currentStage} - ${progress?.message}`);
            lastStage = currentStage;
          }

          pollCount++;
          await new Promise(r => setTimeout(r, 800)); // 800ms 轮询间隔，更快响应停止
        }
        throw new Error('评估超时（3分钟内未完成）');
      } else {
        // 降级：某些情况下后端可能同步返回了结果
        if (evalResult?.report) {
          setCurrentReport(evalResult.report);
          setRadarData(evalResult.radar_data);
          message.success(`评估完成！最佳算法: ${evalResult.report.winner || '未知'}`);
          setActiveMainTab('evaluation');
          setTimeout(() => setProgressInfo(null), 2000);
        } else {
          throw new Error('评估任务未正确启动，未收到 task_id');
        }
      }
    } catch (e: any) {
      if (e.name === 'AbortError' || controller.signal.aborted) {
        message.info('评测已停止');
        setProgressInfo(null);
      } else {
        message.error(`评估失败: ${e.response?.detail || e.message}`);
        setProgressInfo(null);
      }
    } finally {
      abortControllerRef.current = null;
      setEvaluating(false);
    }
  }, [selectedPresets, seed, selectedAlgos, useCustomConfig, customConfig, pollProgress]);

  // 运行批量评估
  const runBatchEvaluation = useCallback(async () => {
    if (!selectedPresets.length) { message.warning('请至少选择一个场景'); return; }

    setEvaluating(true);
    setBatchResult(null);

    try {
      const result = await evaluateBatch(selectedPresets, selectedAlgos, seed, variants);
      setBatchResult(result);

      if (result.status === 'completed') {
        message.success(`批量评估完成！共测试 ${result.num_scenarios} 个场景`);
        setActiveMainTab('evaluation');
      }
    } catch (e: any) {
      message.error(`批量评估失败: ${e.response?.detail || e.message}`);
    } finally {
      setEvaluating(false);
    }
  }, [selectedPresets, selectedAlgos, seed, variants]);

  // 运行可视化评估
  const runVisualization = useCallback(async () => {
    if (!selectedAlgos.length) { message.warning('请至少选择一个算法'); return; }

    // 创建新的 AbortController
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setVisualizing(true);
    setVizResult(null);
    setProgressInfo({ stage: 'starting', message: '正在初始化可视化评估...', percent: 0 });

    try {
      setProgressInfo({ stage: 'generating', message: '正在生成测试场景...', percent: 3 });

      let genResult;
      if (useCustomConfig) {
        genResult = await generateCustomScenario(customConfig);
      } else {
        genResult = await generateScenario(selectedPresets[0], seed);
      }
      setCurrentScenario(genResult.scenario);

      setProgressInfo({
        stage: 'evaluating',
        message: `场景已生成，正在运行算法评估与仿真(${150}步)...`,
        percent: 10,
      });

      const vizAlgos = selectedAlgos.slice(0, 1);
      const faultCfg = customConfig.fault_injection;

      const result = await evaluateWithVisualization(
        genResult.scenario,
        vizAlgos,
        150,
        faultCfg ?? undefined,
        controller.signal,
      );

      // 防御: result 可能为 null (HTTP 错误/网络异常)
      if (!result) {
        throw new Error('可视化评估返回空数据');
      }

      // 检查后端返回的错误结构
      if (result.success === false) {
        throw new Error(result.error || '可视化评估失败(后端错误)');
      }

      if (result.task_id) {
        await pollProgress(result.task_id);
      }

      setProgressInfo({ stage: 'completed', message: '可视化数据就绪！', percent: 100 });
      setVizResult(result);
      setActiveMainTab('visualize');

      const totalSteps = result.trajectory?.total_steps || 0;
      const completedTasks = result.trajectory?.summary?.completed_tasks || '?';
      const totalTasks = result.trajectory?.summary?.total_tasks || '?';

      message.success(
        `可视化就绪！算法: ${result.visualization_algo}, ` +
        `仿真${totalSteps}步, ` +
        `预计完成任务${completedTasks}/${totalTasks}`
      );
      setTimeout(() => setProgressInfo(null), 2000);
    } catch (e: any) {
      if (e.name === 'AbortError' || controller.signal.aborted) {
        message.info('可视化评估已停止');
        setProgressInfo(null);
      } else {
        message.error(`可视化评估失败: ${e.response?.detail || e.message}`);
        setProgressInfo(null);
      }
    } finally {
      abortControllerRef.current = null;
      setVisualizing(false);
    }
  }, [selectedAlgos, selectedPresets, seed, useCustomConfig, customConfig, pollProgress]);

  const handleRun = () => {
    if (activeMainTab === 'visualize') {
      runVisualization();
    } else if (evalMode === 'single') {
      runSingleEvaluation();
    } else {
      runBatchEvaluation();
    }
  };

  // 停止正在运行的评测
  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      message.info('正在停止评测...');
    }
  };

  // 更新自定义配置
  const updateConfig = (key: keyof CustomScenarioConfig, value: any) => {
    setCustomConfig(prev => ({ ...prev, [key]: value }));
  };

  const updateFaultConfig = (key: keyof FaultInjectionConfig, value: any) => {
    setCustomConfig(prev => ({
      ...prev,
      fault_injection: {
        ...(prev.fault_injection || {
          enabled: true,
          fault_type: 'agv_breakdown',
          faulty_agv_indices: [],
          faulty_segment_indices: [],
          blocked_node_ids: [],
          fault_time: 50,
          fault_duration: 30,
        }),
        [key]: value,
      },
    }));
  };

  // 重置自定义配置为默认值
  const resetCustomConfig = () => {
    setCustomConfig({ ...DEFAULT_CUSTOM_CONFIG });
    setUseCustomConfig(false);
    setFaultEnabled(false);
  };

  // UI辅助函数
  const getGradeTag = (grade: string) => (
    <Tag color={GRADE_COLORS[grade] || '#999'} style={{ fontWeight: 'bold', fontSize: 13 }}>
      {grade}
    </Tag>
  );

  const getCategoryTag = (cat: string) => {
    const info = CATEGORY_MAP[cat];
    return info ? <Tag color={info.color}>{info.label}</Tag> : <Tag>{cat}</Tag>;
  };

  // 排名表格列定义
  const rankingColumns = [
    { title: '#', dataIndex: 'rank', key: 'rank', width: 55,
      render: (r: number) => r === 1 ? <TrophyOutlined style={{ color: '#faad14', fontSize: 16 }} />
        : r === 2 ? <StarOutlined style={{ color: '#b0b0b0', fontSize: 14 }} />
        : r === 3 ? <StarOutlined style={{ color: '#cd7f32', fontSize: 14 }} />
        : <Text type="secondary">{r}</Text>,
    },
    { title: '算法名称', key: 'name',
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => (
        <Space>
          <Text strong style={{ color: record[1]?.rank === 1 ? '#faad14' : undefined }}>
            {record[1]?.display_name || record[0]}
          </Text>
          {getCategoryTag(algorithms.find(a => a.name === record[0])?.category || '')}
        </Space>
      ),
    },
    { title: '总分', dataIndex: ['1', 'total_score'], key: 'score', width: 80,
      sorter: (a: [string, AlgorithmScoreCard], b: [string, AlgorithmScoreCard]) =>
        b[1].total_score - a[1].total_score,
      render: (score: number) => <Text strong style={{ fontSize: 15 }}>{score.toFixed(1)}</Text>,
    },
    { title: '等级', key: 'grade', width: 60,
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => getGradeTag(record[1].grade),
    },
    { title: '效率', key: 'eff', width: 65,
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => {
        const v = record[1]?.dimensions?.efficiency?.score ?? 0;
        return <Progress percent={Math.round(v)} size="small" strokeColor="#52c41a" />;
      },
    },
    { title: '质量', key: 'qual', width: 65,
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => {
        const v = record[1]?.dimensions?.quality?.score ?? 0;
        return <Progress percent={Math.round(v)} size="small" strokeColor="#1890ff" />;
      },
    },
    { title: '资源', key: 'res', width: 65,
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => {
        const v = record[1]?.dimensions?.resource?.score ?? 0;
        return <Progress percent={Math.round(v)} size="small" strokeColor="#fa8c16" />;
      },
    },
    { title: '实时', key: 'rt', width: 65,
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => {
        const v = record[1]?.dimensions?.realtime?.score ?? 0;
        return <Progress percent={Math.round(v)} size="small" strokeColor="#722ed1" />;
      },
    },
    { title: '鲁棒', key: 'rob', width: 65,
      render: (_: unknown, record: [string, AlgorithmScoreCard]) => {
        const v = record[1]?.dimensions?.robustness?.score ?? 0;
        return <Progress percent={Math.round(v)} size="small" strokeColor="#eb2f96" />;
      },
    },
  ];

  // 场景统计卡片
  const renderScenarioInfo = () => {
    const meta = currentReport?.metadata || (currentScenario as any)?.metadata;
    if (!meta) return null;

    return (
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={4}>
          <Statistic title="节点数" value={meta.nodes ?? '-'} prefix={<DashboardOutlined />} />
        </Col>
        <Col span={4}>
          <Statistic title="边数" value={meta.edges ?? '-'} />
        </Col>
        <Col span={4}>
          <Statistic title="AGV数量" value={meta.agvs ?? '-'} prefix={<TeamOutlined />} />
        </Col>
        <Col span={4}>
          <Statistic title="任务数量" value={meta.tasks ?? '-'} prefix={<AimOutlined />} />
        </Col>
        <Col span={4}>
          <Statistic title="计算耗时" value={currentReport?.execution_time_ms?.toFixed(0) || '-'} suffix="ms"
            prefix={<ClockCircleOutlined />} />
        </Col>
        <Col span={4}>
          <Statistic title="场景类型" value={currentReport?.type || '-'} prefix={<SafetyCertificateOutlined />} />
        </Col>
      </Row>
    );
  };

  // ==========================================
  // 主渲染
  // ==========================================
  return (
    <div className="benchmark-page">
      {/* 演示模式提示 */}
      {initError && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message="演示模式"
          description={initError}
          closable
          onClose={() => setInitError(null)}
          style={{ marginBottom: 16 }}
        />
      )}
      
      {/* 页面头部 */}
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <Title level={4} style={{ margin: 0, color: '#e0e0e0' }}>
            <ExperimentOutlined style={{ marginRight: 8, color: '#1890ff' }} />
            算法评测与调度可视化
          </Title>
          <Text type="secondary">
            交互式样本配置 · 多维评分对比 · 实时调度仿真动画
            {initError && <Tag color="warning" style={{ marginLeft: 8 }}>DEMO</Tag>}
          </Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading} disabled={evaluating || visualizing}>刷新</Button>
          {(evaluating || visualizing) ? (
            <Button
              danger
              icon={<CloseCircleOutlined />}
              onClick={handleStop}
              style={{ height: 40, paddingLeft: 20, paddingRight: 20, fontSize: 15 }}
            >
              停止
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={handleRun}
              disabled={!selectedAlgos.length}
              style={{
                background: 'linear-gradient(135deg, #1890ff 0%, #096dd9 100%)',
                border: 0,
                boxShadow: '0 4px 12px rgba(24,144,255,0.35)',
                height: 40,
                paddingLeft: 20,
                paddingRight: 20,
                fontSize: 15,
              }}
            >
              {activeMainTab === 'visualize' ? '▶ 运行可视化评测'
                : `运行${evalMode === 'single' ? '单场景' : '批量'}评测`}
            </Button>
          )}
        </Space>
      </div>

      {/* ===================== 评测进度提示 ===================== */}
      {(evaluating || visualizing) && progressInfo && (
        <Card
          size="small"
          style={{
            marginBottom: 12,
            background: 'linear-gradient(135deg, #111827 0%, #1a1f3a 100%)',
            borderColor: progressInfo.stage === 'completed' ? '#52c41a'
              : progressInfo.stage === 'error' ? '#ff4d4f'
              : '#1890ff',
            boxShadow: '0 2px 8px rgba(24,144,255,0.15)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ flexShrink: 0 }}>
              {progressInfo.stage === 'completed' ? (
                <CheckCircleOutlined style={{ fontSize: 24, color: '#52c41a' }} />
              ) : progressInfo.stage === 'error' ? (
                <CloseCircleOutlined style={{ fontSize: 24, color: '#ff4d4f' }} />
              ) : (
                <LoadingOutlined style={{ fontSize: 24, color: '#1890ff' }} />
              )}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text strong style={{ color: '#e2e8f0', fontSize: 13 }}>
                  {progressInfo.message}
                </Text>
                <Text style={{ color: '#1890ff', fontSize: 13, fontWeight: 600 }}>
                  {progressInfo.percent}%
                </Text>
              </div>
              <Progress
                percent={progressInfo.percent}
                size="small"
                status={
                  progressInfo.stage === 'completed' ? 'success'
                  : progressInfo.stage === 'error' ? 'exception'
                  : 'active'
                }
                strokeColor={{
                  '0%': '#1890ff',
                  '100%': progressInfo.stage === 'completed' ? '#52c41a' : '#36cfc9',
                }}
                trailColor="#1e293b"
              />
            </div>
          </div>
        </Card>
      )}

      {/* ===================== 主标签页 ===================== */}
      <Tabs activeKey={activeMainTab} onChange={setActiveMainTab} type="card" size="small">
        
        {/* ===== Tab 1: 场景配置 ===== */}
        <TabPane tab={<span><SettingOutlined />场景配置</span>} key="config">

          {/* ===== 评测类别选择器 (两大类) ===== */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={24} lg={12}>
              <Card
                hoverable
                onClick={() => setEvalCategory('agv_only')}
                style={{
                  borderColor: evalCategory === 'agv_only' ? EVAL_CATEGORY_CONFIG.agv_only.color : undefined,
                  background: evalCategory === 'agv_only' ? 'rgba(24,144,255,0.04)' : undefined,
                  borderWidth: evalCategory === 'agv_only' ? 2 : 1,
                  cursor: 'pointer',
                }}
              >
                <Space>
                  <CarOutlined style={{ fontSize: 28, color: '#1890ff' }} />
                  <div>
                    <Text strong style={{ color: evalCategory === 'agv_only' ? '#1890ff' : undefined }}>
                      类别A: 纯AGV算法评测
                    </Text>
                    <div style={{ fontSize: 12, color: '#a0a8c0', marginTop: 2 }}>
                      {EVAL_CATEGORY_CONFIG.agv_only.description}
                    </div>
                    <Space size="small" style={{ marginTop: 6 }}>
                      <Tag color="blue">仓库</Tag>
                      <Tag color="blue">医院配送</Tag>
                      <Tag>无输送线</Tag>
                    </Space>
                  </div>
                  {evalCategory === 'agv_only' && (
                    <CheckCircleOutlined style={{ fontSize: 20, color: '#52c41a', marginLeft: 'auto' }} />
                  )}
                </Space>
              </Card>
            </Col>

            <Col xs={24} lg={12}>
              <Card
                hoverable
                onClick={() => setEvalCategory('agv_tms_mixed')}
                style={{
                  borderColor: evalCategory === 'agv_tms_mixed' ? EVAL_CATEGORY_CONFIG.agv_tms_mixed.color : undefined,
                  background: evalCategory === 'agv_tms_mixed' ? 'rgba(235,47,150,0.04)' : undefined,
                  borderWidth: evalCategory === 'agv_tms_mixed' ? 2 : 1,
                  cursor: 'pointer',
                }}
              >
                <Space>
                  <MergeCellsOutlined style={{ fontSize: 28, color: '#eb2f96' }} />
                  <div>
                    <Text strong style={{ color: evalCategory === 'agv_tms_mixed' ? '#eb2f96' : undefined }}>
                      类别B: AGV+TMS混合模式评测
                    </Text>
                    <div style={{ fontSize: 12, color: '#a0a8c0', marginTop: 2 }}>
                      {EVAL_CATEGORY_CONFIG.agv_tms_mixed.description}
                    </div>
                    <Space size="small" style={{ marginTop: 6 }}>
                      <Tag color="magenta">工厂</Tag>
                      <Tag color="magenta">港口</Tag>
                      <Tag color="purple">混合长程</Tag>
                      <Tag>含输送线</Tag>
                    </Space>
                  </div>
                  {evalCategory === 'agv_tms_mixed' && (
                    <CheckCircleOutlined style={{ fontSize: 20, color: '#52c41a', marginLeft: 'auto' }} />
                  )}
                </Space>
              </Card>
            </Col>
          </Row>

          {/* 当前类别说明 + 任务类型分布提示 */}
          <Alert
            type={evalCategory === 'agv_only' ? 'info' : 'success'}
            showIcon
            icon={<InfoCircleOutlined />}
            style={{ marginBottom: 16 }}
            message={
              <span>
                <Text strong>{EVAL_CATEGORY_CONFIG[evalCategory].label}</Text>
                {' — '}
                {evalCategory === 'agv_only'
                  ? '仅评估AGV路径规划与任务分配算法（ACO/SA/GA/Greedy等），不含输送线协同'
                  : '评估AGV+输送线混合调度算法，支持三种任务类型: '
                }
                {evalCategory === 'agv_tms_mixed' && (
                  <>
                    <Tag color="blue">纯AGV(37.5%)</Tag>
                    <Tag color="purple">纯输送线(37.5%)</Tag>
                    <Tag color="magenta">混合长程(25%)</Tag>
                  </>
                )}
              </span>
            }
          />

          <Row gutter={20}>
            {/* 左侧: 配置面板 */}
            <Col span={16}>
              <Card
                title={<Space><ToolOutlined /><span>测试样本参数配置</span></Space>}
                style={{ background: '#111827', borderColor: '#1e293b' }}
                bodyStyle={{ background: '#111827' }}
                extra={
                  <Space>
                    <Switch
                      checked={useCustomConfig}
                      onChange={setUseCustomConfig}
                      checkedChildren="自定义模式"
                      unCheckedChildren="预设模式"
                    />
                    <Button size="small" icon={<ReloadOutlined />} onClick={resetCustomConfig}>重置</Button>
                  </Space>
                }
              >
                {useCustomConfig ? (
                  /* ====== 自定义配置模式 ====== */
                  <Collapse defaultActiveKey={['basic']} ghost>
                    {/* 基础设置 */}
                    <Panel header={<Space><DashboardOutlined /><strong>基础设置</strong></Space>} key="basic">
                      <Row gutter={[20, 12]}>
                        <Col span={8}>
                          <Text type="secondary" style={{ fontSize: 12 }}>场景类型</Text>
                          <Select
                            value={customConfig.scenario_type}
                            onChange={v => updateConfig('scenario_type', v)}
                            style={{ width: '100%', marginTop: 4 }}
                          >
                            <Select.Option value="warehouse">仓库 (Warehouse)</Select.Option>
                            <Select.Option value="factory">工厂 (Factory)</Select.Option>
                            <Select.Option value="port">港口 (Port)</Select.Option>
                            <Select.Option value="hospital">医院 (Hospital)</Select.Option>
                            <Select.Option value="mixed">AGV+TMS混合 (Mixed)</Select.Option>
                          </Select>
                        </Col>
                        <Col span={4}>
                          <Text type="secondary" style={{ fontSize: 12 }}>难度</Text>
                          <Select value={customConfig.difficulty}
                            onChange={v => updateConfig('difficulty', v)}
                            style={{ width: '100%', marginTop: 4 }}>
                            <Select.Option value="easy">简单</Select.Option>
                            <Select.Option value="medium">中等</Select.Option>
                            <Select.Option value="hard">困难</Select.Option>
                            <Select.Option value="extreme">极限</Select.Option>
                          </Select>
                        </Col>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>网格大小 (行×列)</Text>
                          <Input.Group compact style={{ marginTop: 4 }}>
                            <InputNumber value={customConfig.grid_rows} min={5} max={50}
                              onChange={v => updateConfig('grid_rows', v ?? 15)} style={{ width: '48%' }} />
                            <span style={{ lineHeight: '32px', color: '#666', padding: '0 4px' }}>×</span>
                            <InputNumber value={customConfig.grid_cols} min={5} max={60}
                              onChange={v => updateConfig('grid_cols', v ?? 20)} style={{ width: '48%' }} />
                          </Input.Group>
                        </Col>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>随机种子</Text>
                          <InputNumber value={customConfig.seed} min={1} max={99999}
                            onChange={v => updateConfig('seed', v ?? 42)}
                            style={{ width: '100%', marginTop: 4 }} />
                        </Col>
                      </Row>
                    </Panel>

                    {/* AGV配置 */}
                    <Panel header={<Space><CarOutlined /><strong>AGV 配置</strong></Space>} key="agv">
                      <Row gutter={[20, 12]}>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>AGV 数量</Text>
                          <InputNumber value={customConfig.num_agvs} min={1} max={100}
                            onChange={v => updateConfig('num_agvs', v ?? 15)}
                            style={{ width: '100%', marginTop: 4 }} />
                        </Col>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>速度范围 (m/s)</Text>
                          <Input.Group compact style={{ marginTop: 4 }}>
                            <InputNumber value={customConfig.agv_speed_min} min={0.5} max={5} step={0.5}
                              onChange={v => updateConfig('agv_speed_min', v ?? 1.0)} style={{ width: '48%' }} />
                            <span style={{ lineHeight: '32px', color: '#666', padding: '0 4px' }}>~</span>
                            <InputNumber value={customConfig.agv_speed_max} min={0.5} max={5} step={0.5}
                              onChange={v => updateConfig('agv_speed_max', v ?? 3.0)} style={{ width: '48%' }} />
                          </Input.Group>
                        </Col>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>电池电量范围 (%)</Text>
                          <Input.Group compact style={{ marginTop: 4 }}>
                            <InputNumber value={customConfig.battery_min} min={5} max={100}
                              onChange={v => updateConfig('battery_min', v ?? 30)} style={{ width: '48%' }} />
                            <span style={{ lineHeight: '32px', color: '#666', padding: '0 4px' }}>~</span>
                            <InputNumber value={customConfig.battery_max} min={5} max={100}
                              onChange={v => updateConfig('battery_max', v ?? 100)} style={{ width: '48%' }} />
                          </Input.Group>
                        </Col>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>载重能力</Text>
                          <Slider value={customConfig.agv_capacity * 2} min={1} max={4} step={1}
                            marks={{ 1: '0.5', 2: '1.0', 3: '1.5', 4: '2.0' }}
                            onChange={v => updateConfig('agv_capacity', v / 2)}
                            style={{ marginTop: 4 }} />
                        </Col>
                      </Row>
                    </Panel>

                    {/* 任务配置 */}
                    <Panel header={<Space><AimOutlined /><strong>任务 设置</strong></Space>} key="tasks">
                      <Row gutter={[20, 12]}>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>任务数量</Text>
                          <InputNumber value={customConfig.num_tasks} min={1} max={300}
                            onChange={v => updateConfig('num_tasks', v ?? 40)}
                            style={{ width: '100%', marginTop: 4 }} />
                        </Col>
                        <Col span={6}>
                          <Text type="secondary" style={{ fontSize: 12 }}>高优先级比例</Text>
                          <Slider value={customConfig.high_priority_ratio * 100} min={0} max={100}
                            marks={{ 0: '0%', 20: '20%', 50: '50%', 100: '100%' }}
                            onChange={v => updateConfig('high_priority_ratio', v / 100)}
                            style={{ marginTop: 4 }} />
                        </Col>
                        <Col span={12}>
                          <Text type="secondary" style={{ fontSize: 12 }}>预计时长范围 (秒)</Text>
                          <Input.Group compact style={{ marginTop: 4 }}>
                            <InputNumber value={customConfig.task_duration_min} min={1} max={600}
                              onChange={v => updateConfig('task_duration_min', v ?? 30)}
                              placeholder="最短" style={{ width: '45%' }} addonBefore="最短" />
                            <span style={{ lineHeight: '32px', color: '#666', padding: '0 6px' }}>~</span>
                            <InputNumber value={customConfig.task_duration_max} min={1} max={600}
                              onChange={v => updateConfig('task_duration_max', v ?? 180)}
                              placeholder="最长" style={{ width: '45%' }} addonBefore="最长" />
                          </Input.Group>
                        </Col>
                      </Row>
                    </Panel>

                    {/* TMS输送线 */}
                    <Panel header={<Space><InboxOutlined /><strong>TMS 输送线设置</strong></Space>} key="tms">
                      <Row gutter={[20, 12]} align="middle">
                        <Col span={4}>
                          <Switch checked={customConfig.has_conveyor}
                            onChange={c => updateConfig('has_conveyor', c)}
                            checkedChildren="启用" unCheckedChildren="关闭" />
                        </Col>
                        {customConfig.has_conveyor && (
                          <>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>输送任务数</Text>
                              <InputNumber value={customConfig.num_conveyor_tasks} min={0} max={50}
                                onChange={v => updateConfig('num_conveyor_tasks', v ?? 10)}
                                style={{ width: '100%', marginTop: 4 }} />
                            </Col>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>线路段数</Text>
                              <InputNumber value={customConfig.num_conveyor_segments} min={0} max={20}
                                onChange={v => updateConfig('num_conveyor_segments', v ?? 4)}
                                style={{ width: '100%', marginTop: 4 }} />
                            </Col>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>输送速度 (m/s)</Text>
                              <InputNumber value={customConfig.conveyor_speed} min={0.1} max={3} step={0.1}
                                onChange={v => updateConfig('conveyor_speed', v ?? 0.5)}
                                style={{ width: '100%', marginTop: 4 }} />
                            </Col>
                          </>
                        )}
                      </Row>

                      {/* 三种任务类型分布滑块（仅启用输送线时显示） */}
                      {customConfig.has_conveyor && (
                        <>
                          <Divider style={{ margin: '12px 0' }} />
                          <Text type="secondary" strong style={{ fontSize: 12 }}>
                            三种任务类型分布
                            <Tooltip title="调整三种任务类型的占比，三者之和应约为1.0">
                              <InfoCircleOutlined style={{ marginLeft: 6, cursor: 'help' }} />
                            </Tooltip>
                          </Text>
                          <Row gutter={[16, 8]} align="middle" style={{ marginTop: 8 }}>
                            <Col span={7}>
                              <Tag color="blue"><TruckOutlined /> 纯AGV</Tag>
                              <Slider
                                min={0} max={100} step={5}
                                value={(customConfig.agv_only_ratio ?? 0.375) * 100}
                                onChange={v => updateConfig('agv_only_ratio', v / 100)}
                                tooltipFormatter={(v) => `${v}%`}
                              />
                              <Text type="secondary" style={{ fontSize: 11 }}>{((customConfig.agv_only_ratio ?? 0.375) * 100).toFixed(0)}%</Text>
                            </Col>
                            <Col span={7}>
                              <Tag color="purple"><SwapOutlined /> 纯输送线</Tag>
                              <Slider
                                min={0} max={100} step={5}
                                value={(customConfig.conveyor_only_ratio ?? 0.375) * 100}
                                onChange={v => updateConfig('conveyor_only_ratio', v / 100)}
                                tooltipFormatter={(v) => `${v}%`}
                              />
                              <Text type="secondary" style={{ fontSize: 11 }}>{((customConfig.conveyor_only_ratio ?? 0.375) * 100).toFixed(0)}%</Text>
                            </Col>
                            <Col span={7}>
                              <Tag color="magenta"><MergeCellsOutlined /> 混合长程</Tag>
                              <Slider
                                min={0} max={100} step={5}
                                value={(customConfig.mixed_ratio ?? 0.25) * 100}
                                onChange={v => updateConfig('mixed_ratio', v / 100)}
                                tooltipFormatter={(v) => `${v}%`}
                              />
                              <Text type="secondary" style={{ fontSize: 11 }}>{((customConfig.mixed_ratio ?? 0.25) * 100).toFixed(0)}%</Text>
                            </Col>
                            <Col span={3} style={{ textAlign: 'center' }}>
                              <Text type="secondary" style={{ fontSize: 10 }}>合计</Text>
                              <Text strong style={{
                                fontSize: 14,
                                color: Math.abs(((customConfig.agv_only_ratio ?? 0.375) + (customConfig.conveyor_only_ratio ?? 0.375) + (customConfig.mixed_ratio ?? 0.25)) - 1.0) < 0.05 ? '#52c41a' : '#faad14',
                              }}>
                                {((customConfig.agv_only_ratio ?? 0.375) + (customConfig.conveyor_only_ratio ?? 0.375) + (customConfig.mixed_ratio ?? 0.25) * 100).toFixed(0)}%
                              </Text>
                            </Col>
                          </Row>
                          <div style={{ background: 'rgba(235,47,150,0.03)', padding: '6px 10px', borderRadius: 4, marginTop: 4 }}>
                            <Text type="secondary" style={{ fontSize: 11 }}>
                              <TruckOutlined style={{ color: '#1890ff' }} /> 纯AGV: AGV直接搬运 →{' '}
                              <SwapOutlined style={{ color: '#722ed1' }} /> 纯输送线: 物料自动转运消除 →{' '}
                              <MergeCellsOutlined style={{ color: '#eb2f96' }} /> 混合长程: AGV→输送线→AGV五阶段协同
                            </Text>
                          </div>
                        </>
                      )}
                    </Panel>

                    {/* 故障模拟 */}
                    <Panel
                      header={
                        <Space>
                          <BugOutlined />
                          <strong>故障模拟</strong>
                          {faultEnabled && <Badge status="error" />}
                        </Space>
                      }
                      key="fault"
                    >
                      <Row gutter={[8, 12]}>
                        <Col span={4}>
                          <Switch checked={faultEnabled} onChange={setFaultEnabled}
                            checkedChildren="启用故障" unCheckedChildren="禁用" />
                        </Col>

                        {faultEnabled && (
                          <>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>故障类型</Text>
                              <Select
                                value={customConfig.fault_injection?.fault_type || 'agv_breakdown'}
                                onChange={v => updateFaultConfig('fault_type', v)}
                                style={{ width: '100%', marginTop: 4 }}
                              >
                                <Select.Option value="agv_breakdown">🔴 AGV故障</Select.Option>
                                <Select.Option value="conveyor_jam">🟠 输送线堵塞</Select.Option>
                                <Select.Option value="node_blocked">🟡 节点阻塞</Select.Option>
                                <Select.Option value="batch_fault">⚫ 批量故障</Select.Option>
                              </Select>
                            </Col>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {(customConfig.fault_injection?.fault_type || '').includes('conveyor')
                                  ? '故障线路段索引' : '故障AGV索引'}
                              </Text>
                              <Select
                                mode="tags"
                                value={customConfig.fault_injection?.faulty_agv_indices?.map(String) ||
                                  customConfig.fault_injection?.faulty_segment_indices?.map(String) || []}
                                onChange={vals => {
                                  const intVals = vals.map(v => parseInt(v) || 0).filter(v => !isNaN(v));
                                  if ((customConfig.fault_injection?.fault_type || '').includes('conveyor'))
                                    updateFaultConfig('faulty_segment_indices', intVals);
                                  else
                                    updateFaultConfig('faulty_agv_indices', intVals);
                                }}
                                placeholder="输入索引，回车添加"
                                style={{ width: '100%', marginTop: 4 }}
                                tokenSeparators={[',']}
                              />
                            </Col>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>发生时刻 (步)</Text>
                              <InputNumber value={customConfig.fault_injection?.fault_time || 50}
                                min={0} max={500}
                                onChange={v => updateFaultConfig('fault_time', v ?? 50)}
                                style={{ width: '100%', marginTop: 4 }} />
                            </Col>
                            <Col span={5}>
                              <Text type="secondary" style={{ fontSize: 12 }}>持续时间 (步)</Text>
                              <InputNumber value={customConfig.fault_injection?.fault_duration || 30}
                                min={5} max={300}
                                onChange={v => updateFaultConfig('fault_duration', v ?? 30)}
                                style={{ width: '100%', marginTop: 4 }} />
                            </Col>
                          </>
                        )}
                      </Row>

                      {faultEnabled && (
                        <Alert
                          type="warning"
                          showIcon
                          icon={<WarningOutlined />}
                          style={{ marginTop: 12, background: 'rgba(250,173,20,0.06)', borderColor: 'rgba(250,173,20,0.2)' }}
                          message={
                            <span style={{ fontSize: 12 }}>
                              已配置故障模拟：
                              类型=<strong>{customConfig.fault_injection?.fault_type}</strong>，
                              发生时刻=<strong>{customConfig.fault_injection?.fault_time}步</strong>，
                              持续<strong>{customConfig.fault_injection?.fault_duration}步</strong>
                              {(customConfig.fault_injection?.faulty_agv_indices?.length ?? 0) > 0 &&
                                <>，影响AGV: [{customConfig.fault_injection?.faulty_agv_indices?.join(',')}]</>
                              }
                              {(customConfig.fault_injection?.faulty_segment_indices?.length ?? 0) > 0 &&
                                <>，线路段: [{customConfig.fault_injection?.faulty_segment_indices?.join(',')}]</>
                              }
                            </span>
                          }
                        />
                      )}
                    </Panel>
                  </Collapse>
                ) : (
                  /* ====== 预设模式 ====== */
                  <div>
                    <Row gutter={[20, 12]} align="middle">
                      <Col span={8}>
                        <Text type="secondary" style={{ display: 'block', marginBottom: 6 }}>评测模式</Text>
                        <Radio.Group value={evalMode} onChange={e => setEvalMode(e.target.value)} buttonStyle="solid">
                          <Radio.Button value="single">单场景对比</Radio.Button>
                          <Radio.Button value="batch">批量多场景</Radio.Button>
                        </Radio.Group>
                      </Col>
                      <Col span={evalMode === 'batch' ? 10 : 10}>
                        <Text type="secondary" style={{ display: 'block', marginBottom: 6 }}>
                          测试场景 {evalMode === 'batch' ? '(可多选)' : ''}
                          <Tag color={EVAL_CATEGORY_CONFIG[evalCategory].color}
                            style={{ marginLeft: 8, fontSize: 10 }}
                          >
                            {EVAL_CATEGORY_CONFIG[evalCategory].label}
                          </Tag>
                        </Text>
                        {/* 按当前类别筛选预设 */}
                        <Select mode={evalMode === 'batch' ? 'multiple' : undefined}
                          value={selectedPresets} onChange={setSelectedPresets}
                          placeholder={`选择${EVAL_CATEGORY_CONFIG[evalCategory].label}场景`}
                          style={{ width: '100%' }} maxTagCount={3}>
                          {presets
                            .filter(p => {
                              // 按类别筛选：纯AGV模式只显示无输送线场景，混合模式只显示有输送线或混合场景
                              if (evalCategory === 'agv_only') return !p.has_conveyor;
                              return true; // agv_tms_mixed 显示所有（特别是含输送线的）
                            })
                            .map(p => (
                            <Select.Option key={p.name} value={p.name}>
                              <Space>
                                {p.has_conveyor && <Badge status="processing" />}
                                <span>{p.name}</span>
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  ({p.agvs}AGV/{p.tasks}任务)
                                </Text>
                                <Tag style={{ fontSize: 10, marginLeft: 4 }} color={
                                  p.difficulty === 'easy' ? 'green'
                                  : p.difficulty === 'medium' ? 'blue'
                                  : p.difficulty === 'hard' ? 'orange'
                                  : 'red'
                                }>
                                  {p.difficulty}
                                </Tag>
                              </Space>
                            </Select.Option>
                          ))}
                        </Select>
                      </Col>
                      <Col span={6}>
                        <Text type="secondary" style={{ display: 'block', marginBottom: 6 }}>随机种子</Text>
                        <InputNumber value={seed} onChange={v => setSeed(v ?? 42)} min={1} max={99999}
                          style={{ width: '100%' }} />
                      </Col>
                    </Row>

                    {evalMode === 'batch' && (
                      <Row style={{ marginTop: 12 }}>
                        <Col span={6}>
                          <Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>每种变体数</Text>
                          <Slider min={1} max={5} value={variants} onChange={setVariants}
                            marks={{ 1: '1', 3: '3', 5: '5' }} />
                        </Col>
                      </Row>
                    )}

                    <Divider style={{ borderColor: '#1e293b' }} />

                    {/* 快捷预设卡片 - 按类别显示 */}
                    <Text type="secondary" strong>
                      快捷预设模板
                      <Tag color={EVAL_CATEGORY_CONFIG[evalCategory].color} style={{ marginLeft: 8 }}>
                        {EVAL_CATEGORY_CONFIG[evalCategory].label}
                      </Tag>
                    </Text>
                    <Row gutter={[12, 12]} style={{ marginTop: 10 }}>
                      {(evalCategory === 'agv_only' ? [
                        { name: '小型仓库', preset: 'small_warehouse', desc: '5AGV·15任务·简单', color: 'green' },
                        { name: '中型仓库', preset: 'medium_warehouse', desc: '15AGV·40任务·中等', color: 'blue' },
                        { name: '医院配送', preset: 'hospital_delivery', desc: '8AGV·20任务·简单', color: 'geekblue' },
                      ] : [
                        { name: '大型仓库(含输送线)', preset: 'large_warehouse', desc: '30AGV·80任务·含TMS', color: 'orange' },
                        { name: '工厂车间', preset: 'factory_floor', desc: '20AGV·50任务·含输送线', color: 'cyan' },
                        { name: '港口码头', preset: 'port_logistics', desc: '40AGV·100任务·极端', color: 'purple' },
                        { name: 'AGV+TMS混合', preset: 'mixed_scenario', desc: '18AGV·45+15TMS任务', color: 'magenta' },
                        { name: '高压测试', preset: 'stress_test', desc: '25AGV·120任务·极端', color: 'red' },
                      ]).map(item => (
                        <Col span={6} key={item.preset}>
                          <div style={{
                            padding: '10px 12px',
                            background: selectedPresets.includes(item.preset)
                              ? 'rgba(24,144,255,0.1)' : 'rgba(255,255,255,0.02)',
                            borderRadius: 6,
                            border: selectedPresets.includes(item.preset)
                              ? '1px solid rgba(24,144,255,0.4)'
                              : '1px solid rgba(255,255,255,0.06)',
                            cursor: 'pointer',
                            transition: 'all 0.2s',
                          }}
                          onClick={() => {
                            if (evalMode === 'single') {
                              setSelectedPresets([item.preset]);
                            } else {
                              setSelectedPresets(prev =>
                                prev.includes(item.preset)
                                  ? prev.filter(p => p !== item.preset)
                                  : [...prev, item.preset]
                              );
                            }
                          }}
                          onMouseEnter={e => (e.currentTarget.style.borderColor = 'rgba(24,144,255,0.3)')}
                          onMouseLeave={e => {
                            e.currentTarget.style.borderColor = selectedPresets.includes(item.preset)
                              ? 'rgba(24,144,255,0.4)'
                              : 'rgba(255,255,255,0.06)';
                          }}
                          >
                            <Text strong style={{ fontSize: 12, color: selectedPresets.includes(item.preset) ? '#1890ff' : '#ccc' }}>
                              {item.name}
                            </Text>
                            <div style={{ marginTop: 4 }}>
                              <Text type="secondary" style={{ fontSize: 10 }}>{item.desc}</Text>
                              <Tag color={item.color} style={{ float: 'right', fontSize: 9, marginTop: 2 }}>选择</Tag>
                            </div>
                          </div>
                        </Col>
                      ))}
                    </Row>
                  </div>
                )}

                <Divider style={{ borderColor: '#1e293b', margin: '16px 0' }} />

                {/* 算法选择 */}
                <Row gutter={[20, 12]} align="middle">
                  <Col span={useCustomConfig ? 12 : 8}>
                    <Text type="secondary" style={{ display: 'block', marginBottom: 6 }}>
                      参评算法{activeMainTab === 'visualize' ? '(可视化仅使用第一个)' : ''}
                    </Text>
                    <Select mode="multiple" value={selectedAlgos} onChange={setSelectedAlgos}
                      placeholder="选择算法" style={{ width: '100%' }} maxTagCount={3}>
                      {algorithms.map(a => (
                        <Select.Option key={a.name} value={a.name}>
                          <Space size={4}>
                            {getCategoryTag(a.category)}
                            <span>{a.display_name}</span>
                            {!a.is_available && <Tooltip title={`缺少依赖: ${a.required_deps.join(', ')}`}><Tag color="warning" style={{ fontSize: 9, marginLeft: 4 }}>依赖缺失</Tag></Tooltip>}
                            {a.is_available && <Tag color="success" style={{ fontSize: 9, marginLeft: 4 }}>可用</Tag>}
                          </Space>
                        </Select.Option>
                      ))}
                    </Select>
                  </Col>

                  <Col span={useCustomConfig ? 12 : 16}>
                    <Alert
                      type="info"
                      showIcon
                      icon={<InfoCircleOutlined />}
                      message={
                        <span>
                          已选 <strong>{selectedAlgos.length}</strong> 个算法，
                          {useCustomConfig ? (
                            <>自定义场景: <strong>{customConfig.scenario_type}</strong> ({customConfig.grid_rows}×{customConfig.grid_cols}),
                              <strong>{customConfig.num_agvs}</strong>AGV, <strong>{customConfig.num_tasks}</strong>任务</>
                          ) : evalMode === 'batch' ? (
                            <><strong>{selectedPresets.length}</strong> 种场景，约 <strong>{selectedPresets.length * variants}</strong> 次</>
                          ) : (
                            <>当前: <strong>{selectedPresets[0] || '-'}</strong></>
                          )}
                          {faultEnabled && <Tag color="error" style={{ marginLeft: 8 }}>含故障注入</Tag>}
                          {customConfig.has_conveyor && <Badge status="processing" style={{ marginLeft: 8 }} />}
                        </span>
                      }
                      style={{ background: 'rgba(24,144,255,0.05)', borderColor: 'rgba(24,144,255,0.2)' }}
                    />
                  </Col>
                </Row>
              </Card>
            </Col>

            {/* 右侧: 场景预览 */}
            <Col span={8}>
              <Card
                title={<Space><EyeOutlined /><span>场景预览</span></Space>}
                style={{ background: '#111827', borderColor: '#1e293b', height: '100%' }}
                bodyStyle={{ background: '#111827' }}
              >
                {currentScenario ? (
                  <div>
                    <Row gutter={[12, 8]}>
                      <Col span={12}>
                        <Statistic title="节点" value={currentScenario.metadata.num_nodes} prefix={<DashboardOutlined />} />
                      </Col>
                      <Col span={12}>
                        <Statistic title="边" value={currentScenario.metadata.num_edges} />
                      </Col>
                      <Col span={8}>
                        <Statistic title="AGV" value={currentScenario.metadata.num_agvs} prefix={<CarOutlined />} />
                      </Col>
                      <Col span={8}>
                        <Statistic title="任务" value={currentScenario.metadata.num_tasks} prefix={<AimOutlined />} />
                      </Col>
                      <Col span={8}>
                        {currentScenario.metadata.has_conveyor && (
                          <Statistic title="TMS任务" value={currentScenario.metadata.conveyor_tasks || 0}
                            prefix={<InboxOutlined />} valueStyle={{ color: '#ed8936' }} />
                        )}
                      </Col>
                    </Row>

                    <Divider style={{ borderColor: '#1e293b', margin: '12px 0' }} />

                    <Text strong style={{ fontSize: 12, color: '#aaa' }}>AGV列表 (前5个)</Text>
                    <div style={{ maxHeight: 120, overflow: 'auto', marginTop: 6 }}>
                      {(currentScenario.agvs || []).slice(0, 5).map(agv => (
                        <div key={agv.id} style={{
                          padding: '4px 8px', marginBottom: 3, borderRadius: 4,
                          background: agv.status === 'fault' ? 'rgba(255,77,79,0.08)' : 'rgba(255,255,255,0.02)',
                          border: `1px solid ${agv.status === 'fault' ? 'rgba(255,77,79,0.2)' : 'transparent'}`,
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        }}>
                          <Space>
                            <Tag style={{ fontSize: 10, margin: 0 }} color={agv.status === 'fault' ? 'red' : 'blue'}>
                              {agv.id.replace('AGV-', '')}
                            </Tag>
                            {agv.speed && <Text code style={{ fontSize: 9 }}>{agv.speed}m/s</Text>}
                          </Space>
                          <Space size={4}>
                            <Progress percent={agv.battery_level || agv.battery || 80}
                              size="small" strokeWidth={4}
                              style={{ width: 50 }}
                              strokeColor={(agv.battery_level ?? 80) < 30 ? '#ff4d4f' : undefined} />
                            {agv.status === 'fault' ? <Tag color="red" style={{ fontSize: 9 }}>故障</Tag>
                              : <Text type="secondary" style={{ fontSize: 9 }}>{agv.status}</Text>}
                          </Space>
                        </div>
                      ))}
                    </div>

                    <Divider style={{ borderColor: '#1e293b', margin: '12px 0' }} />

                    <Text strong style={{ fontSize: 12, color: '#aaa' }}>任务列表 (前5个)</Text>
                    <div style={{ maxHeight: 100, overflow: 'auto', marginTop: 6 }}>
                      {(currentScenario.tasks || []).slice(0, 5).map(task => (
                        <div key={task.id} style={{
                          padding: '3px 8px', marginBottom: 2, borderRadius: 3,
                          display: 'flex', justifyContent: 'space-between', fontSize: 11,
                        }}>
                          <Text type="secondary">{task.id.replace('TASK-', '')}</Text>
                          <Tag color={task.priority >= 7 ? 'orange' : 'default'} style={{ fontSize: 9, margin: 0 }}>
                            P{task.priority}
                          </Tag>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={<Text type="secondary">点击"运行评测"后此处显示生成的场景数据</Text>}
                  />
                )}
              </Card>
            </Col>
          </Row>
        </TabPane>

        {/* ===== Tab 2 & 3: 算法评测结果 (保留原有功能) ===== */}
        <TabPane tab={<span><BarChartOutlined />算法评测</span>} key="evaluation">
          {(currentReport || batchResult) ? (
            <Tabs defaultActiveKey="overview" type="card" size="small">
              <TabPane tab={<span><BarChartOutlined />总览</span>} key="overview">
                {currentReport && currentReport.winner && (
                  <>
                    <Alert type="success" showIcon icon={<TrophyOutlined />}
                      message={<span style={{ fontSize: 15 }}>
                        最佳算法：<strong>{currentReport.winner}</strong>
                        （{currentReport.results?.[currentReport.winner]?.total_score?.toFixed(1) || '-'}分）
                      </span>}
                      style={{ marginBottom: 16, background: 'rgba(82,196,26,0.08)', borderColor: 'rgba(82,196,26,0.25)' }} />
                    
                    {renderScenarioInfo()}

                    <Row gutter={20}>
                      <Col span={12}>
                        <Card title={<span><RadarChartOutlined />五维能力雷达</span>} size="small"
                          style={{ background: '#111827', borderColor: '#1e293b', minHeight: 420 }}
                          bodyStyle={{ background: '#111827' }}>
                          {radarData ? <RadarChart data={radarData} width={430} height={360} />
                            : <Empty description="暂无雷达图数据" />}
                        </Card>
                      </Col>
                      <Col span={12}>
                        <Card title={<span><TrophyOutlined />排名</span>} size="small"
                          style={{ background: '#111827', borderColor: '#1e293b', minHeight: 420 }}
                          bodyStyle={{ background: '#111827', overflow: 'auto', maxHeight: 380 }}>
                          <Table dataSource={(currentReport.rankings || []).map(([name]) => ({
                            name, card: currentReport.results?.[name],
                          }))} columns={rankingColumns as any} rowKey="name" pagination={false} size="small"
                          rowClassName={r => r.card?.rank === 1 ? 'winner-row' : ''} />
                        </Card>
                      </Col>
                    </Row>
                  </>
                )}

                {batchResult && (
                  <>
                    <Alert type="success" showIcon icon={<FireOutlined />}
                      message={`批量完成！共测试 ${batchResult.num_scenarios} 个场景`} style={{ marginBottom: 16 }} />

                    <Row gutter={16} style={{ marginBottom: 20 }}>
                      {Object.entries(batchResult.overall_rankings || {}).map(([name, score], idx) => (
                        <Col span={4} key={name}>
                          <Card size="small" style={{
                            background: idx === 0 ? 'rgba(250,173,20,0.1)' : '#111827',
                            border: idx === 0 ? '1px solid rgba(250,173,20,0.3)' : '1px solid #1e293b',
                          }} bodyStyle={{ textAlign: 'center', padding: '12px 8px' }}>
                            {idx === 0 && <TrophyOutlined style={{ position: 'absolute', top: -10, right: 8, color: '#faad14', fontSize: 18 }} />}
                            <Statistic title={<Text ellipsis style={{ fontSize: 12 }}>{name}</Text>}
                              value={score} precision={1} suffix="/100"
                              valueStyle={{ color: idx === 0 ? '#faad14' : score >= 70 ? '#52c41a' : '#888', fontSize: 22, fontWeight: 'bold' }} />
                          </Card>
                        </Col>
                      ))}
                    </Row>

                    <Card title="各场景最佳" size="small" style={{ background: '#111827', borderColor: '#1e293b' }}
                      bodyStyle={{ background: '#111827' }}>
                      <Row gutter={[12, 12]}>
                        {Object.entries(batchResult.best_by_type || {}).map(([type, algo]) => (
                          <Col span={6} key={type}>
                            <div style={{ padding: '10px 14px', background: 'rgba(24,144,255,0.06)',
                              borderRadius: 6, border: '1px solid rgba(24,144,255,0.12)' }}>
                              <Text type="secondary">{type}</Text>
                              <Text strong style={{ color: '#1890ff', float: 'right' }}>{algo}</Text>
                            </div>
                          </Col>
                        ))}
                      </Row>
                    </Card>
                  </>
                )}
              </TabPane>

              <TabPane tab={<span><LineChartOutlined />维度分析</span>} key="details">
                {currentReport && (
                  <Collapse defaultActiveKey={['0']} ghost>
                    {(currentReport.rankings || []).map(([name, _score], idx) => {
                      const card = currentReport.results[name];
                      if (!card) return null;
                      return (
                        <Panel header={<Space>
                          <Text strong>{idx + 1}. {card.display_name}</Text>
                          {getGradeTag(card.grade)}
                          <Tag color="blue">{card.total_score.toFixed(1)}分</Tag>
                        </Space>} key={String(idx)}>
                          <Row gutter={[24, 16]}>
                            {Object.entries(DIMENSION_LABELS).map(([key, label]) => {
                              const dim = card.dimensions[key];
                              if (!dim) return null;
                              return (<Col span={12} key={key}>
                                <div style={{ marginBottom: 8 }}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                                    <Text>{label}</Text>
                                    <Text type="secondary">{dim.score.toFixed(1)}</Text>
                                  </div>
                                  <Progress percent={Math.round(dim.normalized || dim.score)}
                                    strokeColor={dim.normalized >= 80 ? '#52c41a' : dim.normalized >= 60 ? '#1890ff'
                                      : dim.normalized >= 40 ? '#faad14' : '#ff4d4f'}
                                    trailColor="rgba(255,255,255,0.06)" />
                                </div>
                                <div style={{ marginTop: 8, paddingLeft: 8 }}>
                                  {Object.entries(dim.details).map(([k, v]) => (
                                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                                      <Text type="secondary" style={{ fontSize: 11 }}>{k}</Text>
                                      <Text style={{ fontSize: 11 }}>{typeof v === 'number' ? v.toFixed(2) : v}</Text>
                                    </div>
                                  ))}
                                </div>
                              </Col>);
                            })}
                          </Row>
                        </Panel>
                      );
                    })}
                  </Collapse>
                )}
              </TabPane>
            </Tabs>
          ) : (
            <Card style={{ background: '#111827', borderColor: '#1e293b', minHeight: 400 }}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
                <div style={{ marginTop: 16 }}>
                  <Title level={5} style={{ color: '#888' }}>开始评测</Title>
                  <Paragraph type="secondary">
                    在左侧"场景配置"中选择参数，然后点击"运行评测"按钮。
                  </Paragraph>
                </div>
              } />
            </Card>
          )}
        </TabPane>

        {/* ===== Tab 3: 调度可视化 ===== */}
        <TabPane tab={<span><ApiOutlined />调度可视化</span>} key="visualize">
          <Row gutter={[20, 0]}>
            <Col span={24}>
              {vizResult ? (
                <SchedulingVisualizer
                  scenario={currentScenario}
                  trajectory={vizResult.trajectory}
                  selectedAlgorithm={vizResult.visualization_algo}
                />
              ) : (
                <Card style={{ background: '#111827', borderColor: '#1e293b', minHeight: 520 }}>
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      <div style={{ marginTop: 20 }}>
                        <Title level={5} style={{ color: '#888' }}>调度过程可视化</Title>
                        <Paragraph type="secondary" style={{ maxWidth: 500, margin: '12px auto' }}>
                          选择场景配置和算法，然后点击&quot;<strong>运行可视化评测</strong>&quot;按钮，
                          即可在下方查看算法的实时调度过程动画。
                          <br /><br />
                          支持的功能:
                        </Paragraph>
                        
                        <Row gutter={[12, 12]} style={{ maxWidth: 650, margin: '0 auto' }}>
                          {[
                            { icon: <CarOutlined />, title: 'AGV实时追踪', desc: '显示每台AGV的位置、状态、电量' },
                            { icon: <AimOutlined />, title: '任务执行监控', desc: '取货→运输→卸货全流程可视化' },
                            { icon: <BugOutlined />, title: '故障事件模拟', desc: 'AGV故障/堵塞等异常情况演示' },
                            { icon: <LineChartOutlined />, title: '性能指标曲线', desc: '完成率/利用率等实时统计' },
                          ].map(item => (
                            <Col span={12} key={item.title}>
                              <div style={{ textAlign: 'center', padding: '14px 10px',
                                background: 'rgba(24,144,255,0.04)', borderRadius: 8,
                                border: '1px solid rgba(24,144,255,0.08)' }}>
                                <div style={{ fontSize: 24, color: '#1890ff', marginBottom: 6 }}>{item.icon}</div>
                                <Text strong style={{ display: 'block', marginBottom: 3 }}>{item.title}</Text>
                                <Text type="secondary" style={{ fontSize: 11 }}>{item.desc}</Text>
                              </div>
                            </Col>
                          ))}
                        </Row>
                      </div>
                    }
                  />
                </Card>
              )}
            </Col>

            {/* 可视化结果摘要 */}
            {vizResult?.trajectory && (
              <Col span={24} style={{ marginTop: 12 }}>
                <Row gutter={[12, 12]}>
                  <Col span={4}>
                    <Statistic title="仿真总步数" value={vizResult.trajectory.total_steps} suffix="步"
                      prefix={<ClockCircleOutlined />} valueStyle={{ fontSize: 18 }} />
                  </Col>
                  <Col span={4}>
                    <Statistic title="任务完成" value={`${vizResult.trajectory.summary.completed_tasks}/${vizResult.trajectory.summary.total_tasks}`}
                      prefix={<CheckCircleOutlined />}
                      valueStyle={{
                        color: vizResult.trajectory.summary.completion_rate > 70 ? '#52c41a'
                          : vizResult.trajectory.summary.completion_rate > 30 ? '#faad14' : '#ff4d4f',
                        fontSize: 18
                      }} />
                  </Col>
                  <Col span={4}>
                    <Statistic title="完成率" value={vizResult.trajectory.summary.completion_rate} precision={1}
                      suffix="%" prefix={<LineChartOutlined />}
                      valueStyle={{ fontSize: 18 }} />
                  </Col>
                  <Col span={4}>
                    <Statistic title="总行驶距离" value={vizResult.trajectory.summary.total_distance} suffix="m"
                      prefix={<DashboardOutlined />} valueStyle={{ fontSize: 18 }} />
                  </Col>
                  <Col span={4}>
                    <Statistic title="峰值活跃AGV" value={vizResult.trajectory.summary.peak_active_agvs}
                      prefix={<TeamOutlined />} valueStyle={{ color: '#1890ff', fontSize: 18 }} />
                  </Col>
                  <Col span={4}>
                    <Statistic title="事件总数" value={vizResult.trajectory.summary.total_events}
                      prefix={<ThunderboltOutlined />} valueStyle={{ color: '#722ed1', fontSize: 18 }} />
                  </Col>
                </Row>
              </Col>
            )}

            {/* 事件时间轴 */}
            {vizResult?.trajectory?.events && (
              <Col span={24} style={{ marginTop: 12 }}>
                <Card title={<span><FieldTimeOutlined style={{ marginRight: 6 }} />关键事件时间轴</span>}
                  size="small" style={{ background: '#111827', borderColor: '#1e293b' }}
                  bodyStyle={{ background: '#111827', maxHeight: 220, overflow: 'auto' }}>
                  <Timeline
                    items={vizResult.trajectory.events.slice(0, 30).map(ev => ({
                      color: ev.event_type.includes('fault') ? (ev.event_type.includes('recover') ? 'green' : 'red')
                        : ev.event_type.includes('complete') ? 'green'
                          : ev.event_type.includes('assigned') ? 'blue' : 'gray',
                      children: (
                        <Text style={{ fontSize: 12 }}>
                          <Tag color="default" style={{ fontSize: 9, marginRight: 6 }}>步{ev.time_step}</Tag>
                          {ev.description}
                          {ev.extra?.fault_type ? <Tag color="red" style={{ fontSize: 9, marginLeft: 6 }}>{String(ev.extra.fault_type)}</Tag> : null}
                        </Text>
                      ),
                    }))}
                  />
                  {vizResult.trajectory.events.length > 30 && (
                    <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginTop: 8, fontSize: 11 }}>
                      还有 {vizResult.trajectory.events.length - 30} 条事件未显示...
                    </Text>
                  )}
                </Card>
              </Col>
            )}
          </Row>
        </TabPane>
      </Tabs>

      {/* 加载遮罩 */}
      {(evaluating || visualizing) && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.6)', zIndex: 1000,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        }}>
          <Spin size="large" tip={<span style={{ color: '#fff', marginTop: 16, display: 'block' }}>
            {visualizing ? '正在生成调度仿真轨迹...' : '正在执行算法评测...'}
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {activeMainTab === 'visualize' ? '正在计算AGV路径和任务分配序列，请耐心等待'
                : evalMode === 'batch' ? '多场景批量测试中' : '正在运行所有选定的算法'}
            </Text>
          </span>} />
        </div>
      )}

      {/* 自定义样式 */}
      <style>{`
        .benchmark-page .ant-card-head-title { color: #e0e0e0 !important; }
        .benchmark-page .ant-table { background: transparent; }
        .benchmark-page .ant-table-thead > tr > th {
          background: #1a2332 !important; color: #aaa !important; border-bottom: 1px solid #1e293b !important;
        }
        .benchmark-page .ant-table-tbody > tr > td {
          border-bottom: 1px solid #1a1f2e !important; color: #ccc !important;
        }
        .benchmark-page .ant-table-tbody > tr:hover > td { background: rgba(24,144,255,0.04) !important; }
        .benchmark-page .winner-row { background: rgba(250,173,20,0.06) !important; }
        .benchmark-page .ant-collapse-header { color: #e0e0e0 !important; }
        .benchmark-page .ant-descriptions-item-label { background: #161d2d !important; color: #888 !important; }
        .benchmark-page .ant-descriptions-item-content { background: #111827 !important; color: #ccc !important; }
        .benchmark-page .ant-tabs-tab-active .ant-tabs-tab-btn { color: #1890ff !important; }
        .benchmark-page .ant-collapse-content { background: transparent !important; }
        .benchmark-page .ant-collapse-content-box { background: transparent !important; }
      `}</style>
    </div>
  );
};

export default AlgorithmBenchmark;
