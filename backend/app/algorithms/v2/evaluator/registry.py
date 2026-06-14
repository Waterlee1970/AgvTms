"""
算法注册表 - 统一管理所有调度算法
=================================

设计原则:
  1. 策略模式: 所有算法实现统一接口 AlgorithmSpec
  2. 工厂模式: 通过名称动态创建算法实例
  3. 开闭原则: 新算法只需注册，无需修改评估框架
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class AlgorithmCategory(Enum):
    HEURISTIC = "heuristic"
    META_HEURISTIC = "meta_heuristic"
    OPTIMIZATION = "optimization"
    LEARNING = "learning"
    HYBRID = "hybrid"


@dataclass
class AlgorithmResult:
    """统一的算法执行结果"""
    success: bool = True
    assignments: Dict[str, str] = field(default_factory=dict)
    paths: Dict[str, List[str]] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    compute_time_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def num_assignments(self) -> int:
        return len(self.assignments)

    @property
    def avg_path_length(self) -> float:
        if not self.paths:
            return 0.0
        lengths = [len(p) for p in self.paths.values()]
        return sum(lengths) / len(lengths)


@dataclass
class AlgorithmSpec:
    """算法规格描述"""
    name: str
    display_name: str
    category: AlgorithmCategory
    version: str = "1.0.0"
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    required_deps: List[str] = field(default_factory=list)
    # 用 _is_available_explicit 标记是否显式设置过 is_available，
    # 避免在 __post_init__ 中覆盖调用者手动指定的值。
    _is_available_explicit: bool = field(default=False, init=False, repr=False)
    is_available: bool = True

    def __post_init__(self):
        # 仅当未显式指定 is_available 且有依赖时，才自动检测
        if self.required_deps and not self._is_available_explicit:
            self.is_available = self._check_deps()

    def _check_deps(self) -> bool:
        for dep in self.required_deps:
            try:
                __import__(dep)
            except ImportError:
                return False
        return True

    def __setattr__(self, key, value):
        """拦截 is_available 的外部赋值，标记为已显式设置"""
        super().__setattr__(key, value)
        if key == 'is_available':
            object.__setattr__(self, '_is_available_explicit', True)


class BaseAlgorithm(ABC):
    """算法基类"""

    @property
    @abstractmethod
    def name(self) -> str: pass

    @property
    @abstractmethod
    def spec(self) -> AlgorithmSpec: pass

    @abstractmethod
    def execute(self, nodes, edges, tasks, agvs, **kwargs) -> AlgorithmResult: pass

    def configure(self, config): pass
    def reset(self): pass


# ==================== 内置算法实现 ====================

class FcfsAlgorithm(BaseAlgorithm):
    """先来先服务 - 基准算法"""

    @property
    def name(self): return "fcfs"

    @property
    def spec(self):
        return AlgorithmSpec("fcfs", "先来先服务 (FCFS)", AlgorithmCategory.HEURISTIC,
                            description="按任务到达顺序分配给最近空闲AGV")

    def execute(self, nodes, edges, tasks, agvs, **kwargs):
        t0 = time.perf_counter()
        assignments, paths = {}, {}
        assigned, pos = set(), {n["id"]: (n.get("x",0), n.get("y",0)) for n in nodes}

        for task in sorted(tasks, key=lambda t: t.get("id","")):
            tid = task.get("id", task.get("task_id",""))
            pickup = task.get("pickup_node_id", task.get("pickup",""))
            if not pickup: continue

            best_agv, best_d = None, float('inf')
            for a in agvs:
                aid = a.get("id", a.get("agv_id",""))
                if aid in assigned or a.get("status","idle") == "busy": continue
                apos = a.get("current_node_id", a.get("pos",""))
                if apos not in pos or pickup not in pos: continue
                d = ((pos[apos][0]-pos[pickup][0])**2 + (pos[apos][1]-pos[pickup][1])**2)**0.5
                if d < best_d:
                    best_d, best_agv = d, aid

            if best_agv:
                assignments[tid] = best_agv
                assigned.add(best_agv)
                dropoff = task.get("dropoff_node_id", task.get("dropoff",""))
                src = next((a.get("current_node_id","") for a in agvs if a.get("id")==best_agv), "")
                paths[best_agv] = [src, pickup, dropoff]

        return AlgorithmResult(success=True, assignments=assignments, paths=paths,
                              compute_time_ms=(time.perf_counter()-t0)*1000,
                              metrics={"algorithm": "fcfs"})


class GreedyAlgorithm(BaseAlgorithm):
    """贪心算法 - 每次选最优对"""

    @property
    def name(self): return "greedy"

    @property
    def spec(self):
        return AlgorithmSpec("greedy", "最近邻贪心 (Greedy)", AlgorithmCategory.HEURISTIC,
                            description="每次迭代选择距离最小的AGV-任务对")

    def execute(self, nodes, edges, tasks, agvs, **kwargs):
        t0 = time.perf_counter()
        assignments, paths = {}, {}
        remaining, available = list(tasks), [a for a in agvs if a.get("status","idle")!="busy"]
        pos = {n["id"]: (n.get("x",0), n.get("y",0)) for n in nodes}

        while remaining and available:
            best_pair, best_cost = None, float('inf')
            for task in remaining:
                tid = task.get("id", task.get("task_id",""))
                pickup = task.get("pickup_node_id", task.get("pickup",""))
                if pickup not in pos: continue
                px, py = pos[pickup]
                for a in available:
                    aid = a.get("id", a.get("agv_id",""))
                    apos = a.get("current_node_id", a.get("pos",""))
                    if apos not in pos: continue
                    cost = ((pos[apos][0]-px)**2 + (pos[apos][1]-py)**2)**0.5
                    if cost < best_cost:
                        best_cost = cost
                        best_pair = (tid, aid, task, a)

            if best_pair:
                tid, aid, task, agv = best_pair
                assignments[tid] = aid
                dropoff = task.get("dropoff_node_id", task.get("dropoff",""))
                paths[aid] = [agv.get("current_node_id",""), task.get("pickup_node_id",""), dropoff]
                remaining = [t for t in remaining if t.get("id",t.get("task_id",""))!=tid]
                available = [a for a in available if a.get("id",a.get("agv_id",""))!=aid]

        return AlgorithmResult(success=True, assignments=assignments, paths=paths,
                              compute_time_ms=(time.perf_counter()-t0)*1000,
                              metrics={"algorithm": "greedy"})


class V1HybridAdapter(BaseAlgorithm):
    """V1混合调度 (SA+ACO+NLP) 适配器 — 修复版"""

    @property
    def name(self): return "v1_hybrid"

    @property
    def spec(self):
        return AlgorithmSpec("v1_hybrid", "V1 混合调度 (SA+ACO+NLP)", AlgorithmCategory.HYBRID,
                            version="1.5.0",
                            description="模拟退火分配 + 蚁群路径规划 + 非线性输送线优化")

    def execute(self, nodes, edges, tasks, agvs, **kwargs):
        t0 = time.perf_counter()
        try:
            # === 修复: 使用相对路径兼容两种运行模式 ===
            try:
                from backend.app.algorithms.hybrid import HybridScheduler, AlgorithmConfig
                from backend.app.models.schemas import MapNode, MapEdge, AgvTask, AgvStatus, ConveyorTask, ConveyorSegment
            except ImportError:
                from app.algorithms.hybrid import HybridScheduler, AlgorithmConfig
                from app.models.schemas import MapNode, MapEdge, AgvTask, AgvStatus, ConveyorTask, ConveyorSegment

            def _get_task_field(t, *keys):
                """按优先级获取任务字段值"""
                for k in keys:
                    v = t.get(k)
                    if v is not None and v != '':
                        return str(v)
                return ''

            def _get_agv_field(a, *keys):
                """按优先级获取AGV字段值"""
                for k in keys:
                    v = a.get(k)
                    if v is not None and v != '':
                        return v
                return ''

            mn = [MapNode(id=n["id"], name=n.get("name", n.get("id", "")),
                          x=float(n.get("x", 0)), y=float(n.get("y", 0)))
                  for n in nodes]
            me = [MapEdge(id=e.get("id", ""), from_node=e.get("from_node", e.get("from", "")),
                         to_node=e.get("to_node", e.get("to", "")),
                         distance=float(e.get("distance", e.get("weight", 1.0))))
                  for e in edges]
            
            # AgvTask: 支持 pickup_node / pickup_node_id / pickup 三种字段名
            tk = [AgvTask(
                      id=t.get("id", ""),
                      priority=int(t.get("priority", 5)),
                      pickup_node=_get_task_field(t, "pickup_node", "pickup_node_id", "pickup"),
                      dropoff_node=_get_task_field(t, "dropoff_node", "dropoff_node_id", "dropoff"),
                      status=t.get("status", "pending")
                  ) for t in tasks]
            
            # AgvStatus: 支持 current_node / current_node_id / pos 三种字段名
            av = [AgvStatus(
                      id=a.get("id", ""),
                      current_node=_get_agv_field(a, "current_node", "current_node_id", "pos"),
                      battery=float(_get_agv_field(a, "battery", "battery_level", "100")),
                      status=a.get("status", "idle")
                  ) for a in agvs]

            ct = kwargs.get("conveyor_tasks")
            cs = kwargs.get("conveyor_segments")

            # === 修复: 传入 AlgorithmConfig 参数 ===
            config = AlgorithmConfig()
            scheduler = HybridScheduler(config)
            result = scheduler.schedule(
                mn, me, tk, av,
                conveyor_tasks=[ConveyorTask(**c) for c in ct] if ct else None,
                conveyor_segments=[ConveyorSegment(**c) for c in cs] if cs else None)

            # ScheduleResult.assignments 是 List[AgvAssignment]，需转换为 Dict[str,str]
            assignments = {}
            if result and result.assignments:
                for a in result.assignments:
                    if isinstance(a, dict):
                        assignments[a.get("task_id", "")] = a.get("agv_id", "")
                    elif hasattr(a, 'task_id') and hasattr(a, 'agv_id'):
                        assignments[a.task_id] = a.agv_id

            paths = {}
            if result and hasattr(result, 'agv_paths') and result.agv_paths:
                paths = dict(result.agv_paths)

            metrics_dict = {}
            if result and hasattr(result, 'metrics') and result.metrics:
                m = result.metrics
                metrics_dict = {
                    "makespan": getattr(m, 'total_makespan', 0),
                    "total_cost": getattr(result, 'total_cost', 0),
                    "utilization": getattr(m, 'agv_utilization', 0),
                }

            return AlgorithmResult(True, assignments=assignments, paths=paths,
                                  metrics=metrics_dict,
                                  compute_time_ms=(time.perf_counter()-t0)*1000,
                                  metadata={"algorithm": "v1_hybrid"})
        except Exception as e:
            return AlgorithmResult(False, error=f"V1-Hybrid: {str(e)}",
                                  compute_time_ms=(time.perf_counter()-t0)*1000)


class V2MipAdapter(BaseAlgorithm):
    """V2 MIP 最优分配适配器 — 修复版 (正确使用 MipTaskAssigner)"""

    @property
    def name(self): return "v2_mip"

    @property
    def spec(self):
        avail = True
        try: import ortools
        except: avail = False
        return AlgorithmSpec("v2_mip", "V2 MIP 最优分配 (CP-SAT)", AlgorithmCategory.OPTIMIZATION,
                            version="2.0.0", required_deps=["ortools"], is_available=avail,
                            description="Google OR-Tools CP-SAT 整数规划最优任务分配")

    def execute(self, nodes, edges, tasks, agvs, **kwargs):
        t0 = time.perf_counter()
        try:
            # === 修复: 使用正确的类名和路径 + 统一字段映射 + 兼容导入 ===
            try:
                from backend.app.algorithms.v2.task_assignment.mip_solver import (
                    MipTaskAssigner, ObjectiveMode, AssignmentResult, create_distance_matrix,
                )
                from backend.app.models.schemas import MapNode, MapEdge
            except ImportError:
                from app.algorithms.v2.task_assignment.mip_solver import (
                    MipTaskAssigner, ObjectiveMode, AssignmentResult, create_distance_matrix,
                )
                from app.models.schemas import MapNode, MapEdge

            def _get_task_field(t, *keys):
                for k in keys:
                    v = t.get(k)
                    if v is not None and v != '':
                        return str(v)
                return ''

            def _get_agv_field(a, *keys):
                for k in keys:
                    v = a.get(k)
                    if v is not None and v != '':
                        return v
                return ''

            # 构建正确的 schema 对象（用于距离矩阵计算）
            mn_schema = [MapNode(id=n["id"], name=n.get("name", n["id"]),
                                  x=float(n["x"]), y=float(n["y"])) for n in nodes]
            me_schema = [MapEdge(from_node=e.get("from_node", e.get("from", "")),
                                 to_node=e.get("to_node", e.get("to", "")),
                                 distance=float(e.get("distance", e.get("weight", 1.0))))
                         for e in edges]

            # === 使用统一的字段映射函数 ===
            task_dicts = []
            for t in tasks:
                task_dicts.append({
                    "id": str(t.get("id", t.get("task_id", ""))),
                    "pickup_node": _get_task_field(t, "pickup_node", "pickup_node_id", "pickup"),
                    "dropoff_node": _get_task_field(t, "dropoff_node", "dropoff_node_id", "dropoff"),
                    "priority": int(t.get("priority", 5)),
                    "deadline": None,
                })

            agv_dicts = []
            for a in agvs:
                agv_dicts.append({
                    "id": str(a.get("id", a.get("agv_id", ""))),
                    "current_node": _get_agv_field(a, "current_node", "current_node_id", "pos"),
                    "battery": float(_get_agv_field(a, "battery", "battery_level", "100")),
                    "capacity": int(_get_agv_field(a, "capacity", "1")),
                    "speed": float(_get_agv_field(a, "speed", "1.5")),
                })

            # 构建距离矩阵
            dist_matrix = create_distance_matrix(mn_schema, me_schema)

            # 创建 assigner 并执行
            assigner = MipTaskAssigner(
                objective_mode=ObjectiveMode.BALANCED,
                time_limit_seconds=kwargs.get("timeout", 10),
            )
            ar: AssignmentResult = assigner.assign(task_dicts, agv_dicts, dist_matrix)

            # === 修复: 转换 AssignmentResult.assignments (Dict[str, List[str]] → Dict[str, str]) ===
            assignments = {}
            for agv_id, task_list in ar.assignments.items():
                for tid in task_list:
                    assignments[tid] = agv_id

            # 路径规划（使用统一的字段映射函数，与输入端保持一致）
            paths = {}
            for tid, aid in assignments.items():
                agv = next((a for a in agvs if a.get("id") == aid or a.get("agv_id") == aid), None)
                task = next((t for t in tasks if t.get("id") == tid or t.get("task_id") == tid), None)
                if agv and task:
                    # 使用统一的字段映射（修复: 原来用了错误的字段名导致路径节点为空）
                    src = _get_agv_field(agv, "current_node", "current_node_id", "pos")
                    pickup = _get_task_field(task, "pickup_node", "pickup_node_id", "pickup")
                    dropoff = _get_task_field(task, "dropoff_node", "dropoff_node_id", "dropoff")
                    if src and pickup and dropoff:
                        paths[aid] = [src, pickup, dropoff]

            return AlgorithmResult(
                ar.status == "OPTIMAL" or ar.status == "FEASIBLE",
                assignments=assignments, paths=paths,
                metrics={
                    "optimal_cost": ar.objective_value,
                    "makespan": ar.makespan,
                    "total_travel": ar.total_travel,
                    "optimality_gap": ar.optimality_gap,
                    "status": ar.status,
                },
                compute_time_ms=(time.perf_counter()-t0)*1000 + getattr(ar, 'solve_time_ms', 0),
                metadata={"algorithm": "v2_mip"}
            )
        except Exception as e:
            return AlgorithmResult(False, error=f"V2-MIP: {str(e)}",
                                  compute_time_ms=(time.perf_counter()-t0)*1000)


class V2OrchestratorAdapter(BaseAlgorithm):
    """V2 三层混合编排器适配器 — 修复版 (含预测引擎联动)"""

    @property
    def name(self): return "v2_orchestrator"

    @property
    def spec(self):
        avail = True
        try: import ortools
        except: avail = False
        return AlgorithmSpec("v2_orchestrator", "V2 混合编排器 (三层架构)", AlgorithmCategory.HYBRID,
                            version="2.0.0", required_deps=["ortools"], is_available=avail,
                            description="战略层MIP + 战术层规则/RL + 操作层A*时间窗")

    def execute(self, nodes, edges, tasks, agvs, **kwargs):
        t0 = time.perf_counter()
        try:
            # === 修复1: 使用正确的导入路径 + 统一字段映射 + 兼容导入 ===
            try:
                from backend.app.algorithms.v2.hybrid_orchestrator.orchestrator import (
                    HybridOrchestratorV2, OrchestratorConfig, OrchestratorMode,
                )
                from backend.app.models.schemas import (
                    MapNode, MapEdge, AgvTask, AgvStatus, AgvTaskStatus,
                )
            except ImportError:
                from app.algorithms.v2.hybrid_orchestrator.orchestrator import (
                    HybridOrchestratorV2, OrchestratorConfig, OrchestratorMode,
                )
                from app.models.schemas import (
                    MapNode, MapEdge, AgvTask, AgvStatus, AgvTaskStatus,
                )

            # === 通用字段映射函数 ===
            def _get_task_field(t, *keys):
                for k in keys:
                    v = t.get(k)
                    if v is not None and v != '':
                        return str(v)
                return ''

            def _get_agv_field(a, *keys):
                for k in keys:
                    v = a.get(k)
                    if v is not None and v != '':
                        return v
                return ''

            mn = [MapNode(id=n["id"], name=n.get("name", n.get("id", "")),
                          x=float(n.get("x", 0)), y=float(n.get("y", 0)))
                  for n in nodes]
            me = [MapEdge(from_node=e.get("from_node", e.get("from", "")),
                         to_node=e.get("to_node", e.get("to", "")),
                         distance=float(e.get("distance", e.get("weight", 1.0))))
                  for e in edges]
            
            # AgvTask: 使用统一字段映射
            tk = []
            for t in tasks:
                pickup = _get_task_field(t, "pickup_node", "pickup_node_id", "pickup")
                dropoff = _get_task_field(t, "dropoff_node", "dropoff_node_id", "dropoff")
                if pickup and dropoff:  # 只添加有效任务
                    tk.append(AgvTask(
                        id=t.get("id"),
                        priority=int(t.get("priority", 5)),
                        pickup_node=pickup,
                        dropoff_node=dropoff,
                        status=getattr(AgvTaskStatus, str(t.get("status", "pending")).upper(), AgvTaskStatus.PENDING)
                    ))
            
            # AgvStatus: 使用统一字段映射
            av = []
            for a in agvs:
                av.append(AgvStatus(
                    id=str(a.get("id", a.get("agv_id", ""))),
                    current_node=_get_agv_field(a, "current_node", "current_node_id", "pos"),
                    battery=float(_get_agv_field(a, "battery", "battery_level", "100")),
                    speed=float(a.get("speed", 0.0)),
                    capacity=int(a.get("capacity", 1)),
                ))

            # === 修复3: 使用正确的枚举值 ===
            mode_name = kwargs.get("mode", "benchmark")
            mode_map = {
                "strategic": OrchestratorMode.REALTIME,
                "tactical": OrchestratorMode.REALTIME,
                "operational": OrchestratorMode.REALTIME,
                "auto": OrchestratorMode.BENCHMARK,
                "realtime": OrchestratorMode.REALTIME,
                "simulation": OrchestratorMode.SIMULATION,
                "benchmark": OrchestratorMode.BENCHMARK,
                "debug": OrchestratorMode.DEBUG,
            }
            cfg = OrchestratorConfig(mode=mode_map.get(mode_name, OrchestratorMode.BENCHMARK))
            cfg.simulation_speedup = 10.0   # 加速仿真
            cfg.max_agvs = max(len(agvs), 5)

            # === 初始化编排器 ===
            orch = HybridOrchestratorV2(config=cfg)
            orch.initialize(mn, me)

            # === 预测引擎联动 ===
            if kwargs.get("enable_predictive", True):
                try:
                    from backend.app.algorithms.v2.predictive.engine import PredictiveEngine
                    engine = PredictiveEngine()
                    node_set = {n.id for n in mn}
                    edge_list = [(e.from_node, e.to_node) for e in me if not e.is_conveyor]
                    engine.set_graph_topology(node_set, edge_list)

                    # 注入历史数据（模拟）
                    for task in tk[:min(10, len(tk))]:
                        engine.record_task_arrival(
                            task_id=task.id or f"task_{hash(task.pickup_node)%10000}",
                            pickup_node=task.pickup_node,
                            dropoff_node=task.dropoff_node,
                        )

                    insight = engine.predict(horizon_seconds=300.0)
                    if hasattr(insight, 'overall_risk_score') and insight.overall_risk_score > 0.7:
                        logger.warning(f"[V2-Orch] 高风险预测: score={insight.overall_risk_score}")
                except Exception as pred_err:
                    logger.debug(f"预测引擎跳过: {pred_err}")

            # 执行调度
            ct = kwargs.get("conveyor_tasks")
            cs = kwargs.get("conveyor_segments")
            from backend.app.models.schemas import ConveyorTask as CT, ConveyorSegment as CS
            conv_tasks = [CT(**c) for c in ct] if ct else None
            conv_segs = [CS(**c) for c in cs] if cs else None

            result = orch.schedule(mn, me, tk, av,
                                   conveyor_tasks=conv_tasks,
                                   conveyor_segments=conv_segs)

            # 转换结果格式
            assignments = {}
            if result and result.assignments:
                for a in result.assignments:
                    if isinstance(a, dict):
                        assignments[a.get("task_id", "")] = a.get("agv_id", "")
                    elif hasattr(a, 'task_id'):
                        assignments[a.task_id or ""] = a.agv_id

            paths = {}
            if result and hasattr(result, 'agv_paths') and result.agv_paths:
                paths = dict(result.agv_paths)

            metrics_dict = {}
            if result and hasattr(result, 'metrics') and result.metrics:
                m = result.metrics
                metrics_dict = {
                    "makespan": getattr(m, 'total_makespan', getattr(result, 'makespan', 0)),
                    "total_cost": getattr(result, 'total_cost', 0),
                    "utilization": getattr(m, 'agv_utilization', 0),
                    "completion_rate": getattr(m, 'task_completion_rate', 0),
                }
            elif result:
                metrics_dict = {"makespan": getattr(result, 'makespan', 0)}

            return AlgorithmResult(
                True, assignments=assignments, paths=paths,
                metrics=metrics_dict,
                compute_time_ms=(time.perf_counter()-t0)*1000,
                metadata={"algorithm": "v2_orchestrator"}
            )
        except Exception as e:
            return AlgorithmResult(False, error=f"V2-Orchestrator: {str(e)}",
                                  compute_time_ms=(time.perf_counter()-t0)*1000)


class RLDQNAdapter(BaseAlgorithm):
    """RL DQN 调度器 (可选依赖) — 修复版 (含训练/推理双模式)"""

    @property
    def name(self): return "rl_dqn"

    @property
    def spec(self):
        avail = False
        try:
            import torch; import gymnasium
            avail = True
        except: pass
        return AlgorithmSpec("rl_dqn", "RL-DQN 强化学习调度", AlgorithmCategory.LEARNING,
                            version="2.1.0", required_deps=["torch","gymnasium"],
                            is_available=avail, description="Double Dueling DQN + Action Mask + PER")

    def execute(self, nodes, edges, tasks, agvs, **kwargs):
        """
        RL 调度执行。

        模式:
        - 有预训练模型 → 推理模式（快速）
        - 无模型 + allow_training=True → 训练后推理（慢）
        - 否则 → 使用规则回退（FCFS-like with learned ordering）
        """
        t0 = time.perf_counter()
        try:
            # === 修复: 兼容两种导入模式 ===
            try:
                from backend.app.algorithms.v2.rl_scheduler import (
                    RLScheduler, RLSchedulerMode, AgvEnvConfig,
                    AgvState as RLAgvState, TaskState as RLTaskState,
                )
            except ImportError:
                from app.algorithms.v2.rl_scheduler import (
                    RLScheduler, RLSchedulerMode, AgvEnvConfig,
                )
                from app.algorithms.v2.rl_scheduler.environment import (
                    AgvState as RLAgvState, TaskState as RLTaskState,
                )
            import numpy as np

            model_path = kwargs.get("model_path")
            allow_training = kwargs.get("allow_training", False)
            train_steps = kwargs.get("train_steps", 5000)

            # === 构建 RL Scheduler ===
            env_cfg = AgvEnvConfig(
                max_agvs=max(len(agvs), 5),
                max_tasks=max(len(tasks), 10),
                max_steps_per_episode=min(len(tasks) * len(agvs) * 3, 200),
            )
            scheduler = RLScheduler(mode=RLSchedulerMode.DQN_ONLY,
                                   env_config=env_cfg,
                                   model_dir=kwargs.get("model_dir", "backend/benchmark_results/rl_models"))

            scheduler.initialize()

            # 尝试加载模型
            model_loaded = scheduler.load_models("default")

            if not model_loaded and allow_training:
                # 快速训练模式
                logger.info("[RL-DQN] No model found, starting quick training...")
                train_result = scheduler.train_dqn(total_timesteps=train_steps)
                scheduler.save_models("default")
                logger.info(f"[RL-DQN] Training done: {train_result}")
                model_loaded = True

            # === 将评估框架的数据转换为 RL 决策 ===
            assignments, paths = {}, {}

            if model_loaded:
                # 使用 RL 推理
                remaining_tasks = list(tasks)
                assigned_agvs = set()

                for _ in range(min(len(tasks) * len(agvs), 50)):
                    if not remaining_tasks:
                        break
                    available_agvs = [a for a in agvs
                                     if a.get("id") not in assigned_agvs
                                     and a.get("status", "idle") != "busy"]
                    if not available_agvs or not remaining_tasks:
                        break

                    # 构建状态向量
                    state_vec = self._build_rl_state(nodes, edges, remaining_tasks, available_agvs)

                    # 构建动作掩码
                    n_task = min(len(remaining_tasks), env_cfg.max_tasks)
                    n_agv = min(len(available_agvs), env_cfg.max_agvs)
                    action_mask = np.ones(n_task * n_agv + 1, dtype=bool)

                    # RL 决策
                    action = scheduler.dispatch(state_vec, action_mask=action_mask)

                    # 解码动作
                    if action < n_task * n_agv:
                        task_idx = action // n_agv
                        agv_idx = action % n_agv
                        if task_idx < len(remaining_tasks) and agv_idx < len(available_agvs):
                            task = remaining_tasks[task_idx]
                            agv = available_agvs[agv_idx]

                            tid = task.get("id", task.get("task_id", ""))
                            aid = agv.get("id", agv.get("agv_id", ""))
                            assignments[tid] = aid
                            assigned_agvs.add(aid)

                            # 构建路径
                            src = agv.get("current_node", agv.get("current_node_id",
                                                agv.get("pos", "")))
                            pickup = task.get("pickup_node", task.get("pickup", ""))
                            dropoff = task.get("dropoff_node", task.get("dropoff", ""))
                            paths[aid] = [src, pickup, dropoff] if all([src, pickup, dropoff]) else []

                            remaining_tasks.pop(task_idx)
                    else:
                        break  # skip action
            else:
                # 回退到智能贪心（按距离+优先级排序）
                pos = {n["id"]: (n.get("x", 0), n.get("y", 0)) for n in nodes}
                sorted_tasks = sorted(tasks, key=lambda t: (
                    -int(t.get("priority", 5)),  # 高优先级先
                ))
                used_agvs = set()
                for task in sorted_tasks:
                    tid = task.get("id", task.get("task_id", ""))
                    pickup = task.get("pickup_node", task.get("pickup_node_id",
                                              task.get("pickup", "")))
                    if pickup not in pos:
                        continue
                    best_agv, best_d = None, float('inf')
                    for a in agvs:
                        aid = a.get("id", a.get("agv_id", ""))
                        if aid in used_agvs:
                            continue
                        apos = a.get("current_node", a.get("current_node_id",
                                               a.get("pos", "")))
                        if apos not in pos:
                            continue
                        d = ((pos[apos][0]-pos[pickup][0])**2 +
                             (pos[apos][1]-pos[pickup][1])**2)**0.5
                        if d < best_d:
                            best_d, best_agv = d, aid
                    if best_agv:
                        assignments[tid] = best_agv
                        used_agvs.add(best_agv)
                        dropoff = task.get("dropoff_node", task.get("dropoff_node_id",
                                                  task.get("dropoff", "")))
                        paths[best_agv] = [
                            agvs[[i for i,a in enumerate(agvs)
                                  if a.get("id")==best_agv][0]].get("current_node",
                                      agvs[[i for i,a in enumerate(agvs)
                                            if a.get("id")==best_agv][0]].get("current_node_id",""))
                            if any(a.get("id")==best_agv for a in agvs) else "",
                            pickup, dropoff
                        ]

            return AlgorithmResult(True, assignments=assignments, paths=paths,
                                  metrics={
                                      "model_loaded": model_loaded,
                                      "rl_mode": "inference" if model_loaded else "fallback",
                                  },
                                  compute_time_ms=(time.perf_counter()-t0)*1000,
                                  metadata={"algorithm": "rl_dqn"})
        except Exception as e:
            import traceback; traceback.print_exc()
            return AlgorithmResult(False, error=f"RL-DQN: {str(e)}",
                                  compute_time_ms=(time.perf_counter()-t0)*1000)

    def _build_rl_state(self, nodes, edges, tasks, agvs):
        """构建 RL 环境的状态向量"""
        import numpy as np
        obs = []

        # AGV 特征 (每个AGV 7维)
        for a in agvs[:20]:
            is_busy = 1.0 if a.get("status", "idle") in ("busy", "moving") else 0.0
            obs.extend([
                float(a.get("x", 0)) / 100.0,
                float(a.get("y", 0)) / 100.0,
                float(a.get("battery", 100)) / 100.0,
                float(a.get("speed", 1.5)) / 3.0,
                float(a.get("capacity", 1)) / 3.0,
                is_busy,
                0.0,  # progress placeholder
            ])
        # Pad to max_agvs*7
        pad_len = max(0, 20 - len(agvs)) * 7
        obs.extend([0.0] * pad_len)

        # Task 特征 (每个任务 6维)
        for t in tasks[:50]:
            obs.extend([
                0.5,  # px normalized (placeholder)
                0.5,  # py normalized
                0.5,  # dx normalized
                0.5,  # dy normalized
                float(t.get("priority", 5)) / 10.0,
                0.0,  # age
            ])
        pad_len = max(0, 50 - len(tasks)) * 6
        obs.extend([0.0] * pad_len)

        # 全局特征
        obs.extend([
            0.0,   # time
            1.0,   # idle ratio
            len(tasks) / 50.0,
            0.0,   # congestion
        ])

        return np.array(obs[:20*7 + 50*6 + 4], dtype=np.float32)


# ==================== 算法注册表（单例） ====================

class AlgorithmRegistry:
    """
    全局算法注册表

    使用方式:
        registry = AlgorithmRegistry.get_instance()
        algo = registry.create("fcfs")
        result = algo.execute(nodes, edges, tasks, agvs)
    """

    _instance = None
    _algorithms: Dict[str, type] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._register_builtins()
        return cls._instance

    @classmethod
    def get_instance(cls) -> "AlgorithmRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _register_builtins(self):
        """注册所有内置算法"""
        builtins = [
            FcfsAlgorithm,
            GreedyAlgorithm,
            V1HybridAdapter,
            V2MipAdapter,
            V2OrchestratorAdapter,
            RLDQNAdapter,
        ]
        for algo_cls in builtins:
            instance = algo_cls()
            self._algorithms[instance.name] = algo_cls

    def register(self, algorithm_class: type):
        """注册自定义算法"""
        instance = algorithm_class()
        self._algorithms[instance.name] = algorithm_class

    def create(self, name: str, **config) -> BaseAlgorithm:
        """创建算法实例"""
        if name not in self._algorithms:
            raise ValueError(f"未知算法: {name}. 已注册: {list(self._algorithms.keys())}")
        instance = self._algorithms[name]()
        if config:
            instance.configure(config)
        return instance

    def list_all(self) -> List[AlgorithmSpec]:
        """列出所有已注册算法的规格"""
        specs = []
        for algo_cls in self._algorithms.values():
            instance = algo_cls()
            specs.append(instance.spec)
        return specs

    def list_available(self) -> List[AlgorithmSpec]:
        """列出可用的算法（依赖已安装）"""
        return [s for s in self.list_all() if s.is_available]

    def list_by_category(self, category: AlgorithmCategory) -> List[AlgorithmSpec]:
        """按类别列出算法"""
        return [s for s in self.list_all() if s.category == category]


def get_registry() -> AlgorithmRegistry:
    """获取全局注册表实例"""
    return AlgorithmRegistry.get_instance()


def create_algorithm(name: str, **config) -> BaseAlgorithm:
    """便捷方法：直接创建算法"""
    return get_registry().create(name, **config)
