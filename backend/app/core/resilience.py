"""
Resilience & Circuit Breaker Module — 工程化加固 (Phase 5).

功能:
  1. Circuit Breaker (断路器) — 防止级联故障
  2. Retry with Exponential Backoff (重试) — 临时故障恢复
  3. Fallback Chain (降级链) — 多层降级策略
  4. Rate Limiter (限流) — 保护后端服务
  5. Health Check Aggregator (健康聚合)
  6. Leader Election / Master-Slave Switch (主备切换)

依赖:
  - tenacity: 重试和断路器实现
  - asyncio: 异步支持
"""

import time
import logging
import asyncio
import threading
from typing import Any, Callable, Optional, Dict, List, Tuple, TypeVar, Union
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from collections import defaultdict
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# ==================== 可选导入 ====================

try:
    from tenacity import (
        retry, stop_after_attempt, wait_exponential,
        retry_if_exception_type, before_sleep_log, RetryError,
    )
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logger.warning("tenacity not installed. Install: pip install tenacity")

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    try:
        import redis
        REDIS_AVAILABLE = True
        aioredis = None
    except ImportError:
        REDIS_AVAILABLE = False


# ==================== 类型定义 ====================

T = TypeVar('T')
F = TypeVar('F', bound=Callable[..., Any])


class CircuitState(Enum):
    """断路器状态."""
    CLOSED = "closed"       # 正常，允许请求通过
    OPEN = "open"           # 断开，快速失败
    HALF_OPEN = "half_open" # 半开，允许试探性请求


@dataclass 
class BreakerConfig:
    """断路器配置."""
    failure_threshold: int = 5       # 连续失败次数阈值
    recovery_timeout: float = 30.0   # 断开后恢复等待时间(秒)
    half_open_max_calls: int = 3     # 半开状态最大试探请求数
    success_threshold: int = 2       # 半开→关闭的成功阈值


class BreakerStats:
    """断路器统计."""
    def __init__(self):
        self.total_calls: int = 0
        self.successes: int = 0
        self.failures: int = 0
        self.rejected_calls: int = 0   # 被快速拒绝的调用
        self.timeouts: int = 0
        self.last_failure_time: Optional[float] = None
        self.last_success_time: Optional[float] = None


@dataclass
class RateLimitConfig:
    """限流配置."""
    requests_per_second: float = 100.0
    burst_size: int = 20
    window_seconds: float = 1.0


@dataclass
class FallbackResult:
    """降级结果."""
    success: bool
    value: Any = None
    error: Optional[str] = None
    method_used: str = ""
    fallback_depth: int = 0  # 0=原始方法, 1=一级降级...


# ==================== Circuit Breaker 实现 ====================

