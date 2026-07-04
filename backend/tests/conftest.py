"""
全局测试配置和Fixtures - 对标pytest最佳实践

功能:
1. 环境变量配置 (测试数据库/Redis)
2. 全局Fixtures (事件循环/Mock对象/测试数据)
3. 测试标记定义
4. 数据库初始化/清理

使用方式:
    pytest tests/ -v -m "unit"           # 仅运行单元测试
    pytest tests/ -v -m "integration"     # 仅运行集成测试
    pytest tests/ -v --cov=app --cov-report=html  # 带覆盖率报告
"""

import asyncio
import os
import sys
import pytest
from typing import AsyncGenerator, Dict, Any
from unittest.mock import MagicMock, AsyncMock

# ==================== 1. 环境变量设置 ====================

os.environ.setdefault("TESTING", "true")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/agvtms_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("LOG_LEVEL", "DEBUG")  # 测试环境输出详细日志
os.environ.setdefault("ENV", "dev")
os.environ.setdefault("USE_DB_PERSISTENCE", "false")  # 默认不使用真实数据库
os.environ.setdefault("USE_REDIS_CACHE", "false")    # 默认不使用真实Redis

# 确保项目根目录在sys.path中 (便于导入app模块)
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


# ==================== 2. 全局Fixtures ====================

