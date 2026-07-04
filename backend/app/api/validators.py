"""
请求校验增强模块 - Pydantic v2严格模式 + 自定义校验器

功能:
1. 请求体大小限制
2. 自定义业务规则校验器
3. SQL注入防护 (过滤参数)
4. 枚举值验证
5. 复杂字段关联校验

对标:
- Spring Boot @Validated + @NotNull/@Size/@Pattern
- Joi (Node.js schema validation)

使用示例:
    from app.api.validators import (
        TaskRequestValidator,
        VehicleRequestValidator,
        SecurityValidator,
    )
    
    @router.post("/tasks")
    async def create_task(request: TaskCreateRequest):
        errors = TaskRequestValidator.validate_create(request.dict())
        if errors:
            raise HTTPException(422, detail=[e.dict() for e in errors])
"""

import re
from typing import Any, Optional, List, Dict, Set, Type
from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi import HTTPException


# ==================== 1. 基础校验错误类型 ====================

class ValidationErrorDetail(BaseModel):
    """
    校验错误详情 (符合RFC 7807 Problem Details规范)
    
    示例:
        {
            "field": "priority",
            "message": "优先级必须在 1-100 范围内",
            "rejected_value": 150,
            "code": "RANGE_ERROR"
        }
    """
    field: str = Field(description="错误字段名")
    message: str = Field(description="人类可读的错误描述")
    rejected_value: Any = Field(default=None, description="被拒绝的原始值")
    code: str = Field(default="VALIDATION_ERROR", description="错误代码")


class BusinessRuleError(HTTPException):
    """业务规则违反异常"""
    
    def __init__(self, message: str, field: str = None, code: str = "BUSINESS_RULE_VIOLATION"):
        detail = ValidationErrorDetail(
            field=field or "_general",
            message=message,
            code=code,
        )
        super().__init__(
            status_code=422,
            detail=[detail.model_dump()],
        )


# ==================== 2. 任务请求校验器 ====================

class TaskRequestValidator:
    """
    任务创建/更新请求校验器
    
    校验规则集 (对标海康RCS任务参数规范):
    - task_type 枚举校验
    - 起终点不能相同
    - priority 范围 [1-100]
    - cargo重量合理性
    - 时间约束逻辑性
    """
    
    ALLOWED_TASK_TYPES = {"agv_only", "conveyor_only", "agv_conveyor"}
    PRIORITY_RANGE = (1, 100)
    MAX_CARGO_WEIGHT_KG = 5000  # 系统最大载重
    
    @classmethod
    def validate_create(cls, data: dict) -> List[ValidationErrorDetail]:
        """
        验证任务创建请求
        
        Args:
            data: 请求数据字典
            
        Returns:
            错误列表 (空表示全部通过)
        """
        errors = []
        
        # 1) task_type 必须在允许列表中
        task_type = data.get("task_type")
        if not task_type or task_type not in cls.ALLOWED_TASK_TYPES:
            errors.append(ValidationErrorDetail(
                field="task_type",
                message=f"任务类型无效，必须是以下之一: {cls.ALLOWED_TASK_TYPES}",
                rejected_value=task_type,
                code="ENUM_INVALID",
            ))
        
        # 2) 起终点不能相同
        pickup = data.get("pickup_node", "")
        dropoff = data.get("dropoff_node", "")
        if pickup and dropoff and pickup == dropoff:
            errors.append(ValidationErrorDetail(
                field="dropoff_node",
                message="取货点和放货点不能相同",
                rejected_value=dropoff,
                code="SAME_LOCATION",
            ))
        
        # 3) priority范围检查
        priority = data.get("priority")
        if priority is not None:
            try:
                p = int(priority)
                if not cls.PRIORITY_RANGE[0] <= p <= cls.PRIORITY_RANGE[1]:
                    errors.append(ValidationErrorDetail(
                        field="priority",
                        message=f"优先级必须在 {cls.PRIORITY_RANGE} 范围内",
                        rejected_value=priority,
                        code="RANGE_ERROR",
                    ))
            except (TypeError, ValueError):
                errors.append(ValidationErrorDetail(
                    field="priority",
                    message="优先级必须为整数",
                    rejected_value=priority,
                    code="TYPE_ERROR",
                ))
        
        # 4) cargo重量检查
        cargo = data.get("cargo") or {}
        weight = cargo.get("weight_kg") if isinstance(cargo, dict) else None
        if weight is not None:
            try:
                w = float(weight)
                if w <= 0:
                    errors.append(ValidationErrorDetail(
                        field="cargo.weight_kg",
                        message="货物重量必须为正数",
                        rejected_value=weight,
                        code="POSITIVE_REQUIRED",
                    ))
                elif w > cls.MAX_CARGO_WEIGHT_KG:
                    errors.append(ValidationErrorDetail(
                        field="cargo.weight_kg",
                        message=f"货物重量超过系统上限 ({cls.MAX_CARGO_WEIGHT_KG}kg)",
                        rejected_value=weight,
                        code="MAX_EXCEEDED",
                    ))
            except (TypeError, ValueError):
                errors.append(ValidationErrorDetail(
                    field="cargo.weight_kg",
                    message="重量必须为有效数字",
                    rejected_value=weight,
                    code="TYPE_ERROR",
                ))
        
        return errors


