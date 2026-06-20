"""
Phase 5-8 统一 API 路由.

Phase 5: 策略切换 API + 调度循环控制
Phase 6: 分布式调度 API + 消息队列监控
Phase 7: VDA5050 完整 order API + Modbus 设备管理
Phase 8: 3D 数字孪生 API + RL A/B 测试 API
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/advanced", tags=["V2 - Phase 5-8 高级功能"])


# ==================== Phase 5: 策略切换 API ====================

class StrategySwitchRequest(BaseModel):
    cost_function: Optional[str] = Field(None, description="成本函数: distance/time/weighted/energy/priority")
    dispatcher: Optional[str] = Field(None, description="分派策略: greedy/hungarian/mip")
    router: Optional[str] = Field(None, description="路由策略: astar/sipp/dstar")


@router.get("/strategy")
async def get_strategy():
    """获取当前策略配置"""
    from ..core.integration import get_strategy_info
    return get_strategy_info()


@router.put("/strategy")
async def set_strategy(req: StrategySwitchRequest):
    """运行时切换策略"""
    from ..core.integration import set_strategy as do_set
    result = do_set(
        cost_function=req.cost_function,
        dispatcher=req.dispatcher,
        router=req.router,
    )
    return {"success": True, "updated": result}


@router.post("/scheduler-loop/start")
async def start_scheduler_loop():
    """启动持续调度循环"""
    from ..core.integration import start_scheduler_loop
    try:
        from ..services.schedule_service import schedule_service
        await start_scheduler_loop(schedule_service)
        return {"success": True, "message": "Scheduler loop started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scheduler-loop/stop")
async def stop_scheduler_loop():
    """停止调度循环"""
    from ..core.integration import stop_scheduler_loop
    await stop_scheduler_loop()
    return {"success": True, "message": "Scheduler loop stopped"}


@router.get("/scheduler-loop/status")
async def scheduler_loop_status():
    """调度循环状态"""
    from ..core.scheduler_loop import get_scheduler_loop
    loop = get_scheduler_loop()
    return {
        "is_running": loop.is_running,
        "stats": loop.stats,
    }


# ==================== Phase 6: 分布式调度 API ====================

@router.post("/distributed/submit-order")
async def distributed_submit_order(order_data: Dict[str, Any]):
    """提交订单到分布式消息队列"""
    from ..core.distributed import distributed_scheduler
    msg_id = await distributed_scheduler.submit_order(order_data)
    if msg_id:
        return {"success": True, "message_id": msg_id}
    raise HTTPException(status_code=503, detail="Message queue unavailable (Redis not connected)")


@router.get("/distributed/results")
async def distributed_get_results(count: int = Query(10, le=100)):
    """获取分布式调度结果"""
    from ..core.distributed import distributed_scheduler
    results = await distributed_scheduler.get_results(count=count)
    return {"results": results, "count": len(results)}


@router.post("/distributed/worker/start")
async def distributed_start_worker():
    """启动分布式调度 Worker"""
    from ..core.distributed import distributed_scheduler
    try:
        from ..services.schedule_service import schedule_service
        await distributed_scheduler.start_worker(schedule_service)
        return {"success": True, "message": "Distributed worker started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/distributed/worker/stop")
async def distributed_stop_worker():
    """停止分布式 Worker"""
    from ..core.distributed import distributed_scheduler
    await distributed_scheduler.stop_worker()
    return {"success": True, "message": "Worker stopped"}


# ==================== Phase 7: VDA5050 完整 Order API ====================

class Vda5050OrderRequest(BaseModel):
    """VDA5050 完整 Order 请求"""
    order_id: str = Field(..., description="订单 ID")
    path: List[str] = Field(..., description="有序节点 ID 列表")
    max_speed: float = Field(1.5, description="最大速度 m/s")
    serial_number: str = Field("", description="AGV 序列号")


@router.post("/vda5050/build-order")
async def build_vda5050_order(req: Vda5050OrderRequest):
    """构建完整 VDA5050 Order JSON"""
    from ..protocols.vda5050_complete import build_order_from_path
    order = build_order_from_path(
        order_id=req.order_id,
        path=req.path,
        max_speed=req.max_speed,
    )
    order.serial_number = req.serial_number
    return order.to_dict()


@router.get("/vda5050/schema")
async def vda5050_schema():
    """获取 VDA5050 v2.0 完整 Schema 信息"""
    return {
        "version": "2.0.0",
        "message_types": {
            "order": {
                "fields": ["headerId", "version", "manufacturer", "serialNumber",
                           "timestamp", "orderId", "orderUpdateId", "nodes", "edges"],
                "node_fields": ["nodeId", "sequenceId", "released", "actions",
                                "x", "y", "theta", "mapId"],
                "edge_fields": ["edgeId", "startNodeId", "endNodeId", "sequenceId",
                                "maxSpeed", "released", "actions", "direction"],
            },
            "state": {
                "fields": ["headerId", "version", "manufacturer", "serialNumber",
                           "timestamp", "orderId", "orderUpdateId", "lastNodeId",
                           "lastEdgeId", "agvPosition", "agvState", "driving",
                           "batteryState", "actionStates", "safetyState",
                           "errors", "warnings", "information"],
            },
            "instantAction": {
                "types": ["stop", "cancelOrder", "start"],
            },
        },
        "action_types": ["pickPosition", "dropPosition", "initPosition",
                         "charge", "wait", "customAction"],
        "blocking_types": ["NONE", "SOFT", "HARD"],
    }


# ==================== Phase 7: 适配器管理 API ====================

@router.get("/adapters")
async def list_adapters():
    """列出所有已注册的适配器"""
    from ..adapters import AdapterRegistry, adapter_manager
    return {
        "registered": AdapterRegistry.list_adapters(),
        "running": adapter_manager.get_info(),
    }


class AdapterStartRequest(BaseModel):
    adapter_name: str = Field(..., description="适配器名称: opcua/mqtt/modbus/vda5050")
    config: Dict[str, Any] = Field(default_factory=dict, description="适配器配置")


@router.post("/adapters/start")
async def start_adapter(req: AdapterStartRequest):
    """启动适配器"""
    from ..adapters import adapter_manager
    try:
        adapter = await adapter_manager.start_adapter(req.adapter_name, req.config)
        return {
            "success": True,
            "adapter": req.adapter_name,
            "vehicle_count": adapter.vehicle_count,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/adapters/{adapter_name}")
async def stop_adapter(adapter_name: str):
    """停止适配器"""
    from ..adapters import adapter_manager
    await adapter_manager.stop_adapter(adapter_name)
    return {"success": True, "message": f"Adapter {adapter_name} stopped"}


# ==================== Phase 8: 3D 数字孪生 API ====================

@router.get("/digital-twin/scene")
async def get_3d_scene():
    """获取 3D 数字孪生场景数据"""
    import asyncio
    from ..core.digital_twin import build_scene_from_schedule
    from ..services.schedule_service import schedule_service
    from ..services.map_service import map_service

    loop = asyncio.get_event_loop()

    # 使用 run_in_executor 避免阻塞 async 事件循环
    agvs = await loop.run_in_executor(None, schedule_service.get_agvs)
    graph = await loop.run_in_executor(None, map_service.get_graph)

    # _results 是内存 dict，读取很快，不需要 executor
    results = list(schedule_service._results.values())
    latest_result = results[-1] if results else None

    scene = await loop.run_in_executor(
        None, build_scene_from_schedule,
        agvs, graph.nodes, graph.edges, latest_result,
    )
    return scene.to_dict()


@router.get("/digital-twin/trajectory/{agv_id}")
async def get_trajectory(agv_id: str):
    """获取指定 AGV 的 3D 轨迹"""
    from ..core.digital_twin import Trajectory3D, Point3D
    # 从最近的调度结果中提取轨迹
    from ..services.schedule_service import schedule_service
    results = list(schedule_service._results.values())
    for result in reversed(results):
        for assignment in result.assignments:
            if assignment.agv_id == agv_id:
                trajectory = Trajectory3D(agv_id=agv_id)
                for i, node_id in enumerate(assignment.path):
                    trajectory.add_point(
                        timestamp=float(i) * 2.0, x=0, y=0, speed=1.5
                    )
                return trajectory.to_dict()
    return {"agv_id": agv_id, "points": [], "message": "No trajectory found"}


# ==================== Phase 8: RL A/B 测试 API ====================

class ABTestStartRequest(BaseModel):
    test_name: str
    strategy_a: str = Field("mip", description="策略 A: mip/hungarian/greedy/rl")
    strategy_b: str = Field("rl", description="策略 B")
    traffic_split: float = Field(0.5, ge=0.0, le=1.0)


@router.post("/ab-test/start")
async def start_ab_test(req: ABTestStartRequest):
    """启动 A/B 测试"""
    from ..core.rl_production import ab_test_framework
    ab_test_framework.start_test(
        req.test_name, req.strategy_a, req.strategy_b, req.traffic_split
    )
    return {"success": True, "message": f"A/B test '{req.test_name}' started"}


@router.get("/ab-test/{test_name}")
async def get_ab_test_result(test_name: str):
    """获取 A/B 测试结果"""
    from ..core.rl_production import ab_test_framework
    result = ab_test_framework.get_result(test_name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Test '{test_name}' not found")
    return {
        "test_name": result.test_name,
        "strategy_a": result.strategy_a,
        "strategy_b": result.strategy_b,
        "samples_a": result.samples_a,
        "samples_b": result.samples_b,
        "metrics_a": result.metrics_a or {},
        "metrics_b": result.metrics_b or {},
        "winner": result.winner if result.winner else None,
    }


@router.get("/rl/model-status")
async def rl_model_status():
    """RL 模型状态"""
    from ..core.rl_production import rl_inference
    return {
        "loaded": rl_inference._loaded,
        "model_type": rl_inference._model_type,
        "model_path": rl_inference.model_path,
    }
