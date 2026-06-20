"""
Phase 8: 3D 数字孪生数据模型 — 支持前端 Three.js/Babylon.js 渲染.

提供 3D 场景所需的完整数据结构:
  - SceneModel: 3D 场景模型 (地图 + AGV + 轨迹)
  - Agv3DModel: AGV 3D 模型定义 (含 mesh/材质/动画)
  - Trajectory3D: 3D 轨迹数据 (含时间戳的位置序列)
  - HeatmapData: 热力图数据 (拥堵/利用率)
  - PlaybackData: 历史回放数据

前端通过 API 获取这些数据, 用 Three.js 渲染 3D 数字孪生。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Point3D:
    """3D 坐标点"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_dict(self) -> Dict:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass
class Agv3DModel:
    """
    AGV 3D 模型定义.

    包含渲染所需的完整信息:
      - 几何体类型 (box/cylinder/custom)
      - 尺寸
      - 颜色/材质
      - 朝向
      - 动画状态
    """
    agv_id: str
    position: Point3D = field(default_factory=Point3D)
    rotation: float = 0.0  # Y 轴旋转角度 (度)
    geometry_type: str = "box"  # box / cylinder / custom
    dimensions: Tuple[float, float, float] = (1.2, 0.8, 0.5)  # 长×宽×高 (米)
    color: str = "#4A90D9"
    state: str = "idle"  # idle/moving/charging/error
    battery_level: float = 100.0
    load_status: bool = False
    current_task: str = ""
    speed: float = 0.0
    # 动画
    animation: str = "idle"  # idle/moving/loading/unloading

    def to_dict(self) -> Dict:
        return {
            "agvId": self.agv_id,
            "position": self.position.to_dict(),
            "rotation": self.rotation,
            "geometryType": self.geometry_type,
            "dimensions": list(self.dimensions),
            "color": self.color,
            "state": self.state,
            "batteryLevel": self.battery_level,
            "loadStatus": self.load_status,
            "currentTask": self.current_task,
            "speed": self.speed,
            "animation": self.animation,
        }


@dataclass
class TrajectoryPoint:
    """轨迹点 (含时间戳)"""
    timestamp: float
    position: Point3D
    rotation: float = 0.0
    speed: float = 0.0
    state: str = "moving"

    def to_dict(self) -> Dict:
        return {
            "t": self.timestamp,
            "pos": self.position.to_dict(),
            "rot": self.rotation,
            "speed": self.speed,
            "state": self.state,
        }


@dataclass
class Trajectory3D:
    """3D 轨迹数据 — 用于动画播放和历史回放"""
    agv_id: str
    points: List[TrajectoryPoint] = field(default_factory=list)
    total_duration: float = 0.0
    total_distance: float = 0.0

    def add_point(self, timestamp: float, x: float, y: float, z: float = 0.0,
                  rotation: float = 0.0, speed: float = 0.0, state: str = "moving"):
        self.points.append(TrajectoryPoint(
            timestamp=timestamp,
            position=Point3D(x, y, z),
            rotation=rotation,
            speed=speed,
            state=state,
        ))

    def to_dict(self) -> Dict:
        return {
            "agvId": self.agv_id,
            "points": [p.to_dict() for p in self.points],
            "totalDuration": self.total_duration,
            "totalDistance": self.total_distance,
        }


@dataclass
class HeatmapCell:
    """热力图单元格"""
    x: float
    y: float
    value: float  # 0.0 ~ 1.0
    label: str = ""

    def to_dict(self) -> Dict:
        return {"x": self.x, "y": self.y, "value": self.value, "label": self.label}


@dataclass
class HeatmapData:
    """热力图数据 — 拥堵/利用率可视化"""
    heatmap_type: str  # "congestion" / "utilization" / "battery"
    cells: List[HeatmapCell] = field(default_factory=list)
    max_value: float = 1.0
    min_value: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        return {
            "type": self.heatmap_type,
            "cells": [c.to_dict() for c in self.cells],
            "maxValue": self.max_value,
            "minValue": self.min_value,
            "timestamp": self.timestamp,
        }


@dataclass
class MapElement3D:
    """地图 3D 元素 (节点/边/区域)"""
    element_type: str  # "node" / "edge" / "zone" / "charging_station"
    element_id: str
    position: Point3D = field(default_factory=Point3D)
    dimensions: Tuple[float, float, float] = (1.0, 1.0, 0.1)
    color: str = "#CCCCCC"
    label: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "type": self.element_type,
            "id": self.element_id,
            "position": self.position.to_dict(),
            "dimensions": list(self.dimensions),
            "color": self.color,
            "label": self.label,
            "metadata": self.metadata,
        }


@dataclass
class MapEdge3D:
    """地图边 (连接两个节点)"""
    edge_id: str = ""
    source: str = ""   # source node element_id
    target: str = ""   # target node element_id
    edge_type: str = "path"  # path / corridor / door
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "type": self.edge_type,
            "weight": self.weight,
            "metadata": self.metadata,
        }


@dataclass
class SceneModel:
    """
    完整 3D 场景模型 — 前端 Three.js 渲染所需的所有数据.

    包含:
      - 地图元素 (节点/边/区域)
      - AGV 3D 模型列表
      - 实时轨迹
      - 热力图
    """
    scene_id: str = "default"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    map_elements: List[MapElement3D] = field(default_factory=list)
    map_edges: List[MapEdge3D] = field(default_factory=list)  # 新增: 地图边
    agvs: List[Agv3DModel] = field(default_factory=list)
    trajectories: List[Trajectory3D] = field(default_factory=list)
    heatmaps: List[HeatmapData] = field(default_factory=list)
    camera_default: Dict = field(default_factory=lambda: {
        "position": [50, 50, 50],
        "target": [0, 0, 0],
    })

    def to_dict(self) -> Dict:
        return {
            "sceneId": self.scene_id,
            "timestamp": self.timestamp,
            "mapElements": [e.to_dict() for e in self.map_elements],
            "mapEdges": [e.to_dict() for e in self.map_edges],  # 新增
            "agvs": [a.to_dict() for a in self.agvs],
            "trajectories": [t.to_dict() for t in self.trajectories],
            "heatmaps": [h.to_dict() for h in self.heatmaps],
            # 兼容前端旧字段名
            "heatmapData": [c for h in self.heatmaps for c in h.cells],
            "cameraDefault": self.camera_default,
        }


