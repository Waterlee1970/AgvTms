"""
SLO (Service Level Objective) 监控系统 — Phase 3 可靠性核心.

对标 Google SRE Book / 极智嘉RMS SLA体系.

功能:
  1. SLI (Service Level Indicator) 指标采集:
     - 可用性 (Availability): 成功请求比例
     - 延迟 (Latency): P50/P95/P99/P99.9
     - 错误率 (Error Rate): HTTP 5xx比例
     - 吞吐量 (Throughput): QPS/RPS
     - 饱和度 (Saturation): CPU/内存/连接池使用率
  
  2. SLO 目标管理与预算:
     - 按服务/端点配置 SLO 目标
     - Error Budget 计算 (月度/周度)
     - 预算消耗率实时计算
     - 预算耗尽告警触发
  
  3. SLO 违规检测与报告:
     - 滑动窗口统计 (1h/24h/7d/30d)
     - 烧毁速率 (Burn Rate) 预测
     - 多级告警阈值 (快/中/慢)
     - 历史趋势分析

依赖:
  - prometheus-client (已有)
  - time (标准库)

使用示例:
    from app.core.slo_monitor import slo_registry, SLOConfig
    
    # 注册端点 SLO
    slo_registry.register(
        endpoint="/api/v2/tasks",
        config=SLOConfig(
            availability_target=0.999,      # 99.9%可用性
            latency_p99_target_ms=500,       # P99延迟 < 500ms
            error_rate_target=0.001,         # 错误率 < 0.1%
        )
    )
    
    # 记录请求结果
    slo_registry.record_request(
        endpoint="/api/v2/tasks",
        success=True,
        latency_ms=123.5,
        status_code=200,
    )
    
    # 获取当前 SLO 状态
    status = slo_registry.get_slo_status("/api/v2/tasks")
    print(f"可用性: {status.availability:.4f}")
    print(f"剩余预算: {status.error_budget_remaining:.2%}")
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache


# ==================== 数据模型 ====================

class AlertSeverity(str, Enum):
    """告警严重级别"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class SLOConfig:
    """
    SLO 配置.
    
    对标工业界标准:
    - 高可用服务: 99.9%+ (月停机 < 43分钟)
    - 关键任务: 99.99%+ (月停机 < 4.3分钟)
    - 一般服务: 99%+ (月停机 < 7.2小时)
    """
    availability_target: float = 0.999        # 可用性目标 (99.9%)
    latency_p50_target_ms: float = 100.0       # P50延迟目标
    latency_p95_target_ms: float = 300.0       # P95延迟目标
    latency_p99_target_ms: float = 500.0       # P99延迟目标
    error_rate_target: float = 0.001           # 错误率目标 (0.1%)
    
    # 预算计算周期 (默认30天)
    budget_period_days: int = 30
    
    def __post_init__(self):
        """参数校验"""
        assert 0 < self.availability_target <= 1.0, f"无效的可用性目标: {self.availability_target}"
        assert self.latency_p50_target_ms > 0, "P50目标必须大于0"
        assert self.latency_p99_target_ms >= self.latency_p50_target_ms, "P99必须>=P50"
        assert 0 <= self.error_rate_target <= 1.0, f"错误率目标必须在[0,1]: {self.error_rate_target}"


@dataclass
class RequestRecord:
    """单次请求记录"""
    timestamp: float
    success: bool
    latency_ms: float
    status_code: int
    
    @property
    def is_error(self) -> bool:
        """是否为错误 (5xx服务端错误)"""
        return self.status_code >= 500
    
    @property
    def is_client_error(self) -> bool:
        """客户端错误 (4xx)"""
        return 400 <= self.status_code < 500


class WindowSize(Enum):
    """统计窗口大小"""
    HOUR_1 = "1h"
    HOUR_6 = "6h"
    HOUR_24 = "24h"
    DAY_7 = "7d"
    DAY_30 = "30d"
    
    @property
    def seconds(self) -> int:
        mapping = {
            self.HOUR_1: 3600,
            self.HOUR_6: 21600,
            self.HOUR_24: 86400,
            self.DAY_7: 604800,
            self.DAY_30: 2592000,
        }
        return mapping[self]


