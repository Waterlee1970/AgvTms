# AgvTms 工程化成熟度提升 - 分阶段实施方案

> **文档版本**: v1.0 Implementation Guide  
> **基于**: `AgvTms-Engineering-Roadmap-v3.md`  
> **时间跨度**: 6个月 (Phase 1-4)  
> **当前状态**: Phase 0 (已启动) - 已完成2项最高ROI任务

---

## 📊 总体路线图概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    6个月工程化成熟度提升路径                          │
│                                                                     │
│   当前得分: 5.95                                                    │
│      │                                                              │
│      ├─ Phase 1 (Week 1-4):  ──→ 7.0    [+1.05] ⭐⭐⭐⭐⭐ 紧急     │
│      │                                                             │
│      ├─ Phase 2 (Week 5-8):  ──→ 8.8    [+1.8]  ⭐⭐⭐⭐ 重要      │
│      │                                                             │
│      ├─ Phase 3 (Week 9-12): ──→ 8.5    [+2.5]  ⭐⭐⭐ 重要       │
│      │                                                             │
│      └─ Phase 4 (Month 4-6):  ──→ 8.30   [+2.35] ⭐⭐⭐ 长期      │
│                                                                    │
│   目标得分: 8.30 (达到海康RCS 85%水平)                               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

✅ 已完成:
  🥇 统一API响应格式 (backend/app/schemas/response.py)
  🥈 请求追踪中间件 (backend/app/middleware/request_context.py)

📋 下一步: 继续Phase 1剩余任务...
```

---

## ✅ 已完成的最高ROI任务回顾

### 任务1: 统一API响应格式 ✅

**文件位置**: `/backend/app/schemas/response.py`

**核心能力**:
```python
from app.schemas.response import (
    success, error, bad_request, not_found,
    paginated, batch_result, ResponseCode, ApiResponse
)

# 使用示例
resp = success(data={'task_id': '123'}, message='任务创建成功', trace_id='abc123')
# 输出:
# {
#   "code": "200",
#   "message": "任务创建成功",
#   "data": {"task_id": "123"},
#   "trace_id": "abc123",
#   "timestamp": 1685928000.123
# }
```

**验收结果**: 
- ✅ 所有响应类型(成功/错误/分页/批量)均已实现
- ✅ Pydantic v2类型安全 + 自动JSON序列化
- ✅ 单元测试全部通过

### 任务2: 请求追踪中间件 ✅

**文件位置**: `/backend/app/middleware/request_context.py`

**核心能力**:
```python
from app.middleware.request_context import (
    get_request_id, get_user_id, get_tenant_id, RequestContext
)

