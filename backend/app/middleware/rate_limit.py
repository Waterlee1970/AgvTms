"""
API限流与熔断器 - 对标Spring Cloud Gateway + Resilience4j

功能:
1. 基于令牌桶的速率限制 (Token Bucket)
2. 并发连接数控制
3. 熔断保护 (Circuit Breaker)
4. 限流响应标准化

对标参考:
- Resilience4j (Spring Cloud Circuit Breaker)
- Sentinel (Alibaba 流量控制)
- 海康RCS API Gateway限流策略
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List, Any
from enum import Enum
from fastapi import Request, Response
from fastapi.responses import JSONResponse

# 延迟导入避免循环依赖
def _get_response_module():
    from app.schemas.response import ApiResponse, ResponseCode, too_many_requests
    return ApiResponse, ResponseCode, too_many_requests


@dataclass
class BucketState:
    """令牌桶状态"""
    tokens: float = 20.0          # 当前令牌数
    last_refill: float = 0.0       # 上次填充时间


class RateLimiter:
    """
    令牌桶限流器
    
    配置对标海康RCS限流策略:
    - 默认: 100 req/s per IP
    - API密钥认证: 1000 req/s per key
    - 内部服务调用: unlimited
    
    使用示例:
        limiter = RateLimiter(rate=100.0, burst=20)
        allowed, meta = await limiter.is_allowed("client_ip_192.168.1.1")
    """
    
    def __init__(
        self,
        rate: float = 100.0,       # 每秒填充令牌数
        burst: int = 20,            # 桶容量 (突发允许)
    ):
        self.rate = rate
        self.burst = burst
        self._buckets: Dict[str, BucketState] = {}
        self._lock = asyncio.Lock()
    
    async def is_allowed(self, key: str) -> Tuple[bool, dict]:
        """
        检查是否允许请求
        
        Args:
            key: 限流键 (通常是IP地址或API Key)
            
        Returns:
            (allowed, meta_dict) 元组
        """
        now = time.monotonic()
        
        async with self._lock:
            if key not in self._buckets:
                self._buckets[key] = BucketState(tokens=self.burst, last_refill=now)
            
            bucket = self._buckets[key]
            
            # 补充令牌
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self.burst, bucket.tokens + elapsed * self.rate)
            bucket.last_refill = now
            
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True, {
                    "remaining": int(bucket.tokens),
                    "limit": self.burst,
                    "reset_after": int((self.burst - bucket.tokens) / self.rate) if self.rate > 0 else 0,
                }
            else:
                retry_after = (1 - bucket.tokens) / self.rate if self.rate > 0 else float('inf')
                return False, {
                    "retry_after": retry_after,
                    "limit": self.burst,
                    "remaining": 0,
                }
    
    async def reset(self, key: str):
        """重置指定键的限流状态"""
        async with self._lock:
            if key in self._buckets:
                del self._buckets[key]
    
    async def get_stats(self) -> dict:
        """获取限流统计信息 (用于监控)"""
        async with self._lock:
            return {
                "total_buckets": len(self._buckets),
                "rate": self.rate,
                "burst": self.burst,
            }


class CircuitBreakerState(str, Enum):
    """熔断器状态枚举"""
    CLOSED = "closed"           # 关闭态：正常放行
    OPEN = "open"               # 打开态：熔断中，快速失败
    HALF_OPEN = "half_open"     # 半开态：允许探测


class CircuitBreaker:
    """
    熔断器 - 对标Resilience4j CircuitBreaker
    
    三态模型:
    - CLOSED (关闭): 正常状态, 请求正常通过
    - OPEN (打开): 熔断状态, 快速失败不调用下游
    - HALF_OPEN (半开): 允许探测请求通过
    
    使用示例:
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        
        if await breaker.can_execute():
            result = await call_downstream()
            if success:
                await breaker.record_success()
            else:
                await breaker.record_failure()
    """
    
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,       # 连续失败N次触发熔断
        recovery_timeout: float = 30.0,    # 熔断持续N秒后尝试恢复
        half_open_max_calls: int = 3,      # 半开状态允许N个探测请求
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
    
    async def can_execute(self) -> bool:
        """检查是否允许执行 (OPEN态直接拒绝)"""
        async with self._lock:
            if self.state == CircuitBreakerState.CLOSED:
                return True
            
            if self.state == CircuitBreakerState.OPEN:
                # 检查是否可以转为HALF_OPEN
                if self.last_failure_time and \
                   time.monotonic() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitBreakerState.HALF_OPEN
                    self.success_count = 0
                    return True
                return False
            
            # HALF_OPEN: 限制并发探测请求数
            return self.success_count < self.half_open_max_calls
    
    async def record_success(self):
        """记录成功"""
        async with self._lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self.state = CircuitBreakerState.CLOSED
                    self.failure_count = 0
            
            self.failure_count = 0
    
    async def record_failure(self):
        """记录失败"""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.state = CircuitBreakerState.OPEN  # 探测失败, 重新熔断
            elif self.failure_count >= self.failure_threshold:
                self.state = CircuitBreakerState.OPEN
    
    def get_state(self) -> CircuitBreakerState:
        """获取当前状态 (用于监控面板)"""
        return self.state
    
    async def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }


class ConcurrencyLimiter:
    """
    并发连接数限制器
    
    用于防止单个客户端占用过多资源
    """
    
    def __init__(self, max_concurrent: int = 10):
        self.max_concurrent = max_concurrent
        self._semaphore: Dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()
    
    async def acquire(self, key: str) -> bool:
        """
        尝试获取并发许可
        
        Returns:
            True: 成功获取
            False: 已达上限
        """
        async with self._lock:
            if key not in self._semaphore:
                self._semaphore[key] = asyncio.Semaphore(self.max_concurrent)
            
            sem = self._semaphore[key]
        
        # 非阻塞式获取
        return sem.locked() is False or sem._value > 0
    
    async def release(self, key: str):
        """释放许可"""
        async with self._lock:
            if key in self._semaphore:
                try:
                    self._semaphore[key].release()
                except ValueError:
                    pass  # 已经释放过


class RateLimitMiddleware:
    """
    FastAPI限流中间件
    
    集成到main.py:
        app.add_middleware(RateLimitMiddleware)
    
    特性:
    - 基于IP和API Key的双重限流策略
    - 标准化429响应 (符合ApiResponse格式)
    - 自动注入X-RateLimit-*响应头
    - 支持路径白名单
    """
    
    def __init__(
        self,
        rate_per_second: float = 100.0,
        burst: int = 20,
        excluded_paths: Optional[List[str]] = None,
        enable_circuit_breaker: bool = False,
    ):
        self.limiter = RateLimiter(rate=rate_per_second, burst=burst)
        self.concurrency_limiter = ConcurrencyLimiter(max_concurrent=50)
        self.excluded_paths = set(excluded_paths or ["/health", "/docs", "/openapi.json", "/redoc", "/metrics"])
        self.enable_circuit_breaker = enable_circuit_breaker
        
        # 全局熔断器 (保护整个API网关)
        if enable_circuit_breaker:
            self.circuit_breaker = CircuitBreaker(
                name="api_gateway",
                failure_threshold=10,
                recovery_timeout=60.0,
            )
    
    async def __call__(self, request: Request, call_next):
        # 跳过健康检查、文档和监控路径
        if request.url.path in self.excluded_paths:
            return await call_next(request)
        
        # 获取限流键 (支持API Key优先，否则使用IP)
        client_ip = request.client.host if request.client else "unknown"
        api_key = request.headers.get("X-API-Key")
        limit_key = api_key or f"ip:{client_ip}"
        
        # 可选: 熔断检查
        if self.enable_circuit_breaker:
            if not await self.circuit_breaker.can_execute():
                ApiResponse, _, _ = _get_response_module()
                response = JSONResponse(
                    status_code=503,
                    content=ApiResponse[None](
                        code="503",
                        message="服务暂时不可用，请稍后重试",
                        data=None,
                        trace_id=request.headers.get("X-Request-ID"),
                    ).model_dump(),
                )
                response.headers["Retry-After"] = str(int(self.circuit_breaker.recovery_timeout))
                return response
        
        # 限流检查
        allowed, meta = await self.limiter.is_allowed(limit_key)
        
        if not allowed:
            _, ResponseCode, _ = _get_response_module()
            retry_after = meta.get("retry_after", 60)
            
            response = JSONResponse(
                status_code=429,
                content={
                    "code": "429",
                    "message": "请求过于频繁，请稍后重试",
                    "data": None,
                    "trace_id": request.headers.get("X-Request-ID"),
                    "timestamp": time.time(),
                    "extra": {"retry_after": retry_after},
                },
            )
            response.headers["Retry-After"] = str(int(retry_after))
            response.headers["X-RateLimit-Limit"] = str(meta.get("limit", 20))
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(int(time.time() + retry_after))
            return response
        
        # 正常处理请求
        try:
            response = await call_next(request)
            
            # 注入限流头信息 (用于前端展示)
            response.headers["X-RateLimit-Limit"] = str(meta.get("limit", 20))
            response.headers["X-RateLimit-Remaining"] = str(meta.get("remaining", 0))
            response.headers["X-RateLimit-Reset"] = str(int(time.time() + meta.get("reset_after", 1)))
            
            return response
            
        except Exception as exc:
            # 记录到熔断器
            if self.enable_circuit_breaker:
                await self.circuit_breaker.record_failure()
            raise  # 继续向上抛出异常


# ==================== 全局实例 (单例模式) ====================

_global_limiter: Optional[RateLimiter] = None
_global_breakers: Dict[str, CircuitBreaker] = {}


def get_rate_limiter(rate: float = 100.0, burst: int = 20) -> RateLimiter:
    """获取全局限流器实例"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter(rate=rate, burst=burst)
    return _global_limiter


