"""
VDA5050 Protocol Adapter.

Implements the VDA5050 v2.0 standard for AGV communication:
- Order: Task dispatch to AGV
- State: AGV status reporting
- InstantAction: Emergency stop, cancel, etc.
- Connection: MQTT topic management

Reference: https://vda5050.org/v2/
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# VDA5050 Data Models
# =============================================================================

class Vda5050ActionType(str, Enum):
    """VDA5050 standard action types."""
    PICKUP = "pickPosition"
    DROPOFF = "dropPosition"
    INIT_POSITION = "initPosition"
    CHARGE = "charge"
    WAIT = "wait"
    CUSTOM = "customAction"


class Vda5050NodeAction(BaseModel):
    """Action to perform at a node."""
    actionType: str = Field(..., description="Type of action")
    actionId: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    actionParameters: List[Dict[str, Any]] = Field(default_factory=list)
    blockingType: str = Field(default="SOFT", description="NONE, SOFT, or HARD")


class Vda5050Edge(BaseModel):
    """VDA5050 edge between nodes."""
    edgeId: str
    startNodeId: str
    endNodeId: str
    maxSpeed: float = Field(default=1.5, description="m/s")
    length: float = Field(default=0.0, description="meters")
    orientation: str = Field(default="", description="forward/backward")
    rotationAllowed: bool = True
    maxRotationSpeed: float = 0.0
    direction: str = Field(default="forward")


class Vda5050Node(BaseModel):
    """VDA5050 node (waypoint)."""
    nodeId: str
    x: float
    y: float
    theta: Optional[float] = None
    allowedHorizations: List[float] = Field(default_factory=list)
    actions: List[Vda5050NodeAction] = Field(default_factory=list)
    released: bool = True


class Vda5050Order(BaseModel):
    """VDA5050 Order message — task dispatched to AGV."""
    order_id: str = Field(alias="orderId", description="Unique order ID")
    order_update_id: int = Field(alias="orderUpdateId", default=0)
    nodes: List[Vda5050Node] = Field(default_factory=list)
    edges: List[Vda5050Edge] = Field(default_factory=list)
    header_id: int = Field(alias="headerId", default=0)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    version: str = Field(default="2.0.0")
    manufacturer: str = Field(default="AGV-TMS")
    serial_number: str = Field(alias="serialNumber", default="")

    model_config = {"populate_by_name": True}


class Vda5050State(BaseModel):
    """VDA5050 State message — AGV status report."""
    header_id: int = Field(alias="headerId", default=0)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    version: str = Field(default="2.0.0")
    manufacturer: str = Field(default="")
    serial_number: str = Field(alias="serialNumber", default="")
    agv_position: Optional[Dict[str, Any]] = Field(alias="agvPosition", default=None)
    battery_state: Optional[Dict[str, Any]] = Field(alias="batteryState", default=None)
    velocity: Optional[Dict[str, Any]] = None
    driving: bool = False
    paused: bool = False
    new_base_request: bool = Field(alias="newBaseRequest", default=False)
    last_node_id: str = Field(alias="lastNodeId", default="")
    last_node_sequence_id: int = Field(alias="lastNodeSequenceId", default=0)
    distance_since_last_node: float = Field(alias="distanceSinceLastNode", default=0.0)
    action_states: List[Dict[str, Any]] = Field(alias="actionStates", default_factory=list)
    error_states: List[Dict[str, Any]] = Field(alias="errorStates", default_factory=list)
    safety_state: Optional[Dict[str, Any]] = Field(alias="safetyState", default=None)
    connection: str = Field(default="ONLINE")  # ONLINE, OFFLINE, CONNECTION_ESTABLISHED

    model_config = {"populate_by_name": True}


class Vda5050InstantAction(BaseModel):
    """VDA5050 InstantAction — emergency/cancel actions."""
    header_id: int = Field(alias="headerId", default=0)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    version: str = Field(default="2.0.0")
    manufacturer: str = Field(default="")
    serial_number: str = Field(alias="serialNumber", default="")
    instant_action_type: str = Field(alias="instantActionType")
    action_id: str = Field(alias="actionId", default_factory=lambda: str(uuid.uuid4())[:8])
    action_parameters: List[Dict[str, Any]] = Field(alias="actionParameters", default_factory=list)

    model_config = {"populate_by_name": True}


# =============================================================================
# MQTT Topic Management
# =============================================================================

class Vda5050Topics:
    """VDA5050 standard MQTT topic structure."""

    @staticmethod
    def order_topic(manufacturer: str, serial: str) -> str:
        """Topic for sending orders to AGV."""
        return f"uagv/v2/{manufacturer}/{serial}/order"

    @staticmethod
    def state_topic(manufacturer: str, serial: str) -> str:
        """Topic AGV publishes state to."""
        return f"uagv/v2/{manufacturer}/{serial}/state"

    @staticmethod
    def connection_topic(manufacturer: str, serial: str) -> str:
        """Topic AGV publishes connection status."""
        return f"uagv/v2/{manufacturer}/{serial}/connection"

    @staticmethod
    def instant_action_topic(manufacturer: str, serial: str) -> str:
        """Topic for instant actions (emergency stop, cancel)."""
        return f"uagv/v2/{manufacturer}/{serial}/instantAction"

    @staticmethod
    def visualization_topic(manufacturer: str, serial: str) -> str:
        """Topic for visualization data."""
        return f"uagv/v2/{manufacturer}/{serial}/visualization"


# =============================================================================
# VDA5050 Adapter
# =============================================================================

class Vda5050Adapter:
    """
    Adapter between AGV-TMS internal models and VDA5050 protocol.

    Converts internal ScheduleResult/AgvAssignment to VDA5050 Orders,
    and VDA5050 State messages to internal AgvStatus.
    """

    def __init__(self, manufacturer: str = "AGV-TMS"):
        self.manufacturer = manufacturer
        self._order_counter: Dict[str, int] = {}  # agv_id -> update counter
        self._header_counter: int = 0

    def _next_header_id(self) -> int:
        self._header_counter += 1
        return self._header_counter

    def _next_order_update_id(self, agv_id: str) -> int:
        self._order_counter[agv_id] = self._order_counter.get(agv_id, 0) + 1
        return self._order_counter[agv_id]

    # ---- Conversion: Internal → VDA5050 ----

    def assignment_to_order(
        self,
        agv_id: str,
        agv_serial: str,
        path: List[str],
        task_id: str = "",
        nodes_data: Optional[Dict[str, Dict]] = None,
    ) -> Vda5050Order:
        """
        Convert an internal AGV path assignment to a VDA5050 Order.

        Args:
            agv_id: Internal AGV ID (e.g. "AGV001")
            agv_serial: AGV serial number for VDA5050 topic
            path: List of node IDs forming the path
            task_id: Associated task ID
            nodes_data: Optional dict of {node_id: {x, y, theta, actions}}
        """
        nodes_data = nodes_data or {}
        vda_nodes: List[Vda5050Node] = []
        vda_edges: List[Vda5050Edge] = []

        for i, node_id in enumerate(path):
            nd = nodes_data.get(node_id, {})
            actions = []
            if i == 0 and task_id:
                actions.append(Vda5050NodeAction(
                    actionType=Vda5050ActionType.PICKUP.value,
                    actionParameters=[{"key": "taskId", "value": task_id}],
                ))
            elif i == len(path) - 1 and task_id:
                actions.append(Vda5050NodeAction(
                    actionType=Vda5050ActionType.DROPOFF.value,
                    actionParameters=[{"key": "taskId", "value": task_id}],
                ))

            vda_nodes.append(Vda5050Node(
                nodeId=node_id,
                x=nd.get("x", 0.0),
                y=nd.get("y", 0.0),
                theta=nd.get("theta"),
                actions=actions,
            ))

            if i > 0:
                prev = path[i - 1]
                prev_data = nodes_data.get(prev, {})
                curr_data = nodes_data.get(node_id, {})
                dx = curr_data.get("x", 0.0) - prev_data.get("x", 0.0)
                dy = curr_data.get("y", 0.0) - prev_data.get("y", 0.0)
                length = (dx ** 2 + dy ** 2) ** 0.5
                vda_edges.append(Vda5050Edge(
                    edgeId=f"edge_{prev}_{node_id}",
                    startNodeId=prev,
                    endNodeId=node_id,
                    length=length,
                ))

        return Vda5050Order(
            orderId=f"order_{agv_id}_{task_id}_{int(time.time())}",
            orderUpdateId=self._next_order_update_id(agv_id),
            nodes=vda_nodes,
            edges=vda_edges,
            headerId=self._next_header_id(),
            manufacturer=self.manufacturer,
            serialNumber=agv_serial,
        )

    def instant_action_cancel(self, agv_serial: str) -> Vda5050InstantAction:
        """Create a cancel order instant action."""
        return Vda5050InstantAction(
            instantActionType="cancelOrder",
            manufacturer=self.manufacturer,
            serialNumber=agv_serial,
            headerId=self._next_header_id(),
        )

    def instant_action_emergency_stop(self, agv_serial: str) -> Vda5050InstantAction:
        """Create an emergency stop instant action."""
        return Vda5050InstantAction(
            instantActionType="stop",
            manufacturer=self.manufacturer,
            serialNumber=agv_serial,
            headerId=self._next_header_id(),
        )

    # ---- Conversion: VDA5050 → Internal ----

    def state_to_agv_status(self, state: Vda5050State) -> Dict[str, Any]:
        """Convert a VDA5050 State message to internal AGV status dict."""
        pos = state.agv_position or {}
        battery = state.battery_state or {}

        # Map VDA5050 state to internal status
        if state.paused:
            status = "waiting"
        elif state.driving:
            status = "moving"
        elif state.connection == "OFFLINE":
            status = "error"
        else:
            status = "idle"

        return {
            "x": pos.get("x", 0.0),
            "y": pos.get("y", 0.0),
            "battery": battery.get("batteryCharge", 100.0),
            "status": status,
            "current_node": state.last_node_id or None,
            "speed": (state.velocity or {}).get("vx", 0.0),
            "connection": state.connection,
        }

    # ---- Topic Helpers ----

    def get_order_topic(self, agv_serial: str) -> str:
        return Vda5050Topics.order_topic(self.manufacturer, agv_serial)

    def get_state_topic(self, agv_serial: str) -> str:
        return Vda5050Topics.state_topic(self.manufacturer, agv_serial)

    def get_instant_action_topic(self, agv_serial: str) -> str:
        return Vda5050Topics.instant_action_topic(self.manufacturer, agv_serial)


# Singleton
vda5050_adapter = Vda5050Adapter()
