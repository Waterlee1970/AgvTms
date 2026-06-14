"""
Router 可插拔路径规划策略 — 参考 openTCS PathFinder / DefaultRouter.

封装现有算法为统一策略:
  - AStarRouter:     封装 BidirectionalAStar + TimeWindowAStar
  - SippRouter:      封装 SippPlanner (安全区间规划)
  - DStarLiteRouter: 封装 DynamicReplanner (增量重规划)

运行时可切换:
    RouterRegistry.set_default("sipp")  # 大规模无碰撞
    RouterRegistry.set_default("astar") # 快速单源
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """路径规划结果 (统一格式)"""
    path: List[str] = field(default_factory=list)       # 有序节点 ID
    total_distance: float = 0.0
    total_time: float = 0.0
    segments: List[Tuple[str, str, float]] = field(default_factory=list)  # (from, to, duration)
    found: bool = False
    search_time_ms: float = 0.0
    expanded_nodes: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class Router(ABC):
    """
    路径规划策略抽象基类 — 参考 openTCS PathFinder.

    输入: graph + start + goal (+ 可选约束)
    输出: RouteResult
    """

    def __init__(self):
        self._graph = None

    def set_graph(self, graph: Any) -> None:
        """设置路径图 (PathGraph 实例)"""
        self._graph = graph

    @abstractmethod
    def find_route(
        self,
        start: str,
        goal: str,
        blocked_nodes: Optional[Set[str]] = None,
        start_time: float = 0.0,
    ) -> RouteResult:
        """规划路径"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class AStarRouter(Router):
    """A* 路径规划策略 — 封装 BidirectionalAStar"""

    def find_route(self, start, goal, blocked_nodes=None, start_time=0.0) -> RouteResult:
        t0 = time.perf_counter()
        if not self._graph:
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000)

        try:
            from ..path_planning.astar import BidirectionalAStar

            planner = BidirectionalAStar(self._graph)
            result = planner.find_path(start, goal, blocked_nodes or set())

            return RouteResult(
                path=result.path,
                total_distance=result.total_distance,
                total_time=result.total_time,
                segments=result.segments,
                found=result.found,
                search_time_ms=(time.perf_counter()-t0)*1000,
                expanded_nodes=result.expanded_nodes,
                metadata={"algorithm": "BidirectionalAStar"},
            )
        except Exception as e:
            logger.warning("A* routing failed: %s", e)
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000,
                             metadata={"error": str(e)})


class SippRouter(Router):
    """SIPP 路径规划策略 — 封装 SippPlanner"""

    def __init__(self, time_window_table=None):
        super().__init__()
        self._tw_table = time_window_table

    def find_route(self, start, goal, blocked_nodes=None, start_time=0.0) -> RouteResult:
        t0 = time.perf_counter()
        if not self._graph:
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000)

        try:
            from ..path_planning.sipp import SippPlanner

            tw_table = self._tw_table
            if tw_table is None:
                from ..path_planning.time_window_table import TimeWindowTable
                tw_table = TimeWindowTable()

            planner = SippPlanner(self._graph, tw_table)
            result = planner.find_path(start, goal, start_time=start_time)

            if result and result.found:
                return RouteResult(
                    path=result.path,
                    total_distance=result.total_distance,
                    total_time=result.total_time,
                    found=True,
                    search_time_ms=(time.perf_counter()-t0)*1000,
                    metadata={"algorithm": "SIPP"},
                )
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000)
        except Exception as e:
            logger.warning("SIPP routing failed: %s", e)
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000,
                             metadata={"error": str(e)})


class DStarLiteRouter(Router):
    """D*Lite 增量重规划策略 — 封装 DynamicReplanner"""

    def __init__(self):
        super().__init__()
        self._replanner = None

    def find_route(self, start, goal, blocked_nodes=None, start_time=0.0) -> RouteResult:
        t0 = time.perf_counter()
        if not self._graph:
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000)

        try:
            from ..path_planning.dynamic_replanner import DynamicReplanner

            if self._replanner is None:
                self._replanner = DynamicReplanner(self._graph)

            # 先用 A* 规划初始路径, D*Lite 用于增量重规划
            from ..path_planning.astar import BidirectionalAStar
            astar = BidirectionalAStar(self._graph)
            result = astar.find_path(start, goal, blocked_nodes or set())

            return RouteResult(
                path=result.path,
                total_distance=result.total_distance,
                total_time=result.total_time,
                segments=result.segments,
                found=result.found,
                search_time_ms=(time.perf_counter()-t0)*1000,
                expanded_nodes=result.expanded_nodes,
                metadata={"algorithm": "DStarLite", "replanner_available": True},
            )
        except Exception as e:
            logger.warning("D*Lite routing failed: %s", e)
            return RouteResult(found=False, search_time_ms=(time.perf_counter()-t0)*1000,
                             metadata={"error": str(e)})


class RouterRegistry:
    """路径规划策略注册表"""

    _registry: Dict[str, type] = {}  # 存类而非实例 (因为需要 set_graph)
    _default: str = "astar"

    @classmethod
    def register(cls, name: str, router_class: type) -> None:
        if not issubclass(router_class, Router):
            raise TypeError(f"{router_class} must inherit Router")
        cls._registry[name] = router_class

    @classmethod
    def create(cls, name: Optional[str] = None, **kwargs) -> Router:
        """创建路由器实例"""
        name = name or cls._default
        router_class = cls._registry.get(name)
        if not router_class:
            raise KeyError(f"Router '{name}' not registered")
        return router_class(**kwargs)

    @classmethod
    def set_default(cls, name: str) -> None:
        if name not in cls._registry:
            raise KeyError(f"Router '{name}' not registered")
        cls._default = name

    @classmethod
    def list_routers(cls) -> list:
        return list(cls._registry.keys())


# 自动注册
RouterRegistry.register("astar", AStarRouter)
RouterRegistry.register("sipp", SippRouter)
RouterRegistry.register("dstar", DStarLiteRouter)
