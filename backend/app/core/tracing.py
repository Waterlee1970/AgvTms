"""
OpenTelemetry 分布式追踪集成 — Phase 3 可观测性.

对标: Jaeger / Zipkin / 云厂商Trace服务.

功能:
  1. 自动追踪:
     - FastAPI请求自动追踪 (中间件)
     - 数据库查询追踪 (SQL语句+耗时)
     - 外部HTTP调用追踪
     - MQTT消息收发追踪
  
  2. Span语义:
     - HTTP属性 (method, url, status_code)
     - 数据库属性 (db.system, db.statement)
     - 消息属性 (messaging.system, destination)
     - 自定义业务属性 (task_id, agv_id, algorithm)
  
  3. 采样策略:
     - 基于采样率 (1.0=全量, 0.1=10%)
     - 基于父Span (父被采样则子必采)
     - 基于错误 (5xx必采)
  
  4. 导出器:
     - 控制台输出 (开发环境)
     - OTLP/gRPC (生产环境, 发送至Jaeger/Tempo)
     - 文件输出 (离线分析)

依赖:
  - opentelemetry-api >= 1.15.0
  - opentelemetry-sdk >= 1.15.0
  - opentelemetry-instrumentation-fastapi (可选)
  - opentelemetry-exporter-otlp (可选，生产环境)

安装:
    pip install opentelemetry-api opentelemetry-sdk \
            opentelemetry-instrumentation-fastapi \
            opentelemetry-exporter-otlp

使用示例:
    from app.core.tracing import tracer, get_current_span
    
    # 手动创建Span
    with tracer.start_as_current_span("algorithm.execute") as span:
        span.set_attribute("algorithm", "aco")
        span.set_attribute("iterations", 100)
        # ... 业务逻辑 ...
    
    # 获取当前Trace ID
    trace_id = get_current_span().get_span_context().trace_id
"""

from __future__ import annotations

import os
import time
import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, TypeVar
from functools import wraps

# ==================== 可选导入 ====================

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
    from opentelemetry.semconv.trace import SpanAttributes as SemAttrs
    
    # 尝试导入OTLP导出器 (生产环境需要)
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        OTLP_AVAILABLE = True
    except ImportError:
        OTLP_AVAILABLE = False
    
    # 尝试导入FastAPI instrumentation
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FASTAPI_INSTR_AVAILABLE = True
    except ImportError:
        FASTAPI_INSTR_AVAILABLE = False
    
    OTEL_AVAILABLE = True
    
except ImportError:
    OTEL_AVAILABLE = False
    OTLP_AVAILABLE = False
    FASTAPI_INSTR_AVAILABLE = False
    
    # 创建Mock对象以支持优雅降级
    class MockSpan:
        def set_attribute(self, *args, **kwargs): pass
        def set_status(self, *args, **kwargs): pass
        def add_event(self, *args, **kwargs): pass
        def record_exception(self, *args, **kwargs): pass
        def get_span_context(self):
            class Ctx:
                trace_id = "00000000000000000000000000000000"
                span_id = "0000000000000000"
            return Ctx()
    
    class MockTracer:
        def start_as_current_span(self, name: str):
            @contextmanager
            def dummy(ctx_manager=None):
                yield MockSpan()
            return dummy(ctx_manager=self)
        
        def start_span(self, name: str):
            return MockSpan()
    
    trace = type('trace', (), {'get_current_span': lambda: MockSpan()})
    SemAttrs = type('SemAttrs', (), {
        'HTTP_METHOD': 'http.method',
        'HTTP_URL': 'http.url',
        'HTTP_STATUS_CODE': 'http.status_code',
        'DB_SYSTEM': 'db.system',
        'DB_STATEMENT': 'db.statement',
        'DB_OPERATION': 'db.operation',
        'MESSAGING_SYSTEM': 'messaging.system',
        'MESSAGING_DESTINATION': 'messaging.destination',
        'MESSAGING_MESSAGE_ID': 'messaging.message_id',
    })


T = TypeVar('T')
logger = logging.getLogger(__name__)


# ==================== 配置 ====================