@dataclass
class SLIMetrics:
    """SLI指标快照"""
    window: WindowSize
    total_requests: int = 0
    successful_requests: int = 0
    error_requests: int = 0          # 5xx
    client_error_requests: int = 0   # 4xx
    
    # 延迟分布
    latency_sum: float = 0.0
    latency_min: float = float('inf')
    latency_max: float = 0.0
    latency_samples: List[float] = field(default_factory=list)  # 用于分位数计算
    
    @property
    def availability(self) -> float:
        """可用性 (成功率)"""
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests
    
    @property
    def error_rate(self) -> float:
        """错误率 (5xx比例)"""
        if self.total_requests == 0:
            return 0.0
        return self.error_requests / self.total_requests
    
    @property
    def avg_latency(self) -> float:
        """平均延迟"""
        if self.total_requests == 0:
            return 0.0
        return self.latency_sum / self.total_requests
    
    def get_percentile(self, p: float) -> float:
        """计算分位数延迟 (近似算法)"""
        if not self.latency_samples:
            return 0.0
        sorted_samples = sorted(self.latency_samples)
        idx = int(len(sorted_samples) * p / 100)
        idx = min(idx, len(sorted_samples) - 1)
        return sorted_samples[idx]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'window': self.window.value,
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'error_requests': self.error_requests,
            'availability': round(self.availability, 6),
            'error_rate': round(self.error_rate, 6),
            'avg_latency_ms': round(self.avg_latency, 2),
            'p50_ms': round(self.get_percentile(50), 2),
            'p95_ms': round(self.get_percentile(95), 2),
            'p99_ms': round(self.get_percentile(99), 2),
        }


@dataclass
class ErrorBudget:
    """错误预算"""
    total_budget_pct: float = 100.0     # 初始预算百分比
    consumed_pct: float = 0.0           # 已消耗百分比
    remaining_pct: float = 100.0       # 剩余百分比
    
    period_start: datetime = field(default_factory=datetime.utcnow)
    period_end: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(days=30))
    
    # 烧毁速率预测
    burn_rate_current: float = 0.0      # 当前烧毁速率
    burn_rate_1h: float = 0.0          # 1小时前速率
    burn_rate_6h: float = 0.0          # 6小时前速率
    
    @property
    def is_exhausted(self) -> bool:
        """预算是否已耗尽"""
        return self.remaining_pct <= 0
    
    @property
    def days_until_exhaustion(self) -> Optional[float]:
        """预计几天后预算耗尽 (None表示不会耗尽)"""
        if self.burn_rate_current <= 0:
            return None
        return self.remaining_pct / self.burn_rate_current * 30  # 按30天周期折算
    
    @property
    def burn_alert_level(self) -> Optional[AlertSeverity]:
        """
        烧毁速率告警级别.
        
        对标Google SRE多快速告警策略:
        - 快速 (2% in 1h): 如果持续，5天耗尽 → CRITICAL
        - 中速 (5% in 6h): 如果持续，5天耗尽 → WARNING
        - 慢速 (10% in 3d): 如果持续，30天耗尽 → INFO
        """
        if self.is_exhausted:
            return AlertSeverity.EMERGENCY
        
        # 快速烧毁: 1小时内消耗超过2%
        if self.burn_rate_1h > 2.0:
            return AlertSeverity.CRITICAL
        
        # 中速烧灭: 6小时内消耗超过5%
        if self.burn_rate_6h > 5.0 / 6:
            return AlertSeverity.WARNING
        
        # 慢速烧灭: 接近预算警戒线
        if self.remaining_pct < 20.0:
            return AlertSeverity.INFO
        
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_budget_pct': round(self.total_budget_pct, 2),
            'consumed_pct': round(self.consumed_pct, 2),
            'remaining_pct': round(self.remaining_pct, 2),
            'is_exhausted': self.is_exhausted,
            'days_until_exhaustion': round(self.days_until_exhaustion, 1) if self.days_until_exhaustion else None,
            'burn_rates': {
                'current_per_day': round(self.burn_rate_current, 4),
                '1h_window': round(self.burn_rate_1h, 4),
                '6h_window': round(self.burn_rate_6h, 4),
            },
            'alert_level': self.burn_alert_level.value if self.burn_alert_level else None,
            'period': {
                'start': self.period_start.isoformat(),
                'end': self.period_end.isoformat(),
            }
        }