@pytest.fixture(scope="session")
def event_loop():
    """全局事件循环 (Session级别共享，避免重复创建)"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_redis():
    """Mock Redis连接 (用于单元测试)"""
    redis_mock = MagicMock()
    redis_mock.get.return_value = None
    redis_mock.set.return_value = True
    redis_mock.hset.return_value = True
    redis_mock.hget.return_value = None
    redis_mock.delete.return_value = 1
    return redis_mock


@pytest.fixture
def mock_db_session():
    """Mock数据库会话 (用于单元测试)"""
    session_mock = AsyncMock()
    session_mock.add.return_value = None
    session_mock.commit.return_value = None
    session_mock.rollback.return_value = None
    session_mock.execute.return_value = MagicMock(scalar_one_or_none=None)
    return session_mock


@pytest.fixture
def sample_task_data() -> Dict[str, Any]:
    """
    标准任务测试数据
    
    使用示例:
        def test_create_task(client, sample_task_data):
            resp = client.post("/api/tasks", json=sample_task_data)
            assert resp.status_code == 200
    """
    return {
        "task_id": "task-test-001",
        "task_type": "agv_only",
        "pickup_node": "warehouse_A",
        "dropoff_node": "workstation_B",
        "priority": 10,
        "cargo": {
            "weight_kg": 50.0,
            "type": "pallet",
            "description": "Test cargo"
        },
        "constraints": {
            "max_waiting_time_sec": 300,
            "deadline": None
        }
    }


@pytest.fixture
def sample_vehicle_data() -> Dict[str, Any]:
    """
    标准车辆测试数据
    
    覆盖7种车型:
    - latent (潜伏顶升式)
    - forklift (叉车式)
    - conveyor (输送线)
    - amr (自主移动机器人)
    - tugger (牵引式)
    - shelf (货架搬运)
    - custom (自定义)
    """
    return {
        "vehicle_id": "agv-test-001",
        "type": "latent",
        "name": "Test AGV Unit",
        "capabilities": {
            "payload_max_kg": 300,
            "speed_max_ms": 1.2,
            "rotation_deg_per_sec": 90,
            "has_lifter": True,
            "supported_cargo_types": ["pallet", "box"]
        },
        "current_state": {
            "position": {"x": 0.0, "y": 0.0, "theta": 0.0},
            "battery_pct": 95.0,
            "status": "idle"
        }
    }


@pytest.fixture
def sample_map_data() -> Dict[str, Any]:
    """
    标准地图测试数据
    
    包含拓扑节点和边:
    - nodes: 工位/充电桩/仓库点位
    - edges: 可通行路径 + 权重
    """
    return {
        "map_id": "test-map-001",
        "name": "Test Workshop Map",
        "nodes": [
            {"id": "node_1", "x": 0, "y": 0, "type": "workstation"},
            {"id": "node_2", "x": 10, "y": 0, "type": "charging"},
            {"id": "node_3", "x": 20, "y": 0, "type": "warehouse"},
        ],
        "edges": [
            {"from": "node_1", "to": "node_2", "weight": 10.0, "bidirectional": True},
            {"from": "node_2", "to": "node_3", "weight": 15.0, "bidirectional": True},
        ],
        "zones": [
            {
                "zone_id": "zone_safe",
                "type": "no_entry",
                "polygon": [[30, 0], [40, 0], [40, 10], [30, 10]],
                "allowed_vehicles": []
            }
        ]
    }


@pytest.fixture
def auth_headers() -> Dict[str, str]:
    """
    认证请求头
    
    模拟JWT Token或API Key认证
    """
    return {
        "X-User-ID": "user-test-001",
        "X-Tenant-ID": "tenant-default",
        "X-API-Key": "test-api-key-secret",
        "X-Request-ID": "req-test-001",
    }


@pytest.fixture
def mock_mqtt_client():
    """Mock MQTT客户端 (用于适配器测试)"""
    mqtt_mock = AsyncMock()
    mqtt_mock.connect.return_value = True
    mqtt_mock.publish.return_value = True
    mqtt_mock.subscribe.return_value = True
    mqtt_mock.disconnect.return_value = None
    return mqtt_mock


@pytest.fixture
def mock_opcua_client():
    """Mock OPC UA客户端"""
    opcua_mock = MagicMock()
    opcua_mock.connect.return_value = True
    opcua_mock.read_node_value.return_value = 42.0
    opcua_mock.write_node_value.return_value = True
    return opcua_mock


# ==================== 3. 辅助函数 ====================

async def create_test_app(overrides: dict = None):
    """
    创建测试用FastAPI应用实例
    
    Args:
        overrides: 配置覆盖项
        
    Returns:
        FastAPI应用实例
    """
    from app.main import app
    
    if overrides:
        for key, value in overrides.items():
            setattr(app.state, key, value)
    
    return app


def assert_response_format(response_dict: dict):
    """
    验证API响应是否符合统一格式 (ApiResponse规范)
    
    必需字段:
    - code: str (响应码)
    - message: str (消息)
    - data: Any (数据载荷)
    - trace_id: Optional[str] (追踪ID)
    - timestamp: float (时间戳)
    
    使用示例:
        resp = client.get("/api/tasks")
        data = resp.json()
        assert_response_format(data)
        assert data["code"] == "200"
    """
    assert "code" in response_dict, "Missing 'code' field"
    assert "message" in response_dict, "Missing 'message' field"
    assert "data" in response_dict, "Missing 'data' field"
    assert "timestamp" in response_dict, "Missing 'timestamp' field"
    
    # 类型检查
    assert isinstance(response_dict["code"], str), "'code' must be string"
    assert isinstance(response_dict["message"], str), "'message' must be string"
    assert isinstance(response_dict["timestamp"], (int, float)), "'timestamp' must be numeric"


def assert_error_response(
    response_dict: dict,
    expected_code: str,
    expected_message_contains: str = None
):
    """
    验证错误响应格式
    
    Args:
        response_dict: API响应字典
        expected_code: 期望的错误码 (如"400", "429", "500")
        expected_message_contains: 错误消息应包含的子串
    """
    assert_response_format(response_dict)
    assert response_dict["code"] == expected_code, \
        f"Expected code {expected_code}, got {response_dict['code']}"
    
    if expected_message_contains:
        assert expected_message_contains.lower() in response_dict["message"].lower(), \
            f"Error message should contain '{expected_message_contains}', got '{response_dict['message']}'"


# ==================== 4. Pytest Hooks ====================

def pytest_configure(config):
    """
    注册自定义标记 (避免--strict-markers警告)
    """
    config.addinivalue_line("markers", "unit: 单元测试 (无需外部依赖)")
    config.addinivalue_line("markers", "integration: 集成测试 (需要数据库/MQTT等)")
    config.addinivalue_line("markers", "e2e: 端到端测试 (需要完整环境)")
    config.addinivalue_line("markers", "slow: 耗时较长的测试 (@pytest.mark.slow)")
    config.addinivalue_line("markers", "performance: 性能基准测试")
    config.addinivalue_line("markers", "mqtt: 需要MQTT Broker的测试")
    config.addinivalue_line("markers", "opcua: 需要OPC UA Server的测试")


# ==================== 5. 测试数据工厂 ====================

class TaskDataFactory:
    """任务数据工厂 (快速生成不同类型的测试任务)"""
    
    @staticmethod
    def create_agv_task(priority: int = 10) -> dict:
        """创建AGV运输任务"""
        return {
            "task_type": "agv_only",
            "pickup_node": f"wh_{priority}",
            "dropoff_node": f"ws_{priority}",
            "priority": priority,
            "cargo": {"weight_kg": 50.0}
        }
    
    @staticmethod
    def create_conveyor_task(priority: int = 5) -> dict:
        """创建输送线任务"""
        return {
            "task_type": "conveyor_only",
            "source_station": f"conv_in_{priority}",
            "destination_station": f"conv_out_{priority}",
            "priority": priority
        }
    
    @staticmethod
    def create_hybrid_task(priority: int = 15) -> dict:
        """创建AGV+输送线混合任务"""
        return {
            "task_type": "agv_conveyor",
            "agv_legs": [{"pickup": "A", "dropoff": "B"}],
            "conveyor_legs": [{"source": "B", "dest": "C"}],
            "priority": priority
        }


class VehicleDataFactory:
    """车辆数据工厂"""
    
    @staticmethod
    def create_latent_agv(vehicle_id: str = "agv-001") -> dict:
        """创建潜伏顶升式AGV"""
        return {
            "vehicle_id": vehicle_id,
            "type": "latent",
            "capabilities": {
                "payload_max_kg": 300,
                "speed_max_ms": 1.2,
                "has_lifter": True,
            }
        }
    
    @staticmethod
    def create_forklift(vehicle_id: str = "forklift-001") -> dict:
        """创建叉车式AGV"""
        return {
            "vehicle_id": vehicle_id,
            "type": "forklift",
            "capabilities": {
                "payload_max_kg": 1000,
                "speed_max_ms": 0.8,
                "max_lift_height_mm": 2500,
            }
        }


# 导出常用类和函数
__all__ = [
    # Fixtures已通过conftest.py自动注册
    # 辅助函数
    "create_test_app",
    "assert_response_format",
    "assert_error_response",
    # 数据工厂
    "TaskDataFactory",
    "VehicleDataFactory",
]
