"""
VDA5050 AGV Simulator.

Simulates AGV behavior following VDA5050 protocol:
- Receives orders via MQTT (or direct callback)
- Moves along path nodes at configurable speed
- Reports state periodically
- Handles instant actions (stop, cancel)
- Simulates battery drain and charging

Usage:
    sim = AgvSimulator(serial="AGV001", start_node="N_P00")
    await sim.start()  # Starts state reporting loop
    await sim.process_order(order)  # Execute an order
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

from .vda5050 import (
    Vda5050Order,
    Vda5050State,
    Vda5050InstantAction,
    Vda5050Topics,
)

logger = logging.getLogger(__name__)


class AgvSimulator:
    """Simulates a single VDA5050-compatible AGV."""

    def __init__(
        self,
        serial: str = "AGV001",
        manufacturer: str = "AGV-TMS",
        start_node: str = "N_P00",
        start_x: float = 0.0,
        start_y: float = 0.0,
        speed: float = 1.5,
        battery: float = 100.0,
        state_callback: Optional[Callable[[Vda5050State], Any]] = None,
        start_theta: float = 0.0,
    ):
        self.serial = serial
        self.manufacturer = manufacturer
        self.current_node = start_node
        self.x = start_x
        self.y = start_y
        self.theta = start_theta
        self.speed = speed
        self.battery = battery
        self.driving = False
        self.paused = False
        self.connection = "ONLINE"
        self.last_node_id = start_node
        self.last_node_sequence_id = 0
        self.distance_since_last_node = 0.0

        self._current_order: Optional[Vda5050Order] = None
        self._path_index = 0
        self._running = False
        self._state_callback = state_callback
        self._node_positions: Dict[str, Dict[str, float]] = {}

        # Error states
        self._errors: List[Dict[str, Any]] = []

        # ---- 扩展状态 (SEER/VDA5050 通道增强) ----
        self.loaded = False          # 是否已装载 (pickPosition/dropPosition)
        self.charging = False        # 是否正在充电
        self.order_id = ""           # 当前 VDA5050 order ID
        self.serial_number = serial  # 对外序列号 (与 AGVId 可能不同)

    def set_node_positions(self, positions: Dict[str, Dict[str, float]]):
        """Set the map of node_id → {x, y} for path following."""
        self._node_positions = positions

    def set_pose(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        theta: Optional[float] = None,
        node_id: Optional[str] = None,
    ) -> "AgvSimulator":
        """
        设置机器人位姿 (位置初始化/二次定位).

        仅更新给定字段; 返回 self 便于链式调用。
        """
        if x is not None:
            self.x = float(x)
        if y is not None:
            self.y = float(y)
        if theta is not None:
            self.theta = float(theta)
        if node_id:
            self.current_node = node_id
            self.last_node_id = node_id
        self.distance_since_last_node = 0.0
        logger.info("AGV %s pose initialized -> (%.3f, %.3f, theta=%.3f)",
                    self.serial, self.x, self.y, self.theta)
        return self

    def set_load(self, loaded: bool) -> None:
        """设置装载状态 (pickPosition/dropPosition 模拟)."""
        self.loaded = bool(loaded)
        logger.info("AGV %s load state -> %s", self.serial, self.loaded)

    def set_charging(self, charging: bool) -> None:
        """进入/退出充电 (模拟)."""
        self.charging = bool(charging)
        if self.charging:
            logger.info("AGV %s start charging", self.serial)
        else:
            logger.info("AGV %s stop charging", self.serial)

    def set_error(self, code: Any = 1, message: str = "", error_type: str = "generic") -> None:
        """注入错误状态 (用于测试/演示)."""
        self._errors = [
            {
                "errorType": error_type,
                "errorReferences": [{"referenceKey": "code", "referenceValue": str(code)}],
                "errorDescription": message,
            }
        ]

    def clear_errors(self) -> None:
        """清除错误状态."""
        self._errors = []

    async def start(self, report_interval: float = 1.0):
        """Start the state reporting loop."""
        self._running = True
        asyncio.create_task(self._state_loop(report_interval))
        logger.info("AGV simulator %s started at node %s", self.serial, self.current_node)

    async def stop(self):
        """Stop the simulator."""
        self._running = False
        self.connection = "OFFLINE"

    async def _state_loop(self, interval: float):
        """Periodically report state."""
        while self._running:
            state = self._build_state()
            if self._state_callback:
                try:
                    result = self._state_callback(state)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.debug("State callback error for %s: %s", self.serial, e)

            # Battery drain / charging
            if self.charging:
                # 充电回充
                self.battery = min(100.0, self.battery + 0.05)
                if self.battery >= 99.9:
                    self.charging = False
                    logger.info("AGV %s charging finished", self.serial)
            elif self.driving:
                self.battery = max(0, self.battery - 0.01)
            else:
                self.battery = max(0, self.battery - 0.001)  # idle drain

            await asyncio.sleep(interval)

    def _build_state(self) -> Vda5050State:
        """Build current VDA5050 State message."""
        return Vda5050State(
            manufacturer=self.manufacturer,
            serialNumber=self.serial,
            agvPosition={"x": self.x, "y": self.y, "theta": self.theta},
            batteryState={
                "batteryCharge": self.battery,
                "charging": self.charging,
            },
            velocity={"vx": self.speed if self.driving else 0.0, "vy": 0.0},
            driving=self.driving,
            paused=self.paused,
            lastNodeId=self.last_node_id,
            lastNodeSequenceId=self.last_node_sequence_id,
            distanceSinceLastNode=self.distance_since_last_node,
            errorStates=list(self._errors),
            connection=self.connection,
        )

    async def process_order(self, order: Vda5050Order):
        """Execute a VDA5050 Order by following the node path."""
        self._current_order = order
        self._path_index = 0
        self.driving = True
        self.order_id = order.order_id

        logger.info("AGV %s processing order %s: %d nodes",
                    self.serial, order.order_id, len(order.nodes))

        for i, node in enumerate(order.nodes):
            if self.paused:
                await self._wait_for_unpause()

            # Move to node
            target_x = node.x
            target_y = node.y
            if self._node_positions and node.nodeId in self._node_positions:
                target_x = self._node_positions[node.nodeId].get("x", target_x)
                target_y = self._node_positions[node.nodeId].get("y", target_y)

            await self._move_to(target_x, target_y)

            self.last_node_id = node.nodeId
            self.last_node_sequence_id = i
            self.current_node = node.nodeId
            self.distance_since_last_node = 0.0

            # Execute node actions (simulate)
            await self._execute_node_actions(node.actions, node.nodeId)

        self.driving = False
        self._current_order = None
        self.order_id = ""
        logger.info("AGV %s completed order %s", self.serial, order.order_id)

    async def _move_to(self, target_x: float, target_y: float):
        """Simulate movement to target position."""
        dx = target_x - self.x
        dy = target_y - self.y
        distance = math.sqrt(dx * dx + dy * dy)

        if distance < 0.1:
            return

        # Update theta (heading)
        self.theta = math.atan2(dy, dx)

        # Move at speed
        travel_time = distance / self.speed
        steps = max(1, int(travel_time / 0.1))  # 100ms steps

        for _ in range(steps):
            if self.paused:
                await self._wait_for_unpause()
            self.x += dx / steps
            self.y += dy / steps
            self.distance_since_last_node += distance / steps
            await asyncio.sleep(0.1)

        self.x = target_x
        self.y = target_y

    async def _wait_for_unpause(self):
        """Wait while paused."""
        while self.paused and self._running:
            await asyncio.sleep(0.5)

    async def _execute_node_actions(self, actions, node_id: str):
        """执行节点动作 (装载/卸货/充电/位置初始化/等待等)."""
        for action in actions:
            action_type = getattr(action, "actionType", "")
            logger.info("AGV %s executing action %s at node %s",
                        self.serial, action_type, node_id)
            if action_type in ("pickPosition", "pick"):
                self.set_load(True)
            elif action_type in ("dropPosition", "drop"):
                self.set_load(False)
            elif action_type == "charge":
                self.set_charging(True)
                # 有界回充, 保证离线/在线均能收敛
                for _ in range(200):
                    if self.battery >= 99.9:
                        break
                    self.battery = min(100.0, self.battery + 0.5)
                    await asyncio.sleep(0.02)
                self.set_charging(False)
            elif action_type == "initPosition":
                # 位置初始化节点: 位姿已由 adapter.set_pose 完成, 原地校准即可
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0.5)  # 其余动作耗时模拟

    async def handle_instant_action(self, action: Vda5050InstantAction):
        """Handle an instant action."""
        ia_type = action.instant_action_type
        logger.info("AGV %s received instant action: %s", self.serial, ia_type)

        if ia_type == "stop":
            self.paused = True
            self.driving = False
        elif ia_type in ("cancelOrder", "cancel"):
            self._current_order = None
            self.driving = False
            self.order_id = ""
        elif ia_type in ("start", "resume"):
            self.paused = False
        elif ia_type == "pause":
            self.paused = True
        elif ia_type == "initPosition":
            # 位置初始化: 就地采用参数位姿
            for param in action.action_parameters:
                key = param.get("key")
                value = param.get("value")
                if key == "x":
                    self.x = float(value)
                elif key == "y":
                    self.y = float(value)
                elif key == "theta":
                    self.theta = float(value)

    def get_state(self) -> Vda5050State:
        """Get current state synchronously."""
        return self._build_state()


class AgvFleetSimulator:
    """Manages a fleet of simulated AGVs."""

    def __init__(self):
        self._agvs: Dict[str, AgvSimulator] = {}
        self._state_handlers: Dict[str, Callable] = {}

    def add_agv(
        self,
        serial: str,
        start_node: str = "N_P00",
        start_x: float = 0.0,
        start_y: float = 0.0,
        speed: float = 1.5,
        battery: float = 100.0,
        state_callback: Optional[Callable] = None,
        manufacturer: str = "AGV-TMS",
        start_theta: float = 0.0,
    ) -> AgvSimulator:
        """Add a simulated AGV to the fleet."""
        sim = AgvSimulator(
            serial=serial,
            manufacturer=manufacturer,
            start_node=start_node,
            start_x=start_x,
            start_y=start_y,
            start_theta=start_theta,
            speed=speed,
            battery=battery,
            state_callback=state_callback,
        )
        self._agvs[serial] = sim
        logger.info("Added AGV simulator: %s", serial)
        return sim

    async def start_all(self):
        """Start all AGV simulators."""
        for sim in self._agvs.values():
            await sim.start()

    async def stop_all(self):
        """Stop all AGV simulators."""
        for sim in self._agvs.values():
            await sim.stop()

    def get_agv(self, serial: str) -> Optional[AgvSimulator]:
        return self._agvs.get(serial)

    def get_all_states(self) -> Dict[str, Vda5050State]:
        """Get current state of all AGVs."""
        return {serial: sim.get_state() for serial, sim in self._agvs.items()}

    def set_node_positions(self, positions: Dict[str, Dict[str, float]]):
        """Set node positions for all AGVs."""
        for sim in self._agvs.values():
            sim.set_node_positions(positions)


# Singleton
fleet_simulator = AgvFleetSimulator()
