"""
API依赖注入模块 - 对标Spring MVC ControllerAdvice

提供:
1. 通用分页参数解析
2. 过滤/排序参数提取
3. 响应格式统一包装
4. 权限校验装饰器

使用示例:
    from app.api.deps import PaginationParams, CommonQueryParams
    
    @router.get("/tasks")
    async def list_tasks(
        params: PaginationParams = Depends(),
    ):
        # params.page, params.page_size, params.offset 自动解析
        ...
"""

from typing import Generic, TypeVar, Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

T = TypeVar('T')


# ==================== 1. 分页参数 ====================

class PaginationParams(BaseModel):
    """
    通用分页参数 (对标Spring Data Pageable)
    
    使用方式:
        GET /api/tasks?page=2&page_size=20
        
    或通过Depends()自动注入:
        async def list(params: PaginationParams = Depends()):
            offset = params.offset  # 计算后的偏移量
            limit = params.page_size
    """
    page: int = Field(
        default=1,
        ge=1,
        le=10000,
        description="页码 (从1开始)",
        examples=[1],
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="每页记录数 (1-100)",
        alias="page_size",
        examples=[20],
    )
    
    @property
    def offset(self) -> int:
        """计算数据库查询偏移量"""
        return (self.page - 1) * self.page_size
    
    @property
    def limit(self) -> int:
        """返回限制条数 (别名)"""
        return self.page_size


# ==================== 2. 排序参数 ====================

class SortField(BaseModel):
    """
    排序字段定义
    """
    field: str = Field(
        description="排序字段名",
        examples=["created_at", "priority", "status"],
    )
    order: str = Field(
        default="desc",
        pattern=r"^(asc|desc)$",
        description="排序方向: asc(升序) 或 desc(降序)",
        examples=["desc"],
    )


class SortParams(BaseModel):
    """
    多字段排序参数
    
    使用方式:
        GET /api/tasks?sort_by=created_at:desc,priority:asc
    """
    sort_by: Optional[str] = Field(
        default=None,
        description="排序规则 (field:order, 支持多字段逗号分隔)",
        examples=["created_at:desc", "priority:asc,status:desc"],
    )
    
    @property
    def parsed(self) -> List[SortField]:
        """解析排序字符串为结构化列表"""
        if not self.sort_by:
            return [SortField(field="id", order="desc")]  # 默认按ID降序
        
        fields = []
        for part in self.sort_by.split(","):
            part = part.strip()
            if ":" in part:
                field, order = part.rsplit(":", 1)
                fields.append(SortField(field=field.strip(), order=order.lower()))
            else:
                fields.append(SortField(field=part))
        
        return fields
    
    allowed_fields: Optional[List[str]] = None  # 由业务层设置允许的排序字段
    
    def validate_allowed_fields(self) -> bool:
        """
        验证排序字段是否在白名单中
        
        Returns:
            True: 所有字段合法
            False: 存在不允许的字段
        """
        if not self.allowed_fields:
            return True  # 未设置则放行
        
        parsed = self.parsed
        for sort_field in parsed:
            if sort_field.field not in self.allowed_fields:
                return False
        
        return True


# ==================== 3. 过滤参数 ====================

class FilterParams(BaseModel):
    """
    通用过滤参数基类
    
    子类应继承并添加具体过滤字段:
        class TaskFilterParams(FilterParams):
            status: Optional[str] = None
            priority_min: Optional[int] = None
            
    使用方式:
        GET /api/tasks?status=pending&priority_min=10&search=keyword
    """
    search: Optional[str] = Field(
        default=None,
        max_length=200,
        description="全文搜索关键词 (模糊匹配多个字段)",
        examples=["运输任务AGV-001"],
    )
    
    # 时间范围过滤 (ISO 8601格式)
    created_after: Optional[str] = Field(
        default=None,
        description="创建时间起点 (含) - ISO8601格式: 2024-01-15T00:00:00Z",
        examples=["2024-01-15T00:00:00Z"],
    )
    created_before: Optional[str] = Field(
        default=None,
        description="创建时间终点 (不含) - ISO8601格式",
        examples=["2024-01-16T23:59:59Z"],
    )


# ==================== 4. 组合查询参数 ====================

class CommonQueryParams(BaseModel):
    """
    通用查询参数组合 (分页+排序+搜索)
    
    这是大多数列表端点的标准参数集。
    
    使用示例:
        @router.get("/api/v2/vehicles")
        async def list_vehicles(params: CommonQueryParams = Depends()):
            query = Vehicle.select()
            
            # 应用搜索
            if params.q:
                query = query.where(Vehicle.name.contains(params.q))
            
            # 应用排序
            for sort in params.sort.parsed:
                query = query.order_by(sort.field)
            
            # 分页查询
            items = await query.offset(params.pagination.offset).limit(params.pagination.limit)
            total = await query.count()
            
            return paginated(items=items, total=total, ...)
    """
    pagination: PaginationParams = Field(default_factory=PaginationParams)
    sort: SortParams = Field(default_factory=SortParams)
    q: Optional[str] = Field(
        default=None,
        max_length=200,
        description="搜索关键词",
    )
    
    model_config = {
        "populate_by_name": True,  # 允许alias和字段名同时使用
    }


# ==================== 5. 响应包装器 ====================

def wrap_response(data: T, **kwargs) -> dict:
    """
    将数据统一包装为ApiResponse格式
    
    Args:
        data: 业务数据载荷
        **kwargs: 额外参数 (message, trace_id, extra等)
        
    Returns:
        符合ApiResponse规范的字典
    """
    from app.schemas.response import success as success_response
    response = success_response(data=data, **kwargs)
    return response.dict()


def wrap_paginated_response(
    items: List[T],
    total: int,
    pagination: PaginationParams,
    **kwargs
) -> dict:
    """
    包装分页响应
    
    Args:
        items: 当前页的数据列表
        total: 总记录数
        pagination: 分页参数对象
        **kwargs: 额外参数
        
    Returns:
        包含PaginatedData的ApiResponse字典
    """
    from app.schemas.response import paginated as paginated_response
    response = paginated_response(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        **kwargs
    )
    return response.dict()


# ==================== 6. 导出便捷类型 ====================

# FastAPI Depends() 的快捷方式
from fastapi import Depends, Query

def get_pagination_params(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> PaginationParams:
    """获取分页参数 (用于FastAPI Depends)"""
    return PaginationParams(page=page, page_size=page_size)


def get_sort_params(
    sort_by: Optional[str] = Query(None, description="排序字段:direction"),
) -> SortParams:
    """获取排序参数"""
    return SortParams(sort_by=sort_by)


__all__ = [
    'PaginationParams',
    'SortParams', 
    'SortField',
    'FilterParams',
    'CommonQueryParams',
    'wrap_response',
    'wrap_paginated_response',
    'get_pagination_params',
    'get_sort_params',
]