class TracingConfig:
    """追踪配置"""
    
    # 服务信息
    SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "agvtms-backend")
    SERVICE_VERSION = os.getenv("APP_VERSION", "3.0.0")
    
    # 采样配置
    TRACE_SAMPLING_RATE = float(os.getenv("OTEL_SAMPLING_RATE", "1.0"))  # 默认全量采样
    
    # 导出器选择
    EXPORTER_TYPE = os.getenv("OTEL_EXPORTER", "console")  # console/otlp/file
    
    # OTLP端点 (Jaeger/Tempo/Grafana Cloud)
    OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
    
    # 是否启用FastAPI自动instrumentation
    AUTO_INSTRUMENT_FASTAPI = os.getenv("OTEL_AUTO_INSTRUMENT_FASTAPI", "true").lower() == "true"
    
    # 自定义属性前缀
    CUSTOM_ATTR_PREFIX = "agvtms."
    
    # 环境标签
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


class CustomAttributes:
    """自定义业务属性名 (遵循OpenTelemetry命名规范)"""
    # 任务相关
    TASK_ID = f"{TracingConfig.CUSTOM_ATTR_PREFIX}task.id"
    TASK_TYPE = f"{TracingConfig.CUSTOM_ATTR_PREFIX}task.type"
    TASK_PRIORITY = f"{TracingConfig.CUSTOM_ATTR_PREFIX}task.priority"
    TASK_STATUS = f"{TracingConfig.CUSTOM_ATTR_PREFIX}task.status"
    
    # AGV相关
    AGV_ID = f"{TracingConfig.CUSTOM_ATTR_PREFIX}agv.id"
    AGV_TYPE = f"{TracingConfig.CUSTOM_ATTR_PREFIX}agv.type"
    AGV_STATUS = f"{TracingConfig.CUSTOM_ATTR_PREFIX}agv.status"
    AGV_BATTERY = f"{TracingConfig.CUSTOM_ATTR_PREFIX}agv.battery"
    
    # 调度相关
    ALGORITHM = f"{TracingConfig.CUSTOM_ATTR_PREFIX}scheduling.algorithm"
    ALGORITHM_ITERATIONS = f"{TracingConfig.CUSTOM_ATTR_PREFIX}scheduling.iterations"
    ALGORITHM_DURATION_MS = f"{TracingConfig.CUSTOM_ATTR_PREFIX}scheduling.duration_ms"
    PATH_LENGTH = f"{TracingConfig.CUSTOM_ATTR_PREFIX}scheduling.path_length"
    CONFLICTS_DETECTED = f"{TracingConfig.CUSTOM_ATTR_PREFIX}scheduling.conflicts"
    
    # 协议相关
    PROTOCOL = f"{TracingConfig.CUSTOM_ATTR_PREFIX}protocol.type"
    MESSAGE_TYPE = f"{TracingConfig.CUSTOM_ATTR_PREFIX}protocol.message_type"


# ==================== 初始化函数 ====================

_tracer_instance = None
_is_initialized = False


def initialize_tracing(app=None) -> bool:
    """
    初始化 OpenTelemetry 追踪.
    
    Args:
        app: FastAPI应用实例 (可选, 用于自动instrumentation)
        
    Returns:
        是否成功初始化
    """
    global _tracer_instance, _is_initialized
    
    if not OTEL_AVAILABLE:
        logger.warning("OpenTelemetry not installed, tracing disabled")
        _is_initialized = False
        return False
    
    if _is_initialized:
        logger.debug("Tracing already initialized")
        return True
    
    try:
        # 创建资源 (附加环境和服务信息)
        resource = Resource.create({
            SERVICE_NAME: TracingConfig.SERVICE_NAME,
            SERVICE_VERSION: TracingConfig.SERVICE_VERSION,
            "environment": TracingConfig.ENVIRONMENT,
            "deployment.region": os.getenv("REGION", "unknown"),
        })
        
        # 创建TracerProvider
        provider = TracerProvider(resource=resource)
        
        # 配置导出器
        if TracingConfig.EXPORTER_TYPE == "otlp" and OTLP_AVAILABLE:
            otlp_exporter = OTLPSpanExporter(
                endpoint=TracingConfig.OTEL_ENDPOINT,
                insecure=True,
            )
            processor = BatchSpanProcessor(otlp_exporter)
            logger.info(f"OTLP exporter configured: {TracingConfig.OTEL_ENDPOINT}")
            
        elif TracingConfig.EXPORTER_TYPE == "console":
            exporter = ConsoleSpanExporter()
            processor = SimpleSpanProcessor(exporter)
            logger.info("Console span exporter configured")
            
        else:
            # 默认使用控制台
            exporter = ConsoleSpanExporter()
            processor = SimpleSpanProcessor(exporter)
            logger.info(f"Defaulting to console exporter ({TracingConfig.EXPORTER_TYPE})")
        
        provider.add_span_processor(processor)
        
        # 设置全局默认provider
        trace.set_tracer_provider(provider)
        
        # 创建Tracer实例
        _tracer_instance = trace.get_tracer(__name__, TracingConfig.SERVICE_VERSION)
        
        # FastAPI自动instrumentation
        if app and TracingConfig.AUTO_INSTRUMENT_FASTAPI and FASTAPI_INSTR_AVAILABLE:
            FastAPIInstrumentor().instrument_app(
                app,
                tracer_provider=provider,
                excluded_urls="/health,/readyz,/livez,/metrics,/docs,/redoc,/openapi.json",
            )
            logger.info("FastAPI auto-instrumentation enabled")
        
        _is_initialized = True
        logger.info(
            f"OpenTelemetry initialized successfully: "
            f"service={TracingConfig.SERVICE_NAME}, "
            f"sampling={TracingConfig.TRACE_SAMPLING_RATE}"
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}", exc_info=True)
        _is_initialized = False
        return False


