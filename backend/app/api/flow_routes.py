"""
Flow Orchestration API Routes.

Endpoints for managing and executing flow definitions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from ..orchestration.flow_engine import (
    FlowDefinition,
    FlowExecutionState,
    flow_engine,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/flows", tags=["V2 - 流程编排"])


@router.get("/", response_model=List[Dict[str, Any]])
async def list_flows():
    """List all registered flow templates."""
    return flow_engine.list_flows()


@router.get("/{flow_id}", response_model=FlowDefinition)
async def get_flow(flow_id: str):
    """Get a flow definition by ID."""
    flow = flow_engine.get_flow(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow


@router.post("/", response_model=Dict[str, Any])
async def create_flow(flow: FlowDefinition):
    """Register a new flow definition."""
    flow_id = flow_engine.register_flow(flow)
    return {"flow_id": flow_id, "message": "Flow registered"}


@router.post("/{flow_id}/execute", response_model=FlowExecutionState)
async def execute_flow(
    flow_id: str,
    variables: Optional[Dict[str, Any]] = None,
):
    """Execute a flow by ID."""
    try:
        state = await flow_engine.execute(flow_id, variables)
        return state
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Flow execution failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/execution/{execution_id}", response_model=FlowExecutionState)
async def get_execution(execution_id: str):
    """Get execution state by ID."""
    state = flow_engine.get_execution(execution_id)
    if not state:
        raise HTTPException(status_code=404, detail="Execution not found")
    return state
