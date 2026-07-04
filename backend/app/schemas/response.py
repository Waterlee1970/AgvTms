"""
统一API响应格式模块 - 对标海康RCS Result<T> 规范

目标: 所有API返回统一结构，便于前端处理和网关拦截

海康RCS原始格式:
{
  "code": 200,
  "message": "success",
  "data": { ... }
}

AgvTms V3增强版 (增加trace_id用于链路追踪 + timestamp):
{
  "code": "200",
  "message": "操作成功",
  "data": { ... },
  "trace_id": "abc123xy",
  "timestamp": 1720146540.123
}
"""

from typing import Any, Generic, TypeVar, Optional, List
from pydantic import BaseModel, Field
from enum import Enum
import time


class ResponseCode(str, Enum):
    """标准业务响应码 (对标海康简化版)"""
    # 成功类
    SUCCESS = "200"              # 成功
    CREATED = "201"              # 创建成功
    ACCEPTED = "202"             # 已接受(异步处理中)
    
    # 客户端错误类
    BAD_REQUEST = "400"          # 参数错误
    UNAUTHORIZED = "401"         # 未认证
    FORBIDDEN = "403"            # 无权限
    NOT_FOUND = "404"            # 资源不存在
    METHOD_NOT_ALLOWED = "405"   # 方法不允许
    CONFLICT = "409"             # 资源冲突
    UNPROCESSABLE_ENTITY = "422" # 语义错误
    TOO_MANY_REQUESTS = "429"    # 请求过于频繁
    
    # 服务端错误类
    INTERNAL_ERROR = "500"       # 服务器内部错误
    NOT_IMPLEMENTED = "501"      # 功能未实现
    BAD_GATEWAY = "502"          # 网关错误
    SERVICE_UNAVAILABLE = "503"  # 服务不可用
    GATEWAY_TIMEOUT = "504"      # 网关超限


T = TypeVar('T')
U = TypeVar('U')


class ApiResponse(BaseModel, Generic[T]):
    """
    统一API响应体 - 对标海康 Result<T>
    
    设计原则:
    1. code: 字符串型状态码 (兼容HTTP语义)
    2. message: 人类可读的消息
    3. data: 业务数据载荷 (可为null/数组/对象/原始类型)
    4. trace_id: 请求追踪ID (用于日志关联和调试)
    5. timestamp: 服务端响应时间戳 (客户端可用于时钟同步)
    """
    code: ResponseCode = Field(
        default=ResponseCode.SUCCESS,
        description="业务响应码"
    )
    message: str = Field(
        default="操作成功",
        description="响应消息"
    )
    data: Optional[T] = Field(
        default=None,
        description="业务数据"
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="请求链路追踪ID"
    )
    timestamp: float = Field(
        default_factory=time.time,
        description="服务端时间戳(unix)"
    )
    extra: Optional[dict] = Field(
        default=None,
        description="附加元数据(分页/统计等)"
    )

    class Config:
        json_encoders = {
            ResponseCode: lambda v: v.value if isinstance(v, ResponseCode) else v
        }
        use_enum_values = True

    def dict(self, **kwargs):
        """确保enum序列化为value"""
        d = super().model_dump(**kwargs)
        if isinstance(d.get('code'), ResponseCode):
            d['code'] = d['code'].value
        return d


# ==================== 快捷工厂方法 ====================

def success(
    data: T = None,
    message: str = "操作成功",
    trace_id: str = None,
    extra: dict = None
) -> ApiResponse[T]:
    """创建成功响应"""
    return ApiResponse[T](
        code=ResponseCode.SUCCESS,
        message=message,
        data=data,
        trace_id=trace_id,
        extra=extra,
    )


def created(
    data: T = None,
    message: str = "创建成功",
    trace_id: str = None,
) -> ApiResponse[T]:
    """创建资源成功响应 (HTTP 201)"""
    return ApiResponse[T](
        code=ResponseCode.CREATED,
        message=message,
        data=data,
        trace_id=trace_id,
    )


