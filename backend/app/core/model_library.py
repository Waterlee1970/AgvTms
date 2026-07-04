"""
3D Model Library - Digital Twin Asset Management
对标 Plant Mirror 内置装备库

功能:
1. GLTF/GLB 模型加载与缓存
2. 程序化AGV/输送线/货架模型生成
3. LOD (Level of Detail) 细节层次管理
4. 模型材质与动画状态管理
5. 工业装备类型定义

技术栈: Three.js r160 + GLTFLoader + React Three Fiber
Author: Digital Twin Team
Date: 2026-07-05
"""

import json
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# ══════════════════════════════════════════════════════════
# 工业装备类型枚举 (对标 Plant Mirror 分类体系)
# ══════════════════════════════════════════════════════════

class EquipmentCategory(str, Enum):
    """装备大类 (Plant Mirror 三大分类)"""
    
    # 🤖 AMR智能装备
    AMR_JACK = "amr_jack"              # 潜伏顶升机器人
    AMR_FORKLIFT = "amr_forklift"       # 叉车AGV
    AMR_BOX = "amr_box"                 # 料箱机器人
    AMR_COMPOSITE = "amr_composite"     # 复合移动机器人
    AMR_SORTER = "amr_sorter"           # 分拣机器人
    AMR_TOWING = "amr_towing"           # 牵引式AGV
    
    # 🏭 传统装备
    CONVEYOR_BELT = "conveyor_belt"      # 输送带
    CONVEYOR_ROLLER = "conveyor_roller"  # 辊筒线
    ELEVATOR = "elevator"                # 提升机
    SHUTTLE = "shuttle"                  # 穿梭车
    TURN_TABLE = "turn_table"            # 旋转台
    WRAPPING = "wrapping"                # 打包台
    
    # 🌐 工业生态
    WORKSTATION = "workstation"          # 工作站/机台
    RACK_SHELF = "rack_shelf"            # 货架
    SAFETY_FENCE = "safety_fence"        # 安全围栏
    CHARGER_STATION = "charger_station"  # 充电桩
    PARKING_SLOT = "parking_slot"        # 停车位
    PICKUP_POINT = "pickup_point"        # 取货点
    DROPOFF_POINT = "dropoff_point"      # 放货点


class EquipmentState(str, Enum):
    """装备运行状态"""
    IDLE = "idle"
    RUNNING = "running"
    WARNING = "warning"
    ERROR = "error"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class LODLevel(int, Enum):
    """细节层次级别"""
    LOW = 0       # 远距离: 简化几何体 (< 500面)
    MEDIUM = 1    # 中距离: 标准细节 (~2000面)
    HIGH = 2      # 近距离: 高精度 (~10000面)
    ULTRA = 3     # 特写: 最高精度 (> 50000面)


# ══════════════════════════════════════════════════════════
# 3D模型数据结构
# ══════════════════════════════════════════════════════════

@dataclass
class ModelMetadata:
    """3D模型元数据"""
    model_id: str
    name: str
    category: EquipmentCategory
    version: str = "1.0.0"
    author: str = "AgvTms"
    
    # 文件信息
    file_path: Optional[str] = None       # GLTF/GLB文件路径
    file_size_kb: int = 0                 # 文件大小 KB
    
    # 几何信息
    vertices_count: int = 0               # 顶点数
    faces_count: int = 0                  # 面数
    bounding_box: Optional[Dict] = None   # 包围盒 {min, max}
    
    # LOD信息
    lod_levels: Dict[int, Dict] = field(default_factory=dict)
    # {0: {vertices: 100, url: "..."}, 1: {vertices: 1000, url: "..."}}
    
    # 动画信息
    has_animation: bool = False
    animation_names: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "category": self.category.value,
            "version": self.version,
            "file_path": self.file_path,
            "file_size_kb": self.file_size_kb,
            "vertices_count": self.vertices_count,
            "faces_count": self.faces_count,
            "bounding_box": self.bounding_box,
            "lod_levels": self.lod_levels,
            "has_animation": self.has_animation,
            "animation_names": self.animation_names,
        }


