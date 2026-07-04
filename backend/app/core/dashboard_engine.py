"""
Digital Twin Dashboard & Analytics API
对标 Plant Mirror 3D+2D数据看板 + 调度可视化

功能:
1. 实时KPI指标看板 (OEE/吞吐量/利用率)
2. AGV状态分布统计
3. 热力图数据生成
4. 调度路径可视化数据
5. 历史趋势查询
6. 自定义图表配置

Author: Digital Twin Team
Date: 2026-07-05
"""

import asyncio
import json
import time
import math
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime, timedelta
from collections import defaultdict


# ══════════════════════════════════════════════════════════
# KPI 数据结构
# ══════════════════════════════════════════════════════════

@dataclass
class KpiMetric:
    """KPI 指标"""
    name: str                           # 指标名称
    value: float                        # 当前值
    unit: str = ""                      # 单位
    previous_value: float = None        # 上周期值 (用于计算变化)
    target: float = None                # 目标值
    
    # 趋势
    trend: str = "stable"               # up / down / stable
    change_pct: float = 0.0             # 变化百分比
    
    # 颜色编码 (基于目标达成)
    status: str = "normal"              # good / warning / critical
    
    # 分位数 (用于历史对比)
    p25: float = None
    p50: float = None                   # 中位数
    p75: float = None
    p90: float = None
    
    # 时间戳
    timestamp: float = field(default_factory=time.time)
    period: str = "realtime"            # realtime / hourly / daily
    
    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "value": round(self.value, 2),
            "unit": self.unit,
            "trend": self.trend,
            "change_pct": round(self.change_pct, 2),
            "status": self.status,
            "timestamp": self.timestamp,
            "period": self.period,
        }
        
        if self.previous_value is not None:
            result["previous_value"] = round(self.previous_value, 2)
        if self.target is not None:
            result["target"] = self.target
            result["achievement_pct"] = round(self.value / max(0.001, self.target) * 100, 1)
        
        if self.p25 is not None:
            result["percentiles"] = {
                "p25": round(self.p25, 2),
                "p50": round(self.p50, 2),
                "p75": round(self.p75, 2),
                "p90": round(self.p90, 2),
            }
        
        return result


@dataclass
class AgvStatusSummary:
    """AGV 状态汇总"""
    total: int = 0
    idle: int = 0
    moving: int = 0
    charging: int = 0
    loading: int = 0
    error: int = 0
    offline: int = 0
    blocked: int = 0
    unloading: int = 0
    
    # 计算字段
    utilization_rate: float = 0.0      # 利用率 (非idle比例)
    avg_battery: float = 0.0           # 平均电量
    avg_speed: float = 0.0             # 平均速度 m/s
    
    # 按类型分布
    by_type: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "total": self.total,
            **{k: v for k, v in [
                ("idle", self.idle), ("moving", self.moving), 
                ("charging", self.charging), ("loading", self.loading),
                ("error", self.error), ("offline", self.offline),
                ("blocked", self.blocked), ("unloading", self.unloading),
            ] if v > 0},
            "utilization_rate": round(self.utilization_rate, 2),
            "avg_battery": round(self.avg_battery, 1),
            "avg_speed": round(self.avg_speed, 2),
            "by_type": self.by_type,
        }


@dataclass
class HeatmapDataPoint:
    """热力图数据点"""
    x: float
    y: float
    value: float                       # 强度值 [0, 1]
    label: str = ""
    category: str = ""                 # congestion | traffic | energy | idle_time
    
    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "value": self.value, 
                "label": self.label, "category": self.category}


@dataclass
class ScheduleVisualizationData:
    """
    调度可视化数据
    
    用于前端绘制调度甘特图/路径动画。
    """
    schedule_id: str
    timestamp: float
    time_window_seconds: float = 3600   # 可视化时间窗口
    
    # 任务时间线
    task_timeline: List[Dict] = field(default_factory=list)
    # [{task_id, agv_id, start_time, end_time, pickup_node, dropoff_node, status}]
    
    # 路径段占用表
    path_segments: List[Dict] = field(default_factory=list)
    # [{segment_id, from_node, to_node, occupied_by, start_time, end_time}]
    
    # 冲突检测
    conflicts: List[Dict] = field(default_factory=list)
    # [{type, location, time, involved_agvs, severity}]
    
    # 效率指标
    makespan: float = 0.0              # 总完工时间
    total_distance_m: float = 0.0      # 总行驶距离
    avg_wait_time_s: float = 0.0       # 平均等待时间
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrendDataPoint:
    """时序趋势点"""
    timestamp: float
    value: float
    label: str = ""
    
    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "value": round(self.value, 2), "label": self.label}


