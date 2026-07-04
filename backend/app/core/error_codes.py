"""
业务错误码体系 — Phase 3 可靠性增强.

对标: 阿里巴巴错误码规范 / HTTP RFC 7807 Problem Details.

设计原则:
  1. 全局唯一: 每个业务错误有唯一标识
  2. 分层分类: 模块(2位) + 功能(2位) + 序号(3位) = 7位码
  3. 可追溯: 错误码可映射到文档和解决方案
  4. 国际化: 支持中英文消息模板

格式:
  - 系统级 (1xxxx): 通用框架错误
  - 任务模块 (10xxx): 任务CRUD/调度相关
  - 车辆模块 (11xxx): AGV管理/状态/控制
  - 地图模块 (12xxx): 地图/路径/节点
  - 调度模块 (13xxx): 算法引擎/分配优化
  - 协议模块 (14xxx): MQTT/VDA5050/OPC UA
  - 用户权限 (15xxx): 认证授权/RBAC
  - 外部集成 (16xxx): 第三方API/数据库

使用示例:
    from app.core.error_codes import ErrorCode, BusinessError, error_registry
    
    # 抛出业务异常
    raise BusinessError(
        code=ErrorCode.TASK_NOT_FOUND,
        message="任务 T-001 不存在",
        details={"task_id": "T-001"},
    )
    
    # 在FastAPI路由中使用
    @app.exception_handler(BusinessError)
    async def business_error_handler(request, exc):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_registry.to_problem_details(exc),
        )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Type, Union


# ==================== 错误码定义 ====================

class ErrorCode(IntEnum):
    """
    业务错误码枚举.
    
    编码规则: MMFFNNN
    - MM: 模块编号 (10-99)
    - FF: 功能子类 (00-99)
    - NNN: 序号 (001-999)
    
    特殊段:
    - 1xxxx: 系统框架级错误
    """
    
    # ========== 系统级 (1xxxx) ==========
    SUCCESS = 0                        # 成功 (特殊值)
    UNKNOWN_ERROR = 10001              # 未知内部错误
    INVALID_REQUEST = 10002            # 请求参数无效
    UNAUTHORIZED = 10003               # 未认证
    FORBIDDEN = 10004                  # 无权限
    RATE_LIMITED = 10005               # 请求过于频繁
    SERVICE_UNAVAILABLE = 10006        # 服务暂时不可用
    TIMEOUT = 10007                    # 请求超时
    CIRCUIT_OPEN = 10008               # 断路器打开
    DEPENDENCY_FAILURE = 10009         # 依赖服务失败
    VALIDATION_ERROR = 10010           # 数据校验失败
    
    # ========== 任务模块 (10xxx) ==========
    TASK_NOT_FOUND = 10001             # 任务不存在
    TASK_ALREADY_EXISTS = 10002        # 任务已存在
    TASK_INVALID_STATUS = 10003        # 任务状态无效
    TASK_STATUS_CONFLICT = 10004       # 任务状态冲突
    TASK_NO_VALID_PATH = 10005         # 无有效路径
    TASK_PRIORITY_OUT_OF_RANGE = 10006 # 优先级超出范围
    TASK_CARGO_EXCEEDED = 10007        # 货物超重/超尺寸
    TASK_CANCELLED = 10008             # 任务已取消
    TASK_EXPIRED = 10009               # 任务已过期
    TASK_DISPATCH_FAILED = 10010       # 任务派发失败
    
    # ========== 车辆模块 (11xxx) ==========
    VEHICLE_NOT_FOUND = 11001          # 车辆不存在
    VEHICLE_OFFLINE = 11002            # 车辆离线
    VEHICLE_LOW_BATTERY = 11003        # 电量过低
    VEHICLE_BUSY = 11004               # 车辆忙碌
    VEHICLE_FAULT = 11005              # 车辆故障
    VEHICLE_INCOMPATIBLE_TYPE = 11006  # 车型不兼容
    VEHICLE_CHARGING = 11007           # 正在充电
    VEHICLE_POSITION_INVALID = 11008   # 位置无效
    
    # ========== 地图模块 (12xxx) ==========
    MAP_NODE_NOT_FOUND = 12001         # 节点不存在
    MAP_EDGE_NOT_FOUND = 12002         # 边不存在
    MAP_PATH_BLOCKED = 12003           # 路径被阻塞
    MAP_INVALID_COORDINATE = 12004     # 坐标无效
    MAP_ZONE_CONFLICT = 12005          # 区域冲突
    
    # ========== 调度模块 (13xxx) ==========
    SCHEDULER_NOT_READY = 13001        # 调度器未就绪
    SCHEDULER_RUNNING = 13002          # 调度器正在运行
    SCHEDULER_NO_FEASIBLE_SOLUTION = 13003  # 无可行解
    ALGORITHM_TIMEOUT = 13004          # 算法超时
    CONFLICT_DETECTED = 13005          # 冲突检测
    DEADLOCK_DETECTED = 13006          # 死锁检测
    RESOURCE_INSUFFICIENT = 13007      # 资源不足
    
    # ========== 协议模块 (14xxx) ==========
    MQTT_CONNECTION_LOST = 14001       # MQTT连接断开
    VDA5050_INVALID_ORDER = 14002      # VDA5050指令无效
    OPC_UA_CONNECTION_FAILED = 14003   # OPC UA连接失败
    PROTOCOL_UNSUPPORTED = 14004       # 不支持的协议版本
    
    # ========== 用户权限 (15xxx) ==========
    TOKEN_EXPIRED = 15001              # Token过期
    TOKEN_INVALID = 15002              # Token无效
    PERMISSION_DENIED = 15003          # 权限不足
    USER_DISABLED = 15004              # 用户已禁用
    ROLE_NOT_FOUND = 15005             # 角色不存在
    
    # ========== 外部集成 (16xxx) ==========
    DATABASE_ERROR = 16001             # 数据库错误
    REDIS_ERROR = 16002                # Redis错误
    KAFKA_ERROR = 16003                # Kafka错误
    EXTERNAL_API_ERROR = 16004         # 第三方API错误
    
    @property
    def module(self) -> str:
        """所属模块"""
        code = self.value
        if code < 20000:
            return "system"
        return f"{code // 10000}xx"
    
    @property
    def http_status(self) -> int:
        """推荐的HTTP状态码"""
        mapping = {
            # 4xx 客户端错误
            self.INVALID_REQUEST: 400,
            self.UNAUTHORIZED: 401,
            self.FORBIDDEN: 403,
            self.RATE_LIMITED: 429,
            self.VALIDATION_ERROR: 422,
            self.TASK_NOT_FOUND: 404,
            self.VEHICLE_NOT_FOUND: 404,
            self.MAP_NODE_NOT_FOUND: 404,
            
            # 5xx 服务端错误
            self.UNKNOWN_ERROR: 500,
            self.SERVICE_UNAVAILABLE: 503,
            self.TIMEOUT: 504,
            self.CIRCUIT_OPEN: 503,
            self.DEPENDENCY_FAILURE: 502,
            self.TASK_DISPATCH_FAILED: 503,
            self.SCHEDULER_NOT_READY: 503,
            self.ALGORITHM_TIMEOUT: 504,
            
            # 4xx/5xx 取决于上下文
            self.TASK_INVALID_STATUS: 409,
            self.TASK_STATUS_CONFLICT: 409,
            self.VEHICLE_OFFLINE: 503,
            self.VEHICLE_FAULT: 503,
            self.MAP_PATH_BLOCKED: 503,
            self.SCHEDULER_NO_FEASIBLE_SOLUTION: 422,
            self.CONFLICT_DETECTED: 409,
            self.DEADLOCK_DETECTED: 409,
            self.DATABASE_ERROR: 503,
            self.REDIS_ERROR: 503,
            self.KAFKA_ERROR: 503,
        }
        
        # 默认根据范围判断
        if 10001 <= self.value <= 10999:
            return mapping.get(self.value, 400)
        elif self.value >= 60000:
            return 500
        
        return mapping.get(self.value, 500)
    
    @property
    def retryable(self) -> bool:
        """是否可重试"""
        always_retryable = {
            self.TIMEOUT, self.SERVICE_UNAVAILABLE, 
            self.CIRCUIT_OPEN, self.DEPENDENCY_FAILURE,
            self.DATABASE_ERROR, self.REDIS_ERROR, self.KAFKA_ERROR,
            self.MQTT_CONNECTION_LOST, self.OPC_UA_CONNECTION_FAILED,
            self.VEHICLE_OFFLINE, self.ALGORITHM_TIMEOUT,
        }
        return self in always_retryable


# ==================== 错误元数据注册表 ====================

@dataclass
class ErrorMetadata:
    """错误的详细元数据"""
    code: ErrorCode
    message_template: str              # 消息模板 (支持{变量})
    message_en: Optional[str] = None   # 英文消息
    description: Optional[str] = None  # 详细描述
    solution: Optional[str] = None     # 解决方案建议
    doc_url: Optional[str] = None      # 文档链接
    severity: str = "error"            # severity级别: warning/error/critical
    
    def format_message(self, **kwargs) -> str:
        """格式化消息"""
        try:
            return self.message_template.format(**kwargs)
        except KeyError:
            return self.message_template


class ErrorRegistry:
    """
    错误元数据注册中心.
    
    存储所有业务错误码的详细信息，支持查询和国际化.
    """
    
    def __init__(self):
        self._registry: Dict[int, ErrorMetadata] = {}
        self._initialize_builtin_errors()
    
    def _initialize_builtin_errors(self) -> None:
        """初始化内置错误元数据"""
        builtin_errors = [
            # 系统级
            ErrorMetadata(
                code=ErrorCode.UNKNOWN_ERROR,
                message_template="系统内部错误: {reason}",
                description="发生了未预期的服务器内部错误",
                solution="请稍后重试，如果问题持续请联系技术支持",
            ),
            ErrorMetadata(
                code=ErrorCode.INVALID_REQUEST,
                message_template="请求参数无效: {field}",
                description="请求的参数不符合要求",
                solution="请检查请求参数是否正确，参考API文档",
            ),
            ErrorMetadata(
                code=ErrorCode.RATE_LIMITED,
                message_template="请求过于频繁，请 {retry_after}s 后重试",
                description="超过了API调用频率限制",
                solution="降低请求频率或申请提高限额",
            ),
            ErrorMetadata(
                code=ErrorCode.TIMEOUT,
                message_template="请求超时: {operation}",
                description="操作在规定时间内未完成",
                solution="请检查网络状况后重试",
            ),
            ErrorMetadata(
                code=ErrorCode.CIRCUIT_OPEN,
                message_template="{service} 服务暂时不可用 (熔断保护)",
                description="依赖服务触发熔断机制，暂时停止调用",
                solution="等待服务恢复后自动重试，通常30秒-5分钟",
            ),
            
            # 任务模块
            ErrorMetadata(
                code=ErrorCode.TASK_NOT_FOUND,
                message_template="任务 {task_id} 不存在",
                description="指定的任务ID在系统中找不到",
                solution="确认任务ID是否正确，或查看已创建的任务列表",
            ),
            ErrorMetadata(
                code=ErrorCode.TASK_INVALID_STATUS,
                message_template="任务状态无效: 当前={current}, 目标={target}",
                description="任务当前状态不支持此操作",
                solution="确认任务的当前状态，检查状态转换图",
            ),
            ErrorMetadata(
                code=ErrorCode.TASK_NO_VALID_PATH,
                message_template="无法为任务 {task_id} 规划有效路径",
                description="起点到终点之间没有可行路径",
                solution="检查地图连通性、AGV可达区域、障碍物设置",
            ),
            ErrorMetadata(
                code=ErrorCode.TASK_DISPATCH_FAILED,
                message_template="任务 {task_id} 派发失败: {reason}",
                description="任务分配给AGV时发生错误",
                solution="检查可用AGV数量、AGV状态、地图配置",
            ),
            
            # 车辆模块
            ErrorMetadata(
                code=ErrorCode.VEHICLE_NOT_FOUND,
                message_template="车辆 {vehicle_id} 不存在",
                description="指定的车辆ID在系统中找不到",
                solution="确认车辆ID是否正确，或查看已注册的车辆列表",
            ),
            ErrorMetadata(
                code=ErrorCode.VEHICLE_OFFLINE,
                message_template="车辆 {vehicle_id} 已离线",
                description="AGV与系统失去通信",
                solution="检查AGV网络连接、电源状态、MQTT连接",
            ),
            ErrorMetadata(
                code=ErrorCode.VEHICLE_LOW_BATTERY,
                message_template="车辆 {vehicle_id} 电量过低: {battery}% (阈值: {threshold}%)",
                description="AGV电量低于工作阈值",
                solution="等待AGV充电完成后再分配任务",
            ),
            ErrorMetadata(
                code=ErrorCode.VEHICLE_FAULT,
                message_template="车辆 {vehicle_id} 处于故障状态: {fault_code}",
                description="AGV报告硬件或软件故障",
                solution="联系运维人员处理故障，排除后再使用",
            ),
            
            # 调度模块
            ErrorMetadata(
                code=ErrorCode.SCHEDULER_NOT_READY,
                message_template="调度器尚未就绪",
                description="算法引擎还在初始化或预热中",
                solution="等待几秒后重试，或检查调度器日志",
            ),
            ErrorMetadata(
                code=ErrorCode.SCHEDULER_NO_FEASIBLE_SOLUTION,
                message_template="无法找到可行的调度方案",
                description="在约束条件下无解",
                solution="放宽约束条件、增加可用资源、调整优先级",
            ),
            ErrorMetadata(
                code=ErrorCode.ALGORITHM_TIMEOUT,
                message_template="{algorithm} 算法执行超时 (> {timeout_ms}ms)",
                description="算法在限定时间内未能返回结果",
                solution="减小问题规模、调整算法参数、增加超时时间",
            ),
            ErrorMetadata(
                code=ErrorCode.DEADLOCK_DETECTED,
                message_template="检测到死锁: 涉及AGVs {agv_ids}",
                description="多辆AGV相互等待形成死锁",
                solution="系统会自动解除死锁（让路/回退），如频繁发生需优化路径规划",
            ),
            
            # 数据库/基础设施
            ErrorMetadata(
                code=ErrorCode.DATABASE_ERROR,
                message_template="数据库操作失败: {operation}",
                description="PostgreSQL访问出错",
                solution="检查数据库连接池状态、网络延迟、慢查询日志",
            ),
            ErrorMetadata(
                code=ErrorCode.REDIS_ERROR,
                message_template="Redis操作失败: {operation}",
                description="Redis缓存访问出错",
                solution="检查Redis服务状态、连接数限制、内存使用率",
            ),
        ]
        
        for meta in builtin_errors:
            self._registry[meta.code.value] = meta
    
    def register(self, metadata: ErrorMetadata) -> None:
        """注册自定义错误"""
        self._registry[metadata.code.value] = metadata
    
    def get(self, code: ErrorCode) -> Optional[ErrorMetadata]:
        """获取错误元数据"""
        return self._registry.get(code.value)
    
    def to_problem_details(self, error: 'BusinessError') -> Dict[str, Any]:
        """
        转换为RFC 7807 Problem Details格式.
        
        参考: https://tools.ietf.org/html/rfc7807
        """
        meta = self.get(error.code)
        
        result: Dict[str, Any] = {
            'type': f'urn:agvtms:error:{error.code.value}',
            'title': meta.message_template if meta else str(error.code.name),
            'status': error.http_status,
            'detail': error.message,
            'code': error.code.value,
            'instance': error.instance,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        }
        
        if error.details:
            result['details'] = error.details
        
        if meta and meta.solution:
            result['solution'] = meta.solution
        
        if error.trace_id:
            result['trace_id'] = error.trace_id
        
        return result
    
    def list_all_codes(self, module_filter: Optional[str] = None) -> List[Dict]:
        """列出所有错误码 (用于文档生成)"""
        codes = []
        for code_val, meta in sorted(self._registry.items()):
            if module_filter and not str(code_val).startswith(module_filter.replace('x', '')):
                continue
            
            codes.append({
                'code': code_val,
                'name': meta.code.name,
                'http_status': meta.code.http_status,
                'message_template': meta.message_template,
                'retryable': meta.code.retryable,
                'severity': meta.severity,
            })
        
        return codes


# ==================== 业务异常类 ====================

class BusinessError(Exception):
    """
    业务异常 — 所有业务逻辑错误的基类.
    
    使用示例:
        raise BusinessError(
            code=ErrorCode.TASK_NOT_FOUND,
            message="任务不存在",
            details={"task_id": "T-001"},
            trace_id="req-abc123",
        )
    """
    
    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
        instance: Optional[str] = None,
        trace_id: Optional[str] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message or (code.name.replace('_', ' '))
        self.details = details or {}
        self.instance = instance
        self.trace_id = trace_id
        self.cause = cause
    
    @property
    def http_status(self) -> int:
        """HTTP响应状态码"""
        return self.code.http_status
    
    @property
    def is_retryable(self) -> bool:
        """是否可自动重试"""
        return self.code.retryable
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典 (用于JSON响应)"""
        return error_registry.to_problem_details(self)
    
    def __repr__(self) -> str:
        return (
            f"BusinessError(code={self.code.name}, "
            f"status={self.http_status}, "
            f"message='{self.message}')"
        )


