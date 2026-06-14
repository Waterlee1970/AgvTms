"""
VDA5050 Protocol API Routes.

Endpoints for VDA5050 AGV management and simulation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from ..protocols.vda5050 import (
    Vda5050Adapter,
    Vda5050Order,
    Vda5050State,
    Vda5050InstantAction,
    Vda5050Topics,
    vda5050_adapter,
)
from ..protocols.agv_simulator import fleet_simulator, AgvSimulator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/vda5050", tags=["V2 - VDA5050协议"])


# =============================================================================
# AGV Simulator Management
# =============================================================================

@router.get("/agvs", response_model=List[Dict[str, Any]])
async def list_simulated_agvs():
    """List all simulated VDA5050 AGVs."""
    states = fleet_simulator.get_all_states()
    return [
        {
            "serial": serial,
            "manufacturer": state.manufacturer,
            "connection": state.connection,
            "position": state.agv_position,
            "battery": (state.battery_state or {}).get("batteryCharge", 100),
            "driving": state.driving,
            "paused": state.paused,
            "last_node": state.last_node_id,
        }
        for serial, state in states.items()
    ]


@router.post("/agvs", response_model=Dict[str, Any])
async def create_simulated_agv(
    serial: str = "AGV001",
    start_node: str = "N_P00",
    start_x: float = 0.0,
    start_y: float = 0.0,
    speed: float = 1.5,
    battery: float = 100.0,
):
    """Create a new simulated VDA5050 AGV."""
    if fleet_simulator.get_agv(serial):
        raise HTTPException(status_code=409, detail=f"AGV {serial} already exists")
    sim = fleet_simulator.add_agv(
        serial=serial, start_node=start_node, start_x=start_x, start_y=start_y,
        speed=speed, battery=battery,
    )
    await sim.start()
    return {"serial": serial, "status": "started", "node": start_node}


@router.post("/agvs/{serial}/order", response_model=Dict[str, Any])
async def send_order_to_agv(serial: str, order: Vda5050Order):
    """Send a VDA5050 Order to a simulated AGV."""
    sim = fleet_simulator.get_agv(serial)
    if not sim:
        raise HTTPException(status_code=404, detail=f"AGV {serial} not found")

    import asyncio
    asyncio.create_task(sim.process_order(order))
    return {"serial": serial, "order_id": order.order_id, "status": "accepted"}


@router.post("/agvs/{serial}/instant-action", response_model=Dict[str, Any])
async def send_instant_action(serial: str, action: Vda5050InstantAction):
    """Send an instant action (emergency stop, cancel) to an AGV."""
    sim = fleet_simulator.get_agv(serial)
    if not sim:
        raise HTTPException(status_code=404, detail=f"AGV {serial} not found")
    await sim.handle_instant_action(action)
    return {"serial": serial, "action": action.instant_action_type, "status": "executed"}


@router.get("/agvs/{serial}/state", response_model=Vda5050State)
async def get_agv_state(serial: str):
    """Get the current VDA5050 State of an AGV."""
    sim = fleet_simulator.get_agv(serial)
    if not sim:
        raise HTTPException(status_code=404, detail=f"AGV {serial} not found")
    return sim.get_state()


# =============================================================================
# VDA5050 Protocol Info
# =============================================================================

@router.get("/info")
async def get_vda5050_info():
    """Get VDA5050 protocol information and capabilities."""
    return {
        "version": "2.0.0",
        "manufacturer": "AGV-TMS",
        "topics": {
            "order": "uagv/v2/{manufacturer}/{serial}/order",
            "state": "uagv/v2/{manufacturer}/{serial}/state",
            "connection": "uagv/v2/{manufacturer}/{serial}/connection",
            "instantAction": "uagv/v2/{manufacturer}/{serial}/instantAction",
        },
        "message_types": ["Order", "State", "InstantAction", "Connection", "Visualization"],
        "action_types": ["pickPosition", "dropPosition", "initPosition", "charge", "wait", "customAction"],
        "instant_action_types": ["stop", "cancelOrder", "start"],
    }
