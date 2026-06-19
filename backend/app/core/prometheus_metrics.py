"""
Prometheus 监控指标系统 — AGV-TMS 运维可观测性核心.

对标: 极智嘉RMS Prometheus+Grafana / 海康RCS 自研监控系统

指标体系 (4大维度):

┌─────────────────────────────────────────────────────┐
│                  AGV-TMS Metrics                     │
├──────────┬──────────┬───────────┬────────────────────┤
│ 业务指标 │  系统指标 │  调度指标 │   可靠性指标         │
│ ─────── │ ─────── │ ─────── │ ─────────          │
│ 任务总数 │ CPU/内存 │ 规划耗时  │ 断路器状态           │
│ 任务延迟 │ DB连接数 │ 冲突次数  │ 重试率              │
│ AGV在线 │ Kafka积压│ 死锁检测  │ 降级深度             │
│ 电池电量 │ Redis命中│ 路径长度  │ SLA达标率            │
│ 充电队列 │ GC暂停   │ 拥堵等级  │ 错误率              │
└──────────┴──────────┴───────────┴────────────────────┘

使用:
    from app.core.prometheus_metrics import (
        metrics, TASKS_DISPATCHED, PATH_PLANNING_DURATION,
        record_path_planning, record_task_dispatch,
    )
    
    # 方式1: 直接操作Counter/Histogram/Gauge
    TASKS_DISPATCHED.labels(priority="high").inc()
    PATH_PLANNING_DURATION.observe(elapsed_ms)
    
    # 方式2: 便捷方法 (自动绑定labels)
    metrics.record_dispatch(task_id="T-001", agv_id="AGV-01", priority="high")
    metrics.record_path_planning(elapsed_ms=45.2, nodes_expanded=128, algorithm="astar")

依赖:
    prometheus-client >= 0.17.0
    pip install prometheus-client
"""

from __future__ import annotations

import os
import time
import threading
from typing import Any, Callable, Dict, List, Optional, TypeVar
from functools import wraps as functools_wraps
from contextlib import contextmanager

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, Info, CollectorRegistry,
        generate_latest, CONTENT_TYPE_LATEST,
        multiprocess as mp_util,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # Mock classes for graceful degradation
    class CollectorRegistry:
        def __init__(self): pass

    class Counter:
        def __init__(self, *a, **k): pass
        def labels(self, **kw): return self
        def inc(self, v=1.0): pass
        def _children(self): return []
    class Histogram:
        def __init__(self, *a, **k): pass
        def labels(self, **kw): return self
        def observe(self, v): pass
        def _children(self): return []
    class Gauge:
        def __init__(self, *a, **k): pass
        def labels(self, **kw): return self
        def set(self, v): pass
        def inc(self, v=1.0): pass
        def dec(self, v=1.0): pass
        def set_to_current_time(self): pass
        def track_inprogress(self):
            class _Ctx:
                def __enter__(s): return s
                def __exit__(s, *a): pass
            return _Ctx()
        def _children(self): return []
    class Info:
        def __init__(self, *a, **k): pass
        def info(self, d): pass
    def generate_latest(*a, **k): return b"# Prometheus client not installed\n"
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


# ==================== Registry ====================

_registry = CollectorRegistry()
_multiprocess = os.getenv("prometheus_multiproc_dir", "") != ""

if _multiprocess:
    try:
        registry = mp_util.MultiProcessCollector(_registry).__dict__.get('registry', _registry)
    except Exception:
        registry = _registry
else:
    registry = _registry


# ════════════════════════════════════════════════
#  1. 业务指标 (Business Metrics)
# ════════════════════════════════════════════════

# --- 任务相关 ---

TASKS_TOTAL = Counter(
    "agvtms_tasks_total",
    "Total tasks created",
    ["status"],  # pending/assigned/in_progress/completed/failed/cancelled
    registry=registry,
)

TASKS_DISPATCHED = Counter(
    "agvtms_tasks_dispatched_total",
    "Tasks dispatched to AGVs",
    ["priority", "algorithm_version"],
    registry=registry,
)

TASK_COMPLETION_TIME = Histogram(
    "agvtms_task_completion_time_seconds",
    "Task lifecycle duration from creation to completion",
    ["task_type"],
    buckets=[5, 15, 30, 60, 120, 300, 600, 1800, 3600],
    registry=registry,
)

TASK_QUEUE_DEPTH = Gauge(
    "agvtms_task_queue_depth",
    "Current number of pending tasks in dispatch queue",
    registry=registry,
)

# --- AGV相关 ---

