"""
WebSocket Real-time Push for 3D Digital Twin (M6 Milestone)

Endpoints:
  - WS /ws/agv-status: Real-time AGV position/animation data
  - GET /api/v2/digital-twin/scene: Initial scene snapshot
  - POST /api/v2/digital-twin/simulate: Start simulation mode

Architecture:
  Frontend (DigitalTwin3D.tsx) ←→ WebSocket ←→ AgvStateBroadcaster ←→ HybridScheduler

Author: Architecture Team
Date: 2026-06-19
"""

import asyncio
import json
import time
import random
import math
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, asdict, field
from enum import Enum

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v2/digital-twin", tags=["3D Digital Twin"])

# ==================== 数据模型 ====================

class AgvStatusEnum(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    CHARGING = "charging"
    LOADING = "loading"
    UNLOADING = "unloading"
    ERROR = "error"
    BLOCKED = "blocked"

@dataclass
class AgvPosition3D:
    """AGV 3D位置状态 (推送给前端的数据结构)"""
    id: str
    x: float                    # 世界坐标 X
    y: float                    # 世界坐标 Y (高度=0.3 for AGV model)
    z: float                    # 世界坐标 Z (= Y in 2D map)
    theta: float                # 朝向角度 (radians)
    speed: float                # 当前速度 m/s
    status: str                 # AgvStatusEnum value
    battery_level: float        # 0~100%
    current_task_id: str = ""   # 当前任务ID
    color: str = "#00aaff"      # 显示颜色
    path_progress: float = 0.0  # 路径进度 0~1
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass 
class SceneSnapshot:
    """完整场景快照 (初始加载用)"""
    timestamp: float
    agvs: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    heatmap_data: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)


class SimulateRequest(BaseModel):
    """启动模拟请求"""
    num_agvs: int = 10
    duration_seconds: int = 60
    speed_multiplier: float = 1.0
    map_size: int = 20


# ==================== 广播器核心 ====================

