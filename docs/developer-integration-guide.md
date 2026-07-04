# AgvTms 开发者集成技术手册

> **目标读者**: 系统集成商 / 平台开发商 / 内部IT团队  
> **前置知识**: Python基础 / REST API概念 / Docker容器化  
> **配套资源**: [API在线文档](http://localhost:8000/docs) | [Postman集合](./assets/agvtms-api.postman_collection.json)

---

## 一、系统架构总览

### 1.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        集成方业务系统层                              │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│   │   WMS    │  │   MES    │  │   ERP    │  │   Custom System   │   │
│   │ 仓储管理  │  │  制造执行  │  │  资源规划  │  │   自定义业务逻辑   │   │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │
└────────┼─────────────┼─────────────┼─────────────────┼─────────────┘
         │             │             │                 │
         ▼             ▼             ▼                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      AgvTms 集成接口层                              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    API Gateway (Nginx)                      │   │
│  │  - 反向代理 / 负载均衡 / SSL终结 / 限流熔断                  │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                             │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │                  FastAPI Application                        │   │
│  │                                                             │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │
│  │  │  REST API   │  │  WebSocket  │  │   Event Bus (Kafka) │  │   │
│  │  │  (同步调用)  │  │  (实时推送)  │  │   (异步事件流)      │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │   │
│  └─────────┼────────────────┼────────────────────┼─────────────┘   │
│            │                │                    │                 │
│  ┌─────────▼────────────────▼────────────────────▼─────────────┐   │
│  │                    业务逻辑层                                │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐  │   │
│  │  │ Task Svc │ │VehicleSvc│ │Map Svc   │ │ Schedule Engine │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────────────┘  │   │
│  └────────────────────────────────────────────────────────────┘   │
│                             │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │                     适配器层 (Adapter Layer)                 │   │
│  │                                                             │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │   │
│  │  │ MQTT Adapter │  │ OPC UA Adapter│  │ HTTP Adapter     │  │   │
│  │  │ (AGV实时控制) │  │ (PLC/传感器)  │  │ (第三方REST)     │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────┘  │   │
│  └────────────────────────────────────────────────────────────┘   │
│                             │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐   │
│  │                     设备层 (Device Layer)                     │   │
│  │   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐  │   │
│  │   │  AGV集群 │ │ 输送线  │ │  电梯   │ │  其他IoT设备    │  │   │
│  │   └─────────┘ └─────────┘ └─────────┘ └─────────────────┘  │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                     基础设施层                              │   │
│  │   PostgreSQL │ Redis (Cache) │ InfluxDB (TSDB) │ Grafana   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 技术栈选型说明

| 组件 | 选型理由 | 替代方案 | 版本要求 |
|------|---------|---------|---------|
| **Python 3.10+** | 异步IO生态丰富 | Go/Rust (性能敏感场景) | ≥3.10 |
| **FastAPI** | 自动OpenAPI文档 + 高性能异步 | Flask/Django/FastAPI | ≥0.100 |
| **Pydantic v2** | 数据校验 + JSON序列化性能提升2x | Marshmallow/Dataclasses | ≥2.0 |
| **SQLAlchemy 2.0** | 异步ORM + 类型安全 | Tortoise ORM/Databases | ≥2.0 |
| **Redis** | 缓存 + 发布订阅 + 分布式锁 | Memcached (简单场景) | ≥7.0 |
| **PostgreSQL** | 复杂查询 + JSONB支持 | MySQL 8.0 (已有环境) | ≥14 |
| **InfluxDB** | 时序数据存储 (AGV轨迹) | TimescaleDB (Postgres插件) | ≥2.0 |
| **Kafka** | 高吞吐事件流 (可选) | RabbitMQ/NATS (轻量替代) | ≥3.0 |

---

## 二、API接口规范

### 2.1 统一响应格式

所有API遵循统一的 `Result<T>` 响应结构（对标海康RCS）:

**成功响应**:
```json
{
  "code": 200,
  "message": "操作成功",
  "data": {
    "task_id": "task-uuid-12345",
    "status": "PENDING",
    "created_at": "2026-07-05T09:30:00Z"
  },
  "trace_id": "req-abc123xyz",
  "timestamp": "2026-07-05T09:30:00.123Z"
}
```

**错误响应**:
```json
{
  "code": 400,
  "message": "参数错误: agv_id不能为空",
  "data": null,
  "trace_id": "req-def456uvw",
  "errors": [
    {
      "field": "agv_id",
      "message": "This field is required"
    }
  ],
  "timestamp": "2026-07-05T09:30:00.456Z"
}
```

**分页响应**:
```json
{
  "code": 200,
  "message": "查询成功",
  "data": {
    "items": [...],
    "pagination": {
      "total": 150,
      "page": 1,
      "page_size": 20,
      "total_pages": 8
    }
  },
  "trace_id": "req-ghi789rst",
  "timestamp": "2026-07-05T09:30:00.789Z"
}
```

### 2.2 标准错误码体系

| 错误码范围 | 类别 | 示例 |
|-----------|------|------|
| **200** | 成功 | 操作成功 |
| **201** | 创建成功 | 资源已创建 |
| **400** | 参数错误 | 字段缺失/格式非法/业务校验失败 |
| **401** | 未认证 | Token过期/无效 |
| **403** | 无权限 | 权限不足 |
| **404** | 资源不存在 | 任务ID/车辆ID未找到 |
| **409** | 冲突 | 资源状态冲突(如重复取消) |
| **422** | 业务规则违反 | AGV电量不足/任务超时 |
| **429** | 请求过频 | 触发限流阈值 |
| **500** | 服务内部错误 | 数据库异常/未知错误 |
| **503** | 服务不可用 | 调度引擎过载/维护中 |

### 2.3 请求头规范

```http
# 必需头部
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...  # JWT Token
Content-Type: application/json
X-Request-ID: uuid-unique-per-request           # 请求追踪ID(可选,系统会自动生成)
X-Tenant-ID: factory-001                         # 租户ID(多租户场景必需)

# 可选头部
Accept-Language: zh-CN                            # 国际化语言偏好
If-None-Match: "etag-value"                       # 条件GET(缓存协商)
Prefer: return=minimal                            # 最小化响应体(PATCH场景)
```

---

## 三、核心API端点详解

### 3.1 任务管理 API

#### 创建搬运任务

```http
POST /api/v3/tasks
Content-Type: application/json

{
  // 基本信息
  "task_type": "agv_only",           // agv_only | conveyor_only | mixed
  "priority": 10,                    // 0-255, 数值越大越优先
  
  // 路径信息
  "pickup_node": "warehouse_A",      // 取货节点ID
  "dropoff_node": "workstation_B",   // 卸货节点ID
  
  // 物料信息 (可选)
  "cargo": {
    "weight_kg": 50.5,
    "dimensions": {
      "length_cm": 60,
      "width_cm": 40,
      "height_cm": 30
    },
    "sku": "PART-A001",
    "quantity": 10
  },
  
  // 约束条件 (可选)
  "constraints": {
    "deadline": "2026-07-05T12:00:00Z",  // 截止时间
    "required_vehicle_type": "latent",     // 指定车型
    "avoid_zones": ["zone_maintenance"]   // 禁行区域
  },
  
  // 回调通知 (可选)
  "webhook_url": "https://your-system.example.com/callbacks/task-status"
}

Response 201:
{
  "code": 201,
  "data": {
    "task_id": "task-20260705-abc123",
    "status": "PENDING",
    "estimated_duration_seconds": 180,
    "created_at": "2026-07-05T09:30:00Z"
  }
}
```

#### 批量创建任务

```http
POST /api/v3/tasks/batch
Content-Type: application/json

{
  "tasks": [
    {
      "pickup_node": "A",
      "dropoff_node": "B",
      "priority": 5
    },
    {
      "pickup_node": "C",
      "dropoff_node": "D",
      "priority": 8
    }
  ],
  "mode": "atomic"  // atomic(全部成功或失败) | best_effort(部分成功)
}

Response 200:
{
  "code": 200,
  "data": {
    "total": 2,
    "succeeded": 2,
    "failed": 0,
    "results": [
      { "index": 0, "task_id": "task-001", "status": "success" },
      { "index": 1, "task_id": "task-002", "status": "success" }
    ]
  }
}
```

#### 查询任务列表 (分页)

```http
GET /api/v3/tasks?page=1&page_size=20&status=PENDING&sort_by=-created_at

Query Parameters:
- page: 页码 (默认1)
- page_size: 每页数量 (默认20, 最大100)
- status: 过滤状态 (PENDING/ASSIGNED/IN_PROGRESS/COMPLETED/FAILED/CANCELLED)
- vehicle_id: 按车辆过滤
- priority_min/priority_max: 优先级范围
- created_after/created_before: 时间范围
- sort_by: 排序字段 (-前缀表示降序)

Response 200:
{
  "code": 200,
  "data": {
    "items": [...],
    "pagination": {
      "total": 150,
      "page": 1,
      "page_size": 20,
      "total_pages": 8
    }
  }
}
```

### 3.2 车队管理 API

#### 注册新车辆

```http
POST /api/v3/vehicles
Content-Type: application/json

{
  "vehicle_id": "agv-latent-001",
  "type": "latent",                    // standard/forklift/latent/lift/sorter/towing/custom
  "name": "潜伏顶升AGV-01号",
  
  // 能力配置
  "capabilities": {
    "payload_max_kg": 300,
    "speed_max_ms": 1.2,
    "navigation_types": ["qr_code", "slam"],
    "can_dock_conveyor": true,
    "narrow_aisle_capable": false
  },
  
  // 初始位置
  "initial_position": {
    "node_id": "charging_station_1",
    "orientation_deg": 0,
    "battery_percent": 95
  },
  
  // 元数据
  "metadata": {
    "manufacturer": "ExampleRobotics",
    "model": "LatentPro-V2",
    "firmware_version": "2.1.0"
  }
}

Response 201:
{
  "code": 201,
  "data": {
    "vehicle_id": "agv-latent-001",
    "registration_time": "2026-07-05T09:30:00Z"
  }
}
```

#### 获取车队状态总览

```http
GET /api/v3/fleet/status

Response 200:
{
  "code": 200,
  "data": {
    "summary": {
      "total_vehicles": 25,
      "online": 22,
      "offline": 2,
      "error": 1,
      "avg_battery_percent": 68.5
    },
    "by_status": {
      "IDLE": 8,
      "MOVING": 10,
      "CHARGING": 3,
      "EXECUTING": 1,
      "WAITING": 0,
      "ERROR": 1,
      "OFFLINE": 2
    },
    "by_type": {
      "latent": { "count": 15, "online": 13 },
      "forklift": { "count": 6, "online": 6 },
      "lift": { "count": 4, "online": 3 }
    },
    "alerts": [
      {
        "level": "warning",
        "vehicle_id": "agv-forklift-003",
        "message": "电池电量低于20%",
        "battery_percent": 18
      }
    ]
  }
}
```

### 3.3 调度引擎 API

#### 手动触发调度

```http
POST /api/v3/dispatcher/run
Content-Type: application/json

{
  // 调度模式
  "mode": "full",              // full | incremental | emergency
  
  // 范围过滤 (可选)
  "filter": {
    "task_ids": ["task-001", "task-002"],
    "vehicle_ids": ["agv-001", "agv-002"],
    "zone_ids": ["zone_production"]
  },
  
  // 优化目标权重 (可选)
  "objectives": {
    "minimize_makespan": 0.4,     // 最小化完成时间
    "minimize_distance": 0.3,     // 最小化行驶距离
    "balance_workload": 0.2,      // 负载均衡
    "minimize_energy": 0.1        // 节能优化
  },
  
  // 超时设置 (毫秒)
  "timeout_ms": 25000
}

Response 200:
{
  "code": 200,
  "data": {
    "schedule_id": "sched-20260705-001",
    "status": "COMPLETED",
    "duration_ms": 3200,
    
    "statistics": {
      "tasks_scheduled": 48,
      "vehicles_utilized": 22,
      "makespan_minutes": 45.2,
      "total_distance_km": 12.8,
      "estimated_energy_kwh": 8.5
    },
    
    "assignments": [
      {
        "task_id": "task-001",
        "vehicle_id": "agv-001",
        "path": ["node_A", "node_B", "node_C"],
        "start_time": "2026-07-05T09:32:00Z",
        "estimated_end_time": "2026-07-05T09:35:20Z"
      }
    ],
    
    "conflicts_detected": 2,
    "conflicts_resolved": 2,
    "warnings": []
  }
}
```

#### 获取实时调度状态 (WebSocket)

```javascript
// 客户端连接示例
const ws = new WebSocket('ws://localhost:8000/ws/v3/dispatcher/live');

ws.onopen = () => {
  // 订阅特定区域的事件
  ws.send(JSON.stringify({
    action: 'subscribe',
    channels: ['task_updates', 'vehicle_positions', 'system_alerts'],
    filters: {
      zone_id: 'zone_production'
    }
  }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  switch(message.type) {
    case 'task.status_changed':
      console.log(`Task ${message.data.task_id}: ${message.data.old_status} -> ${message.data.new_status}`);
      break;
      
    case 'vehicle.position_update':
      // 实时更新地图上的车辆位置
      updateVehicleMarker(message.data.vehicle_id, message.data.position);
      break;
      
    case 'schedule.completed':
      console.log(`Schedule ${message.data.schedule_id} finished in ${message.data.duration_ms}ms`);
      break;
      
    case 'alert':
      showAlertNotification(message.data);
      break;
  }
};
```

### 3.4 地图配置 API

#### 导入地图拓扑

```http
POST /api/v3/maps/import
Content-Type: application/json

{
  "map_id": "factory_floor_1F",
  "name": "一楼生产车间",
  "metadata": {
    "width_meters": 120,
    "height_meters": 80,
    "resolution_cm_per_pixel": 2
  },
  
  // 节点定义
  "nodes": [
    {
      "node_id": "pickup_zone_A",
      "type": "PICKUP",           // PICKUP/DROPOFF/CHARGE/CROSS/PATH/CONVEYOR_IN/CONVEYOR_OUT
      "position": { "x": 10.5, "y": 20.3 },
      "properties": {
        "capacity": 3,
        "allowed_vehicle_types": ["latent", "lift"]
      }
    }
  ],
  
  // 边定义 (可行走路径)
  "edges": [
    {
      "edge_id": "edge_A_to_B",
      "from_node": "pickup_zone_A",
      "to_node": "crossroad_01",
      "weight": 15.0,            // 距离(米)或通行时间(秒)
      "direction": "bidirectional", // bidirectional/oneway
      "properties": {
        "max_speed_ms": 1.0,
        "width_meters": 1.2
      }
    }
  ],
  
  // 区域定义 (用于交通管制)
  "zones": [
    {
      "zone_id": "zone_maintenance",
      "type": "restricted",       // restricted/charging/parking/production
      "polygon": [[x1,y1], [x2,y2], ...],
      "access_control": {
        "allowed_vehicles": [],
        "time_windows": []        // 或 ["09:00-17:00"] 表示时段限制
      }
    }
  ]
}

Response 201:
{
  "code": 201,
  "data": {
    "map_id": "factory_floor_1F",
    "nodes_count": 45,
    "edges_count": 78,
    "zones_count": 5,
    "validation_warnings": []
  }
}
```

---

## 四、协议适配器开发指南

### 4.1 适配器架构设计

AgvTms 采用**策略模式 + 依赖注入**实现协议解耦:

```
┌─────────────────────────────────────────────────────────────┐
│                    Adapter Interface                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ class BaseAdapter(ABC):                              │   │
│  │   async def connect() -> None: ...                   │   │
│  │   async def disconnect() -> None: ...                │   │
│  │   async def send_command(cmd: Command) -> Result: .. │   │
│  │   async def subscribe(topic: str, handler): ...      │   │
│  │   @property                                          │   │
│  │   def protocol_name(self) -> str: ...                │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         △                    △                    △
         │                    │                    │
┌────────┴────┐     ┌────────┴────┐     ┌────────┴────┐
│ MQTTAdapter │     │ OPCUAAdapter│     │  HttpAdapter │
└─────────────┘     └─────────────┘     └─────────────┘
```

### 4.2 自定义适配器开发示例

假设需要对接一款使用私有TCP协议的AGV:

**Step 1**: 创建适配器类文件 `backend/app/adapters/custom_agv_adapter.py`

```python
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional
from dataclasses import dataclass
import asyncio
import json
import logging

from app.adapters.base import BaseAdapter, CommandResult, ConnectionStatus

logger = logging.getLogger(__name__)


@dataclass
class CustomAgvConfig:
    """自定义AGV适配器配置"""
    host: str
    port: int = 8888
    device_id: str = ""
    reconnect_interval_sec: float = 5.0
    heartbeat_interval_sec: float = 10.0


class CustomAgvAdapter(BaseAdapter):
    """
    自定义TCP协议AGV适配器
    
    协议格式:
    - 命令帧: [0xAA][长度][命令码][Payload][CRC][0x55]
    - 响应帧: [0xBB][长度][状态码][Payload][CRC][0x55]
    """
    
    # === 协议常量 ===
    FRAME_HEADER_CMD = 0xAA
    FRAME_HEADER_RESP = 0xBB
    FRAME_FOOTER = 0x55
    
    # 命令码
    CMD_MOVE_TO = 0x01
    CMD_STOP = 0x02
    CMD_STATUS_QUERY = 0x03
    CMD_SET_SPEED = 0x04
    
    # 状态码
    STATUS_SUCCESS = 0x00
    STATUS_BUSY = 0x01
    STATUS_ERROR = 0xFF
    
    def __init__(self, config: CustomAgvConfig):
        super().__init__()
        self.config = config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._status_cache: dict = {}
        
    @property
    def protocol_name(self) -> str:
        return "custom_tcp_agv"
    
    async def connect(self) -> None:
        """建立TCP连接并初始化"""
        try:
            logger.info(f"[{self.protocol_name}] Connecting to {self.config.host}:{self.config.port}")
            
            self._reader, self._writer = await asyncio.open_connection(
                self.config.host, 
                self.config.port
            )
            
            # 启动心跳保活
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            # 启动消息接收循环
            asyncio.create_task(self._receive_loop())
            
            self._connection_status = ConnectionStatus.CONNECTED
            logger.info(f"[{self.protocol_name}] Connected successfully")
            
        except Exception as e:
            logger.error(f"[{self.protocol_name}] Connection failed: {e}")
            self._connection_status = ConnectionStatus.DISCONNECTED
            raise
            
    async def disconnect(self) -> None:
        """断开连接"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            
        self._connection_status = ConnectionStatus.DISCONNECTED
        logger.info(f"[{self.protocol_name}] Disconnected")
    
    async def send_command(
        self, 
        command: str, 
        params: dict[str, Any],
        timeout_sec: float = 10.0
    ) -> CommandResult:
        """发送命令并等待响应"""
        
        try:
            # 1. 将通用命令映射为协议特定格式
            frame = self._build_frame(command, params)
            
            # 2. 发送
            self._writer.write(frame)
            await self._writer.drain()
            logger.debug(f"[{self.protocol_name}] Sent command: {command}")
            
            # 3. 等待响应 (带超时)
            response = await asyncio.wait_for(
                self._wait_for_response(command),
                timeout=timeout_sec
            )
            
            return CommandResult(
                success=response['status'] == self.STATUS_SUCCESS,
                data=response.get('payload'),
                error_message=None if response['status'] == self.STATUS_SUCCESS else f"Error code: {response['status']}"
            )
            
        except asyncio.TimeoutError:
            logger.error(f"[{self.protocol_name}] Command timed out: {command}")
            return CommandResult(success=False, error_message="Command timeout")
        except Exception as e:
            logger.error(f"[{self.protocol_name}] Command failed: {e}")
            return CommandResult(success=False, error_message=str(e))
    
    async def subscribe(self, topic: str, callback: Callable[[dict], None]) -> None:
        """订阅设备状态变更"""
        # 对于TCP协议,所有消息通过_receive_loop统一处理
        # 这里注册回调函数供接收循环调用
        self._callbacks.setdefault(topic, []).append(callback)
        logger.info(f"[{self.protocol_name}] Subscribed to topic: {topic}")
    
    # === 私有方法 ===
    
    def _build_frame(self, command: str, params: dict) -> bytes:
        """构建协议帧"""
        cmd_map = {
            'move_to': self.CMD_MOVE_TO,
            'stop': self.CMD_STOP,
            'get_status': self.CMD_STATUS_QUERY,
            'set_speed': self.CMD_SET_SPEED,
        }
        
        cmd_code = cmd_map.get(command)
        if not cmd_code:
            raise ValueError(f"Unsupported command: {command}")
        
        payload = json.dumps(params).encode('utf-8')
        length = len(payload) + 2  # +cmd_code + status/crc
        
        frame = bytes([
            self.FRAME_HEADER_CMD,
            length,
            cmd_code,
        ]) + payload
        
        # 简单CRC (仅演示)
        crc = sum(frame) & 0xFF
        frame += bytes([crc, self.FRAME_FOOTER])
        
        return frame
    
    async def _receive_loop(self):
        """持续接收响应"""
        while self.is_connected:
            try:
                header = await self._reader.readexactly(2)  # header + length
                
                if header[0] != self.FRAME_HEADER_RESP:
                    continue
                    
                length = header[1]
                body = await self._reader.readexactly(length)
                
                status_code = body[0]
                payload = json.loads(body[1:-1].decode('utf-8')) if length > 2 else {}
                
                response = {'status': status_code, 'payload': payload}
                
                # 触发等待中的Future
                # (实际实现需要维护command_id -> Future映射表)
                
                # 如果是主动上报的状态更新,触发回调
                if status_code == self.STATUS_SUCCESS and 'position' in payload:
                    await self._emit_event('vehicle.position', payload)
                    
            except asyncio.IncompleteReadError:
                logger.warning(f"[{self.protocol_name}] Connection lost")
                break
            except Exception as e:
                logger.error(f"[{self.protocol_name}] Receive error: {e}")
                
        # 自动重连
        await self._auto_reconnect()
    
    async def _heartbeat_loop(self):
        """心跳保活"""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval_sec)
                await self.send_command('get_status', {})
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[{self.protocol_name}] Heartbeat failed: {e}")
    
    async def _auto_reconnect(self):
        """自动重连"""
        for attempt in range(5):
            logger.info(f"[{self.protocol_name}] Reconnecting (attempt {attempt+1})...")
            try:
                await asyncio.sleep(self.config.reconnect_interval_sec * (attempt + 1))
                await self.connect()
                return
            except Exception as e:
                logger.error(f"[{self.protocol_name}] Reconnect failed: {e}")
        
        self._connection_status = ConnectionStatus.DISCONNECTED
    
    async def _emit_event(self, event_type: str, data: dict):
        """触发事件回调"""
        callbacks = self._callbacks.get(event_type, [])
        for cb in callbacks:
            try:
                cb(data)
            except Exception as e:
                logger.error(f"[{self.protocol_name}] Callback error: {e}")
```

**Step 2**: 在 `backend/app/adapters/__init__.py` 注册适配器

```python
from .custom_agv_adapter import CustomAgvAdapter, CustomAgvConfig

__all__ = ['CustomAgvAdapter', 'CustomAgvConfig']
```

**Step 3**: 在配置文件中启用

```yaml
# config/settings.yaml
adapters:
  custom_agv:
    enabled: true
    class: "app.adapters.custom_agv_adapter.CustomAgvAdapter"
    config:
      host: "192.168.1.100"
      port: 8888
      device_id: "AGV-CUSTOM-001"
```

### 4.3 MQTT适配器高级用法

**发布车辆控制命令**:

```python
from app.adapters.mqtt_vehicle_adapter import MqttVehicleAdapter

adapter = MqttVehicleAdapter(broker_host="mqtt.example.com")

# 连接
await adapter.connect()

# 发送移动命令
result = await adapter.send_command("move_to", {
    "vehicle_id": "agv-001",
    "target": {"x": 10.5, "y": 20.3, "theta": 0},
    "speed": 1.0,
    "path": ["node_A", "node_B", "node_C"]
})

if result.success:
    print("Command accepted by AGV")
else:
    print(f"Failed: {result.error_message}")

# 订阅状态变更
async def on_status_change(data):
    print(f"Vehicle {data['vehicle_id']} status: {data['status']}")

await adapter.subscribe("vehicle/status/#", on_status_change)
```

---

## 五、Webhook事件通知

### 5.1 事件类型清单

| 事件名称 | 触发时机 | Payload关键字段 |
|---------|---------|---------------|
| `task.created` | 任务创建成功 | task_id, type, priority |
| `task.assigned` | 任务分配给车辆 | task_id, vehicle_id |
| `task.started` | 任务开始执行 | task_id, start_time |
| `task.completed` | 任务完成 | task_id, duration, actual_path |
| `task.failed` | 任务失败 | task_id, error_code, retry_count |
| `vehicle.online` | 车辆上线 | vehicle_id, ip, firmware_version |
| `vehicle.offline` | 车辆离线 | vehicle_id, last_seen |
| `vehicle.battery.low` | 电量低于阈值 | vehicle_id, battery_percent |
| `schedule.completed` | 调度完成 | schedule_id, statistics |
| `alert.fired` | 系统告警 | level, message, source |

### 5.2 Webhook配置

```http
PUT /api/v3/webhooks/config
Content-Type: application/json

{
  "endpoint_url": "https://your-system.example.com/webhooks/agvtms",
  "secret": "your_webhook_secret_for_signature_verification",
  "events": [
    "task.completed",
    "task.failed",
    "vehicle.battery.low",
    "alert.fired"
  ],
  "retry_policy": {
    "max_retries": 3,
    "backoff_base_sec": 1.0,
    "backoff_multiplier": 2.0
  }
}

# Webhook请求格式
POST https://your-system.example.com/webhooks/agvtms
X-Agvtms-Signature: sha256=abcdef1234567890...
X-Agvtms-Timestamp: 1685928000
X-Agvtms-Event: task.completed

{
  "event_id": "evt-uuid-12345",
  "event_type": "task.completed",
  "timestamp": "2026-07-05T09:35:20Z",
  "data": {
    "task_id": "task-001",
    "vehicle_id": "agv-001",
    "duration_seconds": 320,
    "actual_path": ["A", "B", "C"]
  }
}
```

**签名验证 (Node.js示例)**:

```javascript
const crypto = require('crypto');

function verifySignature(payload, signature, secret) {
  const expectedSig = crypto
    .createHmac('sha256', secret)
    .update(payload)
    .digest('hex');
  
  return `sha256=${expectedSig}` === signature;
}

// Express中间件
app.post('/webhooks/agvtms', (req, res) => {
  const signature = req.headers['x-agvtms-signature'];
  const isValid = verifySignature(JSON.stringify(req.body), signature, WEBHOOK_SECRET);
  
  if (!isValid) {
    return res.status(401).json({ error: 'Invalid signature' });
  }
  
  // 处理事件...
  handleEvent(req.body.event_type, req.body.data);
  
  res.status(200).send('OK');
});
```

---

## 六、SDK与客户端库

### 6.1 Python SDK

```bash
pip install agvtms-sdk
```

```python
from agvtms import AgvTmsClient

# 初始化客户端
client = AgvTmsClient(
    base_url="http://localhost:8000/api/v3",
    api_key="your_api_key_here"
)

# 创建任务
task = client.tasks.create(
    pickup_node="warehouse_A",
    dropoff_node="workstation_B",
    cargo_weight_kg=50
)
print(f"Task created: {task.task_id}")

# 监听任务完成事件
@client.events.on("task.completed")
def on_completed(data):
    print(f"Task {data.task_id} completed!")

# 启动事件监听 (后台线程)
client.events.start_listening()
```

### 6.2 JavaScript/TypeScript SDK

```bash
npm install @agvtms/sdk
```

```typescript
import { AgvTmsClient } from '@agvtms/sdk';

const client = new AgvTmsClient({
  baseUrl: 'http://localhost:8000/api/v3',
  apiKey: 'your_api_key'
});

// 创建任务
const task = await client.tasks.create({
  pickupNode: 'warehouse_A',
  dropoffNode: 'workstation_B',
  priority: 10
});

// WebSocket实时监控
client.dispatcher.subscribe(['task_updates'], (msg) => {
  if (msg.type === 'task.status_changed') {
    console.log(`Task ${msg.data.task_id} is now ${msg.data.new_status}`);
  }
});
```

### 6.3 Java SDK (Spring Boot集成)

```java
// Maven依赖
// implementation 'io.agvtms:agvtms-java-sdk:1.0.0'

import io.agvtms.client.AgvTmsClient;
import io.agvtms.model.TaskCreateRequest;

public class WarehouseIntegrationService {
    
    private final AgvTmsClient client;
    
    public WarehouseIntegrationService() {
        this.client = AgvTmsClient.builder()
            .baseUrl("http://localhost:8000/api/v3")
            .apiKey("your_api_key")
            .connectTimeout(Duration.ofSeconds(10))
            .build();
    }
    
    public void createTransferTask(String from, String to) {
        TaskCreateRequest request = TaskCreateRequest.builder()
            .pickupNode(from)
            .dropoffNode(to)
            .taskType(TaskType.AGV_ONLY)
            .priority(10)
            .build();
        
        var response = client.tasks().create(request);
        log.info("Task created: {}", response.getData().getTaskId());
    }
}
```

---

## 七、部署与运维集成

### 7.1 Docker Compose 编排 (完整版)

```yaml
# docker-compose.production.yml
version: '3.8'

services:
  # === 后端API服务 ===
  agvtms-backend:
    image: agvtms/backend:v1.6.0
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres:5432/agvtms
      - REDIS_URL=redis://redis:6379/0
      - INFLUXDB_URL=http://influxdb:8086
      - LOG_LEVEL=INFO
      - JAEGER_ENDPOINT=http://jaeger:14268/api/traces
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '1.0'
          memory: 512M
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # === 前端静态资源 ===
  agvtms-frontend:
    image: agvtms/frontend:v1.6.0
    ports:
      - "3000:80"
    depends_on:
      - agvtms-backend
    restart: unless-stopped

  # === Nginx反向代理 ===
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - agvtms-backend
      - agvtms-frontend

  # === PostgreSQL数据库 ===
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: agvtms
      POSTGRES_USER: user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user"]
      interval: 10s
      timeout: 5s
      retries: 5

  # === Redis缓存 ===
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s

  # === InfluxDB时序数据库 (AGV轨迹数据) ===
  influxdb:
    image: influxdb:2.0-alpine
    environment:
      DOCKER_INFLUXDB_INIT_MODE: setup
      DOCKER_INFLUXDB_INIT_USERNAME: admin
      DOCKER_INFLUXDB_INIT_PASSWORD: ${INFLUX_PASSWORD}
      DOCKER_INFLUXDB_INIT_ORG: agvtms
      DOCKER_INFLUXDB_INIT_BUCKET: vehicle_telemetry
    volumes:
      - influx_data:/var/lib/influxdb2
    ports:
      - "8086:8086"

  # === Grafana监控面板 ===
  grafana:
    image: grafana/grafana:10.0
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
      GF_INSTALL_PLUGINS: grafana-clock-panel,grafana-worldmap-panel
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./grafana/datasources:/etc/grafana/provisioning/datasources:ro
    ports:
      - "3001:3000"
    depends_on:
      - influxdb

  # === Jaeger分布式追踪 ===
  jaeger:
    image: jaegertracing/all-in-one:1.46
    environment:
      COLLECTOR_OTLP_ENABLED: true
    ports:
      - "16686:16686"  # UI
      - "14268:14268"  # HTTP collector

volumes:
  pg_data:
  redis_data:
  influx_data:
  grafana_data:
```

### 7.2 Kubernetes Helm Chart (生产级)

```yaml
# charts/agvtms/values.yaml
replicaCount: 3

image:
  repository: agvtms/backend
  tag: v1.6.0
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8000

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: agvtms.example.com
      paths:
        - path: /
          pathType: Prefix

resources:
  limits:
    cpu: 2000m
    memory: 2Gi
  requests:
    cpu: 500m
    memory: 512Mi

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

postgresql:
  enabled: true
  auth:
    password: ${DB_PASSWORD}
  primary:
    persistence:
      size: 50Gi

redis:
  enabled: true
  architecture: standalone
  auth:
    password: ${REDIS_PASSWORD}

monitoring:
  grafana:
    enabled: true
  prometheus:
    enabled: true
    serviceMonitor:
      enabled: true
```

```bash
# 部署命令
helm repo add agvtms https://charts.agvtms.io
helm install my-agvtms agvtms/agvtms -f values.yaml --namespace production
```

### 7.3 可观测性集成

#### Prometheus指标导出

```python
# backend/app/metrics.py
from prometheus_client import Counter, Histogram, Gauge, Info

# 业务指标
TASKS_CREATED = Counter(
    'agvtms_tasks_created_total', 
    'Total tasks created',
    ['task_type', 'priority_level']
)

TASKS_COMPLETED = Counter(
    'agvtms_tasks_completed_total',
    'Tasks completed successfully',
    ['vehicle_type']
)

SCHEDULE_DURATION = Histogram(
    'agvtms_schedule_duration_seconds',
    'Time spent on scheduling algorithm',
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
)

VEHICLES_ONLINE = Gauge(
    'agvtms_vehicles_online_count',
    'Number of currently online vehicles',
    ['vehicle_type']
)

ALERTS_ACTIVE = Gauge(
    'agvtms_alerts_active_total',
    'Current number of active alerts',
    ['severity']
)
```

**Grafana Dashboard JSON** (预置面板):

```json
{
  "dashboard": {
    "title": "AgvTms Operations Overview",
    "panels": [
      {
        "title": "Task Throughput (Tasks/min)",
        "type": "graph",
        "targets": [{
          "expr": "rate(agvtms_tasks_created_total[5m])",
          "legendFormat": "Created"
        }, {
          "expr": "rate(agvtms_tasks_completed_total[5m])",
          "legendFormat": "Completed"
        }]
      },
      {
        "title": "Schedule Latency (P99)",
        "type": "gauge",
        "targets": [{
          "expr": "histogram_quantile(0.99, agvtms_schedule_duration_seconds_bucket)"
        }]
      },
      {
        "title": "Fleet Status Distribution",
        "type": "piechart",
        "targets": [{
          "expr": "agvtms_vehicles_online_count"
        }]
      }
    ]
  }
}
```

---

## 八、最佳实践与常见问题

### 8.1 性能优化建议

| 场景 | 建议 | 预期效果 |
|------|------|---------|
| 高并发任务创建 | 使用批量API `/tasks/batch` | 吞吐量提升3-5x |
| 实时性要求高 | 直接用WebSocket而非轮询 | 延迟从5s降至<100ms |
| 大规模车队 (>50辆) | 启用V2 MIP调度引擎 | 调度质量提升20% |
| 历史数据分析 | InfluxDB冷存储 + 预聚合 | 查询速度提升10x |
| 跨机房部署 | Kafka替代内置Event Bus | 消息可靠性99.999% |

### 8.2 安全加固检查清单

- [ ] 强制HTTPS/TLS 1.3加密传输
- [ ] JWT Token有效期 ≤ 1小时 + Refresh Token机制
- [ ] API Key权限最小化原则 (RBAC)
- [ ] 速率限制: 100 req/sec per IP, 1000 req/sec per tenant
- [ ] SQL注入防护 (SQLAlchemy参数化查询)
- [ ] CORS白名单严格限制
- [ ] 敏感字段脱敏 (日志中不打印Token/密码)
- [ ] 定期依赖漏洞扫描 (`pip-audit`, `npm audit`)

### 8.3 故障排查速查表

| 问题现象 | 可能原因 | 解决方法 |
|---------|---------|---------|
| 调度请求超时 | 算法计算量大/死锁 | 减少任务数/检查地图连通性 |
| AGV频繁离线 | MQTT网络不稳定 | 增加重连间隔/检查防火墙 |
| WebSocket断连 | Nginx超时设置过短 | `proxy_read_timeout 3600s` |
| 内存持续增长 | 事件监听器泄漏 | 检查是否正确unsubscribe |
| DB连接池耗尽 | 未正确关闭Session | 使用`async with`上下文管理器 |

---

## 附录

### A. API快速参考卡片

```
任务管理:
  POST   /api/v3/tasks           创建任务
  POST   /api/v3/tasks/batch     批量创建
  GET    /api/v3/tasks           任务列表(分页)
  GET    /api/v3/tasks/{id}      任务详情
  PUT    /api/v3/tasks/{id}      更新任务
  DELETE /api/v3/tasks/{id}      取消任务
  POST   /api/v3/tasks/{id}/resume  重试失败任务

车队管理:
  POST   /api/v3/vehicles        注册车辆
  GET    /api/v3/vehicles        车辆列表
  GET    /api/v3/vehicles/{id}   车辆详情
  GET    /api/v3/fleet/status    车队总览
  POST   /api/v3/vehicles/{id}/stop   急停
  POST   /api/v3/vehicles/{id}/resume  恢复

调度引擎:
  POST   /api/v3/dispatcher/run  触发调度
  GET    /api/v3/dispatcher/history  调度历史
  WS     /ws/v3/dispatcher/live  实时事件流

地图管理:
  POST   /api/v3/maps/import     导入地图
  GET    /api/v3/maps/{id}       地图详情
  PUT    /api/v3/maps/{id}       更新地图
```

### B. 版本兼容性矩阵

| AgvTms版本 | API版本 | Python版本 | Breaking Changes |
|-----------|---------|-----------|------------------|
| v1.6.0 | v3 (最新), v2 (稳定), v1 (弃用) | 3.10+ | - |
| v1.5.x | v2, v1 | 3.9+ | - |
| v1.4.x | v1 | 3.8+ | ❌ 不再维护 |

### C. 相关资源链接

| 资源 | URL |
|------|-----|
| API在线文档 (Swagger UI) | http://localhost:8000/docs |
| Postman集合下载 | ./assets/agvtms-api.postman_collection.json |
| GitHub仓库 (Issues/PR) | https://github.com/your-org/AgvTms |
| 技术博客 | https://blog.agvtms.io |
| Discord社区 | https://discord.gg/agvtms |
| 企业支持邮箱 | enterprise@agvtms.io |

---

**文档版本**: v1.6.0-dev  
**最后更新**: 2026-07-05  
**作者**: AgvTms Core Team  
**许可**: CC BY-SA 4.0 (文档内容)
