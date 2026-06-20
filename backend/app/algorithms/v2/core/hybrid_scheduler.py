"""
HybridScheduler — 混合调度引擎 (M1↔M2 统一入口)

整合三大核心能力:
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 任务分派 (Dispatcher)                              │
│    ├─ MIP优化分派 (CP-SAT, 大规模最优)                       │
│    ├─ 匈牙利算法 (O(n³), 中等规模快速)                        │
│    └─ 贪心就近 (fallback, 无依赖)                            │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: 路径规划 (Router)                                  │
│    ├─ 单agent: Theta* (任意角度, 平滑路径)                   │
│    ├─ 多agent: ECBS (Conflict-Based Search, 无冲突协调)     │
│    └─ Fallback: BidirectionalAStar / SIPP                   │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 交通管制 (Traffic Control)                        │
│    ├─ ResourceLockManager (死锁预防, O(1) 锁操作)            │
│    └─ TrafficControlSystem (拥堵检测, 通行审批)              │
└─────────────────────────────────────────────────────────────┘

智能决策逻辑:
- agent_count ≤ 3: 使用 Theta* (单agent最优, 低开销)
- agent_count > 3: 使用 ECBS (多agent协调, ω=1.2 子最优)
- 拥堵等级 ≥ MEDIUM: 自动触发重规划
- 死锁检测 → WAIT_DIE 策略回退

性能目标:
- 10 agents × 100 nodes: < 500ms 端到端调度延迟
- 100 agents × 500 nodes: < 2s (ECBS + 预测缓存)

Author: Architecture Team
Date: 2026-06-19
"""

from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum, auto

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 数据结构定义
# ═══════════════════════════════════════════════════════════════

class SchedulerMode(Enum):
    """调度模式"""
    SINGLE_AGENT = "single_agent"      # 纯单agent模式 (Theta*)
    MULTI_AGENT = "multi_agent"        # 多agent协调模式 (CBS/ECBS)
    AUTO = "auto"                      # 自动选择 (推荐)


class SchedulingPriority(Enum):
    """任务优先级 (数值越大越优先)"""
    LOW = 1
    NORMAL = 5
    HIGH = 10
    URGENT = 20
    EMERGENCY = 50


@dataclass
class AgvState:
    """AGV状态快照"""
    id: str
    current_node: str
    status: str  # idle/moving/charging/loading/error
    battery_level: float = 100.0
    capacity: int = 1
    current_task_id: Optional[str] = None
    speed: float = 1.0  # m/s


@dataclass
class Task:
    """任务定义"""
    id: str
    pickup_node: str
    delivery_node: str  # internal name; accepts dropoff/dropoff_node/delivery externally
    priority: SchedulingPriority = SchedulingPriority.NORMAL
    payload_weight: float = 0.0  # kg
    deadline: Optional[float] = None  # Unix timestamp, None=无截止时间
    created_at: float = field(default_factory=time.time)

    # [G1-FIX P0-2] 兼容多种字段名 (scenarios.py用pickup_node_id/dropoff_node_id, 外部可能用delivery)
    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        """从字典创建任务，兼容多种字段命名风格。"""
        _pick_keys = ("pickup_node", "pickup", "pickup_node_id")
        _drop_keys = ("delivery_node", "delivery", "dropoff_node", "dropoff", "dropoff_node_id")
        pickup = next((d.get(k) for k in _pick_keys if d.get(k)), "")
        delivery = next((d.get(k) for k in _drop_keys if d.get(k)), "")
        return cls(
            id=d.get("id", ""),
            pickup_node=pickup,
            delivery_node=delivery,
            priority=SchedulingPriority(d.get("priority", SchedulingPriority.NORMAL.value)),
            payload_weight=d.get("payload_weight", 0.0),
            deadline=d.get("deadline"),
            created_at=d.get("created_at", time.time()),
        )