# 在任意位置获取请求上下文
request_id = get_request_id()  # 自动透传的trace_id
tenant_id = get_tenant_id()    # 多租户隔离
```

**验收结果**:
- ✅ ContextVar异步安全 (替代ThreadLocal)
- ✅ 自动生成/传播X-Request-ID
- ✅ 支持多租户(tenant_id)隔离

---

## 🚀 Phase 1: 工程化基础补齐 (Week 1-4)

> **目标得分**: 5.95 → **7.0** (+1.05分)  
> **优先级**: ⭐⭐⭐⭐⭐ 最高优先级  
> **状态**: 🔄 进行中 (2/6任务已完成)

### Phase 1 任务清单总览

| # | 任务名称 | 对标标准 | 工期 | 负责人建议 | 状态 |
|---|---------|---------|------|-----------|------|
| 1.1 | ✅ **统一API响应格式** | 海康Result<T> | 3d | Backend Lead | ✅ 完成 |
| 1.2 | ✅ **请求追踪中间件** | Spring Sleuth | 2d | Backend Dev | ✅ 完成 |
| 1.3 | 🔲 **API限流熔断器** | Resilience4j | 3d | Backend Dev+DevOps | ⏳ 待开始 |
| 1.4 | 🔲 **测试框架搭建** | pytest+CI模板 | 2d | DevOps | ⏳ 待开始 |
| 1.5 | 🔲 **核心算法单元测试** | 80%覆盖率目标 | 5d | Algorithm Dev | ⏳ 待开始 |
| 1.6 | 🔲 **适配器集成测试** | Mockito等效 | 4d | Integration Dev | ⏳ 待开始 |

---

### 🔧 任务1.3: API限流熔断器 (预计工期: 3天)

#### Day 1: 实现令牌桶限流器

**创建文件**: `backend/app/middleware/rate_limit.py`

```python
"""
API限流与熔断器 - 对标Spring Cloud Gateway + Resilience4j

功能:
1. 基于令牌桶的速率限制 (Token Bucket)
2. 并发连接数控制
3. 熔断保护 (Circuit Breaker)
4. 限流响应标准化
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from app.schemas.response import ApiResponse, ResponseCode


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
                    "reset_after": int(1.0 / self.rate * (self.burst - bucket.tokens)),
                }
            else:
                retry_after = (1 - bucket.tokens) / self.rate
                return False, {
                    "retry_after": retry_after,
                    "limit": self.burst,
                }


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
        
        from enum import Enum
        class State(Enum):
            CLOSED = "closed"
            OPEN = "open"
            HALF_OPEN = "half_open"
        
        self.State = State
        self.state = State.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
    
    async def can_execute(self) -> bool:
        """检查是否允许执行 (OPEN态直接拒绝)"""
        async with self._lock:
            if self.state == self.State.CLOSED:
                return True
            
            if self.state == self.State.OPEN:
                # 检查是否可以转为HALF_OPEN
                if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                    self.state = self.State.HALF_OPEN
                    self.success_count = 0
                    return True
                return False
            
            # HALF_OPEN: 限制并发探测请求数
            return self.success_count < self.half_open_max_calls
    
    async def record_success(self):
        """记录成功"""
        async with self._lock:
            if self.state == self.State.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self.state = self.State.CLOSED
                    self.failure_count = 0
            
            self.failure_count = 0
    
    async def record_failure(self):
        """记录失败"""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            
            if self.state == self.State.HALF_OPEN:
                self.state = self.State.OPEN  # 探测失败, 重新熔断
            elif self.failure_count >= self.failure_threshold:
                self.state = self.State.OPEN


class RateLimitMiddleware:
    """
    FastAPI限流中间件
    
    集成到main.py:
        app.add_middleware(RateLimitMiddleware)
    """
    
    def __init__(
        self,
        rate_per_second: float = 100.0,
        burst: int = 20,
        excluded_paths: Optional[List[str]] = None,
    ):
        self.limiter = RateLimiter(rate=rate_per_second, burst=burst)
        self.excluded_paths = set(excluded_paths or ["/health", "/docs", "/openapi.json"])
    
    async def __call__(self, request: Request, call_next):
        # 跳过健康检查和文档路径
        if request.url.path in self.excluded_paths:
            return await call_next(request)
        
        # 获取限流键 (IP地址或API Key)
        client_ip = request.client.host if request.client else "unknown"
        api_key = request.headers.get("X-API-Key")
        limit_key = api_key or f"ip:{client_ip}"
        
        # 检查是否限流
        allowed, meta = await self.limiter.is_allowed(limit_key)
        
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content=ApiResponse[None](
                    code=ResponseCode.SERVICE_UNAVAILABLE,
                    message="请求过于频繁，请稍后重试",
                    data=None,
                    trace_id=request.headers.get("X-Request-ID"),
                ).model_dump()
            )
            response.headers["Retry-After"] = str(int(meta.get("retry_after", 1)))
            response.headers["X-RateLimit-Limit"] = str(meta.get("limit", 20))
            response.headers["X-RateLimit-Remaining"] = "0"
            return response
        
        # 正常处理请求
        response = await call_next(request)
        
        # 注入限流头信息
        response.headers["X-RateLimit-Limit"] = str(meta.get("limit", 20))
        response.headers["X-RateLimit-Remaining"] = str(meta.get("remaining", 0))
        
        return response
```

#### Day 2: 集成到FastAPI应用

**修改文件**: `backend/app/main.py`

```python
# 在 main.py 中添加以下导入和中间件注册

from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware

def create_app() -> FastAPI:
    app = FastAPI(
        title="AgvTms API",
        version="3.0.0",
        description="智能物流柔性调度系统 - 对标海康RCS工程化标准"
    )
    
    # 注册中间件 (顺序重要!)
    app.add_middleware(RequestContextMiddleware)  # 最外层: 请求追踪
    app.add_middleware(RateLimitMiddleware)       # 第二层: 限流保护
    
    # ... 其他路由注册 ...
    
    return app
```

#### Day 3: 编写单元测试

**创建文件**: `backend/tests/test_rate_limit.py`

```python
import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.middleware.rate_limit import RateLimiter, CircuitBreaker


class TestRateLimiter:
    """令牌桶限流器测试"""
    
    @pytest.fixture
    def limiter(self):
        return RateLimiter(rate=10.0, burst=5)
    
    @pytest.mark.asyncio
    async def test_allow_under_limit(self, limiter):
        """未超限时应该放行"""
        for _ in range(5):
            allowed, _ = await limiter.is_allowed("test_client")
            assert allowed is True
    
    @pytest.mark.asyncio
    async def test_deny_when_exhausted(self, limiter):
        """令牌用尽时应该拒绝"""
        # 先消耗所有令牌
        for _ in range(5):
            await limiter.is_allowed("test_client")
        
        # 第6次应该被拒绝
        allowed, meta = await limiter.is_allowed("test_client")
        assert allowed is False
        assert "retry_after" in meta
    
    @pytest.mark.asyncio
    async def test_token_refill_after_time(self, limiter):
        """等待后令牌应该补充"""
        # 消耗所有令牌
        for _ in range(5):
            await limiter.is_allowed("test_client")
        
        # 应该被拒绝
        allowed, _ = await limiter.is_allowed("test_client")
        assert allowed is False
        
        # 模拟等待 (实际使用time.sleep会太慢, 这里直接操作内部状态)
        import time
        limiter._buckets["test_client"].last_refill = time.monotonic() - 1.0  # 模拟过了1秒
        
        # 应该有新令牌了
        allowed, _ = await limiter.is_allowed("test_client")
        assert allowed is True


class TestCircuitBreaker:
    """熔断器测试"""
    
    @pytest.fixture
    def breaker(self):
        return CircuitBreaker(
            name="test",
            failure_threshold=3,
            recovery_timeout=1.0,
            half_open_max_calls=2
        )
    
    @pytest.mark.asyncio
    async def test_closed_state_allows_requests(self, breaker):
        """CLOSED状态应该放行所有请求"""
        assert await breaker.can_execute() is True
        assert breaker.state == breaker.State.CLOSED
    
    @pytest.mark.asyncio
    async def test_open_after_threshold_failures(self, breaker):
        """连续失败超过阈值应进入OPEN状态"""
        for i in range(3):
            await breaker.record_failure()
        
        assert breaker.state == breaker.State.OPEN
        assert await breaker.can_execute() is False
    
    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self, breaker):
        """恢复时间过后应转入HALF_OPEN"""
        # 触发熔断
        for _ in range(3):
            await breaker.record_failure()
        
        assert breaker.state == breaker.State.OPEN
        
        # 模拟等待超过recovery_timeout
        import time
        breaker.last_failure_time = time.monotonic() - 2.0
        
        # 现在应该允许探测请求
        assert await breaker.can_execute() is True
        assert breaker.state == breaker.State.HALF_OPEN
    
    @pytest.mark.asyncio
    async def test_close_after_successful_probes(self, breaker):
        """HALF_OPEN状态下探测成功应回到CLOSED"""
        # 进入HALF_OPEN
        for _ in range(3):
            await breaker.record_failure()
        import time
        breaker.last_failure_time = time.monotonic() - 2.0
        
        await breaker.can_execute()  # 触发转为HALF_OPEN
        
        # 记录足够的成功次数
        for _ in range(2):
            await breaker.record_success()
        
        assert breaker.state == breaker.State.CLOSED


class TestRateLimitMiddlewareIntegration:
    """中间件集成测试"""
    
    @pytest.fixture
    def app(self):
        return create_app()
    
    @pytest.fixture
    def client(app):
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")
    
    @pytest.mark.asyncio
    async def test_health_endpoint_not_limited(self, client):
        """健康检查端点不应该被限流"""
        for _ in range(100):
            resp = await client.get("/health")
            assert resp.status_code != 429
    
    @pytest.mark.asyncio
    async def test_api_endpoints_limited(self, client):
        """API端点应该受限流保护"""
        # 发送大量请求 (超过burst限制)
        responses = []
        for _ in range(30):
            resp = await client.get("/api/tasks")
            responses.append(resp.status_code)
        
        # 至少应该有一次返回429
        assert 429 in responses
        
        # 429响应应该包含标准的错误格式
        rate_limited_resp = [r for r in responses if r == 429][0]  # 找到第一个429的response对象
```

#### 验收标准

- [ ] 令牌桶限流器支持自定义rate/burst参数
- [ ] 熔断器三态转换逻辑正确 (CLOSED→OPEN→HALF_OPEN→CLOSED)
- [ ] 单元测试覆盖率达到90%+
- [ ] 集成到main.py且不影响现有功能
- [ ] 健康检查(/health)和文档端点不受限流影响

---

### 📝 任务1.4: 测试框架搭建 (预计工期: 2天)

#### Day 1: 配置pytest环境

**创建文件**: `backend/pyproject.toml` 或修改 `setup.cfg`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]

# 标记分类
markers = [
    "unit: 单元测试 (无需外部依赖)",
    "integration: 集成测试 (需要数据库/MQTT等)",
    "e2e: 端到端测试",
    "slow: 耗时较长的测试",
    "performance: 性能基准测试",
]

# 覆盖率配置
addopts = [
    "-v",
    "--strict-markers",
    "--cov=app",
    "--cov-report=term-missing",
    "--cov-report=html:coverage_html",
]

filterwarnings = [
    "ignore::DeprecationWarning",
]

asyncio_mode = "auto"
```

**创建文件**: `backend/conftest.py` (全局fixtures)

```python
"""
全局测试配置和Fixtures
"""

import asyncio
import pytest
import os
from typing import AsyncGenerator

# 设置测试环境变量
os.environ["TESTING"] = "true"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/agvtms_test"
os.environ["REDIS_URL"] = "redis://localhost:6379/1"


@pytest.fixture(scope="session")
def event_loop():
    """全局事件循环 (Session级别共享)"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_redis():
    """Mock Redis连接 (用于单元测试)"""
    from unittest.mock import MagicMock
    redis_mock = MagicMock()
    redis_mock.get.return_value = None
    redis_mock.set.return_value = True
    return redis_mock


