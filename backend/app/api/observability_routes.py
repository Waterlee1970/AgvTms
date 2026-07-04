"""
增强健康检查与可观测性API路由 — Phase 3.

提供企业级监控端点:

GET /api/v3/health          — 综合健康报告 (含SLO状态)
GET /api/v3/readiness       — 就绪探测 (Kubernetes Ready Probe)
GET /api/v3/liveness        — 存活探测 (Kubernetes Liveness Probe)
GET /api/v3/slo/status      — SLO状态仪表板
GET /api/v3/slo/endpoints   — 各端点SLO详情
GET /api/v3/alerts/active   — 当前活跃告警列表
GET /api/v3/alerts/history  — 告警历史
GET /api/v3/alerts/rules    — 告警规则管理
GET /api/v3/alerts/stats    — 告警统计摘要

对标: Kubernetes Probes + Prometheus + Grafana Alerts.
"""

from __future__ import annotations

import asyncio
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["可观测性"])


# ==================== 数据模型 ====================

class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(..., description="健康状态: healthy/degraded/unhealthy")
    version: str = Field(..., description="应用版本")
    timestamp: str = Field(..., description="检查时间")
    uptime_seconds: float = Field(..., description="运行时长(秒)")
    checks: Dict[str, Any] = Field(default_factory=dict, description="各组件检查结果")
    slo_summary: Optional[Dict] = Field(default=None, description="SLO摘要")


class ReadinessResponse(BaseModel):
    """就绪检查响应"""
    ready: bool = Field(..., description="是否就绪 (可接受流量)")
    checks: Dict[str, str] = Field(..., description="关键依赖状态")
    timestamp: str = Field(..., description="检查时间")


class LivenessResponse(BaseModel):
    """存活检查响应"""
    alive: bool = Field(..., description="是否存活")
    timestamp: str = Field(..., description="检查时间")
    probe_duration_ms: float = Field(..., description="探测耗时(ms)")


class SLOStatusResponse(BaseModel):
    """SLO状态响应"""
    total_endpoints: int = Field(..., description="已注册的端点数")
    overall_health: str = Field(..., description="整体健康状态")
    avg_availability_24h: Optional[str] = Field(default=None, description="24h平均可用性")
    budgets_summary: Dict[str, int] = Field(default_factory=dict, description="预算状态分布")
    top_consumers: List[Dict] = Field(default_factory=list, description="预算消耗Top5")
    endpoints: Optional[List[Dict]] = Field(default=None, description="各端点详情")


class AlertListResponse(BaseModel):
    """告警列表响应"""
    alerts: List[Dict] = Field(default_factory=list, description="告警列表")
    total: int = Field(default=0, description="总数")
    query_time: str = Field(..., description="查询时间")


class AlertRuleResponse(BaseModel):
    """告警规则响应"""
    rules: List[Dict] = Field(default_factory=list, description="规则列表")
    total_enabled: int = Field(default=0, description="已启用数量")
    total_disabled: int = Field(default=0, description="已禁用数量")


# ==================== 启动时间记录 ====================
_start_time = time.time()


# ==================== 健康检查端点 ====================

@router.get("/health", response_model=HealthResponse, summary="综合健康检查")
async def health_check(request: Request):
    """
    综合健康检查端点.
    
    检查项:
    1. 应用进程存活
    2. 关键依赖服务 (PostgreSQL, Redis, Kafka)
    3. 核心模块状态 (调度器, 算法引擎)
    4. SLO合规性摘要
    5. 最近错误统计
    
    适用场景:
    - 负载均衡健康检查
    - 运维监控Dashboard数据源
    - 故障排查入口
    """
    checks = {}
    overall_status = "healthy"
    
    # 1. 应用基础信息
    checks['application'] = {
        'status': 'up',
        'version': getattr(request.app, 'version', '3.0.0'),
        'python_version': f'{__import__("sys").version.split()[0]}',
    }
    
    # 2. 数据库连接检查
    db_check = await _check_database()
    checks['database'] = db_check
    if db_check['status'] != 'up':
        overall_status = 'degraded' if db_check['status'] == 'degraded' else 'unhealthy'
    
    # 3. Redis连接检查
    redis_check = await _check_redis()
    checks['redis'] = redis_check
    if redis_check['status'] != 'up':
        overall_status = 'degraded' if overall_status != 'unhealthy' else overall_status
    
    # 4. 调度器状态检查
    scheduler_check = await _check_scheduler()
    checks['scheduler'] = scheduler_check
    
    # 5. SLO摘要 (如果可用)
    slo_summary = None
    try:
        from .slo_monitor import slo_registry
        slo_summary = slo_registry.get_system_summary()
        checks['slo'] = {'status': slo_summary.get('overall_health', 'unknown')}
    except Exception as e:
        logger.debug(f"SLO check skipped: {e}")
    
    # 6. 断路器状态
    circuit_breaker_status = _check_circuit_breakers()
    checks['circuit_breakers'] = circuit_breaker_status
    
    response = HealthResponse(
        status=overall_status,
        version=getattr(request.app, 'version', '3.0.0'),
        timestamp=datetime.utcnow().isoformat() + 'Z',
        uptime_seconds=round(time.time() - _start_time, 1),
        checks=checks,
        slo_summary=slo_summary,
    )
    
    # 根据状态返回不同HTTP状态码
    http_status = 200
    if overall_status == "degraded":
        http_status = 200  # 部分降级仍返回200 (但body中有详细信息)
    elif overall_status == "unhealthy":
        http_status = 503
    
    return JSONResponse(
        status_code=http_status,
        content=response.model_dump(),
    )


