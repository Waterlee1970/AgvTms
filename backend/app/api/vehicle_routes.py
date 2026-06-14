"""
Multi-Vehicle Type & Traffic Control API Routes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from ..models.vehicle_types import vehicle_type_manager, VehicleType, VehicleCapability
from ..algorithms.v2.traffic_control.world_model import WorldModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["V2 - 多车型与交通"])

# Singleton world model
_world_model = WorldModel()


# =============================================================================
# Vehicle Type Endpoints
# =============================================================================

@router.get("/vehicles/types", response_model=List[Dict[str, Any]])
async def list_vehicle_types():
    """List all registered vehicle types."""
    return vehicle_type_manager.list_types()


@router.get("/vehicles/types/{vtype}")
async def get_vehicle_type(vtype: str):
    """Get details of a specific vehicle type."""
    try:
        vt = VehicleType(vtype)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown vehicle type: {vtype}")
    cap = vehicle_type_manager.get_capability(vt)
    if not cap:
        raise HTTPException(status_code=404, detail="Vehicle type not found")
    return {
        "type": cap.vehicle_type.value,
        "max_load_kg": cap.max_load_kg,
        "max_speed_ms": cap.max_speed_ms,
        "lifting_height_m": cap.lifting_height_m,
        "turning_radius_m": cap.turning_radius_m,
        "width_m": cap.width_m,
        "length_m": cap.length_m,
        "supports_docking": cap.supports_docking,
        "supports_conveyor": cap.supports_conveyor,
        "navigation_methods": [n.value for n in cap.navigation_methods],
    }


@router.post("/vehicles/types/match", response_model=Dict[str, Any])
async def match_vehicle_type(requirements: Dict[str, Any]):
    """Find the best vehicle type for given task requirements."""
    compatible = vehicle_type_manager.find_compatible_types(requirements)
    best = vehicle_type_manager.best_match(requirements)
    scores = {
        vt.value: vehicle_type_manager.score_match(vt, requirements)
        for vt in compatible
    }
    return {
        "best_match": best.value if best else None,
        "compatible_types": [vt.value for vt in compatible],
        "scores": scores,
    }


# =============================================================================
# World Model / Traffic Control Endpoints
# =============================================================================

@router.get("/traffic/world-model/stats")
async def get_world_model_stats():
    """Get world model traffic statistics."""
    return _world_model.get_stats()


@router.get("/traffic/world-model/zones")
async def list_zones():
    """List all traffic zones."""
    return [
        {
            "zone_id": z.zone_id,
            "zone_type": z.zone_type.value,
            "node_ids": z.node_ids,
            "max_capacity": z.max_capacity,
            "current_occupants": list(z.current_occupants),
            "wait_queue_length": len(z.wait_queue),
            "lock_type": z.lock_type.value,
        }
        for z in _world_model._zones.values()
    ]


@router.get("/traffic/world-model/congestion")
async def get_congestion():
    """Get congestion status for all zones."""
    return {
        "congested_zones": _world_model.get_congested_zones(),
        "utilization": _world_model.get_zone_utilization(),
        "avg_utilization": sum(_world_model.get_zone_utilization().values()) / max(len(_world_model._zones), 1),
    }


@router.post("/traffic/world-model/discover")
async def discover_zones():
    """Auto-discover traffic zones from the current map."""
    from ..services.map_service import map_service
    graph = await map_service.get_graph_async()
    _world_model._zones.clear()
    _world_model._node_to_zone.clear()
    _world_model.auto_discover_zones(graph.nodes, graph.edges)
    return {
        "zones_discovered": len(_world_model._zones),
        "stats": _world_model.get_stats(),
    }


@router.get("/traffic/world-model/deadlock/check")
async def check_deadlock():
    """Check for deadlocks in the traffic system."""
    cycle = _world_model.detect_deadlock()
    if cycle:
        return {"deadlock_detected": True, "cycle": cycle}
    return {"deadlock_detected": False, "cycle": None}