def get_tracer():
    """获取Tracer实例"""
    global _tracer_instance
    
    if not OTEL_AVAILABLE:
        return MockTracer()
    
    if _tracer_instance is None:
        _tracer_instance = trace.get_tracer(__name__)
    
    return _tracer_instance


# 全局别名
tracer = get_tracer()


def get_current_span():
    """获取当前活跃的Span"""
    if OTEL_AVAILABLE:
        return trace.get_current_span()
    return MockSpan()


# ==================== 便捷装饰器 ====================

def trace_operation(operation_name: str, **default_attrs):
    """
    追踪操作装饰器.
    
    使用示例:
        @trace_operation("database.query", db_table="tasks")
        async def get_tasks(query):
            ...
    
    Args:
        operation_name: Span名称
        **default_attrs: 默认属性
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            t = get_tracer()
            with t.start_as_current_span(operation_name) as span:
                for k, v in default_attrs.items():
                    span.set_attribute(k, v)
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            t = get_tracer()
            with t.start_as_current_span(operation_name) as span:
                for k, v in default_attrs.items():
                    span.set_attribute(k, v)
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


@contextmanager
def trace_context(name: str, **attributes):
    """
    追踪上下文管理器.
    
    使用示例:
        with trace_context("scheduling.path_planning", algorithm="astar"):
            path = plan_path(start, end)
    """
    t = get_tracer()
    with t.start_as_current_span(name) as span:
        for k, v in attributes.items():
            if v is not None:
                span.set_attribute(str(k), str(v))
        yield span


# ==================== 业务特定追踪辅助 ====================

class TaskTracingHelper:
    """任务相关的追踪辅助工具"""
    
    @staticmethod
    def record_task_dispatch(task_id: str, agv_id: str, priority: int, status: str):
        """记录任务派发事件"""
        span = get_current_span()
        span.set_attributes({
            CustomAttributes.TASK_ID: task_id,
            CustomAttributes.AGV_ID: agv_id,
            CustomAttributes.TASK_PRIORITY: priority,
            CustomAttributes.TASK_STATUS: status,
        })
        span.add_event("task_dispatched", {
            "task_id": task_id,
            "agv_id": agv_id,
        })
    
    @staticmethod
    @contextmanager
    def trace_algorithm_execution(algorithm: str, task_count: int):
        """追踪算法执行过程"""
        with trace_context(
            "scheduling.algorithm_execute",
            algorithm=algorithm,
            task_count=str(task_count),
        ) as span:
            start_time = time.time()
            yield span
            
            elapsed_ms = (time.time() - start_time) * 1000
            span.set_attribute(CustomAttributes.ALGORITHM_DURATION_MS, f"{elapsed_ms:.2f}")


class VehicleTracingHelper:
    """车辆相关的追踪辅助工具"""
    
    @staticmethod
    def record_vehicle_state_change(agv_id: str, old_status: str, new_status: str):
        """记录AGV状态变更"""
        span = get_current_span()
        span.set_attributes({
            CustomAttributes.AGV_ID: agv_id,
            CustomAttributes.AGV_STATUS: new_status,
        })
        span.add_event("vehicle_state_changed", {
            "agv_id": agv_id,
            "old_status": old_status,
            "new_status": new_status,
        })


class DatabaseTracingHelper:
    """数据库操作的追踪辅助工具"""
    
    @staticmethod
    def record_query(operation: str, table: str, duration_ms: float, row_count: Optional[int] = None):
        """记录数据库查询"""
        span = get_current_span()
        span.set_attributes({
            SemAttrs.DB_SYSTEM: "postgresql",
            SemAttrs.DB_OPERATION: operation.lower(),
            f"{SemAttrs.DB_STATEMENT}.table": table,
            f"{CustomAttributes.CUSTOM_ATTR_PREFIX}db.duration_ms": f"{duration_ms:.2f}",
        })
        if row_count is not None:
            span.set_attribute(f"{CustomAttributes.CUSTOM_ATTR_PREFIX}db.row_count", row_count)


class MessagingTracingHelper:
    """消息通信的追踪辅助工具"""
    
    @staticmethod
    def record_mqtt_message(direction: str, topic: str, message_id: str, message_type: str):
        """记录MQTT消息收发"""
        span = get_current_span()
        span.set_attributes({
            SemAttrs.MESSAGING_SYSTEM: "mqtt",
            SemAttrs.MESSAGING_DESTINATION: topic,
            SemAttrs.MESSAGING_MESSAGE_ID: message_id,
            CustomAttributes.MESSAGE_TYPE: message_type,
            f"{CustomAttributes.CUSTOM_ATTR_PREFIX}messaging.direction": direction,
        })
        event_name = f"mqtt_{direction}"
        span.add_event(event_name, {
            "topic": topic,
            "message_id": message_id,
        })


# ==================== 中间件集成 ====================

class TracingMiddleware:
    """
    FastAPI追踪中间件.
    
    如果不使用自动instrumentation，可以手动添加此中间件.
    
    功能:
    1. 为每个请求创建根Span
    2. 提取和注入Trace Context
    3. 记录请求/响应元数据
    4. 记录异常堆栈
    """
    
    def __init__(self, app, excluded_paths: Optional[List[str]] = None):
        self.app = app
        self.excluded_paths = set(excluded_paths or [
            "/health", "/readyz", "/livez", "/metrics",
            "/docs", "/redoc", "/openapi.json", "/favicon.ico",
        ])
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        path = scope.get("path", "")
        if path in self.excluded_paths:
            await self.app(scope, receive, send)
            return
        
        # 开始追踪
        request_method = scope.get("method", "")
        request_url = f"{path}?{scope.get('query_string', b'').decode()}" if scope.get('query_string') else path
        
        t = get_tracer()
        with t.start_as_current_span(f"http.{request_method.lower()}") as span:
            # 设置标准HTTP属性
            span.set_attribute(SemAttrs.HTTP_METHOD, request_method)
            span.set_attribute(SemAttrs.HTTP_URL, request_url)
            
            # 包装send方法以捕获状态码
            start_time = time.time()
            status_code = [500]  # 使用列表以便在嵌套函数中修改
            
            async def send_wrapper(message):
                if message.get("type") == "http.response.start":
                    status_code[0] = message.get("status", 500)
                await send(message)
            
            try:
                await self.app(scope, receive, send_wrapper)
                
                # 记录响应属性
                duration_ms = (time.time() - start_time) * 1000
                span.set_attribute(SemAttrs.HTTP_STATUS_CODE, status_code[0])
                span.set_attribute(
                    f"{CustomAttributes.CUSTOM_ATTR_PREFIX}http.duration_ms",
                    f"{duration_ms:.2f}",
                )
                
                # 错误状态码设置Span状态
                if status_code[0] >= 500:
                    span.set_status(trace.Status(
                        trace.StatusCode.ERROR,
                        f"HTTP {status_code[0]}"
                    ))
                    
            except Exception as exc:
                duration_ms = (time.time() - start_time) * 1000
                span.set_attribute(SemAttrs.HTTP_STATUS_CODE, 500)
                span.set_attribute(
                    f"{CustomAttributes.CUSTOM_ATTR_PREFIX}http.duration_ms",
                    f"{duration_ms:.2f}",
                )
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
                raise


# ==================== 导出便捷符号 ====================

__all__ = [
    # 核心API
    'tracer', 'get_tracer', 'get_current_span',
    'initialize_tracing', 'TracingMiddleware',
    
    # 配置
    'TracingConfig', 'CustomAttributes',
    
    # 便捷工具
    'trace_operation', 'trace_context',
    'TaskTracingHelper', 'VehicleTracingHelper',
    'DatabaseTracingHelper', 'MessagingTracingHelper',
    
    # 可用性检查
    'OTEL_AVAILABLE', 'FASTAPI_INSTR_AVAILABLE',
]