# 便捷工厂方法
def raise_not_found(resource: str, resource_id: str, **kwargs) -> BusinessError:
    """快速抛出404异常"""
    code_map = {
        'task': ErrorCode.TASK_NOT_FOUND,
        'vehicle': ErrorCode.VEHICLE_NOT_FOUND,
        'node': ErrorCode.MAP_NODE_NOT_FOUND,
    }
    code = code_map.get(resource, ErrorCode.TASK_NOT_FOUND)
    return BusinessError(
        code=code,
        message=f"{resource} {resource_id} 不存在",
        details={f"{resource}_id": resource_id, **kwargs},
    )


def raise_conflict(message: str, **kwargs) -> BusinessError:
    """快速抛出409冲突异常"""
    return BusinessError(
        code=ErrorCode.TASK_STATUS_CONFLICT,
        message=message,
        details=kwargs,
    )


def raise_service_unavailable(service: str, reason: str = "") -> BusinessError:
    """快速抛出503不可用异常"""
    msg = f"{service} 服务暂时不可用"
    if reason:
        msg += f": {reason}"
    return BusinessError(
        code=ErrorCode.SERVICE_UNAVAILABLE,
        message=msg,
        details={'service': service, 'reason': reason},
    )


# ==================== FastAPI 集成 ====================

