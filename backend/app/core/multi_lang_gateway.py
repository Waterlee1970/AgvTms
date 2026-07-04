"""
多语言后端 API Gateway — 路由转发与服务发现.

功能:
  1. 智能路由: 根据路径前缀/服务名转发到对应后端 (Python/Java/.NET/Go/Node.js)
  2. 服务注册与发现: Redis-based 轻量级注册中心
  3. 健康检查: 定期探测后端服务可用性
  4. 熔断降级: 单个后端故障不影响全局
  5. 负载均衡: Round-Robin / Weighted-Random / Least-Connections
  6. 请求聚合: 合并多个后端响应
  7. 分布式追踪: OpenTelemetry 兼容的 Trace ID 注入

使用方式:
  from app.core.multi_lang_gateway import MultiLangGateway, gateway
  
  # 初始化 (在 main.py lifespan 中)
  await gateway.initialize()
  
  # 作为 FastAPI 中间件或路由挂载
  app.add_middleware(GatewayMiddleware, gateway=gateway)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


# ==================== 数据模型 ====================


class ServiceProtocol(str, Enum):
    """后端服务协议."""
    HTTP = "http"
    HTTPS = "https"
    GRPC = "grpc"       # 需要特殊处理 (grpcio)
    WEBSOCKET = "ws"    # WebSocket 升级


class LoadBalanceStrategy(str, Enum):
    """负载均衡策略."""
    ROUND_ROBIN = "round_robin"        # 轮询
    WEIGHTED_RANDOM = "weighted_random" # 加权随机
    LEAST_CONNECTIONS = "least_connections"  # 最少连接
    IP_HASH = "ip_hash"                # IP哈希 (会话粘滞)


class CircuitBreakerState(str, Enum):
    """熔断器状态."""
    CLOSED = "closed"      # 正常工作
    OPEN = "open"          # 熔断中 (拒绝请求)
    HALF_OPEN = "half_open" # 半开 (试探性放行)


@dataclass
class BackendService:
    """后端服务定义."""
    
    name: str                           # 服务名称 (如 "agvtms-scheduler-java")
    host: str                           # 主机地址 (Docker服务名或IP)
    port: int                           # 端口
    protocol: ServiceProtocol = ServiceProtocol.HTTP
    version: str = "1.0.0"
    health_url: Optional[str] = None     # 健康检查URL (默认 /healthz)
    weight: int = 100                    # 负载权重 (用于加权随机)
    tags: List[str] = field(default_factory=list)  # 标签 ["java", "scheduler", "critical"]
    timeout_seconds: float = 30.0        # 请求超时
    max_retries: int = 2                 # 最大重试次数
    
    # 运行时状态 (非配置)
    is_healthy: bool = True
    last_health_check: Optional[datetime] = None
    consecutive_failures: int = 0       # 连续失败次数
    active_connections: int = 0         # 当前活跃连接数
    total_requests: int = 0             # 总请求数
    success_count: int = 0              # 成功数
    failure_count: int = 0              # 失败数
    avg_response_time_ms: float = 0.0   # 平均响应时间(ms)
    
    @property
    def base_url(self) -> str:
        """构建基础 URL."""
        if self.protocol == ServiceProtocol.WEBSOCKET:
            return f"ws://{self.host}:{self.port}"
        elif self.protocol == ServiceProtocol.GRPC:
            return f"grpc://{self.host}:{self.port}"
        else:
            return f"{self.protocol.value}://{self.host}:{self.port}"


@dataclass
class RouteRule:
    """路由规则."""
    
    path_prefix: str              # 路径前缀 (如 "/api/v2/schedule")
    target_service: str           # 目标服务名
    methods: Optional[List[str]] = None  # HTTP方法过滤 (None=全部)
    strip_prefix: bool = True     # 是否剥离前缀
    priority: int = 100           # 优先级 (数字越小越优先)
    timeout_override: Optional[float] = None  # 超时覆盖
    enabled: bool = True


@dataclass
class CircuitBreakerConfig:
    """熔断器配置."""
    
    failure_threshold: int = 5         # 触发熔断的连续失败次数
    reset_timeout_seconds: float = 30.0  # 熔断恢复等待时间
    half_open_max_calls: int = 3       # 半开状态最大试探请求数
    slow_call_threshold_ms: float = 5000.0  # 慢调用阈值(ms)


@dataclass
class GatewayRequest:
    """网关请求上下文."""
    
    request_id: str = ""
    method: str = "GET"
    path: str = "/"
    query_params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    client_ip: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # 路由结果
    matched_route: Optional[RouteRule] = None
    target_service: Optional[BackendService] = None


@dataclass 
class GatewayResponse:
    """网关响应."""
    
    status_code: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    response_time_ms: float = 0.0
    from_service: str = ""      # 来源服务
    from_cache: bool = False     # 是否来自缓存
    error: Optional[str] = None


# ==================== 服务注册表 ====================


class ServiceRegistry:
    """
    Redis-based 轻量级服务注册表.
    
    功能:
      - 服务注册/注销/心跳续约
      - 服务发现 (按名称/标签查询)
      - 健康状态管理
      - TTL 自动过期清理
    
    Redis Key 结构:
      agvtms:services:{name}          → Hash (服务信息)
      agvtms:services:index:name      → Set  (所有服务名)
      agvtms:services:index:tag:{tag} → Set  (按标签索引)
    """
    
    REDIS_KEY_PREFIX = "agvtms:services"
    HEARTBEAT_INTERVAL_SECONDS = 10   # 心跳间隔
    SERVICE_TTL_SECONDS = 30          # 服务过期时间 (2倍心跳间隔)
    
    def __init__(self):
        self._local_services: Dict[str, BackendService] = {}
        self._redis_client = None
        self._use_redis = False
        
    async def initialize(self, redis_client=None):
        """初始化 (可选Redis客户端)."""
        if redis_client:
            self._redis_client = redis_client
            self._use_redis = True
            logger.info("ServiceRegistry initialized with Redis backend")
        else:
            logger.info("ServiceRegistry initialized in local-only mode")
    
    async def register(self, service: BackendService) -> bool:
        """注册服务."""
        self._local_services[service.name] = service
        
        # 设置健康检查URL (如果未指定)
        if not service.health_url:
            service.health_url = f"{service.base_url}/healthz"
        
        if self._use_redis and self._redis_client:
            try:
                key = f"{self.REDIS_KEY_PREFIX}:{service.name}"
                service_data = {
                    "name": service.name,
                    "host": service.host,
                    "port": str(service.port),
                    "protocol": service.protocol.value,
                    "version": service.version,
                    "health_url": service.health_url or "",
                    "weight": str(service.weight),
                    "tags": ",".join(service.tags),
                    "registered_at": datetime.utcnow().isoformat(),
                }
                await self._redis_client.hset(key, mapping=service_data)
                await self._redis_client.expire(key, self.SERVICE_TTL_SECONDS)
                
                # 更新索引
                await self._redis_client.sadd(f"{self.REDIS_KEY_PREFIX}:index:name", service.name)
                for tag in service.tags:
                    await self._redis_client.sadd(
                        f"{self.REDIS_KEY_PREFIX}:index:tag:{tag}", service.name
                    )
                    
                logger.info("Service registered: %s → %s:%d", service.name, service.host, service.port)
                return True
                
            except Exception as e:
                logger.warning("Redis register failed for %s: %s", service.name, e)
        
        return True
    
    async def deregister(self, name: str) -> bool:
        """注销服务."""
        if name in self._local_services:
            del self._local_services[name]
        
        if self._use_redis and self._redis_client:
            try:
                # 获取标签以清理索引
                key = f"{self.REDIS_KEY_PREFIX}:{name}"
                tags_str = await self._redis_client.hget(key, "tags") or ""
                tags = tags_str.split(",") if tags_str else []
                
                await self._redis_client.delete(key)
                await self._redis_client.srem(f"{self.REDIS_KEY_PREFIX}:index:name", name)
                for tag in tags:
                    if tag:
                        await self._redis_client.srem(f"{self.REDIS_KEY_PREFIX}:index:tag:{tag}", name)
                        
                logger.info("Service deregistered: %s", name)
                return True
            except Exception as e:
                logger.warning("Redis deregister failed for %s: %s", name, e)
        
        return True
    
    async def discover(self, name: str = None, tag: str = None) -> List[BackendService]:
        """服务发现."""
        services = []
        
        if name:
            # 精确查找
            service = self._local_services.get(name)
            if service:
                services.append(service)
        else:
            # 按标签查找
            for svc in self._local_services.values():
                if not tag or tag in svc.tags:
                    services.append(svc)
        
        # 如果使用Redis，合并远程数据
        if self._use_redis and self._redis_client:
            try:
                if name:
                    remote_key = f"{self.REDIS_KEY_PREFIX}:{name}"
                    remote_data = await self._redis_client.hgetall(remote_key)
                    if remote_data and name not in [s.name for s in services]:
                        services.append(self._dict_to_service(remote_data))
                elif tag:
                    remote_names = await self._redis_client.smembers(
                        f"{self.REDIS_KEY_PREFIX}:index:tag:{tag}"
                    )
                    for rname in remote_names:
                        rname = rname.decode() if isinstance(rname, bytes) else rname
                        if rname not in [s.name for s in services]:
                            remote_data = await self._redis_client.hgetall(
                                f"{self.REDIS_KEY_PREFIX}:{rname}"
                            )
                            if remote_data:
                                services.append(self._dict_to_service(remote_data))
            except Exception as e:
                logger.warning("Redis discovery failed: %s", e)
        
        return [s for s in services if s.is_healthy]
    
    async def heartbeat(self, name: str) -> bool:
        """心跳续约."""
        if self._use_redis and self._redis_client:
            try:
                key = f"{self.REDIS_KEY_PREFIX}:{name}"
                exists = await self._redis_client.exists(key)
                if exists:
                    await self._redis_client.expire(key, self.SERVICE_TTL_SECONDS)
                    await self._redis_client.hset(
                        key, "last_heartbeat", datetime.utcnow().isoformat()
                    )
                    return True
            except Exception as e:
                logger.debug("Heartbeat failed for %s: %s", name, e)
        return False
    
    async def list_all_services(self) -> List[Dict[str, Any]]:
        """列出所有已注册服务 (含状态)."""
        result = []
        for name, svc in self._local_services.items():
            result.append({
                "name": svc.name,
                "host": svc.host,
                "port": svc.port,
                "protocol": svc.protocol.value,
                "version": svc.version,
                "is_healthy": svc.is_healthy,
                "active_connections": svc.active_connections,
                "total_requests": svc.total_requests,
                "success_rate": (
                    f"{svc.success_count / max(svc.total_requests, 1) * 100:.1f}%"
                    if svc.total_requests > 0 else "N/A"
                ),
                "avg_response_time_ms": f"{svc.avg_response_time_ms:.1f}",
            })
        return result
    
    @staticmethod
    def _dict_to_service(data: dict) -> BackendService:
        """从字典创建服务实例 (处理bytes类型)."""
        def _get(key, default=""):
            val = data.get(key, default)
            return val.decode() if isinstance(val, bytes) else val
        
        return BackendService(
            name=_get("name"),
            host=_get("host"),
            port=int(_get("port", "0")),
            protocol=ServiceProtocol(_get("protocol", "http")),
            version=_get("version", "1.0.0"),
            health_url=_get("health_url") or None,
            weight=int(_get("weight", "100")),
            tags=[t.strip() for t in _get("tags", "").split(",") if t.strip()],
        )


# ==================== 熔断器 ====================


class CircuitBreaker:
    """
    熔断器实现.
    
    状态转换:
      CLOSED ──(连续失败>=threshold)──► OPEN
       ▲                                    │
       │                          (timeout过期)
       │                                    ▼
       ◄──── HALF_OPEN ──(成功) ──────────┘
                        │
                   (失败≥max_calls)
                        │
                        ▼
                      OPEN
    """
    
    def __init__(self, config: CircuitBreakerConfig = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_success_count = 0
        self._lock = asyncio.Lock()
    
    async def can_execute(self) -> Tuple[bool, Optional[str]]:
        """检查是否允许执行请求."""
        async with self._lock:
            if self.state == CircuitBreakerState.CLOSED:
                return True, None
            
            elif self.state == CircuitBreakerState.OPEN:
                # 检查是否可以转为半开状态
                if self.last_failure_time:
                    elapsed = time.time() - self.last_failure_time
                    if elapsed >= self.config.reset_timeout_seconds:
                        self.state = CircuitBreakerState.HALF_OPEN
                        self.half_open_success_count = 0
                        logger.info("Circuit breaker transitioning to HALF_OPEN")
                        return True, None
                
                return False, f"Circuit breaker OPEN (retry after {self.config.reset_timeout_seconds:.0f}s)"
            
            else:  # HALF_OPEN
                if self.half_open_success_count >= self.config.half_open_max_calls:
                    return False, "Circuit breaker HALF_OPEN (max probe calls reached)"
                return True, None
    
    async def record_success(self):
        """记录成功调用."""
        async with self._lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.half_open_success_count += 1
                if self.half_open_success_count >= self.config.half_open_max_calls:
                    self.state = CircuitBreakerState.CLOSED
                    self.failure_count = 0
                    logger.info("Circuit breaker recovered to CLOSED")
            elif self.state == CircuitBreakerState.CLOSED:
                self.failure_count = 0  # 重置失败计数
    
    async def record_failure(self):
        """记录失败调用."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.state = CircuitBreakerState.OPEN
                logger.warning("Circuit breaker tripped to OPEN from HALF_OPEN")
                
            elif self.state == CircuitBreakerState.CLOSED:
                if self.failure_count >= self.config.failure_threshold:
                    self.state = CircuitBreakerState.OPEN
                    logger.warning(
                        "Circuit breaker OPEN after %d failures",
                        self.failure_count,
                    )
    
    @property
    def state_info(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure_time": (
                datetime.fromtimestamp(self.last_failure_time).isoformat()
                if self.last_failure_time else None
            ),
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "reset_timeout_seconds": self.config.reset_timeout_seconds,
                "half_open_max_calls": self.config.half_open_max_calls,
            },
        }


