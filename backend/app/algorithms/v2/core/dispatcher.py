"""
Dispatcher 可插拔分派策略 — 参考 openTCS Dispatcher 接口.

封装现有算法为统一策略:
  - MipDispatcher:      封装 MipTaskAssigner (CP-SAT/OR-Tools)
  - HungarianDispatcher: 封装 HungarianAssigner (O(n³) 二分匹配)
  - GreedyDispatcher:   贪心就近分配

运行时可切换:
    DispatcherRegistry.set_default("hungarian")  # 小规模快速分配
    DispatcherRegistry.set_default("mip")        # 大规模最优分配
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """分派结果 (统一格式)"""
    assignments: Dict[str, List[str]] = field(default_factory=dict)  # agv_id → [task_ids]
    task_agv_map: Dict[str, str] = field(default_factory=dict)       # task_id → agv_id
    objective_value: float = 0.0
    solve_time_ms: float = 0.0
    status: str = "UNKNOWN"
    unassigned_tasks: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class Dispatcher(ABC):
    """
    分派策略抽象基类 — 参考 openTCS Dispatcher.

    输入: tasks + agvs + distance_matrix
    输出: DispatchResult (任务到 AGV 的分配方案)
    """

    @abstractmethod
    def dispatch(
        self,
        tasks: List[dict],
        agvs: List[dict],
        distance_matrix: Dict[Tuple[str, str], float],
    ) -> DispatchResult:
        """执行任务分派"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class MipDispatcher(Dispatcher):
    """MIP 分派策略 — 封装 MipTaskAssigner"""

    def __init__(self, objective_mode: str = "balanced", time_limit: float = 5.0):
        self.objective_mode = objective_mode
        self.time_limit = time_limit

    def dispatch(self, tasks, agvs, distance_matrix) -> DispatchResult:
        t0 = time.perf_counter()
        try:
            from ..task_assignment.mip_solver import MipTaskAssigner, ObjectiveMode

            mode = ObjectiveMode(self.objective_mode) if isinstance(self.objective_mode, str) else self.objective_mode
            assigner = MipTaskAssigner(objective_mode=mode, time_limit_seconds=self.time_limit)
            result = assigner.assign(tasks, agvs, distance_matrix)

            task_agv_map = {}
            for agv_id, task_ids in result.assignments.items():
                for tid in task_ids:
                    task_agv_map[tid] = agv_id

            return DispatchResult(
                assignments=result.assignments,
                task_agv_map=task_agv_map,
                objective_value=result.objective_value,
                solve_time_ms=(time.perf_counter() - t0) * 1000,
                status=result.status,
                unassigned_tasks=result.unassigned_tasks,
                metadata={"solver": "CP-SAT", "makespan": result.makespan},
            )
        except Exception as e:
            logger.warning("MIP dispatch failed (%s), falling back to greedy", e)
            return GreedyDispatcher().dispatch(tasks, agvs, distance_matrix)


class HungarianDispatcher(Dispatcher):
    """匈牙利算法分派策略 — 封装 HungarianAssigner"""

    def dispatch(self, tasks, agvs, distance_matrix) -> DispatchResult:
        t0 = time.perf_counter()
        try:
            from ..task_assignment.hungarian import HungarianAssigner

            # 构建代价矩阵: cost_matrix[i][j] = 任务 i 分配给 AGV j 的代价
            task_ids = [t.get("id", f"T{i}") for i, t in enumerate(tasks)]
            agv_ids = [a.get("id", f"A{j}") for j, a in enumerate(agvs)]

            cost_matrix = []
            for task in tasks:
                row = []
                pickup = task.get("pickup", "")
                for agv in agvs:
                    current = agv.get("current_node", "")
                    cost = distance_matrix.get((current, pickup), 999.0)
                    row.append(cost)
                cost_matrix.append(row)

            assigner = HungarianAssigner(maximize=False)
            result = assigner.assign(cost_matrix, task_ids, agv_ids)

            assignments: Dict[str, List[str]] = {aid: [] for aid in agv_ids}
            task_agv_map = {}
            for tid, aid in result.assignments.items():
                assignments.setdefault(aid, []).append(tid)
                task_agv_map[tid] = aid

            return DispatchResult(
                assignments=assignments,
                task_agv_map=task_agv_map,
                objective_value=result.total_cost,
                solve_time_ms=(time.perf_counter() - t0) * 1000,
                status=result.status,
                metadata={"solver": "Hungarian"},
            )
        except Exception as e:
            logger.warning("Hungarian dispatch failed (%s), falling back to greedy", e)
            return GreedyDispatcher().dispatch(tasks, agvs, distance_matrix)


class GreedyDispatcher(Dispatcher):
    """贪心就近分派策略 — 无依赖, 最简单"""

    def dispatch(self, tasks, agvs, distance_matrix) -> DispatchResult:
        t0 = time.perf_counter()
        assignments: Dict[str, List[str]] = {a.get("id", f"A{i}"): [] for i, a in enumerate(agvs)}
        task_agv_map: Dict[str, str] = {}
        unassigned: List[str] = []

        # 按 AGV 容量限制
        agv_loads = {a.get("id", f"A{i}"): 0 for i, a in enumerate(agvs)}
        agv_capacities = {a.get("id", f"A{i}"): a.get("capacity", 1) for i, a in enumerate(agvs)}

        for task in sorted(tasks, key=lambda t: -t.get("priority", 1)):
            tid = task.get("id", "")
            pickup = task.get("pickup", "")
            best_agv = None
            best_cost = float("inf")
            for agv in agvs:
                aid = agv.get("id", "")
                if agv_loads[aid] >= agv_capacities[aid]:
                    continue
                current = agv.get("current_node", "")
                cost = distance_matrix.get((current, pickup), 999.0)
                if cost < best_cost:
                    best_cost = cost
                    best_agv = aid
            if best_agv:
                assignments[best_agv].append(tid)
                task_agv_map[tid] = best_agv
                agv_loads[best_agv] += 1
            else:
                unassigned.append(tid)

        return DispatchResult(
            assignments=assignments,
            task_agv_map=task_agv_map,
            objective_value=0.0,
            solve_time_ms=(time.perf_counter() - t0) * 1000,
            status="HEURISTIC",
            unassigned_tasks=unassigned,
            metadata={"solver": "Greedy"},
        )


class DispatcherRegistry:
    """分派策略注册表"""

    _registry: Dict[str, Dispatcher] = {}
    _default: str = "greedy"

    @classmethod
    def register(cls, name: str, dispatcher: Dispatcher) -> None:
        cls._registry[name] = dispatcher

    @classmethod
    def get(cls, name: Optional[str] = None) -> Dispatcher:
        name = name or cls._default
        return cls._registry.get(name, GreedyDispatcher())

    @classmethod
    def set_default(cls, name: str) -> None:
        if name not in cls._registry:
            raise KeyError(f"Dispatcher '{name}' not registered")
        cls._default = name

    @classmethod
    def list_dispatchers(cls) -> list:
        return list(cls._registry.keys())


# 自动注册
DispatcherRegistry.register("greedy", GreedyDispatcher())
DispatcherRegistry.register("hungarian", HungarianDispatcher())
DispatcherRegistry.register("mip", MipDispatcher())
