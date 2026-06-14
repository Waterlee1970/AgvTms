"""
Schedule Orchestration Service.

Manages the scheduling pipeline, stores results, and provides
the interface between the API layer and algorithm layer.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from ..algorithms.hybrid import run_hybrid_schedule
from ..models.schemas import (
    AlgorithmConfig,
    AgvStatus,
    AgvTask,
    ConveyorSegment,
    ConveyorTask,
    ScheduleResult,
)

logger = logging.getLogger(__name__)


class ScheduleService:
    """Service for orchestrating scheduling operations."""

    def __init__(self):
        self._results: Dict[str, ScheduleResult] = {}
        self._config = AlgorithmConfig()
        self._tasks: List[AgvTask] = []
        self._agvs: List[AgvStatus] = []
        self._conveyor_tasks: List[ConveyorTask] = []
        self._conveyor_segments: List[ConveyorSegment] = []
        self._init_demo_data()

    def _init_demo_data(self):
        """Initialize demo data for testing."""
        # Demo AGVs
        self._agvs = [
            AgvStatus(id="AGV001", name="AGV-1号", x=0.0, y=0.0, battery=95.0,
                       current_node="N_P00", status="idle"),
            AgvStatus(id="AGV002", name="AGV-2号", x=10.0, y=0.0, battery=88.0,
                       current_node="N_P01", status="idle"),
            AgvStatus(id="AGV003", name="AGV-3号", x=20.0, y=0.0, battery=72.0,
                       current_node="N_P02", status="idle"),
            AgvStatus(id="AGV004", name="AGV-4号", x=30.0, y=0.0, battery=100.0,
                       current_node="N_P03", status="idle"),
        ]

        # Demo tasks
        self._tasks = [
            AgvTask(id="T001", pickup_node="N_P00", dropoff_node="N_D03", priority=8,
                     create_time=datetime.now(), deadline=None),
            AgvTask(id="T002", pickup_node="N_P01", dropoff_node="N_D00", priority=5,
                     create_time=datetime.now(), deadline=None),
            AgvTask(id="T003", pickup_node="N_P03", dropoff_node="N_D01", priority=9,
                     create_time=datetime.now(), deadline=None),
            AgvTask(id="T004", pickup_node="N_P02", dropoff_node="N_D04", priority=3,
                     create_time=datetime.now(), deadline=None),
        ]

        # Demo conveyor segments
        self._conveyor_segments = [
            ConveyorSegment(id="conv_ce_c00", from_node="N_CE", to_node="N_C_00",
                            speed=0.5, length=5.0, name="输送带-入口段"),
            ConveyorSegment(id="conv_c00_c01", from_node="N_C_00", to_node="N_C_01",
                            speed=0.5, length=10.0, name="输送带-1段"),
            ConveyorSegment(id="conv_c01_c02", from_node="N_C_01", to_node="N_C_02",
                            speed=0.5, length=10.0, name="输送带-2段"),
            ConveyorSegment(id="conv_c02_c03", from_node="N_C_02", to_node="N_C_03",
                            speed=0.5, length=10.0, name="输送带-3段"),
            ConveyorSegment(id="conv_c03_c04", from_node="N_C_03", to_node="N_C_04",
                            speed=0.5, length=10.0, name="输送带-4段"),
            ConveyorSegment(id="conv_c04_cx", from_node="N_C_04", to_node="N_CX",
                            speed=0.5, length=5.0, name="输送带-出口段"),
        ]

        # Demo conveyor tasks
        self._conveyor_tasks = [
            ConveyorTask(id="CT001", segment_id="conv_ce_c00", duration=10.0,
                          priority=5, cargo_id="货品A"),
            ConveyorTask(id="CT002", segment_id="conv_ce_c00", duration=12.0,
                          priority=3, cargo_id="货品B",
                          predecessor_ids=["CT001"]),
            ConveyorTask(id="CT003", segment_id="conv_ce_c00", duration=8.0,
                          priority=7, cargo_id="货品C"),
        ]

    # ---- Schedule Operations ----

    def run_scheduling(
        self,
        tasks: Optional[List[AgvTask]] = None,
        agvs: Optional[List[AgvStatus]] = None,
        conveyor_tasks: Optional[List[Dict]] = None,
    ) -> ScheduleResult:
        """Run the hybrid scheduling pipeline."""
        from ..services.map_service import map_service

        graph = map_service.get_graph()
        nodes = graph.nodes
        edges = graph.edges

        use_tasks = tasks or self._tasks
        use_agvs = agvs or self._agvs

        # Parse conveyor tasks if provided as dicts
        use_conveyor_tasks = self._conveyor_tasks
        if conveyor_tasks:
            use_conveyor_tasks = [ConveyorTask(**ct) for ct in conveyor_tasks]

        result = run_hybrid_schedule(
            nodes=nodes,
            edges=edges,
            tasks=use_tasks,
            agvs=use_agvs,
            conveyor_tasks=use_conveyor_tasks,
            conveyor_segments=self._conveyor_segments,
            config=self._config,
        )

        # Assign ID and store
        result.id = str(uuid.uuid4())[:8]
        self._results[result.id] = result

        # Update AGV statuses based on assignments
        for assignment in result.assignments:
            for agv in self._agvs:
                if agv.id == assignment.agv_id:
                    agv.status = "executing"
                    agv.current_task = assignment.task_id
                    agv.path = assignment.path

        return result

    def get_result(self, result_id: str) -> Optional[ScheduleResult]:
        """Retrieve a schedule result by ID."""
        return self._results.get(result_id)

    # ---- AGV Operations ----

    def get_agvs(self) -> List[AgvStatus]:
        return copy.deepcopy(self._agvs)

    def update_agv(self, agv_id: str, updates: dict) -> Optional[AgvStatus]:
        for agv in self._agvs:
            if agv.id == agv_id:
                for key, value in updates.items():
                    if hasattr(agv, key):
                        setattr(agv, key, value)
                return agv
        return None

    # ---- Task Operations ----

    def get_tasks(self) -> List[AgvTask]:
        return copy.deepcopy(self._tasks)

    def add_task(self, task: AgvTask) -> AgvTask:
        if not task.id:
            task.id = f"T{len(self._tasks) + 1:03d}"
        self._tasks.append(task)
        return task

    def add_tasks(self, tasks: List[AgvTask]) -> List[AgvTask]:
        for task in tasks:
            self.add_task(task)
        return tasks

    # ---- Config Operations ----

    def get_config(self) -> AlgorithmConfig:
        return copy.deepcopy(self._config)

    def update_config(self, config: AlgorithmConfig) -> AlgorithmConfig:
        self._config = config
        return self._config

    # ---- Metrics ----

    def get_metrics(self) -> dict:
        """Get current system metrics."""
        results_list = list(self._results.values())
        latest = results_list[-1] if results_list else None

        return {
            "total_agvs": len(self._agvs),
            "active_agvs": sum(1 for a in self._agvs if a.status != "idle"),
            "total_tasks": len(self._tasks),
            "pending_tasks": sum(1 for t in self._tasks if t.status == "pending"),
            "completed_tasks": sum(1 for t in self._tasks if t.status == "completed"),
            "total_schedules_run": len(results_list),
            "latest_metrics": latest.metrics.model_dump() if latest else None,
        }


# Singleton instance
schedule_service = ScheduleService()
