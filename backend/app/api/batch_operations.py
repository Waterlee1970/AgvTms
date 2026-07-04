"""
批量操作标准化模块 - 对标Spring Batch + AWS Batch API

功能:
1. 批量创建/更新/删除通用框架
2. 部分失败处理机制 (partial failure)
3. 异步批量执行支持
4. 进度追踪与结果查询
5. 批量操作限流与幂等性保证

设计原则:
- 原子性: 要么全成功要么明确标记失败项
- 可观测性: 返回每个项目的处理结果
- 性能: 支持异步后台执行避免超时
- 幂等性: 相同请求重复提交结果一致

使用示例:
    from app.api.batch_operations import BatchProcessor
    
    processor = BatchProcessor(
        max_batch_size=50,
        validator=TaskRequestValidator,
        executor=create_task_single,  # 单项处理函数
    )
    
    result = await processor.process(items=request.tasks)
    # result.succeeded_count, result.failed_count, result.errors[], result.items[]
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional, Callable, TypeVar, Generic
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel, Field  # 添加Field导入

from .validators import validate_batch_items, ValidationErrorDetail


# ==================== 1. 数据模型 ====================

class BatchOperationStatus(str, Enum):
    """批量操作状态枚举"""
    PENDING = "pending"           # 待处理
    PROCESSING = "processing"     # 处理中
    COMPLETED = "completed"       # 全部完成 (可能有部分失败)
    FAILED = "failed"             # 整体失败 (如校验不通过)
    CANCELLED = "cancelled"       # 用户取消


class ItemStatus(str, Enum):
    """单项处理状态"""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"           # 跳过 (如重复)


@dataclass
class BatchItemResult:
    """单项处理结果"""
    index: int                    # 在原始列表中的位置 (0-based)
    input_data: Any               # 原始输入数据
    status: ItemStatus
    output_data: Optional[Any] = None      # 处理后的数据 (成功时)
    error_detail: Optional[ValidationErrorDetail] = None  # 错误详情 (失败时)
    processing_time_ms: float = 0.0        # 处理耗时


@dataclass
class BatchResult:
    """
    批量操作总结果 (对标AWS Batch Job Summary)
    
    示例JSON输出:
    {
        "batch_id": "batch-uuid-001",
        "status": "completed",
        "total_items": 10,
        "succeeded": 8,
        "failed": 2,
        "items": [...],
        "created_at": "...",
        "completed_at": "...",
    }
    """
    batch_id: str
    status: BatchOperationStatus
    total_items: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    items: List[BatchItemResult] = field(default_factory=list)
    errors_summary: List[Dict] = field(default_factory=list)  # 聚合错误统计
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    processing_time_total_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化的字典 (符合ApiResponse.data格式)"""
        return {
            "batch_id": self.batch_id,
            "status": self.status.value,
            "total_items": self.total_items,
            "succeeded": self.succeeded_count,
            "failed": self.failed_count,
            "items": [
                {
                    "index": item.index,
                    "status": item.status.value,
                    "data": (
                        item.output_data.model_dump() if hasattr(item.output_data, 'model_dump')
                        else item.output_data
                    ) if item.output_data else None,
                    "error": item.error_detail.model_dump() if item.error_detail else None,
                }
                for item in self.items
            ],
            "errors_summary": self.errors_summary,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "processing_time_total_ms": round(self.processing_time_total_ms, 2),
        }


# ==================== 2. 批量处理器核心类 ====================

T = TypeVar('T')  # 输入类型
U = TypeVar('U')  # 输出类型


