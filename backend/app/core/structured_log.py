"""
结构化日志系统 — 替换所有 print/logging 为 structlog.

特性:
  1. JSON结构化输出 (生产环境) / 可读彩色 (开发环境)
  2. 自动绑定: request_id, agv_id, task_id, trace_id
  3. 性能指标: 内置计时器、调用计数
  4. 日志采样: 高频操作自动降频
  5. 敏感数据脱敏

对标: 极智嘉RMS ELK日志体系 + 海康RCS Splunk集成

使用:
    from app.core.structured_log import get_logger
    log = get_logger("module_name")
    log.info("task_dispatched", task_id="T-001", agv_id="AGV-01", priority="high")
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar


# ==================== 全局上下文 ====================

# 上下文变量 — 每个请求/任务自动绑定
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_agv_id_ctx: ContextVar[str] = ContextVar("agv_id", default="")
_task_id_ctx: ContextVar[str] = ContextVar("task_id", default="")
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def set_context(
    request_id: str = "",
    agv_id: str = "",
    task_id: str = "",
    trace_id: str = "",
):
    """设置当前上下文字段."""
    for var, val in [
        (_request_id_ctx, request_id or ""),
        (_agv_id_ctx, agv_id or ""),
        (_task_id_ctx, task_id or ""),
        (_trace_id_ctx, trace_id or uuid.uuid4().hex[:16]),
    ]:
        if val:
            var.set(val)


def clear_context():
    """清除上下文 (请求结束时调用)."""
    try:
        _request_id_ctx.set("")
        _agv_id_ctx.set("")
        _task_id_ctx.set("")
        _trace_id_ctx.set("")
    except Exception:
        pass


# ==================== 敏感数据脱敏 ====================

SENSITIVE_FIELDS = {
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth_token", "access_token", "refresh_token",
    "card_number", "credit_card", "ssn", "social_security",
}


def _mask_sensitive(value: Any, key: str = "") -> Any:
    """脱敏敏感字段."""
    if isinstance(value, str):
        lower_key = key.lower().replace("-", "_")
        # 检查是否是敏感字段
        for sf in SENSITIVE_FIELDS:
            if sf in lower_key:
                return f"{value[:3]}{'*' * max(0, len(value) - 6)}{value[-3:]}" if len(value) > 6 else "***"
        return value
    elif isinstance(value, dict):
        return {k: _mask_sensitive(v, k) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [_mask_sensitive(v) for v in value]
    return value


def _sanitize_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """清理所有kwargs中的敏感数据."""
    result = {}
    for k, v in kwargs.items():
        result[k] = _mask_sensitive(v, k)
    return result


# ==================== 日志采样器 ====================

class LogSampler:
    """
    高频日志采样器.
    
    当同一消息在短时间内重复出现时，自动降频记录，
    避免日志洪流淹没磁盘IO。
    
    原理: 固定窗口内相同level+message只记录N次，超出部分计数汇总。
    
    使用场景:
      - AGV心跳状态 (每秒一次 → 每10秒汇总一次)
      - Kafka消息处理 (每秒100条 → 每5秒汇总)
    """

    def __init__(
        self,
        window_seconds: float = 10.0,
        max_per_window: int = 3,
    ):
        self._window = window_seconds
        self._max = max_per_window
        self._counts: Dict[tuple, int] = {}
        self._last_emit: Dict[tuple, float] = {}
        self._lock = threading.Lock()

    def should_log(self, level: str, msg: str) -> tuple[bool, int]:
        """返回 (是否应该记录, 当前窗口内累计次数)."""
        key = (level, msg[:80])  # 截断避免内存膨胀
        now = time.monotonic()

        with self._lock:
            count = self._counts.get(key, 0)
            
            # 窗口过期，重置
            last = self._last_emit.get(key, 0)
            if now - last > self._window:
                self._counts[key] = 1
                self._last_emit[key] = now
                return True, 1
            
            count += 1
            self._counts[key] = count
            
            if count <= self._max:
                self._last_emit[key] = now
                return True, count
            
            return False, count
    
    def summary(self, level: str, msg: str, total_count: int) -> str:
        """生成降频汇总消息."""
        return (
            f"[SAMPLED] {msg} "
            f"(suppressed {total_count - self._max} additional "
            f"occurrences in last {self._window:.0f}s)"
        )


# 全局采样器实例
_default_sampler = LogSampler(window_seconds=10.0, max_per_window=3)


# ==================== StructuredLogger ====================

class StructuredLogger:
    """
    结构化日志器 — 核心实现.
    
    特性:
      - JSON输出 (LOG_FORMAT=json) 或 彩色可读 (开发环境)
      - 自动绑定上下文 (request_id, agv_id, task_id, trace_id)
      - 内置计时装饰器 @log.timing()
      - 异常自动捕获 + 完整堆栈
      - 日志采样防洪水
      - 性能指标内置 (slow_query > 1s 自动warning)
    
    输出格式 (JSON模式):
        {
            "ts": "2025-06-19T22:30:00.000Z",
            "level": "INFO",
            "logger": "dispatch_service",
            "message": "task_dispatched",
            "request_id": "req-abc123",
            "agv_id": "AGV-01",
            "task_id": "T-001",
            "priority": "high",
            "elapsed_ms": 12.5,
            "host": "agvtms-prod-01"
        }
    """

    LEVELS = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }

    # ANSI颜色代码 (终端彩色输出)
    _COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    _RESET = "\033[0m"

    def __init__(self, name: str, sampler: Optional[LogSampler] = None):
        self.name = name
        self._sampler = sampler or _default_sampler
        self._json_mode = os.getenv("LOG_FORMAT", "").lower() == "json"
        self._hostname = os.getenv("HOSTNAME", "")
        
        # 绑定标准logging (用于框架兼容)
        self._std_logger = logging.getLogger(name)

    def _build_base_dict(self, level: str, message: str, **kwargs) -> Dict[str, Any]:
        """构建基础日志字典."""
        d = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": level,
            "logger": self.name,
            "message": message,
        }
        
        # 注入上下文字段
        if req_id := _request_id_ctx.get(None):
            d["request_id"] = req_id
        if agv := _agv_id_ctx.get(None):
            d["agv_id"] = agv
        if tid := _task_id_ctx.get(None):
            d["task_id"] = tid
        if trid := _trace_id_ctx.get(None):
            d["trace_id"] = trid
        
        # 主机名
        if self._hostname:
            d["host"] = self._hostname
        
        # 用户字段 (已脱敏)
        sanitized = _sanitize_kwargs(kwargs)
        d.update(sanitized)
        
        return d

    def _format_json(self, data: Dict[str, Any]) -> str:
        """格式化为JSON."""
        return json.dumps(data, ensure_ascii=False, default=str)

    def _format_human(self, level: str, data: Dict[str, Any]) -> str:
        """格式化为人类可读彩色文本."""
        color = self._COLORS.get(level, "")
        reset = self._RESET
        
        # 基础行
        ts = data["ts"].replace("+00:00", "Z")
        parts = [
            f"{color}{level:>8}{reset}",
            ts[11:23],  # HH:MM:ss.mmm
            f"[{data['logger']}]",
            data["message"],
        ]
        
        # 附加字段
        extra_keys = [k for k in data 
                      if k not in ("ts", "level", "logger", "message", "host")]
        if extra_keys:
            extra_str = ", ".join(f"{k}={v}" for k, v in data.items() if k in extra_keys)
            parts.append(f" | {extra_str}")
        
        return " ".join(parts)

    def _emit(self, level: str, message: str, sample: bool = True, **kwargs):
        """核心发射方法."""
        # 日志采样检查
        if sample and os.getenv("LOG_SAMPLING", "1") == "1":
            should, count = self._sampler.should_log(level, message)
            if not should:
                # 输出汇总
                if count == self._sampler._max + 1:
                    summary_msg = self._sampler.summary(level, message, count)
                    self._do_emit(level, summary_msg, **kwargs)
                return
        
        self._do_emit(level, message, **kwargs)

    def _do_emit(self, level: str, message: str, **kwargs):
        """实际写入."""
        data = self._build_base_dict(level, message, **kwargs)
        
        if self._json_mode:
            output = self._format_json(data)
        else:
            output = self._format_human(level, data)
        
        # 同时写stdout和标准logging
        print(output, flush=True)
        
        # 同步到标准logging (保持兼容) - 只传递extra参数
        std_level = getattr(logging, level.upper(), logging.INFO)
        # 过滤掉logging不支持的内置关键字
        safe_kwargs = {k: v for k, v in _sanitize_kwargs(kwargs).items() 
                       if k not in ('level', 'json_mode', 'sampling_enabled', 'log_level', 
                                     'sample', 'ts', 'logger', 'host', 'message')}
        self._std_logger.log(std_level, message, extra=safe_kwargs if safe_kwargs else None)

    def debug(self, message: str, **kwargs): self._emit("DEBUG", message, **kwargs)
    def info(self, message: str, **kwargs): self._emit("INFO", message, **kwargs)
    def warning(self, message: str, **kwargs): self._emit("WARNING", message, **kwargs)
    def error(self, message: str, **kwargs): self._emit("ERROR", message, **kwargs)
    def critical(self, message: str, **kwargs): self._emit("CRITICAL", message, **kwargs)

    def exception(self, message: str, exc: Optional[Exception] = None, **kwargs):
        """记录异常 (自动包含堆栈)."""
        import traceback
        tb_text = "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__
        )) if exc else traceback.format_exc()
        
        kwargs["exception"] = tb_text.strip().split("\n")
        kwargs["exception_type"] = type(exc).__name__ if exc else "Unknown"
        self._emit("ERROR", message, **kwargs)

    # ---- 计时装饰器 ----

    F = TypeVar("F", bound=Callable[..., Any])

    def timing(self, slow_threshold_ms: float = 1000.0, label: str = ""):
        """
        函数计时装饰器.
        
        用法:
            log = get_logger("my_module")
            
            @log.timing(slow_threshold_ms=500)
            def expensive_operation(x):
                ...
                
            # 结果: 自动记录 function_name, elapsed_ms
            #       如果超过阈值自动升级为 WARNING
        """
        def decorator(func: F) -> F:
            func_name = label or func.__name__

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    elapsed = (time.perf_counter() - t0) * 1000
                    self.error(
                        f"{func_name}_failed",
                        elapsed_ms=round(elapsed, 2),
                        error=str(e),
                    )
                    raise
                finally:
                    elapsed = (time.perf_counter() - t0) * 1000
                    lvl = "WARNING" if elapsed > slow_threshold_ms else "DEBUG"
                    self._do_emit(lvl, f"{func_name}_completed", elapsed_ms=round(elapsed, 2))

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    elapsed = (time.perf_counter() - t0) * 1000
                    self.error(
                        f"{func_name}_failed",
                        elapsed_ms=round(elapsed, 2),
                        error=str(e),
                    )
                    raise
                finally:
                    elapsed = (time.perf_counter() - t0) * 1000
                    lvl = "WARNING" if elapsed > slow_threshold_ms else "DEBUG"
                    self._do_emit(lvl, f"{func_name}_completed", elapsed_ms=round(elapsed, 2))

            import asyncio
            if asyncio.iscoroutinefunction(func):
                return async_wrapper  # type: ignore
            return sync_wrapper  # type: ignore

        return decorator

    def bind(self, **kwargs):
        """返回绑定额外字段的子logger (临时绑定)."""
        class BoundLogger(StructuredLogger):
            def __init__(self, parent: StructuredLogger, bound: Dict[str, Any]):
                super().__init__(parent.name, parent._sampler)
                self._bound = bound
                self._json_mode = parent._json_mode
                self._hostname = parent._hostname
                self._std_logger = parent._std_logger

            def _emit(self, level: str, message: str, **kw):
                merged = {**self._bound, **kw}
                super()._emit(level, message, **merged)

        return BoundLogger(self, kwargs)


# ==================== 工厂函数 & 单例缓存 ====================

_logger_cache: Dict[str, StructuredLogger] = {}
_cache_lock = threading.Lock()


def get_logger(name: str) -> StructuredLogger:
    """
    获取结构化日志器.
    
    用法:
        from app.core.structured_log import get_logger
        
        log = get_logger(__name__)
        log.info("user_login", user_id=42, ip="192.168.1.1")
        
        # 绑定上下文 (中间件中)
        set_context(request_id="req-abc", agv_id="AGV-01")
        log.info("task_started", task_id="T-001")
        # 自动携带 request_id, agv_id
    """
    with _cache_lock:
        if name not in _logger_cache:
            _logger_cache[name] = StructuredLogger(name)
        return _logger_cache[name]


# ==================== FastAPI 中间件 ====================

def create_logging_middleware():
    """
    创建HTTP请求日志中间件.
    
    自动为每个请求:
      1. 生成 request_id (或从header提取)
      2. 设置上下文
      3. 记录请求/响应信息
      4. 清理上下文
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response

    access_log = get_logger("http.access")

    class LoggingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # 提取或生成request_id
            rid = request.headers.get(
                "X-Request-ID",
                request.headers.get("X-Trace-Id", uuid.uuid4().hex[:12])
            )
            
            # 设置上下文
            set_context(request_id=rid)
            
            t0 = time.perf_counter()
            
            try:
                response: Response = await call_next(request)
                elapsed = (time.perf_counter() - t0) * 1000
                
                access_log.info(
                    "request_completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    elapsed_ms=round(elapsed, 2),
                    client=request.client.host if request.client else None,
                )
                
                response.headers["X-Request-ID"] = rid
                return response
                
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000
                access_log.exception(
                    "request_failed",
                    exc=e,
                    method=request.method,
                    path=request.url.path,
                    elapsed_ms=round(elapsed, 2),
                )
                raise
            finally:
                clear_context()

    return LoggingMiddleware  # 返回类，让add_middleware自动实例化


# ==================== 初始化入口 ====================

def setup_structured_logging(
    level: str = "INFO",
    json_output: bool | None = None,
    enable_sampling: bool = True,
):
    """
    应用启动时调用 — 全局配置结构化日志.
    
    在 main.py lifespan 或 startup 中调用:
        from app.core.structured_log import setup_structured_logging
        setup_structured_logging(level="DEBUG", json_output=True)
    """
    if json_output is None:
        json_output = os.getenv("ENV", "dev") != "dev"
    
    if json_output:
        os.environ["LOG_FORMAT"] = "json"
    
    if not enable_sampling:
        os.environ["LOG_SAMPLING"] = "0"
    
    # 设置标准 logging 根级别 (防止重复)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",  # 让structlog完全接管
        force=True,
        stream=sys.stdout,
    )
    
    root_log = get_logger("root")
    root_log.info(
        "structured_logging_initialized",
        json_mode=json_output,
        sampling_enabled=enable_sampling,
        log_level=level,
    )
    
    return root_log


# ==================== 导出 ====================

__all__ = [
    "get_logger", "set_context", "clear_context",
    "setup_structured_logging", "create_logging_middleware",
    "LogSampler",
]
