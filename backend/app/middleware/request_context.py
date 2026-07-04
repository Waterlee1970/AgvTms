"""
请求上下文中间件 - 对标企业级API网关实践

功能:
1. 自动生成request_id (用于链路追踪)
2. 记录请求耗时 (性能监控)
3. 绑定用户/租户上下文到contextvar (AsyncIO安全)
4. 结构化日志输出 (对标Spring Cloud Sleuth)

对标参考:
- Spring Cloud Sleuth (TraceId/SpanId)
- OpenTelemetry Python SDK
- AWS X-Ray
- 海康RCS请求日志规范
"""

import time
import uuid
import logging
from contextvars import ContextVar
from typing import Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

# ==================== Context Variables (AsyncIO安全的线程本地存储) ====================

request_id_ctx: ContextVar[str] = ContextVar('request_id', default='')
user_id_ctx: ContextVar[str] = ContextVar('user_id', default='')
tenant_id_ctx: ContextVar[str] = ContextVar('tenant_id', default='default')
client_ip_ctx: ContextVar[str] = ContextVar('client_ip', default='')

logger = logging.getLogger("agvtms.middleware.request_context")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    请求上下文中间件
    
    处理流程:
    1. 从Header提取或生成 request_id
    2. 解析用户身份信息
    3. 记录请求开始日志
    4. 调用后续处理链
    5. 记录响应头和完成日志
    6. 异常时记录详细错误
    
    使用方式 (在main.py中):
        app.add_middleware(RequestContextMiddleware)
    
    获取当前请求信息 (在其他模块中):
        from app.middleware.request_context import get_request_id, get_user_id
        req_id = get_request_id()
    """
    
    # 不需要追踪的路径前缀 (静态资源等)
    SKIP_PATH_PREFIXES = (
        '/_static',
        '/docs',
        '/redoc',
        '/openapi.json',
        '/favicon.ico',
        '/metrics',
    )
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        url = request.url.path
        
        # 静态路径跳过处理
        if any(url.startswith(prefix) for prefix in self.SKIP_PATH_PREFIXES):
            return await call_next(request)
        
        # ==================== 1. 请求ID生成/传播 ====================
        # 支持从Header传播 (微服务场景) 或自动生成
        req_id = request.headers.get(
            "X-Request-ID"
        ) or request.headers.get(
            "X-Trace-Id"  # OpenTelemetry兼容
        ) or uuid.uuid4().hex[:12]
        
        request_id_ctx.set(req_id)
        
        # ==================== 2. 用户上下文解析 ====================
        # 从JWT Token或自定义Header获取 (具体解析逻辑由Auth中间件负责)
        user_id = request.headers.get("X-User-ID", "")
        tenant_id = request.headers.get("X-Tenant-ID", "default")
        
        user_id_ctx.set(user_id)
        tenant_id_ctx.set(tenant_id)
        
        # 客户端IP (考虑代理场景)
        client_ip = request.headers.get(
            "X-Forwarded-For",
            request.client.host if request.client else "unknown"
        ).split(',')[0].strip()
        client_ip_ctx.set(client_ip)
        
        # ==================== 3. 计时开始 ====================
        start_time = time.perf_counter()
        
        # ==================== 4. 入口日志 (对标SLF4J MDC) ====================
        logger.info(
            "REQUEST_START",
            extra={
                "method": request.method,
                "path": url,
                "query_string": str(request.query_params),
                "client_ip": client_ip,
                "user_agent": request.headers.get("user-agent", "")[:200],
                "content_type": request.headers.get("content-type", ""),
                "content_length": request.headers.get("content-length", ""),
                "request_id": req_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
            }
        )
        
        # ==================== 5. 调用后续处理链 ====================
        try:
            response = await call_next(request)
            
            # 计算耗时
            duration = time.perf_counter() - start_time
            duration_ms = round(duration * 1000, 2)
            
            # ==================== 6. 注入响应头 ====================
            response.headers["X-Request-ID"] = req_id
            response.headers["X-Response-Time-ms"] = str(duration_ms)
            response.headers["X-Server-Timestamp"] = str(time.time())
            
            # ==================== 7. 完成日志 ====================
            log_level = logging.INFO if response.status_code < 400 else logging.WARNING
            logger.log(
                log_level,
                "REQUEST_END",
                extra={
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "response_size": response.headers.get("content-length", "unknown"),
                    "request_id": req_id,
                }
            )
            
            return response
            
        except Exception as exc:
            # ==================== 8. 异常处理与日志 ====================
            duration = time.perf_counter() - start_time
            duration_ms = round(duration * 1000, 2)
            
            logger.error(
                "REQUEST_ERROR",
                extra={
                    "error_class": type(exc).__name__,
                    "error_message": str(exc)[:500],
                    "duration_ms": duration_ms,
                    "request_id": req_id,
                    "method": request.method,
                    "path": url,
                },
                exc_info=True,  # 输出完整堆栈
            )
            
            # 返回标准错误响应 (避免泄露内部细节)
            return JSONResponse(
                status_code=500,
                content={
                    "code": "500",
                    "message": "服务器内部错误",
                    "data": None,
                    "trace_id": req_id,
                    "timestamp": time.time(),
                }
            )


# ==================== 辅助函数 (供其他模块使用) ====================

def get_request_id() -> str:
    """
    获取当前请求ID (用于日志/数据库记录/异常报告)
    
    用法示例:
        from app.middleware.request_context import get_request_id
        
        def some_service_function():
            req_id = get_request_id()
            logger.info("Processing...", extra={"request_id": req_id})
            db.execute("INSERT INTO logs (request_id) VALUES (%s)", (req_id,))
    """
    return request_id_ctx.get()


def get_user_id() -> str:
    """获取当前用户ID (用于审计/权限检查)"""
    return user_id_ctx.get()


def get_tenant_id() -> str:
    """获取当前租户ID (多租户数据隔离)"""
    return tenant_id_ctx.get()


def get_client_ip() -> str:
    """获取客户端真实IP地址"""
    return client_ip_ctx.get()


def set_request_context(
    request_id: str = None,
    user_id: str = None,
    tenant_id: str = None,
):
    """
    手动设置请求上下文 (主要用于测试或异步任务继承)
    
    场景示例:
        # 在Kafka Consumer中继承上游请求上下文
        async def handle_message(message):
            set_request_context(
                request_id=message.value.get('request_id'),
                user_id=message.value.get('user_id'),
            )
            # 后续日志会自动携带这些信息
    """
    if request_id is not None:
        request_id_ctx.set(request_id)
    if user_id is not None:
        user_id_ctx.set(user_id)
    if tenant_id is not None:
        tenant_id_ctx.set(tenant_id)


class RequestContext:
    """请求上下文管理器 (用于with语法)"""
    
    def __init__(
        self,
        request_id: str = None,
        user_id: str = None,
        tenant_id: str = None,
    ):
        self.request_id = request_id or uuid.uuid4().hex[:12]
        self.user_id = user_id
        self.tenant_id = tenant_id
        self._token_req = None
        self._token_user = None
        self._token_tenant = None
    
    def __enter__(self):
        self._token_req = request_id_ctx.set(self.request_id)
        self._token_user = user_id_ctx.set(self.user_id or '')
        self._token_tenant = tenant_id_ctx.set(self.tenant_id or 'default')
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 恢复之前的值
        if self._token_req:
            request_id_ctx.reset(self._token_req)
        if self._token_user:
            user_id_ctx.reset(self._token_user)
        if self._token_tenant:
            tenant_id_ctx.reset(self._token_tenant)
        return False  # 不抑制异常