@dataclass 
class ProceduralModelConfig:
    """
    程序化模型配置 (用于无外部模型时的 fallback)
    
    基于 Three.js 基础几何体组合生成工业装备模型
    """
    equipment_type: EquipmentCategory
    
    # 外观参数
    primary_color: str = "#4A90D9"         # 主体颜色
    secondary_color: str = "#2C5282"       # 次要颜色
    accent_color: str = "#F6AD55"          # 强调色 (如警示灯)
    
    # 尺寸参数 (米)
    length: float = 1.5                    # 长度
    width: float = 1.0                     # 宽度
    height: float = 0.4                    # 高度
    
    # AGV特有参数
    wheel_radius: float = 0.15             # 轮子半径
    fork_height: float = 0.3               # 叉齿高度 (叉车)
    lift_capacity: float = 1000.0          # 载重 kg
    
    # 细节级别
    show_label: bool = True                # 显示标签
    show_battery_indicator: bool = True    # 电量指示器
    show_status_light: bool = True         # 状态灯
    
    # 材质参数
    metalness: float = 0.3                 # 金属度
    roughness: float = 0.7                 # 粗糙度
    opacity: float = 1.0                   # 透明度


@dataclass
class ModelInstance:
    """场景中的模型实例"""
    instance_id: str
    model_id: str                          # 引用的模型ID
    position: Dict[str, float]             # {x, y, z}
    rotation: Dict[str, float]             # {x, y, z} Euler angles (度)
    scale: Dict[str, float] = field(default_factory=lambda: {"x": 1.0, "y": 1.0, "z": 1.0})
    
    # 运行时状态
    state: EquipmentState = EquipmentState.IDLE
    battery_level: float = 100.0           # 0~100%
    speed: float = 0.0                     # m/s
    current_task: str = ""
    
    # 显示属性
    visible: bool = True
    highlight: bool = False                # 高亮选中
    color_override: Optional[str] = None   # 颜色覆盖
    
    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "model_id": self.model_id,
            "position": self.position,
            "rotation": self.rotation,
            "scale": self.scale,
            "state": self.state.value,
            "battery_level": self.battery_level,
            "speed": self.speed,
            "current_task": self.current_task,
            "visible": self.visible,
            "highlight": self.highlight,
            "color_override": self.color_override,
        }


# ══════════════════════════════════════════════════════════
# 3D模型库管理器 (Singleton)
# ══════════════════════════════════════════════════════════