def setup_error_handlers(app) -> None:
    """
    注册全局异常处理器.
    
    在main.py中调用:
        from app.core.error_codes import setup_error_handlers
        setup_error_handlers(app)
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse
    
    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError):
        """业务异常统一处理"""
        return JSONResponse(
            status_code=exc.http_status,
            content={
                'code': f'{exc.code.value}',
                'message': exc.message,
                'data': None,
                'trace_id': exc.trace_id or request.headers.get('X-Request-ID', ''),
                'details': exc.details if exc.details else None,
            },
            headers={'X-Error-Code': str(exc.code.value)},
        )
    
    @app.exception_handler(ValueError)
    async def validation_error_handler(request: Request, exc: ValueError):
        """校验异常处理"""
        return JSONResponse(
            status_code=422,
            content=error_registry.to_problem_details(BusinessError(
                code=ErrorCode.VALIDATION_ERROR,
                message=str(exc),
                details={'validation_errors': exc.args},
            )),
        )
    
    logger = __import__('logging').getLogger(__name__)
    logger.info("Business error handlers registered")


# ==================== 全局单例 ====================
error_registry = ErrorRegistry()

__all__ = [
    'ErrorCode', 'BusinessError', 'ErrorRegistry', 'ErrorMetadata',
    'error_registry', 'setup_error_handlers',
    'raise_not_found', 'raise_conflict', 'raise_service_unavailable',
]
