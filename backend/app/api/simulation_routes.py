"""
Simulation Engine API Routes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..algorithms.v2.simulation.simulator import simulation_engine, SimulationResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/simulation", tags=["V2 - 仿真引擎"])


@router.post("/run")
async def run_simulation(
    duration: float = Query(3600, description="Simulation duration in seconds"),
    speedup: float = Query(5.0, description="Simulation speed multiplier"),
):
    """
    Run a fast-forward simulation.

    Uses the current fleet and task list to simulate operations
    at 5x-10x real-time speed.
    """
    from ..services.schedule_service import schedule_service

    # Get current state
    agvs = [a.model_dump(mode="json") for a in await schedule_service.get_agvs_async()]
    tasks = [t.model_dump(mode="json") for t in await schedule_service.get_tasks_async()]

    # Configure simulation
    simulation_engine.speedup = speedup
    simulation_engine.initialize(agvs=agvs, tasks=tasks)

    # Run
    result = await simulation_engine.run(duration=duration)

    return {
        "total_sim_time": result.total_time,
        "real_time": round(result.real_time, 3),
        "actual_speedup": round(result.total_time / max(result.real_time, 0.001), 1),
        "events_processed": result.events_processed,
        "tasks_completed": result.tasks_completed,
        "tasks_failed": result.tasks_failed,
        "avg_utilization": round(result.avg_agv_utilization, 4),
        "timeline_length": len(result.timeline),
    }


@router.get("/result")
async def get_last_result():
    """Get the last simulation result."""
    result = simulation_engine._results
    if not result.events_processed:
        return {"message": "No simulation run yet"}
    return {
        "total_sim_time": result.total_time,
        "real_time": result.real_time,
        "events_processed": result.events_processed,
        "tasks_completed": result.tasks_completed,
        "avg_utilization": result.avg_agv_utilization,
        "timeline": result.timeline[:100],  # First 100 events
        "metrics_history": result.metrics_history,
    }