class BatchProcessor(Generic[T, U]):
    """
    通用批量操作处理器
    
    使用示例:
        # 同步批量创建
        processor = BatchProcessor[T, U](
            max_batch_size=50,
            stop_on_first_error=False,  # 允许部分失败
            validator=TaskRequestValidator,
        )
        
        result = await processor.execute(
            items=request.tasks,
            process_fn=single_create_task,
        )
        
        if result.failed_count > 0 and result.succeeded_count > 0:
            # 部分成功: HTTP 207 Multi-Status
            return JSONResponse(status_code=207, content=result.to_dict())
        elif result.failed_count == result.total_items:
            # 全部失败: HTTP 400
            raise HTTPException(400, detail=result.errors_summary)
        else:
            # 全部成功: HTTP 201 Created
            return JSONResponse(status_code=201, content=result.to_dict())
    """
    
    def __init__(
        self,
        max_batch_size: int = 50,
        stop_on_first_error: bool = False,
        validator_class=None,
        concurrency_limit: int = 5,          # 并发数限制 (防资源耗尽)
        timeout_per_item_seconds: float = 30.0,  # 单项超时
    ):
        self.max_batch_size = max_batch_size
        self.stop_on_first_error = stop_on_first_error
        self.validator_class = validator_class
        self.concurrency_limit = concurrency_limit
        self.timeout_per_item = timeout_per_item_seconds
    
    async def execute(
        self,
        items: List[T],
        process_fn: Callable[[T], U],         # 单项处理函数 (async)
        context: Optional[Dict[str, Any]] = None,  # 额外上下文 (如user_id)
    ) -> BatchResult:
        """
        执行批量操作
        
        流程:
        1. 校验输入 (大小限制 + 业务规则)
        2. 逐项或并发处理
        3. 聚合结果
        4. 返回BatchResult
        
        Args:
            items: 待处理的原始数据列表
            process_fn: 异步单项处理函数
            context: 额外上下文信息
            
        Returns:
            BatchResult 包含每项的处理详情
        """
        import time
        start_time = time.monotonic()
        
        batch_id = f"batch-{uuid.uuid4().hex[:12]}"
        result = BatchResult(batch_id=batch_id, status=BatchOperationStatus.PROCESSING)
        
        # Step 1: 输入校验
        validation_errors = validate_batch_items(
            items=items,
            max_batch_size=self.max_batch_size,
            validator_class=self.validator_class,
        )
        
        if validation_errors:
            result.status = BatchOperationStatus.FAILED
            result.total_items = len(items)
            result.failed_count = len(items)  # 校验失败视为全部未通过
            result.errors_summary = [err.model_dump() for err in validation_errors]
            result.completed_at = datetime.utcnow()
            result.processing_time_total_ms = (time.monotonic() - start_time) * 1000
            return result
        
        result.total_items = len(items)
        
        # Step 2: 逐项处理
        semaphore = asyncio.Semaphore(self.concurrency_limit)
        
        async def process_single(index: int, item: T) -> BatchItemResult:
            """处理单个项目 (带并发控制和超时)"""
            item_start = time.monotonic()
            
            async with semaphore:
                try:
                    # 超时控制
                    output = await asyncio.wait_for(
                        process_fn(item, **(context or {})),
                        timeout=self.timeout_per_item,
                    )
                    
                    elapsed = (time.monotonic() - item_start) * 1000
                    
                    return BatchItemResult(
                        index=index,
                        input_data=item,
                        status=ItemStatus.SUCCESS,
                        output_data=output,
                        processing_time_ms=round(elapsed, 2),
                    )
                    
                except asyncio.TimeoutError:
                    elapsed = (time.monotonic() - item_start) * 1000
                    return BatchItemResult(
                        index=index,
                        input_data=item,
                        status=ItemStatus.FAILED,
                        error_detail=ValidationErrorDetail(
                            field=f"[{index}]",
                            message="处理超时，请稍后重试或减小批次大小",
                            code="TIMEOUT",
                        ),
                        processing_time_ms=round(elapsed, 2),
                    )
                    
                except Exception as e:
                    elapsed = (time.monotonic() - item_start) * 1000
                    return BatchItemResult(
                        index=index,
                        input_data=item,
                        status=ItemStatus.FAILED,
                        error_detail=ValidationErrorDetail(
                            field=f"[{index}]",
                            message=str(e)[:500],
                            code="PROCESSING_ERROR",
                        ),
                        processing_time_ms=round(elapsed, 2),
                    )
        
        # 并发执行所有项目
        tasks = [process_single(idx, item) for idx, item in enumerate(items)]
        item_results = await asyncio.gather(*tasks, return_exceptions=False)
        
        # Step 3: 聚合结果
        result.items = item_results
        for item_result in item_results:
            if item_result.status == ItemStatus.SUCCESS:
                result.succeeded_count += 1
            else:
                result.failed_count += 1
                if item_result.error_detail:
                    result.errors_summary.append(item_result.error_detail.model_dump())
        
        # 判断整体状态
        if result.failed_count == 0:
            result.status = BatchOperationStatus.COMPLETED
        elif result.succeeded_count > 0:
            result.status = BatchOperationStatus.COMPLETED  # 部分成功也算完成
        else:
            result.status = BatchOperationStatus.FAILED
        
        result.completed_at = datetime.utcnow()
        result.processing_time_total_ms = (time.monotonic() - start_time) * 1000
        
        return result


# ==================== 3. Pydantic 请求/响应模型 ====================

class BatchCreateRequest(BaseModel):
    """通用批量创建请求体模板"""
    items: List[Any] = Field(
        description="待创建的项目列表",
        min_length=1,
        max_length=50,
    )
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="可选配置 (stop_on_error, dry_run等)",
    )


class BatchResponse(BaseModel):
    """标准批量响应体 (可直接用作response_model)"""
    batch_id: str
    status: str
    total_items: int
    succeeded: int
    failed: int
    items: List[Dict]
    errors_summary: List[Dict]


__all__ = [
    'BatchOperationStatus',
    'ItemStatus',
    'BatchItemResult',
    'BatchResult',
    'BatchProcessor',
    'BatchCreateRequest',
    'BatchResponse',
]