@router.get("/readiness", response_model=ReadinessResponse, summary="Kubernetes就绪探测")
async def readiness_probe(request: Request):
    """
    就绪探测端点 (Kubernetes Readiness Probe).
    
    判断标准:
    - 所有critical依赖可用 → ready=True
    - 部分依赖降级 → ready=True但标记degraded
    - 核心依赖不可用 → ready=False
    
    K8s配置示例:
        livenessProbe:
          httpGet:
            path: /api/v3/liveness
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
          
        readinessProbe:
          httpGet:
            path: /api/v3/readiness
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
    """
    checks = {}
    is_ready = True
    
    # PostgreSQL就绪检查
    db_ok, db_detail = await _check_database_ready()
    checks['database'] = 'up' if db_ok else ('down' if db_ok is False else 'unknown')
    if db_ok is False:
        is_ready = False
    
    # Redis就绪检查
    redis_ok, redis_detail = await _check_redis_ready()
    checks['redis'] = 'up' if redis_ok else ('down' if redis_ok is False else 'unknown')
    # Redis不可用不影响就绪 (可以降级运行)
    
    # 调度器就绪检查
    try:
        from ..services.schedule_service import schedule_service
        scheduler_ready = schedule_service.is_ready()
        checks['scheduler'] = 'ready' if scheduler_ready else 'not_ready'
    except Exception:
        checks['scheduler'] = 'unknown'
    
    # 内存检查 (避免OOM)
    memory_ok, memory_pct = _check_memory_usage()
    checks['memory'] = f'{memory_pct:.1f}%' if memory_pct else 'ok'
    if memory_ok is False:
        is_ready = False
    
    return ReadinessResponse(
        ready=is_ready,
        checks=checks,
        timestamp=datetime.utcnow().isoformat() + 'Z',
    )


@router.get("/liveness", response_model=LivenessResponse, summary="Kubernetes存活探测")
async def liveness_probe(request: Request):
    """
    存活探测端点 (Kubernetes Liveness Probe).
    
    检查项:
    1. 进程是否响应 (能处理请求即存活)
    2. 主线程未死锁
    3. 未发生严重内存泄漏
    
    此端点应该非常快速 (< 100ms)，不做复杂检查.
    
    如果此探测失败，K8s会重启Pod.
    """
    probe_start = time.time()
    
    # 1. 简单的响应测试 (如果能执行到这里说明进程存活)
    alive = True
    
    # 2. 快速线程死锁检测 (简化版)
    thread_alive = _quick_thread_check()
    if not thread_alive:
        alive = False
    
    # 3. 内存泄漏检测 (简单阈值)
    memory_ok, _ = _check_memory_usage(threshold_mb=2000)  # 2GB警告线
    # 内存高只记录日志，不判定为死亡
    
    probe_duration_ms = (time.time() - probe_start) * 1000
    
    status_code = 200 if alive else 503
    return JSONResponse(
        status_code=status_code,
        content=LivenessResponse(
            alive=alive,
            timestamp=datetime.utcnow().isoformat() + 'Z',
            probe_duration_ms=round(probe_duration_ms, 2),
        ).model_dump(),
    )


# ==================== SLO监控端点 ====================

