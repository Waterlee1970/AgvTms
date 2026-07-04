"""
CAD/DXF Map Importer - 工厂地图导入工具
对标 Plant Mirror CAD图纸解析 (2D→3D自动转换)

功能:
1. AutoCAD DXF 文件解析
2. JSON 地图配置导入/导出
3. 坐标系转换 (CAD坐标 → AGV世界坐标)
4. 图层过滤与分类
5. 拓扑图自动生成
6. 验证与错误修复

支持的DXF实体:
- LINE / POLYLINE / LWPOLYLINE: 路径/墙壁
- CIRCLE / ARC: 充电桩/停车位标记
- TEXT / MTEXT: 节点标签
- INSERT (Block): 设备位置

Author: Digital Twin Team
Date: 2026-07-05
"""

import json
import re
import math
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path


# ══════════════════════════════════════════════════════════
# 类型定义
# ══════════════════════════════════════════════════════════

class EntityType(str, Enum):
    """DXF 实体类型"""
    LINE = "LINE"
    POLYLINE = "POLYLINE"
    LWPOLYLINE = "LWPOLYLINE"
    CIRCLE = "CIRCLE"
    ARC = "ARC"
    TEXT = "TEXT"
    MTEXT = "MTEXT"
    POINT = "POINT"
    INSERT = "INSERT"          # Block reference
    SOLID = "SOLID"            # Filled polygon


class LayerClassification(str, Enum):
    """图层用途分类"""
    WALL = "wall"               # 墙壁/障碍物
    PATH = "path"               # 行驶路径
    NODE = "node"               # 节点/点位
    EQUIPMENT = "equipment"     # 设备位置
    LABEL = "label"             # 标注文字
    DIMENSION = "dimension"     # 尺寸标注
    UNKNOWN = "unknown"


@dataclass
class Point2D:
    """2D坐标点"""
    x: float
    y: float
    
    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}
    
    def distance_to(self, other: 'Point2D') -> float:
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def __add__(self, other: 'Point2D') -> 'Point2D':
        return Point2D(self.x + other.x, self.y + other.y)
    
    def __sub__(self, other: 'Point2D') -> 'Point2D':
        return Point2D(self.x - other.x, self.y - other.y)
    
    def __mul__(self, scalar: float) -> 'Point2D':
        return Point2D(self.x * scalar, self.y * scalar)


@dataclass
class DxfEntity:
    """解析后的DXF实体"""
    entity_type: EntityType
    layer: str = ""
    handle: str = ""
    color: int = 256             # 256 = ByLayer
    
    # 几何数据
    points: List[Point2D] = field(default_factory=list)   # 顶点列表
    center: Optional[Point2D] = None                       # 圆心
    radius: float = 0.0                                    # 半径
    start_angle: float = 0.0                               # 弧起始角
    end_angle: float = 360.0                                # 弧终止角
    
    # 文本内容 (TEXT/MTEXT)
    text: str = ""
    height: float = 2.5                                    # 文字高度
    
    # Block引用 (INSERT)
    block_name: str = ""
    insertion_point: Optional[Point2D] = None
    
    # 分类结果
    classification: LayerClassification = LayerClassification.UNKNOWN
    confidence: float = 0.0
    
    # 导出时的附加属性
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        result = {
            "type": self.entity_type.value,
            "layer": self.layer,
            "handle": self.handle,
            "classification": self.classification.value,
        }
        
        if self.points:
            result["points"] = [p.to_dict() for p in self.points]
        if self.center:
            result["center"] = self.center.to_dict()
            result["radius"] = self.radius
        if self.text:
            result["text"] = self.text
            result["height"] = self.height
        if self.block_name:
            result["block_name"] = self.block_name
            if self.insertion_point:
                result["insertion_point"] = self.insertion_point.to_dict()
        
        if self.properties:
            result.update(self.properties)
        
        return result