# ==================== 负载均衡器 ====================


class LoadBalancer:
    """负载均衡器."""
    
    def __init__(self, strategy: LoadBalanceStrategy = LoadBalanceStrategy.ROUND_ROBIN):
        self.strategy = strategy
        self._round_robin_counter = 0
        self._lock = asyncio.Lock()
    
    def select(
        self,
        services: List[BackendService],
        client_ip: str = "",
    ) -> Optional[BackendService]:
        """选择目标服务实例."""
        if not services:
            return None
        
        healthy_services = [s for s in services if s.is_healthy]
        if not healthy_services:
            # 退化: 使用所有服务 (包括不健康的)
            healthy_services = services
        
        if len(healthy_services) == 1:
            return healthy_services[0]
        
        if self.strategy == LoadBalanceStrategy.ROUND_ROBIN:
            return self._round_robin(healthy_services)
        elif self.strategy == LoadBalanceStrategy.WEIGHTED_RANDOM:
            return self._weighted_random(healthy_services)
        elif self.strategy == LoadBalanceStrategy.LEAST_CONNECTIONS:
            return self._least_connections(healthy_services)
        elif self.strategy == LoadBalanceStrategy.IP_HASH:
            return self._ip_hash(healthy_services, client_ip)
        else:
            return healthy_services[0]
    
    def _round_robin(self, services: List[BackendService]) -> BackendService:
        idx = self._round_robin_counter % len(services)
        self._round_robin_counter += 1
        return services[idx]
    
    def _weighted_random(self, services: List[BackendService]) -> BackendService:
        total_weight = sum(s.weight for s in services)
        if total_weight <= 0:
            return services[0]
        
        import random
        rand_val = random.randint(0, total_weight)
        cumulative = 0
        for svc in services:
            cumulative += svc.weight
            if rand_val <= cumulative:
                return svc
        return services[-1]
    
    def _least_connections(self, services: List[BackendService]) -> BackendService:
        return min(services, key=lambda s: s.active_connections)
    
    def _ip_hash(self, services: List[BackendService], client_ip: str) -> BackendService:
        if not client_ip:
            return services[0]
        hash_val = int(hashlib.md5(client_ip.encode()).hexdigest(), 16)
        return services[hash_val % len(services)]


