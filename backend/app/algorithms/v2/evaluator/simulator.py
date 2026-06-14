"""
调度仿真器 - 生成算法执行的可视化轨迹数据
==========================================

核心功能:
  1. 基于场景和算法结果，仿真完整的调度过程
  2. 生成每时间步的AGV位置、任务状态快照
  3. 支持故障注入仿真
  4. 输出前端可直接渲染的轨迹数据结构
"""

from __future__ import annotations

import math
import heapq
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple, Set
from enum import Enum
import random


class EventType(Enum):
    TASK_ASSIGNED = "task_assigned"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    AGV_MOVING = "agv_moving"
    AGV_IDLE = "agv_idle"
    AGV_CHARGING = "agv_charging"
    FAULT_OCCURRED = "fault_occurred"
    FAULT_RECOVERED = "fault_recovered"
    CONVEYOR_JAM = "conveyor_jam"
    CONVEYOR_RECOVERED = "conveyor_recovered"
    NODE_BLOCKED = "node_blocked"
    NODE_UNBLOCKED = "node_unblocked"
    # === 物料流转事件 ===
    MATERIAL_ON_CONVEYOR = "material_on_conveyor"      # 物料进入输送线（AGV放料完成）
    MATERIAL_PICKUP_FROM_CONVEYOR = "material_pickup"   # 物料被AGV从输送线接走
    CONVEYOR_TRANSIT_START = "conveyor_transit_start"  # 输送线开始转运
    CONVEYOR_TRANSIT_END = "conveyor_transit_end"      # 输送线转运完成


class MaterialPhaseEnum(str, Enum):
    """物料流转阶段"""
    AGV_PICKUP = "agv_pickup"           # 阶段1: AGV前往取货点取物料
    CONVEYOR_LOADING = "conveyor_loading" # 阶段2: AGV把物料放到输送线入口
    CONVEYOR_TRANSIT = "conveyor_transit" # 阶段3: 物料在输送线上自动转运
    AGV_RECEIVING = "agv_receiving"      # 阶段4: AGV前往出口接物料
    AGV_DROPOFF = "agv_dropoff"          # 阶段5: AGV将物料送到最终目的地
    COMPLETED = "completed"              # 完成
    # === 纯任务类型专用阶段 ===
    AGV_ONLY_DROPOFF = "agv_only_dropoff"   # 纯AGV任务：AGV卸货阶段


class AGVStateEnum(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    EXECUTING = "busy"
    FAULT = "fault"
    CHARGING = "charging"


@dataclass
class SimulationEvent:
    """单个仿真事件"""
    time_step: int
    event_type: str
    entity_id: str
    description: str
    position: Optional[Tuple[float, float]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "time_step": self.time_step,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "description": self.description,
            "position": list(self.position) if self.position else None,
            "extra": self.extra,
        }


@dataclass
class AgvSnapshot:
    """单AGV在某一时刻的快照"""
    agv_id: str
    node_id: str
    position: Tuple[float, float]
    state: str
    battery: float
    current_task_id: Optional[str]
    path: List[str]
    progress: float  # 0-1 在当前边上移动的进度

    def to_dict(self):
        return {
            "agv_id": self.agv_id,
            "node_id": self.node_id,
            "position": list(self.position),
            "state": self.state,
            "battery": round(self.battery, 2),
            "current_task_id": self.current_task_id,
            "path": self.path,
            "progress": round(self.progress, 3),
        }


@dataclass
class TaskSnapshot:
    """单个任务在某一时刻的快照"""
    task_id: str
    status: str  # pending, assigned, in_progress, completed
    pickup_node_id: str
    dropoff_node_id: str
    assigned_agv_id: Optional[str]
    priority: int
    progress: float

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "status": self.status,
            "pickup_node_id": self.pickup_node_id,
            "dropoff_node_id": self.dropoff_node_id,
            "assigned_agv_id": self.assigned_agv_id,
            "priority": self.priority,
            "progress": round(self.progress, 3),
        }


@dataclass
class ConveyorSnapshot:
    """输送线段在某一时刻的快照"""
    segment_id: str
    node_id: str
    status: str  # running, jammed, stopped
    speed_mps: float
    capacity: int
    current_load: int

    def to_dict(self):
        return {
            "segment_id": self.segment_id,
            "node_id": self.node_id,
            "status": self.status,
            "speed_mps": self.speed_mps,
            "capacity": self.capacity,
            "current_load": self.current_load,
        }


@dataclass
class MaterialOnConveyor:
    """物料在输送线上的状态（跟踪每件物料的转运进度）"""
    material_id: str
    task_id: str
    from_segment_id: str
    to_segment_id: str
    entry_node_id: str      # 进入的节点
    exit_node_id: str       # 将要离开的节点
    enter_step: int         # 进入时间步
    estimated_arrival_step: int  # 预计到达时间步
    progress: float = 0.0   # 转运进度 0-1
    status: str = "transiting"  # transiting | waiting_pickup | picked_up

    def to_dict(self):
        return {
            "material_id": self.material_id,
            "task_id": self.task_id,
            "from_segment": self.from_segment_id,
            "to_segment": self.to_segment_id,
            "entry_node": self.entry_node_id,
            "exit_node": self.exit_node_id,
            "enter_step": self.enter_step,
            "estimated_arrival": self.estimated_arrival_step,
            "progress": round(self.progress, 3),
            "status": self.status,
        }


@dataclass
class TimeStepSnapshot:
    """某一时刻的全局状态快照"""
    step: int
    simulation_time: float
    agvs: List[Dict[str, Any]]
    tasks: List[Dict[str, Any]]
    conveyors: List[Dict[str, Any]]
    active_faults: List[Dict[str, Any]]
    materials_on_conveyor: List[Dict[str, Any]]  # 输送线上的物料状态
    metrics: Dict[str, float]

    def to_dict(self):
        return {
            "step": self.step,
            "simulation_time": round(self.simulation_time, 2),
            "agvs": self.agvs,
            "tasks": self.tasks,
            "conveyors": self.conveyors,
            "active_faults": self.active_faults,
            "materials_on_conveyor": self.materials_on_conveyor,
            "metrics": {k: round(v, 2) for k, v in self.metrics.items()},
        }