class CircuitBreaker:
    """
    熔断器 (Circuit Breaker) 模式实现.
    
    状态机:
        CLOSED ──[连续failure≥threshold]──→ OPEN
          ↑                                      │
          │         [timeout elapsed]             │
          ↓                                      ↓
        HALF_OPEN ←─────────────────────────┐
          │                                  │
          ├─[success ≥ threshold]────→ CLOSED
          └─[failure]────────────────→ OPEN
    
    使用示例:
        breaker = CircuitBreaker("db_query", config=BreakerConfig(failure_threshold=3))
        
        @breaker.protect
        def query_database(sql):
            ...
    
    """
    
    _instances: Dict[str, 'CircuitBreaker'] = {}
    
    def __new__(cls, name: str, **kwargs):
        if name not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[name] = instance
        return cls._instances[name]
    
    def __init__(
        self,
        name: str,
        config: Optional[BreakerConfig] = None,
    ):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self.name = name
        self.config = config or BreakerConfig()
        self.state = CircuitState.CLOSED
        self.stats = BreakerStats()
        
        # 内部状态
        self._consecutive_failures: int = 0
        self._consecutive_successes: int = 0
        self._half_open_calls: int = 0
        self._last_state_change: float = time.time()
        self._lock = threading.RLock()
        
        self._initialized = True
        
    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
    
    def _should_allow_request(self) -> bool:
        """检查是否允许请求通过."""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
                
            elif self.state == CircuitState.OPEN:
                # 检查是否过了恢复超时时间
                elapsed = time.time() - self._last_state_change
                if elapsed >= self.config.recovery_timeout:
                    logger.info(f"Circuit '{self.name}' transitioning to HALF_OPEN after {elapsed:.1f}s")
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    return True
                return False
                
            elif self.state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.config.half_open_max_calls:
                    return True
                return False
                
        return False
    
    def _record_success(self):
        """记录成功."""
        with self._lock:
            self.stats.successes += 1
            self.stats.last_success_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.config.success_threshold:
                    logger.info(f"Circuit '{self.name}' recovered → CLOSED")
                    self.state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
            else:
                self._consecutive_failures = 0
                
    def _record_failure(self):
        """记录失败."""
        with self._lock:
            self.stats.failures += 1
            self.stats.last_failure_time = time.time()
            
            if self.state == CircuitState.CLOSED:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.config.failure_threshold:
                    logger.warning(
                        f"Circuit '{self.name}' TRIPPED! "
                        f"{self._consecutive_failures} consecutive failures"
                    )
                    self.state = CircuitState.OPEN
                    self._last_state_change = time.time()
                    
            elif self.state == CircuitState.HALF_OPEN:
                logger.warning(f"Circuit '{self.name}' failed in HALF_OPEN → back to OPEN")
                self.state = CircuitState.OPEN
                self._last_state_change = time.time()
                self._consecutive_successes = 0
                self._half_open_calls = 0
                
    def record_call(self):
        """记录一次调用."""
        self.stats.total_calls += 1
        
    def call_allowed(self) -> bool:
        """检查是否允许调用 (外部使用)."""
        if self._should_allow_request():
            self.record_call()
            if self.state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1
            return True
        else:
            self.stats.rejected_calls += 1
            return False
            
    def protect_sync(self, func: Callable[..., T], *args, fallback=None, **kwargs) -> Union[T, Any]:
        """同步函数保护装饰器的核心逻辑."""
        if not self.call_allowed():
            if callable(fallback):
                result = fallback(*args, **kwargs)
                return FallbackResult(success=True, value=result, method_used="circuit_fallback", fallback_depth=1)
            raise CircuitOpenError(f"Circuit '{self.name}' is OPEN")
            
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            if fallback and callable(fallback):
                try:
                    fb_result = fallback(*args, **kwargs)
                    return FallbackResult(success=True, value=fb_result, method_used="exception_fallback", fallback_depth=1)
                except Exception as fb_err:
                    raise
            raise
            
    def protect(self, func: F = None, *, fallback: Optional[Callable] = None) -> Union[F, Callable]:
        """
        断路器装饰器.
        
        用法:
            @breaker.protect
            def risky_operation(): ...
            
            @breaker.protect(fallback=safe_alternative)
            def risky_with_fallback(): ...
        """
        def decorator(fn: F) -> F:
            @wraps(fn)
            def wrapper(*args, **kwargs):
                return self.protect_sync(fn, *args, fallback=fallback, **kwargs)
            return wrapper
            
        if func is not None:
            return decorator(func)
        return decorator
    
    async def protect_async(self, func: Callable, *args, fallback=None, **kwargs) -> Union[Any, FallbackResult]:
        """异步函数保护."""
        if not self.call_allowed():
            if fallback:
                if asyncio.iscoroutinefunction(fallback):
                    result = await fallback(*args, **kwargs)
                else:
                    result = fallback(*args, **kwargs)
                return FallbackResult(success=True, value=result, method_used="async_circuit_fallback", fallback_depth=1)
            raise CircuitOpenError(f"Circuit '{self.name}' is OPEN")
            
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            if fallback:
                if asyncio.iscoroutinefunction(fallback):
                    result = await fallback(*args, **kwargs)
                else:
                    result = fallback(*args, **kwargs)
                return FallbackResult(success=True, value=result, method_used="async_exception_fallback", fallback_depth=1)
            raise
    
    def reset(self):
        """手动重置为关闭状态."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._half_open_calls = 0
            self._last_state_change = time.time()
            
    def get_status(self) -> Dict[str, Any]:
        """获取当前状态信息."""
        return {
            "name": self.name,
            "state": self.state.value,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
            },
            "stats": {
                "total_calls": self.stats.total_calls,
                "successes": self.stats.successes,
                "failures": self.stats.failures,
                "rejected": self.stats.rejected_calls,
                "success_rate": round(
                    self.stats.successes / max(self.stats.total_calls, 1) * 100, 1
                ) if self.stats.total_calls > 0 else 0,
            }
        }


class CircuitOpenError(Exception):
    """断路器打开时的异常."""
    pass


# ==================== 预定义的断路器实例 ====================

# 数据库操作断路器
db_breaker = CircuitBreaker("database", config=BreakerConfig(
    failure_threshold=3,
    recovery_timeout=15.0,
))

# Redis 操作断路器  
redis_breaker = CircuitBreaker("redis", config=BreakerConfig(
    failure_threshold=5,
    recovery_timeout=10.0,
))

# 外部 API 调用断路器
external_api_breaker = CircuitBreaker("external_api", config=BreakerConfig(
    failure_threshold=5,
    recovery_timeout=30.0,
))

# RL 推理断路器
rl_inference_breaker = CircuitBreaker("rl_inference", config=BreakerConfig(
    failure_threshold=10,
    recovery_timeout=60.0,  # ML 服务容错更宽容
))


# ==================== Rate Limiter ====================

class TokenBucketRateLimiter:
    """
    令牌桶限流器.
    
    特性:
      - 支持突发流量
      - 线程安全
      - 分布式支持 (Redis)
    
    算法:
      桶容量 = burst_size
      补充速率 = requests_per_second tokens/s
      
    使用:
        limiter = TokenBucketRateLimiter(rps=100, burst=20)
        if limiter.allow():
            process_request()
    """
    
    def __init__(
        self,
        name: str = "default",
        rps: float = 100.0,
        burst: int = 20,
        distributed: bool = False,
        redis_client=None,
    ):
        self.name = name
        self.rps = rps
        self.burst = burst
        self.distributed = distributed
        self.redis_client = redis_client
        
        # 本地状态
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()
        
        # 统计
        self._total_requests: int = 0
        self._rejected: int = 0
        
    def allow(self, cost: float = 1.0) -> bool:
        """检查是否允许请求 (消耗一个令牌)."""
        if self.distributed and self.redis_client:
            return self._allow_distributed(cost)
        return self._allow_local(cost)
    
    def _refill_local(self):
        """本地令牌补充."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rps)
        self._last_refill = now
        
    def _allow_local(self, cost: float = 1.0) -> bool:
        with self._lock:
            self._refill_local()
            self._total_requests += 1
            
            if self._tokens >= cost:
                self._tokens -= cost
                return True
            else:
                self._rejected += 1
                return False
                
    def _allow_distributed(self, cost: float = 1.0) -> bool:
        # TODO: Redis Lua script 实现分布式令牌桶
        return self._allow_local(cost)
        
    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rps": self.rps,
            "burst": self.burst,
            "current_tokens": round(self._tokens, 2),
            "total_requests": self._total_requests,
            "rejected": self._rejected,
            "rejection_rate": round(
                self._rejected / max(self._total_requests, 1) * 100, 1
            )
        }


