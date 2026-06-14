"""
CostFunction 可插拔成本函数 — 参考 openTCS CostFunction 接口.

openTCS: C(e) = Σ wᵢ · fᵢ(e), 线性加权综合法
AGV-TMS: 同样设计, 支持距离/时间/能耗/负载均衡多维度

用法:
    # 使用默认距离成本
    cost_fn = DistanceCost()

    # 使用加权多目标
    cost_fn = WeightedCost(weights={"distance": 0.4, "time": 0.3, "energy": 0.3})

    # 注册并切换
    CostFunctionRegistry.register("energy", EnergyAwareCost())
    CostFunctionRegistry.set_default("energy")
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EdgeInfo:
    """边信息 (CostFunction 输入)"""
    edge_id: str
    from_node: str
    to_node: str
    distance: float = 0.0       # 米
    travel_time: float = 0.0    # 秒
    speed_limit: float = 1.5    # m/s
    is_conveyor: bool = False
    congestion_factor: float = 1.0
    max_vehicles: int = 1       # 最大并发通行量
    properties: Dict[str, Any] = None


@dataclass
class VehicleInfo:
    """车辆信息 (CostFunction 输入)"""
    vehicle_id: str
    battery_level: float = 100.0
    speed: float = 1.5
    load_status: bool = False
    capacity: int = 1
    vehicle_type: str = "standard"


@dataclass
class OrderInfo:
    """订单信息 (CostFunction 输入)"""
    order_id: str
    priority: int = 1
    deadline: Optional[float] = None
    cargo_type: str = "standard"


class CostFunction(ABC):
    """
    成本函数抽象基类 — 参考 openTCS CostFunction.

    子类实现 calculate() 方法, 返回边的通行成本。
    """

    @abstractmethod
    def calculate(
        self,
        edge: EdgeInfo,
        vehicle: Optional[VehicleInfo] = None,
        order: Optional[OrderInfo] = None,
    ) -> float:
        """计算边的通行成本"""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class DistanceCost(CostFunction):
    """纯距离成本 (最简单)"""

    def calculate(self, edge, vehicle=None, order=None) -> float:
        return edge.distance


class TimeCost(CostFunction):
    """纯时间成本"""

    def calculate(self, edge, vehicle=None, order=None) -> float:
        return edge.travel_time


class WeightedCost(CostFunction):
    """
    多目标加权成本 — 参考 openTCS 线性加权综合法.

    C(e) = w₁·distance + w₂·time + w₃·energy + w₄·congestion
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or {
            "distance": 0.4,
            "time": 0.3,
            "energy": 0.2,
            "congestion": 0.1,
        }

    def calculate(self, edge, vehicle=None, order=None) -> float:
        distance_cost = edge.distance
        time_cost = edge.travel_time
        # 简化能耗: 距离 × 拥塞因子
        energy_cost = edge.distance * edge.congestion_factor
        # 拥塞成本
        congestion_cost = edge.congestion_factor - 1.0

        total = (
            self.weights.get("distance", 0) * distance_cost
            + self.weights.get("time", 0) * time_cost
            + self.weights.get("energy", 0) * energy_cost
            + self.weights.get("congestion", 0) * max(0, congestion_cost)
        )
        return total


class EnergyAwareCost(CostFunction):
    """能耗感知成本 — 优先选择低能耗路径"""

    def calculate(self, edge, vehicle=None, order=None) -> float:
        base = edge.distance
        # 载货时能耗更高
        load_factor = 1.5 if (vehicle and vehicle.load_status) else 1.0
        # 拥塞增加能耗
        congestion_penalty = edge.congestion_factor
        return base * load_factor * congestion_penalty


class PriorityCost(CostFunction):
    """优先级感知成本 — 高优先级任务路径成本降低"""

    def calculate(self, edge, vehicle=None, order=None) -> float:
        base = edge.distance * (1 + max(0, edge.congestion_factor - 1))
        if order and order.priority > 5:
            # 高优先级: 降低成本 (优先选择)
            base *= 0.7
        return base


class CostFunctionRegistry:
    """成本函数注册表 (运行时可切换)"""

    _registry: Dict[str, CostFunction] = {}
    _default: str = "distance"

    @classmethod
    def register(cls, name: str, cost_fn: CostFunction) -> None:
        cls._registry[name] = cost_fn
        logger.debug("Registered cost function: %s", name)

    @classmethod
    def get(cls, name: Optional[str] = None) -> CostFunction:
        name = name or cls._default
        return cls._registry.get(name, DistanceCost())

    @classmethod
    def set_default(cls, name: str) -> None:
        if name not in cls._registry:
            raise KeyError(f"Cost function '{name}' not registered")
        cls._default = name

    @classmethod
    def list_functions(cls) -> list:
        return list(cls._registry.keys())


# 自动注册内置成本函数
CostFunctionRegistry.register("distance", DistanceCost())
CostFunctionRegistry.register("time", TimeCost())
CostFunctionRegistry.register("weighted", WeightedCost())
CostFunctionRegistry.register("energy", EnergyAwareCost())
CostFunctionRegistry.register("priority", PriorityCost())