# ==================== API Gateway 核心类 ====================


class MultiLangGateway:
    """
    多语言后端 API Gateway.
    
    功能:
      - 路由规则管理与匹配
      - 服务发现与选择
      - 请求转发 (httpx 异步HTTP客户端)
      - 熔断保护
      - 负载均衡
      - 重试机制
      - 响应缓存 (可选)
    
    配置示例:
      gateway = MultiLangGateway()
      
      # 注册后端服务
      await gateway.register_service(BackendService(
          name="agvtms-scheduler-java",
          host="scheduler-java",
          port=8080,
          tags=["java", "scheduler"],
      ))
      
      # 添加路由规则
      gateway.add_route(RouteRule(
          path_prefix="/api/v2/schedule",
          target_service="agvtms-scheduler-java",
      ))
      
      # 处理请求
      response = await gateway.forward(request_context)
    """
    
    def __init__(self):
        self.registry = ServiceRegistry()
        self.routes: List[RouteRule] = []
        self.load_balancer = LoadBalancer()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._http_client: Optional[httpx.AsyncClient] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._initialized = False
        
        # 统计指标
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "circuit_breaker_trips": 0,
        }
    
    async def initialize(
        self,
        redis_client=None,
        http_timeout: float = 30.0,
        enable_health_check: bool = True,
    ):
        """初始化 Gateway."""
        await self.registry.initialize(redis_client)
        
        # 创建共享 httpx 客户端
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=http_timeout, connect=5.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=True,
        )
        
        # 启动健康检查任务
        if enable_health_check:
            self._health_check_task = asyncio.create_task(
                self._health_check_loop()
            )
        
        self._initialized = True
        logger.info("MultiLangGateway initialized successfully")
    
    async def shutdown(self):
        """关闭 Gateway (释放资源)."""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        if self._http_client:
            await self._http_client.aclose()
        
        logger.info("MultiLangGateway shutdown complete")
    
    # ========== 服务管理 ==========
    
    async def register_service(self, service: BackendService) -> bool:
        """注册后端服务 + 初始化熔断器."""
        result = await self.registry.register(service)
        if result:
            self.circuit_breakers[service.name] = CircuitBreaker()
            # 添加默认路由 (如果没有显式添加)
            self._add_default_routes_for_service(service)
        return result
    
    async def deregister_service(self, name: str) -> bool:
        """注销后端服务."""
        if name in self.circuit_breakers:
            del self.circuit_breakers[name]
        # 移除相关路由
        self.routes = [r for r in self.routes if r.target_service != name]
        return await self.registry.deregister(name)
    
    def _add_default_routes_for_service(self, service: BackendService):
        """为新注册的服务添加默认路由规则."""
        # 根据服务名推断路由前缀
        name_lower = service.name.lower()
        
        if "schedule" in name_lower:
            prefix = "/api/v2/schedule"
        elif "opcua" in name_lower or "opc-ua" in name_lower:
            prefix = "/api/v2/opcua"
        elif "vehicle" in name_lower:
            prefix = "/api/v2/vehicles"
        elif "notify" in name_lower or "notification" in name_lower:
            prefix = "/api/v2/notify"
        elif "algorithm" in name_lower:
            prefix = "/api/v2/algorithms"
        else:
            # 通用前缀: /api/ext/{service-name}
            prefix = f"/api/ext/{service.name.replace('agvtms-', '')}"
        
        # 检查是否已有相同前缀的路由
        existing = [r for r in self.routes if r.path_prefix == prefix]
        if not existing:
            self.routes.append(RouteRule(
                path_prefix=prefix,
                target_service=service.name,
                priority=100,
            ))
            logger.info("Auto-added route: %s → %s", prefix, service.name)
    
    # ========== 路由管理 ==========
    
    def add_route(self, route: RouteRule):
        """添加路由规则."""
        # 按优先级排序插入
        self.routes.append(route)
        self.routes.sort(key=lambda r: r.priority)
        logger.debug("Route added: %s → %s (priority=%d)", route.path_prefix, route.target_service, route.priority)
    
    def remove_route(self, path_prefix: str):
        """移除路由规则."""
        self.routes = [r for r in self.routes if r.path_prefix != path_prefix]
    
    def match_route(self, path: str, method: str = "GET") -> Optional[Tuple[RouteRule, str]]:
        """
        匹配路由规则.
        
        Returns:
            (matched_rule, remaining_path) 或 None
        """
        for route in self.routes:
            if not route.enabled:
                continue
            
            if route.methods and method.upper() not in [m.upper() for m in route.methods]:
                continue
            
            if path.startswith(route.path_prefix):
                remaining = path[len(route.path_prefix):] if route.strip_prefix else path
                return route, remaining
        
        return None
    
    # ========== 请求转发核心 ==========
    
    async def forward(self, request: GatewayRequest) -> GatewayResponse:
        """
        转发请求到目标后端服务.
        
        流程:
          1. 匹配路由规则
          2. 发现并选择目标服务 (负载均衡)
          3. 检查熔断器
          4. 构建转发请求
          5. 发送HTTP请求 (带重试)
          6. 记录统计
        """
        self.stats["total_requests"] += 1
        start_time = time.time()
        
        try:
            # Step 1: 匹配路由
            match_result = self.match_route(request.path, request.method)
            if not match_result:
                return GatewayResponse(
                    status_code=404,
                    body=json.dumps({"error": "No matching route found"}).encode(),
                    error=f"No route for {request.method} {request.path}",
                )
            
            route_rule, remaining_path = match_result
            request.matched_route = route_rule
            
            # Step 2: 服务发现 + 负载均衡
            services = await self.registry.discover(name=route_rule.target_service)
            if not services:
                return GatewayResponse(
                    status_code=503,
                    body=json.dumps({"error": f"No available service: {route_rule.target_service}"}).encode(),
                    error=f"Service unavailable: {route_rule.target_service}",
                )
            
            selected_service = self.load_balancer.select(services, request.client_ip)
            if not selected_service:
                return GatewayResponse(
                    status_code=503,
                    body=json.dumps({"error": "No healthy instance"}).encode(),
                    error="All instances unhealthy",
                )
            
            request.target_service = selected_service
            selected_service.active_connections += 1
            selected_service.total_requests += 1
            
            # Step 3: 熔断检查
            cb = self.circuit_breakers.get(selected_service.name)
            if cb:
                can_execute, cb_error = await cb.can_execute()
                if not can_execute:
                    selected_service.active_connections -= 1
                    selected_service.failure_count += 1
                    self.stats["circuit_breaker_trips"] += 1
                    return GatewayResponse(
                        status_code=503,
                        body=json.dumps({
                            "error": "Service temporarily unavailable",
                            "reason": cb_error,
                        }).encode(),
                        error=f"Circuit breaker open: {selected_service.name}",
                    )
            
            # Step 4 & 5: 构建并发送请求
            target_url = urljoin(selected_service.base_url, remaining_path or "/")
            
            # 查询参数
            if request.query_params:
                import urllib.parse
                existing_params = urllib.parse.urlparse(target_url).query
                if existing_params:
                    target_url = target_url.split("?")[0]
                params = request.query_params
            else:
                params = None
            
            # 请求头 (注入追踪ID)
            forward_headers = dict(request.headers)
            forward_headers["X-Request-ID"] = request.request_id
            forward_headers["X-Forwarded-For"] = request.client_ip
            forward_headers["X-Forwarded-Host"] = forward_headers.get("Host", "")
            forward_headers["X-Gateway-From"] = "agvtms-gateway"
            forward_headers.pop("Host", None)  # 让httpx自动设置
            
            # 发送请求 (带重试)
            last_exception = None
            retry_count = 0
            response = None
            
            while retry_count <= selected_service.max_retries:
                try:
                    response = await self._http_client.request(
                        method=request.method,
                        url=target_url,
                        headers=forward_headers,
                        content=request.body,
                        params=params,
                        timeout=route_rule.timeout_override or selected_service.timeout_seconds,
                    )
                    break
                    
                except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                    last_exception = e
                    retry_count += 1
                    if retry_count <= selected_service.max_retries:
                        logger.debug(
                            "Retry %d/%d for %s: %s",
                            retry_count, selected_service.max_retries, selected_service.name, e,
                        )
                        await asyncio.sleep(0.1 * retry_count)  # 指数退避
                    continue
            
            selected_service.active_connections -= 1
            response_time_ms = (time.time() - start_time) * 1000
            
            # Step 6: 处理响应
            if response is None:
                # 所有重试都失败
                selected_service.failure_count += 1
                self.stats["failed_requests"] += 1
                
                if cb:
                    await cb.record_failure()
                
                return GatewayResponse(
                    status_code=502,
                    body=json.dumps({
                        "error": "Upstream service unavailable",
                        "service": selected_service.name,
                        "retries": retry_count,
                    }).encode(),
                    response_time_ms=response_time_ms,
                    from_service=selected_service.name,
                    error=str(last_exception),
                )
            
            # 成功响应
            selected_service.success_count += 1
            self.stats["successful_requests"] += 1
            
            # 更新平均响应时间 (EMA)
            alpha = 0.3
            selected_service.avg_response_time_ms = (
                alpha * response_time_ms +
                (1 - alpha) * selected_service.avg_response_time_ms
            )
            
            if cb:
                await cb.record_success()
            
            # 构建响应
            resp_body = await response.aread()
            resp_headers = dict(response.headers)
            # 移除hop-by-hop头
            for hop_header in ["transfer-encoding", "connection", "keep-alive"]:
                resp_headers.pop(hop_header, None)
            
            return GatewayResponse(
                status_code=response.status_code,
                headers=resp_headers,
                body=resp_body,
                response_time_ms=response_time_ms,
                from_service=selected_service.name,
            )
            
        except Exception as e:
            self.stats["failed_requests"] += 1
            logger.exception("Gateway forward error")
            return GatewayResponse(
                status_code=500,
                body=json.dumps({
                    "error": "Internal gateway error",
                    "detail": str(e) if logger.isEnabledFor(logging.DEBUG) else "Check logs",
                }).encode(),
                response_time_ms=(time.time() - start_time) * 1000,
                error=str(e),
            )
    
    # ========== 健康检查循环 ==========
    
    async def _health_check_loop(self, interval: float = 15.0):
        """定期健康检查所有已注册服务."""
        while True:
            try:
                await self._check_all_services_health()
            except Exception as e:
                logger.error("Health check loop error: %s", e)
            
            await asyncio.sleep(interval)
    
    async def _check_all_services_health(self):
        """检查所有服务健康状态."""
        tasks = []
        for name, service in self._local_services.items():
            if service.health_url:
                tasks.append((name, self._check_single_service(service)))
        
        for name, task in tasks:
            try:
                is_healthy = await asyncio.wait_for(task, timeout=10.0)
                was_healthy = service.is_healthy
                service.is_healthy = is_healthy
                service.last_health_check = datetime.utcnow()
                
                if was_healthy and not is_healthy:
                    logger.warning("Service became UNHEALTHY: %s", name)
                elif not was_healthy and is_healthy:
                    logger.info("Service recovered HEALTHY: %s", name)
                    
            except Exception as e:
                logger.debug("Health check exception for %s: %s", name, e)
                service.is_healthy = False
    
    async def _check_single_service(self, service: BackendService) -> bool:
        """检查单个服务的健康状态."""
        try:
            if not self._http_client:
                return True
            
            resp = await self._http_client.get(
                service.health_url,
                timeout=5.0,
            )
            return resp.status_code == 200
            
        except Exception:
            return False
    
    # ========== 批量注册预设服务 ==========
    
    async def setup_preset_services(self):
        """
        注册预设的多语言后端服务.
        
        这些是推荐的微服务拆分方案:
          - Java: 调度算法服务
          - .NET: 工业协议适配
          - Go: 高频车辆数据采集
          - Node.js: 通知推送服务
        """
        preset_services = [
            BackendService(
                name="agvtms-scheduler-java",
                host="agvtms-scheduler-java",
                port=8080,
                protocol=ServiceProtocol.HTTP,
                version="1.0.0",
                tags=["java", "spring-boot", "scheduler", "critical"],
                weight=150,  # 高优先级
                timeout_seconds=60.0,  # 调度可能耗时较长
            ),
            BackendService(
                name="agvtms-opcua-dotnet",
                host="agvtms-opcua-dotnet",
                port=5000,
                protocol=ServiceProtocol.HTTP,
                version="1.0.0",
                tags=["dotnet", "aspnet-core", "opcua", "industrial"],
                weight=120,
                timeout_seconds=30.0,
            ),
            BackendService(
                name="agvtms-vehicle-go",
                host="agvtms-vehicle-go",
                port=9000,
                protocol=ServiceProtocol.GRPC,
                version="1.0.0",
                tags=["go", "grpc", "vehicle", "telemetry"],
                weight=100,
                timeout_seconds=10.0,  # 高频低延迟要求
                max_retries=1,
            ),
            BackendService(
                name="agvtms-notification-nodejs",
                host="agvtms-notification-nodejs",
                port=3001,
                protocol=ServiceProtocol.HTTP,
                version="1.0.0",
                tags=["nodejs", "notification", "email", "dingtalk"],
                weight=80,
                timeout_seconds=15.0,
            ),
        ]
        
        for service in preset_services:
            await self.register_service(service)
        
        logger.info("Registered %d preset multi-language services", len(preset_services))
    
    # ========== 状态与统计 ==========
    
    def get_status(self) -> Dict[str, Any]:
        """获取 Gateway 整体状态."""
        return {
            "initialized": self._initialized,
            "registered_services": len(self.registry._local_services),
            "active_routes": len([r for r in self.routes if r.enabled]),
            "load_balance_strategy": self.load_balancer.strategy.value,
            "stats": dict(self.stats),
            "circuit_breakers": {
                name: cb.state_info
                for name, cb in self.circuit_breakers.items()
            },
            "routes": [
                {"prefix": r.path_prefix, "target": r.target_service, "enabled": r.enabled}
                for r in sorted(self.routes, key=lambda x: x.priority)
            ],
        }
    
    def get_service_details(self, name: str = None) -> List[Dict[str, Any]]:
        """获取服务详情列表."""
        return self.registry.list_all_services()