# 全局限流器实例
api_rate_limiter = TokenBucketRateLimiter("api_global", rps=200, burst=50)
algorithm_rate_limiter = TokenBucketRateLimiter("algorithm", rps=30, burst=10)


# ==================== Master-Slave Leader Election ====================

class LeaderElection:
    """
    基于 Redis 的主备选举 (简化版).
    
    用于多实例场景下的单点任务执行 (如定时调度).
    
    原理:
      - 所有实例尝试获取锁
      - 获取到锁的成为 leader (主节点)
      - 定期续租 (heartbeat)
      - leader 失效时自动转移
    
    使用:
        election = LeaderElection(redis_client, "agvtms_leader")
        if election.is_leader():
            run_scheduled_task()
    """
    
    def __init__(
        self,
        redis_client=None,
        lock_key: str = "agvtms:leader:election",
        ttl: int = 30,  # 锁过期时间(秒)
        heartbeat_interval: float = 10.0,
    ):
        self.redis = redis_client
        self.lock_key = lock_key
        self.ttl = ttl
        self.heartbeat_interval = heartbeat_interval
        
        self._is_leader = False
        self._instance_id = f"{__import__('socket').gethostname()}-{os.getpid()}"
        self._heartbeat_task: Optional[asyncio.Task] = None
        
    async def elect(self) -> bool:
        """参与选举."""
        if not self.redis:
            return True  # 无 Redis 时默认都是 leader
            
        try:
            acquired = await self.redis.set(
                self.lock_key,
                self._instance_id,
                nx=True,
                ex=self.ttl,
            )
            
            if acquired:
                self._is_leader = True
                logger.info(f"Instance {self._instance_id} elected as LEADER")
                self._start_heartbeat()
                return True
            else:
                current_leader = await self.redis.get(self.lock_key)
                logger.debug(f"Not leader. Current: {current_leader}")
                return False
                
        except Exception as e:
            logger.error(f"Election error: {e}, assuming follower")
            return False
            
    def is_leader(self) -> bool:
        return self._is_leader
        
    async def resign(self):
        """主动放弃领导权."""
        self._stop_heartbeat()
        self._is_leader = False
        if self.redis:
            try:
                await self.redis.delete(self.lock_key)
            except Exception:
                pass
                
    def _start_heartbeat(self):
        """启动心跳续租."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
            
        async def heartbeat_loop():
            while self._is_leader:
                try:
                    await self.redis.expire(self.lock_key, self.ttl)
                    await asyncio.sleep(self.heartbeat_interval)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Heartbeat error: {e}")
                    await asyncio.sleep(self.heartbeat_interval)
                    
        self._heartbeat_task = asyncio.create_task(heartbeat_loop())
        
    def _stop_heartbeat(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None


# ==================== 健康聚合器 ====================

class HealthAggregator:
    """
    系统健康状态聚合器.
    
    收集各子系统健康状态，输出统一健康报告.
    """
    
    def __init__(self):
        self._checks: Dict[str, Callable] = {}
        self._status_cache: Dict[str, bool] = {}
        self._cache_ttl: float = 5.0
        self._last_check: float = 0.0
        
    def register(self, name: str, check_fn: Callable):
        """注册健康检查项."""
        self._checks[name] = check_fn
        
    async def check_all(self) -> Dict[str, Any]:
        """执行所有健康检查."""
        results = {}
        overall_healthy = True
        
        for name, check_fn in self._checks.items():
            start = time.perf_counter()
            try:
                healthy = await check_fn() if asyncio.iscoroutinefunction(check_fn) else check_fn()
                latency = (time.perf_counter() - start) * 1000
                results[name] = {"healthy": healthy, "latency_ms": round(latency, 1)}
                if not healthy:
                    overall_healthy = False
            except Exception as e:
                results[name] = {"healthy": False, "error": str(e), "latency_ms": -1}
                overall_healthy = False
                
        return {
            "status": "healthy" if overall_healthy else "degraded",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checks": results,
            "breakers": {name: cb.get_status() for name, cb in CircuitBreaker._instances.items()},
            "rate_limiters": {name: rl.get_stats() for name, rl in [
                ("global", api_rate_limiter),
                ("algorithm", algorithm_rate_limiter),
            ]},
        }


# ==================== FastAPI 集成工具 ====================

def create_resilience_middleware():
    """创建 resilience 中间件 (用于 FastAPI)."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    
    class ResilienceMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # API 限流检查
            path = request.url.path
            limiter = algorithm_rate_limiter if '/api' in path and ('schedule' in path or 'algorithm' in path) else api_rate_limiter
            
            if not limiter.allow():
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limited", "message": "Too many requests"},
                )
                
            response = await call_next(request)
            response.headers["X-Circuit-State"] = ",".join([
                f"{name}:{cb.state.value}" for name, cb in CircuitBreaker._items()
            ])
            return response
            
    return ResilienceMiddleware()


# ==================== 导出 ====================

__all__ = [
    "CircuitBreaker", "CircuitState", "CircuitOpenError", "BreakerConfig",
    "TokenBucketRateLimiter", "RateLimitConfig",
    "LeaderElection", "HealthAggregator",
    "FallbackResult",
    # 预定义实例
    "db_breaker", "redis_breaker", "external_api_breaker", "rl_inference_breaker",
    "api_rate_limiter", "algorithm_rate_limiter",
]

import os
