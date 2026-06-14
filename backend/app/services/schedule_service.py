"""
Schedule Orchestration Service — Persistence-Enhanced.

Manages the scheduling pipeline, stores results in PostgreSQL,
caches real-time state in Redis, and supports V1/V2 algorithm switching.

Falls back to in-memory storage when DB is unavailable.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from ..config import settings
from ..models.schemas import (
    AgvStatus,
    AgvTask,
    AgvTaskStatus,
    AlgorithmConfig,
    ConveyorSegment,
    ConveyorTask,
    ScheduleResult,
)
from . import redis_service

logger = logging.getLogger(__name__)


class ScheduleService:
    """Service for orchestrating scheduling operations with persistence."""

    def __init__(self):
        # In-memory fallbacks
        self._results: Dict[str, ScheduleResult] = {}
        self._config: AlgorithmConfig = AlgorithmConfig()
        self._tasks: List[AgvTask] = []
        self._agvs: List[AgvStatus] = []
        self._conveyor_tasks: List[ConveyorTask] = []
        self._conveyor_segments: List[ConveyorSegment] = []
        self._active_version: str = settings.ACTIVE_ALGORITHM_VERSION
        self._init_demo_data()

    def _init_demo_data(self):
        """Initialize demo data (in-memory fallback)."""
        from ..db.seed import build_demo_agvs, build_demo_tasks, build_demo_conveyors
        self._agvs = build_demo_agvs()
        self._tasks = build_demo_tasks()
        self._conveyor_segments = build_demo_conveyors()
        self._conveyor_tasks = [
            # Main line tasks (through 10 stations)
            ConveyorTask(id="CT001", segment_id="conv_entry", duration=8.0, priority=8, cargo_id="货品A-001"),
            ConveyorTask(id="CT002", segment_id="conv_main_00", duration=12.0, priority=6, cargo_id="货品A-001"),
            ConveyorTask(id="CT003", segment_id="conv_main_01", duration=10.0, priority=6, cargo_id="货品A-001", predecessor_ids=["CT002"]),
            ConveyorTask(id="CT004", segment_id="conv_main_03", duration=14.0, priority=5, cargo_id="货品B-002"),
            ConveyorTask(id="CT005", segment_id="conv_main_05", duration=11.0, priority=7, cargo_id="货品C-003"),
            ConveyorTask(id="CT006", segment_id="conv_main_07", duration=9.0, priority=4, cargo_id="货品D-004"),
            ConveyorTask(id="CT007", segment_id="conv_exit", duration=8.0, priority=5, cargo_id="货品E-005"),
            # Buffer line tasks (parallel bypass)
            ConveyorTask(id="CT008", segment_id="conv_buf_0_A", duration=6.0, priority=3, cargo_id="缓存-A1"),
            ConveyorTask(id="CT009", segment_id="conv_buf_1_A", duration=6.0, priority=3, cargo_id="缓存-A2"),
            ConveyorTask(id="CT010", segment_id="conv_buf_2_B", duration=6.0, priority=2, cargo_id="缓存-B3"),
        ]

    # ---- DB Helpers ----

    async def _load_state_from_db(self):
        """Load AGVs, tasks, conveyors from DB (best-effort)."""
        try:
            from ..db.database import _check_db_available, get_session_factory
            if not await _check_db_available():
                return

            from ..db.models import AgvRecord, TaskRecord, ConveyorSegmentRecord, AlgorithmConfigRecord
            from sqlalchemy import select

            factory = get_session_factory()
            async with factory() as session:
                # Load AGVs
                result = await session.execute(select(AgvRecord))
                db_agvs = result.scalars().all()
                if db_agvs:
                    self._agvs = [AgvStatus.model_validate(r.state) for r in db_agvs]

                # Load Tasks
                result = await session.execute(select(TaskRecord).where(TaskRecord.status == "pending"))
                db_tasks = result.scalars().all()
                if db_tasks:
                    self._tasks = [AgvTask.model_validate(r.task_data) for r in db_tasks]

                # Load Conveyors
                result = await session.execute(select(ConveyorSegmentRecord))
                db_convs = result.scalars().all()
                if db_convs:
                    self._conveyor_segments = [ConveyorSegment.model_validate(r.segment_data) for r in db_convs]

                # Load active algorithm version
                result = await session.execute(
                    select(AlgorithmConfigRecord).where(AlgorithmConfigRecord.is_active == True)
                )
                active_config = result.scalars().first()
                if active_config:
                    self._active_version = active_config.version
                    self._config = AlgorithmConfig.model_validate(active_config.config_data)

                logger.info("Loaded state from DB: %d AGVs, %d tasks, %d conveyors, algo=%s",
                           len(self._agvs), len(self._tasks), len(self._conveyor_segments), self._active_version)
        except Exception as e:
            logger.debug("Failed to load state from DB: %s", e)

    async def _save_result_to_db(self, result: ScheduleResult, version: str):
        """Persist scheduling result to DB."""
        try:
            from ..db.database import _check_db_available, get_session_factory
            if not await _check_db_available():
                return
            from ..db.models import ScheduleResultRecord

            factory = get_session_factory()
            async with factory() as session:
                record = ScheduleResultRecord(
                    id=result.id,
                    result_data=result.model_dump(mode="json"),
                    assignments=[a.model_dump(mode="json") for a in result.assignments],
                    metrics=result.metrics.model_dump(mode="json"),
                    agv_paths=result.agv_paths,
                    total_cost=result.total_cost,
                    makespan=result.makespan,
                    algorithm_version=version,
                    runtime_ms=result.algorithm_runtime_ms,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("Failed to save result to DB: %s", e)

    async def _save_agv_to_db(self, agv: AgvStatus):
        """Upsert AGV state to DB."""
        try:
            from ..db.database import _check_db_available, get_session_factory
            if not await _check_db_available():
                return
            from ..db.models import AgvRecord
            from sqlalchemy import select

            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(select(AgvRecord).where(AgvRecord.id == agv.id))
                record = result.scalars().first()
                if record:
                    record.state = agv.model_dump(mode="json")
                    record.name = agv.name
                    record.last_seen = datetime.now()
                else:
                    session.add(AgvRecord(
                        id=agv.id,
                        name=agv.name,
                        vehicle_type="standard",
                        state=agv.model_dump(mode="json"),
                    ))
                await session.commit()
        except Exception as e:
            logger.debug("Failed to save AGV to DB: %s", e)

    async def _save_task_to_db(self, task: AgvTask):
        """Upsert task to DB."""
        try:
            from ..db.database import _check_db_available, get_session_factory
            if not await _check_db_available():
                return
            from ..db.models import TaskRecord
            from sqlalchemy import select

            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(select(TaskRecord).where(TaskRecord.id == task.id))
                record = result.scalars().first()
                if record:
                    record.task_data = task.model_dump(mode="json")
                    record.status = task.status
                    record.assigned_agv = task.assigned_agv
                else:
                    session.add(TaskRecord(
                        id=task.id,
                        task_type="agv",
                        task_data=task.model_dump(mode="json"),
                        status=task.status,
                        assigned_agv=task.assigned_agv,
                    ))
                await session.commit()
        except Exception as e:
            logger.debug("Failed to save task to DB: %s", e)

    # ---- Schedule Operations ----

    async def run_scheduling_async(
        self,
        tasks: Optional[List[AgvTask]] = None,
        agvs: Optional[List[AgvStatus]] = None,
        conveyor_tasks: Optional[List[Dict]] = None,
        version: Optional[str] = None,
    ) -> ScheduleResult:
        """Run scheduling pipeline (async, supports V1/V2)."""
        from ..services.map_service import map_service

        use_version = version or self._active_version
        graph = await map_service.get_graph_async()
        nodes = graph.nodes
        edges = graph.edges

        use_tasks = tasks or self._tasks
        use_agvs = agvs or self._agvs
        use_conveyor_tasks = self._conveyor_tasks
        if conveyor_tasks:
            use_conveyor_tasks = [ConveyorTask(**ct) for ct in conveyor_tasks]

        result: ScheduleResult
        if use_version == "v2":
            result = self._run_v2(nodes, edges, use_tasks, use_agvs, use_conveyor_tasks, self._conveyor_segments)
        else:
            result = self._run_v1(nodes, edges, use_tasks, use_agvs, use_conveyor_tasks, self._conveyor_segments)

        # Assign ID
        result.id = str(uuid.uuid4())[:8]

        # Store in memory + DB
        self._results[result.id] = result
        await self._save_result_to_db(result, use_version)

        # Cache recent result in Redis
        await redis_service.cache_set_json(
            redis_service.schedule_result_key(result.id),
            result.model_dump(mode="json"),
            ttl=3600,
        )

        # Update AGV statuses + task statuses
        assigned_task_ids = set()
        for assignment in result.assignments:
            assigned_task_ids.add(assignment.task_id)
            for agv in self._agvs:
                if agv.id == assignment.agv_id:
                    agv.status = "executing"
                    agv.current_task = assignment.task_id
                    agv.path = assignment.path
                    await self._save_agv_to_db(agv)

        # Update task status: assigned tasks -> completed
        for task in self._tasks:
            if task.id in assigned_task_ids and task.status == AgvTaskStatus.PENDING:
                task.status = AgvTaskStatus.COMPLETED
                task.assigned_agv = next(
                    (a.agv_id for a in result.assignments if a.task_id == task.id), None
                )
                task.actual_start = datetime.now()
                task.actual_end = datetime.now()

        return result

    def _run_v1(self, nodes, edges, tasks, agvs, conveyor_tasks, conveyor_segments) -> ScheduleResult:
        """Run V1 hybrid scheduling (ACO+SA+NLP)."""
        from ..algorithms.hybrid import run_hybrid_schedule
        return run_hybrid_schedule(
            nodes=nodes, edges=edges, tasks=tasks, agvs=agvs,
            conveyor_tasks=conveyor_tasks, conveyor_segments=conveyor_segments,
            config=self._config,
        )

    def _run_v2(self, nodes, edges, tasks, agvs, conveyor_tasks, conveyor_segments) -> ScheduleResult:
        """Run V2 hybrid scheduling (MIP + A*+TW + SIPP + Traffic Control)."""
        try:
            from ..algorithms.v2.hybrid_orchestrator.orchestrator import (
                HybridOrchestratorV2, OrchestratorConfig, OrchestratorMode,
            )
            config = OrchestratorConfig(mode=OrchestratorMode.REALTIME)
            orchestrator = HybridOrchestratorV2(config=config)
            result = orchestrator.schedule(
                nodes=nodes, edges=edges, tasks=tasks, agvs=agvs,
                conveyor_tasks=conveyor_tasks, conveyor_segments=conveyor_segments,
            )
            logger.info("V2 scheduling: %d assignments, %.1fms",
                       len(result.assignments), result.algorithm_runtime_ms)
            return result
        except Exception as e:
            logger.warning("V2 scheduling failed (%s), falling back to V1", e)
            return self._run_v1(nodes, edges, tasks, agvs, conveyor_tasks, conveyor_segments)

    # ---- Synchronous API (backward compat) ----

    def run_scheduling(
        self,
        tasks: Optional[List[AgvTask]] = None,
        agvs: Optional[List[AgvStatus]] = None,
        conveyor_tasks: Optional[List[Dict]] = None,
    ) -> ScheduleResult:
        """Run scheduling (sync, V1 only, for backward compat)."""
        from ..services.map_service import map_service
        graph = map_service.get_graph()
        use_tasks = tasks or self._tasks
        use_agvs = agvs or self._agvs
        use_conveyor_tasks = self._conveyor_tasks
        if conveyor_tasks:
            use_conveyor_tasks = [ConveyorTask(**ct) for ct in conveyor_tasks]

        result = self._run_v1(
            graph.nodes, graph.edges, use_tasks, use_agvs,
            use_conveyor_tasks, self._conveyor_segments,
        )
        result.id = str(uuid.uuid4())[:8]
        self._results[result.id] = result

        for assignment in result.assignments:
            for agv in self._agvs:
                if agv.id == assignment.agv_id:
                    agv.status = "executing"
                    agv.current_task = assignment.task_id
                    agv.path = assignment.path
        return result

    async def get_result_async(self, result_id: str) -> Optional[ScheduleResult]:
        """Get result by ID (async, checks Redis then DB then memory)."""
        # Redis cache
        cached = await redis_service.cache_get_json(redis_service.schedule_result_key(result_id))
        if cached:
            return ScheduleResult.model_validate(cached)

        # Memory
        if result_id in self._results:
            return self._results[result_id]

        # DB
        try:
            from ..db.database import _check_db_available, get_session_factory
            if await _check_db_available():
                from ..db.models import ScheduleResultRecord
                from sqlalchemy import select
                factory = get_session_factory()
                async with factory() as session:
                    result = await session.execute(
                        select(ScheduleResultRecord).where(ScheduleResultRecord.id == result_id)
                    )
                    record = result.scalars().first()
                    if record:
                        return ScheduleResult.model_validate(record.result_data)
        except Exception as e:
            logger.debug("Failed to load result from DB: %s", e)
        return None

    def get_result(self, result_id: str) -> Optional[ScheduleResult]:
        """Get result by ID (sync, memory only)."""
        return self._results.get(result_id)

    # ---- AGV Operations ----

    async def get_agvs_async(self) -> List[AgvStatus]:
        return copy.deepcopy(self._agvs)

    async def update_agv_async(self, agv_id: str, updates: dict) -> Optional[AgvStatus]:
        for agv in self._agvs:
            if agv.id == agv_id:
                for key, value in updates.items():
                    if hasattr(agv, key):
                        setattr(agv, key, value)
                await self._save_agv_to_db(agv)
                # Publish update
                await redis_service.publish(
                    redis_service.CHANNEL_AGV_UPDATES,
                    {"type": "agv_update", "data": agv.model_dump(mode="json")},
                )
                return agv
        return None

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

    async def get_tasks_async(self) -> List[AgvTask]:
        return copy.deepcopy(self._tasks)

    async def add_tasks_async(self, tasks: List[AgvTask]) -> List[AgvTask]:
        for task in tasks:
            if not task.id:
                task.id = f"T{len(self._tasks) + 1:03d}"
            self._tasks.append(task)
            await self._save_task_to_db(task)
        return tasks

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

    async def get_config_async(self) -> AlgorithmConfig:
        return copy.deepcopy(self._config)

    async def update_config_async(self, config: AlgorithmConfig) -> AlgorithmConfig:
        self._config = config
        # Persist to DB
        try:
            from ..db.database import _check_db_available, get_session_factory
            if await _check_db_available():
                from ..db.models import AlgorithmConfigRecord
                from sqlalchemy import select, update
                factory = get_session_factory()
                async with factory() as session:
                    # Deactivate all, then insert new
                    await session.execute(
                        update(AlgorithmConfigRecord).values(is_active=False)
                    )
                    session.add(AlgorithmConfigRecord(
                        version=self._active_version,
                        config_data=config.model_dump(mode="json"),
                        is_active=True,
                    ))
                    await session.commit()
        except Exception as e:
            logger.debug("Failed to save config to DB: %s", e)
        return self._config

    async def get_active_version_async(self) -> str:
        return self._active_version

    async def set_active_version_async(self, version: str) -> str:
        if version not in ("v1", "v2"):
            raise ValueError(f"Invalid version: {version}")
        self._active_version = version
        try:
            from ..db.database import _check_db_available, get_session_factory
            if await _check_db_available():
                from ..db.models import AlgorithmConfigRecord
                from sqlalchemy import select, update
                factory = get_session_factory()
                async with factory() as session:
                    await session.execute(
                        update(AlgorithmConfigRecord).values(is_active=False)
                    )
                    result = await session.execute(
                        select(AlgorithmConfigRecord).where(AlgorithmConfigRecord.version == version)
                    )
                    record = result.scalars().first()
                    if record:
                        record.is_active = True
                    else:
                        session.add(AlgorithmConfigRecord(
                            version=version,
                            config_data=AlgorithmConfig().model_dump(mode="json"),
                            is_active=True,
                        ))
                    await session.commit()
        except Exception as e:
            logger.debug("Failed to set active version in DB: %s", e)
        return version

    def get_config(self) -> AlgorithmConfig:
        return copy.deepcopy(self._config)

    def update_config(self, config: AlgorithmConfig) -> AlgorithmConfig:
        self._config = config
        return self._config

    # ---- Metrics ----

    async def get_metrics_async(self) -> dict:
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
            "active_algorithm": self._active_version,
        }

    def get_metrics(self) -> dict:
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

    # ---- Reset ----

    def reset_state(self):
        """Reset all data to initial state, clearing all execution traces."""
        logger.info("Resetting system state to initial conditions")
        # Clear schedule results
        self._results.clear()
        # Re-initialize demo data (fresh AGVs + fresh tasks)
        self._init_demo_data()
        logger.info("System reset complete: %d AGVs, %d tasks restored",
                     len(self._agvs), len(self._tasks))


# Singleton instance
schedule_service = ScheduleService()
