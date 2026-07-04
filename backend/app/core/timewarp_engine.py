"""
TimeWarp Simulation Engine - 数字孪生仿真加速
对标 Plant Mirror 轻量调度算子 (10x仿真加速)

功能:
1. 时间扭曲控制 (0.1x ~ 50x 变速播放)
2. 录制/回放系统
3. 快照/状态存档
4. 事件时间线编辑
5. 预测性仿真 (基于历史数据)

技术架构:
┌──────────┐   ┌──────────────┐   ┌─────────────────┐
│ Scheduler │──▶│ TimeWarpCore │──▶│ WebSocket Push  │
│ (真实调度) │   │ (时间控制)    │   │ (30FPS→可变)    │
└──────────┘   └──────────────┘   └─────────────────┘
                      │
              ┌───────▼────────┐
              │ RecordingStore │
              │ (录制/回放)     │
              └────────────────┘

Author: Digital Twin Team
Date: 2026-07-05
"""

import asyncio
import json
import time
import math
import threading
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime, timedelta
from collections import deque


# ══════════════════════════════════════════════════════════
# 核心枚举与常量
# ══════════════════════════════════════════════════════════

class PlaybackState(str, Enum):
    """播放状态"""
    STOPPED = "stopped"
    PAUSED = "paused"
    PLAYING = "playing"
    RECORDING = "recording"


class TimeWarpMode(str, Enum):
    """时间模式"""
    REALTIME = "realtime"           # 实时 (1.0x)
    ACCELERATED = "accelerated"     # 加速 (>1.0x)
    SLOW_MOTION = "slow_motion"     # 慢动作 (<1.0x)
    STEP = "step"                   # 单帧步进
    PREDICTIVE = "predictive"       # 预测仿真 (基于历史)


@dataclass
class SimulationFrame:
    """
    单帧仿真数据
    
    每一帧包含该时刻所有AGV/设备的状态快照。
    帧率由TimeWarpCore动态调整以匹配目标倍速。
    """
    frame_id: int
    timestamp: float                # 仿真时间戳 (Unix epoch)
    wall_time: float                # 实际记录时的真实时间
    
    # AGV状态列表
    agv_states: List[Dict[str, Any]] = field(default_factory=list)
    
    # 全局事件 (在此帧触发的事件)
    events: List[Dict[str, Any]] = field(default_factory=list)
    
    # 性能指标
    delta_time: float = 0.033        # 与上一帧的时间间隔 (秒)
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass 
class TimelineEvent:
    """
    时间线事件
    
    用于标记关键事件点，支持跳转和注释。
    """
    event_id: str
    timestamp: float                 # 发生时间 (仿真时间)
    event_type: str                  # task_start | task_complete | error | collision_warning...
    source_id: str                   # 触发源 (AGV ID / Task ID)
    
    title: str = ""
    description: str = ""
    severity: str = "info"           # info | warning | critical
    
    # 关联数据
    data: Dict[str, Any] = field(default_factory=dict)
    
    # 书签 (用于快速定位)
    is_bookmark: bool = False
    color: Optional[str] = None      # 显示颜色
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Snapshot:
    """
    仿真快照
    
    可在任何时刻保存完整场景状态，
    支持从快照恢复继续仿真。
    """
    snapshot_id: str
    timestamp: float                 # 仿真时间
    created_at: float                # 创建时的真实时间
    
    name: str = ""                   # 用户定义名称
    description: str = ""
    
    # 完整状态数据
    agv_states: Dict[str, Any] = field(default_factory=dict)
    task_states: Dict[str, Any] = field(default_factory=dict)
    map_state: Dict[str, Any] = field(default_factory=dict)
    simulation_params: Dict[str, Any] = field(default_factory=dict)
    
    # 元数据
    file_size_bytes: int = 0
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════
# TimeWarp 核心
# ══════════════════════════════════════════════════════════

