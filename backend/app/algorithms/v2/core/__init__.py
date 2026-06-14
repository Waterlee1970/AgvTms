"""
可插拔策略接口层 — 参考 openTCS 策略模式设计.

openTCS 通过 CostFunction / Dispatcher / Router 三个独立接口实现策略可插拔:
  - CostFunction: 边权重计算 (距离/时间/能耗/负载)
  - Dispatcher:   任务分派策略 (就近/贪心/MIP/匈牙利)
  - Router:       路径规划策略 (Dijkstra/A*/SIPP/D*Lite)

AGV-TMS 将现有算法封装为策略实现, 支持运行时切换。
"""

from .cost_function import (
    CostFunction,
    DistanceCost,
    WeightedCost,
    EnergyAwareCost,
    CostFunctionRegistry,
)
from .dispatcher import (
    Dispatcher,
    DispatchResult,
    MipDispatcher,
    HungarianDispatcher,
    GreedyDispatcher,
    DispatcherRegistry,
)
from .router import (
    Router,
    RouteResult,
    AStarRouter,
    SippRouter,
    DStarLiteRouter,
    RouterRegistry,
)

__all__ = [
    # CostFunction
    "CostFunction", "DistanceCost", "WeightedCost", "EnergyAwareCost",
    "CostFunctionRegistry",
    # Dispatcher
    "Dispatcher", "DispatchResult",
    "MipDispatcher", "HungarianDispatcher", "GreedyDispatcher",
    "DispatcherRegistry",
    # Router
    "Router", "RouteResult",
    "AStarRouter", "SippRouter", "DStarLiteRouter",
    "RouterRegistry",
]
