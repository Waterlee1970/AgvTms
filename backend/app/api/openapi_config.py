"""
OpenAPI 3.0 规范增强配置 - 对标Swagger/ReDoc最佳实践

功能:
1. 统一的Tags分组定义 (含图标和描述)
2. API安全定义 (Bearer Token / API Key)
3. 全局响应示例 (成功/错误)
4. 自定义Schema扩展
5. 版本化文档管理

使用方式:
    from app.api.openapi_config import custom_openapi_schema
    
    app = FastAPI(openapi_function=custom_openapi_schema)
"""

from typing import Dict, Any, Optional, List
from fastapi import FastAPI


# ==================== 1. API Tags 定义 ====================

OPENAPI_TAGS = [
    {
        "name": "Tasks",
        "description": "任务管理 | 创建、查询、取消运输任务",
        "externalDocs": {
            "url": "/docs#/default/Tasks",
            "description": "查看所有任务相关接口",
        },
    },
    {
        "name": "Vehicles",
        "description": "车辆管理 | AGV注册、状态监控、急停控制",
    },
    {
        "name": "Map",
        "description": "地图管理 | 拓扑节点、路径边、区域管制",
    },
    {
        "name": "Scheduling",
        "description": "调度引擎 | 手动触发、算法切换、历史记录",
    },
    {
        "name": "VDA5050",
        "description": "VDA5050协议 | 国际AGV通信标准接口",
        "externalDocs": {
            "url": "https://vda5050.org/",
            "description": "VDA5050官方规范文档",
        },
    },
    {
        "name": "Analytics",
        "description": "数据分析 | 统计报表、性能指标、异常告警",
    },
    {
        "name": "Simulation",
        "description": "仿真引擎 | 场景模拟、压力测试、算法对比",
    },
    {
        "name": "Industrial Integration",
        "description": "工业集成 | OPC UA、WMS/MES对接",
    },
    {
        "name": "Monitoring",
        "description": "系统监控 | 健康检查、Prometheus指标、系统信息",
    },
    {
        "name": "Advanced Features",
        "description": "高级功能 | 分布式调度、数字孪生、AB测试",
    },
]


# ==================== 2. 安全定义 ====================

SECURITY_SCHEMES = {
    "bearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT Bearer Token认证 (从Login接口获取)",
    },
    "apiKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API密钥认证 (适用于系统集成场景)",
    },
}


# ==================== 3. 全局响应示例 ====================

EXAMPLE_RESPONSES: Dict[str, Dict] = {
    # 成功响应示例
    "success_create_task": {
        "code": "201",
        "message": "任务创建成功",
        "data": {
            "task_id": "task-20240715-001",
            "task_type": "agv_only",
            "status": "pending",
            "pickup_node": "WH_A",
            "dropoff_node": "LINE_B",
            "priority": 10,
            "created_at": "2024-07-15T10:30:00Z",
        },
        "trace_id": "req-abc123xyz789",
        "timestamp": 1721039400.123,
    },
    
    # 分页响应示例
    "paginated_list": {
        "code": "200",
        "message": "查询成功",
        "data": {
            "items": [...],  # 实际数据列表
            "total": 150,
            "page": 1,
            "page_size": 20,
            "total_pages": 8,
        },
        "trace_id": "req-def456uvw012",
        "timestamp": 1721039410.456,
    },
    
    # 错误响应示例
    "error_validation": {
        "code": "400",
        "message": "请求参数校验失败",
        "data": [
            {"field": "priority", "message": "优先级必须在 1-100 范围内", "code": "RANGE_ERROR"},
        ],
        "trace_id": "req-err789ghi345",
        "timestamp": 1721039420.789,
    },
    
    "error_not_found": {
        "code": "404",
        "message": "资源不存在",
        "data": None,
        "trace_id": "req-nf012jkl678",
        "timestamp": 1721039430.012,
    },
    
    "error_rate_limited": {
        "code": "429",
        "message": "请求过于频繁，请稍后重试",
        "data": None,
        "extra": {"retry_after": 30.0},
        "trace_id": "req-rl345mno901",
        "timestamp": 1721039440.345,
    },
    
    "error_server": {
        "code": "500",
        "message": "服务器内部错误",
        "data": None,
        "trace_id": "req-svr567pqr234",
        "timestamp": 1721039450.678,
    },
}


# ==================== 4. 自定义OpenAPI Schema函数 ====================