# ==================== FastAPI 中间件集成 ====================


class GatewayMiddleware:
    """
    FastAPI 中间件 — 将 HTTP 请求转发到多语言后端.
    
    使用方式:
      app.add_middleware(
          GatewayMiddleware,
          gateway=gateway,
          exclude_paths=["/docs", "/openapi.json", "/healthz", "/metrics"],
      )
    """
    
    def __init__(
        self,
        app,
        gateway: MultiLangGateway,
        exclude_paths: List[str] = None,
    ):
        self.app = app
        self.gateway = gateway
        self.exclude_paths = set(exclude_paths or [
            "/docs", "/redoc", "/openapi.json", "/healthz", "/metrics",
            "/api/v1/",  # V1 API 本地处理
        ])
    
    async def __call__(self, scope, receive, send):
        """ASGI 接口."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # 解析请求
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        
        # 检查是否需要跳过网关
        for exclude in self.exclude_paths:
            if path.startswith(exclude):
                await self.app(scope, receive, send)
                return
        
        # 检查是否有匹配的路由
        match = self.gateway.match_route(path, method)
        if not match:
            await self.app(scope, receive, send)
            return
        
        # 通过网关转发
        # 读取完整请求体
        body_parts = []
        async for chunk in receive():
            if chunk.get("type") == "http.request":
                body_parts.append(chunk.get("body", b""))
        
        full_body = b"".join(body_parts) if body_parts else None
        
        # 构建请求上下文
        query_string = scope.get("query_string", b"").decode()
        query_params = {}
        if query_string:
            import urllib.parse
            query_params = dict(urllib.parse.parse_qsl(query_string))
        
        headers = {}
        for key, value in scope.get("headers", []):
            headers[key.decode()] = value.decode()
        
        request = GatewayRequest(
            request_id=headers.get("X-Request-ID", str(uuid.uuid4())),
            method=method,
            path=path,
            query_params=query_params,
            headers=headers,
            body=full_body,
            client_ip=headers.get("X-Real-IP", headers.get("X-Forwarded-For", "unknown")),
        )
        
        # 转发请求
        response = await self.gateway.forward(request)
        
        # 发送响应
        await send({
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [
                (k.encode(), v.encode()) for k, v in response.headers.items()
            ] + [
                (b"X-Gateway-Service", response.from_service.encode()),
                (b"X-Response-Time", f"{response.response_time_ms:.1f}ms".encode()),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": response.body,
        })


# ==================== FastAPI Router (替代中间件方式) ====================


def create_gateway_router(gateway: MultiLangGateway):
    """
    创建网关路由 (作为 catch-all 路由).
    
    比中间件更灵活，可精确控制哪些路径走网关.
    
    使用:
      from fastapi import APIRouter, Request
      
      gateway_router = create_gateway_router(gateway)
      app.include_router(gateway_router)  # 放在最后 (最低优先级)
    """
    from fastapi import APIRouter, Request, Response
    from fastapi.responses import JSONResponse
    
    router = APIRouter(tags=["gateway"])
    
    @router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    async def gateway_catch_all(full_path: str, request: Request):
        """捕获所有未匹配的请求并转发到对应后端服务."""
        
        path = f"/{full_path}" if full_path else "/"
        
        # 检查路由匹配
        match = gateway.match_route(path, request.method)
        if not match:
            return JSONResponse(
                status_code=404,
                content={"error": "Not Found", "path": path},
            )
        
        # 构建请求
        body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
        
        gw_request = GatewayRequest(
            request_id=request.headers.get("X-Request-ID", str(uuid.uuid4())),
            method=request.method,
            path=path,
            query_params=dict(request.query_params),
            headers=dict(request.headers),
            body=body,
            client_ip=request.client.host if request.client else "unknown",
        )
        
        # 转发
        gw_response = await gateway.forward(gw_request)
        
        # 构建FastAPI响应
        return Response(
            content=gw_response.body,
            status_code=gw_response.status_code,
            headers={
                **gw_response.headers,
                "X-Gateway-Service": gw_response.from_service,
                "X-Response-Time": f"{gw_response.response_time_ms:.1f}ms",
            },
        )
    
    @router.get("/gateway/status")
    async def gateway_status():
        """获取网关状态和统计信息."""
        return gateway.get_status()
    
    @router.get("/gateway/services")
    async def gateway_list_services():
        """列出所有已注册的后端服务."""
        return await gateway.registry.list_all_services()
    
    return router


# ==================== 全局单例 ====================

# 全局 Gateway 实例 (在 main.py 中初始化)
gateway = MultiLangGateway()


async def get_gateway() -> MultiLangGateway:
    """依赖注入: 获取 Gateway 实例."""
    return gateway