@dataclass
class SLOStatus:
    """单个端点的完整SLO状态"""
    endpoint: str
    config: SLOConfig
    metrics: Dict[WindowSize, SLIMetrics] = field(default_factory=dict)
    budget: ErrorBudget = field(default_factory=ErrorBudget)
    last_updated: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def overall_status(self) -> HealthStatus:
        """综合健康状态"""
        from .ha_health import HealthStatus
        # 检查各窗口是否达标
        for window, metrics in self.metrics.items():
            if metrics.availability < self.config.availability_target * 0.95:  # 容忍5%偏差
                if metrics.availability < self.config.availability_target * 0.9:
                    return HealthStatus.UNHEALTHY
                return HealthStatus.DEGRADED
            
            if metrics.error_rate > self.config.error_rate_target * 2:
                return HealthStatus.DEGRADED
        
        if self.budget.is_exhausted:
            return HealthStatus.UNHEALTHY
        
        if self.budget.burn_alert_level in [AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY]:
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY
    
    def to_dict(self, verbose: bool = False) -> Dict[str, Any]:
        result = {
            'endpoint': self.endpoint,
            'slo_targets': {
                'availability': f"{self.config.availability_target:.3%}",
                'p99_latency_ms': self.config.latency_p99_target_ms,
                'error_rate': f"{self.config.error_rate_target:.4f}",
            },
            'overall_status': self.overall_status.value,
            'budget': self.budget.to_dict(),
            'last_updated': self.last_updated.isoformat(),
        }
        
        if verbose:
            result['metrics_by_window'] = {
                w.value: m.to_dict() for w, m in self.metrics.items()
            }
        
        return result


# ==================== 核心引擎 ====================

# 循环导入解决
try:
    from .ha_health import HealthStatus
except ImportError:
    class HealthStatus(str, Enum):
        HEALTHY = "healthy"
        DEGRADED = "degraded"
        UNHEALTHY = "unhealthy"