# ══════════════════════════════════════════════════════════
# Dashboard 引擎
# ══════════════════════════════════════════════════════════

class DashboardEngine:
    """
    数字孪生仪表盘引擎
    
    功能:
    - 实时KPI计算与缓存
    - AGV状态聚合
    - 热力图数据生成
    - 历史趋势存储与查询
    - 调度可视化数据组装
    """
    
    # 历史数据保留时长
    TREND_HISTORY_HOURS = 24
    MAX_TREND_POINTS = 1440            # 每分钟一个点，24小时
    
    def __init__(self):
        # ── KPI 缓存 ──
        self._kpi_cache: Dict[str, KpiMetric] = {}
        self._kpi_history: Dict[str, List[TrendDataPoint]] = defaultdict(list)
        self._kpi_last_update: float = 0
        
        # ── AGV 状态缓存 ──
        self._agv_states: Dict[str, Dict] = {}
        self._agv_status_summary: AgvStatusSummary = AgvStatusSummary()
        
        # ── 热力图数据 ──
        self._heatmap_data: List[HeatmapDataPoint] = []
        self._heatmap_update_interval: float = 10.0  # 秒
        
        # ── 调度可视化 ──
        self._schedule_viz: Optional[ScheduleVisualizationData] = None
        
        # ── 数据源回调 ──
        self._agv_data_source: Optional[Callable[[], List[Dict]]] = None
        self._task_data_source: Optional[Callable[[], List[Dict]]] = None
        self._schedule_data_source: Optional[Callable[[], Dict]] = None
    
    # ═════════════════════════════════════════════════════
    # 数据源注入
    # ═════════════════════════════════════════════════════
    
    def set_agv_data_source(self, source: Callable[[], List[Dict]]):
        """设置AGV数据源 (从WebSocket广播器或Redis获取)"""
        self._agv_data_source = source
    
    def set_task_data_source(self, source: Callable[[], List[Dict]]):
        """设置任务数据源"""
        self._task_data_source = source
    
    def set_schedule_data_source(self, source: Callable[[], Dict]):
        """设置调度结果数据源"""
        self._schedule_data_source = source
    
    # ═════════════════════════════════════════════════════
    # KPI 计算
    # ═════════════════════════════════════════════════════
    
    async def calculate_kpis(self) -> Dict[str, KpiMetric]:
        """计算所有KPI指标"""
        now = time.time()
        
        # 刷新AGV数据
        await self._refresh_agv_data()
        
        summary = self._agv_status_summary
        
        kpis = {}
        
        # 1. OEE (设备综合效率) - 简化版本
        oee = self._calculate_oee(summary)
        kpis["oee"] = oee
        
        # 2. 吞吐量 (任务完成数/小时)
        throughput = await self._calculate_throughput()
        kpis["throughput"] = throughput
        
        # 3. AGV利用率
        utilization = KpiMetric(
            name="agv_utilization",
            value=summary.utilization_rate * 100,
            unit="%",
            target=85.0,
            status=self._calc_status(summary.utilization_rate * 100, target=85),
        )
        kpis["agv_utilization"] = utilization
        
        # 4. 平均完成时间
        avg_completion = await self._calculate_avg_completion_time()
        kpis["avg_completion_time"] = avg_completion
        
        # 5. 在线率
        online_rate = 100.0
        if summary.total > 0:
            online_rate = ((summary.total - summary.offline - summary.error) / summary.total) * 100
        online = KpiMetric(
            name="online_rate",
            value=online_rate,
            unit="%",
            target=98.0,
            status=self._calc_status(online_rate, target=98),
        )
        kpis["online_rate"] = online
        
        # 6. 平均电量
        battery = KpiMetric(
            name="avg_battery",
            value=summary.avg_battery,
            unit="%",
            target=60.0,
            status=self._calc_status(summary.avg_battery, target=60, higher_is_bad=False),
        )
        kpis["avg_battery"] = battery
        
        # 7. 错误率
        error_rate = 0.0
        if summary.total > 0:
            error_rate = (summary.error / summary.total) * 100
        errors = KpiMetric(
            name="error_rate",
            value=error_rate,
            unit="%",
            target=1.0,
            status=self._calc_status(error_rate, target=1, higher_is_bad=True),
        )
        kpis["error_rate"] = errors
        
        # 更新缓存并记录历史
        for name, kpi in kpis.items():
            old_kpi = self._kpi_cache.get(name)
            
            # 计算变化
            if old_kpi:
                kpi.previous_value = old_kpi.value
                if old_kpi.value != 0:
                    kpi.change_pct = ((kpi.value - old_kpi.value) / abs(old_kpi.value)) * 100
                kpi.trend = "up" if kpi.change_pct > 1 else "down" if kpi.change_pct < -1 else "stable"
            
            self._kpi_cache[name] = kpi
            
            # 记录历史 (每分钟一次)
            self._record_trend(name, kpi.value)
        
        self._kpi_last_update = now
        return kpis
    
    def _calculate_oee(self, summary: AgvStatusSummary) -> KpiMetric:
        """
        OEE = 可用性 × 性能 × 质量
        
        简化版本:
        - 可用性 = 在线AGV比例
        - 性能 = 工作中AGV比例 (相对于总在线)
        - 质量 = 无错误AGV比例
        """
        if summary.total == 0:
            return KpiMetric(name="oee", value=0, unit="%", target=85.0, status="warning")
        
        availability = (summary.total - summary.offline) / summary.total
        
        working_count = summary.moving + summary.loading + summary.unloading
        performance = working_count / max(1, summary.total - summary.offline - summary.charging)
        
        quality = (summary.total - summary.error) / summary.total
        
        oee_value = availability * performance * quality * 100
        
        return KpiMetric(
            name="oee",
            value=oee_value,
            unit="%",
            target=85.0,
            status=self._calc_status(oee_value, target=85),
        )
    
    async def _calculate_throughput(self) -> KpiMetric:
        """计算吞吐量 (完成的任务数/小时)"""
        completed_count = 0
        
        if self._task_data_source:
            try:
                tasks = self._task_data_source()
                completed_count = len([t for t in tasks if t.get("status") in ("completed", "done")])
            except Exception:
                pass
        
        return KpiMetric(
            name="throughput",
            value=float(completed_count),
            unit="tasks/h",
            target=100.0,
        )
    
    async def _calculate_avg_completion_time(self) -> KpiMetric:
        """计算平均任务完成时间"""
        times = []
        
        if self._task_data_source:
            try:
                tasks = self._task_data_source()
                for t in tasks:
                    if t.get("completed_at") and t.get("created_at"):
                        try:
                            start = float(t["created_at"])
                            end = float(t["completed_at"])
                            if end > start:
                                times.append((end - start) / 60)  # 分钟
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass
        
        avg = sum(times) / len(times) if times else 0.0
        
        return KpiMetric(
            name="avg_completion_time",
            value=avg,
            unit="min",
            target=10.0,
            status=self._calc_status(avg, target=10, higher_is_bad=True),
        )
    
    @staticmethod
    def _calc_status(value: float, target: float, higher_is_bad: bool = False) -> str:
        """根据值和目标计算状态"""
        if higher_is_bad:
            ratio = value / max(0.001, target)
            if ratio >= 2.0:
                return "critical"
            elif ratio >= 1.5:
                return "warning"
            else:
                return "good"
        else:
            pct = (value / max(0.001, target)) * 100
            if pct >= 95:
                return "good"
            elif pct >= 70:
                return "warning"
            else:
                return "critical"
    
    # ═════════════════════════════════════════════════════
    # AGV 状态管理
    # ═════════════════════════════════════════════════════
    
    async def _refresh_agv_data(self):
        """刷新AGV状态数据"""
        if not self._agv_data_source:
            return
        
        try:
            agv_list = self._agv_data_source()
            
            summary = AgvStatusSummary(total=len(agv_list))
            type_counts: Dict[str, int] = defaultdict(int)
            total_battery = 0.0
            total_speed = 0.0
            
            self._agv_states.clear()
            
            for agv in agv_list:
                agv_id = agv.get("id", "unknown")
                self._agv_states[agv_id] = agv
                
                status = agv.get("status", "unknown").lower()
                
                # 统计各状态数量
                if status == "idle":
                    summary.idle += 1
                elif status == "moving":
                    summary.moving += 1
                elif status == "charging":
                    summary.charging += 1
                elif status in ("loading", "executing"):
                    summary.loading += 1
                elif status == "unloading":
                    summary.unloading += 1
                elif status == "error":
                    summary.error += 1
                elif status == "offline":
                    summary.offline += 1
                elif status == "blocked":
                    summary.blocked += 1
                
                # 电量和速度累加
                battery = agv.get("battery_level", agv.get("battery", 100))
                speed = agv.get("speed", 0)
                total_battery += float(battery)
                total_speed += float(speed)
                
                # 类型统计
                vehicle_type = agv.get("vehicle_type", agv.get("type", "standard"))
                type_counts[vehicle_type] += 1
            
            # 计算派生指标
            active_count = summary.total - summary.idle - summary.offline - summary.charging
            summary.utilization_rate = active_count / max(1, summary.total)
            summary.avg_battery = total_battery / max(1, summary.total)
            summary.avg_speed = total_speed / max(1, summary.total)
            summary.by_type = dict(type_counts)
            
            self._agv_status_summary = summary
            
        except Exception as e:
            print(f"[Dashboard] Error refreshing AGV data: {e}")
    
    def get_agv_status_summary(self) -> AgvStatusSummary:
        """获取AGV状态汇总"""
        return self._agv_status_summary
    
    def get_agv_list(self) -> List[Dict]:
        """获取所有AGV详细状态"""
        return list(self._ag_states.values())
    
    def get_agv_detail(self, agv_id: str) -> Optional[Dict]:
        """获取单个AGV详情"""
        return self._agv_states.get(agv_id)
    
    # ═════════════════════════════════════════════════════
    # 热力图
    # ═════════════════════════════════════════════════════
    
    def generate_heatmap(self, category: str = "congestion") -> List[HeatmapDataPoint]:
        """
        生成热力图数据
        
        类别:
        - congestion: 拥堵程度 (基于AGV密度)
        - traffic: 交通流量 (基于路径使用频率)
        - energy: 能耗热点
        - idle_time: 空闲时间聚集区
        """
        points = []
        
        if category == "congestion":
            points = self._generate_congestion_heatmap()
        elif category == "traffic":
            points = self._generate_traffic_heatmap()
        elif category == "energy":
            points = self._generate_energy_heatmap()
        else:
            points = self._generate_congestion_heatmap()  # 默认
        
        self._heatmap_data = points
        return points
    
    def _generate_congestion_heatmap(self) -> List[HeatmapDataPoint]:
        """基于AGV位置生成拥堵热力图"""
        points = []
        
        # 使用网格聚合
        grid_size = 5.0  # 5米网格
        grid: Dict[Tuple[int, int], int] = defaultdict(int)
        
        for agv_id, agv in self._agv_states.items():
            x = agv.get("x", 0)
            y = agv.get("y", 0)
            
            gx = int(x / grid_size)
            gy = int(y / grid_size)
            grid[(gx, gy)] += 1
        
        # 找到最大值用于归一化
        max_count = max(grid.values()) if grid else 1
        
        for (gx, gy), count in grid.items():
            x = gx * grid_size + grid_size / 2
            y = gy * grid_size + grid_size / 2
            intensity = count / max(1, max_count)
            
            points.append(HeatmapDataPoint(
                x=x, y=y, value=intensity,
                label=f"{count} AGVs",
                category="congestion",
            ))
        
        return points
    
    def _generate_traffic_heatmap(self) -> List[HeatmapDataPoint]:
        """基于路径生成流量热力图"""
        # TODO: 从历史轨迹数据生成
        return [
            HeatmapDataPoint(x=10, y=20, value=0.7, label="High traffic", category="traffic"),
        ]
    
    def _generate_energy_heatmap(self) -> List[HeatmapDataPoint]:
        """生成能耗热力图 (充电桩附近高亮)"""
        points = []
        
        for agv_id, agv in self._agv_states.items():
            if agv.get("status") == "charging":
                points.append(HeatmapDataPoint(
                    x=agv.get("x", 0),
                    y=agv.get("y", 0),
                    value=0.9,
                    label=f"{agv_id} charging",
                    category="energy",
                ))
        
        return points
    
    # ═════════════════════════════════════════════════════
    # 调度可视化
    # ═════════════════════════════════════════════════════
    
    async def generate_schedule_visualization(self) -> Optional[ScheduleVisualizationData]:
        """生成调度可视化数据"""
        if not self._schedule_data_source:
            return None
        
        try:
            sched_data = self._schedule_data_source()
            
            viz = ScheduleVisualizationData(
                schedule_id=sched_data.get("id", f"sched_{int(time.time())}"),
                timestamp=time.time(),
            )
            
            # 解析任务时间线
            tasks = sched_data.get("assignments", sched_data.get("tasks", []))
            for task in tasks:
                if isinstance(task, dict):
                    viz.task_timeline.append({
                        "task_id": task.get("id", ""),
                        "agv_id": task.get("agv_id", task.get("vehicle_id", "")),
                        "start_time": task.get("start_time", task.get("assigned_at")),
                        "end_time": task.get("end_time", task.get("estimated_complete")),
                        "pickup_node": task.get("pickup", task.get("origin")),
                        "dropoff_node": task.get("dropoff", task.get("destination")),
                        "status": task.get("status", "pending"),
                    })
            
            # 解析路径占用
            for task in tasks:
                if isinstance(task, dict) and task.get("path"):
                    path = task["path"]
                    if isinstance(path, list) and len(path) >= 2:
                        for i in range(len(path) - 1):
                            viz.path_segments.append({
                                "segment_id": f"seg_{task.get('id', 'x')}_{i}",
                                "from_node": path[i],
                                "to_node": path[i+1],
                                "occupied_by": task.get("agv_id", task.get("vehicle_id", "")),
                                "start_time": task.get("start_time"),
                                "end_time": task.get("end_time"),
                            })
            
            # 统计指标
            if viz.task_timeline:
                times = [t.get("end_time", 0) - t.get("start_time", 0) 
                        for t in viz.task_timeline if t.get("end_time") and t.get("start_time")]
                if times:
                    viz.makespan = max(times)
                    viz.avg_wait_time_s = sum(times) / len(times)
            
            self._schedule_viz = viz
            return viz
            
        except Exception as e:
            print(f"[Dashboard] Error generating schedule visualization: {e}")
            return None
    
    # ═════════════════════════════════════════════════════
    # 历史趋势
    # ═════════════════════════════════════════════════════
    
    def _record_trend(self, metric_name: str, value: float):
        """记录一个趋势点"""
        history = self._kpi_history[metric_name]
        
        point = TrendDataPoint(timestamp=time.time(), value=value)
        history.append(point)
        
        # 限制长度
        while len(history) > self.MAX_TREND_POINTS:
            history.pop(0)
    
    def get_trend(self, metric_name: str, hours: float = 1.0) -> List[TrendDataPoint]:
        """获取指定指标的最近趋势"""
        history = self._kpi_history.get(metric_name, [])
        
        if not history:
            return []
        
        cutoff = time.time() - hours * 3600
        return [p for p in history if p.timestamp >= cutoff]
    
    def get_all_trends(self, hours: float = 1.0) -> Dict[str, List[TrendDataPoint]]:
        """获取所有指标的趋势"""
        return {name: self.get_trend(name, hours) for name in self._kpi_history}
    
    # ═════════════════════════════════════════════════════
    # 综合仪表盘数据
    # ═════════════════════════════════════════════════════
    
    async def get_full_dashboard(self) -> Dict[str, Any]:
        """获取完整仪表盘数据 (一次性调用)"""
        kpis = await self.calculate_kpis()
        
        return {
            "timestamp": time.time(),
            "kpis": {name: kpi.to_dict() for name, kpi in kpis.items()},
            "agv_summary": self._agv_status_summary.to_dict(),
            "agv_list": self.get_agv_list(),
            "heatmap": {
                "congestion": [p.to_dict() for p in self.generate_heatmap("congestion")],
                "energy": [p.to_dict() for p in self.generate_heatmap("energy")],
            },
            "schedule_viz": self._schedule_viz.to_dict() if self._schedule_viz else None,
        }