class AgvStateBroadcaster:
    """
    AGV状态实时广播器 (Singleton)
    
    功能:
    - 管理所有WebSocket连接
    - 定期广播AGV状态增量更新
    - 支持模拟模式生成虚拟AGV数据
    - 集成HybridScheduler获取真实调度结果
    """
    
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._agv_states: Dict[str, AgvPosition3D] = {}
        self._simulating = False
        self._sim_task: Optional[asyncio.Task] = None
        self._broadcast_interval = 1.0 / 30  # 30 FPS (33ms)
        self._last_broadcast_time = 0.0
        self._message_count = 0
        
        # 模拟参数
        self._sim_agvs: List[Dict] = []
        self._sim_speed = 1.0
        self._sim_map_size = 20
    
    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        self._connections.add(websocket)
        
        # 发送当前全量快照
        snapshot = self._build_snapshot()
        await websocket.send_json({
            "type": "snapshot",
            "data": snapshot.to_dict()
        })
        
        print(f"[WS] Connected: {websocket.client.host}, total={len(self._connections)}")
    
    def disconnect(self, websocket: WebSocket):
        """断开连接"""
        self._connections.discard(websocket)
        print(f"[WS] Disconnected: {websocket.client.host}, remaining={len(self._connections)}")
    
    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        if not self._connections:
            return
            
        payload = json.dumps(message, ensure_ascii=False)
        dead_conns = set()
        
        for conn in self._connections:
            try:
                await conn.send_text(payload)
            except Exception:
                dead_conns.add(conn)
        
        # 清理断开的连接
        self._connections -= dead_conns
        self._message_count += 1
    
    def update_agv_state(self, agv_data: Dict[str, Any]):
        """更新单个AGV状态 (由HybridScheduler调用)"""
        agv_id = agv_data.get("id")
        if not agv_id:
            return
            
        existing = self._agv_states.get(agv_id)
        if existing:
            # 增量更新
            for k, v in agv_data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
        else:
            # 新增
            self._agv_states[agv_id] = AgvPosition3D(
                id=agv_id,
                x=agv_data.get("x", 0),
                y=0.3,  # AGV模型高度
                z=agv_data.get("y", 0),
                theta=agv_data.get("theta", 0),
                speed=agv_data.get("speed", 0),
                status=agv_data.get("status", "idle"),
                battery_level=agv_data.get("battery_level", 100),
                current_task_id=agv_data.get("current_task_id", ""),
                color=self._status_to_color(agv_data.get("status", "idle")),
            )
    
    def _status_to_color(self, status: str) -> str:
        """状态→颜色映射"""
        colors = {
            "idle": "#00ff88",
            "moving": "#00aaff",
            "charging": "#ffaa00",
            "loading": "#aa00ff",
            "unloading": "#ff00aa",
            "error": "#ff3333",
            "blocked": "#ff6666",
        }
        return colors.get(status, "#888888")
    
    def _build_snapshot(self) -> SceneSnapshot:
        """构建当前场景快照"""
        return SceneSnapshot(
            timestamp=time.time(),
            agvs=[agv.to_dict() for agv in self._agv_states.values()],
        )
    
    # ==================== 模拟模式 ====================
    
    async def start_simulation(self, config: SimulateRequest):
        """启动模拟模式"""
        if self._simulating:
            raise HTTPException(400, "Simulation already running")
            
        self._simulating = True
        self._sim_speed = config.speed_multiplier
        self._sim_map_size = config.map_size
        
        # 初始化模拟AGV
        self._sim_agvs = []
        for i in range(config.num_agvs):
            self._sim_agvs.append({
                "id": f"SIM-AGV-{i:03d}",
                "x": random.uniform(0, config.map_size),
                "y": random.uniform(0, config.map_size),
                "target_x": random.uniform(0, config.map_size),
                "target_y": random.uniform(0, config.map_size),
                "theta": random.uniform(0, 6.28),
                "speed": random.uniform(0.5, 2.0),
                "status": random.choice(["idle", "moving"]),
                "battery_level": random.uniform(20, 100),
                "color": f"hsl({random.randint(160, 280)}, 70%, 60%)",
            })
            # 注册到状态表
            self.update_agv_state(self._sim_agvs[-1])
        
        # 启动后台模拟循环
        self._sim_task = asyncio.create_task(
            self._simulation_loop(config.duration_seconds)
        )
        
        return {
            "status": "started",
            "num_agvs": config.num_agvs,
            "duration": config.duration_seconds,
            "interval_s": self._broadcast_interval,
        }
    
    async def stop_simulation(self):
        """停止模拟"""
        self._simulating = False
        if self._sim_task:
            self._sim_task.cancel()
            try:
                await self._sim_task
            except asyncio.CancelledError:
                pass
        return {"status": "stopped"}
    
    async def _simulation_loop(self, duration: float):
        """模拟主循环"""
        start_time = time.time()
        frame_count = 0
        
        while self._simulating and (time.time() - start_time < duration):
            loop_start = time.time()
            
            # 更新每个模拟AGV的位置
            for agv in self._sim_agvs:
                # 向目标点移动
                dx = agv["target_x"] - agv["x"]
                dy = agv["target_y"] - agv["y"]
                dist = (dx**2 + dy**2) ** 0.5
                
                if dist < 0.5:
                    # 到达目标，选择新目标
                    agv["target_x"] = random.uniform(0, self._sim_map_size)
                    agv["target_y"] = random.uniform(0, self._sim_map_size)
                    agv["status"] = random.choice(["idle", "moving"])
                else:
                    # 移动
                    step = agv["speed"] * self._broadcast_interval * self._sim_speed
                    agv["x"] += (dx / dist) * step
                    agv["y"] += (dy / dist) * step
                    agv["theta"] = math.atan2(dy, dx)
                    agv["status"] = "moving"
                
                # 更新电池（缓慢下降）
                if agv["status"] == "moving":
                    agv["battery_level"] = max(0, agv["battery_level"] - 0.001)
                
                # 推送更新
                self.update_agv_state(agv)
            
            # 广播帧数据
            await self.broadcast({
                "type": "frame",
                "timestamp": time.time(),
                "frame": frame_count,
                "agvs": [agv for agv in self._sim_agvs],
            })
            
            frame_count += 1
            
            # 帧率控制
            elapsed = time.time() - loop_start
            sleep_time = max(0, self._broadcast_interval - elapsed)
            await asyncio.sleep(sleep_time)
        
        # 时间到，自动停止
        self._simulating = False
        await self.broadcast({"type": "simulation_complete", "total_frames": frame_count})