@dataclass 
class ImportResult:
    """导入结果"""
    success: bool
    filename: str = ""
    file_size_bytes: int = 0
    
    # 统计
    total_entities: int = 0
    parsed_entities: int = 0
    skipped_entities: int = 0
    errors: List[str] = field(default_factory=list)
    
    # 分类统计
    classification_counts: Dict[str, int] = field(default_factory=dict)
    
    # 生成的地图数据
    nodes: List[Dict] = field(default_factory=list)
    edges: List[Dict] = field(default_factory=list)
    equipment_positions: List[Dict] = field(default_factory=list)
    
    # 坐标转换信息
    coordinate_transform: Dict[str, Any] = field(default_factory=dict)
    
    # 处理时间 (毫秒)
    processing_time_ms: float = 0.0
    
    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════
# 图层名称规则引擎
# ══════════════════════════════════════════════════════════

LAYER_RULES: Dict[str, LayerClassification] = {
    # 中文常见命名
    "墙": LayerClassification.WALL,
    "墙体": LayerClassification.WALL,
    "柱子": LayerClassification.WALL,
    "障碍": LayerClassification.WALL,
    "路径": LayerClassification.PATH,
    "通道": LayerClassification.PATH,
    "车道": LayerClassification.PATH,
    "AGV路径": LayerClassification.PATH,
    "行驶线": LayerClassification.PATH,
    "节点": LayerClassification.NODE,
    "站点": LayerClassification.NODE,
    "工位": LayerClassification.EQUIPMENT,
    "设备": LayerClassification.EQUIPMENT,
    "充电桩": LayerClassification.EQUIPMENT,
    "货架": LayerClassification.EQUIPMENT,
    "输送带": LayerClassification.EQUIPMENT,
    "标注": LayerClassification.LABEL,
    "文字": LayerClassification.LABEL,
    "尺寸": LayerClassification.DIMENSION,
    
    # 英文常见命名 (AutoCAD标准)
    "wall": LayerClassification.WALL,
    "walls": LayerClassification.WALL,
    "path": LayerClassification.PATH,
    "paths": LayerClassification.PATH,
    "route": LayerClassification.PATH,
    "agv_path": LayerClassification.PATH,
    "agv-path": LayerClassification.PATH,
    "node": LayerClassification.NODE,
    "nodes": LayerClassification.NODE,
    "station": LayerClassification.NODE,
    "stations": LayerClassification.NODE,
    "point": LayerClassification.NODE,
    "equipment": LayerClassification.EQUIPMENT,
    "machine": LayerClassification.EQUIPMENT,
    "charger": LayerClassification.EQUIPMENT,
    "rack": LayerClassification.EQUIPMENT,
    "conveyor": LayerClassification.EQUIPMENT,
    "text": LayerClassification.LABEL,
    "label": LayerClassification.LABEL,
    "dim": LayerClassification.DIMENSION,
    "dimension": LayerClassification.DIMENSION,
}


@dataclass
class CoordinateTransform:
    """
    坐标变换配置
    
    将 CAD 坐标系转换为 AGV 世界坐标系:
    - 单位换算 (mm → m)
    - 原点偏移
    - 旋转对齐
    - Y轴翻转 (CAD Y轴向上 vs 屏幕Y轴向下)
    """
    scale: float = 0.001              # 默认 mm → m
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_degrees: float = 0.0     # 逆时针旋转角度
    flip_y: bool = True                # 翻转Y轴
    
    def transform(self, point: Point2D) -> Point2D:
        """应用坐标变换"""
        # 缩放
        p = Point2D(point.x * self.scale, point.y * self.scale)
        
        # 旋转
        if self.rotation_degrees != 0:
            rad = math.radians(self.rotation_degrees)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            p = Point2D(
                p.x * cos_r - p.y * sin_r,
                p.x * sin_r + p.y * cos_r
            )
        
        # Y翻转
        if self.flip_y:
            p.y = -p.y
        
        # 平移
        p.x += self.offset_x
        p.y += self.offset_y
        
        return p
    
    def inverse_transform(self, point: Point2D) -> Point2D:
        """反向变换 (世界坐标 → CAD坐标)"""
        # 反向平移
        p = Point2D(point.x - self.offset_x, point.y - self.offset_y)
        
        # 反向Y翻转
        if self.flip_y:
            p.y = -p.y
        
        # 反向旋转
        if self.rotation_degrees != 0:
            rad = math.radians(-self.rotation_degrees)
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            p = Point2D(
                p.x * cos_r - p.y * sin_r,
                p.x * sin_r + p.y * cos_r
            )
        
        # 反向缩放
        p = Point2D(p.x / self.scale, p.y / self.scale)
        
        return p
    
    def to_dict(self) -> dict:
        return {
            "scale": self.scale,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "rotation_degrees": self.rotation_degrees,
            "flip_y": self.flip_y,
        }