# ══════════════════════════════════════════════════════════
# FastAPI Router - Dashboard API
# ══════════════════════════════════════════════════════════

from fastapi import APIRouter, Query

dashboard_router = APIRouter(prefix="/api/v3/dashboard", tags=["Digital Twin Dashboard"])

# 全局单例
_dashboard: Optional[DashboardEngine] = None


def get_dashboard() -> DashboardEngine:
    global _dashboard
    if _dashboard is None:
        _dashboard = DashboardEngine()
    return _dashboard


@dashboard_router.get("/full")
async def get_full_dashboard():
    """
    获取完整仪表盘数据
    
    一次性返回所有看板数据：
    - KPI指标 (OEE/吞吐量/利用率等)
    - AGV状态汇总
    - 拥堵热力图
    - 调度可视化数据
    """
    dashboard = get_dashboard()
    return await dashboard.get_full_dashboard()


@dashboard_router.get("/kpis")
async def get_kpis():
    """获取所有KPI指标"""
    dashboard = get_dashboard()
    kpis = await dashboard.calculate_kpis()
    return {name: kpi.to_dict() for name, kpi in kpis.items()}


@dashboard_router.get("/kpi/{metric_name}")
async def get_kpi_detail(metric_name: str):
    """获取单个KPI详情 (含历史趋势)"""
    dashboard = get_dashboard()
    kpis = await dashboard.calculate_kpis()
    
    if metric_name not in kpis:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown KPI: {metric_name}")
    
    kpi = kpis[metric_name]
    trend = dashboard.get_trend(metric_name, hours=24)
    
    return {
        **kpi.to_dict(),
        "trend_24h": [t.to_dict() for t in trend[-144:]],  # 最近24小时 (每10分钟一点)
    }


