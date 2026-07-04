"""
API V2 增强路由示例 - 展示Phase 2改进的最佳实践

本文件演示如何使用新创建的:
- deps (分页/排序/搜索参数)
- validators (请求校验)
- batch_operations (批量操作)
- openapi_config (文档增强)

对标海康RCS API v3.0规范
"""

from typing import Optional, List, Dict, Any
import asyncio
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

# 导入Phase 2新增模块
from .deps import PaginationParams, CommonQueryParams
from .validators import (
    TaskRequestValidator,
    ValidationErrorDetail,
)
from .batch_operations import BatchProcessor, BatchResult, BatchCreateRequest

# 创建V2增强版路由器
router = APIRouter(
    prefix="/api/v2/enhanced",
    tags=["API V2 Enhanced"],
)


# ==================== 数据模型 ====================

class TaskCreateRequest(BaseModel):
    """任务创建请求 (严格Pydantic v2校验)"""
    task_type: str = Field(description="任务类型", examples=["agv_only"])
    pickup_node: str = Field(..., min_length=1, max_length=50, description="取货点位")
    dropoff_node: str = Field(..., min_length=1, max_length=50, description="放货点位")
    priority: int = Field(default=10, ge=1, le=100, description="优先级 (1-100)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_type": "agv_only",
                "pickup_node": "WH_A",
                "dropoff_node": "LINE_B",
                "priority": 10,
            }
        }


class TaskResponse(BaseModel):
    """任务响应 (标准化字段)"""
    task_id: str
    status: str
    priority: int
    created_at: str


# ==================== 端点实现示例 ====================

@router.get(
    "/tasks",
    response_model=Dict[str, Any],
    summary="查询任务列表 (支持分页/过滤/排序)",
    responses={200: {"description": "成功返回分页数据"}},
)
async def list_tasks_enhanced(
    params: CommonQueryParams = Depends(),
):
    """
    ## 查询任务列表 (V2增强版)
    
    ### 功能特性:
    - ✅ 标准分页: `?page=1&page_size=20`
    - ✅ 多字段排序: `?sort_by=priority:desc,created_at:asc`
    - ✅ 全文搜索: `?q=关键词`
    - ✅ 统一响应格式: ApiResponse[PaginatedData]
    
    ### 示例请求:
        GET /api/v2/enhanced/tasks?page=2&page_size=10&sort_by=priority:desc&q=AGV
    """
    from app.schemas.response import paginated
    
    # 模拟数据 (实际从DB查询)
    mock_items = [
        TaskResponse(task_id=f"task-{i}", status="pending", priority=i%20+1, created_at="2024-07-15T10:30:00Z")
        for i in range(1, 101)  
    ]
    
    # 应用搜索过滤
    if params.q:
        mock_items = [item for item in mock_items if params.q.lower() in item.task_id.lower()]
    
    total = len(mock_items)
    
    # 手动分页 (仅用于演示，生产环境应在SQL层完成)
    start_idx = params.pagination.offset
    end_idx = start_idx + params.pagination.limit
    paginated_items = mock_items[start_idx:end_idx]
    
    # 包装成统一响应格式
    response = paginated(
        items=[item.model_dump() for item in paginated_items],
        total=total,
        page=params.pagination.page,
        page_size=params.pagination.page_size,
        message="查询成功",
    )
    
    return response.model_dump()


@router.post(
    "/tasks/batch",
    response_model=Dict[str, Any],
    summary="批量创建任务",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "全部或部分成功"},
        207: {"description": "部分成功 (Multi-Status)"},
        400: {"description": "校验失败"},
    },
)
async def batch_create_tasks(request: BatchCreateRequest):
    """
    ## 批量创建任务 (V2专业版)
    
    ### 特性:
    - 📦 支持最多50个/批
    - 🔍 自动逐项业务规则校验
    - ⚡ 并发执行提升性能
    - 📊 返回每项处理结果
    """
    processor = BatchProcessor(
        max_batch_size=50,
        stop_on_first_error=False,
        validator_class=TaskRequestValidator,
        concurrency_limit=3,
    )
    
    async def create_single_task(item_data: dict, **context) -> dict:
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        await asyncio.sleep(0.01)  # 模拟处理延迟
        
        return TaskResponse(
            task_id=task_id,
            status="pending",
            priority=item_data.get("priority", 10),
            created_at=datetime.utcnow().isoformat() + "Z",
        ).model_dump()
    
    result: BatchResult = await processor.execute(
        items=request.items if isinstance(request.items[0], dict) else [item.model_dump() for item in request.items],
        process_fn=create_single_task,
    )
    
    # 选择HTTP状态码
    if result.failed_count == result.total_items:
        raise HTTPException(status_code=400, detail=result.errors_summary)
    
    http_status = status.HTTP_207_MULTI_STATUS if result.failed_count > 0 else status.HTTP_201_CREATED
    
    from app.schemas.response import success
    return JSONResponse(
        status_code=http_status,
        content=success(data=result.to_dict(), message="批量操作完成").model_dump(),
    )


# 需要导入JSONResponse
from fastapi.responses import JSONResponse


# 注册到main.py的方式 (取消注释即可启用):
# from app.api.v2_enhanced_routes import router as v2_enhanced_router
# app.include_router(v2_enhanced_router)

__all__ = ['router']