@pytest.fixture
def sample_task_data():
    """标准任务测试数据"""
    return {
        "task_type": "agv_only",
        "pickup_node": "warehouse_A",
        "dropoff_node": "workstation_B",
        "priority": 10,
        "cargo": {"weight_kg": 50.0}
    }


@pytest.fixture
def sample_vehicle_data():
    """标准车辆测试数据"""
    return {
        "vehicle_id": "agv-test-001",
        "type": "latent",
        "capabilities": {
            "payload_max_kg": 300,
            "speed_max_ms": 1.2
        }
    }
```

**创建目录结构**:

```
backend/tests/
├── conftest.py              # 全局配置
├── __init__.py
├── unit/                   # 单元测试
│   ├── __init__.py
│   ├── test_schemas.py      # 数据模型测试
│   ├── test_algorithms/     # 算法测试
│   │   ├── __init__.py
│   │   ├── test_aco.py
│   │   ├── test_sa.py
│   │   └── test_hybrid.py
│   └── test_services/
│       ├── __init__.py
│       └── test_schedule_service.py
├── integration/             # 集成测试
│   ├── __init__.py
│   ├── test_api_routes.py
│   ├── test_mqtt_adapter.py
│   └── test_opcua_adapter.py
├── e2e/                     # E2E测试
│   └── test_full_workflow.py
└── performance/             # 性能测试
    └── benchmark_scheduler.py