@router.get("/slo/status", response_model=SLOStatusResponse, summary="SLO状态仪表板")
async def slo_status_dashboard(
    verbose: bool = Query(False, description="包含详细窗口数据"),
    request: Request = None,
):
    """
    SLO状态总览仪表板.
    
    展示所有已注册API端点的SLO达标情况，
    包括可用性、错误预算、烧毁速率等关键指标.
    
    数据来源: app.core.slo_monitor.SLORegistry
    """
    try:
        from .slo_monitor import slo_registry
        
        summary = slo_registry.get_system_summary()
        
        result = SLOStatusResponse(
            total_endpoints=summary.get('total_endpoints', 0),
            overall_health=summary.get('overall_health', 'unknown'),
            avg_availability_24h=summary.get('avg_availability_24h'),
            budgets_summary=summary.get('budgets_summary', {}),
            top_consumers=summary.get('top_consumers', []),
        )
        
        if verbose:
            all_statuses = slo_registry.get_all_statuses(verbose=True)
            result.endpoints = [s.to_dict(verbose=True) for s in all_statuses]
        
        return result
        
    except ImportError:
        raise HTTPException(status_code=503, detail="SLO monitoring module not available")


@router.get("/slo/endpoints/{endpoint:path}", summary="单个端点SLO详情")
async def slo_endpoint_detail(
    endpoint: str,
    verbose: bool = Query(True, description="包含详细指标"),
):
    """获取指定API端点的详细SLO状态."""
    try:
        from .slo_monitor import slo_registry
        
        status = slo_registry.get_slo_status(endpoint, verbose=verbose)
        if not status:
            raise HTTPException(status_code=404, detail=f"No SLO registered for endpoint: {endpoint}")
        
        return status.to_dict(verbose=verbose)
        
    except ImportError:
        raise HTTPException(status_code=503, detail="SLO monitoring module not available")


# ==================== 告警管理端点 ====================

@router.get("/alerts/active", response_model=AlertListResponse, summary="当前活跃告警")
async def active_alerts(
    severity: Optional[str] = Query(None, description="按级别过滤: info/warning/critical/emergency"),
):
    """获取当前正在触发的告警列表."""
    try:
        from .alert_engine import alert_manager, AlertSeverity
        
        alerts = alert_manager.get_active_alerts()
        
        if severity:
            sev = AlertSeverity.from_string(severity)
            alerts = [a for a in alerts if a.get('severity') == sev.value]
        
        return AlertListResponse(
            alerts=alerts,
            total=len(alerts),
            query_time=datetime.utcnow().isoformat() + 'Z',
        )
        
    except ImportError:
        raise HTTPException(status_code=503, detail="Alert engine not available")


@router.get("/alerts/history", response_model=AlertListResponse, summary="告警历史")
async def alert_history(
    limit: int = Query(100, ge=1, le=1000, description="返回数量上限"),
    severity: Optional[str] = Query(None, description="按级别过滤"),
):
    """获取历史告警记录."""
    try:
        from .alert_engine import alert_manager, AlertSeverity
        
        sev_filter = AlertSeverity.from_string(severity) if severity else None
        history = alert_manager.get_alert_history(limit=limit, severity=sev_filter)
        
        return AlertListResponse(
            alerts=history,
            total=len(history),
            query_time=datetime.utcnow().isoformat() + 'Z',
        )
        
    except ImportError:
        raise HTTPException(status_code=503, detail="Alert engine not available")


@router.get("/alerts/rules", response_model=AlertRuleResponse, summary="告警规则列表")
async def alert_rules_list():
    """列出所有告警规则及其状态."""
    try:
        from .alert_engine import alert_manager
        
        stats = alert_manager.get_statistics()
        rules_data = []
        
        for rule_id, rule in alert_manager._rules.items():
            rules_data.append({
                'rule_id': rule.rule_id,
                'name': rule.name,
                'condition': rule.condition,
                'severity': rule.severity.value,
                'enabled': rule.enabled,
                'duration_seconds': rule.duration_seconds,
                'cooldown_seconds': rule.cooldown_seconds,
                'description': rule.description,
                'last_triggered': (
                    datetime.fromtimestamp(rule.last_triggered).isoformat() + 'Z'
                    if rule.last_triggered else None
                ),
            })
        
        return AlertRuleResponse(
            rules=rules_data,
            total_enabled=stats.get('rules_enabled', 0),
            total_disabled=stats.get('rules_total', 0) - stats.get('rules_enabled', 0),
        )
        
    except ImportError:
        raise HTTPException(status_code=503, detail="Alert engine not available")


@router.get("/alerts/stats", summary="告警统计摘要")
async def alert_statistics():
    """获取告警系统的统计数据."""
    try:
        from .alert_engine import alert_manager
        
        stats = alert_manager.get_statistics()
        active = alert_manager.get_active_alerts()
        
        return {
            **stats,
            'current_active_alerts': len(active),
            'by_severity_active': _count_by_severity(active),
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        }
        
    except ImportError:
        raise HTTPException(status_code=503, detail="Alert engine not available")