class ModelLibrary:
    """
    3D模型库管理器
    
    功能:
    - 模型注册与元数据管理
    - 程序化模型生成配置
    - LOD策略计算
    - 模型查找与过滤
    
    使用示例:
        library = ModelLibrary()
        
        # 注册内置模型
        library.register_builtin_models()
        
        # 获取AGV模型配置
        agv_config = library.get_procedural_config(EquipmentCategory.AMR_JACK)
        
        # 计算LOD等级
        lod = library.calculate_lod(distance=50.0, screen_size=0.1)
    """
    
    _instance: Optional['ModelLibrary'] = None
    
    def __init__(self):
        self._models: Dict[str, ModelMetadata] = {}
        self._procedural_configs: Dict[EquipmentCategory, ProceduralModelConfig] = {}
        self._instances: Dict[str, ModelInstance] = {}
        self._initialized = False
        
        # LOD切换阈值 (距离单位: 米)
        self._lod_thresholds = {
            LODLevel.LOW: 80.0,      # > 80m 使用最低LOD
            LODLevel.MEDIUM: 40.0,    # > 40m 使用中LOD
            LODLevel.HIGH: 10.0,      # > 10m 使用高LOD
            LODLevel.ULTRA: 0.0,      # < 10m 使用最高LOD
        }
    
    @classmethod
    def get_instance(cls) -> 'ModelLibrary':
        """获取单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def initialize(self):
        """初始化模型库 (加载内置模型)"""
        if self._initialized:
            return
        
        self._register_agv_models()
        self._register_conveyor_models()
        self._register_industrial_models()
        self._initialized = True
    
    # ═════════════════════════════════════════════════════
    # 内置模型注册
    # ═════════════════════════════════════════════════════
    
    def _register_agv_models(self):
        """注册AMR智能装备模型"""
        
        # --- 潜伏顶升机器人 (最常用) ---
        self._models["amr_jack_v1"] = ModelMetadata(
            model_id="amr_jack_v1",
            name="潜伏顶升机器人 JackV1",
            category=EquipmentCategory.AMR_JACK,
            version="1.2.0",
            vertices_count=1250,
            faces_count=840,
            bounding_box={"min": [-0.75, 0, -0.5], "max": [0.75, 0.45, 0.5]},
            lod_levels={
                0: {"vertices": 120, "description": "Box简化体"},
                1: {"vertices": 600, "description": "标准+轮子"},
                2: {"vertices": 1250, "description": "完整含顶升机构"},
                3: {"vertices": 3500, "description": "高精含螺丝细节"},
            },
            has_animation=True,
            animation_names=["lift_up", "lift_down", "wheel_rotate"],
        )
        self._procedural_configs[EquipmentCategory.AMR_JACK] = ProceduralModelConfig(
            equipment_type=EquipmentCategory.AMR_JACK,
            primary_color="#4A90D9",
            secondary_color="#2C5282",
            accent_color="#48BB78",
            length=1.5, width=1.0, height=0.35,
            wheel_radius=0.12,
            lift_capacity=1500.0,
        )
        
        # --- 叉车AGV ---
        self._models["amr_forklift_v1"] = ModelMetadata(
            model_id="amr_forklift_v1",
            name="叉车AGV ForkliftV1",
            category=EquipmentCategory.AMR_FORKLIFT,
            version="1.0.0",
            vertices_count=2100,
            faces_count=1450,
            bounding_box={"min": [-1.2, 0, -0.8], "max": [1.2, 1.5, 0.8]},
            lod_levels={
                0: {"vertices": 180},
                1: {"vertices": 900},
                2: {"vertices": 2100},
                3: {"vertices": 5500},
            },
            has_animation=True,
            animation_names=["fork_lift", "fork_lower", "mast_tilt"],
        )
        self._procedural_configs[EquipmentCategory.AMR_FORKLIFT] = ProceduralModelConfig(
            equipment_type=EquipmentCategory.AMR_FORKLIFT,
            primary_color="#E53E3E",
            secondary_color="#C53030",
            accent_color="#DD6B20",
            length=2.4, width=1.2, height=1.4,
            fork_height=1.2,
            lift_capacity=2000.0,
        )
        
        # --- 料箱机器人 ---
        self._models["amr_box_v1"] = ModelMetadata(
            model_id="amr_box_v1",
            name="料箱机器人 BoxRobotV1",
            category=EquipmentCategory.AMR_BOX,
            version="1.0.0",
            vertices_count=1680,
            faces_count=1120,
            lod_levels={
                0: {"vertices": 150},
                1: {"vertices": 780},
                2: {"vertices": 1680},
                3: {"vertices": 4200},
            },
            has_animation=True,
            animation_names=["gripper_close", "gripper_open", "rotate"],
        )
        self._procedural_configs[EquipmentCategory.AMR_BOX] = ProceduralModelConfig(
            equipment_type=EquipmentCategory.AMR_BOX,
            primary_color="#9F7AEA",
            secondary_color="#6B46C1",
            accent_color="#F6E05E",
            length=1.2, width=0.8, height=0.6,
            lift_capacity=50.0,
        )
        
        # --- 其他AMR类型占位符 ---
        for cat in [EquipmentCategory.AMR_COMPOSITE, EquipmentCategory.AMR_SORTER, 
                    EquipmentCategory.AMR_TOWING]:
            self._procedural_configs[cat] = ProceduralModelConfig(
                equipment_type=cat,
                primary_color="#38B2AC",
                secondary_color="#2C7A7B",
                accent_color="#ED8936",
            )
    
    def _register_conveyor_models(self):
        """注册传统输送设备模型"""
        
        # --- 输送带 ---
        self._models["conveyor_belt_straight"] = ModelMetadata(
            model_id="conveyor_belt_straight",
            name="直线输送带",
            category=EquipmentCategory.CONVEYOR_BELT,
            version="1.0.0",
            vertices_count=450,
            faces_count=300,
            lod_levels={
                0: {"vertices": 40, "description": "长方体"},
                1: {"vertices": 220},
                2: {"vertices": 450},
                3: {"vertices": 1200, "description": "含滚筒细节"},
            },
            has_animation=True,
            animation_names=["belt_move"],
        )
        self._procedural_configs[EquipmentCategory.CONVEYOR_BELT] = ProceduralModelConfig(
            equipment_type=EquipmentCategory.CONVEYOR_BELT,
            primary_color="#718096",
            secondary_color="#4A5568",
            accent_color="#A0AEC0",
            length=3.0, width=0.5, height=0.15,
            metalness=0.6,
            roughness=0.4,
        )
        
        # --- 其他输送设备 ---
        for cat in [EquipmentCategory.CONVEYOR_ROLLER, EquipmentCategory.ELEVATOR,
                    EquipmentCategory.SHUTTLE, EquipmentCategory.TURN_TABLE,
                    EquipmentCategory.WRAPPING]:
            self._procedural_configs[cat] = ProceduralModelConfig(
                equipment_type=cat,
                primary_color="#A0AEC0",
                secondary_color="#718096",
                accent_color="#63B3ED",
            )
    
    def _register_industrial_models(self):
        """注册工业生态设施模型"""
        
        # --- 工作站 ---
        self._models["workstation_standard"] = ModelMetadata(
            model_id="workstation_standard",
            name="标准工作站",
            category=EquipmentCategory.WORKSTATION,
            version="1.0.0",
            vertices_count=800,
            faces_count=550,
            lod_levels={0: {"vertices": 60}, 1: {"vertices": 350}, 
                       2: {"vertices": 800}, 3: {"vertices": 2000}},
        )
        
        # --- 充电桩 ---
        self._models["charger_station_v1"] = ModelMetadata(
            model_id="charger_station_v1",
            name="自动充电桩",
            category=EquipmentCategory.CHARGER_STATION,
            version="1.0.0",
            vertices_count=320,
            faces_count=200,
            lod_levels={0: {"vertices": 30}, 1: {"vertices": 150}, 
                       2: {"vertices": 320}, 3: {"vertices": 800}},
            has_animation=True,
            animation_names=["charging_pulse"],
        )
        
        # --- 其他工业设施 ---
        for cat in [EquipmentCategory.RACK_SHELF, EquipmentCategory.SAFETY_FENCE,
                    EquipmentCategory.PARKING_SLOT, EquipmentCategory.PICKUP_POINT,
                    EquipmentCategory.DROPOFF_POINT]:
            self._procedural_configs[cat] = ProceduralModelConfig(
                equipment_type=cat,
                primary_color="#CBD5E0",
                secondary_color="#A0AEC0",
                accent_color="#48BB78",
            )
    
    # ═════════════════════════════════════════════════════
    # 模型查询接口
    # ═════════════════════════════════════════════════════
    
    def get_model(self, model_id: str) -> Optional[ModelMetadata]:
        """获取模型元数据"""
        return self._models.get(model_id)
    
    def get_models_by_category(self, category: EquipmentCategory) -> List[ModelMetadata]:
        """按类别获取所有模型"""
        return [m for m in self._models.values() if m.category == category]
    
    def get_all_models(self) -> List[ModelMetadata]:
        """获取所有已注册模型"""
        return list(self._models.values())
    
    def get_procedural_config(self, category: EquipmentCategory) -> ProceduralModelConfig:
        """获取程序化模型配置"""
        if category not in self._procedural_configs:
            # 返回默认配置
            return ProceduralModelConfig(equipment_type=category)
        return self._procedural_configs[category]
    
    def search_models(self, query: str) -> List[ModelMetadata]:
        """搜索模型 (按名称或ID模糊匹配)"""
        query_lower = query.lower()
        results = []
        for model in self._models.values():
            if (query_lower in model.name.lower() or 
                query_lower in model.model_id.lower() or
                query_lower in model.category.value):
                results.append(model)
        return results
    
    # ═════════════════════════════════════════════════════
    # LOD 管理
    # ═════════════════════════════════════════════════════
    
    def calculate_lod(self, distance: float, 
                      camera_fov: float = 60.0,
                      screen_pixel_size: Optional[float] = None) -> LODLevel:
        """
        计算合适的LOD级别
        
        Args:
            distance: 相机到物体的距离 (米)
            camera_fov: 相机视场角 (度)
            screen_pixel_size: 物体在屏幕上的像素大小 (可选，更精确)
        
        Returns:
            推荐的LOD级别
        """
        # 如果提供了屏幕像素大小，使用更精确的方法
        if screen_pixel_size is not None:
            if screen_pixel_size < 20:
                return LODLevel.LOW
            elif screen_pixel_size < 80:
                return LODLevel.MEDIUM
            elif screen_pixel_size < 200:
                return LODLevel.HIGH
            else:
                return LODLevel.ULTRA
        
        # 否则基于距离判断
        if distance >= self._lod_thresholds[LODLevel.LOW]:
            return LODLevel.LOW
        elif distance >= self._lod_thresholds[LODLevel.MEDIUM]:
            return LODLevel.MEDIUM
        elif distance >= self._lod_thresholds[LODLevel.HIGH]:
            return LODLevel.HIGH
        else:
            return LODLevel.ULTRA
    
    def get_lod_model_info(self, model_id: str, lod_level: LODLevel) -> Optional[Dict]:
        """获取特定LOD级别的模型信息"""
        model = self._models.get(model_id)
        if not model or lod_level.value not in model.lod_levels:
            return None
        return {
            "model_id": model_id,
            "lod_level": lod_level.value,
            **model.lod_levels[lod_level.value],
            "base_model": model.name,
        }
    
    def set_lod_threshold(self, level: LODLevel, distance: float):
        """自定义LOD切换阈值"""
        self._lod_thresholds[level] = distance
    
    # ═════════════════════════════════════════════════════
    # 场景实例管理
    # ═════════════════════════════════════════════════════
    
    def create_instance(self, instance_id: str, model_id: str,
                        position: Dict[str, float],
                        rotation: Dict[str, float]) -> Optional[ModelInstance]:
        """创建模型实例"""
        if model_id not in self._models:
            return None
        
        instance = ModelInstance(
            instance_id=instance_id,
            model_id=model_id,
            position=position,
            rotation=rotation,
        )
        self._instances[instance_id] = instance
        return instance
    
    def get_instance(self, instance_id: str) -> Optional[ModelInstance]:
        """获取实例"""
        return self._instances.get(instance_id)
    
    def update_instance_state(self, instance_id: str, **kwargs):
        """更新实例状态"""
        instance = self._instances.get(instance_id)
        if instance:
            for key, value in kwargs.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
    
    def remove_instance(self, instance_id: str):
        """移除实例"""
        self._instances.pop(instance_id, None)
    
    def get_all_instances(self) -> List[ModelInstance]:
        """获取所有实例"""
        return list(self._instances.values())
    
    def get_instances_by_model(self, model_id: str) -> List[ModelInstance]:
        """获取某模型的所有实例"""
        return [inst for inst in self._instances.values() if inst.model_id == model_id]
    
    # ═════════════════════════════════════════════════════
    # 导出/序列化
    # ═════════════════════════════════════════════════════
    
    def to_library_manifest(self) -> dict:
        """导出模型库清单 (供前端加载)"""
        return {
            "version": "1.0.0",
            "total_models": len(self._models),
            "categories": {
                cat.value: len([m for m in self._models.values() if m.category == cat])
                for cat in EquipmentCategory
            },
            "models": [m.to_dict() for m in self._models.values()],
            "lod_thresholds": {k.value: v for k, v in self._lod_thresholds.items()},
        }
    
    def to_scene_description(self) -> dict:
        """导出当前场景描述"""
        return {
            "timestamp": time.time(),
            "total_instances": len(self._instances),
            "instances": [inst.to_dict() for inst in self._instances.values()],
        }
    
    def get_stats(self) -> dict:
        """获取库统计信息"""
        return {
            "total_models": len(self._models),
            "total_instances": len(self._instances),
            "categories": {cat.value: len([m for m in self._models.values() if m.category == cat])
                         for cat in EquipmentCategory},
            "models_with_lod": sum(1 for m in self._models.values() if m.lod_levels),
            "animated_models": sum(1 for m in self._models.values() if m.has_animation),
        }


import time


# ══════════════════════════════════════════════════════════
# 前端 Three.js 配置输出工具
# ══════════════════════════════════════════════════════════

def generate_threejs_geometry_config(config: ProceduralModelConfig) -> dict:
    """
    将程序化模型配置转换为 Three.js 几何体参数
    
    用于前端 DigitalTwin3D.tsx 或 ThreeDigitalTwin 组件渲染
    """
    base = {
        "equipment_type": config.equipment_type.value,
        "dimensions": {
            "length": config.length,
            "width": config.width,
            "height": config.height,
        },
        "materials": {
            "primary": {"color": config.primary_color, 
                       "metalness": config.metalness, 
                       "roughness": config.roughness,
                       "opacity": config.opacity},
            "secondary": {"color": config.secondary_color,
                         "metalness": config.metalness * 0.8,
                         "roughness": config.roughness * 1.2},
            "accent": {"color": config.accent_color,
                      "emissive": config.accent_color,
                      "emissiveIntensity": 0.5},
        },
        "features": {
            "label": config.show_label,
            "battery_indicator": config.show_battery_indicator,
            "status_light": config.show_status_light,
        },
    }
    
    # AGV特有部件
    if config.equipment_type.value.startswith("amr_"):
        base["agv_parts"] = {
            "wheels": {"radius": config.wheel_radius, "count": 4},
        }
        if config.equipment_type == EquipmentCategory.AMR_FORKLIFT:
            base["agv_parts"]["fork"] = {"height": config.fork_height}
        base["specs"] = {
            "lift_capacity_kg": config.lift_capacity,
        }
    
    return base


def export_library_to_json(output_path: str = None) -> str:
    """
    导出完整模型库为JSON文件
    
    Returns:
        JSON字符串或写入文件后返回路径
    """
    library = ModelLibrary.get_instance()
    library.initialize()
    
    manifest = library.to_library_manifest()
    json_str = json.dumps(manifest, ensure_ascii=False, indent=2)
    
    if output_path:
        Path(output_path).write_text(json_str, encoding='utf-8')
        return output_path
    
    return json_str


# ══════════════════════════════════════════════════════════
# FastAPI Router - 模型库API
# ══════════════════════════════════════════════════════════

from fastapi import APIRouter, HTTPException, Query

model_router = APIRouter(prefix="/api/v3/models", tags=["3D Model Library"])

# 全局单例
_library: Optional[ModelLibrary] = None


def get_library() -> ModelLibrary:
    global _library
    if _library is None:
        _library = ModelLibrary.get_instance()
        _library.initialize()
    return _library


@model_router.get("/manifest")
async def get_model_manifest():
    """
    获取完整模型库清单
    
    返回所有可用模型的元数据、LOD信息和分类统计。
    前端应在初始化时调用此端点缓存模型列表。
    """
    lib = get_library()
    return lib.to_library_manifest()


@model_router.get("/list")
async def list_models(
    category: Optional[str] = Query(None, description="按类别过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """分页查询模型列表"""
    lib = get_library()
    
    models = lib.get_all_models()
    
    # 过滤
    if category:
        try:
            cat = EquipmentCategory(category)
            models = [m for m in models if m.category == cat]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    
    if search:
        models = lib.search_models(search)
    
    # 分页
    total = len(models)
    models = models[offset:offset + limit]
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [m.to_dict() for m in models],
    }


@model_router.get("/{model_id}")
async def get_model_detail(model_id: str):
    """获取模型详细信息 (含LOD配置)"""
    lib = get_library()
    model = lib.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    
    # 获取使用此模型的场景实例数量
    instances = lib.get_instances_by_model(model_id)
    
    return {
        **model.to_dict(),
        "instance_count": len(instances),
        "procedural_config": generate_threejs_geometry_config(
            lib.get_procedural_config(model.category)
        ),
    }


@model_router.get("/config/{category}")
async def get_equipment_config(category: str):
    """
    获取装备类型的程序化模型配置
    
    前端使用此配置在Three.js中动态生成3D几何体，
    当外部GLTF模型不可用时作为fallback方案。
    """
    try:
        cat = EquipmentCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category}")
    
    lib = get_library()
    config = lib.get_procedural_config(cat)
    
    return {
        "category": category,
        "threejs_config": generate_threejs_geometry_config(config),
        "metadata": {
            "has_gltf_model": cat.value in [m.model_id for m in lib.get_all_models()],
            "supported_states": [s.value for s in EquipmentState],
        },
    }


@model_router.get("/stats")
async def get_library_stats():
    """获取模型库统计信息"""
    lib = get_library()
    return lib.get_stats()


@model_router.get("/categories")
async def list_categories():
    """列出所有装备类别及其模型数量"""
    lib = get_library()
    
    categories = []
    for cat in EquipmentCategory:
        count = len(lib.get_models_by_category(cat))
        categories.append({
            "id": cat.value,
            "name": cat.name.replace("_", " "),
            "type": "AMR" if cat.value.startswith("amr_") else 
                   "Conveyor" if cat.value.startswith("conveyor") else 
                   "Industrial",
            "model_count": count,
        })
    
    return {"categories": categories}


# 导出便捷访问
__all__ = [
    'ModelLibrary', 'ModelMetadata', 'ProceduralModelConfig', 'ModelInstance',
    'EquipmentCategory', 'EquipmentState', 'LODLevel',
    'generate_threejs_geometry_config', 'export_library_to_json',
    'model_router',
]