@dataclass
class SimulationTrajectory:
    """完整调度轨迹"""
    scenario_name: str
    algorithm_name: str
    total_steps: int
    time_per_step: float
    snapshots: List[TimeStepSnapshot]
    events: List[SimulationEvent]
    summary: Dict[str, Any]

    def to_dict(self):
        return {
            "scenario_name": self.scenario_name,
            "algorithm_name": self.algorithm_name,
            "total_steps": self.total_steps,
            "time_per_step": self.time_per_step,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "events": [e.to_dict() for e in self.events],
            "summary": self.summary,
        }

    def to_json(self, indent=2):
        import json
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class SchedulingSimulator:
    """
    调度过程仿真器
    
    使用方式:
        sim = SchedulingSimulator(scenario, algorithm_result)
        traj = sim.simulate(time_steps=200)
        
        # 带故障注入
        sim.inject_fault({
            "enabled": True,
            "fault_type": "agv_breakdown",
            "faulty_agv_indices": [0, 2],
            "fault_time": 50,
            "fault_duration": 30,
        })
        traj = sim.simulate(time_steps=200)
    """

    def __init__(self, scenario, algorithm_result=None, seed=42):
        """
        初始化仿真器
        
        Args:
            scenario: AGVTMS_Scenario 对象或字典
            algorithm_result: AlgorithmResult 对象(包含分配结果)
            seed: 随机种子
        """
        from .scenarios import AGVTMS_Scenario

        if isinstance(scenario, dict):
            scenario = AGVTMS_Scenario.from_dict(scenario)

        self.scenario = scenario
        self.algorithm_result = algorithm_result
        self.rng = random.Random(seed)

        # 构建节点查找表
        self.node_map: Dict[str, Dict] = {n["id"]: n for n in scenario.nodes}
        self.edge_map: Dict[str, Dict] = {}
        # 增强的邻接表：包含边权重和输送线属性
        self.adjacency: Dict[str, List[Tuple[str, float, Dict]]] = {}

        for e in scenario.edges:
            eid = e.get("id", f"{e['from']}_{e['to']}")
            self.edge_map[eid] = e
            w = e.get("weight", 1.0)
            # 边属性：是否为输送线、速度、容量
            edge_attr = {
                "is_conveyor": e.get("is_conveyor", False),
                "conveyor_speed_mps": e.get("conveyor_speed_mps"),
                "capacity": e.get("capacity"),
            }
            self.adjacency.setdefault(e["from"], []).append((e["to"], w, edge_attr))
            self.adjacency.setdefault(e["to"], []).append((e["from"], w, edge_attr))

        # 故障配置
        self.fault_config: Dict[str, Any] = {"enabled": False}
        self.active_faults: List[Dict] = []

        # 内部状态
        self._assignment_map: Dict[str, str] = {}  # task_id -> agv_id
        self._agv_paths: Dict[str, List[str]] = {}  # agv_id -> path nodes
        self._task_pickup_done: Set[str] = set()
        self._task_complete_set: Set[str] = set()
        
        # === 物料流转状态跟踪（AGV-输送线协同）===
        self._materials_on_conveyor: Dict[str, MaterialOnConveyor] = {}  # material_id -> state
        self._task_phase: Dict[str, str] = {}  # task_id -> current phase (MaterialPhaseEnum)
        self._material_counter: int = 0  # 物料ID计数器
        self._conveyor_task_waiting_pickup: Dict[str, List[str]] = {}  # exit_node_id -> [material_ids]
        
        # === 共享出入口资源管理（2个入口、2个出口）===
        self._global_entry_nodes: List[str] = []   # 全局入口节点列表
        self._global_exit_nodes: List[str] = []     # 全局出口节点列表
        self._entry_queue: Dict[str, List[str]] = {}  # entry_node_id -> [task_ids等待放料]
        self._exit_queue: Dict[str, List[str]] = {}   # exit_node_id -> [task_ids等待接货]
        self._entry_in_use: Dict[str, str] = {}       # entry_node_id -> task_id(正在使用)
        self._exit_in_use: Dict[str, str] = {}         # exit_node_id -> task_id(正在使用)
        
        # 从场景中加载全局出入口配置
        if hasattr(scenario, 'global_conveyor_entries'):
            self._global_entry_nodes = scenario.global_conveyor_entries or []
        if hasattr(scenario, 'global_conveyor_exits'):
            self._global_exit_nodes = scenario.global_conveyor_exits or []
        
        # 如果场景没有配置，从conveyor_tasks中自动提取
        if not self._global_entry_nodes and scenario.conveyor_tasks:
            entries = list(dict.fromkeys(
                t.get("conveyor_entry_node_id", "") for t in scenario.conveyor_tasks 
                if t.get("conveyor_entry_node_id")
            ))
            self._global_entry_nodes = entries[:2]
        
        if not self._global_exit_nodes and scenario.conveyor_tasks:
            exits = list(dict.fromkeys(
                t.get("conveyor_exit_node_id", "") for t in scenario.conveyor_tasks 
                if t.get("conveyor_exit_node_id")
            ))
            self._global_exit_nodes = exits[:2]
        
        # 初始化出入口队列
        for nid in self._global_entry_nodes:
            self._entry_queue[nid] = []
            self._entry_in_use[nid] = ""
        for nid in self._global_exit_nodes:
            self._exit_queue[nid] = []
            self._exit_in_use[nid] = ""

        # 初始化输送线任务的阶段状态（根据task_type设置不同初始阶段）
        for task in (self.scenario.conveyor_tasks or []):
            tid = task.get("id", "")
            if tid:
                ttype = task.get("task_type", "mixed")
                initial_phase = task.get("phase", MaterialPhaseEnum.AGV_PICKUP.value)
                
                if ttype == "agv_only":
                    # 纯AGV任务：从agv_pickup开始，最终到agv_only_dropoff
                    self._task_phase[tid] = MaterialPhaseEnum.AGV_PICKUP.value
                elif ttype == "conveyor_only":
                    # 纯输送线任务：直接从loading阶段开始（物料已在入口）
                    self._task_phase[tid] = MaterialPhaseEnum.CONVEYOR_LOADING.value
                else:
                    # 混合任务：使用默认的5阶段流程
                    self._task_phase[tid] = initial_phase or MaterialPhaseEnum.AGV_PICKUP.value

        # 从算法结果初始化分配
        self._init_from_algorithm_result()

    def _init_from_algorithm_result(self):
        """从算法结果中提取任务-AGV分配关系"""
        if not self.algorithm_result:
            # 默认贪心分配：按顺序分配可用AGV给待处理任务
            agv_ids = [a["id"] for a in self.scenario.agvs if a.get("status") != "fault"]
            
            # 先分配普通tasks
            for i, task in enumerate(self.scenario.tasks):
                if task.get("status") != "completed" and agv_ids:
                    self._assignment_map[task["id"]] = agv_ids[i % len(agv_ids)]
            
            # 再分配conveyor_tasks（只分配需要AGV的任务：agv_only 和 mixed）
            conv_task_offset = len(self.scenario.tasks)
            agv_only_or_mixed = [
                t for t in (self.scenario.conveyor_tasks or [])
                if t.get("status") != "completed" 
                and t.get("task_type", "mixed") in ("agv_only", "mixed")
            ]
            
            for i, ctask in enumerate(agv_only_or_mixed):
                if agv_ids:
                    tid = ctask["id"]
                    self._assignment_map[tid] = agv_ids[(conv_task_offset + i) % len(agv_ids)]
            
            return

        assignments = getattr(self.algorithm_result, 'assignments', None) or []
        if isinstance(assignments, dict):
            for task_id, info in assignments.items():
                if isinstance(info, dict):
                    agv_id = info.get("agv_id") or info.get("agv")
                else:
                    agv_id = info
                if agv_id:
                    self._assignment_map[task_id] = agv_id
        elif isinstance(assignments, list):
            for item in assignments:
                if isinstance(item, dict):
                    tid = item.get("task_id")
                    aid = item.get("agv_id") or item.get("agv")
                    if tid and aid:
                        self._assignment_map[tid] = aid

        # 如果算法没有提供分配，使用num_assignments来估算
        num_assigned = getattr(self.algorithm_result, 'num_assignments', 0)
        if num_assigned > 0 and not self._assignment_map:
            agv_ids = [a["id"] for a in self.scenario.agvs]
            tasks = [t for t in self.scenario.tasks if t.get("status") != "completed"]
            for i in range(min(num_assigned, len(tasks))):
                self._assignment_map[tasks[i]["id"]] = agv_ids[i % len(agv_ids)]

    def inject_fault(self, fault_config: Dict[str, Any]):
        """
        注入故障配置
        
        Args:
            fault_config: 故障配置字典
                - enabled: bool 是否启用
                - fault_type: 故障类型
                - faulty_agv_indices: 故障AGV索引列表
                - faulty_segment_indices: 故障输送段索引列表
                - blocked_node_ids: 阻塞节点ID列表
                - fault_time: 故障发生时刻
                - fault_duration: 故障持续时间
        """
        self.fault_config = fault_config or {"enabled": False}

    def simulate(
        self,
        time_steps: int = 150,
        time_per_step: float = 1.0,
        agv_speed: float = 2.0,
    ) -> SimulationTrajectory:
        """
        执行仿真
        
        Args:
            time_steps: 总仿真步数
            time_per_step: 每步代表的真实时间(秒)
            agv_speed: AGV移动速度 (单位/秒)
            
        Returns:
            SimulationTrajectory 完整轨迹数据
        """
        events: List[SimulationEvent] = []
        snapshots: List[TimeStepSnapshot] = []

        # 初始化AGV状态
        agv_states: Dict[str, Dict] = {}
        for agv in self.scenario.agvs:
            nid = agv.get("current_node_id", agv.get("current_node"))
            pos = self._get_node_pos(nid)
            agv_states[agv["id"]] = {
                "agv_id": agv["id"],
                "node_id": nid,
                "position": pos,
                "state": "fault" if agv.get("status") == "fault" else "idle",
                "battery": agv.get("battery_level", agv.get("battery", 80)),
                "current_task_id": None,
                "path": [],
                "progress": 0.0,
                "start_step": 0,
                "fault_end_step": -1,
            }

        # 初始化任务状态
        task_states: Dict[str, Dict] = {}
        for task in self.scenario.tasks:
            pickup_nid = task.get("pickup_node_id", task.get("pickup_node", ""))
            dropoff_nid = task.get("dropoff_node_id", task.get("dropoff_node", ""))
            task_states[task["id"]] = {
                "task_id": task["id"],
                "status": "pending",
                "pickup_node_id": pickup_nid,
                "dropoff_node_id": dropoff_nid,
                "assigned_agv_id": self._assignment_map.get(task["id"]),
                "priority": task.get("priority", 5),
                "progress": 0.0,
                "start_step": -1,
                "complete_step": -1,
            }

        # 初始化输送线状态
        conv_states: Dict[str, Dict] = {}
        for seg in (self.scenario.conveyor_segments or []):
            conv_states[seg["id"]] = {
                "segment_id": seg["id"],
                "node_id": seg.get("node_id", ""),
                "status": seg.get("status", "running"),
                "speed_mps": seg.get("speed_mps", 0.5),
                "capacity": seg.get("capacity", 10),
                "current_load": self.rng.randint(1, seg.get("capacity", 10) // 2),
            }

        # 统计计数器
        completed_count = 0
        # 总任务数 = 普通任务 + 输送线任务（包括 agv_only / conveyor_only / mixed）
        total_tasks = len(self.scenario.tasks) + len(self.scenario.conveyor_tasks or [])
        total_distance = 0.0

        # 为每个AGV规划路径（基于分配的任务）
        self._plan_all_paths(agv_states, task_states)

        # 同时为输送线协同任务预分配AGV（如果还有空闲AGV的话）
        self._preassign_conveyor_tasks(agv_states, task_states)

        # 主仿真循环
        for step in range(time_steps):
            sim_time = step * time_per_step
            current_events = []

            # === 处理故障注入 ===
            fault_config = self.fault_config
            if fault_config.get("enabled"):
                fault_time = fault_config.get("fault_time", time_steps // 3)
                fault_duration = fault_config.get("fault_duration", time_steps // 5)
                fault_type = fault_config.get("fault_type", "agv_breakdown")

                if step == fault_time:
                    # 故障发生
                    if fault_type == "agv_breakdown":
                        for idx in fault_config.get("faulty_agv_indices", []):
                            if idx < len(self.scenario.agvs):
                                agv = self.scenario.agvs[idx]
                                aid = agv["id"]
                                if aid in agv_states and agv_states[aid]["state"] != "fault":
                                    old_state = agv_states[aid]["state"]
                                    agv_states[aid]["state"] = "fault"
                                    agv_states[aid]["fault_end_step"] = step + int(fault_duration)
                                    pos = agv_states[aid]["position"]
                                    ev = SimulationEvent(
                                        time_step=step,
                                        event_type="fault_occurred",
                                        entity_id=aid,
                                        description=f"AGV-{aid} 发生故障！",
                                        position=pos,
                                        extra={"fault_type": "breakdown", "previous_state": old_state},
                                    )
                                    events.append(ev)
                                    current_events.append(ev.to_dict())

                    elif fault_type == "conveyor_jam":
                        for idx in fault_config.get("faulty_segment_indices", []):
                            segs = self.scenario.conveyor_segments or []
                            if idx < len(segs):
                                sid = segs[idx]["id"]
                                if sid in conv_states:
                                    conv_states[sid]["status"] = "jammed"
                                    nid = conv_states[sid]["node_id"]
                                    pos = self._get_node_pos(nid)
                                    ev = SimulationEvent(
                                        time_step=step,
                                        event_type="conveyor_jam",
                                        entity_id=sid,
                                        description=f"输送线路段 {sid} 发生堵塞！",
                                        position=pos,
                                    )
                                    events.append(ev)
                                    current_events.append(ev.to_dict())

                    elif fault_type == "node_blocked":
                        for nid in fault_config.get("blocked_node_ids", []):
                            pos = self._get_node_pos(nid)
                            ev = SimulationEvent(
                                time_step=step,
                                event_type="node_blocked",
                                entity_id=nid,
                                description=f"节点 {nid} 被阻塞！",
                                position=pos,
                            )
                            events.append(ev)
                            current_events.append(ev.to_dict())

                # 检查故障恢复
                for aid, state in agv_states.items():
                    if state["state"] == "fault" and state["fault_end_step"] > 0:
                        if step >= state["fault_end_step"]:
                            state["state"] = "idle"
                            state["current_task_id"] = None
                            state["path"] = []
                            state["progress"] = 0
                            ev = SimulationEvent(
                                time_step=step,
                                event_type="fault_recovered",
                                entity_id=aid,
                                description=f"AGV-{aid} 故障恢复，重新可用",
                                position=state["position"],
                            )
                            events.append(ev)
                            current_events.append(ev.to_dict())
                            
                            # 重新规划路径
                            self._replan_for_agv(aid, agv_states, task_states)

                # 输送线恢复
                if fault_type == "conveyor_jam":
                    recovery_time = fault_time + int(fault_duration * 0.7)
                    if step == recovery_time:
                        for idx in fault_config.get("faulty_segment_indices", []):
                            segs = self.scenario.conveyor_segments or []
                            if idx < len(segs):
                                sid = segs[idx]["id"]
                                if sid in conv_states:
                                    conv_states[sid]["status"] = "running"
                                    ev = SimulationEvent(
                                        time_step=step,
                                        event_type="conveyor_recovered",
                                        entity_id=sid,
                                        description=f"输送线路段 {sid} 恢复运行",
                                    )
                                    events.append(ev)
                                    current_events.append(ev.to_dict())

            # === 更新AGV位置 ===
            for aid, state in agv_states.items():
                if state["state"] == "fault":
                    continue

                if state["state"] == "moving" and state["path"]:
                    # === 特殊处理：路径已走完（path只剩1个节点=当前位置）===
                    if len(state["path"]) <= 1:
                        # 立即处理到达逻辑（不再等待progress）
                        tid_raw = state.get("current_task_id")
                        
                        # 输送线协同任务
                        if tid_raw and str(tid_raw).startswith("conv_"):
                            # 处理 "conv_TASKID", "conv_receive_TASKID", "conv_agvonly_TASKID" 格式
                            tid_str = str(tid_raw)
                            if tid_str.startswith("conv_receive_"):
                                conv_tid = tid_str[len("conv_receive_"):]
                                conv_task_type = "mixed"
                            elif tid_str.startswith("conv_agvonly_"):
                                conv_tid = tid_str[len("conv_agvonly_"):]
                                conv_task_type = "agv_only"
                            elif tid_str.startswith("conv_"):
                                conv_tid = tid_str[5:]
                                conv_task_type = "mixed"  # 默认混合任务
                            else:
                                conv_tid = tid_str
                                conv_task_type = state.get("_conveyor_task_type", "mixed")
                            
                            ctask = next((t for t in (self.scenario.conveyor_tasks or []) 
                                         if t.get("id") == conv_tid), None)
                            if ctask:
                                ttype = ctask.get("task_type", conv_task_type)
                                phase = self._task_phase.get(conv_tid, "")
                                current_node = state["node_id"]
                                
                                if ttype == "agv_only":
                                    # === 纯AGV任务 ===
                                    if phase == MaterialPhaseEnum.AGV_PICKUP.value:
                                        # 阶段1完成：取完货，去卸货点
                                        dropoff_nid = ctask.get("agv_dropoff_node_id", "")
                                        if dropoff_nid:
                                            path_to_dropoff = self._calculate_path(current_node, dropoff_nid)
                                            state["path"] = path_to_dropoff
                                            self._task_phase[conv_tid] = MaterialPhaseEnum.AGV_ONLY_DROPOFF.value
                                            
                                    elif phase == MaterialPhaseEnum.AGV_ONLY_DROPOFF.value:
                                        # 阶段2完成：到达卸货点，完成任务
                                        self._complete_agv_only_task(ctask, conv_tid, aid, agv_states, task_states, step, events)
                                        completed_count += 1
                                        
                                elif ttype == "conveyor_only":
                                    # 纯输送线任务不应该有AGV参与（这里只是安全检查）
                                    state["state"] = "idle"
                                    
                                else:  # mixed 混合任务
                                    # 原有的5阶段逻辑
                                    if phase == MaterialPhaseEnum.CONVEYOR_LOADING.value:
                                        self._check_conveyor_loading(ctask, conv_tid, agv_states, events, step)
                                    elif phase == MaterialPhaseEnum.AGV_RECEIVING.value:
                                        dropoff_nid = ctask.get("agv_dropoff_node_id", "")
                                        if dropoff_nid:
                                            new_path = self._calculate_path(current_node, dropoff_nid)
                                            state["path"] = new_path
                                            state["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_DROPOFF.value
                                            self._task_phase[conv_tid] = MaterialPhaseEnum.AGV_DROPOFF.value
                                            for mid, m in list(self._materials_on_conveyor.items()):
                                                if m.task_id == conv_tid and m.status == "waiting_pickup":
                                                    m.status = "picked_up"
                                                    ev = SimulationEvent(
                                                        time_step=step,
                                                        event_type="material_pickup_from_conveyor",
                                                        entity_id=conv_tid,
                                                        description=f"[流转阶段4→5] AGV-{aid} 接走物料({mid})",
                                                        position=state["position"],
                                                        extra={"material_id": mid},
                                                    )
                                                    events.append(ev)
                                                    break
                                    elif phase == MaterialPhaseEnum.AGV_DROPOFF.value:
                                        self._check_final_dropoff(ctask, conv_tid, agv_states, task_states, step, events)
                                        completed_count += 1
                                    else:
                                        state["state"] = "idle"
                        else:
                            state["state"] = "idle"
                    
                    elif len(state["path"]) >= 2:
                        current_node = state["path"][0]
                        next_node = state["path"][1]
                        edge_key = (current_node, next_node)
                    else:
                        current_node = state.get("node_id", "")
                        next_node = ""
                        edge_key = None
                    
                    # 查找当前边的属性（验证：AGV不应该在输送线边上）
                    edge_attr = self._get_edge_attr(current_node, next_node)
                    
                    if edge_attr.get("is_conveyor"):
                        # 安全检查：如果路径规划错误导致AGV上了输送线
                        # 立即停止并标记错误状态
                        state["state"] = "idle"
                        state["path"] = []  # 清空路径防止继续移动
                        ev = SimulationEvent(
                            time_step=step,
                            event_type="fault_occurred",
                            entity_id=aid,
                            description=f"⚠️ 路径规划错误：AGV-{aid} 不应在输送线边 ({current_node}->{next_node})",
                            position=state["position"],
                            extra={"error": "agv_on_conveyor", "edge": f"{current_node}_{next_node}"},
                        )
                        events.append(ev)
                        continue  # 跳过本步移动更新
                    
                    # 普通边：标准AGV移动（AGV只在普通道路上行驶）
                    speed_factor = agv_speed * time_per_step * 0.05
                    dist_per_step = 1.0

                    state["progress"] += speed_factor / max(dist_per_step, 1.0)
                    
                    if state["progress"] >= 1.0:
                        # 到达下一个节点
                        state["progress"] = 0.0
                        if len(state["path"]) > 1:
                            next_node = state["path"][1]
                            state["path"] = state["path"][1:]
                            state["node_id"] = next_node
                            state["position"] = self._get_node_pos(next_node)
                            # 根据边类型累加实际距离
                            total_distance += dist_per_step
                            
                            # 检查是否到达目标节点
                            tid_raw = state.get("current_task_id")
                            
                            # === 输送线协同任务处理（conv_TASKID / conv_agvonly_TASKID 格式）===
                            conv_tid = None
                            if tid_raw and str(tid_raw).startswith("conv_"):
                                tid_str = str(tid_raw)
                                if tid_str.startswith("conv_receive_"):
                                    conv_tid = tid_str[len("conv_receive_"):]
                                    _ct = "mixed"
                                elif tid_str.startswith("conv_agvonly_"):
                                    conv_tid = tid_str[len("conv_agvonly_"):]
                                    _ct = "agv_only"
                                elif tid_str.startswith("conv_"):
                                    conv_tid = tid_str[5:]
                                    _ct = "mixed"
                                
                                ctask = next((t for t in (self.scenario.conveyor_tasks or []) if t.get("id") == conv_tid), None)
                                
                                if ctask:
                                    ttype = ctask.get("task_type", _ct)
                                    phase = self._task_phase.get(conv_tid, "")
                                    current_node_id = state["node_id"]
                                    
                                    if ttype == "agv_only":
                                        # 纯AGV任务：取货→卸货
                                        if phase == MaterialPhaseEnum.AGV_PICKUP.value:
                                            dropoff_nid = ctask.get("agv_dropoff_node_id", "")
                                            if dropoff_nid:
                                                state["path"] = self._calculate_path(current_node_id, dropoff_nid)
                                                self._task_phase[conv_tid] = MaterialPhaseEnum.AGV_ONLY_DROPOFF.value
                                                events.append(SimulationEvent(step, "task_started", conv_tid,
                                                    f"[纯AGV] AGV-{aid} 取完物料，前往 {dropoff_nid} 卸货", state["position"]))
                                        elif phase == MaterialPhaseEnum.AGV_ONLY_DROPOFF.value:
                                            self._complete_agv_only_task(ctask, conv_tid, aid, agv_states, task_states, step, events)
                                            completed_count += 1
                                            
                                    elif ttype != "conveyor_only":  # mixed任务继续原有逻辑
                                        # 阶段1完成：AGV到达取货点，接下来去放料点
                                        entry_nid = ctask.get("conveyor_entry_node_id", "")
                                        if entry_nid:
                                            path_to_entry = self._calculate_path(current_node_id, entry_nid)
                                            state["path"] = path_to_entry
                                            state["_conveyor_task_phase"] = MaterialPhaseEnum.CONVEYOR_LOADING.value
                                            self._task_phase[conv_tid] = MaterialPhaseEnum.CONVEYOR_LOADING.value
                                            ev = SimulationEvent(
                                                time_step=step,
                                                event_type="task_started",
                                                entity_id=conv_tid,
                                                description=f"[流转阶段1→2] AGV-{aid} 取完物料，前往输送线入口 {entry_nid} 放料",
                                                position=state["position"],
                                            )
                                            events.append(ev)
                                            
                                    elif phase == MaterialPhaseEnum.CONVEYOR_LOADING.value:
                                        # 阶段2完成：检查是否到达输送线入口
                                        target_entry = ctask.get("conveyor_entry_node_id", "")
                                        if current_node_id == target_entry:
                                            # 精确匹配：到达入口，放料
                                            self._check_conveyor_loading(ctask, conv_tid, agv_states, events, step)
                                        elif len(state["path"]) <= 1:
                                            # 路径走完了但可能没精确到入口（路径规划偏差）
                                            # 直接触发放料逻辑
                                            self._check_conveyor_loading(ctask, conv_tid, agv_states, events, step)
                                        
                                    elif phase == MaterialPhaseEnum.AGV_RECEIVING.value:
                                        # 阶段4完成：AGV到达出口接料
                                        target_exit = ctask.get("conveyor_exit_node_id", "")
                                        if current_node_id == target_exit or len(state["path"]) <= 1:
                                            # 检查出口是否被占用（同一时刻只允许一个AGV接货）
                                            current_exit_user = self._exit_in_use.get(target_exit, "")
                                            
                                            if current_exit_user and current_exit_user != conv_tid:
                                                # 出口被占用，进入等待队列
                                                if conv_tid not in self._exit_queue.get(target_exit, []):
                                                    self._exit_queue.setdefault(target_exit, []).append(conv_tid)
                                                    
                                                ev = SimulationEvent(
                                                    time_step=step,
                                                    event_type="node_blocked",
                                                    entity_id=conv_tid,
                                                    description=f"[出口排队] 任务{conv_tid}的AGV-{aid}到达出口{target_exit}，等待中...",
                                                    position=self._get_node_pos(target_exit),
                                                )
                                                events.append(ev)
                                                
                                                # AGV原地等待
                                                state["state"] = "executing"
                                                state["progress"] = 0.0
                                                state["_waiting_for_exit"] = target_exit
                                            else:
                                                # 可以接货！立即完成接货并释放出口
                                                self._exit_in_use[target_exit] = conv_tid  # 短暂标记
                                                
                                                dropoff_nid = ctask.get("agv_dropoff_node_id", "")
                                                if dropoff_nid:
                                                    path_to_dropoff = self._calculate_path(current_node_id, dropoff_nid)
                                                    state["path"] = path_to_dropoff
                                                    state["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_DROPOFF.value
                                                    self._task_phase[conv_tid] = MaterialPhaseEnum.AGV_DROPOFF.value
                                                    
                                                    # 接走物料
                                                    for mid, m in list(self._materials_on_conveyor.items()):
                                                        if m.task_id == conv_tid and m.status == "waiting_pickup":
                                                            m.status = "picked_up"
                                                            ev = SimulationEvent(
                                                                time_step=step,
                                                                event_type="material_pickup_from_conveyor",
                                                                entity_id=conv_tid,
                                                                description=f"[流转阶段4→5] AGV-{aid} 在出口{target_exit}接走物料({mid})",
                                                                position=state["position"],
                                                                extra={"material_id": mid},
                                                            )
                                                            events.append(ev)
                                                            break
                                                    
                                                    # 立即释放出口（接货已完成）
                                                    self._exit_in_use[target_exit] = ""
                                                    
                                                    # 处理等待队列中的下一个任务
                                                    self._process_exit_queue(target_exit, agv_states, events, step)
                                                    
                                    elif phase == MaterialPhaseEnum.AGV_DROPOFF.value:
                                        # 阶段5完成：到达最终目的地
                                        target_dropoff = ctask.get("agv_dropoff_node_id", "")
                                        if current_node_id == target_dropoff or len(state["path"]) <= 1:
                                            self._check_final_dropoff(ctask, conv_tid, agv_states, task_states, step, events)
                                            completed_count += 1
                                        
                            elif tid_raw and tid_raw in task_states:
                                    tstate["status"] = "completed"
                                    tstate["complete_step"] = step
                                    tstate["progress"] = 1.0
                                    state["current_task_id"] = None
                                    state["state"] = "idle"
                                    completed_count += 1
                                    ev = SimulationEvent(
                                        time_step=step,
                                        event_type="task_completed",
                                        entity_id=tid,
                                        description=f"任务 {tid} 完成！",
                                        position=state["position"],
                                    )
                                    events.append(ev)
                                    current_events.append(ev.to_dict())
                                    
                                    # 分配下一个任务
                                    self._assign_next_task(aid, agv_states, task_states, step, events)
                        else:
                            # 路径走完但还没到达目标——检查是否是输送线协同任务
                            tid_raw = state.get("current_task_id")
                            if tid_raw and str(tid_raw).startswith("conv_"):
                                tid_str = str(tid_raw)
                                if tid_str.startswith("conv_receive_"):
                                    conv_tid = tid_str[len("conv_receive_"):]
                                elif tid_str.startswith("conv_"):
                                    conv_tid = tid_str[5:]
                                ctask = next((t for t in (self.scenario.conveyor_tasks or []) 
                                             if t.get("id") == conv_tid), None)
                                if ctask:
                                    phase = self._task_phase.get(conv_tid, "")
                                    current_node = state["node_id"]
                                    
                                    if phase == MaterialPhaseEnum.CONVEYOR_LOADING.value:
                                        # 到达入口：放料
                                        self._check_conveyor_loading(ctask, conv_tid, agv_states, events, step)
                                    elif phase == MaterialPhaseEnum.AGV_RECEIVING.value:
                                        # 到达出口：接货
                                        target_exit = ctask.get("conveyor_exit_node_id", "")
                                        dropoff_nid = ctask.get("agv_dropoff_node_id", "")
                                        if dropoff_nid:
                                            path_to_dropoff = self._calculate_path(current_node, dropoff_nid)
                                            state["path"] = path_to_dropoff
                                            state["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_DROPOFF.value
                                            self._task_phase[conv_tid] = MaterialPhaseEnum.AGV_DROPOFF.value
                                            for mid, m in list(self._materials_on_conveyor.items()):
                                                if m.task_id == conv_tid and m.status == "waiting_pickup":
                                                    m.status = "picked_up"
                                                    ev = SimulationEvent(
                                                        time_step=step,
                                                        event_type="material_pickup_from_conveyor",
                                                        entity_id=conv_tid,
                                                        description=f"[流转阶段4→5] AGV-{aid} 接走物料({mid})",
                                                        position=state["position"],
                                                        extra={"material_id": mid},
                                                    )
                                                    events.append(ev)
                                                    break
                                    elif phase == MaterialPhaseEnum.AGV_DROPOFF.value:
                                        # 到达最终目的地
                                        self._check_final_dropoff(ctask, conv_tid, agv_states, task_states, step, events)
                                        completed_count += 1
                                    else:
                                        state["state"] = "idle"
                            else:
                                state["state"] = "idle"

                elif state["state"] == "executing":
                    # 检查是否在等待出入口（不执行普通装卸货逻辑）
                    waiting_for = state.get("_waiting_for_entry") or state.get("_waiting_for_exit")
                    if waiting_for:
                        # 在等待出入口，保持等待状态
                        continue
                    
                    # 普通装卸货耗时模拟
                    state["progress"] += 0.1
                    if state["progress"] >= 1.0:
                        state["progress"] = 0.3
                        state["state"] = "moving"
                        tid = state.get("current_task_id")
                        if tid and tid in task_states and task_states[tid]["status"] == "in_progress":
                            # 设置去往卸货点的路径
                            dropoff = task_states[tid]["dropoff_node_id"]
                            new_path = self._calculate_path(state["node_id"], dropoff)
                            if len(new_path) > 1:
                                state["path"] = new_path
                                state["progress"] = 0

                elif state["state"] == "idle":
                    # 尝试获取新任务（优先检查输送线协同任务）
                    if not state.get("current_task_id"):
                        # 先尝试分配输送线任务
                        conv_assigned = self._try_assign_conveyor_task(aid, agv_states, task_states, step, events)
                        if not conv_assigned:
                            # 没有输送线任务，尝试普通任务
                            assigned = self._assign_next_task(aid, agv_states, task_states, step, events)
                            if not assigned and step % 30 == 0:
                                # 偶尔记录空闲事件
                                pass

                # 电池消耗
                if state["state"] in ("moving", "executing"):
                    state["battery"] = max(0, state["battery"] - 0.02)
                    
                # 低电量自动充电
                if state["battery"] < 20 and state["state"] == "idle":
                    state["state"] = "charging"
                    state["battery"] = min(100, state["battery"] + 0.5)

                if state["state"] == "charging":
                    state["battery"] = min(100, state["battery"] + 1.0)
                    if state["battery"] >= 95:
                        state["state"] = "idle"

            # === 更新任务进度显示 ===
            for tid, tstate in task_states.items():
                if tstate["status"] == "in_progress":
                    aid = tstate.get("assigned_agv_id")
                    if aid and aid in agv_states:
                        agv_state = agv_states[aid]
                        # 基于AGV位置计算任务进度
                        pickup_pos = self._get_node_pos(tstate["pickup_node_id"])
                        dropoff_pos = self._get_node_pos(tstate["dropoff_node_id"])
                        curr_pos = agv_state["position"]
                        
                        total_dist = self._euclidean(pickup_pos, dropoff_pos)
                        if total_dist > 0:
                            covered = self._euclidean(pickup_pos, curr_pos)
                            tstate["progress"] = max(0, min(1, covered / total_dist))

            # === 更新输送线负载 ===
            for sid, cstate in conv_states.items():
                if cstate["status"] == "running":
                    # 随机波动
                    cstate["current_load"] = max(
                        0,
                        min(cstate["capacity"],
                            cstate["current_load"] + self.rng.choice([-1, 0, 0, 0, 1]))
                    )

            # === 物料在输送线上的流转（AGV-输送线协同核心）===
            self._simulate_conveyor_transit(step, conv_states, task_states, agv_states, events)

            # === 收集活跃故障 ===
            active_fault_list = []
            if self.fault_config.get("enabled"):
                fault_time = self.fault_config.get("fault_time", 0)
                fault_duration = self.fault_config.get("fault_duration", 0)
                if fault_time <= step < fault_time + fault_duration:
                    for idx in self.fault_config.get("faulty_agv_indices", []):
                        if idx < len(self.scenario.agvs):
                            active_fault_list.append({
                                "type": "agv_breakdown",
                                "entity_id": self.scenario.agvs[idx]["id"],
                                "since_step": fault_time,
                                "estimated_recovery": fault_time + fault_duration,
                            })

            # === 计算本步指标 ===
            moving_count = sum(1 for s in agv_states.values() if s["state"] == "moving")
            idle_count = sum(1 for s in agv_states.values() if s["state"] == "idle")
            fault_count = sum(1 for s in agv_states.values() if s["state"] == "fault")
            
            step_metrics = {
                "completion_rate": (completed_count / total_tasks * 100) if total_tasks > 0 else 0,
                "active_agvs": moving_count,
                "idle_agvs": idle_count,
                "fault_agvs": fault_count,
                "total_distance": total_distance,
                "avg_battery": sum(s["battery"] for s in agv_states.values()) / len(agv_states) if agv_states else 0,
            }

            # 生成快照 (只传递dataclass需要的字段)
            _agv_fields = {'agv_id', 'node_id', 'position', 'state', 'battery', 'current_task_id', 'path', 'progress'}
            _task_fields = {'task_id', 'status', 'pickup_node_id', 'dropoff_node_id', 'assigned_agv_id', 'priority', 'progress'}
            _conv_fields = {'segment_id', 'node_id', 'status', 'speed_mps', 'capacity', 'current_load'}

            # 收集当前输送线上的物料状态
            materials_data = [m.to_dict() for m in self._materials_on_conveyor.values()]

            snapshot = TimeStepSnapshot(
                step=step,
                simulation_time=sim_time,
                agvs=[AgvSnapshot(**{k: s[k] for k in _agv_fields if k in s}).to_dict() for s in agv_states.values()],
                tasks=[TaskSnapshot(**{k: t[k] for k in _task_fields if k in t}).to_dict() for t in task_states.values()],
                conveyors=[ConveyorSnapshot(**{k: c[k] for k in _conv_fields if k in c}).to_dict() for c in conv_states.values()],
                active_faults=active_fault_list,
                materials_on_conveyor=materials_data,
                metrics=step_metrics,
            )
            snapshots.append(snapshot)

        # 生成汇总
        algo_name = "unknown"
        if self.algorithm_result:
            algo_name = getattr(self.algorithm_result, 'algorithm_name', 'unknown')

        # 统计物料流转数据
        conv_task_phases = self._task_phase
        conveyor_stats = {
            "total_conveyor_tasks": len(self.scenario.conveyor_tasks or []),
            "conveyor_completed": sum(1 for p in conv_task_phases.values() 
                                     if p == MaterialPhaseEnum.COMPLETED.value),
            "conveyor_in_transit": sum(1 for p in conv_task_phases.values() 
                                       if p == MaterialPhaseEnum.CONVEYOR_TRANSIT.value),
            "materials_ever_on_conveyor": len(self._materials_on_conveyor),
            "materials_waiting_pickup": sum(1 for m in self._materials_on_conveyor.values() 
                                           if m.status == "waiting_pickup"),
        }

        summary = {
            "total_simulation_time": time_steps * time_per_step,
            "total_tasks": total_tasks,
            "completed_tasks": completed_count,
            "completion_rate": round(completed_count / total_tasks * 100, 1) if total_tasks > 0 else 0,
            "total_distance": round(total_distance, 1),
            "total_events": len(events),
            "fault_injected": self.fault_config.get("enabled", False),
            "peak_active_agvs": max((s.metrics.get("active_agvs", 0) for s in snapshots), default=0),
            **conveyor_stats,
        }

        trajectory = SimulationTrajectory(
            scenario_name=self.scenario.metadata.name,
            algorithm_name=algo_name,
            total_steps=time_steps,
            time_per_step=time_per_step,
            snapshots=snapshots,
            events=events,
            summary=summary,
        )

        return trajectory

    # ==================== 辅助方法 ====================

    def _get_node_pos(self, node_id: str) -> Tuple[float, float]:
        """获取节点的坐标(x,y)"""
        node = self.node_map.get(node_id)
        if node:
            return (float(node.get("x", 0)), float(node.get("y", 0)))
        return (0.0, 0.0)

    def _get_edge_attr(self, from_node: str, to_node: str) -> Dict[str, Any]:
        """获取两个节点之间的边属性（是否输送线、速度、容量等）"""
        for neighbor_info in self.adjacency.get(from_node, []):
            if isinstance(neighbor_info, tuple) and len(neighbor_info) == 3:
                neighbor, weight, edge_attr = neighbor_info
                if neighbor == to_node:
                    return edge_attr
            elif isinstance(neighbor_info, tuple) and len(neighbor_info) == 2:
                neighbor, weight = neighbor_info
                if neighbor == to_node:
                    return {"is_conveyor": False, "conveyor_speed_mps": None, "capacity": None}
        # 回退到 edge_map 查找
        eid = f"{from_node}_{to_node}"
        rev_eid = f"{to_node}_{from_node}"
        e = self.edge_map.get(eid) or self.edge_map.get(rev_eid)
        if e:
            return {
                "is_conveyor": e.get("is_conveyor", False),
                "conveyor_speed_mps": e.get("conveyor_speed_mps"),
                "capacity": e.get("capacity"),
            }
        return {}

    def _is_conveyor_edge(self, from_node: str, to_node: str) -> bool:
        """检查两个节点之间的边是否为输送线边（AGV不能通行）"""
        attr = self._get_edge_attr(from_node, to_node)
        return attr.get("is_conveyor", False)

    def _calculate_path(self, from_node: str, to_node: str, allow_conveyor: bool = False) -> List[str]:
        """
        BFS/Dijkstra最短路径计算
        
        Args:
            from_node: 起始节点
            to_node: 目标节点
            allow_conveyor: 是否允许路径经过输送线边（默认False，AGV不能上输送线）
        
        Note:
            AGV只能在普通边上行驶，输送线边是物料自动传输的通道。
            AGV与输送线的交互只在接驳点（入口/出口节点）进行。
        """
        if from_node == to_node:
            return [from_node]

        visited = {from_node}
        # 使用优先队列实现 Dijkstra（考虑权重）
        queue = [(0.0, from_node, [from_node])]  # (累积权重, 当前节点, 路径)

        while queue:
            current_cost, current, path = heapq.heappop(queue)
            for neighbor_info in self.adjacency.get(current, []):
                # 兼容新旧两种邻接表格式
                if isinstance(neighbor_info, tuple) and len(neighbor_info) == 3:
                    neighbor, weight, edge_attr = neighbor_info
                elif isinstance(neighbor_info, tuple) and len(neighbor_info) == 2:
                    neighbor, weight = neighbor_info
                    edge_attr = {}
                else:
                    continue

                # === 关键：AGV不能走输送线边 ===
                if not allow_conveyor and edge_attr.get("is_conveyor"):
                    continue  # 跳过输送线边，AGV不可通行

                if neighbor not in visited:
                    new_cost = current_cost + weight
                    new_path = path + [neighbor]
                    if neighbor == to_node:
                        return new_path
                    visited.add(neighbor)
                    heapq.heappush(queue, (new_cost, neighbor, new_path))

        # 无法到达，返回直连
        return [from_node, to_node]

    def _validate_agv_path(self, path: List[str]) -> Tuple[bool, str]:
        """
        验证AGV路径是否合法（不包含输送线边）
        
        Args:
            path: 节点路径列表
            
        Returns:
            (是否合法, 错误信息)
        """
        if not path or len(path) < 2:
            return True, ""
        
        for i in range(len(path) - 1):
            from_node = path[i]
            to_node = path[i + 1]
            
            if self._is_conveyor_edge(from_node, to_node):
                return False, f"路径包含输送线边: {from_node} -> {to_node}"
        
        return True, ""

    def _euclidean(self, p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        """欧氏距离"""
        return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

    def _plan_all_paths(self, agv_states: Dict, task_states: Dict):
        """为所有AGV规划初始路径"""
        agv_task_map: Dict[str, str] = {}  # agv_id -> task_id
        
        for tid, aid in self._assignment_map.items():
            if aid not in agv_task_map:
                agv_task_map[aid] = tid

        for aid, tid in agv_task_map.items():
            if aid not in agv_states or tid not in task_states:
                continue
            
            tstate = task_states[tid]
            if tstate["status"] != "pending":
                continue
                
            astate = agv_states[aid]
            
            # 先到取货点
            path_to_pickup = self._calculate_path(astate["node_id"], tstate["pickup_node_id"])
            if len(path_to_pickup) > 1:
                astate["path"] = path_to_pickup
                astate["state"] = "moving"
                astate["current_task_id"] = tid
                tstate["status"] = "assigned"

    def _preassign_conveyor_tasks(self, agv_states: Dict, task_states: Dict):
        """
        为输送线协同任务预分配AGV
        
        只为需要AGV的任务分配：
          - mixed任务（阶段1开始）
          - agv_only任务（纯AGV搬运）
        
        conveyor_only任务不需要AGV，跳过
        """
        conv_tasks = self.scenario.conveyor_tasks or []
        if not conv_tasks:
            return

        # 找到所有当前空闲的AGV（没有路径/任务的）
        idle_agvs = [
            aid for aid, astate in agv_states.items()
            if astate["state"] == "idle" and not astate.get("current_task_id")
        ]

        if not idle_agvs:
            return

        # 按优先级排序输送线任务，只筛选需要AGV的任务
        sorted_ctasks = sorted(
            [t for t in conv_tasks 
             if t.get("task_type", "mixed") in ("agv_only", "mixed")],
            key=lambda t: t.get("priority", 5), reverse=True
        )

        assigned_count = 0
        for ctask in sorted_ctasks:
            if assigned_count >= len(idle_agvs):
                break
            
            tid = ctask.get("id", "")
            ttype = ctask.get("task_type", "mixed")
            phase = self._task_phase.get(tid)
            
            # 只为阶段1的任务分配AGV
            if phase != MaterialPhaseEnum.AGV_PICKUP.value:
                continue
            
            if ttype == "agv_only":
                # 纯AGV任务：取货点就是agv_pickup_node_id，目的地是agv_dropoff_node_id
                pickup_nid = ctask.get("agv_pickup_node_id", "")
            elif ttype == "mixed":
                # 混合任务：去agv_pickup_node_id取货
                pickup_nid = ctask.get("agv_pickup_node_id", "")
            else:
                continue  # conveyor_only不需要AGV
            
            if not pickup_nid:
                continue

            aid = idle_agvs[assigned_count]
            astate = agv_states[aid]

            path_to_pickup = self._calculate_path(astate["node_id"], pickup_nid)
            if len(path_to_pickup) > 1:
                astate["path"] = path_to_pickup
                astate["state"] = "moving"
                
                # 根据任务类型设置不同的current_task_id格式
                if ttype == "agv_only":
                    astate["current_task_id"] = f"conv_agvonly_{tid}"
                else:
                    astate["current_task_id"] = f"conv_{tid}"
                    
                astate["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_PICKUP.value
                astate["_conveyor_task_type"] = ttype  # 记录任务类型
                astate["progress"] = 0.0
                self._assignment_map[tid] = aid
                assigned_count += 1

    def _replan_for_agv(self, agv_id: str, agv_states: Dict, task_states: Dict):
        """为恢复后的AGV重新规划任务"""
        # 找到一个未完成的未分配任务
        for tid, tstate in task_states.items():
            if tstate["status"] == "pending" and not tstate.get("assigned_agv_id"):
                self._assign_task_to_agv(agv_id, tid, agv_states, task_states)
                return
        
        # 或者找一个已分配但还没开始的
        for tid, tstate in task_states.items():
            if tstate["status"] == "assigned":
                aid = tstate.get("assigned_agv_id")
                if aid and aid in agv_states and agv_states[aid]["state"] == "fault":
                    # 原来的AGV还坏着，重新分配
                    tstate["assigned_agv_id"] = agv_id
                    self._assign_task_to_agv(agv_id, tid, agv_states, task_states)
                    return

    def _assign_next_task(
        self,
        agv_id: str,
        agv_states: Dict,
        task_states: Dict,
        step: int,
        events: List[SimulationEvent],
    ) -> bool:
        """为指定AGV分配下一个任务"""
        if agv_id not in agv_states:
            return False
            
        astate = agv_states[agv_id]
        if astate.get("current_task_id"):
            return False

        # 优先找已分配给该AGV但还未处理的任务
        best_tid = None
        best_priority = 0

        for tid, tstate in task_states.items():
            if tstate["status"] == "pending":
                assigned_aid = tstate.get("assigned_agv_id")
                if assigned_aid == agv_id:
                    if tstate["priority"] > best_priority:
                        best_priority = tstate["priority"]
                        best_tid = tid

        if not best_tid:
            # 找任何pending的任务
            for tid, tstate in task_states.items():
                if tstate["status"] == "pending" and not tstate.get("assigned_agv_id"):
                    if tstate["priority"] > best_priority:
                        best_priority = tstate["priority"]
                        best_tid = tid

        if best_tid:
            return self._assign_task_to_agv(agv_id, best_tid, agv_states, task_states, step, events)

        return False

    def _assign_task_to_agv(
        self,
        agv_id: str,
        task_id: str,
        agv_states: Dict,
        task_states: Dict,
        step: int = -1,
        events: List[SimulationEvent] = None,
    ) -> bool:
        """将任务分配给AGV并规划路径"""
        if agv_id not in agv_states or task_id not in task_states:
            return False

        astate = agv_states[agv_id]
        tstate = task_states[task_id]

        if tstate["status"] not in ("pending", "assigned"):
            return False

        path = self._calculate_path(astate["node_id"], tstate["pickup_node_id"])
        astate["path"] = path
        astate["state"] = "moving"
        astate["current_task_id"] = task_id
        astate["progress"] = 0.0

        tstate["status"] = "assigned"
        tstate["assigned_agv_id"] = agv_id

        if events is not None and step >= 0:
            ev = SimulationEvent(
                time_step=step,
                event_type="task_assigned",
                entity_id=task_id,
                description=f"任务 {task_id} 分配给 AGV-{agv_id}",
                position=astate["position"],
            )
            events.append(ev)

        return True

    # ==================== 物料流转方法（AGV-输送线协同）====================

    def _complete_agv_only_task(self, ctask, tid, agv_id, agv_states, task_states, step, events):
        """完成纯AGV任务：AGV到达卸货点，释放资源"""
        if agv_id not in agv_states:
            return
            
        astate = agv_states[agv_id]
        
        # 任务完成
        self._task_phase[tid] = MaterialPhaseEnum.COMPLETED.value
        
        # AGV释放
        astate["current_task_id"] = None
        astate["state"] = "idle"
        astate["path"] = []
        if "_conveyor_task_phase" in astate:
            del astate["_conveyor_task_phase"]
        
        ev = SimulationEvent(
            time_step=step,
            event_type="task_completed",
            entity_id=tid,
            description=f"[纯AGV完成] 任务 {tid} 完成！AGV-{agv_id} 将物料送达目的地",
            position=astate["position"],
            extra={"task_type": "agv_only"},
        )
        events.append(ev)


    def _complete_conveyor_only_task(self, ctask, tid, mat_id, step, events):
        """
        完成纯输送线任务：
          - 物料已到达下料口并等待了足够时间
          - 物料被"消除"（模拟离开系统）
          - 任务标记为完成
        """
        # 更新任务阶段
        self._task_phase[tid] = MaterialPhaseEnum.COMPLETED.value
        
        # 清理物料状态
        if mat_id in self._materials_on_conveyor:
            del self._materials_on_conveyor[mat_id]
        
        exit_nid = ctask.get("conveyor_exit_node_id", "")
        
        ev = SimulationEvent(
            time_step=step,
            event_type="task_completed",
            entity_id=tid,
            description=f"[纯输送线完成] 任务 {tid} 完成！物料({mat_id})在下料口{exit_nid}等待后已被消除",
            position=self._get_node_pos(exit_nid),
            extra={"task_type": "conveyor_only", "material_id": mat_id},
        )
        events.append(ev)


    def _auto_start_conveyor_only_task(self, ctask, tid, step, events):
        """
        启动纯输送线任务：
          - 在上料口自动产生物料
          - 物料在输送线上转运到下料口  
          - 到达下料口后等待消除（不需要AGV）
        
        纯输送线任务的完整生命周期：
          1. 上料口产生物料 → 2. 输送线转运 → 3. 下料口到达 → 4. 等待时间后消除
        """
        entry_nid = ctask.get("conveyor_entry_node_id", "")  # 上料口
        exit_nid = ctask.get("conveyor_exit_node_id", "")    # 下料口
        if not entry_nid or not exit_nid:
            return
        
        # 检查入口资源（同一时刻只允许一个任务产生物料）
        current_user = self._entry_in_use.get(entry_nid, "")
        if current_user and current_user != tid:
            return
        
        self._entry_in_use[entry_nid] = tid
        self._material_counter += 1
        mat_id = f"MAT_PURE_{self._material_counter:04d}"
        
        est_conv_time = ctask.get("estimated_conveyor_time", 30)
        mat = MaterialOnConveyor(
            material_id=mat_id, task_id=tid,
            from_segment_id=ctask.get("from_segment_id", ""),
            to_segment_id=ctask.get("to_segment_id", ""),
            entry_node_id=entry_nid, exit_node_id=exit_nid,
            enter_step=step,
            estimated_arrival_step=step + int(est_conv_time / 0.8),
            progress=0.0, status="transiting",
        )
        self._materials_on_conveyor[mat_id] = mat
        self._task_phase[tid] = MaterialPhaseEnum.CONVEYOR_TRANSIT.value
        if self._entry_in_use.get(entry_nid) == tid:
            self._entry_in_use[entry_nid] = ""
        
        events.append(SimulationEvent(step, "material_on_conveyor", tid,
            f"[纯输送线] 物料({mat_id})进入输送线",
            self._get_node_pos(entry_nid), {"task_type": "conveyor_only"}))

    def _simulate_conveyor_transit(self, step, conv_states, task_states, agv_states, events):
        """
        模拟物料在输送线上的流转（每步调用）
        
        三种任务类型的不同流转逻辑：
          
          1. 纯输送线任务 (conveyor_only):
             上料口产生物料 → 输送线自动转运 → 下料口到达 → 等待消除时间 → 任务完成
             
          2. 纯AGV任务 (agv_only):
             AGV取货 → AGV搬运（可经缓存区）→ 卸货点 → 物料等待消除
             （不经过输送线，在普通路径处理）
             
          3. 混合任务 (mixed) - 长程任务:
             输送线上料口产生物料 → 输送线转运 → 下料口 → AGV接货 → AGV搬运到最终目的地
        """
        conv_tasks = self.scenario.conveyor_tasks or []
        
        # --- 阶段推进：更新已在输送线上物料的进度 ---
        for mat_id, mat in list(self._materials_on_conveyor.items()):
            if mat.status == "picked_up":
                continue  # 已被AGV接走
            
            # 查找对应输送线段的运行状态
            to_seg = mat.to_segment_id
            cstate = conv_states.get(to_seg)
            
            if not cstate or cstate["status"] != "running":
                # 输送线堵塞/停止，物料暂停
                continue
            
            # 计算转运速度（基于输送线速度和容量负载）
            speed_mps = cstate["speed_mps"] or 0.5
            load_ratio = cstate["current_load"] / max(cstate["capacity"], 1)
            
            # 负载越高速度越慢（排队效应）
            effective_speed = speed_mps * max(0.3, 1.0 - load_ratio * 0.6)
            
            # 进度递增
            progress_per_step = effective_speed * 0.08  # 缩放因子
            mat.progress += progress_per_step
            
            if mat.progress >= 1.0:
                # 转运完成！物料到达出口（下料口）
                mat.progress = 1.0
                
                # 查找该物料所属的任务类型
                ctask = next((t for t in (self.scenario.conveyor_tasks or []) 
                             if t.get("id") == mat.task_id), None)
                task_type = ctask.get("task_type", "mixed") if ctask else "mixed"
                
                if task_type == "conveyor_only":
                    # === 纯输送线任务：到达下料口后进入等待消除状态 ===
                    exit_node = mat.exit_node_id  # 获取下料口节点
                    mat.status = "waiting_removal"  # 等待消除（不需要AGV）
                    mat.exit_step = step  # 记录到达时间
                    
                    ev = SimulationEvent(
                        time_step=step,
                        event_type="conveyor_transit_end",
                        entity_id=mat.task_id,
                        description=f"[纯输送线] 物料({mat_id})已到达下料口{exit_node}，等待消除",
                        position=self._get_node_pos(exit_node),
                        extra={"material_id": mat_id, "exit_node": exit_node, "task_type": "conveyor_only"},
                    )
                    events.append(ev)
                else:
                    # === 混合任务：到达出口，需要AGV接货 ===
                    mat.status = "waiting_pickup"
                    
                    exit_node = mat.exit_node_id
                    if exit_node:
                        self._conveyor_task_waiting_pickup.setdefault(exit_node, []).append(mat_id)
                    
                    ev = SimulationEvent(
                        time_step=step,
                        event_type="conveyor_transit_end",
                        entity_id=mat.task_id,
                        description=f"任务 {mat.task_id} 的物料已到达输送线 {to_seg} 出口({exit_node})，等待AGV接货",
                        position=self._get_node_pos(exit_node),
                    extra={"material_id": mat_id, "exit_node": exit_node},
                )
                events.append(ev)

        # --- 处理输送线任务的阶段转换（支持三种任务类型）---
        for ctask in conv_tasks:
            tid = ctask.get("id", "")
            ttype = ctask.get("task_type", "mixed")
            phase = self._task_phase.get(tid, MaterialPhaseEnum.AGV_PICKUP.value)
            
            # 纯输送线任务：自动开始loading（模拟物料在入口产生）
            if ttype == "conveyor_only" and phase == MaterialPhaseEnum.CONVEYOR_LOADING.value:
                self._auto_start_conveyor_only_task(ctask, tid, step, events)
                continue
            
            # === 纯输送线任务：检查是否到达下料口并等待消除 ===
            if ttype == "conveyor_only" and phase == MaterialPhaseEnum.CONVEYOR_TRANSIT.value:
                # 检查该任务是否有物料已到达下料口等待消除
                for mat_id, mat in self._materials_on_conveyor.items():
                    if (mat.task_id == tid and mat.status == "waiting_removal"
                            and hasattr(mat, 'exit_step')):
                        # 计算等待时间（可配置，默认20步）
                        removal_wait_time = ctask.get("removal_wait_time", 20)
                        waited_steps = step - mat.exit_step
                        
                        if waited_steps >= removal_wait_time:
                            # 等待时间到！物料消除，任务完成
                            self._complete_conveyor_only_task(ctask, tid, mat_id, step, events)
                        break  # 一个任务只处理一次
            
            # === 自动阶段推进：检查物料状态来触发阶段转换（混合任务）===
            if phase == MaterialPhaseEnum.CONVEYOR_TRANSIT.value and ttype != "conveyor_only":
                # 检查该任务是否有物料已到达出口等待接货
                for mat_id, mat in self._materials_on_conveyor.items():
                    if (mat.task_id == tid and mat.status == "waiting_pickup"
                            and self._task_phase.get(tid) == MaterialPhaseEnum.CONVEYOR_TRANSIT.value):
                        # 物料到达出口！推进到阶段4（AGV去接货）
                        self._task_phase[tid] = MaterialPhaseEnum.AGV_RECEIVING.value
                        
                        ev = SimulationEvent(
                            time_step=step,
                            event_type="conveyor_transit_end",
                            entity_id=tid,
                            description=f"[流转阶段3→4] 任务{tid}的物料({mat_id})到达出口{mat.exit_node_id}，等待AGV接货",
                            position=self._get_node_pos(mat.exit_node_id),
                            extra={"material_id": mat_id, "exit_node": mat.exit_node_id},
                        )
                        events.append(ev)
                        
                        # 立即尝试为这个任务分配合适的AGV去接货（只分配一次）
                        self._dispatch_single_receiving_agv(ctask, tid, mat.exit_node_id, agv_states, events, step)
                        break  # 一个任务只处理一次
            
            # 根据当前阶段执行不同逻辑
            if phase == MaterialPhaseEnum.AGV_PICKUP.value:
                # 阶段1: 只处理需要AGV的任务（跳过conveyor_only）
                if ttype != "conveyor_only":
                    self._try_start_conveyor_task_phase1(ctask, tid, agv_states, task_states, step, events)
                
            elif phase == MaterialPhaseEnum.CONVEYOR_LOADING.value:
                # 阶段2: 检查是否到达了输送线入口（放料点）
                self._check_conveyor_loading(ctask, tid, agv_states, events, step)
                
            elif phase == MaterialPhaseEnum.CONVEYOR_TRANSIT.value:
                # 阶段3: 物料在输送线上转运（由上面的进度更新处理）
                pass  # 已在上方 _materials_on_conveyor 更新中处理
                
            elif phase == MaterialPhaseEnum.AGV_RECEIVING.value:
                # 阶段4: 安排AGV去出口接货
                self._try_dispatch_receiving_agv(ctask, tid, agv_states, task_states, step, events)
                
            elif phase == MaterialPhaseEnum.AGV_DROPOFF.value:
                # 阶段5: AGV运送物料到最终目的地
                self._check_final_dropoff(ctask, tid, agv_states, task_states, step, events)

    def _try_start_conveyor_task_phase1(self, ctask, tid, agv_states, task_states, step, events):
        """阶段1: 尝试为输送线任务分配AGV去取货"""
        pickup_nid = ctask.get("agv_pickup_node_id", "")
        if not pickup_nid:
            return
        
        # 如果该任务已经分配了AGV，不再重复分配
        if tid in self._assignment_map:
            return
        
        # 找一个idle的AGV
        for aid, astate in agv_states.items():
            if astate["state"] == "idle" and not astate.get("current_task_id"):
                # 分配这个AGV去取货
                path_to_pickup = self._calculate_path(astate["node_id"], pickup_nid)
                astate["path"] = path_to_pickup
                astate["state"] = "moving"
                astate["current_task_id"] = f"conv_{tid}"
                astate["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_PICKUP.value
                astate["progress"] = 0.0
                self._assignment_map[tid] = aid
                
                ev = SimulationEvent(
                    time_step=step,
                    event_type="task_assigned",
                    entity_id=tid,
                    description=f"[流转] AGV-{aid} 开始前往 {pickup_nid} 取物料(任务{tid} 阶段1)",
                    position=astate["position"],
                    extra={"phase": "agv_pickup", "pickup": pickup_nid},
                )
                events.append(ev)
                break

    def _try_assign_conveyor_task(self, agv_id, agv_states, task_states, step, events) -> bool:
        """
        为空闲AGV分配待处理的输送线协同任务
        
        Returns:
            是否成功分配了任务
        """
        conv_tasks = self.scenario.conveyor_tasks or []
        
        # 按优先级排序，找最高优先级的待处理任务
        best_tid = None
        best_priority = -1
        
        for ctask in conv_tasks:
            tid = ctask.get("id", "")
            phase = self._task_phase.get(tid)
            
            # 只处理需要AGV操作的阶段（1/4/5）
            if phase in (MaterialPhaseEnum.AGV_PICKUP.value, 
                        MaterialPhaseEnum.AGV_RECEIVING.value):
                pri = ctask.get("priority", 5)
                if pri > best_priority:
                    best_priority = pri
                    best_tid = tid
        
        if best_tid:
            ctask = next((t for t in conv_tasks if t["id"] == best_tid), None)
            if not ctask:
                return False
            
            phase = self._task_phase[best_tid]
            
            if phase == MaterialPhaseEnum.AGV_PICKUP.value:
                self._try_start_conveyor_task_phase1(ctask, best_tid, agv_states, task_states, step, events)
                return True
            elif phase == MaterialPhaseEnum.AGV_RECEIVING.value:
                self._try_dispatch_receiving_agv(ctask, best_tid, agv_states, task_states, step, events)
                return True
        
        return False

    def _dispatch_single_receiving_agv(self, ctask, tid, exit_node_id, agv_states, events, step):
        """为阶段4任务分配唯一一个AGV去出口接货（只调用一次，避免重复）"""
        # 注意：不在这里抢占出口资源，而是等AGV真正到达出口时再检查
        # 这样可以避免"占着茅坑不拉屎"的问题
        
        # 找最近的idle AGV
        best_aid = None
        best_dist = float('inf')
        
        for aid, astate in agv_states.items():
            if astate["state"] != "idle" or astate.get("current_task_id"):
                continue
            
            dist = len(self._calculate_path(astate["node_id"], exit_node_id))
            if dist < best_dist:
                best_dist = dist
                best_aid = aid
        
        if not best_aid or best_dist < 2:  # 至少需要能到达
            return
        
        astate = agv_states[best_aid]
        path_to_exit = self._calculate_path(astate["node_id"], exit_node_id)
        
        astate["path"] = path_to_exit
        astate["state"] = "moving"
        astate["current_task_id"] = f"conv_receive_{tid}"
        astate["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_RECEIVING.value
        astate["progress"] = 0.0
        self._assignment_map[tid] = best_aid  # 更新为接货AGV
        # 不在这里标记出口占用！等真正到达出口时再处理
        
        ev = SimulationEvent(
            time_step=step,
            event_type="task_assigned",
            entity_id=tid,
            description=f"[流转] AGV-{best_aid} 前往 {exit_node_id} 接物料(任务{tid} 阶段4)",
            position=astate["position"],
            extra={"phase": "agv_receiving", "exit_node": exit_node_id},
        )
        events.append(ev)

    def _check_conveyor_loading(self, ctask, tid, agv_states, events, step):
        """阶段2: AGV到达输送线入口，放下物料（带入口排队机制）"""
        entry_nid = ctask.get("conveyor_entry_node_id", "")
        aid = self._assignment_map.get(tid)
        
        if not aid or aid not in agv_states:
            return
        
        astate = agv_states[aid]
        
        # 检查AGV是否到达入口节点
        current_phase = astate.get("_conveyor_task_phase", "")
        at_entry = astate["node_id"] == entry_nid
        is_conv_task = (astate.get("current_task_id") or "") == f"conv_{tid}"
        
        if (at_entry and is_conv_task 
                and current_phase in (MaterialPhaseEnum.AGV_PICKUP.value, 
                                     MaterialPhaseEnum.CONVEYOR_LOADING.value)):
            
            # === 入口资源争用检查 ===
            current_user = self._entry_in_use.get(entry_nid, "")
            
            if current_user and current_user != tid:
                # 入口被占用！加入等待队列
                if tid not in self._entry_queue.get(entry_nid, []):
                    self._entry_queue.setdefault(entry_nid, []).append(tid)
                    
                    ev = SimulationEvent(
                        time_step=step,
                        event_type="node_blocked",
                        entity_id=tid,
                        description=f"[入口排队] 任务{tid}的AGV-{aid}到达入口{entry_nid}，但正在被任务{current_user}使用，进入等待队列(当前{len(self._entry_queue[entry_nid])}个等待)",
                        position=self._get_node_pos(entry_nid),
                        extra={"queue_position": len(self._entry_queue.get(entry_nid, [])), "entry_node": entry_nid},
                    )
                    events.append(ev)
                
                # AGV在原地等待（保持executing状态但不移动）
                astate["state"] = "executing"  # 用executing模拟"等待中"
                astate["progress"] = 0.0
                astate["_waiting_for_entry"] = entry_nid  # 标记：正在等待入口
                return
            
            # === 可以使用入口 ===
            self._entry_in_use[entry_nid] = tid
            
            # 创建物料对象，放入输送线
            self._material_counter += 1
            mat_id = f"MAT_{self._material_counter:04d}"
            
            est_conv_time = ctask.get("estimated_conveyor_time", 30)
            est_arrival = step + int(est_conv_time / 0.8)  # 粗略估算到达步数
            
            mat = MaterialOnConveyor(
                material_id=mat_id,
                task_id=tid,
                from_segment_id=ctask.get("from_segment_id", ""),
                to_segment_id=ctask.get("to_segment_id", ""),
                entry_node_id=entry_nid,
                exit_node_id=ctask.get("conveyor_exit_node_id", ""),
                enter_step=step,
                estimated_arrival_step=est_arrival,
                progress=0.0,
                status="transiting",
            )
            self._materials_on_conveyor[mat_id] = mat
            
            # 更新任务阶段
            self._task_phase[tid] = MaterialPhaseEnum.CONVEYOR_TRANSIT.value
            
            # === 释放入口资源（放料完成，入口空闲了）===
            if self._entry_in_use.get(entry_nid) == tid:
                self._entry_in_use[entry_nid] = ""  # 释放！
            
            # AGV释放
            astate["current_task_id"] = None
            astate["state"] = "idle"
            astate["path"] = []
            if "_conveyor_task_phase" in astate:
                del astate["_conveyor_task_phase"]
            if "_waiting_for_entry" in astate:
                del astate["_waiting_for_entry"]  # 清除入口等待标记
            
            ev = SimulationEvent(
                time_step=step,
                event_type="material_on_conveyor",
                entity_id=tid,
                description=f"[流转] AGV-{aid} 将物料放到输送线入口 {entry_nid}，物料({mat_id})开始转运",
                position=self._get_node_pos(entry_nid),
                extra={"material_id": mat_id, "phase": "conveyor_transit"},
            )
            events.append(ev)
            
            ev2 = SimulationEvent(
                time_step=step,
                event_type="conveyor_transit_start",
                entity_id=mat_id,
                description=f"输送线开始转运物料 {mat_id} → 出口 {ctask.get('conveyor_exit_node_id', '')}",
                position=self._get_node_pos(entry_nid),
            )
            events.append(ev2)
            
            # === 处理入口队列中的下一个任务 ===
            self._process_entry_queue(entry_nid, agv_states, events, step)
    
    def _process_entry_queue(self, entry_nid, agv_states, events, step):
        """处理入口等待队列：释放入口后，通知下一个AGV可以放料"""
        queue = self._entry_queue.get(entry_nid, [])
        
        if not queue:
            return
        
        next_tid = queue.pop(0)  # 取出队首任务
        
        next_aid = self._assignment_map.get(next_tid)
        if not next_aid or next_aid not in agv_states:
            return
        
        next_astate = agv_states[next_aid]
        
        # 检查该AGV是否已经到达入口
        next_ctask = next((t for t in (self.scenario.conveyor_tasks or []) 
                          if t.get("id") == next_tid), None)
        if not next_ctask:
            return
        
        target_entry = next_ctask.get("conveyor_entry_node_id", "")
        if next_astate["node_id"] == target_entry and next_astate["state"] == "executing":
            # AGV在等待，可以继续放料了
            self._entry_in_use[entry_nid] = next_tid
            next_astate["state"] = "moving"  # 恢复为moving以便触发下一步检查
            next_astate["progress"] = 0.5    # 设置一个进度，让下一步立即触发放料逻辑
            if "_waiting_for_entry" in next_astate:
                del next_astate["_waiting_for_entry"]  # 清除等待标记
            
            ev = SimulationEvent(
                time_step=step,
                event_type="node_unblocked",
                entity_id=next_tid,
                description=f"[入口释放] 入口{entry_nid}空闲，任务{next_tid}的AGV-{next_aid}可以放料了",
                position=self._get_node_pos(entry_nid),
            )
            events.append(ev)

    def _try_dispatch_receiving_agv(self, ctask, tid, agv_states, task_states, step, events):
        """
        阶段4: 安排AGV去输送线出口接物料（仅作为备用调度器）
        
        注意：主要的单次分配由 _dispatch_single_receiving_agv 在阶段转换时完成。
        此方法只在没有AGV被分配时作为备用触发。
        """
        exit_nid = ctask.get("conveyor_exit_node_id", "")
        if not exit_nid:
            return
        
        # 检查是否已经有任何AGV在处理这个任务的接货
        for aid, astate in agv_states.items():
            task_id = str(astate.get("current_task_id") or "")
            if (f"conv_receive_{tid}" in task_id or f"conv_{tid}" in task_id) and astate["state"] == "moving":
                return  # 已有AGV在处理，不重复分配
        
        # 检查该出口是否有等待接货的物料
        waiting_mats = self._conveyor_task_waiting_pickup.get(exit_nid, [])
        relevant_mats = [m for m in waiting_mats 
                        if self._materials_on_conveyor[m].task_id == tid]
        
        if not relevant_mats:
            return
        
        # 只找一个idle的AGV
        for aid, astate in agv_states.items():
            if astate["state"] == "idle" and not astate.get("current_task_id"):
                path_to_exit = self._calculate_path(astate["node_id"], exit_nid)
                astate["path"] = path_to_exit
                astate["state"] = "moving"
                astate["current_task_id"] = f"conv_receive_{tid}"
                astate["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_RECEIVING.value
                astate["progress"] = 0.0
                
                ev = SimulationEvent(
                    time_step=step,
                    event_type="task_assigned",
                    entity_id=tid,
                    description=f"[流转] AGV-{aid} 前往 {exit_nid} 接物料(任务{tid} 阶段4)",
                    position=astate["position"],
                    extra={"phase": "agv_receiving", "exit_node": exit_nid},
                )
                events.append(ev)
                break

    def _check_final_dropoff(self, ctask, tid, agv_states, task_states, step, events):
        """阶段5: AGV到达最终卸货点（含出口释放）"""
        dropoff_nid = ctask.get("agv_dropoff_node_id", "")
        aid = self._assignment_map.get(tid)
        
        if not aid or aid not in agv_states:
            return
        
        astate = agv_states[aid]
        
        # 检查AGV是否到达最终目的地
        current_phase = astate.get("_conveyor_task_phase", "")
        at_dropoff = (astate["node_id"] == dropoff_nid) or len(astate.get("path", [])) <= 1
        # 匹配 conv_TASKID 和 conv_receive_TASKID 两种格式
        task_id_str = (astate.get("current_task_id") or "")
        is_conv_task = (task_id_str == f"conv_{tid}" 
                       or task_id_str == f"conv_receive_{tid}"
                       or tid in task_id_str)
        
        if (at_dropoff and is_conv_task 
                and current_phase in (MaterialPhaseEnum.AGV_RECEIVING.value,
                                     MaterialPhaseEnum.AGV_DROPOFF.value)):
            
            # === 释放出口资源 ===
            exit_nid = ctask.get("conveyor_exit_node_id", "")
            if exit_nid and self._exit_in_use.get(exit_nid) == tid:
                self._exit_in_use[exit_nid] = ""
                self._process_exit_queue(exit_nid, agv_states, events, step)
            
            # 任务完成！
            self._task_phase[tid] = MaterialPhaseEnum.COMPLETED.value
            
            # 清理物料状态
            for mid, m in list(self._materials_on_conveyor.items()):
                if m.task_id == tid:
                    m.status = "picked_up"
            
            # 从等待队列中移除
            if exit_nid in self._conveyor_task_waiting_pickup:
                self._conveyor_task_waiting_pickup[exit_nid] = [
                    m for m in self._conveyor_task_waiting_pickup[exit_nid]
                    if self._materials_on_conveyor.get(m, type("", (), {"task_id":""})()).task_id != tid
                ]
            
            # AGV释放
            astate["current_task_id"] = None
            astate["state"] = "idle"
            astate["path"] = []
            if "_conveyor_task_phase" in astate:
                del astate["_conveyor_task_phase"]
            
            ev = SimulationEvent(
                time_step=step,
                event_type="task_completed",
                entity_id=tid,
                description=f"[流转完成] 任务 {tid} 完成！物料经输送线转运→AGV接货→送达目的地 {dropoff_nid}",
                position=self._get_node_pos(dropoff_nid),
                extra={"phases_completed": 5, "final_dropoff": dropoff_nid},
            )
            events.append(ev)
    
    def _process_exit_queue(self, exit_nid, agv_states, events, step):
        """处理出口等待队列：释放出口后，通知下一个AGV可以接货"""
        queue = self._exit_queue.get(exit_nid, [])
        
        if not queue:
            return
        
        next_tid = queue.pop(0)
        
        next_aid = self._assignment_map.get(next_tid)
        if not next_aid or next_aid not in agv_states:
            return
        
        next_astate = agv_states[next_aid]
        next_ctask = next((t for t in (self.scenario.conveyor_tasks or []) 
                          if t.get("id") == next_tid), None)
        if not next_ctask:
            return
        
        target_exit = next_ctask.get("conveyor_exit_node_id", "")
        # AGV在等待状态且已经在出口位置
        if (next_astate["state"] == "executing" and 
            next_astate.get("_waiting_for_exit") == exit_nid):
            
            self._exit_in_use[exit_nid] = next_tid
            
            # 唤醒AGV：恢复为moving以触发下一步的接货逻辑
            dropoff_nid = next_ctask.get("agv_dropoff_node_id", "")
            if dropoff_nid:
                path_to_dropoff = self._calculate_path(next_astate["node_id"], dropoff_nid)
                next_astate["path"] = path_to_dropoff
                next_astate["_conveyor_task_phase"] = MaterialPhaseEnum.AGV_DROPOFF.value
                self._task_phase[next_tid] = MaterialPhaseEnum.AGV_DROPOFF.value
                
                # 接走物料
                for mid, m in list(self._materials_on_conveyor.items()):
                    if m.task_id == next_tid and m.status == "waiting_pickup":
                        m.status = "picked_up"
                        
                        ev = SimulationEvent(
                            time_step=step,
                            event_type="material_pickup_from_conveyor",
                            entity_id=next_tid,
                            description=f"[出口释放→接货] 出口{exit_nid}空闲，任务{next_tid}的AGV-{next_aid}立即接走物料({mid})",
                            position=next_astate["position"],
                            extra={"material_id": mid},
                        )
                        events.append(ev)
                        break
                
                # 清除等待标记，恢复正常状态
                next_astate["state"] = "moving"
                next_astate["progress"] = 0.0
                if "_waiting_for_exit" in next_astate:
                    del next_astate["_waiting_for_exit"]
                
                # 接货完成后释放出口（让下一个排队的人可以用）
                if self._exit_in_use.get(exit_nid) == next_tid:
                    self._exit_in_use[exit_nid] = ""
                    self._process_exit_queue(exit_nid, agv_states, events, step)  # 递归处理下一个


# ==================== 便捷函数 ====================

def run_simulation(
    scenario,
    algorithm_result=None,
    time_steps: int = 150,
    fault_config: Dict = None,
    seed: int = 42,
) -> SimulationTrajectory:
    """
    便捷函数：运行一次仿真
    
    Args:
        scenario: 场景对象/字典
        algorithm_result: 算法结果
        time_steps: 仿真步数
        fault_config: 故障配置
        seed: 随机种子
        
    Returns:
        仿真轨迹
    """
    sim = SchedulingSimulator(scenario, algorithm_result, seed=seed)
    if fault_config:
        sim.inject_fault(fault_config)
    
    return sim.simulate(time_steps=time_steps)
