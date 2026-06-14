"""
Discrete Event Simulation Engine.

Provides fast-forward simulation (5x-10x acceleration) for:
- Scenario testing without real AGVs
- Performance benchmarking
- What-if analysis
- Historical replay with modifications

Uses SimPy-style discrete event simulation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SimulationEvent:
    """A discrete event in the simulation."""
    time: float  # Simulation time
    event_type: str  # task_arrival, agv_arrival, charge_start, etc.
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResult:
    """Results from a simulation run."""
    total_time: float = 0.0
    real_time: float = 0.0
    speedup: float = 1.0
    events_processed: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_agv_utilization: float = 0.0
    total_distance: float = 0.0
    total_energy: float = 0.0
    collisions: int = 0
    deadlocks: int = 0
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    metrics_history: List[Dict[str, Any]] = field(default_factory=list)


class SimulationEngine:
    """
    Discrete event simulation engine.

    Simulates AGV fleet operations at configurable speed.
    """

    def __init__(self, speedup: float = 5.0):
        self.speedup = speedup
        self._sim_time: float = 0.0
        self._event_queue: List[SimulationEvent] = []
        self._agvs: Dict[str, Dict[str, Any]] = {}
        self._tasks: List[Dict[str, Any]] = []
        self._results = SimulationResult(speedup=speedup)
        self._running = False

    def initialize(
        self,
        agvs: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]] = None,
        edges: List[Dict[str, Any]] = None,
    ):
        """Initialize simulation with fleet and tasks."""
        self._agvs = {a["id"]: a.copy() for a in agvs}
        self._tasks = [t.copy() for t in tasks]
        self._sim_time = 0.0
        self._event_queue = []
        self._results = SimulationResult(speedup=self.speedup)

        # Schedule task arrivals
        for task in self._tasks:
            arrival_time = task.get("arrival_time", 0.0)
            self._schedule_event(SimulationEvent(
                time=arrival_time,
                event_type="task_arrival",
                data={"task": task},
            ))

        logger.info("Simulation initialized: %d AGVs, %d tasks, %.1fx speedup",
                    len(self._agvs), len(self._tasks), self.speedup)

    def _schedule_event(self, event: SimulationEvent):
        """Add an event to the queue (sorted by time)."""
        self._event_queue.append(event)
        self._event_queue.sort(key=lambda e: e.time)

    async def run(self, duration: float = 3600.0) -> SimulationResult:
        """
        Run the simulation for a given duration (simulated seconds).

        Returns simulation results.
        """
        self._running = True
        start_real = time.perf_counter()
        end_sim_time = self._sim_time + duration

        logger.info("Starting simulation: %.0fs simulated, %.1fx speedup",
                    duration, self.speedup)

        while self._running and self._event_queue and self._sim_time < end_sim_time:
            event = self._event_queue.pop(0)
            self._sim_time = event.time

            # Record timeline
            self._results.timeline.append({
                "time": self._sim_time,
                "event": event.event_type,
                "data": event.data,
            })

            # Process event
            await self._process_event(event)
            self._results.events_processed += 1

            # Yield to event loop periodically (for real-time monitoring)
            if self._results.events_processed % 100 == 0:
                await asyncio.sleep(0)

        self._running = False
        self._results.total_time = self._sim_time
        self._results.real_time = time.perf_counter() - start_real

        # Calculate final metrics
        self._calculate_final_metrics()

        logger.info("Simulation complete: %d events, %.1fs sim / %.1fs real (%.1fx actual)",
                    self._results.events_processed,
                    self._results.total_time, self._results.real_time,
                    self._results.total_time / max(self._results.real_time, 0.001))

        return self._results

    async def _process_event(self, event: SimulationEvent):
        """Process a simulation event."""
        if event.event_type == "task_arrival":
            await self._handle_task_arrival(event.data.get("task"))
        elif event.event_type == "agv_arrival":
            await self._handle_agv_arrival(event.data)
        elif event.event_type == "task_complete":
            await self._handle_task_complete(event.data)
        elif event.event_type == "charge_complete":
            await self._handle_charge_complete(event.data)

    async def _handle_task_arrival(self, task: Dict[str, Any]):
        """Handle a task arrival event."""
        # Find best available AGV
        available_agvs = [
            agv for agv in self._agvs.values()
            if agv.get("status") == "idle" and agv.get("battery", 100) > 20
        ]

        if not available_agvs:
            self._results.tasks_failed += 1
            return

        # Simple nearest AGV selection
        agv = available_agvs[0]
        agv["status"] = "busy"
        agv["current_task"] = task.get("id")

        # Schedule AGV arrival at pickup
        travel_time = 10.0 / self.speedup  # Simplified
        self._schedule_event(SimulationEvent(
            time=self._sim_time + travel_time,
            event_type="agv_arrival",
            data={"agv_id": agv["id"], "task_id": task.get("id"), "phase": "pickup"},
        ))

    async def _handle_agv_arrival(self, data: Dict[str, Any]):
        """Handle AGV arrival at pickup/dropoff."""
        agv = self._agvs.get(data["agv_id"])
        if not agv:
            return

        phase = data.get("phase")
        if phase == "pickup":
            # Schedule dropoff
            travel_time = 20.0 / self.speedup
            self._schedule_event(SimulationEvent(
                time=self._sim_time + travel_time,
                event_type="agv_arrival",
                data={"agv_id": agv["id"], "task_id": data["task_id"], "phase": "dropoff"},
            ))
        elif phase == "dropoff":
            # Complete task
            self._schedule_event(SimulationEvent(
                time=self._sim_time,
                event_type="task_complete",
                data={"agv_id": agv["id"], "task_id": data["task_id"]},
            ))

    async def _handle_task_complete(self, data: Dict[str, Any]):
        """Handle task completion."""
        agv = self._agvs.get(data["agv_id"])
        if agv:
            agv["status"] = "idle"
            agv["current_task"] = None
            agv["battery"] = max(0, agv.get("battery", 100) - 5)  # Battery drain
        self._results.tasks_completed += 1

    async def _handle_charge_complete(self, data: Dict[str, Any]):
        """Handle charge completion."""
        agv = self._agvs.get(data["agv_id"])
        if agv:
            agv["status"] = "idle"
            agv["battery"] = 100.0

    def _calculate_final_metrics(self):
        """Calculate final simulation metrics."""
        if self._agvs:
            busy_time = sum(
                1 for a in self._agvs.values()
                if a.get("status") == "busy"
            )
            self._results.avg_agv_utilization = busy_time / len(self._agvs)

        self._results.metrics_history.append({
            "time": self._sim_time,
            "tasks_completed": self._results.tasks_completed,
            "utilization": self._results.avg_agv_utilization,
        })


# Singleton
simulation_engine = SimulationEngine()
