"""
FastAPI Routes for AGV Logistics Scheduling System.

REST API + WebSocket endpoints.
Async version with DB persistence and Redis caching.
Maintains backward compatibility with V1 endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..models.schemas import (
    AgvStatus,
    AgvTask,
    AlgorithmConfig,
    ConveyorSegment,
    ConveyorTask,
    MapEdge,
    MapNode,
    ScheduleResult,
)
from ..services.map_service import map_service
from ..services.schedule_service import schedule_service
from ..services import redis_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# =============================================================================
# WebSocket Connection Manager
# =============================================================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.active_connections.remove(conn)
        # Also publish to Redis for multi-process broadcast
        await redis_service.publish(redis_service.CHANNEL_SCHEDULE, message)


manager = ConnectionManager()

# =============================================================================
# Schedule Endpoints (V1 — backward compatible, now async)
# =============================================================================

@router.post("/schedule/run", response_model=ScheduleResult)
async def run_scheduling(
    tasks: Optional[List[AgvTask]] = None,
    agvs: Optional[List[AgvStatus]] = None,
    conveyor_tasks: Optional[List[dict]] = None,
):
    """Trigger hybrid scheduling (uses active algorithm version)."""
    try:
        result = await schedule_service.run_scheduling_async(
            tasks=tasks, agvs=agvs, conveyor_tasks=conveyor_tasks,
        )
        await manager.broadcast({
            "type": "schedule_complete",
            "data": result.model_dump(mode="json"),
        })
        return result
    except Exception as e:
        logger.exception("Schedule run failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedule/reset")
async def reset_system():
    """Reset all data to initial state (clear execution traces)."""
    schedule_service.reset_state()
    await manager.broadcast({
        "type": "system_reset",
        "data": {"message": "System has been reset to initial state"},
    })
    return {"message": "系统已重置，所有数据恢复到初始状态"}


@router.get("/schedule/result/{result_id}", response_model=ScheduleResult)
async def get_schedule_result(result_id: str):
    """Get a schedule result by ID."""
    result = await schedule_service.get_result_async(result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return result

# =============================================================================
# AGV Endpoints
# =============================================================================

@router.get("/agv/status", response_model=List[AgvStatus])
async def get_agv_status():
    """Get all AGV statuses."""
    return await schedule_service.get_agvs_async()


@router.put("/agv/{agv_id}/status")
async def update_agv_status(agv_id: str, updates: dict):
    """Update an AGV's status."""
    agv = await schedule_service.update_agv_async(agv_id, updates)
    if not agv:
        raise HTTPException(status_code=404, detail="AGV not found")
    await manager.broadcast({
        "type": "agv_update",
        "data": agv.model_dump(mode="json"),
    })
    return agv


# =============================================================================
# Map Endpoints
# =============================================================================

@router.get("/map/graph")
async def get_map_graph():
    """Get full map graph (nodes + edges)."""
    graph = await map_service.get_graph_async()
    return graph.model_dump(mode="json")


@router.get("/map/nodes", response_model=List[MapNode])
async def get_map_nodes():
    return await map_service.get_nodes_async()


@router.get("/map/edges", response_model=List[MapEdge])
async def get_map_edges():
    return await map_service.get_edges_async()


@router.post("/map/node", response_model=MapNode)
async def add_map_node(node: MapNode):
    try:
        return await map_service.add_node_async(node)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/map/node/{node_id}", response_model=MapNode)
async def update_map_node(node_id: str, node: MapNode):
    result = await map_service.update_node_async(node_id, node)
    if not result:
        raise HTTPException(status_code=404, detail="Node not found")
    return result


@router.delete("/map/node/{node_id}")
async def delete_map_node(node_id: str):
    if not await map_service.delete_node_async(node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"status": "deleted", "node_id": node_id}


@router.post("/map/edge", response_model=MapEdge)
async def add_map_edge(edge: MapEdge):
    return await map_service.add_edge_async(edge)


@router.delete("/map/edge/{edge_id}")
async def delete_map_edge(edge_id: str):
    if not await map_service.delete_edge_async(edge_id):
        raise HTTPException(status_code=404, detail="Edge not found")
    return {"status": "deleted", "edge_id": edge_id}


# =============================================================================
# Task Endpoints
# =============================================================================

@router.get("/tasks", response_model=List[AgvTask])
async def get_tasks():
    return await schedule_service.get_tasks_async()


@router.post("/tasks", response_model=List[AgvTask])
async def create_tasks(tasks: List[AgvTask]):
    return await schedule_service.add_tasks_async(tasks)


# =============================================================================
# Algorithm Config Endpoints
# =============================================================================

@router.get("/algorithm/config", response_model=AlgorithmConfig)
async def get_algorithm_config():
    return await schedule_service.get_config_async()


@router.put("/algorithm/config", response_model=AlgorithmConfig)
async def update_algorithm_config(config: AlgorithmConfig):
    result = await schedule_service.update_config_async(config)
    await manager.broadcast({
        "type": "config_update",
        "data": result.model_dump(mode="json"),
    })
    return result


# =============================================================================
# Metrics Endpoint
# =============================================================================

@router.get("/metrics")
async def get_metrics():
    return await schedule_service.get_metrics_async()


# =============================================================================
# Conveyor Endpoints
# =============================================================================

@router.get("/conveyor/segments", response_model=List[ConveyorSegment])
async def get_conveyor_segments():
    return schedule_service._conveyor_segments


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@router.websocket("/ws/schedule/live")
async def websocket_schedule_live(websocket: WebSocket):
    """Live schedule updates via WebSocket."""
    await manager.connect(websocket)
    try:
        # Send initial data
        await websocket.send_json({
            "type": "connected",
            "data": {
                "agvs": [a.model_dump(mode="json") for a in await schedule_service.get_agvs_async()],
                "tasks": [t.model_dump(mode="json") for t in await schedule_service.get_tasks_async()],
                "metrics": await schedule_service.get_metrics_async(),
            },
        })
        # Keep connection alive, listen for client messages
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg.get("type") == "request_schedule":
                result = await schedule_service.run_scheduling_async()
                await websocket.send_json({
                    "type": "schedule_complete",
                    "data": result.model_dump(mode="json"),
                })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
