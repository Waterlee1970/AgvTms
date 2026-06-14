"""
Prometheus Metrics Endpoint.

Exposes metrics in Prometheus format for Grafana dashboards.
Covers the 4 golden signals: latency, traffic, errors, saturation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Response

from ..services.analytics_service import analytics_service
from ..services.schedule_service import schedule_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["监控指标"])

# Metrics counters
_request_count: Dict[str, int] = {}
_request_latency: Dict[str, list] = {}
_start_time = time.time()


def record_request(endpoint: str, latency_ms: float, status: int):
    """Record a request for metrics."""
    key = f"{endpoint}:{status}"
    _request_count[key] = _request_count.get(key, 0) + 1
    if endpoint not in _request_latency:
        _request_latency[endpoint] = []
    _request_latency[endpoint].append(latency_ms)
    # Keep only last 1000
    if len(_request_latency[endpoint]) > 1000:
        _request_latency[endpoint] = _request_latency[endpoint][-1000:]


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus-format metrics endpoint."""
    metrics = []

    # ---- Uptime ----
    uptime = time.time() - _start_time
    metrics.append(f"# HELP agvtms_uptime_seconds Application uptime")
    metrics.append(f"# TYPE agvtms_uptime_seconds counter")
    metrics.append(f'agvtms_uptime_seconds {uptime:.2f}')

    # ---- Request metrics ----
    metrics.append(f"# HELP agvtms_http_requests_total Total HTTP requests")
    metrics.append(f"# TYPE agvtms_http_requests_total counter")
    for key, count in _request_count.items():
        endpoint, status = key.rsplit(":", 1)
        metrics.append(f'agvtms_http_requests_total{{endpoint="{endpoint}",status="{status}"}} {count}')

    # ---- Request latency ----
    metrics.append(f"# HELP agvtms_http_request_duration_ms Request latency in ms")
    metrics.append(f"# TYPE agvtms_http_request_duration_ms summary")
    for endpoint, latencies in _request_latency.items():
        if latencies:
            avg = sum(latencies) / len(latencies)
            p50 = sorted(latencies)[len(latencies) // 2]
            p99 = sorted(latencies)[int(len(latencies) * 0.99)]
            metrics.append(f'agvtms_http_request_duration_ms{{endpoint="{endpoint}",quantile="0.5"}} {p50:.2f}')
            metrics.append(f'agvtms_http_request_duration_ms{{endpoint="{endpoint}",quantile="0.99"}} {p99:.2f}')
            metrics.append(f'agvtms_http_request_duration_ms{{endpoint="{endpoint}",quantile="avg"}} {avg:.2f}')

    # ---- AGV metrics ----
    try:
        agvs = await schedule_service.get_agvs_async()
        total_agvs = len(agvs)
        active_agvs = sum(1 for a in agvs if a.status != "idle")
        idle_agvs = sum(1 for a in agvs if a.status == "idle")
        avg_battery = sum(a.battery for a in agvs) / max(total_agvs, 1)

        metrics.append(f"# HELP agvtms_agvs_total Total AGVs")
        metrics.append(f"# TYPE agvtms_agvs_total gauge")
        metrics.append(f"agvtms_agvs_total {total_agvs}")

        metrics.append(f"# HELP agvtms_agvs_active Active AGVs")
        metrics.append(f"# TYPE agvtms_agvs_active gauge")
        metrics.append(f"agvtms_agvs_active {active_agvs}")

        metrics.append(f"# HELP agvtms_agvs_idle Idle AGVs")
        metrics.append(f"# TYPE agvtms_agvs_idle gauge")
        metrics.append(f"agvtms_agvs_idle {idle_agvs}")

        metrics.append(f"# HELP agvtms_agv_battery_avg Average AGV battery level")
        metrics.append(f"# TYPE agvtms_agv_battery_avg gauge")
        metrics.append(f"agvtms_agv_battery_avg {avg_battery:.2f}")

        # Per-AGV battery
        for agv in agvs:
            metrics.append(f'agvtms_agv_battery{{agv_id="{agv.id}"}} {agv.battery:.2f}')
    except Exception as e:
        logger.debug("AGV metrics error: %s", e)

    # ---- Task metrics ----
    try:
        tasks = await schedule_service.get_tasks_async()
        total_tasks = len(tasks)
        pending = sum(1 for t in tasks if t.status == "pending")
        completed = sum(1 for t in tasks if t.status == "completed")

        metrics.append(f"# HELP agvtms_tasks_total Total tasks")
        metrics.append(f"# TYPE agvtms_tasks_total gauge")
        metrics.append(f"agvtms_tasks_total {total_tasks}")

        metrics.append(f"# HELP agvtms_tasks_pending Pending tasks")
        metrics.append(f"# TYPE agvtms_tasks_pending gauge")
        metrics.append(f"agvtms_tasks_pending {pending}")

        metrics.append(f"# HELP agvtms_tasks_completed Completed tasks")
        metrics.append(f"# TYPE agvtms_tasks_completed counter")
        metrics.append(f"agvtms_tasks_completed {completed}")
    except Exception as e:
        logger.debug("Task metrics error: %s", e)

    # ---- Schedule metrics ----
    try:
        sched_metrics = await schedule_service.get_metrics_async()
        total_schedules = sched_metrics.get("total_schedules_run", 0)

        metrics.append(f"# HELP agvtms_schedules_total Total schedules run")
        metrics.append(f"# TYPE agvtms_schedules_total counter")
        metrics.append(f"agvtms_schedules_total {total_schedules}")
    except Exception as e:
        logger.debug("Schedule metrics error: %s", e)

    # ---- Alert metrics ----
    try:
        alert_stats = analytics_service.get_alert_statistics()
        metrics.append(f"# HELP agvtms_alerts_total Total alerts")
        metrics.append(f"# TYPE agvtms_alerts_total counter")
        metrics.append(f"agvtms_alerts_total {alert_stats.get('total', 0)}")

        for level, count in alert_stats.get("by_level", {}).items():
            metrics.append(f'agvtms_alerts_by_level{{level="{level}"}} {count}')
    except Exception as e:
        logger.debug("Alert metrics error: %s", e)

    # ---- Algorithm version ----
    try:
        version = await schedule_service.get_active_version_async()
        metrics.append(f"# HELP agvtms_active_algorithm Active algorithm version (1=v1, 2=v2)")
        metrics.append(f"# TYPE agvtms_active_algorithm gauge")
        metrics.append(f'agvtms_active_algorithm{{version="{version}"}} {2 if version == "v2" else 1}')
    except Exception as e:
        logger.debug("Algorithm metrics error: %s", e)

    content = "\n".join(metrics) + "\n"
    return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/api/v2/system/info")
async def system_info():
    """Get system information for monitoring."""
    import sys
    import platform

    return {
        "name": "AGV-TMS",
        "version": "2.0.0",
        "python_version": sys.version,
        "platform": platform.platform(),
        "uptime_seconds": time.time() - _start_time,
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "metrics": "/metrics",
            "v2_schedule": "/api/v2/schedule/run",
            "flows": "/api/v2/flows/",
            "vda5050": "/api/v2/vda5050/info",
            "vehicles": "/api/v2/vehicles/types",
            "analytics": "/api/v2/analytics/dashboard",
            "simulation": "/api/v2/simulation/run",
        },
    }