def custom_openapi_schema(app: FastAPI) -> Dict[str, Any]:
    """
    自定义OpenAPI Schema生成器
    
    功能:
    - 注入统一的Tags元数据
    - 添加安全定义
    - 补充全局描述
    - 设置服务器URLs
    """
    if not app.openapi_schema:
        # 获取基础schema (FastAPI自动生成)
        openapi_schema = getattr(app, 'openapi')()
        
        # 1) 更新Info对象
        openapi_schema["info"] = {
            **openapi_schema.get("info", {}),
            "title": "AgvTms API v3.0 - 智能物流柔性调度系统",
            "description": """
## 🚀 AgvTms RESTful API 文档

### 核心能力
- **多车型支持**: 7种AGV类型 + 输送线混合调度
- **双引擎算法**: ACO+SA (V1原型级) / MIP+A*+SIPP (V2工业级)
- **协议适配**: MQTT / OPC UA / VDA5050
- **实时监控**: WebSocket推送 + Prometheus指标

### 认证方式
1. **Bearer Token** (推荐): `Authorization: Bearer <jwt_token>`
2. **API Key** (系统集成): `X-API-Key: <your_key>`

### 统一响应格式
所有API返回统一结构:
```json
{
  "code": "200",          // 业务状态码 (非HTTP状态码)
  "message": "操作成功",   // 人类可读消息
  "data": { ... },        // 业务数据载荷
  "trace_id": "abc123",   // 请求追踪ID (用于调试)
  "timestamp": 1721039400 // 服务端时间戳
}
```

### 错误码说明
| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 202 | 已接受(异步处理中) |
| 400 | 参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 422 | 校验失败 (详见errors数组) |
| 429 | 请求过于频繁 (见Retry-After头) |
| 500/502/503 | 服务端错误 |

### 版本历史
- **v1.0** (2024-01): 基础功能上线
- **v2.0** (2024-03): V2工业级算法 + VDA5050
- **v3.0** (2024-07): 工程化重构 + API专业化

---

📖 更多文档: [产品推广](/docs/product-promotion.md) | [开发者指南](/docs/developer-integration-guide.md)
""",
            "version": "3.0.0",
            "contact": {
                "name": "AgvTms 技术支持",
                "email": "support@agvtms.example.com",
                "url": "https://github.com/example/agvtms",
            },
            "license": {
                "name": "MIT License",
                "url": "https://opensource.org/licenses/MIT",
            },
        }
        
        # 2) 注入Tags
        if "tags" not in openapi_schema or not openapi_schema["tags"]:
            openapi_schema["tags"] = OPENAPI_TAGS
        
        # 3) 添加安全定义
        openapi_schema["components"]["securitySchemes"] = SECURITY_SCHEMES
        
        # 4) 为所有端点添加全局安全要求 (除了公开的health/docs等)
        for path_item in openapi_schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    path = str(operation.get('operationId', ''))
                    # 公开端点不需要认证
                    is_public = any(
                        public_path in path 
                        for public_path in ['health', 'root', 'metrics', 'docs', 'openapi']
                    )
                    if not is_public:
                        operation.setdefault("security", [{"bearerAuth": []}])
                        # 同时支持API Key
                        # operation["security"].append({"apiKeyAuth": []})
        
        # 5) 添加Servers定义
        openapi_schema["servers"] = [
            {"url": "http://localhost:8000", "description": "本地开发环境"},
            {"url": "https://api-dev.agvtms.com", "description": "开发测试环境"},
            {"url": "https://api.agvtms.com", "description": "生产环境"},
        ]
        
        # 6) 扩展字段 (用于自定义工具)
        openapi_schema["x-tagGroups"] = [
            {"name": "核心业务", "tags": ["Tasks", "Vehicles", "Map"]},
            {"name": "调度算法", "tags": ["Scheduling", "Simulation", "Analytics"]},
            {"name": "协议集成", "tags": ["VDA5050", "Industrial Integration"]},
            {"name": "运维监控", "tags": ["Monitoring", "Advanced Features"]},
        ]
        
        # 缓存结果
        app.openapi_schema = openapi_schema
    
    return app.openapi_schema


# ==================== 5. 导出工具函数 ====================

def get_openapi_example(key: str) -> Dict[str, Any]:
    """获取预定义的响应示例"""
    return EXAMPLE_RESPONSES.get(key, EXAMPLE_RESPONSES["error_server"])


__all__ = [
    'OPENAPI_TAGS',
    'SECURITY_SCHEMES',
    'EXAMPLE_RESPONSES',
    'custom_openapi_schema',
    'get_openapi_example',
]