AGVS_ONLINE = Gauge(
    "agvtms_agvs_online_total",
    "Number of AGVs currently online",
    ["model_type", "status"],
    registry=registry,
)

AGV_BATTERY_LEVEL = Gauge(
    "agvtms_agv_battery_percent",
    "AGV battery percentage",
    ["agv_id"],
    registry=registry,
)

AGV_CHARGING_QUEUE_SIZE = Gauge(
    "agvtms_charging_queue_size",
    "Number of AGVs waiting to charge",
    ["charger_id"],
    registry=registry,
)

AGV_TRAVEL_DISTANCE = Counter(
    "agvtms_agv_travel_distance_meters_total",
    "Total travel distance per AGV (for maintenance scheduling)",
    ["agv_id"],
    registry=registry,
)

AGV_STATE_CHANGES = Counter(
    "agvtms_agv_state_changes_total",
    "AGV state transitions (for anomaly detection)",
    ["agv_id", "from_state", "to_state"],
    registry=registry,
)


# ════════════════════════════════════════════════
#  2. 调度算法指标 (Algorithm Metrics)
# ════════════════════════════════════════════════

PATH_PLANNING_DURATION = Histogram(
    "agvtms_path_planning_duration_seconds",
    "Path planning algorithm execution time",
    ["algorithm", "map_complexity"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=registry,
)

PATH_NODES_EXPANDED = Histogram(
    "agvtms_path_nodes_expanded",
    "Number of nodes expanded during search",
    ["algorithm"],
    buckets=[10, 50, 100, 500, 1000, 5000, 10000, 50000],
    registry=registry,
)

PATH_LENGTH_METERS = Histogram(
    "agvtms_path_length_meters",
    "Planned path length in meters",
    ["algorithm"],
    buckets=[5, 10, 20, 50, 100, 200, 500, 1000],
    registry=registry,
)

MAPF_SOLVER_DURATION = Histogram(
    "agvtms_mapf_solver_duration_seconds",
    "MAPF multi-agent solver execution time",
    ["solver_type", "num_agents"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=registry,
)

CONFLICT_COUNT = Counter(
    "agvtms_conflicts_detected_total",
    "Conflicts detected during MAPF solving",
    ["conflict_type"],  # vertex/edge/swap/following
    registry=registry,
)

DEADLOCK_DETECTED = Counter(
    "agvtms_deadlocks_detected_total",
    "Deadlock situations detected by traffic controller",
    ["prevention_action"],  # blocked/wait_die/wound_wait/priority
    registry=registry,
)

TRAFFIC_CONGESTION_LEVEL = Gauge(
    "agvtms_traffic_congestion_level",
    "Current congestion level per zone (0-4: none/low/medium/high/critical)",
    ["zone_id"],
    registry=registry,
)

RESOURCE_LOCK_COUNT = Gauge(
    "agvtms_resource_locks_held",
    "Currently held resource locks",
    ["resource_type"],
    registry=registry,
)

ALGORITHM_SWITCHES = Counter(
    "agvtms_algorithm_switches_total",
    "Algorithm version switches (V1↔V2)",
    ["from_version", "to_version", "trigger_reason"],
    registry=registry,
)


# ════════════════════════════════════════════════
#  3. 系统基础设施指标 (Infrastructure Metrics)
# ════════════════════════════════════════════════

HTTP_REQUEST_DURATION = Histogram(
    "agvtms_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint", "status"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=registry,
)

HTTP_REQUESTS_TOTAL = Counter(
    "agvtms_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=registry,
)

KAFKA_MESSAGES_PRODUCED = Counter(
    "agvtms_kafka_messages_produced_total",
    "Messages produced to Kafka",
    ["topic", "result"],  # success/dropped/file_fallback
    registry=registry,
)

KAFKA_MESSAGES_CONSUMED = Counter(
    "agvtms_kafka_messages_consumed_total",
    "Messages consumed from Kafka",
    ["topic", "consumer_group"],
    registry=registry,
)

KAFKA_CONSUMER_LAG = Gauge(
    "agvtms_kafka_consumer_lag",
    "Kafka consumer group lag (messages behind)",
    ["topic", "consumer_group"],
    registry=registry,
)

DB_QUERY_DURATION = Histogram(
    "agvtms_db_query_duration_seconds",
    "Database query execution time",
    ["operation"],  # select/insert/update/delete/batch
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    registry=registry,
)

DB_CONNECTION_POOL_SIZE = Gauge(
    "agvtms_db_pool_size",
    "Database connection pool usage",
    ["state"],  # idle/used/overflow
    registry=registry,
)

REDIS_HITS = Counter(
    "agvtms_redis_hits_total",
    "Redis cache hits",
    ["operation"],
    registry=registry,
)

REDIS_MISSES = Counter(
    "agvtms_redis_misses_total",
    "Redis cache misses",
    ["operation"],
    registry=registry,
)

INFLUXDB_WRITE_DURATION = Histogram(
    "agvtms_influxdb_write_duration_seconds",
    "InfluxDB write batch duration",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
    registry=registry,
)

INFLUXDB_POINTS_WRITTEN = Counter(
    "agvtms_influxdb_points_written_total",
    "Time-series points written to InfluxDB",
    ["measurement"],
    registry=registry,
)


# ════════════════════════════════════════════════
#  4. 可靠性指标 (Reliability Metrics)
# ════════════════════════════════════════════════

CIRCUIT_BREAKER_STATE = Gauge(
    "agvtms_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["breaker_name"],
    registry=registry,
)

CIRCUIT_BREAKER_FAILURES = Counter(
    "agvtms_circuit_breaker_failures_total",
    "Failures that tripped the circuit breaker",
    ["breaker_name"],
    registry=registry,
)

RETRY_ATTEMPTS = Counter(
    "agvtms_retry_attempts_total",
    "Retry attempts with exponential backoff",
    ["operation", "attempt_number", "outcome"],
    registry=registry,
)

FALLBACK_INVOKED = Counter(
    "agvtms_fallback_invoked_total",
    "Fallback chain invocations",
    ["operation", "fallback_depth", "result"],
    registry=registry,
)

RATE_LIMIT_DROPS = Counter(
    "agvtms_rate_limit_drops_total",
    "Requests dropped by rate limiter",
    ["limiter_name"],
    registry=registry,
)

SLA_BREACH = Counter(
    "agvtms_sla_breaches_total",
    "SLA threshold breaches",
    ["sla_type", "threshold_seconds"],
    registry=registry,
)

UPTIME_SECONDS = Gauge(
    "agvtms_uptime_seconds",
    "Application uptime in seconds",
    registry=registry,
)

START_TIME = Info(
    "agvtms_start_time",
    "Application start timestamp",
    registry=registry,
)


# ==================== 便捷API层 ====================

class AgvTmsMetrics:
    """
    面向业务的便捷指标API.
    
    封装底层Prometheus操作，提供语义化的业务方法。
    所有方法线程安全，可直接在异步/同步代码中使用。
    """

    def __init__(self):
        self._start_time = time.time()
        UPTIME_SECONDS.set(0)
        START_TIME.info({
            "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": os.getenv("APP_VERSION", "unknown"),
        })

    def tick(self):
        """每秒调用更新uptime (建议放在后台循环)."""
        UPTIME_SECONDS.set(time.time() - self._start_time)

    # ---- 任务相关 ----

    def record_task_created(self, task_type: str = "standard"):
        TASKS_TOTAL.labels(status="pending").inc()

    def record_task_dispatch(
        self,
        task_id: str = "",
        agv_id: str = "",
        priority: str = "normal",
        algorithm: str = "v2_astar",
    ):
        TASKS_DISPATCHED.labels(priority=priority, algorithm_version=algorithm).inc()

    def record_task_completed(self, task_type: str = "standard", duration_sec: float = 0):
        TASKS_TOTAL.labels(status="completed").inc()
        if duration_sec > 0:
            TASK_COMPLETION_TIME.labels(task_type=task_type).observe(duration_sec)

    def record_task_failed(self):
        TASKS_TOTAL.labels(status="failed").inc()

    def update_queue_depth(self, depth: int):
        TASK_QUEUE_DEPTH.set(depth)

    # ---- AGV相关 ----

    def update_agv_online(self, model_type: str = "default", status: str = "idle", count: int = 0):
        AGVS_ONLINE.labels(model_type=model_type, status=status).set(count)

    def update_battery(self, agv_id: str, percent: float):
        AGV_BATTERY_LEVEL.labels(agv_id=agv_id).set(percent)

    def update_charging_queue(self, charger_id: str, size: int):
        AGV_CHARGING_QUEUE_SIZE.labels(charger_id=charger_id).set(size)

    def record_travel(self, agv_id: str, meters: float):
        AGV_TRAVEL_DISTANCE.labels(agv_id=agv_id).inc(meters)

    def record_state_change(self, agv_id: str, from_state: str, to_state: str):
        AGV_STATE_CHANGES.labels(agv_id=agv_id, from_state=from_state, to_state=to_state).inc()

    # ---- 调度算法 ----

    def record_path_planning(
        self,
        elapsed_sec: float = 0,
        nodes_expanded: int = 0,
        algorithm: str = "astar",
        map_complexity: str = "medium",
        path_length: float = 0,
    ):
        PATH_PLANNING_DURATION.labels(algorithm=algorithm, map_complexity=map_complexity).observe(elapsed_sec)
        PATH_NODES_EXPANDED.labels(algorithm=algorithm).observe(nodes_expanded)
        if path_length > 0:
            PATH_LENGTH_METERS.labels(algorithm=algorithm).observe(path_length)

    @contextmanager
    def time_path_planning(self, algorithm: str = "astar", map_complexity: str = "medium"):
        """上下文管理器 — 自动记录路径规划耗时."""
        t0 = time.perf_counter()
        expanded = [0]
        yield expanded  # 允许外部写入expanded值
        elapsed = time.perf_counter() - t0
        self.record_path_planning(
            elapsed_sec=elapsed,
            nodes_expanded=expanded[0],
            algorithm=algorithm,
            map_complexity=map_complexity,
        )

    def record_mapf_solve(
        self,
        elapsed_sec: float = 0,
        solver_type: str = "cbs",
        num_agents: int = 0,
    ):
        n_bucket = f"{min(num_agents, 100)}"
        MAPF_SOLVER_DURATION.labels(solver_type=solver_type, num_agents=n_bucket).observe(elapsed_sec)

    def record_conflict(self, conflict_type: str = "vertex"):
        CONFLICT_COUNT.labels(conflict_type=conflict_type).inc()

    def record_deadlock(self, prevention_action: str = "blocked"):
        DEADLOCK_DETECTED.labels(prevention_action=prevention_action).inc()

    def update_congestion(self, zone_id: str, level: int):
        TRAFFIC_CONGESTION_LEVEL.labels(zone_id=zone_id).set(min(max(level, 0), 4))

    def update_lock_count(self, resource_type: str, count: int):
        RESOURCE_LOCK_COUNT.labels(resource_type=resource_type).set(count)

    # ---- HTTP ----

    def record_http_request(
        self,
        method: str = "GET",
        endpoint: str = "/",
        status: str = "200",
        duration_sec: float = 0,
    ):
        HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
        HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint, status=status).observe(duration_sec)

    # ---- 基础设施 ----

    def record_kafka_produce(self, topic: str, success: bool = True):
        result = "success" if success else "dropped"
        KAFKA_MESSAGES_PRODUCED.labels(topic=topic, result=result).inc()

    def record_kafka_consume(self, topic: str, consumer_group: str = "default"):
        KAFKA_MESSAGES_CONSUMED.labels(topic=topic, consumer_group=consumer_group).inc()

    def update_consumer_lag(self, topic: str, consumer_group: str, lag: int):
        KAFKA_CONSUMER_LAG.labels(topic=topic, consumer_group=consumer_group).set(lag)

    def record_db_query(self, operation: str = "select", duration_sec: float = 0):
        DB_QUERY_DURATION.labels(operation=operation).observe(duration_sec)

    def update_db_pool(self, idle: int = 0, used: int = 0, overflow: int = 0):
        DB_CONNECTION_POOL_SIZE.labels(state="idle").set(idle)
        DB_CONNECTION_POOL_SIZE.labels(state="used").set(used)
        DB_CONNECTION_POOL_SIZE.labels(state="overflow").set(overflow)

    def record_cache_hit(self, operation: str = "get"):
        REDIS_HITS.labels(operation=operation).inc()

    def record_cache_miss(self, operation: str = "get"):
        REDIS_MISSES.labels(operation=operation).inc()

    def record_influxdb_write(self, measurement: str = "telemetry", duration_sec: float = 0, points: int = 0):
        INFLUXDB_WRITE_DURATION.observe(duration_sec)
        INFLUXDB_POINTS_WRITTEN.labels(measurement=measurement).inc(points)

    # ---- 可靠性 ----

    def update_circuit_breaker(self, name: str, state: int):
        """state: 0=closed, 1=open, 2=half_open"""
        CIRCUIT_BREAKER_STATE.labels(breaker_name=name).set(state)

    def record_circuit_failure(self, name: str):
        CIRCUIT_BREAKER_FAILURES.labels(breaker_name=name).inc()

    def record_retry(self, operation: str, attempt: int, success: bool):
        RETRY_ATTEMPTS.labels(
            operation=operation,
            attempt_number=str(attempt),
            outcome="success" if success else "failed",
        ).inc()

    def record_fallback(self, operation: str, depth: int, success: bool):
        FALLBACK_INVOKED.labels(
            operation=operation,
            fallback_depth=str(depth),
            result="success" if success else "failed",
        ).inc()

    def record_rate_limit_drop(self, limiter_name: str = "api_global"):
        RATE_LIMIT_DROPS.labels(limiter_name=limiter_name).inc()

    def record_sla_breach(self, sla_type: str, threshold: str):
        SLA_BREACH.labels(sla_type=sla_type, threshold_threshold_seconds=threshold).inc()


# 全局单例
metrics = AgvTmsMetrics()


# ==================== FastAPI /metrics 端点 ====================

async def metrics_endpoint():
    """Prometheus /metrics 端点处理器."""
    return generate_latest(registry)


def get_metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


# ==================== 装饰器: 自动指标采集 ====================

F = TypeVar("F", bound=Callable[..., Any])


def count_exceptions(
    counter: Counter,
    labels_fn: Callable[[Exception, Any, Dict], Dict] = lambda e, args, kw: {},
):
    """
    异常计数装饰器.
    
    用法:
        @count_exceptions(TASKS_DISPATCHED, labels_fn=lambda e,a,kw: {"priority":"high"})
        def risky_operation():
            ...
    """
    def decorator(func: F) -> F:
        @functools_wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                counter.labels(**labels_fn(e, args, kwargs)).inc()
                raise
        return wrapper  # type: ignore
    return decorator


def histogram_timing(hist: Histogram, labels_fn=lambda *a, **kw: {}):
    """
    耗时Histogram装饰器.
    
    用法:
        @histogram_timing(PATH_PLANNING_DURATION, labels_fn=lambda *a,**kw:{"algorithm":"astar"})
        def plan_path(start, goal):
            ...
    """
    def decorator(func: F) -> F:
        @functools_wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter() - t0
                hist.labels(**labels_fn(*args, **kwargs)).observe(elapsed)
        return wrapper  # type: ignore
    return decorator


def gauge_active(gauge: Gauge, labels_fn=lambda *a, **kw: {}):
    """
    进行中计数Gauge装饰器 (track_inprogress).
    
    用法:
        @gauge_active(PATH_PLANNING_IN_PROGRESS, labels_fn=...)
        def long_running_job():
            ...
    """
    def decorator(func: F) -> F:
        @functools_wraps(func)
        def wrapper(*args, **kwargs):
            gauge.labels(**labels_fn(*args, **kwargs)).inc()
            try:
                return func(*args, **kwargs)
            finally:
                gauge.labels(**labels_fn(*args, **kwargs)).dec()
        return wrapper  # type: ignore
    return decorator


# ==================== 导出 ====================

__all__ = [
    # 核心类/函数
    "metrics", "metrics_endpoint", "get_metrics_content_type",
    "AgvTmsMetrics",
    # 装饰器
    "count_exceptions", "histogram_timing", "gauge_active",
    # 业务指标
    "TASKS_TOTAL", "TASKS_DISPATCHED", "TASK_COMPLETION_TIME", "TASK_QUEUE_DEPTH",
    "AGVS_ONLINE", "AGV_BATTERY_LEVEL", "AGV_CHARGING_QUEUE_SIZE",
    "AGV_TRAVEL_DISTANCE", "AGV_STATE_CHANGES",
    # 调度指标
    "PATH_PLANNING_DURATION", "PATH_NODES_EXPANDED", "PATH_LENGTH_METERS",
    "MAPF_SOLVER_DURATION", "CONFLICT_COUNT", "DEADLOCK_DETECTED",
    "TRAFFIC_CONGESTION_LEVEL", "RESOURCE_LOCK_COUNT", "ALGORITHM_SWITCHES",
    # 基础设施指标
    "HTTP_REQUEST_DURATION", "HTTP_REQUESTS_TOTAL",
    "KAFKA_MESSAGES_PRODUCED", "KAFKA_MESSAGES_CONSUMED", "KAFKA_CONSUMER_LAG",
    "DB_QUERY_DURATION", "DB_CONNECTION_POOL_SIZE",
    "REDIS_HITS", "REDIS_MISSES",
    "INFLUXDB_WRITE_DURATION", "INFLUXDB_POINTS_WRITTEN",
    # 可靠性指标
    "CIRCUIT_BREAKER_STATE", "CIRCUIT_BREAKER_FAILURES",
    "RETRY_ATTEMPTS", "FALLBACK_INVOKED", "RATE_LIMIT_DROPS",
    "SLA_BREACH", "UPTIME_SECONDS", "START_TIME",
]
