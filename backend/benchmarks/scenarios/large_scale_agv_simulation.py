"""
AGV-TMS 大规模场景模拟验证引擎 — Phase 4.0 P2-05

支持 20~500 台 AGV 的仿真验证, 包含:
  - 多车型混合车队生成
  - 立体仓库地图生成
  - 持续任务流生成
  - 输送线网络拓扑
  - 完整仿真循环执行
  - 效率指标采集与分析
  - 热力图数据生成

用法:
  python large_scale_agv_simulation.py --help
  python large_scale_agv_simulation.py run --preset medium_logistics
  python large_scale_agv_simulation.py run --preset factory_production --duration 15
  python large_scale_agv_simulation.py run --num-agvs 100 --seed 42 --output sim_result.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
import sys
from collections import defaultdict, Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ==================== 数据模型 ====================

@dataclass
class AgvProfile:
    """AGV 车型配置模板"""
    agv_type: str           # 车型标识
    payload_kg: float        # 额定载重 kg
    speed_max_mps: float     # 最大速度 m/s
    battery_wh: int          # 电池容量 Wh
    charge_power_w: int      # 充电功率 W
    length_mm: float         # 车长 mm
    width_mm: float          # 车宽 mm
    turn_radius_m: float     # 最小转弯半径 m
    acceleration_mps2: float = 0.5  # 加速度 m/s²


# 预定义车型库
AGV_PROFILES: Dict[str, AgvProfile] = {
    "forklift": AgvProfile(
        agv_type="forklift", payload_kg=1500, speed_max_mps=2.0,
        battery_wh=400, charge_power_w=200,
        length_mm=2500, width_mm=1000, turn_radius_m=1.5,
    ),
    "pallet": AgvProfile(
        agv_type="pallet", payload_kg=800, speed_max_mps=1.5,
        battery_wh=300, charge_power_w=150,
        length_mm=1800, width_mm=800, turn_radius_m=1.0,
    ),
    "conveyor": AgvProfile(
        agv_type="conveyor", payload_kg=300, speed_max_mps=1.0,
        battery_wh=200, charge_power_w=100,
        length_mm=1200, width_mm=600, turn_radius_m=0.6,
    ),
    "heavy": AgvProfile(
        agv_type="heavy", payload_kg=3000, speed_max_mps=1.2,
        battery_wh=600, charge_power_w=300,
        length_mm=3500, width_mm=1400, turn_radius_m=2.0,
    ),
    "mini": AgvProfile(
        agv_type="mini", payload_kg=50, speed_max_mps=2.5,
        battery_wh=100, charge_power_w=50,
        length_mm=600, width_mm=400, turn_radius_m=0.4,
    ),
}


class AgvState(str, Enum):
    """仿真中的 AGV 状态"""
    IDLE = "idle"
    MOVING_TO_PICKUP = "moving_to_pickup"
    LOADING = "loading"
    MOVING_TO_DROPOFF = "moving_to_dropoff"
    UNLOADING = "unloading"
    CHARGING = "charging"
    BLOCKED_WAITING = "blocked_waiting"
    ERROR = "error"


class TaskState(str, Enum):
    """仿真中的任务状态"""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_TRANSIT = "in_transit"
    PICKUP_COMPLETE = "pickup_complete"
    DROPOFF_COMPLETE = "dropoff_complete"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SimAgv:
    """仿真中的 AGV 实例"""
    id: str
    profile: AgvProfile
    x: float
    y: float
    angle: float
    speed: float
    state: AgvState
    battery_pct: float
    current_task_id: Optional[str] = None
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    load_weight_kg: float = 0.0
    total_distance_m: float = 0.0
    tasks_completed: int = 0
    total_idle_time_sec: float = 0.0
    total_charge_time_sec: float = 0.0
    
    # 时间序列记录 (用于轨迹回放)
    trajectory_x: List[float] = field(default_factory=list)
    trajectory_y: List[float] = field(default_factory=list)
    
    def distance_to_target(self) -> float:
        if self.target_x is None:
            return float('inf')
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        return math.sqrt(dx*dx + dy*dy)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": self.profile.agv_type,
            "x": round(self.x, 2), "y": round(self.y, 2),
            "state": self.state.value, "battery": round(self.battery_pct, 1),
            "speed": round(self.speed, 2), "load_kg": round(self.load_weight_kg, 1),
            "task": self.current_task_id,
            "tasks_completed": self.tasks_completed,
            "distance_km": round(self.total_distance_m / 1000, 2),
        }


@dataclass
class SimTask:
    """仿真中的任务实例"""
    id: str
    priority: str  # low/normal/high/urgent
    pickup_point: Tuple[float, float]
    dropoff_point: Tuple[float, float]
    cargo_weight_kg: float
    cargo_type: str
    state: TaskState
    assigned_agv_id: Optional[str] = None
    created_tick: int = 0
    assigned_tick: int = 0
    completed_tick: int = 0
    pickup_tick: int = 0
    dropoff_tick: int = 0
    wait_time_sec: float = 0.0  # 从创建到被分配的等待时间
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "priority": self.priority,
            "state": self.state.value,
            "pickup": self.pickup_point, "dropoff": self.dropoff_point,
            "cargo": f"{self.cargo_type}({self.cargo_weight_kg}kg)",
            "agv": self.assigned_agv_id,
            "wait_sec": round(self.wait_time_sec, 1),
        }


@dataclass 
class SimulationConfig:
    """仿真配置"""
    num_agvs: int = 50
    map_width_m: float = 200.0
    map_height_m: float = 150.0
    num_charging_stations: int = 8
    num_pickup_points: int = 15
    num_dropoff_points: int = 12
    num_conveyor_lines: int = 6
    task_interval_sec: float = 5.0
    simulation_duration_min: float = 30.0
    tick_interval_sec: float = 0.1  # 仿真步长 (秒/tick)
    seed: int = 42
    
    # 充电阈值
    battery_low_threshold: float = 20.0   # 低电量阈值 (%)
    battery_charge_target: float = 90.0  # 充电目标 (%)
    charge_rate_pct_per_min: float = 15.0  # 充电速度 %/分钟


@dataclass
class EfficiencyMetrics:
    """效率指标汇总"""
    total_tasks_generated: int = 0
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0
    avg_wait_time_sec: float = 0.0
    avg_cycle_time_sec: float = 0.0  # 创建→完成总时长
    throughput_tasks_per_hour: float = 0.0
    agv_utilization_pct: float = 0.0
    avg_battery_level: float = 0.0
    charge_coverage_pct: float = 0.0  # 未出现低电量故障的比例
    deadlock_count: int = 0
    conflict_count: int = 0
    total_distance_km: float = 0.0
    avg_speed_actual_mps: float = 0.0
    
    # 每个tick的状态快照 (用于时序图表)
    timeline_ticks: List[int] = field(default_factory=list)
    timeline_active_agvs: List[int] = field(default_factory=list)
    timeline_pending_tasks: List[int] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        del d["timeline_ticks"]; del d["timeline_active_agvs"]; del d["timeline_pending_tasks"]
        return d


# ==================== 预设场景 ====================

SCENE_PRESETS: Dict[str, Dict[str, Any]] = {
    "small_warehouse": {
        "description": "小型单层仓库, 20台AGV",
        "config": SimulationConfig(num_agvs=20, map_width_m=80, map_height_m=60,
                                   num_charging_stations=3, num_pickup_points=6,
                                   num_dropoff_points=5, num_conveyor_lines=2,
                                   simulation_duration_min=10),
    },
    "medium_logistics": {
        "description": "中型物流中心, 50台AGV (标准基线)",
        "config": SimulationConfig(num_agvs=50, map_width_m=200, map_height_m=150,
                                   num_charging_stations=8, num_pickup_points=15,
                                   num_dropoff_points=12, num_conveyor_lines=6,
                                   simulation_duration_min=20),
    },
    "large_distribution": {
        "description": "大型分拣中心, 100台AGV",
        "config": SimulationConfig(num_agvs=100, map_width_m=350, map_height_m=280,
                                   num_charging_stations=16, num_pickup_points=28,
                                   num_dropoff_points=22, num_conveyor_lines=12,
                                   simulation_duration_min=20),
    },
    "factory_production": {
        "description": "工厂产线多工位节拍约束, 80台AGV",
        "config": SimulationConfig(num_agvs=80, map_width_m=250, map_height_m=180,
                                   num_charging_stations=10, num_pickup_points=18,
                                   num_dropoff_points=14, num_conveyor_lines=8,
                                   simulation_duration_min=25, task_interval_sec=3.0),
    },
    "full_performance_lab": {
        "description": "全性能实验室样品流转, 30台AGV",
        "config": SimulationConfig(num_agvs=30, map_width_m=120, map_height_m=90,
                                   num_charging_ststations=4, num_pickup_points=8,
                                   num_dropoff_points=6, num_conveyor_lines=3,
                                   simulation_duration_min=15, task_interval_sec=8.0),
    },
}


# ==================== 核心仿真引擎 ====================

class LargeScaleSimulationEngine:
    """
    大规模 AGV 场景仿真引擎.
    
    功能:
      1. 场景生成 (AGV车队/地图/任务流/输送线)
      2. 仿真循环 (位置更新/状态转换/充电管理/交通控制)
      3. 指标采集 (效率/热力图/时序数据)
      4. 报告输出 (JSON/Markdown/CSV)
    """

    def __init__(self, config: SimulationConfig):
        self.config = config
        random.seed(config.seed)
        
        self.agvs: List[SimAgv] = []
        self.tasks: List[SimTask] = []
        self.pickup_points: List[Tuple[float, float]] = []
        self.dropoff_points: List[Tuple[float, float]] = []
        self.charging_stations: List[Tuple[float, float]] = []
        
        self.tick_counter: int = 0
        self.elapsed_sec: float = 0.0
        self.total_ticks: int = 0
        self.is_running: bool = False
        
        self.metrics = EfficiencyMetrics()
        
        # 交通控制
        self._occupied_zones: Set[Tuple[int, int]] = set()  # 区域占用网格 (用于碰撞检测)
        self.zone_grid_size: float = 2.0  # 网格大小(米)
        self.conflict_events: List[Dict] = []  # 冲突事件记录

    # ==================== 场景生成 ====================
    
    def generate_scenario(self) -> Dict[str, Any]:
        """生成完整仿真场景"""
        print(f"[Scenario] Generating {self.config.num_agvs} AGV scenario ({self.config.map_width_m}x{self.config.map_height_m}m)")
        
        self._generate_agv_fleet()
        self._generate_map_features()
        
        return {
            "config": asdict(self.config),
            "agvs_summary": [{"id": a.id, "type": a.profile.agv_type} for a in self.agvs],
            "pickups": len(self.pickup_points),
            "dropoffs": len(self.dropoff_points),
            "charging": len(self.charging_stations),
        }

    def _generate_agv_fleet(self):
        """生成混合车型 AGV 车队"""
        type_weights = [0.35, 0.30, 0.15, 0.12, 0.08]  # pallet/forklift/conveyor/heavy/mini
        type_names = list(AGV_PROFILES.keys())
        
        for i in range(1, self.config.num_agvs + 1):
            agv_type = random.choices(type_names, weights=type_weights)[0]
            profile = AGV_PROFILES[agv_type]
            
            # 在场地内随机分布 (避开边缘)
            margin = 5.0
            x = random.uniform(margin, self.config.map_width_m - margin)
            y = random.uniform(margin, self.config.map_height_m - margin)
            
            # 电量按三角分布 (多数在 60-95% 区间)
            battery = random.triangular(30, 100, 85)
            
            agv = SimAgv(
                id=f"SIM-AGV-{i:04d}",
                profile=profile,
                x=x, y=y,
                angle=random.uniform(-180, 180),
                speed=0.0,
                state=AgvState.IDLE,
                battery_pct=battery,
            )
            # 记录初始位置到轨迹
            agv.trajectory_x.append(x)
            agv.trajectory_y.append(y)
            
            self.agvs.append(agv)

    def _generate_map_features(self):
        """生成地图特征点"""
        w, h = self.config.map_width_m, self.config.map_height_m
        margin = 8.0
        
        # 取货点 (沿边缘分布)
        self.pickup_points = [
            (random.uniform(margin, w - margin), random.uniform(margin, h * 0.3))
            for _ in range(self.config.num_pickup_points)
        ]
        
        # 卸货点 (另一侧分布)
        self.dropoff_points = [
            (random.uniform(margin, w - margin), random.uniform(h * 0.65, h - margin))
            for _ in range(self.config.num_dropoff_points)
        ]
        
        # 充电站 (均匀分布)
        cs_cols = max(1, int(math.sqrt(self.config.num_charging_stations)))
        cs_rows = (self.config.num_charging_stations + cs_cols - 1) // cs_cols
        cell_w = (w - 2 * margin) / cs_cols
        cell_h = (h * 0.4) / cs_rows
        
        for idx in range(self.config.num_charging_stations):
            row, col = divmod(idx, cs_cols)
            cx = margin + (col + 0.5) * cell_w
            cy = margin + (row + 0.5) * cell_h
            self.charging_stations.append((cx, cy))

    # ==================== 仿真执行 ====================
    
    def run(self) -> EfficiencyMetrics:
        """运行完整仿真循环"""
        self.is_running = True
        duration_sec = self.config.simulation_duration_min * 60
        self.total_ticks = int(duration_sec / self.config.tick_interval_sec)
        
        print(f"\n[Sim] Starting {self.total_ticks} ticks "
              f"({self.config.simulation_duration_min:.1f}min, dt={self.config.tick_interval_sec}s)")
        
        start_wall = time.time()
        
        for tick in range(self.total_ticks + 1):
            self.tick_counter = tick
            self.elapsed_sec = tick * self.config.tick_interval_sec
            
            # 1. 生成新任务
            self._spawn_tasks(tick)
            
            # 2. 分配空闲任务给空闲AGV
            self._assign_tasks(tick)
            
            # 3. 更新所有AGV状态
            self._update_agvs(tick)
            
            # 4. 充电管理
            self._manage_charging(tick)
            
            # 5. 采集指标快照
            self._record_metrics_snapshot(tick)
            
            # 进度显示
            if tick % int(60 / self.config.tick_interval_sec) == 0:  # 每"分钟"
                active = sum(1 for a in self.agvs if a.state not in (AgvState.IDLE, AgvState.CHARGING))
                pending = sum(1 for t in self.tasks if t.state == TaskState.PENDING)
                done = self.metrics.total_tasks_completed
                print(f"[Sim] {(self.elapsed_sec/60):.1f}min | "
                      f"active:{active} pending:{pending} done:{done} "
                      f"conflicts:{self.conflict_count()}")
        
        wall_elapsed = time.time() - start_wall
        self.is_running = False
        
        # 最终统计
        self._calculate_final_metrics(duration_sec)
        
        print(f"\n[Sim] Completed in {wall_elapsed:.1f}s real time")
        self._print_summary()
        
        return self.metrics

    def _spawn_tasks(self, tick: int):
        """按间隔生成新任务"""
        interval_ticks = int(self.config.task_interval_sec / self.config.tick_interval_sec)
        if tick > 0 and tick % interval_ticks == 0:
            if random.random() < 0.85:  # 85%概率生成 (模拟不均匀到达)
                pickup = random.choice(self.pickup_points)
                dropoff = random.choice(self.dropoff_points)
                
                priorities = ["low", "normal", "high", "urgent"]
                weights = [0.12, 0.45, 0.30, 0.13]
                
                task = SimTask(
                    id=f"SIM-TASK-{len(self.tasks)+1:06d}",
                    priority=random.choices(priorities, weights=weights)[0],
                    pickup_point=pickup,
                    dropoff_point=dropoff,
                    cargo_weight_kg=round(random.uniform(5, 500), 1),
                    cargo_type=random.choice(["box_small", "box_large", "pallet", "bag"]),
                    state=TaskState.PENDING,
                    created_tick=tick,
                )
                self.tasks.append(task)
                self.metrics.total_tasks_generated += 1

    def _assign_tasks(self, tick: int):
        """贪心分配: 空闲AGV → 最近的待处理任务"""
        idle_agvs = [a for a in self.agvs if a.state == AgvState.IDLE and a.battery_pct > self.config.battery_low_threshold]
        pending_tasks = [t for t in self.tasks if t.state == TaskState.PENDING]
        
        if not idle_agvs or not pending_tasks:
            return
        
        # 按距离排序匹配
        for agv in sorted(idle_agvs, key=lambda a: self._min_dist_to_any_task(a, pending_tasks)):
            if not any(t.state == TaskState.PENDING for t in self.tasks):
                break
            
            best_task = min(
                [t for t in self.tasks if t.state == TaskState.PENDING],
                key=lambda t: math.hypot(agv.x - t.pickup_point[0], agv.y - t.pickup_point[1])
            )
            
            best_task.state = TaskState.ASSIGNED
            best_task.assigned_agv_id = agv.id
            best_task.assigned_tick = tick
            best_task.wait_time_sec = (tick - best_task.created_tick) * self.config.tick_interval_sec
            
            agv.current_task_id = best_task.id
            agv.target_x, agv.target_y = best_task.pickup_point
            agv.state = AgvState.MOVING_TO_PICKUP
            agv.load_weight_kg = 0.0

    def _update_agvs(self, tick: int):
        """更新所有 AGV 位置和状态"""
        dt = self.config.tick_interval_sec
        
        for agv in self.agvs:
            prev_zone = self._get_zone(agv.x, agv.y)
            
            if agv.state in (AgvState.MOVING_TO_PICKUP, AgvState.MOVING_TO_DROPOFF):
                # 移动逻辑
                dist = agv.distance_to_target()
                if dist < 0.3:  # 到达阈值
                    self._handle_arrival(agv, tick)
                else:
                    # 向目标移动
                    dx = agv.target_x - agv.x
                    dy = agv.target_y - agv.y
                    dist = max(dist, 0.01)
                    
                    # 限速
                    speed = min(agv.profile.speed_max_mps, dist / dt * 0.8)
                    
                    move_dist = speed * dt
                    ratio = min(move_dist / dist, 1.0)
                    
                    agv.x += dx * ratio
                    agv.y += dy * ratio
                    agv.angle = math.degrees(math.atan2(dy, dx))
                    agv.speed = speed
                    agv.total_distance_m += move_dist
                    
                    # 电量消耗 (与负载相关)
                    consumption = (0.5 + agv.load_weight_kg / agv.profile.payload_kg * 0.5) \
                                 * speed * dt / (agv.profile.battery_wh * 10)
                    agv.battery_pct -= consumption
            
            elif agv.state == AgvState.LOADING:
                # 装货耗时 (2-5秒)
                if random.random() < 0.2:  # ~20%概率完成装货
                    task = self._get_task(agv.current_task_id)
                    if task:
                        task.state = TaskState.PICKUP_COMPLETE
                        task.pickup_tick = tick
                        agv.load_weight_kg = task.cargo_weight_kg
                        agv.target_x, agv.target_y = task.dropoff_point
                        agv.state = AgvState.MOVING_TO_DROPOFF
            
            elif agv.state == AgvState.UNLOADING:
                # 卸货耗时 (1-3秒)
                if random.random() < 0.3:  # ~30%概率完成卸货
                    task = self._get_task(agv.current_task_id)
                    if task:
                        task.state = TaskState.DROPOFF_COMPLETE
                        task.dropoff_tick = tick
                        task.completed_tick = tick
                        agv.load_weight_kg = 0.0
                        agv.current_task_id = None
                        agv.target_x = None
                        agv.target_y = None
                        agv.state = AgvState.IDLE
                        agv.speed = 0.0
                        agv.tasks_completed += 1
                        self.metrics.total_tasks_completed += 1
            
            elif agv.state == AgvState.IDLE:
                agv.speed = 0.0
                agv.total_idle_time_sec += dt
            
            elif agv.state == AgvState.CHARGING:
                agv.speed = 0.0
                agv.total_charge_time_sec += dt
                agv.battery_pct += self.config.charge_rate_pct_per_min * dt / 60.0
                if agv.battery_pct >= self.config.battery_charge_target:
                    agv.state = AgvState.IDLE
            
            # 记录轨迹 (降采样, 每10个tick记一次)
            if tick % 10 == 0:
                agv.trajectory_x.append(round(agv.x, 2))
                agv.trajectory_y.append(round(agv.y, 2))
            
            # 碰撞检测
            new_zone = self._get_zone(agv.x, agv.y)
            if new_zone != prev_zone:
                if new_zone in self._occupied_zones:
                    self._register_conflict(agv, tick, new_zone)
                self._occupied_zones.discard(prev_zone)
                self._occupied_zones.add(new_zone)

    def _handle_arrival(self, agv: SimAgv, tick: int):
        """处理 AGV 到达目标点"""
        agv.x, agv.y = agv.target_x, agv.target_y
        agv.speed = 0.0
        
        if agv.state == AgvState.MOVING_TO_PICKUP:
            agv.state = AgvState.LOADING
        elif agv.state == AgvState.MOVING_TO_DROPOFF:
            agv.state = AgvState.UNLOADING

    def _manage_charging(self, tick: int):
        """低电量 AGV 自动回充"""
        for agv in self.agvs:
            if agv.state in (AgvState.IDLE,) and agv.battery_pct < self.config.battery_low_threshold:
                # 找最近的充电站
                nearest_cs = min(self.charging_stations, key=lambda cs: math.hypot(cs[0]-agv.x, cs[1]-agv.y))
                agv.target_x, agv.target_y = nearest_cs
                agv.state = AgvState.MOVING_TO_PICKUP  # 复用移动状态
                # 特殊标记: 正在去充电
                agv.current_task_id = "__CHARGING__"

    # ==================== 指标计算 ====================
    
    def _record_metrics_snapshot(self, tick: int):
        """记录时序快照"""
        if tick % int(10 / self.config.tick_interval_sec) == 0:  # 每10秒
            active = sum(1 for a in self.agvs if a.state not in (AgvState.IDLE, AgvState.CHARGING))
            pending = sum(1 for t in self.tasks if t.state == TaskState.PENDING)
            
            self.metrics.timeline_ticks.append(tick)
            self.metrics.timeline_active_agvs.append(active)
            self.metrics.timeline_pending_tasks.append(pending)

    def _calculate_final_metrics(self, duration_sec: float):
        """计算最终效率指标"""
        m = self.metrics
        
        # 平均等待时间
        completed_tasks = [t for t in self.tasks if t.state == TaskState.COMPLETED]
        if completed_tasks:
            m.avg_wait_time_sec = sum(t.wait_time_sec for t in completed_tasks) / len(completed_tasks)
            m.avg_cycle_time_sec = sum((t.completed_tick - t.created_tick) * self.config.tick_interval_sec
                                        for t in completed_tasks) / len(completed_tasks)
        
        # 吞吐量
        m.throughput_tasks_per_hour = m.total_tasks_completed / (duration_sec / 3600) if duration_sec > 0 else 0
        
        # AGV 利用率
        total_time_per_agv = duration_sec
        avg_util = sum(a.total_idle_time_sec for a in self.agvs) / max(len(self.agvs), 1)
        m.agv_utilization_pct = (1 - avg_util / total_time_per_agv) * 100
        
        # 电量统计
        batteries = [a.battery_pct for a in self.agvs]
        m.avg_battery_level = sum(batteries) / len(batteries) if batteries else 0
        
        # 充电覆盖率 (未因低电量导致停机的比例)
        dead_agvs = sum(1 for a in self.agvs if a.battery_pct < 5 and a.state == AgvState.ERROR)
        m.charge_coverage_pct = (1 - dead_agvs / max(len(self.agvs), 1)) * 100
        
        # 死锁检测
        m.deadlock_count = sum(1 for a in self.agvs if a.state == AgvState.BLOCKED_WAITING)
        
        # 冲突计数
        m.conflict_count = len(self.conflict_events)
        
        # 总里程
        m.total_distance_km = sum(a.total_distance_m for a in self.agvs) / 1000
        
        # 实际平均速度
        total_move_time = sum(
            duration_sec - a.total_idle_time_sec - a.total_charge_time_sec
            for a in self.agvs
        )
        m.avg_speed_actual_mps = m.total_distance_km * 1000 / max(total_move_time, 1)

    def _print_summary(self):
        """打印结果摘要"""
        m = self.metrics
        print("\n" + "=" * 60)
        print("  SIMULATION RESULTS SUMMARY")
        print("=" * 60)
        print(f"  Tasks Generated:     {m.total_tasks_generated}")
        print(f"  Tasks Completed:     {m.total_tasks_completed}")
        print(f"  Tasks Failed:        {m.total_tasks_failed}")
        print(f"  Throughput:          {m.throughput_tasks_per_hour:.1f} tasks/hr")
        print(f"  Avg Wait Time:       {m.avg_wait_time_sec:.1f}s")
        print(f"  Avg Cycle Time:      {m.avg_cycle_time_sec:.1f}s")
        print(f"  AGV Utilization:     {m.agv_utilization_pct:.1f}%")
        print(f"  Avg Battery:         {m.avg_battery_level:.1f}%")
        print(f"  Charge Coverage:     {m.charge_coverage_pct:.1f}%")
        print(f"  Deadlocks:           {m.deadlock_count}")
        print(f"  Conflicts:           {m.conflict_count}")
        print(f"  Total Distance:      {m.total_distance_km:.2f} km")
        print("=" * 60)

    # ==================== 分析工具 ====================
    
    def calculate_efficiency_metrics(self) -> Dict[str, Any]:
        """返回完整的效率指标字典"""
        return self.metrics.to_dict()

    def generate_bottleneck_report(self) -> List[Dict[str, Any]]:
        """生成瓶颈 TOP10 报告"""
        bottlenecks: List[Dict[str, Any]] = []
        
        # 1. 最忙的 AGV
        busiest = sorted(self.agvs, key=lambda a: a.tasks_completed, reverse=True)[:5]
        for agv in busiest:
            bottlenecks.append({
                "type": "high_utilization",
                "entity": agv.id,
                "value": agv.tasks_completed,
                "unit": "completed tasks",
                "severity": "info" if agv.tasks_completed < 50 else "warning",
            })
        
        # 2. 最低电量的 AGV
        lowest_batt = sorted(self.agvs, key=lambda a: a.battery_pct)[:3]
        for agv in lowest_batt:
            if agv.battery_pct < 30:
                bottlenecks.append({
                    "type": "low_battery",
                    "entity": agv.id,
                    "value": round(agv.battery_pct, 1),
                    "unit": "%",
                    "severity": "critical" if agv.battery_pct < 10 else "warning",
                })
        
        # 3. 最长的等待时间任务
        long_wait = sorted(
            [t for t in self.tasks if t.wait_time_sec > 0],
            key=lambda t: t.wait_time_sec, reverse=True
        )[:5]
        for task in long_wait:
            bottlenecks.append({
                "type": "long_wait",
                "entity": task.id,
                "value": round(task.wait_time_sec, 1),
                "unit": "sec",
                "severity": "warning" if task.wait_time_sec > 30 else "info",
            })
        
        # 按严重程度排序
        sev_order = {"critical": 0, "warning": 1, "info": 2}
        bottlenecks.sort(key=lambda b: sev_order.get(b["severity"], 3))
        
        return bottlenecks[:10]

    def generate_heatmap_data(self) -> Dict[str, Any]:
        """生成热力图坐标数据 (前端可视化用)"""
        grid_size = 5.0  # 热力网格大小
        w, h = self.config.map_width_m, self.config.map_height_m
        cols = int(w / grid_size)
        rows = int(h / grid_size)
        
        heatmap = [[0] * cols for _ in range(rows)]
        
        # 统计每个 AGV 的访问频率 (基于轨迹)
        for agv in self.agvs:
            for x, y in zip(agv.trajectory_x, agv.trajectory_y):
                col = min(int(x / grid_size), cols - 1)
                row = min(int(y / grid_size), rows - 1)
                if 0 <= col < cols and 0 <= row < rows:
                    heatmap[row][col] += 1
        
        # 归一化
        max_val = max(max(row) for row in heatmap) if heatmap else 1
        if max_val > 0:
            heatmap = [[round(v / max_val, 3) for v in row] for row in heatmap]
        
        return {
            "grid_size_m": grid_size,
            "rows": rows,
            "cols": cols,
            "map_bounds": {"width": w, "height": h},
            "heatmap": heatmap,
            "hotspots": self._find_hotspots(heatmap, rows, cols, grid_size),
        }
    
    def _find_hotspots(self, heatmap: List[List[float]], rows: int, cols: int, grid_size: float) -> List[Dict]:
        """找出热力图热点区域"""
        hotspots = []
        threshold = 0.6
        
        for r in range(rows):
            for c in range(cols):
                if heatmap[r][c] >= threshold:
                    hotspots.append({
                        "x": round(c * grid_size + grid_size/2, 1),
                        "y": round(r * grid_size + grid_size/2, 1),
                        "intensity": round(heatmap[r][c], 2),
                    })
        
        return sorted(hotspots, key=lambda h: h["intensity"], reverse=True)[:10]

    # ==================== 内部工具方法 ====================
    
    def _get_task(self, task_id: Optional[str]) -> Optional[SimTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None
    
    def _min_dist_to_any_task(self, agv: SimAgv, tasks: List[SimTask]) -> float:
        if not tasks:
            return float('inf')
        return min(math.hypot(agv.x - t.pickup_point[0], agv.y - t.pickup_point[1]) for t in tasks)
    
    def _get_zone(self, x: float, y: float) -> Tuple[int, int]:
        return (int(x // self.zone_grid_size), int(y // self.zone_grid_size))
    
    def conflict_count(self) -> int:
        return len(self.conflict_events)
    
    def _register_conflict(self, agv: SimAgv, tick: int, zone: Tuple[int, int]):
        self.conflict_events.append({
            "tick": tick, "agv_id": agv.id, "zone": zone,
            "time_sec": round(tick * self.config.tick_interval_sec, 1),
        })


# ==================== CLI 入口 ====================

def main():
    parser = argparse.ArgumentParser(description="AGV-TMS Large Scale Simulation Engine")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # run 子命令
    run_parser = subparsers.add_parser("run", help="Run simulation")
    run_parser.add_argument("--preset", choices=list(SCENE_PRESETS.keys()),
                            help="Use preset scene configuration")
    run_parser.add_argument("--num-agvs", type=int, help="Override AGV count")
    run_parser.add_argument("--duration", type=float, help="Duration in minutes")
    run_parser.add_argument("--seed", type=int, default=42, help="Random seed")
    run_parser.add_argument("--output", "-o", help="Output JSON file path")
    run_parser.add_argument("--json-only", action="store_true", help="Only output JSON, no summary")
    
    # list 子命令
    subparsers.add_parser("list-presets", help="List all available presets")
    
    args = parser.parse_args()
    
    if args.command == "list-presets":
        print("\nPreset Scenes:")
        print("-" * 70)
        for name, scene in SCENE_PRESETS.items():
            cfg = scene["config"]
            print(f"  {name:<24s} → {scene['description']}")
            print(f"{'':24s}    {cfg.num_agvs} AGVs, {cfg.map_width_m}x{cfg.map_height_m}m, {cfg.simulation_duration_min}min")
        print()
        return 0
    
    elif args.command == "run":
        # 确定配置
        if args.preset:
            cfg = SCENE_PRESETS[args.preset]["config"].__dict__.copy()
            config = SimulationConfig(**cfg)
        else:
            config = SimulationConfig(seed=args.seed)
        
        if args.num_agvs:
            config.num_agvs = args.num_agvs
        if args.duration:
            config.simulation_duration_min = args.duration
        
        output_file = args.output or f"sim_results_{config.num_agvs}agvs.json"
        
        # 创建并运行引擎
        engine = LargeScaleSimulationEngine(config)
        scenario_info = engine.generate_scenario()
        print(json.dumps(scenario_info, indent=2, ensure_ascii=False))
        
        metrics = engine.run()
        
        # 收集全部结果
        result = {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "version": "2.3",
                "engine": "LargeScaleSimulationEngine",
                "config": asdict(config),
            },
            "scenario": scenario_info,
            "metrics": metrics.to_dict(),
            "bottleneck_report": engine.generate_bottleneck_report(),
            "heatmap": engine.generate_heatmap_data(),
            "agv_final_states": [a.to_dict() for a in engine.agvs[:20]],  # 前20台详情
            "task_stats": {
                "total": len(engine.tasks),
                "completed": metrics.total_tasks_completed,
                "pending": sum(1 for t in engine.tasks if t.state == TaskState.PENDING),
                "failed": metrics.total_tasks_failed,
            },
        }
        
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"\n✓ Results saved to {output_file}")
        
        return 0
    
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