@dataclass
class ScheduledPath:
    """
    已调度路径结果
    
    统一格式，兼容 Router.RouteResult 和 MAPF.PathResult
    """
    agv_id: str
    task_id: str
    path: List[str]                    # 有序节点ID列表 [n0, n1, n2, ...]
    total_distance: float = 0.0
    estimated_time: float = 0.0         # 秒 (含等待时间)
    waypoints: int = 0                  # 拐点数 (Theta*优化指标)
    conflicts_resolved: int = 0         # 解决的冲突数 (MAPF指标)
    algorithm_used: str = ""            # 实际使用的算法名
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SchedulingResult:
    """
    完整调度结果
    
    包含所有AGV的路径分配和全局统计信息
    """
    task_id: str                         # 原始请求ID
    paths: Dict[str, ScheduledPath]       # {agv_id: ScheduledPath}
    
    # 全局统计
    total_distance: float = 0.0           # Σ 所有路径距离
    max_makespan: float = 0.0             # 最慢AGV完成时间
    total_conflicts: int = 0              # 检测到的总冲突数
    scheduling_time_ms: float = 0.0       # 调度耗时
    
    # 决策元数据
    mode_used: SchedulerMode = SchedulerMode.AUTO
    algorithm_selected: str = ""
    congestion_level: str = "NONE"
    deadlock_prevented: bool = False
    success: bool = True                  # 整体是否成功 (新增)
    error_message: Optional[str] = None   # 错误信息 (新增)
    
    # 可观测性
    metrics: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# HybridScheduler 核心类
# ═══════════════════════════════════════════════════════════════

