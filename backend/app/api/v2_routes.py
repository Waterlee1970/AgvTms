"""
V2 API Routes — Industrial-grade scheduling endpoints.

Provides:
- POST /api/v2/schedule/run      — V2 scheduling (MIP + A*+TW + SIPP)
- GET  /api/v2/schedule/history  — Historical scheduling results
- GET  /api/v2/schedule/{id}     — Get specific V2 result
- GET  /api/v2/orchestrator/stats — V2 orchestrator statistics
- PUT  /api/algorithm/active     — Switch active algorithm version
- GET  /api/algorithm/active     — Get active algorithm version
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import (
    AgvStatus,
    AgvTask,
    AlgorithmConfig,
    ScheduleResult,
)
from ..services.schedule_service import schedule_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["V2 - 工业级调度"])


# =============================================================================
# V2 Scheduling
# =============================================================================

@router.post("/schedule/run", response_model=ScheduleResult)
async def run_v2_scheduling(
    tasks: Optional[List[AgvTask]] = None,
    agvs: Optional[List[AgvStatus]] = None,
    conveyor_tasks: Optional[List[dict]] = None,
):
    """
    Trigger V2 hybrid scheduling pipeline.

    Pipeline: MIP Task Assignment → A*+TimeWindow Path Planning →
              Traffic Control → Deadlock Detection → Result
    """
    try:
        result = await schedule_service.run_scheduling_async(
            tasks=tasks, agvs=agvs, conveyor_tasks=conveyor_tasks,
            version="v2",
        )
        from ..api.routes import manager
        await manager.broadcast({
            "type": "schedule_complete",
            "data": result.model_dump(mode="json"),
            "version": "v2",
        })
        return result
    except Exception as e:
        logger.exception("V2 schedule run failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schedule/history", response_model=List[Dict[str, Any]])
async def get_schedule_history(limit: int = Query(20, ge=1, le=100)):
    """Get recent scheduling results from database."""
    try:
        from ..db.database import _check_db_available, get_session_factory
        if not await _check_db_available():
            # Fallback to memory
            results = list(schedule_service._results.values())
            return [
                {
                    "id": r.id,
                    "makespan": r.makespan,
                    "total_cost": r.total_cost,
                    "runtime_ms": r.algorithm_runtime_ms,
                    "created_at": r.created_at.isoformat(),
                    "assignments_count": len(r.assignments),
                }
                for r in results[-limit:]
            ]

        from ..db.models import ScheduleResultRecord
        from sqlalchemy import select, desc
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(ScheduleResultRecord)
                .order_by(desc(ScheduleResultRecord.created_at))
                .limit(limit)
            )
            records = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "makespan": r.makespan,
                    "total_cost": r.total_cost,
                    "runtime_ms": r.runtime_ms,
                    "algorithm_version": r.algorithm_version,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "assignments_count": len(r.assignments) if r.assignments else 0,
                }
                for r in records
            ]
    except Exception as e:
        logger.exception("Failed to get schedule history")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schedule/{result_id}", response_model=ScheduleResult)
async def get_v2_schedule_result(result_id: str):
    """Get a specific scheduling result by ID."""
    result = await schedule_service.get_result_async(result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result


# =============================================================================
# Algorithm Version Switching
# =============================================================================

@router.get("/algorithm/active")
async def get_active_algorithm():
    """Get the currently active algorithm version."""
    version = await schedule_service.get_active_version_async()
    return {"active_version": version}


@router.put("/algorithm/active")
async def set_active_algorithm(
    version: str = Query(..., regex="^(v1|v2)$"),
):
    """Switch the active algorithm version (v1 or v2)."""
    try:
        active = await schedule_service.set_active_version_async(version)
        from ..api.routes import manager
        await manager.broadcast({
            "type": "config_update",
            "data": {"active_version": active},
        })
        return {"active_version": active, "message": f"Switched to {active} algorithm"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Orchestrator Stats (V2 specific)
# =============================================================================

@router.get("/orchestrator/stats")
async def get_orchestrator_stats():
    """Get V2 orchestrator statistics (if V2 was last used)."""
    try:
        from ..algorithms.v2.hybrid_orchestrator.orchestrator import HybridOrchestratorV2
        # Return cached stats from last run
        results = list(schedule_service._results.values())
        latest = results[-1] if results else None
        return {
            "available": latest is not None,
            "latest_runtime_ms": latest.algorithm_runtime_ms if latest else 0,
            "latest_makespan": latest.makespan if latest else 0,
            "latest_assignments": len(latest.assignments) if latest else 0,
            "latest_metrics": latest.metrics.model_dump(mode="json") if latest else None,
            "v2_components": [
                "BidirectionalAStar",
                "TimeWindowAStar",
                "SippPlanner",
                "DynamicReplanner",
                "MipTaskAssigner",
                "ZoneManager",
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