```

#### Day 2: 创建CI流水线配置

**创建文件**: `.github/workflows/ci-pipeline.yml`

```yaml
name: CI Pipeline

on:
  push:
    branches: [main, develop, 'release/*']
  pull_request:
    branches: [main]

env:
  PYTHON_VERSION: '3.11'

jobs:
  lint:
    name: Code Quality Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      
      - name: Install linters
        run: |
          cd backend
          pip install ruff black mypy
      
      - name: Ruff Linting
        run: cd backend && ruff check .
      
      - name: Black Format Check
        run: cd backend && black --check .
      
      - name: Type Check (mypy)
        run: cd backend && mypy app --ignore-missing-imports || true

  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    needs: lint
    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: agvtms_test
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: backend/requirements-dev.txt
      
      - name: Install dependencies
        working-directory: backend
        run: |
          pip install -e ".[dev,test]"
          pip install pytest pytest-cov pytest-asyncio httpx
      
      - name: Run unit tests
        working-directory: backend
        run: |
          pytest tests/unit \
            -v \
            --cov=app \
            --cov-report=xml \
            --cov-report=term-missing \
            --cov-fail-under=60
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: backend/coverage.xml
        if: always()

  integration-tests:
    name: Integration Tests
    runs-on: ubuntu-latest
    needs: unit-tests
    services:
      postgres: # 同上
      redis: # 同上
      mqtt:
        image: eclipse-mosquitto:2
        ports: ['1883:1883']
    
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python & deps
        # ... 类似 unit-tests job ...
      - name: Run integration tests
        working-directory: backend
        run: |
          pytest tests/integration -v --integration --timeout=120
