"""
Analytics Service — Multi-dimensional data analysis.

Provides aggregated statistics for dashboard display:
- Task statistics (by time, status, type)
- Efficiency metrics (AGV utilization, throughput)
- Battery/charging statistics
- Alert statistics (by level, type, time)
- Historical trends
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertType(str, Enum):
    AGV_OFFLINE = "agv_offline"
    LOW_BATTERY = "low_battery"
    CRITICAL_BATTERY = "critical_battery"
    CONGESTION = "congestion"
    DEADLOCK = "deadlock"
    TASK_TIMEOUT = "task_timeout"
    AGV_ERROR = "agv_error"
    SYSTEM_ERROR = "system_error"


class Alert(BaseModel):
    """A system alert."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    level: AlertLevel
    type: AlertType
    message: str
    source: str = ""  # AGV ID, zone ID, etc.
    timestamp: datetime = Field(default_factory=datetime.now)
    acknowledged: bool = False
    resolved: bool = False
    resolved_at: Optional[datetime] = None


class AnalyticsService:
    """
    Multi-dimensional analytics service.

    Maintains rolling windows of metrics for real-time dashboard display.
    """

    def __init__(self, history_hours: int = 24):
        self._history_hours = history_hours
        self._task_history: deque = deque(maxlen=10000)  # (timestamp, task_dict)
        self._schedule_history: deque = deque(maxlen=1000)  # (timestamp, result_dict)
        self._agv_metrics_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._alerts: deque = deque(maxlen=5000)
        self._alert_rules: List[Dict[str, Any]] = self._default_alert_rules()

    def _default_alert_rules(self) -> List[Dict[str, Any]]:
        """Default alert rules."""
        return [
            {"type": AlertType.LOW_BATTERY, "condition": "battery < 25", "level": AlertLevel.WARNING},
            {"type": AlertType.CRITICAL_BATTERY, "condition": "battery < 15", "level": AlertLevel.CRITICAL},
            {"type": AlertType.AGV_OFFLINE, "condition": "connection == 'OFFLINE'", "level": AlertLevel.ERROR},
            {"type": AlertType.CONGESTION, "condition": "zone_utilization > 0.9", "level": AlertLevel.WARNING},
            {"type": AlertType.DEADLOCK, "condition": "deadlock_detected", "level": AlertLevel.CRITICAL},
        ]

    # ---- Data Ingestion ----

    def record_task(self, task: Dict[str, Any]):
        """Record a task event."""
        self._task_history.append({
            "timestamp": datetime.now(),
            "task_id": task.get("id"),
            "status": task.get("status"),
            "priority": task.get("priority"),
            "assigned_agv": task.get("assigned_agv"),
        })

    def record_schedule(self, result: Dict[str, Any], version: str = "v2"):
        """Record a scheduling result."""
        self._schedule_history.append({
            "timestamp": datetime.now(),
            "result_id": result.get("id"),
            "makespan": result.get("makespan", 0),
            "total_cost": result.get("total_cost", 0),
            "runtime_ms": result.get("algorithm_runtime_ms", 0),
            "assignments": len(result.get("assignments", [])),
            "version": version,
            "metrics": result.get("metrics", {}),
        })

    def record_agv_metrics(self, agv_id: str, metrics: Dict[str, Any]):
        """Record AGV metrics snapshot."""
        metrics["timestamp"] = datetime.now()
        self._agv_metrics_history[agv_id].append(metrics)

        # Check alert rules
        self._check_agv_alerts(agv_id, metrics)

    def raise_alert(
        self, level: AlertLevel, alert_type: AlertType,
        message: str, source: str = "",
    ) -> Alert:
        """Raise a new alert."""
        alert = Alert(
            level=level, type=alert_type,
            message=message, source=source,
        )
        self._alerts.append(alert)
        logger.warning("Alert [%s] %s: %s (source: %s)",
                       alert.level.value, alert.type.value, alert.message, alert.source)
        return alert

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False

    def resolve_alert(self, alert_id: str) -> bool:
        """Resolve an alert."""
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.resolved = True
                alert.resolved_at = datetime.now()
                return True
        return False

    def _check_agv_alerts(self, agv_id: str, metrics: Dict[str, Any]):
        """Check AGV metrics against alert rules."""
        battery = metrics.get("battery", 100)
        connection = metrics.get("connection", "ONLINE")

        if battery < 15:
            self.raise_alert(
                AlertLevel.CRITICAL, AlertType.CRITICAL_BATTERY,
                f"AGV {agv_id} 电量极低: {battery:.0f}%", agv_id,
            )
        elif battery < 25:
            self.raise_alert(
                AlertLevel.WARNING, AlertType.LOW_BATTERY,
                f"AGV {agv_id} 电量低: {battery:.0f}%", agv_id,
            )

        if connection == "OFFLINE":
            self.raise_alert(
                AlertLevel.ERROR, AlertType.AGV_OFFLINE,
                f"AGV {agv_id} 离线", agv_id,
            )

    # ---- Statistics Queries ----

    def get_task_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get task statistics for the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [t for t in self._task_history if t["timestamp"] > cutoff]

        by_status = defaultdict(int)
        by_priority = defaultdict(int)
        by_hour = defaultdict(int)

        for t in recent:
            by_status[t["status"]] += 1
            by_priority[t.get("priority", 0)] += 1
            by_hour[t["timestamp"].hour] += 1

        return {
            "total": len(recent),
            "by_status": dict(by_status),
            "by_priority": dict(by_priority),
            "by_hour": dict(by_hour),
            "completion_rate": (
                by_status.get("completed", 0) / max(len(recent), 1) * 100
            ),
        }

    def get_efficiency_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get efficiency statistics."""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [s for s in self._schedule_history if s["timestamp"] > cutoff]

        if not recent:
            return {"total_schedules": 0}

        avg_makespan = sum(s["makespan"] for s in recent) / len(recent)
        avg_runtime = sum(s["runtime_ms"] for s in recent) / len(recent)
        avg_utilization = (
            sum(s.get("metrics", {}).get("agv_utilization", 0) for s in recent) / len(recent)
        )

        return {
            "total_schedules": len(recent),
            "avg_makespan": round(avg_makespan, 2),
            "avg_runtime_ms": round(avg_runtime, 2),
            "avg_utilization": round(avg_utilization, 4),
            "avg_assignments": round(sum(s["assignments"] for s in recent) / len(recent), 1),
            "by_version": dict(defaultdict(int, **{
                v: sum(1 for s in recent if s["version"] == v)
                for v in set(s["version"] for s in recent)
            })),
        }

    def get_battery_statistics(self) -> Dict[str, Any]:
        """Get battery/charging statistics for all AGVs."""
        agv_batteries = {}
        for agv_id, history in self._agv_metrics_history.items():
            if history:
                latest = history[-1]
                agv_batteries[agv_id] = {
                    "battery": latest.get("battery", 100),
                    "status": latest.get("status", "idle"),
                    "charging": latest.get("charging", False),
                }

        if not agv_batteries:
            return {"total_agvs": 0}

        batteries = [v["battery"] for v in agv_batteries.values()]
        charging = sum(1 for v in agv_batteries.values() if v["charging"])
        low_battery = sum(1 for b in batteries if b < 25)

        return {
            "total_agvs": len(agv_batteries),
            "avg_battery": round(sum(batteries) / len(batteries), 1),
            "min_battery": min(batteries),
            "max_battery": max(batteries),
            "charging_count": charging,
            "low_battery_count": low_battery,
            "agvs": agv_batteries,
        }

    def get_alert_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get alert statistics."""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [a for a in self._alerts if a.timestamp > cutoff]

        by_level = defaultdict(int)
        by_type = defaultdict(int)
        unacknowledged = 0
        unresolved = 0

        for a in recent:
            by_level[a.level.value] += 1
            by_type[a.type.value] += 1
            if not a.acknowledged:
                unacknowledged += 1
            if not a.resolved:
                unresolved += 1

        return {
            "total": len(recent),
            "by_level": dict(by_level),
            "by_type": dict(by_type),
            "unacknowledged": unacknowledged,
            "unresolved": unresolved,
        }

    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        recent = list(self._alerts)[-limit:]
        recent.reverse()
        return [
            {
                "id": a.id,
                "level": a.level.value,
                "type": a.type.value,
                "message": a.message,
                "source": a.source,
                "timestamp": a.timestamp.isoformat(),
                "acknowledged": a.acknowledged,
                "resolved": a.resolved,
            }
            for a in recent
        ]

    def get_dashboard_summary(self) -> Dict[str, Any]:
        """Get complete dashboard summary (all dimensions)."""
        return {
            "tasks": self.get_task_statistics(),
            "efficiency": self.get_efficiency_statistics(),
            "battery": self.get_battery_statistics(),
            "alerts": self.get_alert_statistics(),
            "recent_alerts": self.get_recent_alerts(10),
            "generated_at": datetime.now().isoformat(),
        }


# Singleton
analytics_service = AnalyticsService()
