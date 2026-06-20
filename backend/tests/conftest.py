"""
AGV-TMS Backend Test Suite — Phase D: 测试基础设施完善

标准化的 pytest 测试框架配置:
  - Fixtures (数据库/Redis/客户端)
  - 参数化测试
  - 异步测试支持
  - 覆盖率报告 (目标: lines >= 50%)
  - Marker 分组

测试文件清单 (Phase A-D):
  - test_resilience*.py     — 断路器/限流/LeaderElection (26 tests)
  - test_api_routes_*.py    — API路由边界条件 (31 tests)
  - test_v2_algorithm_*.py  — V2算法引擎覆盖 (30 tests)
  - test_v2_complete.py     — V2完整流程集成
  - test_evaluator_full.py  — 算法基准评测
  - ... (其他已有测试)
"""

import pytest
import asyncio
import os
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ==================== Fixtures ====================

@pytest.fixture(scope="session")
def event_loop():
    """全局事件循环."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_map_nodes():
    """示例地图节点数据."""
    return [
        {"id": "n1", "x": 0.0, "y": 0.0, "nodeType": "pickup", "label": "A"},
        {"id": "n2", "x": 10.0, "y": 0.0, "nodeType": "dropoff", "label": "B"},
        {"id": "n3", "x": 10.0, "y": 10.0, "nodeType": "charging", "label": "C"},
        {"id": "n4", "x": 0.0, "y": 10.0, "nodeType": "junction", "label": "D"},
        {"id": "n5", "x": 5.0, "y": 5.0, "nodeType": None, "label": "E"},
    ]


@pytest.fixture
def sample_map_edges():
    """示例地图边数据."""
    return [
        {"id": "e1", "source": "n1", "target": "n2", "weight": 10},
        {"id": "e2", "source": "n2", "target": "n3", "weight": 10},
        {"id": "e3", "source": "n3", "target": "n4", "weight": 14},
        {"id": "e4", "source": "n4", "target": "n1", "weight": 10},
        {"id": "e5", "source": "n1", "target": "n3", "weight": 20},
        {"id": "e6", "source": "n2", "target": "n4", "weight": 14},
        {"id": "e7", "source": "n5", "target": "n1", "weight": 7},
        {"id": "e8", "source": "n5", "target": "n2", "weight": 7},
        {"id": "e9", "source": "n5", "target": "n3", "weight": 9},
        {"id": "e10", "source": "n5", "target": "n4", "weight": 9},
    ]


@pytest.fixture
def sample_agvs():
    """示例 AGV 数据."""
    return [
        {
            "id": "agv-001",
            "current_node_id": "n5",
            "position": {"x": 5.0, "y": 5.0},
            "battery_level": 85,
            "state": "idle",
            "speed": 1.2,
            "capacity": 100,
        },
        {
            "id": "agv-002",
            "current_node_id": "n1",
            "position": {"x": 0.0, "y": 0.0},
            "battery_level": 62,
            "state": "moving",
            "speed": 1.0,
            "capacity": 100,
        },
        {
            "id": "agv-003",
            "current_node_id": "n3",
            "position": {"x": 10.0, "y": 10.0},
            "battery_level": 23,
            "state": "charging",
            "speed": 0.8,
            "capacity": 50,
        },
        {
            "id": "agv-004",
            "current_node_id": "n2",
            "position": {"x": 10.0, "y": 0.0},
            "battery_level": 91,
            "state": "error",
            "speed": 0.0,
            "capacity": 200,
        },
    ]


@pytest.fixture
def sample_tasks():
    """示例任务数据."""
    return [
        {
            "id": "task-001",
            "pickup_node_id": "n1",
            "dropoff_node_id": "n3",
            "priority": 2,
            "deadline": 120,
            "weight": 50,
        },
        {
            "id": "task-002",
            "pickup_node_id": "n2",
            "dropoff_node_id": "n4",
            "priority": 5,
            "deadline": 300,
            "weight": 80,
        },
        {
            "id": "task-003",
            "pickup_node_id": "n4",
            "dropoff_node_id": "n2",
            "priority": 8,
            "deadline": 600,
            "weight": 30,
        },
        {
            "id": "task-004",
            "pickup_node_id": "n3",
            "dropoff_node_id": "n1",
            "priority": 1,
            "deadline": 60,
            "weight": 100,
        },
        {
            "id": "task-005",
            "pickup_node_id": "n5",
            "dropoff_node_id": "n3",
            "priority": 4,
            "deadline": 180,
            "weight": 60,
        },
    ]


@pytest.fixture
def sample_scene(sample_map_nodes, sample_map_edges, sample_agvs):
    """完整的场景数据 (用于数字孪生)."""
    from app.core.digital_twin import SceneModel
    
    scene = SceneModel(
        scene_id="test-scene-001",
        name="Test Factory Floor",
        timestamp="2026-06-19T19:13:00Z",
        map_elements=[
            {
                "elementId": n["id"],
                "elementType": "node",
                "position": {"x": n["x"], "y": n["y"], "z": 0.0},
                "color": "#4a90d9" if not n.get("nodeType") else 
                         {"pickup": "#ff9800", "dropoff": "#4caf50", "charging": "#e91e63"}.get(n["nodeType"], "#4a90d9"),
                "label": n.get("label", n["id"]),
                "node_type": n.get("nodeType"),
            }
            for n in sample_map_nodes
        ],
        map_edges=[{"source": e["source"], "target": e["target"]} for e in sample_map_edges],
        agvs=[
            {
                "agvId": a["id"],
                "position": a["position"],
                "rotation": 0,
                "speed": a.get("speed", 1.0),
                "batteryLevel": a["battery_level"],
                "state": a["state"],
                "color": "#00ff88" if a["state"] == "idle" else 
                        "#00aaff" if a["state"] == "moving" else
                        "#ffaa00" if a["state"] == "charging" else "#ff3333",
            }
            for a in sample_agvs
        ],
        heatmap_data=[
            {"x": 5, "y": 5, "value": 0.8},
            {"x": 0, "y": 0, "value": 0.5},
            {"x": 10, "y": 10, "value": 0.3},
        ],
    )
    return scene


# ==================== Markers ====================
# Markers 已在 pytest.ini 中定义:
#   - unit: Unit tests (fast)
#   - integration: Integration tests (need DB/Redis)
#   - slow: Slow tests (benchmark/stress)
#   - algorithm: Algorithm-specific tests
#   - resilience: Circuit breaker and rate limiter tests