@dashboard_router.get("/agv-summary")
async def get_agv_summary():
    """获取AGV状态汇总"""
    dashboard = get_dashboard()
    await dashboard._refresh_agv_data()
    return dashboard.get_agv_status_summary().to_dict()


@dashboard_router.get("/agv-list")
async def get_agv_list(
    status_filter: Optional[str] = Query(None, description="按状态过滤"),
    type_filter: Optional[str] = Query(None, description="按类型过滤"),
):
    """获取AGV列表 (支持过滤)"""
    dashboard = get_dashboard()
    agvs = dashboard.get_agv_list()
    
    if status_filter:
        agvs = [a for a in agvs if a.get("status", "").lower() == status_filter.lower()]
    if type_filter:
        agvs = [a for a in agvs if a.get("vehicle_type", a.get("type", "")).lower() == type_filter.lower()]
    
    return {
        "total": len(agvs),
        "items": agvs,
    }


@dashboard_router.get("/agv/{agv_id}")
async def get_agv_detail(agv_id: str):
    """获取单个AGV的详细信息"""
    dashboard = get_dashboard()
    agv = dashboard.get_agv_detail(agv_id)
    
    if not agv:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"AGV not found: {agv_id}")
    
    return agv


@dashboard_router.get("/heatmap")
async def get_heatmap(
    category: str = Query("congestion", description="congestion|traffic|energy|idle_time"),
):
    """获取热力图数据"""
    dashboard = get_dashboard()
    points = dashboard.generate_heatmap(category)
    return {
        "category": category,
        "points": [p.to_dict() for p in points],
        "count": len(points),
        "generated_at": time.time(),
    }