# ══════════════════════════════════════════════════════════
# DXF 解析器核心
# ══════════════════════════════════════════════════════════

class DxfParser:
    """
    DXF 文件解析器
    
    实现 DXF ASCII 格式的轻量级解析，
    支持常用的几何实体类型。
    
    注意: 这是简化实现，不支持二进制DXF。
    对于复杂DXF文件，建议使用 ezdxf 库。
    """
    
    def __init__(self):
        self._entities: List[DxfEntity] = []
        self._layers: Set[str] = set()
        self._errors: List[str] = []
        self._transform: CoordinateTransform = CoordinateTransform()
    
    def parse_file(self, filepath: str) -> ImportResult:
        """解析DXF文件"""
        import time
        start_time = time.time()
        
        path = Path(filepath)
        if not path.exists():
            return ImportResult(success=False, errors=[f"File not found: {filepath}"])
        
        try:
            content = path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            return ImportResult(success=False, errors=[f"Read error: {str(e)}"])
        
        file_size = path.stat().st_size
        
        # 解析
        try:
            entities = self._parse_content(content)
        except Exception as e:
            return ImportResult(
                success=False, 
                filename=path.name,
                file_size_bytes=file_size,
                errors=[f"Parse error: {str(e)}"],
                processing_time_ms=(time.time() - start_time) * 1000,
            )
        
        # 分类
        for entity in entities:
            self._classify_entity(entity)
        
        # 统计
        classification_counts = {}
        for e in entities:
            cls = e.classification.value
            classification_counts[cls] = classification_counts.get(cls, 0) + 1
        
        # 生成拓扑图
        nodes, edges, equipment = self._generate_topology(entities)
        
        processing_time = (time.time() - start_time) * 1000
        
        return ImportResult(
            success=True,
            filename=path.name,
            file_size_bytes=file_size,
            total_entities=len(entities),
            parsed_entities=len([e for e in entities if e.classification != LayerClassification.UNKNOWN]),
            skipped_entities=len(self._errors),
            errors=self._errors[:20],  # 最多返回20条错误
            classification_counts=classification_counts,
            nodes=nodes,
            edges=edges,
            equipment_positions=equipment,
            coordinate_transform=self._transform.to_dict(),
            processing_time_ms=processing_time,
        )
    
    def parse_json_map(self, filepath: str) -> ImportResult:
        """解析JSON格式的地图配置"""
        import time
        start_time = time.time()
        
        path = Path(filepath)
        if not path.exists():
            return ImportResult(success=False, errors=[f"File not found: {filepath}"])
        
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            return ImportResult(success=False, errors=[f"JSON parse error: {str(e)}"])
        
        # 验证结构
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        equipment = data.get("equipment", data.get("stations", []))
        
        # 验证节点
        valid_nodes = []
        for node in nodes:
            if "id" in node and "x" in node and "y" in node:
                valid_nodes.append(node)
        
        # 验证边
        valid_edges = []
        for edge in edges:
            if "from" in edge and "to" in edge:
                valid_edges.append(edge)
        
        return ImportResult(
            success=True,
            filename=path.name,
            file_size_bytes=path.stat().st_size,
            total_entities=len(valid_nodes) + len(valid_edges),
            parsed_entities=len(valid_nodes) + len(valid_edges),
            nodes=valid_nodes,
            edges=valid_edges,
            equipment_positions=equipment if isinstance(equipment, list) else [],
            processing_time_ms=(time.time() - start_time) * 1000,
        )
    
    def _parse_content(self, content: str) -> List[DxfEntity]:
        """解析DXF文本内容"""
        entities = []
        
        # 分割为代码-值对
        pairs = self._tokenize(content)
        
        # 查找ENTITIES段
        in_entities_section = False
        i = 0
        
        while i < len(pairs):
            code, value = pairs[i]
            
            if code == "SECTION":
                if i + 1 < len(pairs) and pairs[i+1][1] == "ENTITIES":
                    in_entities_section = True
                    i += 2
                    continue
            
            if code == "ENDSEC":
                if in_entities_section:
                    break
            
            if code == "EOF":
                break
            
            # 尝试解析实体
            if in_entities_section and code == "0":
                entity_type_str = value
                
                # 收集实体属性直到下一个实体或结束
                entity_data = []
                i += 1
                while i < len(pairs):
                    c, v = pairs[i]
                    if c == "0" and v in ("LINE", "POLYLINE", "LWPOLYLINE", "CIRCLE", 
                                          "ARC", "TEXT", "MTEXT", "POINT", "INSERT",
                                          "SOLID", "ENDSEC", "EOF"):
                        break
                    entity_data.append((c, v))
                    i += 1
                
                # 解析具体实体
                entity = self._parse_entity(entity_type_str, entity_data)
                if entity:
                    entities.append(entity)
                
                continue
            
            i += 1
        
        self._entities = entities
        return entities
    
    def _tokenize(self, content: str) -> List[Tuple[str, str]]:
        """将DXF文本分割为(组码, 值)对"""
        lines = content.split('\n')
        pairs = []
        i = 0
        
        while i < len(lines):
            # 跳过空行
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # 组码
            code = line
            i += 1
            
            # 值
            value = lines[i].strip() if i < len(lines) else ""
            i += 1
            
            pairs.append((code, value.rstrip('\r')))
        
        return pairs
    
    def _parse_entity(self, entity_type: str, data: List[Tuple[str, str]]) -> Optional[DxfEntity]:
        """解析单个实体的属性"""
        try:
            etype = EntityType(entity_type.upper())
        except ValueError:
            return None
        
        attrs = dict(data)
        
        layer = attrs.get("8", "0")       # 图层代码
        handle = attrs.get("5", "")        # 句柄
        color_str = attrs.get("62", "256")
        
        entity = DxfEntity(
            entity_type=etype,
            layer=layer,
            handle=handle,
            color=int(color_str) if color_str.isdigit() else 256,
        )
        
        if etype == EntityType.LINE:
            entity.points = [
                Point2D(float(attrs.get("10", 0)), float(attrs.get("20", 0))),
                Point2D(float(attrs.get("11", 0)), float(attrs.get("21", 0))),
            ]
        
        elif etype == EntityType.LWPOLYLINE:
            entity.points = self._parse_lwpolyline_points(attrs)
        
        elif etype == EntityType.CIRCLE:
            entity.center = Point2D(float(attrs.get("10", 0)), float(attrs.get("20", 0)))
            entity.radius = float(attrs.get("40", 0))
        
        elif etype == EntityType.ARC:
            entity.center = Point2D(float(attrs.get("10", 0)), float(attrs.get("20", 0)))
            entity.radius = float(attrs.get("40", 0))
            entity.start_angle = float(attrs.get("50", 0))
            entity.end_angle = float(attrs.get("51", 360))
        
        elif etype in (EntityType.TEXT, EntityType.MTEXT):
            entity.insertion_point = Point2D(float(attrs.get("10", 0)), float(attrs.get("20", 0)))
            entity.text = attrs.get("1", attrs.get("300", ""))
            entity.height = float(attrs.get("40", 2.5))
            entity.points = [entity.insertion_point]
        
        elif etype == EntityType.POINT:
            entity.points = [
                Point2D(float(attrs.get("10", 0)), float(attrs.get("20", 0)))
            ]
        
        elif etype == EntityType.INSERT:
            entity.block_name = attrs.get("2", "")
            entity.insertion_point = Point2D(float(attrs.get("10", 0)), float(attrs.get("20", 0)))
            entity.points = [entity.insertion_point]
        
        self._layers.add(layer)
        return entity
    
    def _parse_lwpolyline_points(self, attrs: Dict) -> List[Point2D]:
        """解析LWPOLYLINE的顶点数据"""
        points = []
        
        # 顶点数量
        num_vertices = int(attrs.get("90", 0))
        
        # 顶点数据存储在代码10/20中 (重复出现)
        x_values = []
        y_values = []
        
        # 需要从原始数据中按顺序提取 (这里简化处理)
        flags = int(attrs.get("70", 0))      # 标志位
        is_closed = bool(flags & 1)           # 闭合多边形
        
        # 从attrs提取所有10/20代码的值
        keys = sorted(attrs.keys())
        x_codes = [k for k in keys if k == "10"]
        y_codes = [k for k in keys if k == "20"]
        
        for xc in x_codes:
            x_values.append(float(attrs[xc]))
        for yc in y_codes:
            y_values.append(float(attrs[yc]))
        
        count = min(len(x_values), len(y_values), num_vertices if num_vertices > 0 else max(len(x_values), len(y_values)))
        
        for i in range(count):
            points.append(Point2D(x_values[i], y_values[i]))
        
        return points
    
    def _classify_entity(self, entity: DxfEntity):
        """基于图层名和几何特征分类"""
        layer_lower = entity.layer.lower().strip()
        
        # 精确匹配
        if layer_lower in LAYER_RULES:
            entity.classification = LAYER_RULES[layer_lower]
            entity.confidence = 0.95
            return
        
        # 模糊匹配 (包含关系)
        for pattern, classification in LAYER_RULES.items():
            if pattern in layer_lower or layer_lower in pattern:
                entity.classification = classification
                entity.confidence = 0.7
                return
        
        # 基于几何特征推断
        if entity.entity_type in (EntityType.TEXT, EntityType.MTEXT):
            entity.classification = LayerClassification.LABEL
            entity.confidence = 0.6
        elif entity.entity_type == EntityType.CIRCLE:
            if "充电" in entity.layer or "charge" in entity.layer.lower():
                entity.classification = LayerClassification.EQUIPMENT
            else:
                entity.classification = LayerClassification.NODE
            entity.confidence = 0.5
        elif entity.entity_type in (EntityType.LINE, EntityType.LWPOLYLINE, EntityType.POLYLINE):
            entity.classification = LayerClassification.PATH
            entity.confidence = 0.4
        elif entity.entity_type == EntityType.INSERT:
            entity.classification = LayerClassification.EQUIPMENT
            entity.confidence = 0.6
        
        entity.confidence = entity.confidence if hasattr(entity, 'confidence') else 0.3
    
    def _generate_topology(self, entities: List[DxfEntity]) -> Tuple[List, List, List]:
        """从解析的实体生成拓扑图"""
        nodes = []
        edges = []
        equipment = []
        node_id_counter = [0]
        edge_id_counter = [0]
        
        def next_node_id():
            nid = f"N{node_id_counter[0]:04d}"
            node_id_counter[0] += 1
            return nid
        
        def next_edge_id():
            eid = f"E{edge_id_counter[0]:04d}"
            edge_id_counter[0] += 1
            return eid
        
        # 提取节点
        for entity in entities:
            transformed_points = [self._transform.transform(p) for p in entity.points]
            
            if entity.classification == LayerClassification.NODE:
                if entity.entity_type == EntityType.POINT or (entity.entity_type == EntityType.CIRCLE and entity.radius < 1.0):
                    center = entity.center or (transformed_points[0] if transformed_points else Point2D(0, 0))
                    tc = self._transform.transform(center)
                    nodes.append({
                        "id": next_node_id(),
                        "x": round(tc.x, 4),
                        "y": round(tc.y, 4),
                        "type": "path",
                        "source_layer": entity.layer,
                        "original_cad_position": {"x": center.x, "y": center.y} if entity.center else None,
                    })
            
            elif entity.classification == LayerClassification.EQUIPMENT:
                pos = entity.insertion_point or (entity.center or (transformed_points[0] if transformed_points else Point2D(0, 0)))
                tp = self._transform.transform(pos)
                
                eq_type = "unknown"
                name = entity.text or entity.block_name or entity.layer
                name_lower = name.lower()
                
                if any(kw in name_lower for kw in ["充电", "charge", "chrg"]):
                    eq_type = "charger"
                elif any(kw in name_lower for kw in ["货架", "rack", "shelf"]):
                    eq_type = "rack"
                elif any(kw in name_lower for kw in ["输送", "conveyor", "belt"]):
                    eq_type = "conveyor"
                elif any(kw in name_lower for kw in ["工位", "station", "work"]):
                    eq_type = "workstation"
                elif entity.entity_type == EntityType.CIRCLE:
                    eq_type = "parking"
                
                equipment.append({
                    "id": f"EQ-{len(equipment):03d}",
                    "type": eq_type,
                    "name": name,
                    "x": round(tp.x, 4),
                    "y": round(tp.y, 4),
                    "layer": entity.layer,
                })
                
                # 同时创建关联节点
                nodes.append({
                    "id": next_node_id(),
                    "x": round(tp.x, 4),
                    "y": round(tp.y, 4),
                    "type": eq_type if eq_type != "unknown" else "pickup",
                    "name": name,
                    "source_layer": entity.layer,
                })
        
        # 提取边 (从路径线段)
        path_entities = [e for e in entities if e.classification == LayerClassification.PATH 
                        and e.entity_type in (EntityType.LINE, EntityType.LWPOLYLINE)]
        
        for entity in path_entities:
            tpoints = [self._transform.transform(p) for p in entity.points]
            
            if len(tpoints) >= 2:
                # LINE: 一条边
                if entity.entity_type == EntityType.LINE:
                    dist = tpoints[0].distance_to(tpoints[1])
                    edges.append({
                        "id": next_edge_id(),
                        "from": f"N_{tpoints[0].x:.1f}_{tpoints[0].y:.1f}",
                        "to": f"N_{tpoints[1].x:.1f}_{tpoints[1].y:.1f}",
                        "distance": round(dist, 4),
                        "direction": "bidirectional",
                        "source_layer": entity.layer,
                    })
                
                # LWPOLYLINE: 多条边连接相邻顶点
                elif entity.entity_type == EntityType.LWPOLYLINE:
                    for i in range(len(tpoints) - 1):
                        dist = tpoints[i].distance_to(tpoints[i+1])
                        if dist > 0.01:  # 过滤太短的边 (< 1cm)
                            edges.append({
                                "id": next_edge_id(),
                                "from": f"N_{tpoints[i].x:.1f}_{tpoints[i].y:.1f}",
                                "to": f"N_{tpoints[i+1].x:.1f}_{tpoints[i+1].y:.1f}",
                                "distance": round(dist, 4),
                                "direction": "bidirectional",
                                "source_layer": entity.layer,
                            })
        
        return nodes, edges, equipment
    
    def get_detected_layers(self) -> List[Dict]:
        """获取检测到的图层及其分类"""
        layers_info = []
        for layer in sorted(self._layers):
            entities_on_layer = [e for e in self._entities if e.layer == layer]
            classifications = set(e.classification.value for e in entities_on_layer)
            
            layers_info.append({
                "name": layer,
                "entity_count": len(entities_on_layer),
                "suggested_classification": classifications.pop() if len(classifications) == 1 else "mixed",
                "entity_types": list(set(e.entity_type.value for e in entities_on_layer)),
            })
        
        return layers_info
    
    def set_coordinate_transform(self, transform: CoordinateTransform):
        """自定义坐标变换参数"""
        self._transform = transform