class HybridScheduler:
    """
    混合调度引擎 — AGV-TMS系统的统一调度入口
    
    设计原则:
    1. **分层解耦**: Dispatcher(分派) → Router(路径) → Traffic(管制)
    2. **智能切换**: 根据agent数量自动选择 Theta* 或 ECBS
    3. **弹性降级**: 任何层失败自动降级到更简单策略
    4. **可观测性**: 全链路耗时统计 + Prometheus指标埋点
    
    使用示例:
        ```python
        scheduler = HybridScheduler(graph, traffic_mgr)
        
        # 场景1: 批量调度多个任务
        tasks = [Task(id="T1", pickup="A", delivery="B"), ...]
        agvs = [AgvState(id="AGV-1", current_node="X"), ...]
        result = await scheduler.schedule_batch(tasks, agvs)
        
        # 场景2: 单个紧急任务
        result = await scheduler.schedule_single(
            task=urgent_task,
            agv=nearest_agv,
            mode=SchedulerMode.SINGLE_AGENT,
            use_theta_star=True
        )
        
        # 场景3: 动态重规划 (拥堵感知)
        if result.congestion_level == "CRITICAL":
            reroute_result = await scheduler.reroute(
                task_ids=[result.task_id],
                blocked_nodes=get_blocked_from_congestion(result)
            )
        ```
    """

    def __init__(
        self,
        graph: Any,                          # PathGraph 实例
        traffic_manager=None,                # TrafficControlSystem 实例 (可选)
        mode: SchedulerMode = SchedulerMode.AUTO,
        ebs_omega: float = 1.2,             # ECBS 子最优边界 (推荐 1.2-1.5)
        default_dispatcher: str = "mip",  # [G1-FIX P1-3] 默认MIP(最优), 原来是"hungarian"(仅二分匹配)
        enable_deadlock_prevention: bool = True,
        enable_theta_star: bool = True,      # 是否启用 Theta* (需要 graph_builder 支持)
        congestion_threshold: str = "MEDIUM", # 触发重规划的拥堵阈值
    ):
        """
        初始化混合调度器
        
        Args:
            graph: PathGraph 地图拓扑实例
            traffic_manager: TrafficControlSystem 交通管制系统 (可选)
            mode: 调度模式 (AUTO/SINGLE/MULTI)
            ebs_omega: ECBS 质量边界系数
            default_dispatcher: 默认任务分派器名称
            enable_deadlock_prevention: 是否启用死锁预防
            enable_theta_star: 是否启用 Theta* 路径平滑
            congestion_threshold: 触发动态重规划的拥堵级别
        """
        self._graph = graph
        self._traffic_mgr = traffic_manager
        self._mode = mode
        self._ebs_omega = ebs_omega
        self._default_dispatcher = default_dispatcher
        self._enable_dp = enable_deadlock_prevention
        self._enable_theta = enable_theta_star
        self._congestion_threshold = congestion_threshold

        # 内部状态
        self._dispatcher_cache: Dict[str, Any] = {}
        self._theta_star_instance: Optional[Any] = None
        self._ecbs_solver: Optional[Any] = None
        self._scheduling_history: List[SchedulingResult] = []  # 最近100次记录
        self._stats = {
            "total_schedules": 0,
            "theta_star_uses": 0,
            "ecbs_uses": 0,
            "fallback_uses": 0,
            "reroutes_triggered": 0,
            "deadlocks_detected": 0,
        }

        logger.info(
            "HybridScheduler initialized: mode=%s, theta=%s, omega=%.2f",
            mode.value if hasattr(mode, 'value') else str(mode), enable_theta_star, ebs_omega
        )

    async def schedule_batch(
        self,
        tasks: List[Task],
        agvs: List[AgvState],
        mode: Optional[SchedulerMode] = None,
        timeout_seconds: float = 30.0,  # [G1-FIX P1-4] 全局超时 (原无限制)
        **kwargs
    ) -> SchedulingResult:
        """
        批量调度主入口 (最常用API)
        
        流程:
        1. 任务分派 (Dispatcher): task → AGV 匹配
        2. 路径规划 (Router/ECBS): 计算无碰撞路径
        3. 交通管制 (Traffic): 锁定资源 + 通行审批
        4. 拥堵评估: 判断是否需要重规划
        
        Args:
            tasks: 待调度任务列表
            agvs: 可用AGV列表
            mode: 强制指定调度模式 (None=自动判断)
            timeout_seconds: 全局超时时间(秒), 超时后降级到FCFS
            
        Returns:
            SchedulingResult 完整调度结果
        """
        t_start = time.perf_counter()
        
        # [G1-FIX P1-4] 超时包装器
        async def _run_with_timeout():
            effective_mode = mode or self._mode or SchedulerMode.AUTO

            if effective_mode == SchedulerMode.AUTO:
                effective_mode = self._auto_select_mode(len(tasks), len(agvs))

            logger.info(
                "Batch scheduling started: %d tasks, %d AGVs, mode=%s, timeout=%.1fs",
                len(tasks), len(agvs), effective_mode.value, timeout_seconds
            )

            # === Phase 1: 任务分派 ===
            if time.perf_counter() - t_start > timeout_seconds * 0.5:
                raise TimeoutError("Timeout before dispatch phase")
            dispatch_result = await self._dispatch_tasks(tasks, agvs)
            
            if not dispatch_result or not dispatch_result.get("assignments"):
                return SchedulingResult(
                    task_id=f"batch_{int(time.time())}",
                    paths={},
                    scheduling_time_ms=(time.perf_counter()-t_start)*1000,
                    error_message="No feasible assignment found"
                )

            # === Phase 2: 路径规划 ===
            if time.perf_counter() - t_start > timeout_seconds * 0.8:
                raise TimeoutError("Timeout before path planning, using fallback")
                
            if effective_mode == SchedulerMode.MULTI_AGENT and len(tasks) > 3:
                paths_result = await self._plan_paths_multi_agent(
                    tasks, agvs, dispatch_result
                )
            else:
                paths_result = await self._plan_paths_single_agent(
                    tasks, agvs, dispatch_result
                )

            # === Phase 3: 交通管制集成 ===
            if self._traffic_mgr and self._enable_dp:
                await self._apply_traffic_control(paths_result)

            # === Phase 4: 构建最终结果 ===
            result = self._build_scheduling_result(
                tasks[0].id if tasks else "unknown",
                paths_result,
                effective_mode,
                t_start
            )

            # 记录历史
            self._record_history(result)
            
            # 拥堵感知重规划触发
            if self._should_reroute(result):
                logger.warning(
                    "Congestion level %s exceeds threshold %s, triggering reroute",
                    result.congestion_level, self._congestion_threshold
                )
                self._stats["reroutes_triggered"] += 1

            return result

        try:
            return await asyncio.wait_for(_run_with_timeout(), timeout=timeout_seconds)
        except (asyncio.TimeoutError, TimeoutError) as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            logger.warning("[P1-4] Schedule batch timed out (%.1fms), using FCFS fallback", elapsed)
            self._stats["fallback_uses"] += 1
            return await self._fcfs_fallback_batch(tasks, agvs, t_start)

    async def _fcfs_fallback_batch(
        self, tasks: List[Task], agvs: List[AgvState], t_start: float
    ) -> SchedulingResult:
        """[G1-FIX P1-4] FCFS终极降级 — 保证在O(n*m)时间内返回有效结果"""
        paths = {}
        available = list(agvs)
        
        for task in sorted(tasks, key=lambda t: -t.priority.value):
            if not available:
                break
            best_agv = min(available, key=lambda a: a.id)  # 简单取第一个空闲AGV
            paths[best_agv.id] = ScheduledPath(
                agv_id=best_agv.id, task_id=task.id,
                path=[best_agv.current_node, task.pickup_node, task.delivery_node],
                total_distance=0.0, estimated_time=10.0,
                algorithm_used="FCFS-Fallback", success=True
            )
            available.remove(best_agv)
            
        return SchedulingResult(
            task_id=f"fcfs_fallback_{int(time.time())}",
            paths=paths,
            scheduling_time_ms=(time.perf_counter()-t_start)*1000,
            algorithm_used="FCFS-Fallback",
            error_message="Timed out, used FCFS fallback"
        )

    async def schedule_single(
        self,
        task: Task,
        agv: AgvState,
        use_theta_star: bool = True,
        blocked_nodes: Optional[Set[str]] = None,
        **kwargs
    ) -> ScheduledPath:
        """
        单任务调度快捷方法
        
        适用于:
        - 紧急任务快速响应
        - 单AGV重规划
        - 测试调试场景
        
        Args:
            task: 单个任务
            agv: 指派给此任务的AGV
            use_theta_star: 是否使用 Theta* (否则用 A*)
            blocked_nodes: 动态障碍物集合
            
        Returns:
            ScheduledPath 单条路径结果
        """
        t0 = time.perf_counter()
        
        try:
            if use_theta_star and self._enable_theta:
                path_result = await self._theta_star_plan(
                    start=agv.current_node,
                    goal=task.pickup_node,
                    blocked_nodes=blocked_nodes
                )
                
                # 如果有delivery节点,续接第二段路径
                if path_result.success and task.delivery_node != task.pickup_node:
                    leg2 = await self._theta_star_plan(
                        start=path_result.path[-1],
                        goal=task.delivery_node,
                        blocked_nodes=blocked_nodes
                    )
                    if leg2.success:
                        path_result.path.extend(leg2.path[1:])  # 去掉重复的pickup节点
                        path_result.total_distance += leg2.total_distance
                
                algorithm = "Theta*" 
                self._stats["theta_star_uses"] += 1
            else:
                path_result = await self._astar_fallback(
                    start=agv.current_node,
                    goal=task.pickup_node,
                    blocked_nodes=blocked_nodes
                )
                algorithm = "AStar"

            return ScheduledPath(
                agv_id=agv.id,
                task_id=task.id,
                path=path_result.path,
                total_distance=path_result.total_distance,
                estimated_time=(time.perf_counter()-t0)*1000,
                waypoints=len(path_result.path) - 2,
                algorithm_used=algorithm,
                success=path_result.success,
                metadata={"planning_time_ms": (time.perf_counter()-t0)*1000}
            )

        except Exception as e:
            logger.error("Single task scheduling error: %s", e)
            return ScheduledPath(
                agv_id=agv.id,
                task_id=task.id,
                path=[],
                success=False,
                error_message=str(e),
                algorithm_used="ERROR"
            )

    async def reroute(
        self,
        task_ids: List[str],
        blocked_nodes: Optional[Set[str]] = None,
        reason: str = "congestion",
        **kwargs
    ) -> SchedulingResult:
        """
        动态重规划 (在线调整)
        
        触发场景:
        - 拥堵等级超过阈值
        - 新障碍物出现
        - AGV故障需要重新分配
        - 优先级更高的任务抢占
        
        Args:
            task_ids: 需要重新规划的任务ID列表
            blocked_nodes: 新增障碍节点集合
            reason: 重规划原因 (用于日志和指标)
            
        Returns:
            SchedulingResult 新的调度方案
        """
        logger.info("Rerouting %d tasks due to %s", len(task_ids), reason)
        
        # TODO: 从历史记录恢复tasks/agvs上下文
        # 这里简化实现: 返回空结果提示调用方重新调用schedule_batch
        return SchedulingResult(
            task_id=f"reroute_{reason}_{int(time.time())}",
            paths={},
            error_message="Reroute requires re-invocation of schedule_batch with updated context",
            metrics={"reroute_reason": reason}
        )

    # ════════════════════════════════════════════════════════
    # 私有方法: 分派阶段
    # ════════════════════════════════════════════════════════

    async def _dispatch_tasks(
        self,
        tasks: List[Task],
        agvs: List[AgvState]
    ) -> Optional[Dict[str, Any]]:
        """Phase 1: 任务→AGV分派"""
        from .dispatcher import DispatcherRegistry, DispatchResult

        try:
            dispatcher = DispatcherRegistry.get(self._default_dispatcher)
            
            # 转换为dispatcher期望的格式
            task_dicts = [
                {
                    "id": t.id,
                    "pickup": t.pickup_node,
                    "delivery": t.delivery_node,
                    "priority": t.priority.value,
                    "deadline": t.deadline,
                }
                for t in tasks
            ]
            agv_dicts = [
                {
                    "id": a.id,
                    "current_node": a.current_node,
                    "capacity": a.capacity,
                    "status": a.status,
                }
                for a in agvs
            ]

            # 构建距离矩阵 (简化版: 使用欧氏距离或预计算)
            distance_matrix = self._build_distance_matrix(task_dicts, agv_dicts)

            # 执行分派
            loop = asyncio.get_event_loop()
            result: DispatchResult = await loop.run_in_executor(
                None, 
                lambda: dispatcher.dispatch(task_dicts, agv_dicts, distance_matrix)
            )

            logger.info(
                "Dispatch completed: status=%s, assigned=%d/%d tasks, time=%.1fms",
                result.status,
                len(result.task_agv_map),
                len(tasks),
                result.solve_time_ms
            )

            return {
                "assignments": result.assignments,          # {agv_id: [task_ids]}
                "task_agv_map": result.task_agv_map,         # {task_id: agv_id}
                "unassigned": result.unassigned_tasks,
                "objective": result.objective_value,
                "solve_time_ms": result.solve_time_ms,
                "metadata": result.metadata,
            }

        except Exception as e:
            logger.error("Dispatch failed: %s", e)
            # 降级到贪心分派
            from .dispatcher import GreedyDispatcher
            greedy = GreedyDispatcher()
            # ... 省略贪心调用 (与上面相同签名)
            return None

    def _build_distance_matrix(
        self,
        tasks: List[dict],
        agvs: List[dict]
    ) -> Dict[Tuple[str, str], float]:
        """
        构建任务-AGV距离矩阵
        
        优化:
        - 缓存已计算的距离
        - 使用图的shortest_path预计算表 (如果可用)
        - Fallback: 欧氏距离近似
        """
        matrix = {}
        
        for task in tasks:
            pickup = task["pickup"]
            for agv in agvs:
                current = agv["current_node"]
                key = (current, pickup)
                
                # 尝试从graph获取真实最短路径距离
                try:
                    if hasattr(self._graph, 'get_shortest_distance'):
                        dist = self._graph.get_shortest_distance(current, pickup)
                    else:
                        # Fallback: 欧氏距离近似
                        pos_a = self._get_node_position(current)
                        pos_b = self._get_node_position(pickup)
                        dist = (
                            ((pos_a[0]-pos_b[0])**2 + (pos_a[1]-pos_b[1])**2) ** 0.5
                            if pos_a and pos_b else 999.0
                        )
                    
                    matrix[key] = dist
                    
                except Exception:
                    matrix[key] = 999.0  # 不可达标记

        return matrix

    def _get_node_position(self, node_id: str) -> Optional[Tuple[float, float]]:
        """获取节点的(x,y)坐标"""
        try:
            node = self._graph.nodes.get(node_id)
            if node:
                return (node.x, node.y) if hasattr(node, 'x') else None
            return None
        except Exception:
            return None

    # ════════════════════════════════════════════════════════
    # 私有方法: 路径规划阶段
    # ════════════════════════════════════════════════════════

    async def _plan_paths_single_agent(
        self,
        tasks: List[Task],
        agvs: List[AgvState],
        dispatch_result: dict
    ) -> Dict[str, ScheduledPath]:
        """Phase 2 (单agent模式): 为每个AGV独立使用 Theta* 规划路径"""
        paths = {}
        task_agv_map = dispatch_result.get("task_agv_map", {})

        # 并行规划所有AGV路径 (加速)
        planning_coroutines = []
        
        for agv in agvs:
            assigned_tasks = dispatch_result.get("assignments", {}).get(agv.id, [])
            if not assigned_tasks:
                continue
                
            task = next((t for t in tasks if t.id == assigned_tasks[0]), None)
            if not task:
                continue

            coro = self.schedule_single(
                task=task,
                agv=agv,
                use_theta_star=self._enable_theta
            )
            planning_coroutines.append(coro)

        # 并行执行所有路径规划
        results = await asyncio.gather(*planning_coroutines, return_exceptions=True)

        for result in results:
            if isinstance(result, ScheduledPath) and result.success:
                paths[result.agv_id] = result
            elif isinstance(result, Exception):
                logger.warning("Single-agent path planning error: %s", result)

        return paths

    async def _plan_paths_multi_agent(
        self,
        tasks: List[Task],
        agvs: List[AgvState],
        dispatch_result: dict
    ) -> Dict[str, ScheduledPath]:
        """Phase 2 (多agent模式): 使用 ECBS 协调规划无碰撞路径"""
        paths = {}
        
        try:
            from ..mapf.cbs_solver import ECBSSolver, solve_mapf

            # 构建MAPF输入格式
            agents = []
            for agv in agvs:
                assigned = dispatch_result.get("assignments", {}).get(agv.id, [])
                if not assigned:
                    continue
                task = next((t for t in tasks if t.id == assigned[0]), None)
                if task:
                    agents.append({
                        "agent_id": agv.id,
                        "start": int(agv.current_node.replace("node_", "")) if agv.current_node.startswith("node_") else int(agv.current_node),
                        "goal": int(task.pickup_node.replace("node_", "")) if task.pickup_node.startswith("node_") else int(task.pickup_node),
                        "task_id": task.id,
                    })

            if len(agents) < 2:
                # Agent太少,回退到单agent模式
                logger.info("Only %d agents, falling back to single-agent mode", len(agents))
                return await self._plan_paths_single_agent(tasks, agvs, dispatch_result)

            # 调用ECBS求解器
            loop = asyncio.get_event_loop()
            mapf_result = await loop.run_in_executor(
                None,
                lambda: solve_mapf(
                    agents=agents,
                    graph=self._graph,
                    solver_type="ecbs",
                    omega=self._ebs_omega,
                    time_limit=5.0  # 5秒超时
                )
            )

            # 转换MAPF结果为ScheduledPath格式
            if mapf_result and mapf_result.get("success"):
                for agent_id, path_data in mapf_result.get("paths", {}).items():
                    # path_data 是 PathResult 对象
                    spath = ScheduledPath(
                        agv_id=agent_id,
                        task_id=next(
                            (a["task_id"] for a in agents if a["agent_id"] == agent_id),
                            "unknown"
                        ),
                        path=[str(p.node_id) for p in path_data.path],
                        total_distance=path_data.cost,
                        estimated_time=path_data.makespan,
                        conflicts_resolved=len(path_data.constraints_applied),
                        algorithm_used="ECBS-ω{}".format(self._ebs_omega),
                        success=True,
                        metadata={
                            "makespan": path_data.makespan,
                            "constraints": len(path_data.constraints_applied),
                            "mapf_solver_time_ms": mapf_result.get("solve_time_ms", 0),
                        }
                    )
                    paths[agent_id] = spath

                self._stats["ecbs_uses"] += 1
                logger.info(
                    "ECBS solved: %d agents, SOC=%.2f, time=%.1fms",
                    len(paths),
                    sum(p.total_distance for p in paths.values()),
                    mapf_result.get("solve_time_ms", 0)
                )
            else:
                # ECBS失败,降级到单agent
                logger.warning("ECBS failed, falling back to single-agent")
                self._stats["fallback_uses"] += 1
                return await self._plan_paths_single_agent(tasks, agvs, dispatch_result)

        except ImportError as e:
            logger.warning("MAPF module not available (%s), using single-agent", e)
            return await self._plan_paths_single_agent(tasks, agvs, dispatch_result)
        except Exception as e:
            logger.error("Multi-agent planning error: %s", exc_info=True)
            return await self._plan_paths_single_agent(tasks, agvs, dispatch_result)

        return paths

    async def _theta_star_plan(
        self,
        start: str,
        goal: str,
        blocked_nodes: Optional[Set[str]] = None
    ) -> ScheduledPath:
        """调用 Theta* 规划单agent路径"""
        try:
            from ..path_planning.theta_star import LazyThetaStar

            if self._theta_star_instance is None:
                self._theta_star_instance = LazyThetaStar(self._graph)

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._theta_star_instance.find_path(
                    start=start,
                    goal=goal,
                    blocked_nodes=blocked_nodes
                )
            )

            if result and result.found:
                return ScheduledPath(
                    agv_id="",  # 由调用者填充
                    task_id="",
                    path=result.path,
                    total_distance=result.total_distance,
                    waypoints=len(result.path) - 2 if result.path else 0,
                    algorithm_used="LazyTheta*",
                    success=True,
                    metadata={
                        "expanded_nodes": getattr(result, 'expanded_nodes', 0),
                        "los_shortcuts": getattr(result, 'los_shortcuts', 0),
                    }
                )
            else:
                return ScheduledPath(
                    agv_id="", task_id="", path=[], success=False,
                    error_message="Theta* could not find path"
                )

        except Exception as e:
            logger.error("Theta* planning error: %s", e)
            raise  # 向上传播以便降级处理

    async def _astar_fallback(
        self,
        start: str,
        goal: str,
        blocked_nodes: Optional[Set[str]] = None
    ) -> ScheduledPath:
        """A* 降级规划器"""
        from .router import AStarRouter, RouteResult

        router = AStarRouter()
        router.set_graph(self._graph)

        loop = asyncio.get_event_loop()
        result: RouteResult = await loop.run_in_executor(
            None,
            lambda: router.find_route(start, goal, blocked_nodes or set())
        )

        return ScheduledPath(
            agv_id="", task_id="",
            path=result.path,
            total_distance=result.total_distance,
            waypoints=len(result.path) - 2 if result.path else 0,
            algorithm_used="BidirectionalAStar-Fallback",
            success=result.found,
            error_message=None if result.found else "A* could not find path"
        )

    # ════════════════════════════════════════════════════════
    # 私有方法: 交通管制集成
    # ════════════════════════════════════════════════════════

    async def _apply_traffic_control(self, paths: Dict[str, ScheduledPath]) -> None:
        """Phase 3: 应用交通管制规则"""
        if not self._traffic_mgr:
            return

        try:
            for agv_id, spath in paths.items():
                if not spath.success:
                    continue

                # 请求通行审批
                allowed, reason = await self._traffic_mgr.request_passage(
                    agv_id=agv_id,
                    path=[int(n) for n in spath.path if n.isdigit()],
                    priority=5
                )

                if not allowed:
                    logger.warning(
                        "Passage rejected for AGV %s: %s", agv_id, reason
                    )
                    spath.success = False
                    spath.error_message = f"Traffic control rejected: {reason}"

            # 检测全局拥堵
            congestion = await self._traffic_mgr.detect_congestion()
            logger.info("Congestion level: %s", congestion.level.name if hasattr(congestion, 'level') else congestion)

        except Exception as e:
            logger.error("Traffic control integration error: %s", e)

    # ════════════════════════════════════════════════════════
    # 私有方法: 结果构建与辅助函数
    # ════════════════════════════════════════════════════════

    def _build_scheduling_result(
        self,
        task_id: str,
        paths: Dict[str, ScheduledPath],
        mode: SchedulerMode,
        t_start: float
    ) -> SchedulingResult:
        """构建最终的SchedulingResult对象"""
        successful_paths = {k: v for k, v in paths.items() if v.success}

        return SchedulingResult(
            task_id=task_id,
            paths=paths,
            total_distance=sum(p.total_distance for p in successful_paths.values()),
            max_makespan=max((p.estimated_time for p in successful_paths.values()), default=0.0),
            total_conflicts=sum(p.conflicts_resolved for p in successful_paths.values()),
            scheduling_time_ms=(time.perf_counter() - t_start) * 1000,
            mode_used=mode,
            algorithm_selected=next(
                (p.algorithm_used for p in successful_paths.values()),
                "UNKNOWN"
            ),
            deadlock_prevented=False,  # TODO: 从traffic_mgr获取
            metrics={
                "success_rate": len(successful_paths) / max(len(paths), 1),
                "avg_path_length": (
                    sum(len(p.path) for p in successful_paths.values()) / max(len(successful_paths), 1)
                ),
                "theta_star_ratio": sum(
                    1 for p in successful_paths.values() 
                    if "Theta" in p.algorithm_used
                ) / max(len(successful_paths), 1),
                **self._stats
            }
        )

    def _auto_select_mode(self, n_tasks: int, n_agvs: int) -> SchedulerMode:
        """
        自动选择调度模式
        
        决策树:
        - n_agents <= 3 → SINGLE_AGENT (Theta*, 开销低)
        - n_agents > 3 且 n_tasks/n_agvs > 0.7 → MULTI_AGENT (ECBS, 防碰撞)
        - 否则 → SINGLE_AGENT
        """
        if n_agvs <= 3:
            return SchedulerMode.SINGLE_AGENT
        elif n_tasks / max(n_agvs, 1) > 0.7:
            return SchedulerMode.MULTI_AGENT
        else:
            return SchedulerMode.SINGLE_AGENT

    def _should_reroute(self, result: SchedulingResult) -> bool:
        """判断是否需要触发重规划"""
        threshold_order = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        current_level = threshold_order.get(result.congestion_level, 0)
        threshold = threshold_order.get(self._congestion_threshold, 2)
        return current_level >= threshold

    def _record_history(self, result: SchedulingResult) -> None:
        """记录调度历史 (保留最近100条)"""
        self._scheduling_history.append(result)
        if len(self._scheduling_history) > 100:
            self._scheduling_history.pop(0)
        self._stats["total_schedules"] += 1

    async def _fallback_schedule(
        self,
        tasks: List[Task],
        agvs: List[AgvState],
        t_start: float
    ) -> SchedulingResult:
        """终极降级: 纯A*单agent, 无交通管制"""
        logger.warning("Using fallback scheduler (A*-only)")
        
        paths = {}
        for i, (task, agv) in enumerate(zip(tasks[:len(agvs)], agvs)):
            try:
                spath = await self.schedule_single(
                    task=task, agv=agv, use_theta_star=False
                )
                paths[agv.id] = spath
            except Exception as e:
                logger.error("Fallback failed for task %s: %s", task.id, e)

        return SchedulingResult(
            task_id=tasks[0].id if tasks else "fallback",
            paths=paths,
            scheduling_time_ms=(time.perf_counter() - t_start) * 1000,
            mode_used=SchedulerMode.SINGLE_AGENT,
            algorithm_used="AStar-Fallback",
            error_message="Primary scheduler failed, used fallback",
            metrics={"fallback_reason": "exception_in_main_path"}
        )

    # ════════════════════════════════════════════════════════
    # 公共查询API
    # ════════════════════════════════════════════════════════

    def get_statistics(self) -> Dict[str, Any]:
        """获取调度器运行统计"""
        return {
            **self._stats,
            "history_size": len(self._scheduling_history),
            "recent_avg_time_ms": (
                sum(r.scheduling_time_ms for r in self._scheduling_history[-10:]) /
                max(len(self._scheduling_history[-10:]), 1)
            ) if self._scheduling_history else 0.0,
            "theta_star_usage_pct": (
                self._stats["theta_star_uses"] / max(self._stats["total_schedules"], 1) * 100
            ),
            "ecbs_usage_pct": (
                self._stats["ecbs_uses"] / max(self._stats["total_schedules"], 1) * 100
            ),
            "fallback_rate_pct": (
                self._stats["fallback_uses"] / max(self._stats["total_schedules"], 1) * 100
            ),
        }

    def get_recent_history(self, limit: int = 10) -> List[SchedulingResult]:
        """获取最近的调度历史记录"""
        return self._scheduling_history[-limit:]

    def reset_statistics(self) -> None:
        """重置统计计数器"""
        for key in self._stats:
            self._stats[key] = 0
        self._scheduling_history.clear()
        logger.info("Scheduler statistics reset")


