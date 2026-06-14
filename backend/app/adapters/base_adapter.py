"""
统一车辆适配器接口 — 参考 openTCS CommAdapter 设计.

openTCS CommAdapter 特性:
  - 统一接口对接不同品牌 AGV
  - SPI 自动发现, 热插拔
  - connect / disconnect / sendCommand 生命周期

AGV-TMS 适配 (Python):
  - BaseVehicleAdapter ABC 定义统一接口
  - 适配器注册表 (AdapterRegistry) 管理多个实例
  - 支持多品牌混合调度 (按 AGV ID 路由到对应适配器)

实现:
  - OpcUaVehicleAdapter (继承 OpcUaAdapter)
  - Vda5050VehicleAdapter (封装 VDA5050 路由逻辑)
  - MqttVehicleAdapter (预留, Phase 2)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== 统一数据模型 ====================

class VehicleCommand(str, Enum):
    """
    统一车辆控制指令 (合并 OpcUa AgvCommand + VDA5050 指令).

    参考 openTCS MovementCommand + VDA5050 instantActions。
    """
    MOVE = "move"                # 移动到目标点
    STOP = "stop"                # 紧急停止
    RESUME = "resume"            # 恢复运行
    CHARGE = "charge"            # 去充电
    LOAD = "load"                # 载货
    UNLOAD = "unload"            # 卸货
    CANCEL_TASK = "cancel_task"  # 取消当前任务
    INIT_POSITION = "initPosition"  # VDA5050: 初始化位置
    PICKUP = "pickup"            # VDA5050: 拣货
    DROPOFF = "dropoff"          # VDA5050: 卸货


class VehicleState(str, Enum):
    """
    统一车辆运行状态 (合并 OpcUa AgvState + VDA5050 AGVState).

    参考 openTCS Vehicle.State + VDA5050 agvState。
    """
    IDLE = "idle"
    MOVING = "moving"
    LOADING = "loading"
    UNLOADING = "unloading"
    CHARGING = "charging"
    EXECUTING = "executing"       # 执行任务中
    ERROR = "error"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


@dataclass
class VehicleStatus:
    """统一车辆状态快照"""
    vehicle_id: str
    state: VehicleState = VehicleState.IDLE
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    speed: float = 0.0
    battery_level: float = 100.0
    current_node: str = ""
    target_node: str = ""
    load_status: bool = False
    error_code: int = 0
    error_message: str = ""
    last_heartbeat: float = 0.0
    odometer: float = 0.0
    operating_hours: float = 0.0
    # VDA5050 扩展字段
    order_id: str = ""            # 当前执行的 VDA5050 order ID
    last_node_id: str = ""        # VDA5050 lastNodeId
    sequence_id: int = 0          # VDA5050 sequenceId

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "state": self.state.value,
            "x": self.x, "y": self.y, "angle": self.angle,
            "speed": self.speed,
            "battery_level": self.battery_level,
            "current_node": self.current_node,
            "target_node": self.target_node,
            "load_status": self.load_status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "order_id": self.order_id,
            "last_node_id": self.last_node_id,
            "sequence_id": self.sequence_id,
        }


@dataclass
class CommandResult:
    """统一指令执行结果"""
    success: bool
    vehicle_id: str
    command: str
    message: str = ""
    timestamp: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TransportOrderMessage:
    """
    运输订单消息 (参考 VDA5050 order 结构).

    用于 send_transport_order 方法, 下发完整路径序列。
    """
    order_id: str
    edges: List[Dict[str, Any]] = field(default_factory=list)   # VDA5050 edge 列表
    nodes: List[Dict[str, Any]] = field(default_factory=list)   # VDA5050 node 列表
    actions: List[Dict[str, Any]] = field(default_factory=list) # 节点动作


# ==================== 抽象基类 ====================

class BaseVehicleAdapter(ABC):
    """
    统一车辆适配器抽象基类 — 参考 openTCS CommAdapter.

    子类必须实现:
      - connect / disconnect
      - send_command
      - get_status / get_all_statuses
      - send_transport_order (VDA5050 风格)

    可选覆盖:
      - emergency_stop
      - health_check
    """

    def __init__(self, name: str, protocol: str = "custom"):
        self.name = name
        self.protocol = protocol
        self._connected = False
        self._vehicle_count = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def vehicle_count(self) -> int:
        return self._vehicle_count

    @abstractmethod
    async def connect(self) -> bool:
        """连接到设备/协议端点"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """下发控制指令"""
        ...

    @abstractmethod
    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """获取单个车辆状态"""
        ...

    @abstractmethod
    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有车辆状态"""
        ...

    async def send_transport_order(
        self,
        vehicle_id: str,
        order: TransportOrderMessage,
    ) -> CommandResult:
        """
        下发运输订单 (VDA5050 风格的完整路径序列).

        默认实现: 逐节点发送 MOVE 指令。
        子类可覆盖为真正的 VDA5050 order 下发。
        """
        for node in order.nodes:
            target = node.get("nodeId", "")
            if target:
                result = await self.send_command(
                    vehicle_id, VehicleCommand.MOVE, {"target": target}
                )
                if not result.success:
                    return result
        return CommandResult(
            success=True,
            vehicle_id=vehicle_id,
            command="transport_order",
            message=f"Order {order.order_id} with {len(order.nodes)} nodes sent",
        )

    async def emergency_stop(self, vehicle_id: Optional[str] = None) -> Dict[str, CommandResult]:
        """紧急停止 — 停止指定车辆或全部"""
        results: Dict[str, CommandResult] = {}
        targets = [vehicle_id] if vehicle_id else [
            s.vehicle_id for s in await self.get_all_statuses()
        ]
        for vid in targets:
            results[vid] = await self.send_command(vid, VehicleCommand.STOP)
        return results

    async def health_check(self) -> bool:
        """健康检查 (默认: 检查连接状态)"""
        return self._connected