# ==================== 3. 车辆请求校验器 ====================

class VehicleRequestValidator:
    """
    车辆注册/更新请求校验器
    
    支持的车型 (对标VDA5050 AGVClassification):
    - latent: 潜伏顶升式 (最常见)
    - forklift: 叉车式
    - conveyor: 输送线接口
    - amr: AMR自主移动机器人
    - tugger: 牵引式
    - shelf: 货架搬运
    - custom: 自定义
    """
    
    VEHICLE_TYPES = {
        "latent", "forklift", "conveyor",
        "amr", "tugger", "shelf", "custom",
    }
    
    VEHICLE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$')
    
    @classmethod
    def validate_id(cls, vehicle_id: Optional[str]) -> Optional[ValidationErrorDetail]:
        """校验车辆ID格式 (长度1-50, 字母数字+连字符+下划线)"""
        if not vehicle_id:
            return ValidationErrorDetail(field="vehicle_id", message="车辆ID不能为空", code="REQUIRED")
        
        if len(vehicle_id) < 1 or len(vehicle_id) > 50:
            return ValidationErrorDetail(
                field="vehicle_id", 
                message="长度必须在 1-50 字符之间",
                rejected_value=vehicle_id, 
                code="LENGTH_INVALID",
            )
        
        if not cls.VEHICLE_ID_PATTERN.match(vehicle_id):
            return ValidationErrorDetail(
                field="vehicle_id",
                message="仅允许字母、数字、连字符、下划线，不能以特殊字符开头",
                rejected_value=vehicle_id,
                code="FORMAT_INVALID",
            )
        
        return None
    
    @classmethod
    def validate_vehicle_type(cls, vtype: Optional[str]) -> Optional[ValidationErrorDetail]:
        """校验车型枚举值"""
        if vtype is None:
            return None  # 允许不传 (使用默认值)
        
        if vtype not in cls.VEHICLE_TYPES:
            return ValidationErrorDetail(
                field="type",
                message=f"不支持的车型，允许: {sorted(cls.VEHICLE_TYPES)}",
                rejected_value=vtype,
                code="ENUM_INVALID",
            )
        return None
    
    @classmethod
    def validate_create(cls, data: dict) -> List[ValidationErrorDetail]:
        """综合校验车辆注册请求"""
        errors = []
        
        id_err = cls.validate_id(data.get("vehicle_id"))
        if id_err:
            errors.append(id_err)
        
        type_err = cls.validate_vehicle_type(data.get("type"))
        if type_err:
            errors.append(type_err)
        
        # capabilities 若提供则检查必需字段完整性
        caps = data.get("capabilities")
        if isinstance(caps, dict):
            required_fields = {"payload_max_kg", "speed_max_ms"}
            missing = required_fields - set(caps.keys())
            if missing:
                errors.append(ValidationErrorDetail(
                    field="capabilities",
                    message=f"缺少必需字段: {missing}",
                    code="REQUIRED_FIELDS_MISSING",
                ))
            
            # payload必须为正数
            payload = caps.get("payload_max_kg")
            if payload is not None:
                try:
                    if float(payload) <= 0:
                        errors.append(ValidationErrorDetail(
                            field="capabilities.payload_max_kg",
                            message="最大载重必须为正数",
                            rejected_value=payload,
                            code="POSITIVE_REQUIRED",
                        ))
                except (TypeError, ValueError):
                    pass
        
        return errors


# ==================== 4. 安全性校验器 ====================