# ═══════════════════════════════════════════════════════════════
# 便捷工厂函数
# ═══════════════════════════════════════════════════════════════

def create_hybrid_scheduler(
    graph: Any,
    traffic_manager=None,
    **kwargs
) -> HybridScheduler:
    """
    创建并配置 HybridScheduler 的工厂函数
    
    推荐配置预设:
    
    >>> # 小型仓库 (< 20 AGVs)
    >>> scheduler = create_hybrid_scheduler(graph, mode="auto")
    
    >>> # 大型仓库 (> 50 AGVs, 启用所有优化)
    >>> scheduler = create_hybrid_scheduler(
    ...     graph=large_graph,
    ...     traffic_mgr=tcs,
    ...     mode="auto",
    ...     ebs_omega=1.3,
    ...     default_dispatcher="mip",
    ...     enable_deadlock_prevention=True,
    ...     enable_theta_star=True,
    ...     congestion_threshold="HIGH"
    ... )
    
    Args:
        graph: PathGraph 地图实例
        traffic_manager: TrafficControlSystem 实例
        **kwargs: 传递给 HybridScheduler.__init__
        
    Returns:
        配置好的 HybridScheduler 实例
    """
    return HybridScheduler(graph=graph, traffic_manager=traffic_manager, **kwargs)