# ══════════════════════════════════════════════════════════
# FastAPI Router - 地图导入API
# ══════════════════════════════════════════════════════════

import os
import tempfile
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Form
from typing import Optional

map_import_router = APIRouter(prefix="/api/v3/map-import", tags=["Map Import (CAD/DXF)"])

# 支持的格式
SUPPORTED_FORMATS = {".dxf", ".json"}
MAX_FILE_SIZE_MB = 50


@map_import_router.post("/upload")
async def upload_map_file(file: UploadFile = File(...)):
    """
    上传并解析地图文件 (DXF 或 JSON)
    
    支持的格式:
    - .dxf: AutoCAD DXF 文件 (ASCII格式)
    - .json: AgvTms JSON地图配置
    
    返回解析结果，包含生成的节点、边、设备位置。
    """
    # 验证文件扩展名
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {ext}. Supported: {', '.join(SUPPORTED_FORMATS)}"
        )
    
    # 读取上传内容
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_FILE_SIZE_MB}MB)")
    
    # 保存到临时文件
    suffix = ext
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        parser = DxfParser()
        
        if ext == ".dxf":
            result = parser.parse_file(tmp_path)
        else:
            result = parser.parse_json_map(tmp_path)
        
        # 附加额外信息
        result.layers_detected = parser.get_detected_layers()
        
        return result.to_dict()
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse failed: {str(e)}")
    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except:
            pass


