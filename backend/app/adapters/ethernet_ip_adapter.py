"""
EtherNet/IP (CIP) 控制器适配器 — Phase 4.0 P2-04

支持罗克韦尔 (Rockwell Automation) CompactLogix / ControlLogix PLC:
  - CIP 标签读写 (Read_Tag / Write_Tag Service)
  - 批量标签读取优化
  - AGV + 输送线 + 安全联锁 默认标签映射
  - 三种运行模式: live(pylogix) | simulation | soft-plc(预留)

依赖: pylogix (可选, 未安装时降级为模拟模式)

参考: 
  - ModbusVehicleAdapter (modbus_vehicle_adapter.py) — 同级协议适配器
  - BaseVehicleAdapter (base_vehicle.py) — 统一接口
"""

from __future__ import annotations

import asyncio
import logging
import random
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

# 导入统一接口
try:
    from .base_adapter import (
        BaseVehicleAdapter,
        CommandResult,
        TransportOrderMessage,
        VehicleCommand,
        VehicleState,
        VehicleStatus,
    )
except ImportError:
    # 独立运行时的最小化定义
    class VehicleCommand(str, Enum):
        MOVE = "move"; STOP = "stop"; RESUME = "resume"; CHARGE = "charge"
        LOAD = "load"; UNLOAD = "unload"; CANCEL_TASK = "cancel_task"
        PICKUP = "pickup"; DROPOFF = "dropoff"

    class VehicleState(str, Enum):
        IDLE = "idle"; MOVING = "moving"; LOADING = "loading"
        UNLOADING = "unloading"; CHARGING = "charging"
        EXECUTING = "executing"; ERROR = "error"; OFFLINE = "offline"

    @dataclass
    class VehicleStatus:
        vehicle_id: str; state: VehicleState = VehicleState.IDLE
        x: float = 0.0; y: float = 0.0; angle: float = 0.0
        speed: float = 0.0; battery_level: float = 100.0
        current_node: str = ""; target_node: str = ""
        load_status: bool = False; error_code: int = 0; error_message: str = ""
        last_heartbeat: float = 0.0; odometer: float = 0.0; operating_hours: float = 0.0
        order_id: str = ""; last_node_id: str = ""; sequence_id: int = 0
        def to_dict(self) -> Dict[str, Any]: ...

    @dataclass
    class CommandResult:
        success: bool; vehicle_id: str; command: str
        message: str = ""; timestamp: float = 0.0; data: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class TransportOrderMessage:
        order_id: str; edges: List[Dict] = field(default_factory=list)
        nodes: List[Dict] = field(default_factory=list); actions: List[Dict] = field(default_factory=list)

    class BaseVehicleAdapter:
        def __init__(self, name: str, protocol: str = "custom"):
            self.name = name; self.protocol = protocol; self._connected = False; self._vehicle_count = 0
        @property
        def is_connected(self): return self._connected
        @property
        def vehicle_count(self): return self._vehicle_count
        async def connect(self) -> bool: ...
        async def disconnect(self) -> None: ...
        async def send_command(self, vid, cmd, params=None) -> CommandResult: ...
        async def get_status(self, vid) -> Optional[VehicleStatus]: ...
        async def get_all_statuses(self) -> List[VehicleStatus]: ...

logger = logging.getLogger(__name__)

# pylogix 可选导入
try:
    from pylogix import PLC
    HAS_PYLOGIX = True
except ImportError:
    HAS_PYLOGIX = False
    logger.info("pylogix not installed, EtherNet/IP adapter will run in simulation mode")


# ==================== CIP 数据模型 ====================

class CipServiceCode(int, Enum):
    """CIP 服务码 (EtherNet/IP Common Protocol Specification)"""
    READ_TAG_SERVICE = 0x4C          # CIP Read Tag Service
    WRITE_TAG_SERVICE = 0x4D         # CIP Write Tag Service
    READ_TAG_FRAGMENTED = 0x52       # Fragmented Read
    WRITE_TAG_FRAGMENTED = 0x53      # Fragmented Write
    GET_ATTRIBUTE_SINGLE = 0x0E      # Get Attribute Single


class CipDataType(str, Enum):
    """
    CIP 数据类型 (对应 Logix5000 数据类型).
    
    参考: Rockwell Publication 1756-PM001 - Logix5000 Controllers User Manual
    """
    BOOL = "BOOL"           # 1 bit (布尔)
    SINT = "SINT"           # 8-bit signed integer (-128 ~ 127)
    INT = "INT"             # 16-bit signed (-32768 ~ 32767)
    DINT = "DINT"           # 32-bit signed (~±2.1e9)
    LINT = "LINT"           # 64-bit signed (~±9.2e18)
    REAL = "REAL"           # 32-bit IEEE 754 single-precision float
    LREAL = "LREAL"         # 64-bit double-precision float
    STRING = "STRING"       # 可变长度 SINT[] 字符串
    STRUCT = "STRUCT"       # 结构体 (UDT)
    ARRAY = "ARRAY"         # 数组