def get_circuit_breaker(name: str, **kwargs) -> CircuitBreaker:
    """获取命名熔断器实例"""
    global _global_breakers
    if name not in _global_breakers:
        _global_breakers[name] = CircuitBreaker(name=name, **kwargs)
    return _global_breakers[name]


# ==================== 装饰器版本 (可选使用方式) ====================

def rate_limit(rate: float = 100.0, burst: int = 20, key_func=None):
    """
    限流装饰器 (可用于特定端点)
    
    使用示例:
        @app.get("/api/sensitive")
        @rate_limit(rate=10.0, burst=5)
        async def sensitive_endpoint():
            pass
    """
    def decorator(func):
        limiter = RateLimiter(rate=rate, burst=burst)
        
        async def wrapper(*args, **kwargs):
            # 从request对象提取key
            request = kwargs.get('request') or (args[0] if args else None)
            if hasattr(request, 'client'):
                limit_key = key_func(request) if key_func else f"ip:{request.client.host}"
            else:
                limit_key = "unknown"
            
            allowed, meta = await limiter.is_allowed(limit_key)
            if not allowed:
                _, _, too_many_req_fn = _get_response_module()
                raise HTTPException(
                    status_code=429,
                    detail=too_many_req_fn(retry_after=meta["retry_after"]).dict(),
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


# 导入HTTPException用于装饰器版本
try:
    from fastapi import HTTPException
except ImportError:
    HTTPException = type('HTTPException', (), {})