def accepted(
    data: T = None,
    message: str = "请求已接受，正在处理",
    trace_id: str = None,
) -> ApiResponse[T]:
    """异步处理已接受响应 (HTTP 202)"""
    return ApiResponse[T](
        code=ResponseCode.ACCEPTED,
        message=message,
        data=data,
        trace_id=trace_id,
    )


def error(
    code: ResponseCode = ResponseCode.INTERNAL_ERROR,
    message: str = "操作失败",
    trace_id: str = None,
    data: Any = None,
    extra: dict = None
) -> ApiResponse:
    """创建错误响应"""
    return ApiResponse(
        code=code,
        message=message,
        data=data,
        trace_id=trace_id,
        extra=extra,
    )


def bad_request(
    message: str = "请求参数错误",
    trace_id: str = None,
    details: dict = None,
) -> ApiResponse:
    """400 Bad Request 快捷方法"""
    return error(
        code=ResponseCode.BAD_REQUEST,
        message=message,
        trace_id=trace_id,
        data=details,
    )


def not_found(
    resource: str = "资源",
    trace_id: str = None,
) -> ApiResponse:
    """404 Not Found 快捷方法"""
    return error(
        code=ResponseCode.NOT_FOUND,
        message=f"{resource}不存在",
        trace_id=trace_id,
    )


def unauthorized(
    message: str = "未认证或认证已过期",
    trace_id: str = None,
) -> ApiResponse:
    """401 Unauthorized 快捷方法"""
    return error(
        code=ResponseCode.UNAUTHORIZED,
        message=message,
        trace_id=trace_id,
    )


def forbidden(
    message: str = "权限不足",
    trace_id: str = None,
) -> ApiResponse:
    """403 Forbidden 快捷方法"""
    return error(
        code=ResponseCode.FORBIDDEN,
        message=message,
        trace_id=trace_id,
    )


def conflict(
    message: str = "资源冲突",
    trace_id: str = None,
    details: dict = None,
) -> ApiResponse:
    """409 Conflict 快捷方法"""
    return error(
        code=ResponseCode.CONFLICT,
        message=message,
        trace_id=trace_id,
        data=details,
    )


def too_many_requests(
    retry_after: float = 60.0,
    trace_id: str = None,
) -> ApiResponse:
    """429 Too Many Requests 快捷方法"""
    return error(
        code=ResponseCode.TOO_MANY_REQUESTS,
    message=f"请求过于频繁，请在 {retry_after:.0f}秒 后重试",
        trace_id=trace_id,
        extra={"retry_after": retry_after},
    )


# ==================== 分页响应 ====================

class PaginatedData(BaseModel, Generic[T]):
    """分页数据容器"""
    items: List[T] = Field(default_factory=list, description="数据列表")
    total: int = Field(default=0, ge=0, description="总记录数")
    page: int = Field(default=1, ge=1, description="当前页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页大小")
    total_pages: int = Field(default=0, ge=0, description="总页数")

    @classmethod
    def create(
        cls,
        items: List[T],
        total: int,
        page: int,
        page_size: int,
    ) -> 'PaginatedData[T]':
        """工厂方法: 创建分页响应"""
        import math
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


def paginated(
    items: List[T],
    total: int,
    page: int,
    page_size: int,
    message: str = "查询成功",
    trace_id: str = None,
) -> ApiResponse[PaginatedData[T]]:
    """创建分页成功响应"""
    return success(
        data=PaginatedData.create(items, total, page, page_size),
        message=message,
        trace_id=trace_id,
    )


# ==================== 批量操作响应 ====================

class BatchResult(BaseModel):
    """批量操作结果"""
    succeeded: int = Field(default=0, ge=0, description="成功数量")
    failed: int = Field(default=0, ge=0, description="失败数量")
    errors: List[dict] = Field(default_factory=list, description="错误详情列表")


def batch_result(
    succeeded: int = 0,
    failed: int = 0,
    errors: List[dict] = None,
    message: str = "批量操作完成",
    trace_id: str = None,
) -> ApiResponse[BatchResult]:
    """批量操作结果响应"""
    return success(
        data=BatchResult(
            succeeded=succeeded,
            failed=failed,
            errors=errors or [],
        ),
        message=message,
        trace_id=trace_id,
    )