@dataclass
class CipTagDefinition:
    """
    CIP 标签定义.
    
    对应罗克韦尔 PLC 中的 Controller Tags 或 Program Tags.
    
    Examples:
        "Program:Main.AGV[0].Status" → DINT 类型状态码
        "Conveyor[0].Running" → BOOL 运行状态
        "Safety.EStop" → BOOL 急停按钮
    """
    name: str                          # 标签全名 (CIP 路径)
    data_type: CipDataType              # 数据类型
    array_size: int = 1                 # 数组维度 (1=标量)
    scale: float = 1.0                  # 缩放因子 (原始值→工程值)
    unit: str = ""                      # 工程单位
    description: str = ""               # 描述
    read_only: bool = False             # 是否只读
    
    def __post_init__(self):
        if isinstance(self.data_type, str):
            try:
                self.data_type = CipDataType(self.data_type)
            except ValueError:
                self.data_type = CipDataType.DINT


# ==================== 默认 CIP 标签映射表 ====================

def _build_default_cip_tags() -> Dict[str, CipTagDefinition]:
    """
    构建默认的 CIP 标签映射表.
    
    参考罗克韦尔 ControlLogix 典型 AGV+输送线控制程序架构:
      - Program:Main.AGV[*] — AGV 数组
      - Program:Main.Conveyor[*] — 输送线数组  
      - Safety.* — 安全联锁区域
    """
    tags: Dict[str, CipTagDefinition] = {}
    
    # ===== AGV 状态标签 (Program:Main.AGV[i]) =====
    for i in range(10):  # 支持 0~9 共10台AGV (可扩展)
        pfx = f"AGV[{i}]"
        
        tags.update({
            f"{pfx}.Status":       CipTagDefinition(f"{pfx}.Status", CipDataType.DINT,
                                                    description=f"AGV{i} 状态码 (0=idle,1=moving,4=charging,99=error)"),
            f"{pfx}.Position.X":   CipTagDefinition(f"{pfx}.Position.X", CipDataType.REAL,
                                                    scale=1.0, unit="m", description=f"AGV{i} X坐标"),
            f"{pfx}.Position.Y":   CipTagDefinition(f"{pfx}.Position.Y", CipDataType.REAL,
                                                    scale=1.0, unit="m", description=f"AGV{i} Y坐标"),
            f"{pfx}.Angle":        CipTagDefinition(f"{pfx}.Angle", CipDataType.REAL,
                                                    scale=1.0, unit="deg", description=f"AGV{i} 朝向角度"),
            f"{pfx}.Battery":      CipTagDefinition(f"{pfx}.Battery", CipDataType.DINT,
                                                    scale=0.1, unit="%",
                                                    description=f"AGV{i} 电量百分比 (0-1000→0-100%)"),
            f"{pfx}.Speed":        CipTagDefinition(f"{pfx}.Speed", CipDataType.REAL,
                                                    unit="m/s", description=f"AGV{i} 当前速度"),
            f"{pfx}.ErrorCode":    CipTagDefinition(f"{pfx}.ErrorCode", CipDataType.DINT,
                                                    read_only=True, description=f"AGV{i} 错误码"),
            f"{pfx}.CurrentStation":CipTagDefinition(f"{pfx}.CurrentStation", CipDataType.DINT,
                                                     description=f"AGV{i} 当前工位ID"),
            f"{pfx}.TargetStation":CipTagDefinition(f"{pfx}.TargetStation", CipDataType.DINT,
                                                     description=f"AGV{i} 目标工位ID"),
            f"{pfx}.LoadStatus":   CipTagDefinition(f"{pfx}.LoadStatus", CipDataType.BOOL,
                                                     description=f"AGV{i} 载货状态"),
            
            # 控制指令标签 (写操作)
            f"{pfx}.Cmd.Move":     CipTagDefinition(f"{pfx}.Cmd.Move", CipDataType.BOOL,
                                                     description=f"AGV{i} 启动移动指令"),
            f"{pfx}.Cmd.Stop":     CipTagDefinition(f"{pfx}.Cmd.Stop", CipDataType.BOOL,
                                                     description=f"AGV{i} 停止指令"),
            f"{pfx}.Cmd.Target":   CipTagDefinition(f"{pfx}.Cmd.Target", CipDataType.DINT,
                                                     description=f"AGV{i} 目标位置参数"),
            f"{pfx}.Cmd.Load":     CipTagDefinition(f"{pfx}.Cmd.Load", CipDataType.BOOL,
                                                     description=f"AGV{i} 开始装货"),
            f"{pfx}.Cmd.Unload":   CipTagDefinition(f"{pfx}.Cmd.Unload", CipDataType.BOOL,
                                                     description=f"AGV{i} 开始卸货"),
            f"{pfx}.Cmd.Charge":   CipTagDefinition(f"{pfx}.Cmd.Charge", CipDataType.BOOL,
                                                     description=f"AGV{i} 开始充电"),
        })
    
    # ===== 输送线状态标签 (Program:Main.Conveyor[i]) =====
    for i in range(6):  # 支持 0~5 共6条输送线
        pfx = f"Conveyor[{i}]"
        
        tags.update({
            f"{pfx}.Running":      CipTagDefinition(f"{pfx}.Running", CipDataType.BOOL,
                                                      description=f"Conveyor{i} 运行状态"),
            f"{pfx}.Speed":        CipTagDefinition(f"{pfx}.Speed", CipDataType.REAL,
                                                      unit="m/s", description=f"Conveyor{i} 速度设定"),
            f"{pfx}.ActualSpeed":  CipTagDefinition(f"{pfx}.ActualSpeed", CipDataType.REAL,
                                                      unit="m/s", read_only=True,
                                                      description=f"Conveyor{i} 实际速度"),
            f"{pfx}.PhotoEye.In":  CipTagDefinition(f"{pfx}.PhotoEye.In", CipDataType.BOOL,
                                                      read_only=True,
                                                      description=f"Conveyor{i} 入口光电传感器"),
            f"{pfx}.PhotoEye.Out": CipTagDefinition(f"{pfx}.PhotoEye.Out", CipDataType.BOOL,
                                                      read_only=True,
                                                      description=f"Conveyor{i} 出口光电传感器"),
            f"{pfx}.MotorCurrent": CipTagDefinition(f"{pfx}.MotorCurrent", CipDataType.REAL,
                                                      unit="A", read_only=True,
                                                      description=f"Conveyor{i} 电机电流"),
            f"{pfx}.FaultCode":    CipTagDefinition(f"{pfx}.FaultCode", CipDataType.DINT,
                                                      read_only=True,
                                                      description=f"Conveyor{i} 故障码"),
            f"{pfx}.AccumCount":   CipTagDefinition(f"{pfx}.AccumCount", CipDataType.DINT,
                                                      description=f"Conveyor{i} 累计计数"),
            f"{pfx}.Cmd.Start":    CipTagDefinition(f"{pfx}.Cmd.Start", CipDataType.BOOL,
                                                      description=f"Conveyor{i} 启动指令"),
            f"{pfx}.Cmd.Stop":     CipTagDefinition(f"{pfx}.Cmd.Stop", CipDataType.BOOL,
                                                      description=f"Conveyor{i} 停止指令"),
            f"{pfx}.Cmd.Reset":    CipTagDefinition(f"{pfx}.Cmd.Reset", CipDataType.BOOL,
                                                      description=f"Conveyor{i} 故障复位"),
        })
    
    # ===== 安全联锁标签 (Safety.*) =====
    tags.update({
        "Safety.EStop":           CipTagDefinition("Safety.EStop", CipDataType.BOOL, read_only=True,
                                                   description="急停按钮状态 (True=触发)"),
        "Safety.LightCurtain.East": CipTagDefinition("Safety.LightCurtain.East", CipDataType.BOOL,
                                                       read_only=True, description="东侧光幕状态"),
        "Safety.LightCurtain.West":CipTagDefinition("Safety.LightCurtain.West", CipDataType.BOOL,
                                                       read_only=True, description="西侧光幕状态"),
        "Safety.Door.North":      CipTagDefinition("Safety.Door.North", CipDataType.BOOL,
                                                    read_only=True, description="北门安全开关"),
        "Safety.Door.South":      CipTagDefinition("Safety.Door.South", CipDataType.BOOL,
                                                    read_only=True, description="南门安全开关"),
        "Safety.ZoneEnabled":     CipTagDefinition("Safety.ZoneEnabled", CipDataType.DINT,
                                                    description="当前启用安全区编号"),
        "Safety.SystemReady":     CipTagDefinition("Safety.SystemReady", CipDataType.BOOL,
                                                    read_only=True, description="系统就绪信号"),
        "Safety.ResetAllowed":    CipTagDefinition("Safety.ResetAllowed", CipDataType.BOOL,
                                                    read_only=True, description="允许复位信号"),
    })
    
    return tags


