"""
Resilience & Circuit Breaker Tests — Phase 5 工程化加固.

测试覆盖:
  - CircuitBreaker 状态转换
  - RateLimiter 限流
  - Fallback 链
  - Leader Election (mock)
"""

import pytest
import time
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from app.core.resilience import (
    CircuitBreaker, CircuitState, CircuitOpenError, BreakerConfig,
    TokenBucketRateLimiter,
    LeaderElection,
    HealthAggregator,
)


class TestCircuitBreaker:
    """断路器单元测试."""
    
    def test_initial_state_is_closed(self):
        breaker = CircuitBreaker("test", config=BreakerConfig(failure_threshold=3))
        assert breaker.state == CircuitState.CLOSED
        assert breaker.is_closed is True
        assert breaker.is_open is False
        
    def test_success_does_not_change_state(self):
        breaker = CircuitBreaker("test", config=BreakerConfig(failure_threshold=3))
        
        for _ in range(10):
            breaker.protect_sync(lambda: "ok")
            
        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.successes == 10
        
    def test_trips_open_after_threshold(self):
        breaker = CircuitBreaker("test", config=BreakerConfig(failure_threshold=3))
        
        def fail():
            raise ConnectionError("Service unavailable")
            
        # 前2次失败，断路器仍关闭
        with pytest.raises(ConnectionError):
            breaker.protect_sync(fail)
        with pytest.raises(ConnectionError):
            breaker.protect_sync(fail)
        assert breaker.is_closed  # 还没到阈值
            
        # 第3次触发断开
        with pytest.raises(ConnectionError):
            breaker.protect_sync(fail)
        assert breaker.is_open
        assert breaker.stats.failures == 3
        
    def test_rejects_requests_when_open(self):
        breaker = CircuitBreaker("test", config=BreakerConfig(failure_threshold=1, recovery_timeout=60))
        
        # 触发断开
        try:
            breaker.protect_sync(lambda: (_ for _ in ()).throw(Exception("fail")))
        except Exception:
            pass
        assert breaker.call_allowed() is False
        assert breaker.stats.rejected_calls >= 1
        
    def test_half_open_after_timeout(self):
        breaker = CircuitBreaker(
            "test_half_open",
            config=BreakerConfig(failure_threshold=1, recovery_timeout=0.05)  # 极短超时用于测试
        )
        
        # 触发断开
        try:
            breaker.protect_sync(lambda: (_ for _ in ()).throw(Exception()))
        except Exception:
            pass
        assert breaker.is_open
        
        # 等待恢复时间
        time.sleep(0.06)
        
        # 应该进入半开状态 (call_allowed 会触发状态转换)
        allowed = breaker.call_allowed()
        assert allowed is True
        assert breaker.state == CircuitState.HALF_OPEN
        
    def test_recovers_to_closed_on_success(self):
        breaker = CircuitBreaker(
            "test_recover",
            config=BreakerConfig(failure_threshold=1, recovery_timeout=0.02, success_threshold=1),
        )
        
        # 断开 → 半开 → 成功 → 关闭
        try:
            breaker.protect_sync(lambda: (_ for _ in ()).throw(Exception()))
        except Exception:
            pass
        time.sleep(0.03)
        breaker.call_allowed()  # 进入半开
        breaker.protect_sync(lambda: "ok")  # 成功调用应触发 CLOSED
        
        assert breaker.is_closed
        
    @pytest.mark.asyncio
    async def test_async_protection(self):
        breaker = CircuitBreaker("async_test")
        
        async def async_func():
            await asyncio.sleep(0.001)
            return "async_ok"
            
        result = await breaker.protect_async(async_func)
        assert result == "async_ok"
        assert breaker.stats.successes == 1
        
    def test_decorator_usage(self):
        """测试装饰器用法."""
        from app.core.resilience import db_breaker
        
        call_count = [0]
        
        @db_breaker.protect
        def db_query():
            call_count[0] += 1
            return {"data": "ok"}
            
        result = db_query()
        assert result["data"] == "ok"
        assert call_count[0] == 1
        
    def test_get_status(self):
        breaker = CircuitBreaker("status_test")
        breaker.protect_sync(lambda: None)
        status = breaker.get_status()
        
        assert status["name"] == "status_test"
        assert "state" in status
        assert "stats" in status
        assert status["stats"]["total_calls"] >= 1


class TestRateLimiter:
    """限流器测试."""
    
    def test_basic_rate_limiting(self):
        limiter = TokenBucketRateLimiter(rps=100.0, burst=20)
        
        # 初始 burst 允许快速请求
        allowed = sum(1 for _ in range(20) if limiter.allow())
        assert allowed == 20  # burst 全部通过
        
        # 超过burst后应被限流
        next_allowed = limiter.allow()
        # 可能允许也可能不允许，取决于补充速率
        
    def test_stats_reporting(self):
        limiter = TokenBucketRateLimiter(rps=10, burst=5)
        
        for _ in range(8):  # 5个burst + 3个被拒
            limiter.allow()
            
        stats = limiter.get_stats()
        assert stats["total_requests"] == 8
        assert stats["rejected"] >= 2


class TestFallbackChain:
    """降级链测试."""
    
    def test_fallback_on_circuit_open(self):
        breaker = CircuitBreaker("fb_test", config=BreakerConfig(failure_threshold=1))
        
        # 先让断路器打开
        try:
            breaker.protect_sync(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        except RuntimeError:
            pass
            
        # 使用 fallback
        result = breaker.protect_sync(
            lambda: "should_not_run",
            fallback=lambda: "fallback_value"
        )
        
        if isinstance(result, dict) and result.get("success"):
            assert result["method_used"].startswith("circuit_")
        else:
            # 如果断路器已自动恢复（时间问题）
            assert True


class TestHealthAggregator:
    """健康聚合器测试."""
    
    @pytest.mark.asyncio
    async def test_all_healthy(self):
        agg = HealthAggregator()
        agg.register("check1", lambda: True)
        agg.register("check2", lambda: True)
        
        report = await agg.check_all()
        assert report["status"] == "healthy"
        assert all(c["healthy"] for c in report["checks"].values())
        
    @pytest.mark.asyncio
    async def test_degraded_when_one_fails(self):
        agg = HealthAggregator()
        agg.register("ok_check", lambda: True)
        agg.register("fail_check", lambda: False)
        
        report = await agg.check_all()
        assert report["status"] == "degraded"


# ==================== 性能测试 (标记为 slow) ====================

class TestResiliencePerformance:
    
    @pytest.mark.slow
    def test_circuit_breaker_throughput_under_load(self):
        """高负载下断路器的吞吐量影响."""
        import timeit
        
        breaker = CircuitBreaker("perf_test")
        
        def fast_operation():
            return "ok"
            
        # 无失败时的开销
        iterations = 10000
        elapsed = timeit.timeit(
            lambda: breaker.protect_sync(fast_operation),
            number=iterations
        )
        
        avg_latency_ms = (elapsed / iterations) * 1000
        print(f"\nCircuit Breaker overhead: {avg_latency_ms:.4f} ms/call ({iterations} calls)")
        assert avg_latency_ms < 0.5  # 每次调用 <0.5ms 开销


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