@dashboard_router.get("/schedule-viz")
async def get_schedule_visualization():
    """获取调度可视化数据 (甘特图/路径占用)"""
    dashboard = get_dashboard()
    viz = await dashboard.generate_schedule_visualization()
    
    if not viz:
        return {"message": "No schedule data available"}
    
    return viz.to_dict()


@dashboard_router.get("/trends")
async def get_trends(
    metrics: Optional[str] = Query(None, description="逗号分隔的指标名, 如 'oee,throughput'"),
    hours: float = Query(1.0, ge=0.1, le=168, description="时间范围(小时)"),
):
    """获取指标历史趋势"""
    dashboard = get_dashboard()
    
    if metrics:
        names = metrics.split(",")
        result = {name: [t.to_dict() for t in dashboard.get_trend(name.strip(), hours)] 
                  for name in names}
    else:
        all_trends = dashboard.get_all_trends(hours)
        result = {name: [t.to_dict() for t in trends[-60:]]  # 最近N个点
                  for name, trends in all_trends.items()}
    
    return result


# 导出
__all__ = [
    'DashboardEngine', 'KpiMetric', 'AgvStatusSummary', 'HeatmapDataPoint',
    'ScheduleVisualizationData', 'TrendDataPoint',
    'dashboard_router',
]