class SLORegistry:
    """
    SLO注册中心 — 全局单例.
    
    功能:
    1. 管理所有端点的SLO配置
    2. 采集和聚合请求指标
    3. 计算错误预算和烧毁速率
    4. 触发SLO违规告警
    
    线程安全: 使用threading.Lock保护共享状态
    """
    
    def __init__(self):
        self._configs: Dict[str, SLOConfig] = {}
        self._records: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100000))  # 每端点保留最近10万条
        self._lock = threading.RLock()
        self._start_time = time.time()
        
        # 默认全局SLO配置
        self._default_config = SLOConfig()
        
        # 告警回调列表
        self._alert_callbacks: List[callable] = []
    
    def register(
        self,
        endpoint: str,
        config: Optional[SLOConfig] = None,
        overwrite: bool = False,
    ) -> None:
        """
        注册端点SLO配置.
        
        Args:
            endpoint: API端点路径 (如 "/api/v2/tasks")
            config: SLO配置, None则使用默认配置
            overwrite: 是否覆盖已有配置
        """
        with self._lock:
            if endpoint in self._configs and not overwrite:
                logger.debug(f"SLO already registered for {endpoint}, skipping")
                return
            
            self._configs[endpoint] = config or self._default_config
            logger.info(f"SLO registered: endpoint={endpoint}, target={config}")
    
    def unregister(self, endpoint: str) -> None:
        """取消注册端点SLO"""
        with self._lock:
            self._configs.pop(endpoint, None)
            self._records.pop(endpoint, None)
    
    def record_request(
        self,
        endpoint: str,
        success: bool,
        latency_ms: float,
        status_code: int = 200,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        记录一次请求结果.
        
        此方法应该在中间件或路由处理完成后调用.
        
        Args:
            endpoint: 请求端点
            success: 是否成功 (2xx/3xx视为成功)
            latency_ms: 请求耗时(ms)
            status_code: HTTP状态码
            timestamp: 时间戳, 默认为当前时间
        """
        record = RequestRecord(
            timestamp=timestamp or time.time(),
            success=success,
            latency_ms=latency_ms,
            status_code=status_code,
        )
        
        with self._lock:
            self._records[endpoint].append(record)
    
    def get_slo_status(self, endpoint: str, verbose: bool = False) -> Optional[SLOStatus]:
        """
        获取端点的SLO状态.
        
        Args:
            endpoint: API端点路径
            verbose: 是否包含详细窗口数据
            
        Returns:
            SLOStatus对象, 未注册则返回None
        """
        with self._lock:
            config = self._configs.get(endpoint)
            if not config:
                return None
            
            records = list(self._records.get(endpoint, []))
        
        if not records:
            # 无数据时返回初始状态 (假设完全正常)
            return SLOStatus(endpoint=endpoint, config=config)
        
        now = time.time()
        metrics: Dict[WindowSize, SLIMetrics] = {}
        
        for window in WindowSize:
            cutoff = now - window.seconds
            window_records = [r for r in records if r.timestamp >= cutoff]
            
            if not window_records:
                continue
            
            sla = SLIMetrics(window=window)
            sla.total_requests = len(window_records)
            
            for r in window_records:
                if r.success and not r.is_error:
                    sla.successful_requests += 1
                if r.is_error:
                    sla.error_requests += 1
                if r.is_client_error:
                    sla.client_error_requests += 1
                
                sla.latency_sum += r.latency_ms
                sla.latency_min = min(sla.latency_min, r.latency_ms)
                sla.latency_max = max(sla.latency_max, r.latency_ms)
                
                # 采样存储 (避免内存爆炸, 最多保留10000个样本用于分位数计算)
                if len(sla.latency_samples) < 10000:
                    sla.latency_samples.append(r.latency_ms)
            
            metrics[window] = sla
        
        # 计算错误预算
        budget = self._calculate_budget(config, metrics)
        
        return SLOStatus(
            endpoint=endpoint,
            config=config,
            metrics=metrics,
            budget=budget,
            last_updated=datetime.utcnow(),
        )
    
    def _calculate_budget(
        self,
        config: SLOConfig,
        metrics: Dict[WindowSize, SLIMetrics],
    ) -> ErrorBudget:
        """
        计算错误预算.
        
        预算 = 1 - 可用性目标
        已消耗 = (实际错误请求数) / (总请求数 * (1 - 目标可用性))
        """
        # 使用24小时窗口作为主要计算依据
        window_24h = metrics.get(WindowSize.HOUR_24)
        if not window_24h or window_24h.total_requests == 0:
            return ErrorBudget()
        
        # 允许的错误数量
        allowed_errors = window_24h.total_requests * (1 - config.availability_target)
        actual_errors = window_24h.error_requests
        
        if allowed_errors == 0:
            consumed = 100.0 if actual_errors > 0 else 0.0
        else:
            consumed = min(100.0, (actual_errors / allowed_errors) * 100)
        
        budget = ErrorBudget(
            consumed_pct=consumed,
            remaining_pct=max(0.0, 100.0 - consumed),
            period_start=datetime.utcnow() - timedelta(days=config.budget_period_days),
            period_end=datetime.utcnow(),
        )
        
        # 计算烧毁速率 (需要多个窗口数据对比)
        window_1h = metrics.get(WindowSize.HOUR_1)
        window_6h = metrics.get(WindowSize.HOUR_6)
        
        if window_1h and window_1h.total_requests > 0:
            hourly_burn = (window_1h.error_rate / (1 - config.availability_target)) * 100 if config.availability_target < 1 else 0
            budget.burn_rate_1h = hourly_burn
            budget.burn_rate_current = hourly_burn * 24  # 折算到每天
        
        if window_6h and window_6h.total_requests > 0:
            hourly_burn_6h = (window_6h.error_rate / (1 - config.availability_target)) * 100 / 6 if config.availability_target < 1 else 0
            budget.burn_rate_6h = hourly_burn_6h
        
        return budget
    
    def get_all_statuses(self, verbose: bool = False) -> List[SLOStatus]:
        """获取所有已注册端点的SLO状态"""
        with self._lock:
            endpoints = list(self._configs.keys())
        
        statuses = []
        for ep in endpoints:
            status = self.get_slo_status(ep, verbose=verbose)
            if status:
                statuses.append(status)
        
        return statuses
    
    def get_system_summary(self) -> Dict[str, Any]:
        """
        获取系统整体SLO摘要.
        
        用于Dashboard展示和管理层报告.
        """
        statuses = self.get_all_statuses(verbose=False)
        
        if not statuses:
            return {
                'total_endpoints': 0,
                'overall_health': 'unknown',
                'avg_availability': None,
                'budgets_summary': {'healthy': 0, 'warning': 0, 'exhausted': 0},
                'timestamp': datetime.utcnow().isoformat(),
            }
        
        # 统计各状态数量
        healthy_count = sum(1 for s in statuses if s.overall_status == HealthStatus.HEALTHY)
        degraded_count = sum(1 for s in statuses if s.overall_status == HealthStatus.DEGRADED)
        unhealthy_count = sum(1 for s in statuses if s.overall_status == HealthStatus.UNHEALTHY)
        
        # 平均可用性 (按请求量加权)
        weighted_avail_sum = 0.0
        total_weight = 0.0
        for s in statuses:
            m_24h = s.metrics.get(WindowSize.HOUR_24)
            if m_24h and m_24h.total_requests > 0:
                weighted_avail_sum += m_24h.availability * m_24h.total_requests
                total_weight += m_24h.total_requests
        
        avg_availability = weighted_avail_sum / total_weight if total_weight > 0 else None
        
        # 整体状态判定
        if unhealthy_count > 0:
            overall = HealthStatus.UNHEALTHY.value
        elif degraded_count > 0:
            overall = HealthStatus.DEGRADED.value
        else:
            overall = HealthStatus.HEALTHY.value
        
        return {
            'total_endpoints': len(statuses),
            'overall_health': overall,
            'health_breakdown': {
                'healthy': healthy_count,
                'degraded': degraded_count,
                'unhealthy': unhealthy_count,
            },
            'avg_availability_24h': f"{avg_availability:.4%}" if avg_availability else None,
            'budgets_summary': {
                'healthy': sum(1 for s in statuses if not s.budget.is_exhausted and s.budget.remaining_pct > 20),
                'warning': sum(1 for s in statuses if not s.budget.is_exhausted and s.budget.remaining_pct <= 20),
                'exhausted': sum(1 for s in statuses if s.budget.is_exhausted),
            },
            'top_consumers': [
                {
                    'endpoint': s.endpoint,
                    'consumed_pct': round(s.budget.consumed_pct, 2),
                }
                for s in sorted(statuses, key=lambda x: x.budget.consumed_pct, reverse=True)[:5]
            ],
            'uptime_seconds': round(time.time() - self._start_time, 1),
            'timestamp': datetime.utcnow().isoformat(),
        }
    
    def add_alert_callback(self, callback: callable) -> None:
        """
        添加告警回调.
        
        当SLO违规或预算即将耗尽时调用.
        
        回调签名: callback(severity: AlertSeverity, message: str, slo_status: SLOStatus)
        """
        self._alert_callbacks.append(callback)
    
    def _check_and_alert(self, status: SLOStatus) -> None:
        """检查并触发告警"""
        alert_level = status.budget.burn_alert_level
        if not alert_level:
            return
        
        msg = (
            f"SLO Alert [{alert_level.value}] for {status.endpoint}: "
            f"budget={status.budget.remaining_pct:.1%}%, "
            f"burn_rate={status.budget.burn_rate_current:.2f}/day"
        )
        
        for cb in self._alert_callbacks:
            try:
                cb(alert_level, msg, status)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")
    
    def cleanup_old_records(self, max_age_seconds: int = 2592000) -> int:
        """
        清理过期记录 (默认30天).
        
        Returns:
            清理的记录数
        """
        cutoff = time.time() - max_age_seconds
        cleaned = 0
        
        with self._lock:
            for endpoint, records in self._records.items():
                original_len = len(records)
                # deque不支持直接过滤, 需要重建
                while records and records[0].timestamp < cutoff:
                    records.popleft()
                    cleaned += 1
        
        return cleaned


# ==================== 全局单例 ====================

logger = __import__('logging').getLogger(__name__)

# 全局SLO注册中心实例
slo_registry = SLORegistry()


def init_default_slo_configs():
    """
    初始化默认的SLO配置.
    
    在应用启动时调用, 为关键API端点注册SLO目标.
    """
    default_configs = {
        # 核心调度API - 最高可靠性要求
        '/api/v2/schedule/run': SLOConfig(
            availability_target=0.9999,      # 99.99% (月停机<4.3分钟)
            latency_p99_target_ms=2000.0,    # 调度允许较长时间
            error_rate_target=0.0001,        # 0.01%错误率
        ),
        '/api/v2/tasks': SLOConfig(
            availability_target=0.999,       # 99.9%
            latency_p99_target_ms=500.0,     # P99 < 500ms
            error_rate_target=0.001,         # 0.1%
        ),
        '/api/v2/vehicles': SLOConfig(
            availability_target=0.999,
            latency_p99_target_ms=300.0,
            error_rate_target=0.001,
        ),
        
        # VDA5050协议接口 - 工业级可靠性
        '/api/v2/vda5050': SLOConfig(
            availability_target=0.9999,      # AGV通信不可中断
            latency_p99_target_ms=200.0,     # 低延迟要求
            error_rate_target=0.0001,
        ),
        
        # 监控和分析 - 可容忍短暂降级
        '/api/v2/analytics': SLOConfig(
            availability_target=0.99,        # 99%
            latency_p99_target_ms=2000.0,    # 分析查询可能较慢
            error_rate_target=0.01,          # 1%
        ),
        '/metrics': SLOConfig(
            availability_target=0.99,
            latency_p99_target_ms=500.0,
            error_rate_target=0.01,
        ),
    }
    
    for endpoint, config in default_configs.items():
        slo_registry.register(endpoint, config)
    
    logger.info(f"Default SLO configs initialized: {len(default_configs)} endpoints")


# 导出便捷函数
__all__ = [
    'slo_registry', 'SLOConfig', 'SLOStatus', 'ErrorBudget',
    'SLIMetrics', 'AlertSeverity', 'init_default_slo_configs',
]
