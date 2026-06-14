"""
Analytics & Dashboard API Routes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..services.analytics_service import analytics_service, AlertLevel, AlertType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/analytics", tags=["V2 - 数据分析"])


@router.get("/dashboard")
async def get_dashboard():
    """Get complete dashboard summary (all dimensions)."""
    return analytics_service.get_dashboard_summary()


@router.get("/tasks")
async def get_task_stats(hours: int = Query(24, ge=1, le=168)):
    """Get task statistics."""
    return analytics_service.get_task_statistics(hours)


@router.get("/efficiency")
async def get_efficiency_stats(hours: int = Query(24, ge=1, le=168)):
    """Get efficiency statistics."""
    return analytics_service.get_efficiency_statistics(hours)


@router.get("/battery")
async def get_battery_stats():
    """Get battery/charging statistics."""
    return analytics_service.get_battery_statistics()


@router.get("/alerts")
async def get_alert_stats(hours: int = Query(24, ge=1, le=168)):
    """Get alert statistics."""
    return analytics_service.get_alert_statistics(hours)


@router.get("/alerts/recent")
async def get_recent_alerts(limit: int = Query(50, ge=1, le=200)):
    """Get recent alerts."""
    return analytics_service.get_recent_alerts(limit)


@router.put("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Acknowledge an alert."""
    if not analytics_service.acknowledge_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "acknowledged", "alert_id": alert_id}


@router.put("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    """Resolve an alert."""
    if not analytics_service.resolve_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "resolved", "alert_id": alert_id}


@router.post("/alerts")
async def raise_alert(
    level: str = Query(...),
    alert_type: str = Query(...),
    message: str = Query(...),
    source: str = Query(""),
):
    """Manually raise an alert."""
    try:
        al = AlertLevel(level)
        at = AlertType(alert_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    alert = analytics_service.raise_alert(al, at, message, source)
    return {
        "id": alert.id,
        "level": alert.level.value,
        "type": alert.type.value,
        "message": alert.message,
        "timestamp": alert.timestamp.isoformat(),
    }