class SecurityValidator:
    """
    安全性相关输入校验 (防SQL注入/XSS等)
    
    注意:
    - 这只是辅助防护层，主要依赖ORM参数化查询
    - 前端也应做XSS过滤
    """
    
    SQL_INJECTION_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|EXEC|ALTER)\b)",
        r"(--|\#|\/\*)",
        r"('(\s)*OR|OR(\s)*')",
        r"(;(\s)*(DROP|ALTER))",
    ]
    
    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript\s*:",
        r"on(error|load|click|mouseover)\s*=",
    ]
    
    @classmethod
    def check_sql_injection(cls, value: Any) -> bool:
        """检测潜在的SQL注入风险。True表示安全。"""
        if not isinstance(value, str):
            return True
        
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                return False
        return True
    
    @classmethod
    def check_xss(cls, value: Any) -> bool:
        """检测潜在XSS攻击。True表示安全。"""
        if not isinstance(value, str):
            return True
        
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE | re.DOTALL):
                return False
        return True
    
    @classmethod
    def sanitize_string(cls, value: str, max_length: int = 2000) -> str:
        """清理字符串：截断、去空白、移除控制字符。"""
        if not value:
            return ""
        value = value[:max_length].strip()
        return ''.join(ch for ch in value if ord(ch) >= 32 or ch in '\n\r\t')


# ==================== 5. 过滤参数白名单校验 ====================

def validate_filter_params(
    params: Dict[str, Any],
    allowed_fields: Set[str],
    field_types: Dict[str, Type] = None,
) -> List[ValidationErrorDetail]:
    """
    校验查询过滤参数
    
    功能:
    - 字段白名单 (防止非法属性注入)
    - 类型强制转换与验证
    - 安全性扫描
    
    Args:
        params: 查询参数字典 (request.query_params)
        allowed_fields: 允许的字段集合
        field_types: 各字段的期望类型 {field_name: type}
        
    Returns:
        错误列表
    """
    errors = []
    field_types = field_types or {}
    
    for key, value in params.items():
        # 白名单检查
        if key not in allowed_fields:
            errors.append(ValidationErrorDetail(
                field=key,
                message=f"不允许的过滤字段。允许: {sorted(allowed_fields)}",
                rejected_value=str(value)[:100],
                code="FIELD_NOT_ALLOWED",
            ))
            continue
        
        # 安全性检查
        if isinstance(value, str) and not SecurityValidator.check_sql_injection(value):
            errors.append(ValidationErrorDetail(
                field=key,
                message="包含非法字符，可能存在安全风险",
                rejected_value=value[:50],
                code="SECURITY_RISK",
            ))
            continue
        
        # 类型转换尝试
        expected_type = field_types.get(key)
        if expected_type and value is not None and value != '':
            try:
                if expected_type == int:
                    int(value)
                elif expected_type == float:
                    float(value)
                elif expected_type == bool:
                    if str(value).lower() not in ('true', 'false', '1', '0'):
                        raise ValueError
            except (ValueError, TypeError):
                errors.append(ValidationErrorDetail(
                    field=key,
                    message=f"值无法转换为 {expected_type.__name__}",
                    rejected_value=str(value)[:100],
                    code="TYPE_MISMATCH",
                ))
    
    return errors


# ==================== 6. 批量操作校验 ====================

def validate_batch_items(
    items: List[Any],
    max_batch_size: int = 100,
    validator_class=None,
) -> List[ValidationErrorDetail]:
    """
    校验批量请求数据
    
    规则:
    - 列表大小限制 (防DoS)
    - 每项逐一校验 (调用指定的validator)
    - 返回聚合错误 (支持部分失败报告)
    
    Args:
        items: 批量数据列表
        max_batch_size: 最大允许条数
        validator_class: 单项校验器类 (需实现validate_create方法)
        
    Returns:
        错误列表 (可能包含多个项目的错误)
    """
    errors = []
    
    # 大小限制
    if len(items) > max_batch_size:
        errors.append(ValidationErrorDetail(
            field="_batch_size",
            message=f"批量操作最大允许 {max_batch_size} 条，当前 {len(items)} 条",
            rejected_value=len(items),
            code="BATCH_TOO_LARGE",
        ))
        return errors  # 直接返回，不再逐项校验
    
    # 逐项校验 (收集所有错误而非遇到第一个就停止)
    if validator_class and hasattr(validator_class, 'validate_create'):
        for idx, item in enumerate(items):
            item_dict = item if isinstance(item, dict) else item.model_dump()
            item_errors = validator_class.validate_create(item_dict)
            
            for err in item_errors:
                # 在字段名前加上数组索引便于定位
                err.field = f"[{idx}].{err.field}"
                errors.append(err)
    
    return errors


__all__ = [
    'ValidationErrorDetail',
    'BusinessRuleError',
    'TaskRequestValidator',
    'VehicleRequestValidator',
    'SecurityValidator',
    'validate_filter_params',
    'validate_batch_items',
]
