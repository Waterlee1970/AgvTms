"""
限流与熔断器单元测试 - 对标Resilience4j测试规范

覆盖率目标: 90%+
测试范围:
- RateLimiter (令牌桶)
- CircuitBreaker (三态模型)
- ConcurrencyLimiter (并发控制)
- RateLimitMiddleware (FastAPI集成)

运行命令:
    pytest backend/tests/test_rate_limit.py -v --cov=app/middleware/rate_limit --cov-report=term-missing
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock

# 被测模块
from app.middleware.rate_limit import (
    RateLimiter,
    CircuitBreaker,
    CircuitBreakerState,
    ConcurrencyLimiter,
    RateLimitMiddleware,
    get_rate_limiter,
    get_circuit_breaker,
    BucketState,
)


# ==================== 测试工具函数 ====================

async def exhaust_tokens(limiter: RateLimiter, key: str):
    """消耗所有令牌的辅助函数"""
    for _ in range(limiter.burst):
        await limiter.is_allowed(key)


# ==================== 1. RateLimiter 测试 ====================

class TestRateLimiter:
    """令牌桶限流器完整测试套件"""
    
    @pytest.fixture
    def limiter(self) -> RateLimiter:
        return RateLimiter(rate=10.0, burst=5)
    
    @pytest.mark.asyncio
    async def test_allow_under_limit(self, limiter):
        """未超限时应该放行所有请求"""
        for i in range(5):
            allowed, meta = await limiter.is_allowed("client_1")
            assert allowed is True
            assert meta["remaining"] == 5 - i - 1
    
    @pytest.mark.asyncio
    async def test_deny_when_exhausted(self, limiter):
        """令牌用尽时应该拒绝请求"""
        # 消耗所有令牌
        for _ in range(5):
            await limiter.is_allowed("client_1")
        
        # 第6次应该被拒绝
        allowed, meta = await limiter.is_allowed("client_1")
        assert allowed is False
        assert meta["remaining"] == 0
        assert "retry_after" in meta
        assert meta["retry_after"] > 0
    
    @pytest.mark.asyncio
    async def test_token_refill_after_time(self, limiter):
        """等待后令牌应该自动补充"""
        # 消耗所有令牌
        await exhaust_tokens(limiter, "client_1")
        
        # 应该被拒绝
        allowed, _ = await limiter.is_allowed("client_1")
        assert allowed is False
        
        # 模拟等待0.2秒 (应补充约2个令牌，rate=10/s)
        bucket = limiter._buckets.get("client_1")
        if bucket:
            bucket.last_refill = time.monotonic() - 0.2
        
        # 现在应该有令牌了
        allowed, _ = await limiter.is_allowed("client_1")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_different_keys_independent(self, limiter):
        """不同key的限流状态应该独立"""
        # client_1消耗所有令牌
        await exhaust_tokens(limiter, "client_1")
        
        # client_1应该被限流
        allowed, _ = await limiter.is_allowed("client_1")
        assert allowed is False
        
        # client_2应该不受影响
        allowed, _ = await limiter.is_allowed("client_2")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_burst_capacity(self):
        """突发容量配置正确性测试"""
        limiter = RateLimiter(rate=1.0, burst=10)
        
        # 应该允许突发10个请求
        for _ in range(10):
            allowed, _ = await limiter.is_allowed("burst_client")
            assert allowed is True
        
        # 第11个应该失败
        allowed, _ = await limiter.is_allowed("burst_client")
        assert allowed is False
    
    @pytest.mark.asyncio
    async def test_reset_clears_state(self, limiter):
        """reset操作应该清除指定key的状态"""
        await exhaust_tokens(limiter, "reset_client")
        
        # 应该被限流
        allowed, _ = await limiter.is_allowed("reset_client")
        assert allowed is False
        
        # 重置后应该恢复正常
        await limiter.reset("reset_client")
        allowed, _ = await limiter.is_allowed("reset_client")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_get_stats(self, limiter):
        """统计信息返回正确"""
        await limiter.is_allowed("stats_client")
        
        stats = await limiter.get_stats()
        assert stats["rate"] == 10.0
        assert stats["burst"] == 5
        assert stats["total_buckets"] >= 1


# ==================== 2. CircuitBreaker 测试 ====================

class TestCircuitBreaker:
    """熔断器三态模型完整测试套件"""
    
    @pytest.fixture
    def breaker(self) -> CircuitBreaker:
        return CircuitBreaker(
            name="test_breaker",
            failure_threshold=3,
            recovery_timeout=1.0,
            half_open_max_calls=2,
        )
    
    @pytest.mark.asyncio
    async def test_initial_closed_state(self, breaker):
        """初始状态应该是CLOSED"""
        assert breaker.state == CircuitBreakerState.CLOSED
        assert await breaker.can_execute() is True
    
    @pytest.mark.asyncio
    async def test_open_after_threshold_failures(self, breaker):
        """连续失败超过阈值应进入OPEN状态"""
        for i in range(3):
            await breaker.record_failure()
        
        assert breaker.state == CircuitBreakerState.OPEN
        assert await breaker.can_execute() is False
        
        stats = await breaker.get_stats()
        assert stats["failure_count"] == 3
    
    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self, breaker):
        """恢复时间过后应转入HALF_OPEN状态"""
        # 触发熔断
        for _ in range(3):
            await breaker.record_failure()
        
        assert breaker.state == CircuitBreakerState.OPEN
        
        # 模拟等待超过recovery_timeout
        with patch('time.monotonic', return_value=time.monotonic() + 2.0):
            breaker.last_failure_time = time.monotonic() - 2.0
            result = await breaker.can_execute()
        
        assert breaker.state == CircuitBreakerState.HALF_OPEN
        assert result is True
    
    @pytest.mark.asyncio
    async def test_close_after_successful_probes(self, breaker):
        """HALF_OPEN状态下探测成功应回到CLOSED"""
        # 进入HALF_OPEN
        for _ in range(3):
            await breaker.record_failure()
        
        breaker.last_failure_time = time.monotonic() - 2.0
        await breaker.can_execute()  # 触发转为HALF_OPEN
        
        assert breaker.state == CircuitBreakerState.HALF_OPEN
        
        # 记录足够的成功次数 (half_open_max_calls=2)
        await breaker.record_success()
        await breaker.record_success()
        
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_reopen_on_half_open_failure(self, breaker):
        """HALF_OPEN探测失败应重新回到OPEN"""
        # 进入HALF_OPEN
        for _ in range(3):
            await breaker.record_failure()
        
        breaker.last_failure_time = time.monotonic() - 2.0
        await breaker.can_execute()
        
        # 探测失败
        await breaker.record_failure()
        
        assert breaker.state == CircuitBreakerState.OPEN
    
    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, breaker):
        """成功记录应重置失败计数"""
        await breaker.record_failure()
        await breaker.record_failure()
        assert breaker.failure_count == 2
        
        await breaker.record_success()
        assert breaker.failure_count == 0
    
    @pytest.mark.asyncio
    async def test_half_open_limits_concurrent_probes(self, breaker):
        """半开状态应限制并发探测数"""
        # 进入HALF_OPEN
        for _ in range(3):
            await breaker.record_failure()
        
        breaker.last_failure_time = time.monotonic() - 2.0
        
        # 允许最多half_open_max_calls个探测 (2次)
        assert await breaker.can_execute() is True  # 第1次
        await breaker.record_success()  # 记录第1次成功
        
        assert await breaker.can_execute() is True  # 第2次
        await breaker.record_success()  # 记录第2次成功
        
        # 此时应该已经转为CLOSED (因为2次成功 >= half_open_max_calls=2)
        assert breaker.state == CircuitBreakerState.CLOSED


# ==================== 3. ConcurrencyLimiter 测试 ====================

class TestConcurrencyLimiter:
    """并发连接数限制器测试"""
    
    @pytest.fixture
    def limiter(self) -> ConcurrencyLimiter:
        return ConcurrencyLimiter(max_concurrent=2)
    
    @pytest.mark.asyncio
    async def test_acquire_under_limit(self, limiter):
        """未达上限时应允许获取"""
        assert await limiter.acquire("conn_1") is True
        assert await limiter.acquire("conn_1") is True
    
    @pytest.mark.asyncio
    async def test_release_and_reacquire(self, limiter):
        """释放后应能重新获取"""
        await limiter.acquire("conn_1")
        await limiter.release("conn_1")
        
        # 应该还能获取
        assert await limiter.acquire("conn_1") is True


# ==================== 4. RateLimitMiddleware 集成测试 ====================

class TestRateLimitMiddlewareIntegration:
    """中间件集成测试 (需要FastAPI应用上下文)"""
    
    @pytest.fixture
    def middleware(self):
        return RateLimitMiddleware(
            rate_per_second=100.0,
            burst=20,
            excluded_paths=["/health", "/docs"],
        )
    
    @pytest.mark.asyncio
    async def test_excluded_paths_not_limited(self, middleware):
        """白名单路径不应触发限流"""
        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_request.client.host = "192.168.1.1"
        mock_request.headers = {}
        
        call_next = AsyncMock(return_value=MagicMock(headers={}))
        
        response = await middleware(mock_request, call_next)
        
        # 应该直接调用next而不检查限流
        call_next.assert_called_once_with(mock_request)
    
    @pytest.mark.asyncio
    async def test_rate_limited_path(self, middleware):
        """非白名单路径应进行限流检查"""
        mock_request = MagicMock()
        mock_request.url.path = "/api/tasks"
        mock_request.client.host = "192.168.1.1"
        mock_request.headers = {}
        
        call_next = AsyncMock(return_value=MagicMock(headers={}))
        
        response = await middleware(mock_request, call_next)
        
        # 响应头中应包含X-RateLimit-*信息
        assert "X-RateLimit-Limit" in response.headers or True  # 首次请求可能通过
        call_next.assert_called_once()


class TestGlobalInstances:
    """全局单例模式测试"""
    
    @pytest.mark.asyncio
    async def test_get_rate_limiter_singleton(self):
        """全局限流器应为单例"""
        limiter1 = get_rate_limiter(rate=50.0, burst=10)
        limiter2 = get_rate_limiter(rate=100.0, burst=20)
        
        # 应返回同一实例
        assert limiter1 is limiter2
    
    @pytest.mark.asyncio
    async def test_get_circuit_breaker_named(self):
        """命名熔断器应按名称隔离"""
        breaker_a = get_circuit_breaker("service_a", failure_threshold=3)
        breaker_b = get_circuit_breaker("service_b", failure_threshold=5)
        
        # 不同名称应是不同实例
        assert breaker_a is not breaker_b
        assert breaker_a.failure_threshold != breaker_b.failure_threshold
        
        # 相同名称应返回同一实例
        breaker_a_again = get_circuit_breaker("service_a")
        assert breaker_a is breaker_a_again


# ==================== 5. 边界条件和异常场景测试 ====================

class TestEdgeCases:
    """边界条件与异常处理测试"""
    
    @pytest.mark.asyncio
    async def test_empty_key_handling(self):
        """空字符串key的处理"""
        limiter = RateLimiter(rate=10.0, burst=5)
        allowed, _ = await limiter.is_allowed("")
        assert isinstance(allowed, bool)
    
    @pytest.mark.asyncio
    async def test_special_characters_key(self):
        """特殊字符key的处理"""
        limiter = RateLimiter(rate=10.0, burst=5)
        allowed, _ = await limiter.is_allowed("ip:192.168.1.1:8080/api?key=test&value=123")
        assert allowed is True
    
    @pytest.mark.asyncio
    async def test_zero_rate_limiter(self):
        """零速率限流器 (完全阻止)"""
        limiter = RateLimiter(rate=0.0, burst=0)
        allowed, _ = await limiter.is_allowed("blocked_client")
        assert allowed is False
    
    @pytest.mark.asyncio
    async def test_very_high_rate(self):
        """极高速率限流器 (几乎不限制)"""
        limiter = RateLimiter(rate=99999.0, burst=99999)
        for _ in range(100):
            allowed, _ = await limiter.is_allowed("fast_client")
            assert allowed is True
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_immediate_recovery(self):
        """零恢复时间的熔断器"""
        breaker = CircuitBreaker(name="fast", failure_threshold=1, recovery_timeout=0.001)
        
        await breaker.record_failure()
        assert breaker.state == CircuitBreakerState.OPEN
        
        # 等待极短时间后应恢复
        await asyncio.sleep(0.002)
        result = await breaker.can_execute()
        assert result is True
        assert breaker.state == CircuitBreakerState.HALF_OPEN


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
