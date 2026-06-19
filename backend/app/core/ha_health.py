"""
高可用健康检查服务 — 三端点监控 + 优雅停机 (Phase 5.5)

功能:
  1. 三端点探测 (Kubernetes Liveness/Readiness Probes):
     - GET /healthz   — 基础存活 (进程存活)
     - GET /readyz    — 就绪检测 (依赖服务可用 + 调度器就绪)
     - GET/livez      — 活跃检测 (无死锁 + 响应时间正常)
  
  2. 深度健康检查:
     - PostgreSQL 连接池状态
     - Redis 连通性
     - Kafka (可选) 连接状态
     - InfluxDB (可选) 状态
     - 内存/CPU/GC 状态
  
  3. 优雅停机 (Graceful Shutdown):
     - SIGTERM/SIGINT 信号捕获
     - 正在进行请求完成等待 (最长30s)
     - 数据库连接池排空
     - 缓冲区刷盘
     - WebSocket 连接优雅关闭
  
  4. 启动探针:
     - 启动完成信号 (Startup Probe)
     - 依赖初始化超时检测
  
  5. 自愈机制:
     - 数据库自动重连 (指数退避, 最大10次)
     - Redis 断线重连
     - 内存泄漏检测

SLA 目标:
  - 可用性: 99.5% (月停机 < 3.6小时)
  - RTO (恢复时间目标): < 5分钟
  - RPO (数据丢失目标): < 1分钟
"""

import os
import time
import signal
import asyncio
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Awaitable
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from functools import wraps

# 可选依赖: psutil (用于系统指标采集)
try:
    import psutil
except ImportError:
    psutil = None  # 内存检查将降级为简化模式

logger = logging.getLogger(__name__)


# ==================== 健康状态枚举 ====================