# ==================== 场景构建器 ====================

def build_scene_from_schedule(
    agvs: List[Any],
    nodes: List[Any],
    edges: List[Any],
    schedule_result: Any = None,
) -> SceneModel:
    """
    从调度数据构建 3D 场景模型.

    Args:
        agvs: AGV 列表 (AgvStatus 或 dict)
        nodes: 地图节点列表 (MapNode 或 dict)
        edges: 地图边列表 (MapEdge 或 dict)
        schedule_result: 调度结果 (可选, 含轨迹)

    Returns:
        SceneModel
    """
    scene = SceneModel()

    # 构建地图元素
    for node in nodes:
        node_id = getattr(node, "id", node.get("id", "")) if isinstance(node, dict) else node.id
        x = getattr(node, "x", node.get("x", 0)) if isinstance(node, dict) else node.x
        y = getattr(node, "y", node.get("y", 0)) if isinstance(node, dict) else node.y
        node_type = getattr(node, "type", node.get("type", "path")) if isinstance(node, dict) else getattr(node, "type", "path")

        color_map = {
            "pickup": "#4CAF50",
            "dropoff": "#FF9800",
            "charge": "#2196F3",
            "cross": "#FF5722",
            "path": "#BDBDBD",
        }
        color = color_map.get(str(node_type), "#BDBDBD") if hasattr(node_type, 'value') else color_map.get(str(node_type), "#BDBDBD")

        scene.map_elements.append(MapElement3D(
            element_type="node",
            element_id=str(node_id),
            position=Point3D(x=float(x), y=float(y), z=0.0),
            dimensions=(0.8, 0.8, 0.1),
            color=color,
            label=str(node_id),
        ))

    # 构建 AGV 3D 模型
    for agv in agvs:
        agv_id = getattr(agv, "id", agv.get("id", "")) if isinstance(agv, dict) else agv.id
        x = getattr(agv, "x", agv.get("x", 0)) if isinstance(agv, dict) else agv.x
        y = getattr(agv, "y", agv.get("y", 0)) if isinstance(agv, dict) else agv.y
        battery = getattr(agv, "battery", agv.get("battery", 100)) if isinstance(agv, dict) else agv.battery
        status = getattr(agv, "status", agv.get("status", "idle")) if isinstance(agv, dict) else agv.status
        speed = getattr(agv, "speed", agv.get("speed", 0)) if isinstance(agv, dict) else agv.speed

        state_color = {
            "idle": "#4A90D9",
            "moving": "#4CAF50",
            "charging": "#FFEB3B",
            "error": "#F44336",
            "executing": "#9C27B0",
        }
        state_str = status.value if hasattr(status, 'value') else str(status)
        color = state_color.get(state_str, "#4A90D9")

        scene.agvs.append(Agv3DModel(
            agv_id=str(agv_id),
            position=Point3D(x=float(x), y=float(y), z=0.25),
            color=color,
            state=state_str,
            battery_level=float(battery),
            speed=float(speed),
            animation="moving" if state_str in ("moving", "executing") else "idle",
        ))

    # 构建轨迹 (如果有调度结果)
    if schedule_result and hasattr(schedule_result, "assignments"):
        for assignment in schedule_result.assignments:
            trajectory = Trajectory3D(agv_id=assignment.agv_id)
            for i, node_id in enumerate(assignment.path):
                # 查找节点位置
                node_pos = next(
                    (n for n in scene.map_elements if n.element_id == node_id), None
                )
                if node_pos:
                    trajectory.add_point(
                        timestamp=float(i) * 2.0,
                        x=node_pos.position.x,
                        y=node_pos.position.y,
                        speed=1.5,
                    )
            trajectory.total_duration = float(len(assignment.path)) * 2.0
            scene.trajectories.append(trajectory)

    # 构建地图边
    for i, edge in enumerate(edges):
        src_id = getattr(edge, "source", edge.get("source", "")) if isinstance(edge, dict) else getattr(edge, "source", "")
        tgt_id = getattr(edge, "target", edge.get("target", "")) if isinstance(edge, dict) else getattr(edge, "target", "")

        # 尝试多种字段名
        if not src_id:
            src_id = getattr(edge, "from_node", edge.get("from_node", "")) if isinstance(edge, dict) else getattr(edge, "from_node", "")
        if not tgt_id:
            tgt_id = getattr(edge, "to_node", edge.get("to_node", "")) if isinstance(edge, dict) else getattr(edge, "to_node", "")

        if src_id and tgt_id:
            scene.map_edges.append(MapEdge3D(
                edge_id=f"e_{i}",
                source=str(src_id),
                target=str(tgt_id),
                edge_type="path",
            ))

    # 如果没有显式边数据，根据节点顺序自动生成邻接边（用于可视化展示）
    if len(scene.map_edges) == 0 and len(scene.map_elements) >= 2:
        for i in range(len(scene.map_elements) - 1):
            scene.map_edges.append(MapEdge3D(
                edge_id=f"auto_{i}",
                source=scene.map_elements[i].element_id,
                target=scene.map_elements[i + 1].element_id,
                edge_type="auto_path",
            ))

    return scene
