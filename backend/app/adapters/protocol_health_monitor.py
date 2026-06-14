"""
协议健康监控器 — Phase 3 协议深化 (统一监控).

功能:
  - 聚合所有适配器的健康指标
  - 统一告警阈值检测
  - 协议可用性统计
  - 健康报告 API
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"    # 部分功能异常
    UNHEALTHY = "unhealthy" # 主要功能异常
    UNKNOWN = "unknown"


@dataclass 
class AlertRule:
    """告警规则"""
    metric: str           # 监控指标名
    operator: str         # gt | lt | eq | gte | lte
    threshold: Any        # 阈值
    severity: str = "warning"  # warning | critical | info
    cooldown_seconds: float = 60.0  # 冷却时间
    
    def check(self, value: Any) -> bool:
        """检查是否触发规则"""
        ops = {
            'gt': lambda a, b: a > b,
            'lt': lambda a, b: a < b,
            'eq': lambda a, b: a == b,
            'gte': lambda a, b: a >= b,
            'lte': lambda a, b: a <= b,
        }
        op = ops.get(self.operator)
        return op(value, self.threshold) if op else False


@dataclass
class ProtocolHealthSnapshot:
    """单个协议的健康快照"""
    protocol_name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    connected: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)
    last_check_time: float = 0.0
    uptime_seconds: float = 0.0
    alerts: List[Dict] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "protocol": self.protocol_name,
            "status": self.status.value,
            "connected": self.connected,
            "metrics": self.metrics,
            "last_check_time": self.last_check_time,
            "uptime_s": round(self.uptime_seconds, 1),
            "alert_count": len(self.alerts),
            "alerts": self.alerts[-5:] if self.alerts else [],  # 最近5条
        }


class ProtocolHealthMonitor:
    """
    协议健康监控器.
    
    用法:
        monitor = ProtocolHealthMonitor()
        monitor.register_adapter("mqtt", mqtt_adapter)
        
        async for snapshot in monitor.watch():
            print(snapshot)
    """
    
    DEFAULT_ALERT_RULES = [
        AlertRule(metric="error_rate", operator="gt", threshold=0.1, severity="warning"),
        AlertRule(metric="avg_response_ms", operator="gt", threshold=5000, severity="warning"),
        AlertRule(metric="reconnect_count", operator="gt", threshold=10, severity="critical"),
    ]
    
    def __init__(
        self,
        check_interval: float = 30.0,      # 检查间隔 (秒)
        alert_callback: Optional[Callable[[Dict], None]] = None,
    ):
        self.check_interval = check_interval
        self.alert_callback = alert_callback
        
        self._adapters: Dict[str, Any] = {}   # name → adapter instance
        self._snapshots: Dict[str, ProtocolHealthSnapshot] = {}
        self._alert_rules: List[AlertRule] = list(self.DEFAULT_ALERT_RULES)
        self._alert_history: List[Dict] = []
        
        self._monitor_task: Optional[asyncio.Task] = None
        self._start_time: float = time.time()

    def register_adapter(self, name: str, adapter):
        """注册要监控的适配器"""
        self._adapters[name] = adapter
        self._snapshots[name] = ProtocolHealthSnapshot(protocol_name=name)
        logger.info("Registered protocol health monitoring: %s", name)

    def add_alert_rule(self, rule: AlertRule):
        """添加自定义告警规则"""
        self._alert_rules.append(rule)

    async def start(self):
        """启动后台监控任务"""
        if not self._monitor_task or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitoring_loop())
            logger.info("Protocol health monitor started (interval=%.1fs)", self.check_interval)

    async def stop(self):
        """停止监控"""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitoring_loop(self):
        """监控主循环"""
        while True:
            try:
                await asyncio.sleep(self.check_interval)
                await self.check_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Health monitor error: %s", e)

    async def check_all(self) -> Dict[str, ProtocolHealthSnapshot]:
        """检查所有已注册的适配器"""
        results = {}
        
        for name, adapter in self._adapters.items():
            try:
                snapshot = await self._check_one(name, adapter)
                results[name] = snapshot
                self._snapshots[name] = snapshot
                
                # 触发告警回调
                if snapshot.alerts and self.alert_callback:
                    for alert in snapshot.alerts:
                        self.alert_callback(alert)
                        
            except Exception as e:
                logger.error("Health check error for %s: %s", name, e)
                
        return results

    async def _check_one(self, name: str, adapter) -> ProtocolHealthSnapshot:
        """检查单个适配器的健康状态"""
        snapshot = self._snapshots.get(name, ProtocolHealthSnapshot(protocol_name=name))
        snapshot.last_check_time = time.time()
        
        try:
            # 尝试调用 health_check 方法
            if hasattr(adapter, 'health_check'):
                health_data = await adapter.health_check()
                snapshot.connected = health_data.get('connected', False)
                snapshot.metrics = health_data.get('metrics', {})
                snapshot.uptime_seconds = health_data.get('connection_uptime_s', 0) or \
                                       health_data.get('metrics', {}).get('uptime_seconds', 0)
            
            elif hasattr(adapter, 'is_connected'):
                snapshot.connected = adapter.is_connected
                
            # 确定健康状态
            if snapshot.connected:
                snapshot.status = HealthStatus.HEALTHY
            else:
                snapshot.status = HealthStatus.UNHEALTHY
                
            # 检查告警规则
            snapshot.alerts.clear()
            for rule in self._alert_rules:
                metric_value = snapshot.metrics.get(rule.metric)
                if metric_value is not None and rule.check(metric_value):
                    alert = {
                        "protocol": name,
                        "rule_metric": rule.metric,
                        "value": metric_value,
                        "threshold": rule.threshold,
                        "severity": rule.severity,
                        "time": snapshot.last_check_time,
                    }
                    snapshot.alerts.append(alert)
                    self._alert_history.append(alert)
                    
                    # 如果有严重告警，降级状态
                    if rule.severity == "critical":
                        snapshot.status = HealthStatus.UNHEALTHY
                    elif rule.severity == "warning" and snapshot.status == HealthStatus.HEALTHY:
                        snapshot.status = HealthStatus.DEGRADED
                        
        except Exception as e:
            snapshot.status = HealthStatus.UNKNOWN
            snapshot.alerts.append({
                "protocol": name,
                "error": str(e),
                "severity": "critical",
                "time": snapshot.last_check_time,
            })
            
        return snapshot

    def get_overall_status(self) -> Dict[str, Any]:
        """
        获取整体健康状态汇总.
        
        Returns:
            包含各协议状态和系统级状态的字典
        """
        snapshots = list(self._snapshots.values())
        
        if not snapshots:
            return {"status": "no_adapters", "protocols": {}}
            
        status_counts = {s.value: 0 for s in HealthStatus}
        connected_count = sum(1 for s in snapshots if s.connected)
        
        for snap in snapshots:
            status_counts[snap.status.value] += 1
            
        # 计算系统级状态
        total = len(snapshots)
        if status_counts["unhealthy"] > 0:
            overall = HealthStatus.UNHEALTHY
        elif status_counts["degraded"] > 0:
            overall = HealthStatus.DEGRADED
        elif status_counts["healthy"] == total:
            overall = HealthStatus.HEALTHY
        else:
            overall = HealthStatus.UNKNOWN
            
        return {
            "overall_status": overall.value,
            "total_protocols": total,
            "connected_protocols": connected_count,
            "disconnected_protocols": total - connected_count,
            "status_breakdown": status_counts,
            "protocols": {name: snap.to_dict() for name, snap in self._snapshots.items()},
            "recent_alerts": self._alert_history[-20:] if self._alert_history else [],
            "monitor_uptime_s": round(time.time() - self._start_time, 1),
        }

    async def get_health_api_response(self) -> Dict:
        """生成 API 格式的健康响应 (可直接返回给前端)"""
        overall = self.get_overall_status()
        
        # HTTP 状态码映射
        http_codes = {
            HealthStatus.HEALTHY: 200,
            HealthStatus.DEGRADED: 200,  # 功能正常但有问题
            HealthStatus.UNHEALTHY: 503,  # Service Unavailable
            HealthStatus.UNKNOWN: 500,
        }
        
        return {
            "statusCode": http_codes.get(overall["overall_status"], 200),
            "body": {
                "status": "ok" if overall["overall_status"] in ("healthy", "degraded") else "error",
                "data": overall,
                "timestamp": time.time(),
            },
        }
