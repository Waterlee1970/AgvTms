"""
Pydantic data models for AGV Logistics Scheduling System.

Defines all core entities:
- Map topology: nodes and edges
- AGV tasks, status, and assignments
- Conveyor line segments
- Schedule results and algorithm configurations
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class NodeType(str, Enum):
    """Types of map nodes in the factory layout."""
    PICKUP = "pickup"       # 取货点
    DROPOFF = "dropoff"     # 卸货点
    CHARGE = "charge"       # 充电站
    CROSS = "cross"         # 交叉路口
    PATH = "path"           # 普通路径点
    CONVEYOR_IN = "conveyor_in"    # 输送线入口
    CONVEYOR_OUT = "conveyor_out"  # 输送线出口


class EdgeDirection(str, Enum):
    BIDIRECTIONAL = "bidirectional"
    FORWARD = "forward"
    BACKWARD = "backward"


class AgvTaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgvRunStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    CHARGING = "charging"
    EXECUTING = "executing"
    WAITING = "waiting"
    ERROR = "error"


class HybridStrategy(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class NlpSolver(str, Enum):
    SLSQP = "SLSQP"
    IPOPT = "ipopt"
    COBYLA = "COBYLA"


# =============================================================================
# Map Topology Models
# =============================================================================

class MapNode(BaseModel):
    """A node in the factory floor map."""
    id: str = Field(..., description="Unique node identifier, e.g. 'N001'")
    name: str = Field(..., description="Human-readable name")
    x: float = Field(..., description="X coordinate (meters)")
    y: float = Field(..., description="Y coordinate (meters)")
    type: NodeType = Field(default=NodeType.PATH)
    capacity: int = Field(default=1, description="Max AGVs allowed at node simultaneously")
    conveyor_id: Optional[str] = Field(default=None, description="Associated conveyor line ID")


class MapEdge(BaseModel):
    """An edge connecting two nodes in the factory layout."""
    id: Optional[str] = Field(default=None)
    from_node: str = Field(..., description="Source node ID")
    to_node: str = Field(..., description="Target node ID")
    distance: float = Field(..., gt=0, description="Edge length in meters")
    direction: EdgeDirection = Field(default=EdgeDirection.BIDIRECTIONAL)
    is_conveyor: bool = Field(default=False, description="Whether this edge is a conveyor belt")
    speed_limit: float = Field(default=1.5, description="Max speed on this edge (m/s)")
    congestion_factor: float = Field(default=1.0, ge=0.5, le=5.0)


class MapGraph(BaseModel):
    """Complete factory map topology."""
    nodes: List[MapNode] = Field(default_factory=list)
    edges: List[MapEdge] = Field(default_factory=list)
    name: str = Field(default="Default Layout")
    version: int = Field(default=1)


# =============================================================================
# Task Models
# =============================================================================

class AgvTask(BaseModel):
    """A transport task assigned to or waiting for an AGV."""
    id: Optional[str] = Field(default=None)
    pickup_node: str = Field(..., description="Pickup location node ID")
    dropoff_node: str = Field(..., description="Dropoff location node ID")
    priority: int = Field(default=1, ge=1, le=10, description="1=lowest, 10=highest")
    status: AgvTaskStatus = Field(default=AgvTaskStatus.PENDING)
    assigned_agv: Optional[str] = Field(default=None)
    create_time: datetime = Field(default_factory=datetime.now)
    deadline: Optional[datetime] = Field(default=None)
    estimated_duration: float = Field(default=0.0, description="Estimated duration in seconds")
    actual_start: Optional[datetime] = Field(default=None)
    actual_end: Optional[datetime] = Field(default=None)
    cargo_type: str = Field(default="standard")


class ConveyorTaskType(str, Enum):
    """输送线任务类型"""
    AGV_ONLY = "agv_only"           # 纯AGV搬运任务
    CONVEYOR_ONLY = "conveyor_only" # 纯输送线转运任务
    MIXED = "mixed"                 # 混合长程任务（AGV+输送线协同）


class ConveyorTask(BaseModel):
    """A task on the conveyor line system - 支持三种类型."""
    id: Optional[str] = Field(default=None)
    task_type: ConveyorTaskType = Field(default=ConveyorTaskType.MIXED, description="任务类型: agv_only/conveyor_only/mixed")
    segment_id: str = Field(default="", description="Conveyor segment ID")
    from_segment_id: str = Field(default="", description="源线路段ID")
    to_segment_id: str = Field(default="", description="目标线路段ID")
    agv_pickup_node_id: str = Field(default="", description="AGV取货点节点ID（agv_only/mixed用）")
    agv_dropoff_node_id: str = Field(default="", description="AGV卸货点节点ID（agv_only/mixed用）")
    conveyor_entry_node_id: str = Field(default="", description="输送线入口节点ID")
    conveyor_exit_node_id: str = Field(default="", description="输送线出口节点ID")
    entry_time: Optional[float] = Field(default=None, description="Entry time offset in seconds")
    duration: float = Field(default=0, description="Processing duration on segment")
    predecessor_ids: List[str] = Field(default_factory=list, description="Tasks that must finish first")
    priority: int = Field(default=1)
    cargo_id: str = Field(default="")
    quantity: int = Field(default=1, description="物料数量")
    status: str = Field(default="pending", description="任务状态")
    item_type: str = Field(default="box", description="物品类型")
    phase: str = Field(default="agv_pickup", description="当前所处阶段")
    estimated_conveyor_time: float = Field(default=0, description="预计输送时间(秒)")
    removal_wait_time: float = Field(default=20, description="纯输送线任务的消除等待时间(秒)")


# =============================================================================
# AGV Models
# =============================================================================

class AgvStatus(BaseModel):
    """Real-time status of an AGV."""
    id: str = Field(..., description="AGV unique ID")
    name: str = Field(default="")
    x: float = Field(default=0.0)
    y: float = Field(default=0.0)
    battery: float = Field(default=100.0, ge=0, le=100)
    status: AgvRunStatus = Field(default=AgvRunStatus.IDLE)
    current_task: Optional[str] = Field(default=None)
    current_node: Optional[str] = Field(default=None)
    target_node: Optional[str] = Field(default=None)
    path: List[str] = Field(default_factory=list, description="Planned node path")
    speed: float = Field(default=0.0)
    capacity: int = Field(default=1)


# =============================================================================
# Conveyor Models
# =============================================================================

class ConveyorSegment(BaseModel):
    """A segment of a conveyor line."""
    id: str = Field(..., description="Segment unique ID")
    name: str = Field(default="")
    from_node: str
    to_node: str
    speed: float = Field(default=0.5, description="Conveyor speed in m/s")
    length: float = Field(..., gt=0, description="Segment length in meters")
    direction: EdgeDirection = Field(default=EdgeDirection.FORWARD)
    max_capacity: int = Field(default=5, description="Max items on segment simultaneously")
    energy_consumption: float = Field(default=0.1, description="Energy per second (kW)")


# =============================================================================
# Schedule Result Models
# =============================================================================

class AgvAssignment(BaseModel):
    """Assignment of a task to an AGV with path."""
    agv_id: str
    task_id: str
    path: List[str] = Field(default_factory=list, description="Ordered list of node IDs")
    path_cost: float = Field(default=0.0)
    start_time: float = Field(default=0.0)
    end_time: float = Field(default=0.0)
    wait_times: List[float] = Field(default_factory=list)


class ConveyorTimelineEntry(BaseModel):
    """A scheduled event on a conveyor segment."""
    task_id: str
    segment_id: str
    start_time: float
    end_time: float
    cargo_id: str = ""


class ScheduleMetrics(BaseModel):
    """Performance metrics for a schedule."""
    total_makespan: float = Field(default=0.0, description="Total time to complete all tasks")
    total_agv_travel_distance: float = Field(default=0.0)
    total_conveyor_energy: float = Field(default=0.0)
    agv_utilization: float = Field(default=0.0, description="Average AGV utilization (0-1)")
    task_completion_rate: float = Field(default=0.0)
    avg_task_wait_time: float = Field(default=0.0)
    collision_count: int = Field(default=0)
    conveyor_throughput: float = Field(default=0.0, description="Items per hour")


class ScheduleResult(BaseModel):
    """Complete scheduling result from the hybrid engine."""
    id: Optional[str] = Field(default=None)
    assignments: List[AgvAssignment] = Field(default_factory=list)
    conveyor_timeline: List[ConveyorTimelineEntry] = Field(default_factory=list)
    total_cost: float = Field(default=0.0)
    makespan: float = Field(default=0.0)
    metrics: ScheduleMetrics = Field(default_factory=ScheduleMetrics)
    agv_paths: Dict[str, List[str]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    algorithm_runtime_ms: float = Field(default=0.0)


# =============================================================================
# Algorithm Configuration Models
# =============================================================================

class AcoConfig(BaseModel):
    """Ant Colony Optimization parameters."""
    num_ants: int = Field(default=50, ge=5, le=500)
    alpha: float = Field(default=1.0, ge=0, le=5, description="Pheromone weight")
    beta: float = Field(default=2.0, ge=0, le=5, description="Heuristic weight")
    evaporation_rate: float = Field(default=0.1, ge=0.01, le=0.9)
    iterations: int = Field(default=100, ge=10, le=1000)
    q0: float = Field(default=0.5, ge=0, le=1, description="Exploitation vs exploration")


class SaConfig(BaseModel):
    """Simulated Annealing parameters."""
    initial_temp: float = Field(default=1000.0, ge=1, le=100000)
    cooling_rate: float = Field(default=0.95, ge=0.5, le=0.999)
    iterations: int = Field(default=500, ge=50, le=10000)
    min_temp: float = Field(default=0.01, ge=1e-6, le=100)


class NlpConfig(BaseModel):
    """Nonlinear Programming parameters."""
    solver: NlpSolver = Field(default=NlpSolver.SLSQP)
    tolerance: float = Field(default=1e-6, ge=1e-12, le=1e-2)
    max_iter: int = Field(default=1000, ge=50, le=10000)
    verbose: bool = Field(default=False)


class HybridConfig(BaseModel):
    """Hybrid engine configuration."""
    aco_weight: float = Field(default=0.4, ge=0, le=1)
    sa_weight: float = Field(default=0.3, ge=0, le=1)
    nlp_weight: float = Field(default=0.3, ge=0, le=1)
    strategy: HybridStrategy = Field(default=HybridStrategy.SEQUENTIAL)


class AlgorithmConfig(BaseModel):
    """Complete algorithm configuration."""
    aco: AcoConfig = Field(default_factory=AcoConfig)
    sa: SaConfig = Field(default_factory=SaConfig)
    nlp: NlpConfig = Field(default_factory=NlpConfig)
    hybrid: HybridConfig = Field(default_factory=HybridConfig)