@map_import_router.post("/import-url")
async def import_from_url(url: str, format: str = "auto"):
    """从URL导入地图文件 (支持DXF/JSON)"""
    # TODO: 实现URL下载功能
    raise HTTPException(status_code=501, detail="URL import not yet implemented")


@map_import_router.post("/validate-map-data")
async def validate_map_data(map_data: dict):
    """
    验证地图数据的完整性和一致性
    
    检查项目:
    - 节点ID唯一性
    - 边引用的有效性
    - 连通性检查
    - 坐标范围合理性
    """
    issues = []
    warnings = []
    
    nodes = map_data.get("nodes", [])
    edges = map_data.get("edges", [])
    
    # 节点ID唯一性
    node_ids = [n.get("id") for n in nodes]
    duplicates = [nid for nid in set(node_ids) if node_ids.count(nid) > 1]
    if duplicates:
        issues.append({"level": "error", "message": f"Duplicate node IDs: {duplicates}"})
    
    # 边引用验证
    node_id_set = set(node_ids)
    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        if from_id not in node_id_set:
            issues.append({"level": "error", 
                          "message": f"Edge references unknown node: {from_id}"})
        if to_id not in node_id_set:
            issues.append({"level": "error",
                          "message": f"Edge references unknown node: {to_id}"})
    
    # 连通性检查 (简单版本)
    if nodes and not edges:
        warnings.append({"level": "warning", "message": "No edges defined, graph may be disconnected"})
    
    # 坐标范围
    if nodes:
        xs = [n.get("x", 0) for n in nodes]
        ys = [n.get("y, 0") for n in nodes]
        x_range = max(xs) - min(xs)
        y_range = max(ys) - min(ys)
        
        if x_range > 10000 or y_range > 10000:
            warnings.append({"level": "warning", 
                           "message": f"Very large coordinate range: {x_range:.0f} x {y_range:.0f}m. Check units."})
        if x_range < 1.0 and y_range < 1.0:
            warnings.append({"level": "warning",
                           "message": f"Very small coordinate range: {x_range:.2f} x {y_range:.2f}m. May need scaling."})
    
    return {
        "is_valid": len(issues) == 0,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "issues": issues,
        "warnings": warnings,
    }