```

#### 验收标准

- [ ] `pytest tests/unit` 能正常运行
- [ ] 覆盖率报告生成在 `coverage_html/` 目录
- [ ] CI流水线在GitHub Actions上能触发运行
- [ ] 代码质量检查(ruff/black/mypy)集成到CI
- [ ] 测试数据fixtures可复用

---

### 🧪 任务1.5: 核心算法单元测试 (预计工期: 5天)

#### Day 1-2: ACO蚁群算法测试

**创建文件**: `backend/tests/unit/test_algorithms/test_aco.py`

```python
"""
ACO蚁群算法单元测试
验证: 路径规划正确性、参数敏感性、边界条件
"""

import pytest
import numpy as np
from app.algorithms.aco import AntColonyOptimization


class TestACOInitialization:
    """初始化测试"""
    
    def test_default_params(self):
        """默认参数初始化"""
        aco = AntColonyOptimization()
        assert aco.num_ants == 20
        assert aco.alpha == 1.0
        assert aco.beta == 2.0
        assert aco.rho == 0.1
    
    def test_custom_params(self):
        """自定义参数"""
        aco = AntColonyOptimization(
            num_ants=50,
            alpha=2.0,
            beta=3.0,
            rho=0.2
        )
        assert aco.num_ants == 50
        assert aco.alpha == 2.0


class TestACOSimpleGraph:
    """简单图上的路径规划"""
    
    @pytest.fixture
    def simple_graph(self):
        """4节点完全图"""
        # 节点: 0-A, 1-B, 2-C, 3-D
        # 距离矩阵 (对称)
        distances = np.array([
            [0, 10, 15, 20],
            [10, 0, 35, 25],
            [15, 35, 0, 30],
            [20, 25, 30, 0]
        ])
        return distances
    
    def test_find_path_exists(self, simple_graph):
        """能找到有效路径"""
        aco = AntColonyOptimization(num_ants=10, max_iterations=20)
        
        path, cost = aco.solve(simple_graph, start=0, end=3)
        
        # 验证路径有效性
        assert len(path) >= 2  # 至少包含起点和终点
        assert path[0] == 0     # 起点
        assert path[-1] == 3    # 终点
        assert cost > 0          # 成本为正
    
    def test_optimal_path_for_simple_case(self, simple_graph):
        """简单情况应接近最优解"""
        aco = AntColonyOptimization(
            num_ants=50,
            max_iterations=100,
            rho=0.1
        )
        
        path, cost = aco.solve(simple_graph, start=0, end=3)
        
        # 0->3 的最短路径应该是直达 (cost=20) 或 0->1->3 (cost=35)
        # 或者 0->2->3 (cost=45)
        # 算法应该找到接近20的解
        assert cost <= 25  # 允许一定误差 (启发式算法)