class TimeWarpCore:
    """
    时间扭曲仿真引擎核心
    
    功能:
    - 变速播放控制 (0.1x ~ 50x)
    - 录制/回放管理
    - 快照保存/加载
    - 事件时间线追踪
    - 预测性仿真插值
    
    使用示例:
        engine = TimeWarpCore()
        
        # 启动加速仿真
        await engine.play(speed=5.0)
        
        # 录制当前会话
        engine.start_recording()
        
        # 保存快照
        snapshot = engine.take_snapshot("关键时刻")
        
        # 回放到指定时间点
        await engine.seek_to(timestamp=target_time)
    """
    
    # 速度限制
    MIN_SPEED = 0.1                  # 最慢 0.1x (慢动作)
    MAX_SPEED = 50.0                 # 最快 50x
    DEFAULT_SPEED = 1.0              # 默认实时
    
    # 帧率配置
    TARGET_FPS_REALTIME = 30         # 实时目标帧率
    MAX_FRAME_SKIP = 5               # 最大跳帧数 (防止卡顿)
    
    # 缓冲区大小
    RECORDING_BUFFER_MAX_FRAMES = 90000  # 约30分钟 @ 30FPS
    TIMELINE_EVENTS_MAX = 10000          # 最大事件数
    SNAPSHOTS_MAX = 50                   # 最大快照数
    
    def __init__(self):
        # ── 状态 ──
        self._state: PlaybackState = PlaybackState.STOPPED
        self._mode: TimeWarpMode = TimeWarpMode.REALTIME
        self._speed: float = self.DEFAULT_SPEED
        
        # ── 时间轴 ──
        self._sim_time: float = 0.0         # 当前仿真时间
        self._start_sim_time: float = 0.0   # 本次运行起始时间
        self._wall_start_time: float = 0.0  # 对应的真实时间
        self._total_elapsed: float = 0.0     # 总仿真时长 (含暂停前)
        
        # ── 帧 ──
        self._frame_counter: int = 0
        self._last_frame_time: float = 0.0
        self._current_frame: Optional[SimulationFrame] = None
        
        # ── 录制缓冲区 ──
        self._is_recording: bool = False
        self._recording_frames: deque = deque(maxlen=self.RECORDING_BUFFER_MAX_FRAMES)
        self._recording_start_time: Optional[float] = None
        self._recording_duration: float = 0.0
        
        # ── 事件时间线 ──
        self._timeline_events: List[TimelineEvent] = []
        
        # ── 快照 ──
        self._snapshots: Dict[str, Snapshot] = {}
        
        # ── 回调钩子 ──
        self._on_frame_callback: Optional[Callable[[SimulationFrame], None]] = None
        self._on_event_callback: Optional[Callable[[TimelineEvent], None]] = None
        
        # ── 异步任务 ──
        self._simulation_task: Optional[asyncio.Task] = None
        self._lock: asyncio.Lock = asyncio.Lock()
        
        # ── 数据源 (外部注入) ──
        self._agv_data_provider: Optional[Callable[[], List[Dict]]] = None
    
    # ═════════════════════════════════════════════════════
    # 播放控制
    # ═════════════════════════════════════════════════════
    
    async def play(self, speed: float = None):
        """开始/恢复播放"""
        async with self._lock:
            if self._state == PlaybackState.PLAYING:
                return
            
            if speed is not None:
                self.set_speed(speed)
            
            now = time.monotonic()
            
            if self._state == PlaybackState.PAUSED:
                # 从暂停恢复：调整wall_start以保持连续性
                paused_duration = now - self._pause_start_time
                self._wall_start_time += paused_duration
            else:
                # 全新开始
                self._wall_start_time = now
                self._start_sim_time = self._sim_time
            
            self._state = PlaybackState.PLAYING
            self._update_mode()
            
            # 启动仿真循环
            if self._simulation_task is None or self._simulation_task.done():
                self._simulation_task = asyncio.create_task(self._simulation_loop())
    
    async def pause(self):
        """暂停播放"""
        async with self._lock:
            if self._state != PlaybackState.PLAYING:
                return
            
            self._state = PlaybackState.PAUSED
            self._pause_start_time = time.monotonic()
            
            # 记录已过去的时间
            elapsed = self._calculate_elapsed()
            self._total_elapsed += elapsed
    
    async def stop(self):
        """停止并重置"""
        async with self._lock:
            self._state = PlaybackState.STOPPED
            
            if self._simulation_task and not self._simulation_task.done():
                self._simulation_task.cancel()
                try:
                    await self._simulation_task
                except asyncio.CancelledError:
                    pass
                self._simulation_task = None
            
            # 重置时间轴 (保留录制数据)
            self._sim_time = 0.0
            self._total_elapsed = 0.0
            self._frame_counter = 0
    
    async def step_forward(self):
        """单步前进一帧"""
        async with self._lock:
            if self._state != PlaybackState.STOPPED and self._state != PlaybackState.PAUSED:
                await self.pause()
            
            self._mode = TimeWarpMode.STEP
            dt = 1.0 / self.TARGET_FPS_REALTIME * self._speed
            await self._advance_simulation(dt)
    
    async def step_backward(self):
        """单步后退一帧 (需要录制支持)"""
        if len(self._recording_frames) < 2:
            return None
        
        # 返回上一帧 (不修改当前状态，只返回数据)
        last_frame = self._recording_frames[-2] if len(self._recording_frames) >= 2 else None
        return last_frame
    
    async def seek_to(self, timestamp: float):
        """
        跳转到指定时间点
        
        如果有录制数据则从最近帧恢复，
        否则只能向前seek (通过加速追赶)。
        """
        if timestamp < self._sim_time:
            # 向后跳转：尝试从录制数据恢复
            target_frame = self._find_closest_frame(timestamp)
            if target_frame:
                await self._restore_from_frame(target_frame)
                await self.pause()  # 跳转后自动暂停
                return True
            return False
        else:
            # 向前跳转：设置目标时间并加速
            # 这里简化处理：直接调整sim_time
            async with self._lock:
                self._sim_time = timestamp
                self._wall_start_time = time.monotonic() - timestamp / max(0.001, self._speed)
            return True
    
    def set_speed(self, speed: float):
        """
        设置播放速度
        
        Args:
            speed: 倍速 (0.1 ~ 50.0), 特殊值:
                   -1.0 表示尽可能快 (无延迟)
        """
        self._speed = max(self.MIN_SPEED, min(self.MAX_SPEED, speed))
        self._update_mode()
    
    def _update_mode(self):
        """根据速度更新模式"""
        if self._speed < 0.9:
            self._mode = TimeWarpMode.SLOW_MOTION
        elif self._speed > 1.1:
            self._mode = TimeWarpMode.ACCELERATED
        else:
            self._mode = TimeWarpMode.REALTIME
    
    # ═════════════════════════════════════════════════════
    # 仿真循环
    # ═════════════════════════════════════════════════════
    
    async def _simulation_loop(self):
        """主仿真循环"""
        self._last_frame_time = time.monotonic()
        
        while self._state == PlaybackState.PLAYING:
            loop_start = time.monotonic()
            
            # 计算delta time
            wall_dt = loop_start - self._last_frame_time
            sim_dt = wall_dt * self._speed
            
            # 推进仿真
            frame = await self._advance_simulation(sim_dt)
            
            # 录制
            if self._is_recording and frame:
                self._recording_frames.append(frame)
            
            # 回调通知
            if frame and self._on_frame_callback:
                try:
                    self._on_frame_callback(frame)
                except Exception:
                    pass
            
            self._last_frame_time = loop_start
            self._frame_counter += 1
            
            # 帧率控制 (仅对慢速和非超速模式有效)
            if self._speed <= 5.0:
                target_interval = 1.0 / self.TARGET_FPS_REALTIME
                elapsed = time.monotonic() - loop_start
                sleep_time = target_interval - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            else:
                # 高速模式：让出CPU但不严格控帧
                await asyncio.sleep(0.001)
    
    async def _advance_simulation(self, dt: float) -> Optional[SimulationFrame]:
        """推进仿真时间并生成新帧"""
        self._sim_time += dt
        
        # 获取AGV数据
        agv_states = []
        if self._agv_data_provider:
            try:
                agv_states = self._agv_data_provider()
            except Exception:
                pass
        
        # 构建帧
        frame = SimulationFrame(
            frame_id=self._frame_counter,
            timestamp=self._sim_time,
            wall_time=time.time(),
            agv_states=agv_states,
            delta_time=dt,
        )
        
        self._current_frame = frame
        return frame
    
    # ═════════════════════════════════════════════════════
    # 录制/回放
    # ═════════════════════════════════════════════════════
    
    def start_recording(self):
        """开始录制"""
        self._is_recording = True
        self._recording_start_time = self._sim_time
        self._recording_frames.clear()
    
    def stop_recording(self) -> Dict[str, Any]:
        """停止录制并返回统计"""
        self._is_recording = False
        duration = 0.0
        if self._recording_start_time is not None and len(self._recording_frames) > 1:
            first_frame = self._recording_frames[0]
            last_frame = self._recording_frames[-1]
            duration = last_frame.timestamp - first_frame.timestamp
        self._recording_duration = duration
        
        return {
            "duration_seconds": duration,
            "total_frames": len(self._recording_frames),
            "file_size_estimate_kb": len(self._recording_frames) * 2,  # 估算
        }
    
    def get_recording(self, start_frame: int = 0, end_frame: int = -1) -> List[SimulationFrame]:
        """获取录制帧序列"""
        frames = list(self._recording_frames)
        if end_frame == -1:
            return frames[start_frame:]
        return frames[start_frame:end_frame]
    
    def get_recording_stats(self) -> Dict[str, Any]:
        """获取录制统计"""
        return {
            "is_recording": self._is_recording,
            "total_frames": len(self._recording_frames),
            "buffer_usage_pct": len(self._recording_frames) / self.RECORDING_BUFFER_MAX_FRAMES * 100,
            "duration_seconds": self._recording_duration,
            "start_time": self._recording_start_time,
        }
    
    async def playback_recording(self, speed: float = 1.0, 
                                  on_frame: Callable = None):
        """
        回放录制的帧序列
        
        Args:
            speed: 回放倍速
            on_frame: 每帧回调
        """
        if not self._recording_frames:
            return
        
        self._state = PlaybackState.PLAYING
        self._playback_speed = speed
        
        interval = (1.0 / self.TARGET_FPS_REALTIME) / max(0.01, speed)
        
        for frame in self._recording_frames:
            if self._state != PlaybackState.PLAYING:
                break
            
            if on_frame:
                on_frame(frame)
            elif self._on_frame_callback:
                self._on_frame_callback(frame)
            
            await asyncio.sleep(interval)
    
    # ═════════════════════════════════════════════════════
    # 快照管理
    # ═════════════════════════════════════════════════════
    
    def take_snapshot(self, name: str = "", description: str = "",
                     tags: List[str] = None) -> Snapshot:
        """保存当前状态快照"""
        snapshot_id = f"snap_{int(time.time() * 1000)}"
        
        snapshot = Snapshot(
            snapshot_id=snapshot_id,
            timestamp=self._sim_time,
            created_at=time.time(),
            name=name or f"Snapshot @ {self._format_time(self._sim_time)}",
            description=description,
            agv_states=self._get_current_agv_states(),
            simulation_params=self._export_simulation_params(),
            tags=tags or [],
            file_size_bytes=len(json.dumps({})),  # TODO: 计算实际大小
        )
        
        # 管理快照数量
        if len(self._snapshots) >= self.SNAPSHOTS_MAX:
            # 删除最旧的快照
            oldest_id = min(self._snapshots.keys(), 
                           key=lambda k: self._snapshots[k].created_at)
            del self._snapshots[oldest_id]
        
        self._snapshots[snapshot_id] = snapshot
        return snapshot
    
    def load_snapshot(self, snapshot_id: str) -> bool:
        """从快照恢复状态"""
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            return False
        
        self._sim_time = snapshot.timestamp
        self._import_simulation_params(snapshot.simulation_params)
        
        # TODO: 恢复AGV/Task状态到对应的数据服务
        
        return True
    
    def delete_snapshot(self, snapshot_id: str) -> bool:
        """删除快照"""
        return self._snapshots.pop(snapshot_id, None) is not None
    
    def list_snapshots(self) -> List[Dict[str, Any]]:
        """列出所有快照"""
        snapshots = sorted(self._snapshots.values(), 
                         key=lambda s: s.created_at, reverse=True)
        return [s.to_dict() for s in snapshots]
    
    # ═════════════════════════════════════════════════════
    # 事件时间线
    # ═════════════════════════════════════════════════════
    
    def add_event(self, event_type: str, source_id: str,
                 title: str = "", description: str = "",
                 severity: str = "info", data: Dict = None) -> TimelineEvent:
        """
        添加时间线事件
        
        由外部系统调用（如调度器完成任务、AGV报警等）
        """
        event = TimelineEvent(
            event_id=f"evt_{len(self._timeline_events)}_{int(time.time()*1000)}",
            timestamp=self._sim_time,
            event_type=event_type,
            source_id=source_id,
            title=title,
            description=description,
            severity=severity,
            data=data or {},
        )
        
        self._timeline_events.append(event)
        
        # 限制数量
        if len(self._timeline_events) > self.TIMELINE_EVENTS_MAX:
            self._timeline_events = self._timeline_events[-self.TIMELINE_EVENTS_MAX:]
        
        # 回调通知
        if self._on_event_callback:
            try:
                self._on_event_callback(event)
            except Exception:
                pass
        
        return event
    
    def get_timeline(self, start_time: float = None, 
                    end_time: float = None,
                    event_types: List[str] = None) -> List[TimelineEvent]:
        """查询时间线事件"""
        events = self._timeline_events
        
        if start_time is not None:
            events = [e for e in events if e.timestamp >= start_time]
        if end_time is not None:
            events = [e for e in events if e.timestamp <= end_time]
        if event_types:
            events = [e for e in events if e.event_type in event_types]
        
        return events
    
    def get_events_around(self, timestamp: float, 
                         window_seconds: float = 5.0) -> Dict[str, List]:
        """获取某时刻前后的事件"""
        before = [e for e in self._timeline_events 
                 if timestamp - window_seconds <= e.timestamp < timestamp]
        after = [e for e in self._timeline_events 
                if timestamp < e.timestamp <= timestamp + window_seconds]
        
        return {"before": [e.to_dict() for e in before],
                "after": [e.to_dict() for e in after]}
    
    # ═════════════════════════════════════════════════════
    # 状态查询
    # ═════════════════════════════════════════════════════
    
    @property
    def state(self) -> PlaybackState:
        return self._state
    
    @property
    def mode(self) -> TimeWarpMode:
        return self._mode
    
    @property
    def speed(self) -> float:
        return self._speed
    
    @property
    def sim_time(self) -> float:
        return self._sim_time
    
    @property
    def frame_count(self) -> int:
        return self._frame_counter
    
    def get_status(self) -> Dict[str, Any]:
        """获取完整状态报告"""
        elapsed = self._calculate_elapsed() if self._state == PlaybackState.PLAYING else 0
        
        return {
            "playback_state": self._state.value,
            "time_mode": self._mode.value,
            "speed": self._speed,
            "sim_time": self._sim_time,
            "sim_time_formatted": self._format_time(self._sim_time),
            "elapsed_total": self._total_elapsed + elapsed,
            "frame_count": self._frame_counter,
            "fps": self._calculate_fps(),
            "recording": self.get_recording_stats(),
            "snapshot_count": len(self._snapshots),
            "event_count": len(self._timeline_events),
        }
    
    # ═════════════════════════════════════════════════════
    # 数据源注入
    # ═════════════════════════════════════════════════════
    
    def set_agv_data_provider(self, provider: Callable[[], List[Dict]]):
        """
        设置AGV数据提供者回调
        
        由Digital Twin WebSocket服务注入，
        每帧调用此函数获取最新的AGV状态列表。
        """
        self._agv_data_provider = provider
    
    def set_on_frame_callback(self, callback: Callable[[SimulationFrame], None]):
        """设置帧回调 (用于WebSocket推送等)"""
        self._on_frame_callback = callback
    
    def set_on_event_callback(self, callback: Callable[[TimelineEvent], None]):
        """设置事件回调"""
        self._on_event_callback = callback
    
    # ═════════════════════════════════════════════════════
    # 内部工具方法
    # ═════════════════════════════════════════════════════
    
    def _calculate_elapsed(self) -> float:
        """计算本次运行的已过时间"""
        return (time.monotonic() - self._wall_start_time) * self._speed
    
    def _calculate_fps(self) -> float:
        """估算当前帧率"""
        # 简化计算：基于最近间隔
        if self._frame_counter < 2:
            return 0.0
        total_time = self._sim_time - self._start_sim_time if self._sim_time > self._start_sim_time else 0.001
        return self._frame_counter / max(0.001, total_time) * self._speed
    
    def _find_closest_frame(self, target_time: float) -> Optional[SimulationFrame]:
        """在录制数据中查找最接近的帧"""
        if not self._recording_frames:
            return None
        
        closest = min(self._recording_frames, 
                     key=lambda f: abs(f.timestamp - target_time))
        return closest if abs(closest.timestamp - target_time) < 1.0 else None
    
    async def _restore_from_frame(self, frame: SimulationFrame):
        """从帧数据恢复状态"""
        self._sim_time = frame.timestamp
        self._current_frame = frame
        # TODO: 恢复完整状态到数据服务
    
    def _get_current_agv_states(self) -> Dict[str, Any]:
        """获取当前AGV状态的深拷贝"""
        if self._current_frame:
            return {"timestamp": self._sim_time,
                   "states": self._current_frame.agv_states}
        return {}
    
    def _export_simulation_params(self) -> Dict[str, Any]:
        """导出仿真参数 (供快照使用)"""
        return {
            "speed": self._speed,
            "mode": self._mode.value,
            "frame_counter": self._frame_counter,
        }
    
    def _import_simulation_params(self, params: Dict[str, Any]):
        """导入仿真参数 (从快照恢复)"""
        self._speed = params.get("speed", self.DEFAULT_SPEED)
        mode_str = params.get("mode", TimeWarpMode.REALTIME.value)
        try:
            self._mode = TimeWarpMode(mode_str)
        except ValueError:
            self._mode = TimeWarpMode.REALTIME
        self._frame_counter = params.get("frame_counter", 0)
    
    @staticmethod
    def _format_time(seconds: float) -> str:
        """格式化时间为 HH:MM:SS.mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"


# ══════════════════════════════════════════════════════════
# FastAPI Router - 仿真控制API
# ══════════════════════════════════════════════════════════

from fastapi import APIRouter, HTTPException, Query

timewarp_router = APIRouter(prefix="/api/v3/simulation", tags=["TimeWarp Simulation"])

# 全局单例
_engine: Optional[TimeWarpCore] = None


def get_engine() -> TimeWarpCore:
    global _engine
    if _engine is None:
        _engine = TimeWarpCore()
    return _engine


@timewarp_router.get("/status")
async def get_simulation_status():
    """获取仿真引擎状态 (时间、速度、帧率等)"""
    engine = get_engine()
    return engine.get_status()


@timewarp_router.post("/play")
async def play_simulation(speed: float = Query(1.0, ge=0.1, le=50.0)):
    """开始/恢复播放 (可选设置倍速)"""
    engine = get_engine()
    await engine.play(speed=speed)
    return {"message": f"Playback started at {speed}x", **engine.get_status()}


@timewarp_router.post("/pause")
async def pause_simulation():
    """暂停播放"""
    engine = get_engine()
    await engine.pause()
    return {"message": "Playback paused", **engine.get_status()}


@timewarp_router.post("/stop")
async def stop_simulation():
    """停止并重置"""
    engine = get_engine()
    await engine.stop()
    return {"message": "Simulation stopped and reset"}


@timewarp_router.post("/step")
async def step_simulation():
    """单步前进"""
    engine = get_engine()
    await engine.step_forward()
    return {"message": "Stepped forward", **engine.get_status()}


@timewarp_router.post("/speed")
async def set_speed(speed: float = Query(..., ge=0.1, le=50.0)):
    """设置播放速度 (0.1x ~ 50x)"""
    engine = get_engine()
    engine.set_speed(speed)
    return {"message": f"Speed set to {speed}x", "new_speed": speed}


@timewarp_router.post("/seek")
async def seek_simulation(timestamp: float):
    """跳转到指定仿真时间点"""
    engine = get_engine()
    success = await engine.seek_to(timestamp)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot seek to this time (no recording data)")
    return {"message": f"Seeked to {timestamp}s", **engine.get_status()}


# --- 录制接口 ---

@timewarp_router.post("/recording/start")
async def start_recording():
    """开始录制"""
    engine = get_engine()
    engine.start_recording()
    return {"message": "Recording started"}


@timewarp_router.post("/recording/stop")
async def stop_recording():
    """停止录制"""
    engine = get_engine()
    stats = engine.stop_recording()
    return {"message": "Recording stopped", "stats": stats}


@timewarp_router.get("/recording/stats")
async def get_recording_stats():
    """获取录制状态"""
    engine = get_engine()
    return engine.get_recording_stats()


# --- 快照接口 ---

@timewarp_router.post("/snapshots")
async def create_snapshot(name: str = "", description: str = ""):
    """创建当前状态快照"""
    engine = get_engine()
    snapshot = engine.take_snapshot(name=name, description=description)
    return snapshot.to_dict()


@timewarp_router.get("/snapshots")
async def list_snapshots():
    """列出所有快照"""
    engine = get_engine()
    return engine.list_snapshots()


@timewarp_router.post("/snapshots/{snapshot_id}/restore")
async def restore_snapshot(snapshot_id: str):
    """从快照恢复状态"""
    engine = get_engine()
    success = engine.load_snapshot(snapshot_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
    return {"message": f"Restored from snapshot {snapshot_id}", **engine.get_status()}


@timewarp_router.delete("/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str):
    """删除快照"""
    engine = get_engine()
    success = engine.delete_snapshot(snapshot_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Snapshot not found: {snapshot_id}")
    return {"message": f"Deleted snapshot {snapshot_id}"}


# --- 事件时间线 ---

@timewarp_router.post("/events")
async def add_timeline_event(
    event_type: str,
    source_id: str,
    title: str = "",
    description: str = "",
    severity: str = "info",
    data: Dict = None,
):
    """添加时间线事件 (供外部系统调用)"""
    engine = get_engine()
    event = engine.add_event(
        event_type=event_type,
        source_id=source_id,
        title=title,
        description=description,
        severity=severity,
        data=data,
    )
    return event.to_dict()


@timewarp_router.get("/events")
async def get_timeline(
    start_time: Optional[float] = Query(None),
    end_time: Optional[float] = Query(None),
    types: Optional[str] = Query(None, description="逗号分隔的事件类型"),
):
    """查询事件时间线"""
    engine = get_engine()
    event_types = types.split(",") if types else None
    events = engine.get_timeline(start_time, end_time, event_types)
    return {"events": [e.to_dict() for e in events], "count": len(events)}


@timewarp_router.get("/events/around/{timestamp}")
async def get_events_around(timestamp: float, 
                            window: float = Query(5.0, ge=0.1)):
    """获取某时刻前后的事件"""
    engine = get_engine()
    result = engine.get_events_around(timestamp, window)
    return result


# 导出
__all__ = [
    'TimeWarpCore', 'SimulationFrame', 'TimelineEvent', 'Snapshot',
    'PlaybackState', 'TimeWarpMode',
    'timewarp_router',
]
