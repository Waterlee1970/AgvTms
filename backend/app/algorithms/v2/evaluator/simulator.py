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
class TimeStepSnapshot:
    """某一时刻的全局状态快照"""
    step: int
    simulation_time: float
    agvs: List[Dict[str, Any]]
    tasks: List[Dict[str, Any]]
    conveyors: List[Dict[str, Any]]
    active_faults: List[Dict[str, Any]]
    metrics: Dict[str, float]

    def to_dict(self):
        return {
            "step": self.step,
            "simulation_time": round(self.simulation_time, 2),
            "agvs": self.agvs,
            "tasks": self.tasks,
            "conveyors": self.conveyors,
            "active_faults": self.active_faults,
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
        self.adjacency: Dict[str, List[Tuple[str, float]]] = {}

        for e in scenario.edges:
            eid = e.get("id", f"{e['from']}_{e['to']}")
            self.edge_map[eid] = e
            w = e.get("weight", 1.0)
            self.adjacency.setdefault(e["from"], []).append((e["to"], w))
            self.adjacency.setdefault(e["to"], []).append((e["from"], w))

        # 故障配置
        self.fault_config: Dict[str, Any] = {"enabled": False}
        self.active_faults: List[Dict] = []

        # 内部状态
        self._assignment_map: Dict[str, str] = {}  # task_id -> agv_id
        self._agv_paths: Dict[str, List[str]] = {}  # agv_id -> path nodes
        self._task_pickup_done: Set[str] = set()
        self._task_complete_set: Set[str] = set()

        # 从算法结果初始化分配
        self._init_from_algorithm_result()

    def _init_from_algorithm_result(self):
        """从算法结果中提取任务-AGV分配关系"""
        if not self.algorithm_result:
            # 默认贪心分配：按顺序分配可用AGV给待处理任务
            agv_ids = [a["id"] for a in self.scenario.agvs if a.get("status") != "fault"]
            for i, task in enumerate(self.scenario.tasks):
                if task.get("status") != "completed" and agv_ids:
                    self._assignment_map[task["id"]] = agv_ids[i % len(agv_ids)]
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
        total_tasks = len(self.scenario.tasks)
        total_distance = 0.0

        # 为每个AGV规划路径（基于分配的任务）
        self._plan_all_paths(agv_states, task_states)

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
                    # 沿着路径移动
                    speed_factor = agv_speed * time_per_step
                    state["progress"] += speed_factor * 0.05  # 缩放因子
                    
                    if state["progress"] >= 1.0:
                        # 到达下一个节点
                        state["progress"] = 0.0
                        if len(state["path"]) > 1:
                            next_node = state["path"][1]
                            state["path"] = state["path"][1:]
                            state["node_id"] = next_node
                            state["position"] = self._get_node_pos(next_node)
                            total_distance += 1.0
                            
                            # 检查是否到达取货点
                            tid = state.get("current_task_id")
                            if tid and tid in task_states:
                                tstate = task_states[tid]
                                if tstate["status"] == "assigned" and state["node_id"] == tstate["pickup_node_id"]:
                                    tstate["status"] = "in_progress"
                                    tstate["start_step"] = step
                                    state["state"] = "executing"  # 取货中，短暂停顿
                                    ev = SimulationEvent(
                                        time_step=step,
                                        event_type="task_started",
                                        entity_id=tid,
                                        description=f"AGV-{aid} 开始执行任务 {tid}",
                                        position=state["position"],
                                    )
                                    events.append(ev)
                                    current_events.append(ev.to_dict())
                                    
                                elif tstate["status"] == "in_progress" and state["node_id"] == tstate["dropoff_node_id"]:
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
                            # 路径走完但还没到达目标（可能是等待）
                            state["state"] = "idle"

                elif state["state"] == "executing":
                    # 模拟装卸货耗时
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
                    # 尝试获取新任务
                    if not state.get("current_task_id"):
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

            # 生成快照
            snapshot = TimeStepSnapshot(
                step=step,
                simulation_time=sim_time,
                agvs=[AgvSnapshot(**s).to_dict() for s in agv_states.values()],
                tasks=[TaskSnapshot(**t).to_dict() for t in task_states.values()],
                conveyors=[ConveyorSnapshot(**c).to_dict() for c in conv_states.values()],
                active_faults=active_fault_list,
                metrics=step_metrics,
            )
            snapshots.append(snapshot)

        # 生成汇总
        algo_name = "unknown"
        if self.algorithm_result:
            algo_name = getattr(self.algorithm_result, 'algorithm_name', 'unknown')

        summary = {
            "total_simulation_time": time_steps * time_per_step,
            "total_tasks": total_tasks,
            "completed_tasks": completed_count,
            "completion_rate": round(completed_count / total_tasks * 100, 1) if total_tasks > 0 else 0,
            "total_distance": round(total_distance, 1),
            "total_events": len(events),
            "fault_injected": self.fault_config.get("enabled", False),
            "peak_active_agvs": max((s.metrics.get("active_agvs", 0) for s in snapshots), default=0),
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

    def _calculate_path(self, from_node: str, to_node: str) -> List[str]:
        """BFS最短路径计算"""
        if from_node == to_node:
            return [from_node]

        visited = {from_node}
        queue = [(from_node, [from_node])]

        while queue:
            current, path = queue.pop(0)
            for neighbor, _ in self.adjacency.get(current, []):
                if neighbor not in visited:
                    new_path = path + [neighbor]
                    if neighbor == to_node:
                        return new_path
                    visited.add(neighbor)
                    queue.append((neighbor, new_path))

        # 无法到达，返回直连
        return [from_node, to_node]

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
