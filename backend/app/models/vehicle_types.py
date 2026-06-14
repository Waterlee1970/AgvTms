"""
Multi-Vehicle Type System.

Supports heterogeneous AGV fleets with different capabilities:
- Forklift (叉车): Heavy loads, lifting capability
- Latent (潜伏): Under-cart transport
- Lift (顶升): Top-lifting cart transport
- Sorter (分拣): Sorting conveyor integration

Each vehicle type has a capability matrix that determines
which tasks it can execute and what path constraints apply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class VehicleType(str, Enum):
    """AGV vehicle types (对标海康300+车型的基础分类)."""
    STANDARD = "standard"       # 标准搬运AGV
    FORKLIFT = "forklift"       # 叉车AGV
    LATENT = "latent"           # 潜伏AGV
    LIFT = "lift"               # 顶升AGV
    SORTER = "sorter"           # 分拣AGV
    TOWING = "towing"           # 牵引AGV
    CUSTOM = "custom"           # 自定义


class NavigationMethod(str, Enum):
    """Navigation methods supported by vehicle types."""
    QR_CODE = "qr_code"         # 二维码导航
    SLAM = "slam"              # SLAM导航
    LASER = "laser"            # 激光导航
    MAGNETIC = "magnetic"      # 磁条导航
    VSLAM = "vslam"           # 视觉SLAM


@dataclass
class VehicleCapability:
    """Capability matrix for a vehicle type."""
    vehicle_type: VehicleType
    max_load_kg: float = 100.0          # 最大载重
    max_speed_ms: float = 1.5           # 最大速度
    lifting_height_m: float = 0.0       # 举升高度 (0=无举升)
    turning_radius_m: float = 0.5       # 最小转弯半径
    width_m: float = 0.8               # 车体宽度
    length_m: float = 1.2              # 车体长度
    battery_capacity_kwh: float = 2.0   # 电池容量
    navigation_methods: Set[NavigationMethod] = field(default_factory=lambda: {NavigationMethod.QR_CODE})
    supports_docking: bool = True       # 是否支持对接
    supports_conveyor: bool = False     # 是否支持输送线接驳
    narrow_corridor_only: bool = False  # 是否仅限窄通道

    def can_handle_task(self, task_requirements: Dict[str, Any]) -> bool:
        """Check if this vehicle type can handle a task with given requirements."""
        # Weight check
        cargo_weight = task_requirements.get("cargo_weight", 0)
        if cargo_weight > self.max_load_kg:
            return False

        # Lifting check
        required_lift = task_requirements.get("lifting_height", 0)
        if required_lift > self.lifting_height_m:
            return False

        # Docking check
        requires_docking = task_requirements.get("requires_docking", False)
        if requires_docking and not self.supports_docking:
            return False

        # Conveyor check
        requires_conveyor = task_requirements.get("requires_conveyor", False)
        if requires_conveyor and not self.supports_conveyor:
            return False

        # Navigation check
        required_nav = task_requirements.get("navigation_method")
        if required_nav and required_nav not in self.navigation_methods:
            return False

        return True

    def can_traverse_corridor_width(self, corridor_width_m: float) -> bool:
        """Check if vehicle can fit through a corridor."""
        return self.width_m <= corridor_width_m


# Standard vehicle type capabilities
STANDARD_CAPABILITIES: Dict[VehicleType, VehicleCapability] = {
    VehicleType.STANDARD: VehicleCapability(
        vehicle_type=VehicleType.STANDARD,
        max_load_kg=100.0, max_speed_ms=1.5,
        width_m=0.8, length_m=1.2,
        supports_docking=True, supports_conveyor=True,
    ),
    VehicleType.FORKLIFT: VehicleCapability(
        vehicle_type=VehicleType.FORKLIFT,
        max_load_kg=1000.0, max_speed_ms=1.0,
        lifting_height_m=2.0,
        turning_radius_m=1.5, width_m=1.2, length_m=2.0,
        supports_docking=False, supports_conveyor=False,
    ),
    VehicleType.LATENT: VehicleCapability(
        vehicle_type=VehicleType.LATENT,
        max_load_kg=300.0, max_speed_ms=2.0,
        turning_radius_m=0.3, width_m=0.6, length_m=0.9,
        supports_docking=True, supports_conveyor=True,
        narrow_corridor_only=True,
    ),
    VehicleType.LIFT: VehicleCapability(
        vehicle_type=VehicleType.LIFT,
        max_load_kg=500.0, max_speed_ms=1.5,
        lifting_height_m=0.15,
        turning_radius_m=0.5, width_m=0.9, length_m=1.3,
        supports_docking=True, supports_conveyor=True,
    ),
    VehicleType.SORTER: VehicleCapability(
        vehicle_type=VehicleType.SORTER,
        max_load_kg=50.0, max_speed_ms=2.5,
        turning_radius_m=0.4, width_m=0.7, length_m=1.0,
        supports_docking=True, supports_conveyor=True,
    ),
    VehicleType.TOWING: VehicleCapability(
        vehicle_type=VehicleType.TOWING,
        max_load_kg=2000.0, max_speed_ms=1.2,
        turning_radius_m=1.0, width_m=1.0, length_m=1.5,
        supports_docking=False, supports_conveyor=False,
    ),
}


class VehicleTypeManager:
    """Manages vehicle types and task-to-vehicle matching."""

    def __init__(self):
        self._capabilities: Dict[VehicleType, VehicleCapability] = dict(STANDARD_CAPABILITIES)

    def register_type(self, capability: VehicleCapability):
        """Register or update a vehicle type."""
        self._capabilities[capability.vehicle_type] = capability
        logger.info("Registered vehicle type: %s", capability.vehicle_type)

    def get_capability(self, vehicle_type: VehicleType) -> Optional[VehicleCapability]:
        return self._capabilities.get(vehicle_type)

    def list_types(self) -> List[Dict[str, Any]]:
        """List all registered vehicle types."""
        return [
            {
                "type": cap.vehicle_type.value,
                "max_load_kg": cap.max_load_kg,
                "max_speed_ms": cap.max_speed_ms,
                "lifting_height_m": cap.lifting_height_m,
                "turning_radius_m": cap.turning_radius_m,
                "width_m": cap.width_m,
                "length_m": cap.length_m,
                "supports_docking": cap.supports_docking,
                "supports_conveyor": cap.supports_conveyor,
                "navigation_methods": [n.value for n in cap.navigation_methods],
            }
            for cap in self._capabilities.values()
        ]

    def find_compatible_types(self, task_requirements: Dict[str, Any]) -> List[VehicleType]:
        """Find all vehicle types that can handle the given task requirements."""
        compatible = []
        for vtype, cap in self._capabilities.items():
            if cap.can_handle_task(task_requirements):
                compatible.append(vtype)
        return compatible

    def score_match(
        self, vehicle_type: VehicleType, task_requirements: Dict[str, Any]
    ) -> float:
        """
        Score how well a vehicle type matches a task (0-1, higher is better).

        Considers: load efficiency, speed suitability, feature match.
        """
        cap = self._capabilities.get(vehicle_type)
        if not cap or not cap.can_handle_task(task_requirements):
            return 0.0

        score = 1.0

        # Load efficiency (prefer not over-specifying)
        cargo_weight = task_requirements.get("cargo_weight", 0)
        if cap.max_load_kg > 0:
            load_ratio = cargo_weight / cap.max_load_kg
            # Optimal at 60-80% capacity
            if 0.6 <= load_ratio <= 0.8:
                score *= 1.0
            elif load_ratio < 0.3:
                score *= 0.7  # Too much overcapacity
            else:
                score *= 0.9

        # Speed match (prefer faster for urgent tasks)
        if task_requirements.get("urgent"):
            score *= min(1.0, cap.max_speed_ms / 2.0)

        # Precision match
        if task_requirements.get("requires_docking") and not cap.supports_docking:
            score = 0.0

        return score

    def best_match(self, task_requirements: Dict[str, Any]) -> Optional[VehicleType]:
        """Find the best matching vehicle type for a task."""
        best_type = None
        best_score = 0.0
        for vtype in self._capabilities:
            score = self.score_match(vtype, task_requirements)
            if score > best_score:
                best_score = score
                best_type = vtype
        return best_type


# Singleton
vehicle_type_manager = VehicleTypeManager()