@map_import_router.get("/layer-rules")
async def get_layer_rules():
    """获取内置的图层命名规则 (用于前端提示用户)"""
    rules_by_category = {}
    for pattern, classification in LAYER_RULES.items():
        cat = classification.value
        if cat not in rules_by_category:
            rules_by_category[cat] = []
        rules_by_category[cat].append(pattern)
    
    return {"rules": rules_by_category, "categories": [c.value for c in LayerClassification]}


@map_import_router.post("/coordinate-transform")
async def set_coordinate_transform(
    scale: float = Form(0.001),
    offset_x: float = Form(0.0),
    offset_y: float = Form(0.0),
    rotation: float = Form(0.0),
    flip_y: bool = Form(True),
):
    """设置坐标变换参数"""
    transform = CoordinateTransform(
        scale=scale,
        offset_x=offset_x,
        offset_y=offset_y,
        rotation_degrees=rotation,
        flip_y=flip_y,
    )
    
    # 示例变换
    test_point = Point2D(1000, 2000)  # CAD坐标 (mm)
    transformed = transform.transform(test_point)
    
    return {
        "transform_config": transform.to_dict(),
        "example": {
            "cad_input": test_point.to_dict(),
            "world_output": transformed.to_dict(),
        },
    }


# 导出
__all__ = [
    'DxfParser', 'DxfEntity', 'ImportResult', 'CoordinateTransform',
    'Point2D', 'EntityType', 'LayerClassification',
    'map_import_router', 'LAYER_RULES',
]