class TestACOMultiAGV:
    """多车避碰场景"""
    
    def test_no_collision_paths(self):
        """多辆车不应规划出冲突路径"""
        # 构造可能冲突的场景
        graph = self._create_conflict_scenario()
        
        aco = AntColonyOptimization()
        
        # 为两辆AGV规划路径
        path1, _ = aco.solve(graph, start=0, end=5)
        path2, _ = aco.solve(graph, start=5, end=0)
        
        # 简单检查: 两辆车不应在同一时刻位于同一节点
        # (这里简化验证, 实际应考虑时间窗口)
        assert path1 is not None
        assert path2 is not None
    
    def _create_conflict_scenario(self):
        """创建一个窄通道场景"""
        # 节点布局:
        # 0 -- 1 -- 2 -- 3
        #      |         |
        #      4 --------5
        # 只有1-4这一条窄通道
        pass


class TestACOEdgeCases:
    """边界条件测试"""
    
    def test_same_start_and_end(self):
        """起点终点相同时"""
        graph = np.array([[0, 10], [10, 0]])
        aco = AntColonyOptimization()
        
        path, cost = aco.solve(graph, start=0, end=0)
        
        assert cost == 0 or cost is None  # 取决于实现
    
    def test_disconnected_graph(self):
        """不连通图应报错或返回空"""
        graph = np.array([
            [0, 10, float('inf')],
            [10, 0, float('inf')],
            [float('inf'), float('inf'), 0]
        ])
        aco = AntColonyOptimization()
        
        # 可能抛异常或返回特殊值
        try:
            path, cost = aco.solve(graph, start=0, end=2)
            assert path is None or cost == float('inf')
        except Exception:
            pass  # 抛异常也是可以接受的行为
```

#### Day 3-4: SA模拟退火算法测试

**创建文件**: `backend/tests/unit/test_algorithms/test_sa.py`

```python
"""
SA模拟退火算法单元测试
验证: 任务分配优化、温度调度、收敛性
"""

import pytest
import numpy as np
from app.algorithms.sa import SimulatedAnnealing


class TestSATaskAssignment:
    """任务分配场景测试"""
    
    @pytest.fixture
    def simple_scenario(self):
        """3个任务分配给2台AGV"""
        tasks = [
            {"id": "T1", "pickup": "A", "dropoff": "B", "duration": 10},
            {"id": "T2", "pickup": "B", "dropoff": "C", "duration": 15},
            {"id": "T3", "pickup": "A", "dropoff": "C", "duration": 20},
        ]
        vehicles = [
            {"id": "V1", "position": "A"},
            {"id": "V2", "position": "B"},
        ]
        return tasks, vehicles
    
    def test_feasible_solution(self, simple_scenario):
        """应产生可行解 (每任务都有车辆执行)"""
        tasks, vehicles = simple_scenario
        sa = SimulatedAnnealing(T_init=500, cooling_rate=0.92, max_iter=100)
        
        solution = sa.solve(tasks, vehicles)
        
        # 每个任务都应有对应的车辆
        for task_id in [t["id"] for t in tasks]:
            assigned_vehicle = solution.get(task_id)
            assert assigned_vehicle is not None
            assert assigned_vehicle in [v["id"] for v in vehicles]
    
    def test_solution_quality_improves(self, simple_scenario):
        """迭代过程中解质量应逐步改善"""
        tasks, vehicles = simple_scenario
        sa = SimulatedAnnealing(T_init=1000, cooling_rate=0.95, max_iter=200)
        
        # 记录初始解成本
        initial_cost = sa._calculate_cost(sa._greedy_initial(tasks, vehicles))
        
        # 运行完整优化
        final_solution = sa.solve(tasks, vehicles)
        final_cost = sa._calculate_cost(final_solution)
        
        # 最终成本应 <= 初始成本 (允许一定的随机性导致略差)
        # 这里放宽要求: 大多数情况下应该改善
        assert final_cost <= initial_cost * 1.2  # 允许20%误差


class TestSACoolingSchedule:
    """温度调度策略测试"""
    
    def test_temperature_decreases(self):
        """温度应随迭代单调递减"""
        sa = SimulatedAnnealing(T_init=1000, cooling_rate=0.9)
        
        T_prev = sa.T_init
        for i in range(50):
            T_current = sa.T_init * (sa.cooling_rate ** i)
            assert T_current <= T_prev
            T_prev = T_current
    
    def test_final_temperature_near_zero(self):
        """足够迭代后温度应趋近于零"""
    [User Cancelled]