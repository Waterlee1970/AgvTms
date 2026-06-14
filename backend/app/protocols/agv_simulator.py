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
    ):
        self.serial = serial
        self.manufacturer = manufacturer
        self.current_node = start_node
        self.x = start_x
        self.y = start_y
        self.theta = 0.0
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

    def set_node_positions(self, positions: Dict[str, Dict[str, float]]):
        """Set the map of node_id → {x, y} for path following."""
        self._node_positions = positions

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

            # Battery drain
            if self.driving:
                self.battery = max(0, self.battery - 0.01)
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
                "charging": False,
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
            for action in node.actions:
                logger.info("AGV %s executing action %s at node %s",
                           self.serial, action.actionType, node.nodeId)
                await asyncio.sleep(0.5)  # Simulate action duration

        self.driving = False
        self._current_order = None
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

    async def handle_instant_action(self, action: Vda5050InstantAction):
        """Handle an instant action."""
        logger.info("AGV %s received instant action: %s", self.serial, action.instant_action_type)

        if action.instant_action_type == "stop":
            self.paused = True
        elif action.instant_action_type == "cancelOrder":
            self._current_order = None
            self.driving = False
        elif action.instant_action_type == "start":
            self.paused = False

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
    ) -> AgvSimulator:
        """Add a simulated AGV to the fleet."""
        sim = AgvSimulator(
            serial=serial,
            start_node=start_node,
            start_x=start_x,
            start_y=start_y,
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