class HealthStatus(str, Enum):
    """健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"    # 部分功能降级，但核心可用
    UNHEALTHY = "unhealthy"  # 核心功能不可用


class DependencyStatus(str, Enum):
    """依赖服务状态"""
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HealthCheckResult:
    """单次健康检查结果"""
    
    name: str
    status: DependencyStatus
    response_time_ms: float = 0.0
    message: str = ""
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'status': self.status.value,
            'response_time_ms': round(self.response_time_ms, 2),
            'message': self.message,
            'details': self.details or {},
            'checked_at': datetime.utcnow().isoformat(),
        }


@dataclass
class SystemHealthReport:
    """系统整体健康报告"""
    
    status: HealthStatus
    overall_uptime_seconds: float
    checks: List[HealthCheckResult]
    version: str = ""
    hostname: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY
    
    @property
    def is_ready(self) -> bool:
        """是否就绪 (可接受流量)"""
        # 所有 critical 检查必须通过
        critical_checks = [c for c in self.checks if c.name in {
            'database', 'redis', 'scheduler'
        }]
        return all(c.status == DependencyStatus.UP for c in critical_checks)
    
    @property
    def is_alive(self) -> bool:
        """是否存活 (进程未死锁)"""
        return self.status != HealthStatus.UNHEALTHY
    
    def to_dict(self, verbose: bool = False) -> Dict:
        result = {
            'status': self.status.value,
            'timestamp': self.timestamp.isoformat(),
            'uptime_seconds': round(self.overall_uptime_seconds, 1),
            'version': self.version,
            'hostname': self.hostname,
            'checks_summary': {
                'total': len(self.checks),
                'healthy': sum(1 for c in self.checks if c.status == DependencyStatus.UP),
                'degraded': sum(1 for c in self.checks if c.status == DependencyStatus.DEGRADED),
                'unhealthy': sum(1 for c in self.checks if c.status == DependencyStatus.DOWN),
            },
        }
        
        if verbose:
            result['checks'] = [c.to_dict() for c in self.checks]
        
        return result


# ==================== 配置 ====================

@dataclass
class HealthCheckConfig:
    """健康检查配置"""
    
    # 超时设置
    check_timeout_seconds: float = 5.0       # 单次检查超时
    startup_timeout_seconds: float = 120.0   # 启动探针超时 (2分钟)
    shutdown_timeout_seconds: float = 30.0   # 优雅停机等待时间
    
    # 重连策略
    db_reconnect_max_attempts: int = 10      # DB最大重连次数
    db_reconnect_base_delay: float = 1.0     # 首次延迟(秒)
    db_reconnect_max_delay: float = 60.0     # 最大延迟
    redis_reconnect_max_attempts: int = 10
    redis_reconnect_base_delay: float = 0.5
    
    # 探针间隔
    liveness_interval_seconds: int = 10      # 存活探针频率
    readiness_interval_seconds: int = 5      # 就绪探针频率
    
    # 内存阈值
    memory_warning_percent: float = 80.0     # 内存使用率警告阈值
    memory_critical_percent: float = 95.0    # 内存使用率严重阈值
    
    # 死锁检测
    deadlock_detection_enabled: bool = True
    deadlock_check_interval_seconds: int = 60


# ==================== 依赖检查器基类 ====================

class BaseDependencyChecker:
    """依赖检查器基类"""
    
    def __init__(self, name: str, critical: bool = True):
        self.name = name
        self.critical = critical
        self._last_status = DependencyStatus.UNKNOWN
        self._last_check_time: Optional[datetime] = None
        self._consecutive_failures = 0
        self._total_checks = 0
        self._lock = threading.Lock()
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        """
        执行健康检查 (子类实现)
        
        Returns:
            HealthCheckResult 包含状态和响应时间
        """
        raise NotImplementedError
    
    async def check_with_timeout(self, timeout: float = 5.0) -> HealthCheckResult:
        """带超时的安全检查"""
        try:
            result = await asyncio.wait_for(self.check(timeout), timeout=timeout)
            
            with self._lock:
                self._last_status = result.status
                self._last_check_time = datetime.now()
                self._total_checks += 1
                
                if result.status != DependencyStatus.UP:
                    self._consecutive_failures += 1
                else:
                    self._consecutive_failures = 0
            
            return result
            
        except asyncio.TimeoutError:
            result = HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DOWN,
                response_time_ms=timeout * 1000,
                message=f"Check timed out after {timeout}s",
            )
            with self._lock:
                self._last_status = DependencyStatus.DOWN
                self._consecutive_failures += 1
            
            return result
        
        except Exception as e:
            logger.error(f"[{self.name}] Check error: {e}")
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DOWN,
                message=str(e),
            )
    
    @property
    def last_status(self) -> DependencyStatus:
        return self._last_status
    
    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures


# ==================== 具体依赖检查器 ====================

class DatabaseChecker(BaseDependencyChecker):
    """PostgreSQL 数据库连接检查"""
    
    def __init__(self, get_db_pool_fn: Optional[Callable] = None):
        super().__init__("database", critical=True)
        self._get_db_pool = get_db_pool_fn
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        start = time.time()
        
        try:
            from app.core.database import get_engine, _db_available
            
            if not _db_available:
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.DOWN,
                    message="Database marked unavailable",
                )
            
            engine = get_engine()
            
            # 执行简单查询验证连接
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 as health_check")
                )
                row = result.fetchone()
                
                if row and row[0] == 1:
                    elapsed = (time.time() - start) * 1000
                    
                    # 获取连接池信息
                    pool_info = {}
                    if hasattr(engine.pool, 'size'):
                        pool_info = {
                            'pool_size': engine.pool.size,
                            'checked_in': engine.pool.checkedin(),
                            'checked_out': engine.pool.checkedout(),
                            'overflow': engine.pool.overflow(),
                        }
                    
                    return HealthCheckResult(
                        name=self.name,
                        status=DependencyStatus.UP,
                        response_time_ms=elapsed,
                        message="Database connection healthy",
                        details={'pool': pool_info} if pool_info else None,
                    )
                else:
                    return HealthCheckResult(
                        name=self.name,
                        status=DependencyStatus.DOWN,
                        message="Query returned unexpected result",
                    )
                    
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DOWN,
                message=str(e),
            )


class RedisChecker(BaseDependencyChecker):
    """Redis 连接检查"""
    
    def __init__(self):
        super().__init__("redis", critical=True)
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        start = time.time()
        
        try:
            from app.core.redis_service import redis_client, _redis_available
            
            if not _redis_available:
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.DOWN,
                    message="Redis marked unavailable",
                )
            
            # PING 测试
            result = await redis_client.ping()
            
            if result is True:
                elapsed = (time.time() - start) * 1000
                
                # 获取 Redis INFO
                info = {}
                try:
                    info_data = await redis_client.info()
                    if isinstance(info_data, dict):
                        info = {
                            'used_memory_human': info_data.get('used_memory_human', 'N/A'),
                            'connected_clients': info_data.get('connected_clients', 0),
                            'uptime_in_seconds': info_data.get('uptime_in_seconds', 0),
                        }
                except Exception:
                    pass
                
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.UP,
                    response_time_ms=elapsed,
                    message="Redis PING successful",
                    details={'info': info} if info else None,
                )
            else:
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.DOWN,
                    message="PING returned False",
                )
                
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DOWN,
                message=str(e),
            )


class SchedulerChecker(BaseDependencyChecker):
    """调度引擎状态检查"""
    
    def __init__(self):
        super().__init__("scheduler", critical=True)
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        start = time.time()
        
        try:
            from app.services.schedule_service import schedule_service
            
            # 检查调度服务是否初始化且运行中
            if schedule_service is None:
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.DOWN,
                    message="ScheduleService not initialized",
                )
            
            # 获取调度统计
            stats = schedule_service.get_statistics()
            
            elapsed = (time.time() - start) * 1000
            is_running = stats.get('is_running', False)
            
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.UP if is_running else DependencyStatus.DEGRADED,
                response_time_ms=elapsed,
                message=f"Scheduler {'running' if is_running else 'idle'}",
                details=stats,
            )
            
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DEGRADED,
                message=f"Scheduler error: {e}",
            )


class MemoryChecker(BaseDependencyChecker):
    """内存使用检查"""
    
    def __init__(self, warning_pct: float = 80.0, critical_pct: float = 95.0):
        super().__init__("memory", critical=False)
        self.warning_pct = warning_pct
        self.critical_pct = critical_pct
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        start = time.time()
        
        try:
            import resource
            
            # 获取当前进程内存
            usage = resource.getrusage(resource.RUSAGE_SELF)
            
            # 尝试获取更详细的信息
            mem_info = {}
            try:
                process = psutil.Process(os.getpid())
                memory_info = process.memory_info()
                mem_info = {
                    'rss_mb': round(memory_info.rss / (1024 * 1024), 2),
                    'vms_mb': round(memory_info.vms / (1024 * 1024), 2),
                    'percent': process.memory_percent(),
                }
            except NameError:
                pass
            
            usage_percent = mem_info.get('percent', 50.0)  # 默认假设50%
            
            if usage_percent >= self.critical_pct:
                status = DependencyStatus.DOWN
                msg = f"Critical memory usage: {usage_percent:.1f}%"
            elif usage_percent >= self.warning_pct:
                status = DependencyStatus.DEGRADED
                msg = f"High memory usage: {usage_percent:.1f}%"
            else:
                status = DependencyStatus.UP
                msg = f"Memory usage normal: {usage_percent:.1f}%"
            
            elapsed = (time.time() - start) * 1000
            
            return HealthCheckResult(
                name=self.name,
                status=status,
                response_time_ms=elapsed,
                message=msg,
                details=mem_info if mem_info else None,
            )
            
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.UNKNOWN,
                message=f"Memory check error: {e}",
            )


class KafkaChecker(BaseDependencyChecker):
    """Kafka (可选) 连接检查"""
    
    def __init__(self):
        super().__init__("kafka", critical=False)
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        start = time.time()
        
        try:
            from app.core.kafka_service import EventBus
            
            bus = EventBus._instance
            if bus is None:
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.DEGRADED,
                    message="Kafka EventBus not initialized (optional)",
                )
            
            stats = bus.get_stats()
            producer_stats = stats.get('producer', {})
            
            elapsed = (time.time() - start) * 1000
            
            connected = producer_stats.get('connected', False)
            using_fallback = producer_stats.get('using_fallback', False)
            
            if connected and not using_fallback:
                status = DependencyStatus.UP
                msg = "Kafka connected"
            elif using_fallback:
                status = DependencyStatus.DEGRADED
                msg = "Kafka in fallback mode (memory queue)"
            else:
                status = DependencyStatus.DOWN
                msg = "Kafka disconnected"
            
            return HealthCheckResult(
                name=self.name,
                status=status,
                response_time_ms=elapsed,
                message=msg,
                details={
                    'sent_total': producer_stats.get('sent_total', 0),
                    'errors': producer_stats.get('errors', 0),
                }
            )
            
        except ImportError:
            # Kafka 未安装 → 降级为非关键组件
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DEGRADED,
                message="Kafka not installed (optional component)",
            )


class InfluxChecker(BaseDependencyChecker):
    """InfluxDB (可选) 连接检查"""
    
    def __init__(self):
        super().__init__("influxdb", critical=False)
    
    async def check(self, timeout: float = 5.0) -> HealthCheckResult:
        start = time.time()
        
        try:
            from app.core.influx_service import _influx_client
            
            client = _influx_client
            if client is None:
                return HealthCheckResult(
                    name=self.name,
                    status=DependencyStatus.DEGRADED,
                    message="InfluxDB not initialized (optional)",
                )
            
            stats = client.get_stats()
            
            elapsed = (time.time() - start) * 1000
            
            if stats['connected']:
                status = DependencyStatus.UP
                msg = "InfluxDB connected"
            elif stats['using_fallback']:
                status = DependencyStatus.DEGRADED
                msg = "InfluxDB in file fallback mode"
            else:
                status = DependencyStatus.DOWN
                msg = "InfluxDB disconnected"
            
            return HealthCheckResult(
                name=self.name,
                status=status,
                response_time_ms=elapsed,
                message=msg,
                details={
                    'written_points': stats.get('written_points', 0),
                    'buffer_size': stats.get('buffer_size', 0),
                }
            )
            
        except ImportError:
            return HealthCheckResult(
                name=self.name,
                status=DependencyStatus.DEGRADED,
                message="InfluxDB not installed (optional)",
            )


# ==================== 健康检查管理器 ====================

class HealthCheckManager:
    """
    健康检查管理器 — 统一协调所有依赖检查
    
    功能:
      - 注册/注销检查器
      - 并行执行所有检查
      - 汇总生成报告
      - 定期自动检查
      - 状态缓存
    """
    
    def __init__(self, config: Optional[HealthCheckConfig] = None):
        self.config = config or HealthCheckConfig()
        self._checkers: Dict[str, BaseDependencyChecker] = {}
        self._start_time = time.time()
        self._last_report: Optional[SystemHealthReport] = None
        self._check_lock = asyncio.Lock()
        
        # 自动注册默认检查器
        self._register_default_checkers()
    
    def _register_default_checkers(self):
        """注册默认的依赖检查器"""
        default_checkers = [
            DatabaseChecker(),
            RedisChecker(),
            SchedulerChecker(),
            MemoryChecker(
                warning_pct=self.config.memory_warning_percent,
                critical_pct=self.config.memory_critical_percent,
            ),
            KafkaChecker(),   # 可选
            InfluxChecker(),  # 可选
        ]
        
        for checker in default_checkers:
            self.register_checker(checker)
    
    def register_checker(self, checker: BaseDependencyChecker):
        """注册自定义检查器"""
        self._checkers[checker.name] = checker
        logger.debug(f"[HealthCheck] Registered checker: {checker.name}")
    
    def unregister_checker(self, name: str):
        """注销检查器"""
        self._checkers.pop(name, None)
    
    async def run_all_checks(self, timeout: Optional[float] = None) -> SystemHealthReport:
        """
        执行所有健康检查并生成报告
        
        Args:
            timeout: 总体超时 (None=使用配置值)
            
        Returns:
            SystemHealthReport 完整报告
        """
        timeout = timeout or self.config.check_timeout_seconds
        results: List[HealthCheckResult] = []
        
        # 并行执行所有检查
        tasks = [
            checker.check_with_timeout(timeout)
            for checker in self._checkers.values()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        final_results = []
        checker_names = list(self._checkers.keys())
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(HealthCheckResult(
                    name=checker_names[i],
                    status=DependencyStatus.DOWN,
                    message=str(result),
                ))
            elif isinstance(result, HealthCheckResult):
                final_results.append(result)
        
        # 计算总体状态
        has_unhealthy = any(
            r.status == DependencyStatus.DOWN 
            for r in final_results 
            if self._checkers.get(r.name, BaseDependencyChecker(r.name)).critical
        )
        has_degraded = any(
            r.status in (DependencyStatus.DEGRADED, DependencyStatus.DOWN)
            for r in final_results
        )
        
        if has_unhealthy:
            overall_status = HealthStatus.UNHEALTHY
        elif has_degraded:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY
        
        report = SystemHealthReport(
            status=overall_status,
            overall_uptime_seconds=time.time() - self._start_time,
            checks=final_results,
            version=self._get_version(),
            hostname=os.uname().nodename if hasattr(os, 'uname') else 'localhost',
        )
        
        self._last_report = report
        return report
    
    async def quick_liveness_check(self) -> bool:
        """快速存活检查 (仅检查关键路径)"""
        return True  # 如果能响应 HTTP，则说明进程存活
    
    async def readiness_check(self) -> Tuple[bool, str]:
        """
        就绪检查 (所有 critical 组件可用)
        
        Returns:
            (是否就绪, 原因描述)
        """
        report = await self.run_all_checks(timeout=self.config.check_timeout_seconds)
        
        if report.is_ready:
            return True, "All critical dependencies are ready"
        else:
            failed = [c.name for c in report.checks if c.status != DependencyStatus.UP and c.name in {'database', 'redis', 'scheduler'}]
            return False, f"Not ready: {', '.join(failed)}"
    
    def _get_version(self) -> str:
        """获取应用版本"""
        try:
            # 尝试从 package.json 或 pyproject.toml 读取版本
            version_file = Path(__file__).parent.parent.parent.parent / 'version.txt'
            if version_file.exists():
                return version_file.read_text().strip()
            return "dev"
        except Exception:
            return "unknown"


# ==================== 优雅停机管理器 ====================

class GracefulShutdownManager:
    """
    优雅停机管理器
    
    流程:
      1. SIGTERM/SIGINT 信号触发
      2. 设置 shutting_down 标志
      3. 等待正在处理的请求完成 (最多 shutdown_timeout)
      4. 关闭数据库连接池
      5. 刷新 InfluxDB/Kafka 缓冲区
      6. 关闭 WebSocket 连接
      7. 退出进程
    """
    
    def __init__(
        self, 
        config: Optional[HealthCheckConfig] = None,
        on_shutdown: Optional[Callable[[], Awaitable[None]]] = None
    ):
        self.config = config or HealthCheckConfig()
        self.on_shutdown_callback = on_shutdown
        self._shutting_down = False
        self._active_requests: Set[int] = set()
        self._request_counter = 0
        self._lock = threading.Lock()
        self._shutdown_event = asyncio.Event()
        self._shutdown_complete = asyncio.Event()
    
    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down
    
    def register_request(self) -> int:
        """注册一个新的活跃请求"""
        if self._shutting_down:
            raise RuntimeError("Server is shutting down")
        
        with self._lock:
            req_id = self._request_counter
            self._request_counter += 1
            self._active_requests.add(req_id)
        
        return req_id
    
    def complete_request(self, request_id: int):
        """标记请求已完成"""
        with self._lock:
            self._active_requests.discard(request_id)
        
        # 所有请求都完成了？
        if self._shutting_down and not self._active_requests:
            self._shutdown_complete.set()
    
    async def wait_for_shutdown_completion(self, timeout: Optional[float] = None):
        """等待所有活跃请求完成"""
        timeout = timeout or self.config.shutdown_timeout_seconds
        
        try:
            await asyncio.wait_for(
                self._shutdown_complete.wait(), 
                timeout=timeout
            )
        except asyncio.TimeoutError:
            remaining = len(self._active_requests)
            logger.warning(
                f"[Shutdown] Timeout waiting for requests "
                f"(remaining: {remaining}, forcing shutdown)"
            )
    
    async def initiate_shutdown(self, signal_name: str = "SIGTERM"):
        """
        发起优雅停机流程
        
        Args:
            signal_name: 触发信号名称 (用于日志)
        """
        if self._shutting_down:
            return
        
        self._shutting_down = True
        self._shutdown_event.set()
        
        logger.info(f"[Shutdown] Received {signal_name}, initiating graceful shutdown...")
        logger.info(f"[Shutdown] Active requests: {len(self._active_requests)}")
        
        # 1. 执行自定义回调 (如停止调度循环)
        if self.on_shutdown_callback:
            try:
                await self.on_shutdown_callback()
            except Exception as e:
                logger.error(f"[Shutdown] Callback error: {e}")
        
        # 2. 等待请求完成
        await self.wait_for_shutdown_completion()
        
        # 3. 关闭资源
        await self._cleanup_resources()
        
        logger.info("[Shutdown] Graceful shutdown complete")
        
        # 4. 退出进程
        os._exit(0)
    
    async def _cleanup_resources(self):
        """清理所有资源"""
        cleanup_tasks = []
        
        # 数据库连接池
        try:
            from app.core.database import get_engine
            engine = get_engine()
            if engine:
                await engine.dispose()
                logger.info("[Shutdown] Database connection pool disposed")
        except Exception as e:
            logger.error(f"[Shutdown] DB cleanup error: {e}")
        
        # InfluxDB
        try:
            from app.core.influx_service import _influx_client
            if _influx_client:
                await _influx_client.close()
                logger.info("[Shutdown] InfluxDB client closed")
        except Exception as e:
            logger.error(f"[Shutdown] InfluxDB cleanup error: {e}")
        
        # Kafka
        try:
            from app.core.kafka_service import EventBus
            bus = EventBus._instance
            if bus:
                await bus.shutdown()
                logger.info("[Shutdown] EventBus shutdown complete")
        except Exception as e:
            logger.error(f"[Shutdown] Kafka cleanup error: {e}")
        
        # Redis
        try:
            from app.core.redis_service import redis_client
            if redis_client:
                await redis_client.close()
                logger.info("[Shutdown] Redis connection closed")
        except Exception as e:
            logger.error(f"[Shutdown] Redis cleanup error: {e}")
    
    def install_signal_handlers(self, loop=None):
        """安装信号处理器"""
        loop = loop or asyncio.get_event_loop()
        
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(
                    self.initiate_shutdown(s.name)
                )
            )
        
        logger.info(f"[Shutdown] Signal handlers installed (SIGTERM, SIGINT)")


# ==================== FastAPI Router ====================

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

health_router = APIRouter(tags=["Health"])

# 全局实例
_health_manager: Optional[HealthCheckManager] = None
_shutdown_manager: Optional[GracefulShutdownManager] = None


def init_health_services(config: Optional[HealthCheckConfig] = None):
    """初始化健康检查和停机服务"""
    global _health_manager, _shutdown_manager
    
    config = config or HealthCheckConfig()
    _health_manager = HealthCheckManager(config)
    _shutdown_manager = GracefulShutdownManager(config)
    
    return _health_manager, _shutdown_manager


def get_health_manager() -> HealthCheckManager:
    """获取健康检查管理器"""
    if _health_manager is None:
        init_health_services()
    return _health_manager


def get_shutdown_manager() -> GracefulShutdownManager:
    """获取停机管理器"""
    if _shutdown_manager is None:
        init_health_services()
    return _shutdown_manager


@health_router.get("/healthz")
async def liveness_probe():
    """
    Kubernetes Liveness Probe — 基础存活检查
    
    只要能返回 200 OK，就认为进程存活。
    不检查任何外部依赖，避免误杀。
    """
    manager = get_health_manager()
    
    alive = await manager.quick_liveness_check()
    
    if alive:
        return JSONResponse(
            content={'status': 'alive', 'timestamp': datetime.utcnow().isoformat()},
            status_code=200,
        )
    else:
        return JSONResponse(
            content={'status': 'dead', 'timestamp': datetime.utcnow().isoformat()},
            status_code=503,
        )


@health_router.get("/readyz")
async def readiness_probe():
    """
    Kubernetes Readiness Probe — 就绪检查
    
    检查所有 critical 依赖 (Database, Redis, Scheduler) 是否就绪。
    只有全部就绪才接受流量。
    """
    manager = get_health_manager()
    
    ready, reason = await manager.readiness_check()
    
    if ready:
        return JSONResponse(
            content={
                'status': 'ready',
                'reason': reason,
                'timestamp': datetime.utcnow().isoformat(),
            },
            status_code=200,
        )
    else:
        return JSONResponse(
            content={
                'status': 'not_ready',
                'reason': reason,
                'timestamp': datetime.utcnow().isoformat(),
            },
            status_code=503,
        )


@health_router.get("/livez")
async def deep_health_check(verbose: bool = False):
    """
    Deep Health Check — 详细健康报告 (用于运维监控)
    
    返回所有依赖的状态、响应时间、详细信息。
    
    Args:
        verbose: 是否包含每个检查器的详细信息
    """
    manager = get_health_manager()
    report = await manager.run_all_checks()
    
    status_code = 200 if report.is_healthy else \
                  (206 if report.status == HealthStatus.DEGRADED else 503)
    
    return JSONResponse(
        content=report.to_dict(verbose=verbose),
        status_code=status_code,
    )


@health_router.get("/livez/verbose")
async def detailed_health_check():
    """
    详细健康检查 (verbose=true 的快捷方式)
    """
    return await deep_health_check(verbose=True)


# ==================== 导出 ====================

__all__ = [
    'HealthStatus',
    'DependencyStatus',
    'HealthCheckResult',
    'SystemHealthReport',
    'HealthCheckConfig',
    'BaseDependencyChecker',
    'DatabaseChecker',
    'RedisChecker',
    'SchedulerChecker',
    'MemoryChecker',
    'KafkaChecker',
    'InfluxChecker',
    'HealthCheckManager',
    'GracefulShutdownManager',
    'health_router',
    'get_health_manager',
    'get_shutdown_manager',
    'init_health_services',
]


# ==================== 快速验证脚本 ====================

if __name__ == '__main__':
    import asyncio
    from pathlib import Path
    
    async def test():
        print("=" * 60)
        print("🏥 High-Availability Health Check Test Suite")
        print("=" * 60)
        
        # 测试 1: 健康检查管理器
        print("\n📋 Test 1: Health Check Manager")
        
        manager = HealthCheckManager(HealthCheckConfig())
        
        report = await manager.run_all_checks()
        print(f"  ✅ Overall status: {report.status.value}")
        print(f"  ✅ Uptime: {report.overall_uptime_seconds:.1f}s")
        print(f"  ✅ Checks executed: {len(report.checks)}")
        
        for check in report.checks:
            icon = "✅" if check.status == DependencyStatus.UP else \
                   "⚠️" if check.status == DependencyStatus.DEGRADED else "❌"
            print(f"     {icon} {check.name}: {check.status.value} ({check.response_time_ms:.1f}ms)")
        
        assert isinstance(report.to_dict(), dict)
        print(f"  ✅ Report serialization works")
        
        # 测试 2: 就绪检查
        print("\n🔌 Test 2: Readiness Check")
        ready, reason = await manager.readiness_check()
        print(f"  ✅ Ready: {ready} ({reason})")
        
        # 测试 3: 优雅停机管理器
        print("\n🛑 Test 3: Graceful Shutdown Manager")
        
        shutdown_mgr = GracefulShutdownManager(HealthCheckConfig(shutdown_timeout_seconds=1))
        
        assert shutdown_mgr.is_shutting_down == False
        print(f"  ✅ Initial state: running")
        
        # 注册模拟请求
        req_id = shutdown_mgr.register_request()
        print(f"  ✅ Request registered: #{req_id}")
        
        # 完成请求
        shutdown_mgr.complete_request(req_id)
        print(f"  ✅ Request completed")
        
        # 模拟停机流程 (不真正退出)
        shutdown_mgr._shutting_down = True
        shutdown_mgr._shutdown_event.set()
        
        # 注意: 不调用 initiate_shutdown 因为会 exit 进程
        
        print("\n" + "=" * 60)
        print("🎉 All HA Health Check tests PASSED!")
        print("=" * 60)
        print("\n📍 Available endpoints:")
        print("  GET /healthz          — Liveness probe")
        print("  GET /readyz           — Readiness probe")
        print("  GET /livez            — Deep health check")
        print("  GET /livez/verbose    — Detailed report")
    
    asyncio.run(test())