DEFAULT_CIP_TAG_MAP: Dict[str, CipTagDefinition] = _build_default_cip_tags()


# ==================== 通信指标 ====================

@dataclass
class CipHealthMetrics:
    """EtherNet/IP 通信健康指标"""
    read_count: int = 0
    write_count: int = 0
    batch_read_count: int = 0
    error_count: int = 0
    timeout_count: int = 0
    total_bytes_read: int = 0
    total_bytes_written: int = 0
    _latency_samples: List[float] = field(default_factory=list)
    
    @property
    def avg_latency_ms(self) -> float:
        if not self._latency_samples:
            return 0.0
        return sum(self._latency_samples) / len(self._latency_samples)
    
    @property
    def max_latency_ms(self) -> float:
        if not self._latency_samples:
            return 0.0
        return max(self._latency_samples)
    
    @property
    def p99_latency_ms(self) -> float:
        if len(self._latency_samples) < 10:
            return self.avg_latency_ms
        s = sorted(self._latency_samples)
        idx = int(len(s) * 0.99)
        return s[idx]
    
    def record_read(self, success: bool, latency_ms: float = 0.0, bytes_count: int = 0):
        self.read_count += 1
        self.total_bytes_read += bytes_count
        if success:
            self._record_latency(latency_ms)
        else:
            self.error_count += 1
    
    def record_write(self, success: bool, latency_ms: float = 0.0, bytes_count: int = 0):
        self.write_count += 1
        self.total_bytes_written += bytes_count
        if not success:
            self.error_count += 1
    
    def record_batch_read(self, tag_count: int, latency_ms: float):
        self.batch_read_count += 1
        self.read_count += tag_count
        self._record_latency(latency_ms)
    
    def _record_latency(self, ms: float):
        self._latency_samples.append(ms)
        if len(self._latency_samples) > 200:
            self._latency_samples.pop(0)
    
    def to_dict(self) -> Dict[str, Any]:
        total_ops = self.read_count + self.write_count
        return {
            "read_count": self.read_count,
            "write_count": self.write_count,
            "batch_read_count": self.batch_read_count,
            "error_count": self.error_count,
            "timeout_count": self.timeout_count,
            "error_rate_pct": round(self.error_count / max(1, total_ops) * 100, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "total_bytes_read": self.total_bytes_read,
            "total_bytes_written": self.total_bytes_written,
            "sample_size": len(self._latency_samples),
        }


# ==================== 主适配器类 ====================

class EthernetIpVehicleAdapter(BaseVehicleAdapter):
    """
    EtherNet/IP (CIP) 车辆/PLC 适配器.
    
    支持三种运行模式:
      1. **live**: 通过 pylogix 连接真实罗克韦尔 PLC
      2. **simulation**: 内存模拟 (无需任何外部依赖)
      3. **soft-plc**: 连接开源软PLC (如 OpenPLC / pycomm3 模拟, 预留)
    
    特性:
      - CIP 标签读写 (Read_Tag / Write_Tag)
      - 批量标签读取优化 (减少网络往返)
      - 完整的 AGV + 输送线 + 安全联锁 标签映射
      - 通信健康指标采集
      - 与 ModbusVehicleAdapter 一致的接口风格
    
    用法示例:
        adapter = EthernetIpVehicleAdapter(
            plc_host="192.168.1.100",
            plc_port=44818,
            mode="simulation",
            num_sim_agvs=50,
        )
        await adapter.connect()
        status = await adapter.get_status("agv_0001")
        await adapter.send_command("agv_0001", VehicleCommand.MOVE, {"target": "node_0042"})
    """

    def __init__(
        self,
        plc_host: str = "192.168.1.100",
        plc_port: int = 44818,
        mode: str = "simulation",
        tag_map: Optional[Dict[str, CipTagDefinition]] = None,
        num_sim_agvs: int = 5,
        program_name: str = "Main",  # Logix5000 Program Name
        **kwargs,
    ):
        super().__init__(name="ethernet-ip", protocol="cip")
        
        self.plc_host = plc_host
        self.plc_port = plc_port
        self.mode = mode.lower()
        self.program_name = program_name
        self.tag_map = tag_map or DEFAULT_CIP_TAG_MAP
        self.num_sim_agvs = num_sim_agvs
        
        self._plc: Any = None
        self._sim_tags: Dict[str, Any] = {}  # 模拟标签值存储
        self.metrics = CipHealthMetrics()
        
        # CIP 会话信息
        self._session_handle: Optional[int] = None
        self._session_established_at: float = 0.0

    # ==================== 连接管理 ====================

    async def connect(self) -> bool:
        """建立 CIP 会话或初始化模拟器"""
        if self.mode == "live" and HAS_PYLOGIX:
            return await self._connect_live()
        elif self.mode == "soft-plc":
            logger.info("Soft-PLC mode not yet fully implemented, falling back to simulation")
            self.mode = "simulation"
            self._init_simulation()
            return True
        else:
            self._init_simulation()
            return True

    async def _connect_live(self) -> bool:
        """连接真实罗克韦尔 PLC"""
        try:
            self._plc = PLC(
                IPAddress=self.plc_host,
                Port=self.plc_port,
            )
            
            # 测试连接 — 读取一个简单标签
            test_start = time.time()
            test_val = self._plc.Read("Safety.SystemReady")
            elapsed = (time.time() - test_start) * 1000
            
            if test_val is not None:
                self._connected = True
                self._session_established_at = time.time()
                self._vehicle_count = self._discover_agv_count()
                
                logger.info(
                    "EtherNet/IP connected to %s:%d (program=%s, %d AGVs found, %.1fms)",
                    self.plc_host, self.plc_port, self.program_name,
                    self._vehicle_count, elapsed,
                )
                return True
            else:
                raise ConnectionError("PLC Read returned None (tag may not exist)")
                
        except Exception as e:
            logger.error(
                "EtherNet/IP connect to %s:%d failed: %s, falling back to simulation",
                self.plc_host, self.plc_port, e,
            )
            self.mode = "simulation"
            self._init_simulation()
            return True

    def _init_simulation(self):
        """初始化模拟 PLC 环境"""
        self._connected = True
        
        # 初始化所有标签默认值
        for tag_name, tag_def in self.tag_map.items():
            if tag_def.data_type == CipDataType.BOOL:
                self._sim_tags[tag_name] = False
            elif tag_def.data_type in (CipDataType.DINT, CipDataType.INT, CipDataType.SINT):
                self._sim_tags[tag_name] = 0
            elif tag_def.data_type in (CipDataType.REAL, CipDataType.LREAL):
                self._sim_tags[tag_name] = 0.0
            else:
                self._sim_tags[tag_name] = None
        
        # 初始化模拟 AGV 状态
        for i in range(min(self.num_sim_agvs, 10)):
            pfx = f"AGV[{i}]"
            base_angle = random.uniform(-180, 180)
            self._sim_tags[f"{pfx}.Position.X"] = random.uniform(0, 200)
            self._sim_tags[f"{pfx}.Position.Y"] = random.uniform(0, 150)
            self._sim_tags[f"{pfx}.Angle"] = base_angle
            self._sim_tags[f"{pfx}.Battery"] = random.randint(400, 999)  # 40.0-99.9%
            self._sim_tags[f"{pfx}.Speed"] = 0.0
            self._sim_tags[f"{pfx}.Status"] = 0  # idle
            self._sim_tags[f"{pfx}.CurrentStation"] = random.randint(1, 20)
            self._sim_tags[f"{pfx}.ErrorCode"] = 0
            self._sim_tags[f"{pfx}.LoadStatus"] = False
        
        # 初始化输送线状态
        for i in range(6):
            pfx = f"Conveyor[{i}]"
            self._sim_tags[f"{pfx}.Running"] = i < 3  # 前3条运行
            self._sim_tags[f"{pfx}.Speed"] = random.choice([0.5, 1.0, 1.5])
            self._sim_tags[f"{pfx}.ActualSpeed"] = self._sim_tags.get(f"{pfx}.Speed", 0)
            self._sim_tags[f"{pfx}.PhotoEye.In"] = False
            self._sim_tags[f"{pfx}.PhotoEye.Out"] = False
            self._sim_tags[f"{pfx}.MotorCurrent"] = random.uniform(0.5, 5.0)
            self._sim_tags[f"{pfx}.FaultCode"] = 0
            self._sim_tags[f"{pfx}.AccumCount"] = random.randint(0, 1000)
        
        # 安全系统正常
        self._sim_tags["Safety.EStop"] = False
        self._sim_tags["Safety.SystemReady"] = True
        self._sim_tags["Safety.ZoneEnabled"] = 1
        
        self._vehicle_count = min(self.num_sim_agvs, 10)
        self._session_established_at = time.time()
        
        logger.info(
            "EtherNet/IP simulation mode initialized (%d AGVs, %d Conveyor lines, %d tags)",
            self._vehicle_count, 6, len(self._sim_tags),
        )

    async def disconnect(self) -> None:
        """断开 CIP 会话"""
        if self._plc is not None:
            try:
                self._plc.Close() if hasattr(self._plc, 'Close') else None
            except Exception as e:
                logger.debug("Error closing PLC connection: %s", e)
            finally:
                self._plc = None
        
        self._connected = False
        self._sim_tags.clear()
        self._session_handle = None
        logger.info("EtherNet/IP adapter disconnected")

    # ==================== 标签读写核心 ====================

    async def read_tag(self, tag_name: str) -> Optional[Any]:
        """
        读取单个 CIP 标签值.
        
        Args:
            tag_name: 标签名称 (如 "AGV[0].Status" 或完整路径 "Program:Main.AGV[0].Status")
        
        Returns:
            标签值 (已按 scale 转换为工程值), 或 None 如果读取失败
        """
        full_tag = self._resolve_tag_path(tag_name)
        start = time.time()
        
        if self.mode == "live" and self._plc:
            try:
                val = self._plc.Read(full_tag)
                elapsed = (time.time() - start) * 1000
                
                tag_def = self.tag_map.get(tag_name)
                if val is not None and tag_def:
                    val = self._scale_value(val, tag_def)
                    bytes_est = self._estimate_byte_size(tag_def)
                    self.metrics.record_read(True, elapsed, bytes_est)
                    return val
                elif val is not None:
                    self.metrics.record_read(True, elapsed)
                    return val
                else:
                    self.metrics.record_read(False, elapsed)
                    return None
                    
            except Exception as e:
                elapsed = (time.time() - start) * 1000
                logger.debug("CIP Read '%s' error: %s", tag_name, e)
                self.metrics.record_read(False, elapsed)
                return None
        else:
            # 模拟模式
            val = self._sim_tags.get(tag_name)
            tag_def = self.tag_map.get(tag_name)
            if val is not None and tag_def:
                val = self._scale_value(val, tag_def)
            return val

    async def write_tag(self, tag_name: str, value: Any) -> bool:
        """
        写入单个 CIP 标签值.
        
        Args:
            tag_name: 标签名称
            value: 要写入的值 (工程值, 自动反缩放)
        
        Returns:
            True 写入成功, False 失败或只读标签
        """
        full_tag = self._resolve_tag_path(tag_name)
        start = time.time()
        
        tag_def = self.tag_map.get(tag_name)
        if tag_def and tag_def.read_only:
            logger.warning("Attempt to write read-only tag: %s", tag_name)
            return False
        
        # 反缩放
        write_value = self._unscale_value(value, tag_def) if tag_def else value
        
        if self.mode == "live" and self._plc:
            try:
                result = self._plc.Write(full_tag, write_value)
                elapsed = (time.time() - start) * 1000
                
                success = (result is not None and result.Value is not None)
                bytes_est = self._estimate_byte_size(tag_def)
                self.metrics.record_write(success, elapsed, bytes_est if success else 0)
                return success
                
            except Exception as e:
                elapsed = (time.time() - start) * 1000
                logger.debug("CIP Write '%s' error: %s", tag_name, e)
                self.metrics.record_write(False, elapsed)
                return False
        else:
            self._sim_tags[tag_name] = write_value
            self.metrics.record_write(True, (time.time() - start) * 1000)
            return True

    async def read_multiple_tags(self, tag_names: List[str]) -> Dict[str, Optional[Any]]:
        """
        批量读取多个标签 (性能优化).
        
        对于真实 PLC，逐个读取; 模拟模式下一次返回所有.
        未来可扩展为 CIP Multi-Request Service 减少往返.
        """
        results: Dict[str, Optional[Any]] = {}
        start = time.time()
        
        if self.mode != "live" or not self._plc:
            # 模拟模式: 直接从内存取
            for name in tag_names:
                results[name] = self._sim_tags.get(name)
                tag_def = self.tag_map.get(name)
                if results[name] is not None and tag_def:
                    results[name] = self._scale_value(results[name], tag_def)
            self.metrics.record_batch_read(len(tag_names), (time.time() - start) * 1000)
            return results
        
        # Live 模式: 并发读取
        for name in tag_names:
            results[name] = await self.read_tag(name)
        
        return results

    # ==================== BaseVehicleAdapter 接口实现 ====================

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """
        通过 CIP 下发控制指令.
        
        映射到对应的 Cmd.* 标签写入.
        """
        params = params or {}
        ts = time.time()
        
        # 解析 AGV 编号 (如 agv_0001 → 0)
        try:
            agv_idx = self._parse_agv_index(vehicle_id)
        except (ValueError, IndexError):
            return CommandResult(
                success=False, vehicle_id=vehicle_id, command=command.value,
                message=f"Invalid AGV ID format: {vehicle_id}", timestamp=ts,
            )
        
        pfx = f"AGV[{agv_idx}]"
        
        # 指令到 CIP 标签映射
        cmd_to_tags: Dict[VehicleCommand, tuple] = {
            VehicleCommand.MOVE:      (f"{pfx}.Cmd.Move", True),
            VehicleCommand.STOP:      (f"{pfx}.Cmd.Stop", True),
            VehicleCommand.RESUME:    (f"{pfx}.Cmd.Move", True),
            VehicleCommand.CHARGE:    (f"{pfx}.Cmd.Charge", True),
            VehicleCommand.LOAD:      (f"{pfx}.Cmd.Load", True),
            VehicleCommand.UNLOAD:    (f"{pfx}.Cmd.Unload", True),
            VehicleCommand.PICKUP:    (f"{pfx}.Cmd.Load", True),
            VehicleCommand.DROPOFF:   (f"{pfx}.Cmd.Unload", True),
        }
        
        mapping = cmd_to_tags.get(command)
        if not mapping:
            return CommandResult(
                success=False, vehicle_id=vehicle_id, command=command.value,
                message=f"No CIP tag mapped for command {command.value}", timestamp=ts,
            )
        
        tag_name, tag_value = mapping
        
        # MOVE 指令需要额外设置 Target 参数
        if command == VehicleCommand.MOVE and "target" in params:
            target_val = params["target"]
            if isinstance(target_val, str) and target_val.startswith("node_"):
                target_val = int(target_val.replace("node_", ""))
            await self.write_tag(f"{pfx}.Cmd.Target", target_val)
        
        # 写入指令标签
        success = await self.write_tag(tag_name, tag_value)
        
        # 更新模拟状态
        if self.mode != "live":
            self._apply_simulated_command(agv_idx, command, params)
        
        msg = f"CIP Write {tag_name}={tag_value}" if success else f"CIP Write failed: {tag_name}"
        
        return CommandResult(
            success=success,
            vehicle_id=vehicle_id,
            command=command.value,
            message=msg,
            timestamp=ts,
            data={"cip_tag": tag_name, "agv_index": agv_idx},
        )

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """
        通过 CIP 读取车辆状态.
        
        批量读取 AGV[i].* 相关的所有状态标签, 映射到 VehicleStatus.
        """
        try:
            agv_idx = self._parse_agv_index(vehicle_id)
        except (ValueError, IndexError):
            return None
        
        pfx = f"AGV[{agv_idx}]"
        
        # 需要读取的状态标签列表
        status_tag_names = [
            f"{pfx}.Status", f"{pfx}.Position.X", f"{pfx}.Position.Y",
            f"{pfx}.Angle", f"{pfx}.Battery", f"{pfx}.Speed",
            f"{pfx}.ErrorCode", f"{pfx}.CurrentStation", f"{pfx}.TargetStation",
            f"{pfx}.LoadStatus",
        ]
        
        values = await self.read_multiple_tags(status_tag_names)
        
        # 检查是否所有关键标签都有值
        if values.get(f"{pfx}.Status") is None:
            return None
        
        # 状态码映射
        state_map = {
            0: VehicleState.IDLE, 1: VehicleState.MOVING, 2: VehicleState.LOADING,
            3: VehicleState.UNLOADING, 4: VehicleState.CHARGING,
            5: VehicleState.EXECUTING, 99: VehicleState.ERROR,
        }
        
        raw_state = values.get(f"{pfx}.Status", 0)
        
        return VehicleStatus(
            vehicle_id=vehicle_id,
            state=state_map.get(raw_state, VehicleState.IDLE),
            x=float(values.get(f"{pfx}.Position.X") or 0),
            y=float(values.get(f"{pfx}.Position.Y") or 0),
            angle=float(values.get(f"{pfx}.Angle") or 0),
            speed=float(values.get(f"{pfx}.Speed") or 0),
            battery_level=float(values.get(f"{pfx}.Battery") or 100),
            current_node=str(int(values.get(f"{pfx}.CurrentStation") or 0)),
            target_node=str(int(values.get(f"{pfx}.TargetStation") or 0)),
            load_status=bool(values.get(f"{pfx}.LoadStatus")),
            error_code=int(values.get(f"{pfx}.ErrorCode") or 0),
            last_heartbeat=time.time(),
        )

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有 AGV 状态"""
        statuses = []
        for i in range(min(self._vehicle_count, 10)):
            vid = f"agv_{i+1:04d}"
            status = await self.get_status(vid)
            if status:
                statuses.append(status)
        return statuses

    # ==================== 高级功能 ====================

    async def health_check(self) -> Dict[str, Any]:
        """详细健康检查"""
        base_ok = self._connected
        
        # 尝试读取安全标签作为存活检测
        safety_ready = None
        if base_ok:
            try:
                safety_ready = await self.read_tag("Safety.SystemReady")
                if self.mode == "live" and safety_ready is None:
                    base_ok = False
            except Exception:
                base_ok = False
        
        uptime = time.time() - self._session_established_at if self._session_established_at else 0
        
        conn_info = {}
        if self.mode == "live":
            conn_info = {"host": self.plc_host, "port": self.plc_port}
        
        return {
            "connected": base_ok,
            "protocol": f"ethernet-ip-{self.mode}",
            "mode": self.mode,
            "connection_info": conn_info,
            "program_name": self.program_name,
            "uptime_sec": round(uptime, 1),
            "safety_system_ready": safety_ready,
            "metrics": self.metrics.to_dict(),
            "total_tags_mapped": len(self.tag_map),
            "simulated_agvs": self._vehicle_count if self.mode != "live" else None,
        }

    def discover_tags(self, pattern: str = "*") -> List[CipTagDefinition]:
        """
        发现匹配通配符模式的标签.
        
        Args:
            pattern: 通配符模式 (如 "AGV[0]*", "Conveyor*", "Safety*")
        
        Returns:
            匹配的 CipTagDefinition 列表
        """
        import fnmatch
        matched = [
            tag_def for name, tag_def in self.tag_map.items()
            if fnmatch.fnmatch(name, pattern)
        ]
        return sorted(matched, key=lambda t: t.name)

    def get_supported_protocols(self) -> List[str]:
        """支持的协议列表"""
        protocols = ["ethernet-ip", "cip"]
        if HAS_PYLOGIX:
            protocols.extend(["cip-live", "pylogix"])
        return protocols

    def get_metrics(self) -> CipHealthMetrics:
        """获取通信指标副本"""
        return self.metrics

    async def read_conveyor_status(self, conveyor_id: int = 0) -> Optional[Dict[str, Any]]:
        """
        读取单条输送线的完整状态.
        
        Returns:
            包含 Running/Speed/PhotoEye/MotorCurrent/FaultCode 的字典
        """
        pfx = f"Conveyor[{conveyor_id}]"
        tag_names = [
            f"{pfx}.Running", f"{pfx}.Speed", f"{pfx}.ActualSpeed",
            f"{pfx}.PhotoEye.In", f"{pfx}.PhotoEye.Out",
            f"{pfx}.MotorCurrent", f"{pfx}.FaultCode", f"{pfx}.AccumCount",
        ]
        values = await self.read_multiple_tags(tag_names)
        return values if any(v is not None for v in values()) else None

    async def read_safety_status(self) -> Dict[str, Any]:
        """读取安全联锁系统状态"""
        safety_tags = [k for k in self.tag_map.keys() if k.startswith("Safety.")]
        return await self.read_multiple_tags(safety_tags)

    async def emergency_stop_all(self) -> Dict[int, bool]:
        """通过 CIP 急停所有 AGV"""
        results: Dict[int, bool] = {}
        for i in range(min(self._vehicle_count, 10)):
            success = await self.write_tag(f"AGV[{i}].Cmd.Stop", True)
            results[i] = success
        return results

    # ==================== 内部工具方法 ====================

    def _resolve_tag_path(self, tag_name: str) -> str:
        """
        解析短标签名为完整 CIP 路径.
        
        "AGV[0].Status" → "Program:Main.AGV[0].Status"
        """
        if ":" in tag_name:  # 已经是完整路径
            return tag_name
        if self.program_name:
            return f"Program:{self.program_name}.{tag_name}"
        return tag_name

    def _parse_agv_index(self, vehicle_id: str) -> int:
        """解析 AGV ID 为数组索引"""
        if vehicle_id.startswith("agv_"):
            idx = int(vehicle_id.replace("agv_", "").lstrip("0") or "0") - 1
        elif vehicle_id.startswith("plc_agv_"):
            idx = int(vehicle_id.replace("plc_agv_", "").lstrip("0") or "0") - 1
        else:
            raise ValueError(f"Cannot parse AGV index from '{vehicle_id}'")
        if idx < 0 or idx >= 10:
            raise IndexError(f"AGV index {idx} out of range [0..9]")
        return idx

    def _scale_value(self, raw_value: Any, tag_def: CipTagDefinition) -> Any:
        """按缩放因子将原始值转为工程值"""
        if raw_value is None or tag_def.scale == 1.0:
            return raw_value
        try:
            return float(raw_value) * tag_def.scale
        except (TypeError, ValueError):
            return raw_value

    def _unscale_value(self, eng_value: Any, tag_def: CipTagDefinition) -> Any:
        """将工程值反缩放回 PLC 原始值"""
        if eng_value is None or tag_def.scale == 1.0:
            return eng_value
        try:
            return float(eng_value) / tag_def.scale
        except (TypeError, ValueError):
            return eng_value

    def _estimate_byte_size(self, tag_def: Optional[CipTagDefinition]) -> int:
        """估算标签字节数"""
        if not tag_def:
            return 4
        size_map = {
            CipDataType.BOOL: 1, CipDataType.SINT: 1, CipDataType.INT: 2,
            CipDataType.DINT: 4, CipDataType.LINT: 8, CipDataType.REAL: 4,
            CipDataType.LREAL: 8, CipDataType.STRING: 84,
            CipDataType.STRUCT: 32, CipDataType.ARRAY: 8 * (tag_def.array_size or 1),
        }
        return size_map.get(tag_def.data_type, 4)

    def _discover_agv_count(self) -> int:
        """尝试发现实际连接的 AGV 数量"""
        count = 0
        for i in range(10):
            try:
                val = self._plc.Read(f"Program:{self.program_name}.AGV[{i}].Status")
                if val is not None and val.Value is not None:
                    count += 1
                else:
                    break
            except Exception:
                break
        return max(count, 1)

    def _apply_simulated_command(self, agv_idx: int, command: VehicleCommand, params: Dict):
        """在模拟模式中更新状态以反映指令效果"""
        pfx = f"AGV[{agv_idx}]"
        
        if command == VehicleCommand.MOVE:
            self._sim_tags[f"{pfx}.Status"] = 1  # moving
            self._sim_tags[f"{pfx}.Speed"] = random.uniform(0.5, 2.0)
        elif command == VehicleCommand.STOP:
            self._sim_tags[f"{pfx}.Status"] = 0  # idle
            self._sim_tags[f"{pfx}.Speed"] = 0.0
        elif command == VehicleCommand.CHARGE:
            self._sim_tags[f"{pfx}.Status"] = 4  # charging
        elif command == VehicleCommand.LOAD:
            self._sim_tags[f"{pfx}.LoadStatus"] = True
        elif command == VehicleCommand.UNLOAD:
            self._sim_tags[f"{pfx}.LoadStatus"] = False