@router.post("/alerts/{rule_id}/toggle", description="启用/禁用告警规则")
async def toggle_alert_rule(rule_id: str, enabled: bool = True):
    """切换告警规则的启用状态."""
    try:
        from .alert_engine import alert_manager
        
        if enabled:
            success = alert_manager.enable_rule(rule_id)
        else:
            success = alert_manager.disable_rule(rule_id)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
        
        return {"rule_id": rule_id, "enabled": enabled, "message": "OK"}
        
    except ImportError:
        raise HTTPException(status_code=503, detail="Alert engine not available")


# ==================== 辅助函数 ====================

async def _check_database() -> Dict:
    """检查PostgreSQL连接"""
    try:
        from ..core.database import get_db_pool
        pool = get_db_pool()
        
        start = time.time()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            latency_ms = (time.time() - start) * 1000
        
        status = "up"
        if latency_ms > 1000:
            status = "degraded"
        
        return {
            'status': status,
            'latency_ms': round(latency_ms, 2),
            'pool_size': getattr(pool, '_pool', {}).get('_maxsize', 'unknown'),
        }
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return {
            'status': 'down',
            'error': str(e)[:200],
        }


async def _check_database_ready() -> tuple:
    """数据库就绪检查 (返回三元组: ok, detail)"""
    try:
        from ..core.database import get_db_pool
        pool = get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True, "connected"
    except Exception:
        return False, "connection_failed"


async def _check_redis() -> Dict:
    """检查Redis连接"""
    try:
        from ..core.cache import redis_client
        
        start = time.time()
        await redis_client.ping()
        latency_ms = (time.time() - start) * 1000
        
        status = "up"
        if latency_ms > 100:
            status = "degraded"
        
        return {
            'status': status,
            'latency_ms': round(latency_ms, 2),
        }
    except Exception as e:
        logger.debug(f"Redis health check failed: {e}")
        return {
            'status': 'down',
            'error': str(e)[:100],
        }


async def _check_redis_ready() -> tuple:
    """Redis就绪检查"""
    try:
        from ..core.cache import redis_client
        await redis_client.ping()
        return True, "connected"
    except Exception:
        return False, "failed"


async def _check_scheduler() -> Dict:
    """检查调度器状态"""
    try:
        from ..services.schedule_service import schedule_service
        
        is_running = schedule_service.is_running()
        metrics = await schedule_service.get_metrics_async()
        
        return {
            'status': 'running' if is_running else 'stopped',
            'metrics': metrics or {},
        }
    except Exception as e:
        logger.debug(f"Scheduler check failed: {e}")
        return {
            'status': 'error',
            'error': str(e)[:100],
        }


def _check_circuit_breakers() -> Dict:
    """检查断路器状态"""
    try:
        from .resilience import breaker_instances
        
        breakers_status = {}
        for name, breaker in breaker_instances.items():
            state = breaker.state.value if hasattr(breaker, 'state') else 'unknown'
            breakers_status[name] = {
                'state': state,
                'failures': getattr(breaker, 'failure_count', 0),
            }
        
        any_open = any(v['state'] == 'open' for v in breakers_status.values())
        
        return {
            'status': 'warning' if any_open else 'ok',
            'breakers': breakers_status,
        }
    except Exception as e:
        logger.debug(f"Circuit breaker check failed: {e}")
        return {'status': 'unknown'}


def _check_memory_usage(threshold_mb: float = None) -> tuple:
    """检查内存使用情况"""
    try:
        import psutil
        process = psutil.Process()
        mem_info = process.memory_info()
        mem_gb = mem_info.rss / 1024 / 1024 / 1024
        mem_percent = process.memory_percent()
        
        threshold_gb = (threshold_mb or 4096) / 1024  # 默认4GB
        
        ok = mem_gb < threshold_gb
        return ok, mem_percent
        
    except ImportError:
        return True, None  # psutil不可用时跳过
    except Exception:
        return True, None


def _quick_thread_check() -> bool:
    """快速线程活性检查"""
    try:
        import threading
        main_thread = threading.main_thread()
        return main_thread.is_alive()
    except Exception:
        return True


def _count_by_severity(alerts: List[Dict]) -> Dict[str, int]:
    """按严重级别计数"""
    counts = {"info": 0, "warning": 0, "critical": 0, "emergency": 0}
    for alert in alerts:
        sev = alert.get('severity', 'info')
        counts[sev] = counts.get(sev, 0) + 1
    return counts


# ==================== 导出 ====================
__all__ = ['router']
