"""
Phase 7: VDA5050 完整实现 — order/edge/node/instantAction JSON Schema.

VDA5050 v2.0 完整消息体:
  - Order: order_id + nodes[] + edges[] + actions[]
  - Edge: edgeId + startNodeId + endNodeId + maxSpeed + actions[]
  - Node: nodeId + sequenceId + actions[] + released
  - InstantAction: stop / cancelOrder / start
  - State: 14 个标准字段 (agvPosition, batteryState, etc.)

参考: VDA5050 v2.0 规范文档
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== VDA5050 完整数据模型 ====================

class Vda5050ActionType(str, Enum):
    """VDA5050 标准动作类型"""
    PICK_POSITION = "pickPosition"
    DROP_POSITION = "dropPosition"
    INIT_POSITION = "initPosition"
    CHARGE = "charge"
    WAIT = "wait"
    CUSTOM = "customAction"


class Vda5050InstantActionType(str, Enum):
    """VDA5050 即时动作类型"""
    STOP = "stop"
    CANCEL_ORDER = "cancelOrder"
    START = "start"


class Vda5050BlockingType(str, Enum):
    """动作阻塞类型"""
    NONE = "NONE"
    SOFT = "SOFT"
    HARD = "HARD"


@dataclass
class Vda5050ActionParameter:
    """动作参数"""
    key: str
    value: str

    def to_dict(self) -> Dict:
        return {"key": self.key, "value": self.value}


@dataclass
class Vda5050Action:
    """VDA5050 动作定义"""
    action_type: str
    action_id: str = ""
    blocking_type: str = "HARD"
    action_description: str = ""
    parameters: List[Vda5050ActionParameter] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "actionType": self.action_type,
            "actionId": self.action_id,
            "blockingType": self.blocking_type,
            "actionDescription": self.action_description,
            "actionParameters": [p.to_dict() for p in self.parameters],
        }


@dataclass
class Vda5050Node:
    """
    VDA5050 Node — 路径节点.

    标准字段:
      - nodeId: 节点唯一标识
      - sequenceId: 序列号 (单调递增)
      - released: 是否已发布
      - actions: 节点动作列表
    """
    node_id: str
    sequence_id: int
    released: bool = True
    actions: List[Vda5050Action] = field(default_factory=list)
    # 位置信息 (可选)
    x: Optional[float] = None
    y: Optional[float] = None
    theta: Optional[float] = None
    map_id: str = ""
    allowed_speed: Optional[float] = None

    def to_dict(self) -> Dict:
        result = {
            "nodeId": self.node_id,
            "sequenceId": self.sequence_id,
            "released": self.released,
            "actions": [a.to_dict() for a in self.actions],
        }
        if self.x is not None:
            result["x"] = self.x
        if self.y is not None:
            result["y"] = self.y
        if self.theta is not None:
            result["theta"] = self.theta
        if self.map_id:
            result["mapId"] = self.map_id
        return result


@dataclass
class Vda5050Edge:
    """
    VDA5050 Edge — 路径边.

    标准字段:
      - edgeId: 边唯一标识
      - startNodeId: 起点
      - endNodeId: 终点
      - sequenceId: 序列号
      - maxSpeed: 最大速度 (m/s)
      - actions: 边动作列表
    """
    edge_id: str
    start_node_id: str
    end_node_id: str
    sequence_id: int
    max_speed: float = 1.5
    released: bool = True
    actions: List[Vda5050Action] = field(default_factory=list)
    trajectory: Optional[Dict] = None  # 贝塞尔曲线轨迹
    rotation_allowed: bool = True
    direction: str = "FORWARD"  # FORWARD / BACKWARD / UNDEFINED

    def to_dict(self) -> Dict:
        result = {
            "edgeId": self.edge_id,
            "startNodeId": self.start_node_id,
            "endNodeId": self.end_node_id,
            "sequenceId": self.sequence_id,
            "maxSpeed": self.max_speed,
            "released": self.released,
            "actions": [a.to_dict() for a in self.actions],
            "rotationAllowed": self.rotation_allowed,
            "direction": self.direction,
        }
        if self.trajectory:
            result["trajectory"] = self.trajectory
        return result


@dataclass
class Vda5050CompleteOrder:
    """
    VDA5050 完整 Order 消息.

    标准字段 (VDA5050 v2.0):
      - headerId: 消息头 ID
      - version: 协议版本
      - manufacturer: 制造商
      - serialNumber: AGV 序列号
      - timestamp: ISO 8601 时间戳
      - orderId: 订单 ID
      - orderUpdateId: 订单更新 ID
      - nodes: 节点序列
      - edges: 边序列
    """
    order_id: str
    order_update_id: int = 0
    nodes: List[Vda5050Node] = field(default_factory=list)
    edges: List[Vda5050Edge] = field(default_factory=list)
    header_id: int = 1
    version: str = "2.0.0"
    manufacturer: str = "AGV-TMS"
    serial_number: str = ""

    def to_dict(self) -> Dict:
        return {
            "headerId": self.header_id,
            "version": self.version,
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial_number,
            "timestamp": _iso_timestamp(),
            "orderId": self.order_id,
            "orderUpdateId": self.order_update_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass
class Vda5050CompleteState:
    """
    VDA5050 完整 State 消息 — 14 个标准字段.

    1. headerId, 2. version, 3. manufacturer, 4. serialNumber,
    5. timestamp, 6. orderId, 7. orderUpdateId, 8. lastNodeId,
    9. lastEdgeId, 10. agvPosition, 11. agvState, 12. driving,
    13. batteryState, 14. actionStates
    """
    serial_number: str
    order_id: str = ""
    order_update_id: int = 0
    last_node_id: str = ""
    last_edge_id: str = ""
    agv_position: Optional[Dict] = None  # {x, y, theta, mapId}
    agv_state: str = "idle"  # idle/executing/charging/error/charging
    driving: bool = False
    battery_state: Optional[Dict] = None  # {batteryCharge, charging}
    action_states: List[Dict] = field(default_factory=list)
    safetyState: str = "normal"
    errors: List[Dict] = field(default_factory=list)
    warnings: List[Dict] = field(default_factory=list)
    information: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "headerId": 1,
            "version": "2.0.0",
            "manufacturer": "AGV-TMS",
            "serialNumber": self.serial_number,
            "timestamp": _iso_timestamp(),
            "orderId": self.order_id,
            "orderUpdateId": self.order_update_id,
            "lastNodeId": self.last_node_id,
            "lastEdgeId": self.last_edge_id,
            "agvPosition": self.agv_position,
            "agvState": self.agv_state,
            "driving": self.driving,
            "batteryState": self.battery_state,
            "actionStates": self.action_states,
            "safetyState": self.safetyState,
            "errors": self.errors,
            "warnings": self.warnings,
            "information": self.information,
        }


@dataclass
class Vda5050CompleteInstantAction:
    """VDA5050 完整 InstantAction 消息"""
    instant_action_type: str  # stop / cancelOrder / start
    action_id: str = ""
    header_id: int = 1
    version: str = "2.0.0"
    manufacturer: str = "AGV-TMS"
    serial_number: str = ""

    def to_dict(self) -> Dict:
        return {
            "headerId": self.header_id,
            "version": self.version,
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial_number,
            "timestamp": _iso_timestamp(),
            "instantActionType": self.instant_action_type,
            "actionId": self.action_id,
        }


# ==================== 辅助函数 ====================

def _iso_timestamp() -> str:
    """ISO 8601 时间戳"""
    from datetime import datetime
    return datetime.now().isoformat() + "Z"


def build_order_from_path(
    order_id: str,
    path: List[str],
    node_positions: Optional[Dict[str, Dict[str, float]]] = None,
    max_speed: float = 1.5,
) -> Vda5050CompleteOrder:
    """
    从路径节点列表构建完整 VDA5050 Order.

    Args:
        order_id: 订单 ID
        path: 有序节点 ID 列表 ["N1", "N2", "N3"]
        node_positions: 节点位置 {node_id: {x, y, theta}}
        max_speed: 最大速度

    Returns:
        Vda5050CompleteOrder
    """
    node_positions = node_positions or {}
    nodes = []
    edges = []

    for i, node_id in enumerate(path):
        pos = node_positions.get(node_id, {})
        node = Vda5050Node(
            node_id=node_id,
            sequence_id=i + 1,
            x=pos.get("x"),
            y=pos.get("y"),
            theta=pos.get("theta"),
            map_id=pos.get("map_id", ""),
        )
        nodes.append(node)

        if i > 0:
            prev_id = path[i - 1]
            edge = Vda5050Edge(
                edge_id=f"E{i}",
                start_node_id=prev_id,
                end_node_id=node_id,
                sequence_id=i,
                max_speed=max_speed,
            )
            edges.append(edge)

    return Vda5050CompleteOrder(
        order_id=order_id,
        nodes=nodes,
        edges=edges,
    )


def build_action(
    action_type: str,
    blocking: str = "HARD",
    parameters: Optional[Dict[str, str]] = None,
) -> Vda5050Action:
    """构建 VDA5050 Action"""
    params = [
        Vda5050ActionParameter(key=k, value=v)
        for k, v in (parameters or {}).items()
    ]
    return Vda5050Action(
        action_type=action_type,
        action_id=f"act_{int(time.time()*1000)}",
        blocking_type=blocking,
        parameters=params,
    )