# ==================== 全局单例 ====================

broadcaster = AgvStateBroadcaster()


# ==================== API 端点 ====================

@router.websocket("/ws/agv-status")
async def websocket_agv_status(websocket: WebSocket):
    """
    WebSocket端点: AGV实时状态推送
    
    协议:
      Server → Client:
        - { type: "snapshot", data: {...} }     # 初始全量快照
        - { type: "frame", agvs: [...], ... }    # 增量帧更新
        - { type: "simulation_complete", ... }   # 模拟结束
      
      Client → Server:
        - { action: "subscribe", agv_ids: [...] }  # 订阅特定AGV
        - { action: "focus", agv_id: "xxx" }       # 聚焦某个AGV
        - { action: "ping" }                        # 心跳
    """
    await broadcaster.connect(websocket)
    
    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_text()
            
            try:
                msg = json.loads(data)
                action = msg.get("action")
                
                if action == "subscribe":
                    # TODO: 实现订阅过滤
                    await websocket.send_json({
                        "type": "subscribed",
                        "agv_ids": msg.get("agv_ids", []),
                    })
                    
                elif action == "ping":
                    await websocket.send_json({"type": "pong"})
                    
            except json.JSONDecodeError:
                pass  # 忽略非JSON消息
                
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket)


@router.get("/scene")
async def get_scene_snapshot():
    """
    获取当前场景快照 (用于前端初始加载)
    
    返回完整的AGV列表、地图拓扑、热力图数据
    """
    snapshot = broadcaster._build_snapshot()
    return snapshot.to_dict()


@router.post("/simulate")
async def start_simulation(config: SimulateRequest):
    """
    启动模拟模式 (无需真实AGV硬件)
    
    自动生成虚拟AGV并沿随机路径移动，
    通过WebSocket推送给所有连接的前端
    """
    return await broadcaster.start_simulation(config)


@router.post("/simulate/stop")
async def stop_simulation():
    """停止当前运行的模拟"""
    return await broadcaster.stop_simulation()


@router.get("/stats")
async def get_websocket_stats():
    """获取WebSocket连接统计"""
    return {
        "active_connections": len(broadcaster._connections),
        "total_agvs_registered": len(broadcaster._agv_states),
        "simulating": broadcaster._simulating,
        "total_messages_broadcast": broadcaster._message_count,
        "broadcast_fps": round(1.0 / broadcaster._broadcast_interval, 1),
    }


# ==================== 集成接口 (供HybridScheduler调用) ====================

def push_scheduler_result(result_data: Dict[str, Any]):
    """
    将HybridScheduler的调度结果推送给所有前端连接
    
    由 hybrid_scheduler.py 在每次调度完成后调用
    
    Args:
        result_data: SchedulingResult.to_dict() 或自定义格式
    """
    # 提取AGV路径信息
    if "paths" in result_data:
        for agv_id, path_info in result_data["paths"].items():
            if isinstance(path_info, dict) and "waypoints" in path_info:
                # 取路径的第一个点作为当前位置
                wp = path_info["waypoints"][0] if path_info["waypoints"] else {}
                broadcaster.update_agv_state({
                    "id": agv_id,
                    "x": wp.get("x", 0),
                    "y": wp.get("z", wp.get("y", 0)),
                    "theta": wp.get("heading", 0),
                    "speed": path_info.get("avg_speed", 0),
                    "status": "moving" if path_info.get("waypoints") else "idle",
                    "current_task_id": result_data.get("task_id", ""),
                })
    
    # 异步广播
    asyncio.create_task(broadcaster.broadcast({
        "type": "scheduler_update",
        "timestamp": time.time(),
        "result": result_data,
    }))


# 导出给main.py注册
__all__ = [
    "router",
    "broadcaster",
    "push_scheduler_result",
]
