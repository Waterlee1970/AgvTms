"""
OPC UA 协议适配器 — AGV-TMS 工业设备对接层 (P0-05 增强版)

基于 OPC UA (IEC 62541) 工业通信标准, 实现与 PLC/SCADA/HMI 等工业设备的统一对接。

支持三种模式:
  1. simulation: 内存模拟, 无需外部依赖 (开发/测试用)
  2. live:       连接真实 OPC UA Server (需 asyncua)
  3. record:     回放模式, 从录制文件重放 (演示/调试用)

完整功能:
  - OPC UA 节点读写 (Read/Write/Browse)
  - 方法调用 (Method Call) — 支持带参数的复杂操作
  - 数据变更订阅 (Subscription/Monitoring)
  - 节点空间自动发现与映射
  - 安全认证 (匿名 / 用户名密码 / 证书)
  - 输送线 + AGV + PLC 设备的默认节点映射表
  - 通信指标统计 (读/写/错误率/延迟)

协议标准:
  - OPC UA Part 4: Services (I EC 62541-4)
  - OPC UA Part 5: Information Model (IEC 62541-5)

参考:
  - asyncua 文档: https://asyncua.readthedocs.io/
  - OPC Foundation: https://opcfoundation.org/
  - 海康 RCS-2000 OPC UA 接口规范
  - ModbusVehicleAdapter — 同级协议适配器风格参考

使用示例:
    # 模拟模式
    adapter = OpcUaAdapter(mode='simulation', num_sim_agvs=10)
    await adapter.initialize()
    await adapter.start()
    status = await adapter.get_agv_status('sim_agv_001')

    # 实时模式 (连接真实PLC)
    adapter = OpcUaAdapter(
        mode='live',
        server_url='opc.tcp://192.168.1.100:4840',
        security_mode='SignAndEncrypt',   # 签名+加密
        username='operator', password='op123'
    )
    await adapter.initialize()
    val = await adapter.read_node('ns=2;s=Machine1.Temperature')
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable, Tuple, Set

logger = logging.getLogger(__name__)


# ==================== 可选导入 ====================

try:
    from asyncua import Client, ua
    from asyncua.common.node import Node
    HAS_ASYNCUA = True
except ImportError:
    HAS_ASYNCUA = False
    ua = None
    Node = None
    logger.info(
        "asyncua not installed. Install with: pip install asyncua\n"
        "OPC UA live mode will be unavailable; simulation mode still works."
    )


# ==================== 数据模型 ====================

class AgvCommand(str, Enum):
    """AGV 控制指令类型 (OPC UA Method 对应)"""
    MOVE = "move"
    STOP = "stop"
    RESUME = "resume"
    CHARGE = "charge"
    LOAD = "load"
    UNLOAD = "unload"
    CANCEL_TASK = "cancel_task"


class AgvState(str, Enum):
    """AGV 运行状态 (对应 OPC UA 状态机模型)"""
    IDLE = "idle"
    MOVING = "moving"
    LOADING = "loading"
    UNLOADING = "unloading"
    CHARGING = "charging"
    EXECUTING = "executing"
    ERROR = "error"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class OpcSecurityMode(str, Enum):
    """OPC UA 安全策略"""
    NONE = "None"                    # 无安全性 (仅测试环境)
    SIGN = "Sign"                    # 仅签名
    SIGN_ENCRYPT = "SignAndEncrypt"  # 签名 + 加密 (生产推荐)


class OpcSecurityPolicy(str, Enum):
    """OPC UA 安全加密策略"""
    NONE = "http://opcfoundation.org/UA/SecurityPolicy#None"
    BASIC128RSA15 = "http://opcfoundation.org/UA/SecurityPolicy#Basic128Rsa15"
    BASIC256 = "http://opcfoundation.org/UA/SecurityPolicy#Basic256"
    BASIC256SHA256 = "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256"


class OpcUaDataType(str, Enum):
    """OPC UA 内置数据类型 (对应 NodeClass Variable)"""
    BOOLEAN = "Boolean"
    SBYTE = "SByte"
    BYTE = "Byte"
    INT16 = "Int16"
    UINT16 = "UInt16"
    INT32 = "Int32"
    UINT32 = "UInt32"
    INT64 = "Int64"
    UINT64 = "UInt64"
    FLOAT = "Float"
    DOUBLE = "Double"
    STRING = "String"
    DATETIME = "DateTime"
    NODE_ID = "NodeId"
    LOCALIZED_TEXT = "LocalizedText"
    BYTE_STRING = "ByteString"


@dataclass
class AgvDeviceStatus:
    """AGV 设备完整状态 (OPC UA Node 映射)"""
    agv_id: str
    state: AgvState = AgvState.IDLE
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    speed: float = 0.0
    battery_level: float = 100.0
    current_node_id: str = ""
    target_node_id: str = ""
    load_status: bool = False
    error_code: int = 0
    error_message: str = ""
    last_heartbeat: float = field(default_factory=time.time)
    odometer: float = 0.0
    operating_hours: float = 0.0


@dataclass
class OpcUaNodeDefinition:
    """
    OPC UA 节点定义 — 描述一个可读写的 OPC UA 变量或方法.

    node_id 使用标准 OPC UA NodeId 格式:
      - 字符串: ns=2;s=Machine1.Status
      - 数字:   ns=2;i=1001
      - GUID:   ns=2;g=B83DD6F7-3BB4-4D1D-9E07-E8DDC29B33B5
    """
    node_id: str                          # OPC UA NodeId (必填)
    name: str = ""                        # 显示名称
    data_type: OpcUaDataType = OpcUaDataType.INT32
    description: str = ""
    writable: bool = False                # 是否可写
    unit: str = ""                        # 单位
    scale: float = 1.0                   # 缩放因子
    offset: float = 0.0                  # 偏移量
    # 用于方法的字段
    is_method: bool = False              # 是否为方法节点
    input_args: List[Dict[str, Any]] = field(default_factory=list)   # 方法输入参数
    output_args: List[Dict[str, Any]] = field(default_factory=list)  # 方法输出参数


@dataclass
class OpcUaCommandResult:
    """OPC UA 指令执行结果"""
    success: bool
    command: str
    agv_id: str
    message: str = ""
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OpcUaMetrics:
    """通信指标统计"""
    read_count: int = 0
    write_count: int = 0
    method_call_count: int = 0
    error_count: int = 0
    subscribe_count: int = 0
    total_bytes_rx: int = 0
    total_bytes_tx: int = 0
    # 延迟统计 (ms)
    latencies: List[float] = field(default_factory=list)
    _latency_samples_max: int = 1000

    def record_read(self, latency_ms: float = 0, bytes_count: int = 0):
        self.read_count += 1
        if latency_ms > 0:
            self.latencies.append(latency_ms)
            if len(self.latencies) > self._latency_samples_max:
                self.latencies.pop(0)
        self.total_bytes_rx += bytes_count

    def record_write(self, latency_ms: float = 0, bytes_count: int = 0):
        self.write_count += 1
        if latency_ms > 0:
            self.latencies.append(latency_ms)
            if len(self.latencies) > self._latency_samples_max:
                self.latencies.pop(0)
        self.total_bytes_tx += bytes_count

    def record_method_call(self, latency_ms: float = 0, success: bool = True):
        self.method_call_count += 1
        if not success:
            self.error_count += 1
        if latency_ms > 0:
            self.latencies.append(latency_ms)

    def record_error(self):
        self.error_count += 1

    @property
    def error_rate_pct(self) -> float:
        total = self.read_count + self.write_count + self.method_call_count
        return (self.error_count / max(total, 1)) * 100

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)

    @property
    def p50_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[len(s) // 2]

    @property
    def p99_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "read_count": self.read_count,
            "write_count": self.write_count,
            "method_call_count": self.method_call_count,
            "error_count": self.error_count,
            "subscribe_count": self.subscribe_count,
            "total_bytes_rx": self.total_bytes_rx,
            "total_bytes_tx": self.total_bytes_tx,
            "error_rate_pct": round(self.error_rate_pct, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
        }


# ==================== 默认 OPC UA 节点映射表 ====================
#
# 参考 OPC UA for Machinery (OPC UA Part 102) 和
# OPC UA for Machinery Companion Specification (OPC 40000-1).
# 同时兼容常见的 PLC 厂商命名习惯.
#
# 节点命名约定:
#   Objects.{DeviceType}[{index}].{Property}
#   例如: Objects.AGVs[0].Status, Objects.Conveyors[0].Speed

DEFAULT_OPCUA_NODE_MAP: Dict[str, OpcUaNodeDefinition] = {
    # ================================================================
    # AGV 状态节点 (每台AGV一组, [i] 为索引)
    # ================================================================

    # --- 核心状态 ---
    f"AGV[0].Status": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Status",
        name="AGV Status", data_type=OpcUaDataType.UINT16,
        description="AGV运行状态码 (0=Idle,1=Moving,2=Loading,3=Unloading,"
                     "4=Charging,5=Error,6=Maintenance)",
    ),
    f"AGV[0].Position.X": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Position_X",
        name="X Position", data_type=OpcUaDataType.FLOAT,
        unit="m", scale=1.0, description="X坐标(米)",
    ),
    f"AGV[0].Position.Y": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Position_Y",
        name="Y Position", data_type=OpcUaDataType.FLOAT,
        unit="m", scale=1.0, description="Y坐标(米)",
    ),
    f"AGV[0].Angle": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Angle",
        name="Heading Angle", data_type=OpcUaDataType.FLOAT,
        unit="deg", scale=1.0, description="朝向角度(度)",
    ),
    f"AGV[0].Speed": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Speed",
        name="Current Speed", data_type=OpcUaDataType.FLOAT,
        unit="m/s", scale=1.0, description="当前速度",
    ),

    # --- 电量与里程 ---
    f"AGV[0].BatteryLevel": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].BatteryLevel",
        name="Battery Level", data_type=OpcUaDataType.UINT16,
        scale=0.1, unit="%", description="电量百分比(0-1000→0-100%)",
    ),
    f"AGV[0].Odometer": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Odometer",
        name="Odometer", data_type=OpcUaDataType.DOUBLE,
        scale=0.001, unit="km", description="总里程",
    ),
    f"AGV[0].OperatingHours": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].OperatingHours",
        name="Operating Hours", data_type=OpcUaDataType.DOUBLE,
        unit="h", description="累计运行时间(小时)",
    ),

    # --- 导航状态 ---
    f"AGV[0].CurrentStation": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].CurrentStation",
        name="Current Station", data_type=OpcUaDataType.STRING,
        description="当前所在工位/站点ID",
    ),
    f"AGV[0].TargetStation": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].TargetStation",
        name="Target Station", data_type=OpcUaDataType.STRING,
        description="目标工位/站点ID",
    ),
    f"AGV[0].LoadStatus": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].LoadStatus",
        name="Load Status", data_type=OpcUaDataType.BOOLEAN,
        description="是否载货",
    ),

    # --- 错误信息 ---
    f"AGV[0].ErrorCode": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].ErrorCode",
        name="Error Code", data_type=OpcUaDataType.UINT16,
        description="当前错误码(0=正常)",
    ),
    f"AGV[0].ErrorMessage": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].ErrorMessage",
        name="Error Message", data_type=OpcUaDataType.STRING,
        description="错误描述文本",
    ),

    # --- 心跳与时间戳 ---
    f"AGV[0].Heartbeat": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Heartbeat",
        name="Heartbeat", data_type=OpcUaDataType.DATETIME,
        description="最后心跳时间戳",
    ),
    f"AGV[0].LastUpdateTime": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].LastUpdateTime",
        name="Last Update Time", data_type=OpcUaDataType.DATETIME,
        description="最后状态更新时间",

    ),

    # ================================================================
    # AGV 控制方法节点 (Method Nodes — 可调用)
    # ================================================================
    f"AGV[0].Cmd.MoveTo": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Cmd_MoveTo",
        name="Move To Command", is_method=True,
        input_args=[
            {"name": "targetStation", "type": "String", "description": "目标站点"},
            {"name": "speedFactor", "type": "Float", "description": "速度因子(0.1-1.0)"},
        ],
        output_args=[{"name": "result", "type": "Boolean", "description": "是否成功"}],
    ),
    f"AGV[0].Cmd.Stop": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Cmd_Stop",
        name="Stop Command", is_method=True,
        input_args=[{"name": "emergency", "type": "Boolean", "description": "紧急停止"}],
        output_args=[{"name": "result", "type": "Boolean"}],
    ),
    f"AGV[0].Cmd.Resume": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Cmd_Resume",
        name="Resume Command", is_method=True,
        output_args=[{"name": "result", "type": "Boolean"}],
    ),
    f"AGV[0].Cmd.Load": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Cmd_Load",
        name="Load Command", is_method=True,
        input_args=[
            {"name": "cargoId", "type": "String", "description": "货物标识"},
            {"name": "position", "type": "UInt16", "description": "装载位置"},
        ],
        output_args=[{"name": "result", "type": "Boolean"}],
    ),
    f"AGV[0].Cmd.Unload": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Cmd_Unload",
        name="Unload Command", is_method=True,
        output_args=[{"name": "result", "type": "Boolean"}],
    ),
    f"AGV[0].Cmd.Charge": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.AGVs[0].Cmd_Charge",
        name="Charge Command", is_method=True,
        input_args=[{"name": "chargingStationId", "type": "String"}],
        output_args=[{"name": "result", "type": "Boolean"}],
    ),

    # ================================================================
    # 输送线 (Conveyor) 节点
    # ================================================================
    f"Conveyor[0].Running": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].Running",
        name="Conveyor Running", data_type=OpcUaDataType.BOOLEAN,
        writable=True, description="输送线运行状态",
    ),
    f"Conveyor[0].Speed": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].Speed",
        name="Conveyor Speed", data_type=OpcUaDataType.FLOAT,
        unit="m/s", writable=True, scale=1.0, description="输送带速度",
    ),
    f"Conveyor[0].PhotoEye": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].PhotoEye",
        name="Photo Eye Sensor", data_type=OpcUaDataType.BOOLEAN,
        description="光电传感器状态(有物=True)",
    ),
    f"Conveyor[0].MotorCurrent": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].MotorCurrent",
        name="Motor Current", data_type=OpcUaDataType.FLOAT,
        unit="A", description="电机电流",
    ),
    f"Conveyor[0].FaultCode": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].FaultCode",
        name="Fault Code", data_type=OpcUaDataType.UINT16,
        description="输送线故障码(0=正常)",
    ),
    f"Conveyor[0].ThroughputCount": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].ThroughputCount",
        name="Throughput Count", data_type=OpcUaDataType.UINT32,
        description="累计通过物品数",
    ),
    f"Conveyor[0].Length": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].Length",
        name="Conveyor Length", data_type=OpcUaDataType.FLOAT,
        unit="m", description="输送带长度",
    ),

    # 输送线控制方法
    f"Conveyor[0].Cmd.Start": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].Cmd_Start",
        name="Start Conveyor", is_method=True,
        input_args=[{"name": "speed", "type": "Float", "description": "启动速度(m/s)"}],
        output_args=[{"name": "result", "type": "Boolean"}],
    ),
    f"Conveyor[0].Cmd.Stop": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Conveyors[0].Cmd_Stop",
        name="Stop Conveyor", is_method=True,
        output_args=[{"name": "result", "type": "Boolean"}],
    ),

    # ================================================================
    # 安全联锁 (Safety Interlock) 节点
    # ================================================================
    "Safety.EmergencyStop": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Safety.EmergencyStop",
        name="Emergency Stop", data_type=OpcUaDataType.BOOLEAN,
        writable=False, description="急停按钮状态(按下=True)",
    ),
    "Safety.LightCurtainOK": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Safety.LightCurtainOK",
        name="Light Curtain OK", data_type=OpcUaDataType.BOOLEAN,
        description="光幕正常(未遮挡=True)",
    ),
    "Safety.DoorOpen": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Safety.DoorOpen",
        name="Safety Door Open", data_type=OpcUaDataType.BOOLEAN,
        description="安全门打开状态",
    ),
    "Safety.ZoneEnabled": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Safety.ZoneEnabled",
        name="Zone Enabled", data_type=OpcUaDataType.BOOLEAN,
        writable=True, description="区域使能(允许进入)",
    ),
    "Safety.Reset": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.Safety.Cmd_Reset",
        name="Reset Safety", is_method=True,
        output_args=[{"name": "result", "type": "Boolean"}],
        description="复位安全系统",
    ),

    # ================================================================
    # PLC 系统级节点
    # ================================================================
    "System.CycleCounter": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.System.CycleCounter",
        name="Cycle Counter", data_type=OpcUaDataType.UINT64,
        description="PLC扫描周期计数器",
    ),
    "System.ScanTimeMs": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.System.ScanTimeMs",
        name="Scan Time", data_type=OpcUaDataType.UINT32,
        unit="ms", description="当前扫描周期时间",
    ),
    "System.UptimeSeconds": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.System.UptimeSeconds",
        name="System Uptime", data_type=OpcUaDataType.UINT64,
        unit="s", description="系统运行时长",
    ),
    "System.Mode": OpcUaNodeDefinition(
        node_id="ns=2;s=Objects.System.Mode",
        name="System Mode", data_type=OpcUaDataType.UINT16,
        description="运行模式(0=STOP,1=RUN,2=TEST,3=MAINTENANCE)",
    ),
}


# ==================== 回调类型 ====================

StatusCallback = Callable[[AgvDeviceStatus], Awaitable[None]]
AlertCallback = Callable[[str, str, Dict], Awaitable[None]]  # id, alert_type, detail
DataChangeCallback = Callable[[str, Any, Any], Awaitable[None]]  # node_id, old_val, new_val


# ==================== 模拟器 ====================

class _SimulatedOpcUaServer:
    """
    内存模拟 OPC UA Server — 完整仿真引擎.

    模拟行为:
      - 多台 AGV 在地图节点间匀速移动 (含路径插值)
      - 电量随移动消耗, 低电量告警, 充电恢复
      - 输送线节拍模拟 (光电传感器触发, 吞吐计数)
      - 安全联锁状态模拟 (急停/光幕/门)
      - 随机故障注入 (可配置概率)
      - 心跳信号周期发送
      - OPC UA 节点空间模拟 (支持 browse/read/write)
    """

    def __init__(
        self,
        num_agvs: int = 10,
        num_conveyors: int = 3,
        map_nodes: Optional[List[Dict]] = None,
        move_speed: float = 1.5,
        battery_drain_rate: float = 0.5,
        charge_rate: float = 5.0,
        heartbeat_interval: float = 1.0,
        fault_probability: float = 0.001,
    ):
        self.num_agvs = num_agvs
        self.num_conveyors = num_conveyors
        self.map_nodes = map_nodes or []
        self.move_speed = move_speed
        self.battery_drain_rate = battery_drain_rate
        self.charge_rate = charge_rate
        self.heartbeat_interval = heartbeat_interval
        self.fault_probability = fault_probability

        # AGV 设备池
        self.agvs: Dict[str, AgvDeviceStatus] = {}

        # 输送线状态池
        self.conveyor_states: Dict[int, Dict[str, Any]] = {}

        # 安全联锁状态
        self.safety_state: Dict[str, bool] = {
            "EmergencyStop": False,
            "LightCurtainOK": True,
            "DoorOpen": False,
            "ZoneEnabled": True,
        }

        # PLC 系统状态
        self.system_state: Dict[str, Any] = {
            "CycleCounter": 0,
            "ScanTimeMs": 10,
            "UptimeSeconds": 0,
            "Mode": 1,  # RUN
        }

        # 模拟节点值存储 (模拟 OPC UA 地址空间)
        self._node_values: Dict[str, Any] = {}
        self._callbacks: List[StatusCallback] = []
        self._alert_callbacks: List[AlertCallback] = []
        self._data_change_callbacks: List[Tuple[str, DataChangeCallback]] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._start_time: float = 0

        self._initialize_devices()
        self._initialize_node_store()

    def _initialize_devices(self):
        """初始化所有模拟设备"""
        node_positions = [(n.get("x", 0), n.get("y", 0)) for n in self.map_nodes]

        # 初始化 AGV
        for i in range(self.num_agvs):
            agv_id = f"sim_agv_{i+1:03d}"
            if node_positions and i < len(node_positions):
                px, py = node_positions[i]
            else:
                px, py = random.uniform(0, 100), random.uniform(0, 100)

            self.agvs[agv_id] = AgvDeviceStatus(
                agv_id=agv_id,
                state=AgvState.IDLE,
                x=px, y=py,
                battery_level=random.uniform(60, 100),
                current_node_id=self._nearest_node(px, py),
            )

        # 初始化输送线
        for i in range(self.num_conveyors):
            self.conveyor_states[i] = {
                "running": random.choice([True, False]),
                "speed": random.uniform(0.5, 2.0),
                "photo_eye": False,
                "motor_current": 0.0 if not random.choice([True, False]) else random.uniform(1.0, 5.0),
                "fault_code": 0,
                "throughput_count": random.randint(0, 500),
                "length": random.uniform(5.0, 30.0),
            }

    def _initialize_node_store(self):
        """初始化模拟 OPC UA 节点存储"""
        # AGV 节点初始化
        for agv in self.agvs.values():
            idx = int(agv.agv_id.split("_")[-1])
            prefix = f"AGV[{min(idx-1, 9)}]"
            self._node_values[f"{prefix}.Status"] = 0  # Idle
            self._node_values[f"{prefix}.Position.X"] = round(agv.x, 3)
            self._node_values[f"{prefix}.Position.Y"] = round(agv.y, 3)
            self._node_values[f"{prefix}.Angle"] = round(agv.angle, 2)
            self._node_values[f"{prefix}.Speed"] = round(agv.speed, 3)
            self._node_values[f"{prefix}.BatteryLevel"] = int(agv.battery_level * 10)
            self._node_values[f"{prefix}.Odometer"] = round(agv.odometer, 3)
            self._node_values[f"{prefix}.OperatingHours"] = round(agv.operating_hours, 2)
            self._node_values[f"{prefix}.CurrentStation"] = agv.current_node_id
            self._node_values[f"{prefix}.TargetStation"] = agv.target_node_id
            self._node_values[f"{prefix}.LoadStatus"] = agv.load_status
            self._node_values[f"{prefix}.ErrorCode"] = agv.error_code
            self._node_values[f"{prefix}.ErrorMessage"] = agv.error_message

        # 输送线节点初始化
        for ci, cs in self.conveyor_states.items():
            prefix = f"Conveyor[{ci}]"
            self._node_values[f"{prefix}.Running"] = cs["running"]
            self._node_values[f"{prefix}.Speed"] = round(cs["speed"], 3)
            self._node_values[f"{prefix}.PhotoEye"] = cs["photo_eye"]
            self._node_values[f"{prefix}.MotorCurrent"] = round(cs["motor_current"], 2)
            self._node_values[f"{prefix}.FaultCode"] = cs["fault_code"]
            self._node_values[f"{prefix}.ThroughputCount"] = cs["throughput_count"]
            self._node_values[f"{prefix}.Length"] = round(cs["length"], 2)

        # 安全节点初始化
        for key, val in self.safety_state.items():
            self._node_values[f"Safety.{key}"] = val

        # 系统节点初始化
        for key, val in self.system_state.items():
            self._node_values[f"System.{key}"] = val

    def _nearest_node(self, x: float, y: float) -> str:
        """查找最近地图节点"""
        best_dist, best_id = float('inf'), ''
        for n in self.map_nodes:
            d = ((n.get("x", 0) - x) ** 2 + (n.get("y", 0) - y) ** 2) ** 0.5
            if d < best_dist:
                best_dist, best_id = d, n.get("id", "")
        return best_id or f"node_{int(x)}_{int(y)}"

    # ---- 回调注册 ----

    def on_status_update(self, callback: StatusCallback):
        self._callbacks.append(callback)

    def on_alert(self, callback: AlertCallback):
        self._alert_callbacks.append(callback)

    def on_data_change(self, node_name: str, callback: DataChangeCallback):
        self._data_change_callbacks.append((node_name, callback))

    # ---- 生命周期 ----

    async def start(self):
        """启动模拟引擎"""
        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._simulation_loop())
        logger.info(
            f"[OPC-UA-Sim] Started: {self.num_agvs} AGVs + "
            f"{self.num_conveyors} Conveyors, tick={self.heartbeat_interval}s"
        )

    async def stop(self):
        """停止模拟引擎"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[OPC-UA-Sim] Stopped")

    # ---- 指令处理 ----

    async def send_command(
        self, agv_id: str, command: AgvCommand, params: Dict[str, Any] = None
    ) -> OpcUaCommandResult:
        """下发控制指令到模拟 AGV"""
        agv = self.agvs.get(agv_id)
        if not agv:
            return OpcUaCommandResult(False, command.value, agv_id, "AGV not found")

        params = params or {}

        try:
            if command == AgvCommand.MOVE:
                target = params.get("target", "")
                speed_factor = params.get("speed_factor", 1.0)
                agv.target_node_id = target
                agv.state = AgvState.MOVING
                agv.speed = self.move_speed * max(0.1, min(speed_factor, 1.0))
                idx = int(agv_id.split("_")[-1])
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 1  # Moving
                self._node_values[f"AGV[{min(idx-1,9)}].TargetStation"] = target
                self._node_values[f"AGV[{min(idx-1,9)}].Speed"] = round(agv.speed, 3)
                return OpcUaCommandResult(True, command.value, agv_id, f"Moving to {target}")

            elif command == AgvCommand.STOP:
                agv.state = AgvState.IDLE
                agv.speed = 0.0
                agv.target_node_id = ""
                idx = int(agv_id.split("_")[-1])
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 0
                self._node_values[f"AGV[{min(idx-1,9)}].Speed"] = 0.0
                self._node_values[f"AGV[{min(idx-1,9)}].TargetStation"] = ""
                return OpcUaCommandResult(True, command.value, agv_id, "Stopped")

            elif command == AgvCommand.RESUME:
                if agv.state == AgvState.IDLE and agv.target_node_id:
                    agv.state = AgvState.MOVING
                    agv.speed = self.move_speed
                elif agv.state == AgvState.ERROR:
                    agv.state = AgvState.IDLE
                    agv.error_code = 0
                    agv.error_message = ""
                return OpcUaCommandResult(True, command.value, agv_id, "Resumed")

            elif command == AgvCommand.CHARGE:
                agv.state = AgvState.CHARGING
                agv.speed = 0.0
                idx = int(agv_id.split("_")[-1])
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 4  # Charging
                self._node_values[f"AGV[{min(idx-1,9)}].Speed"] = 0.0
                return OpcUaCommandResult(True, command.value, agv_id, "Charging started")

            elif command == AgvCommand.LOAD:
                agv.load_status = True
                agv.state = AgvState.LOADING
                idx = int(agv_id.split("_")[-1])
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 2  # Loading
                self._node_values[f"AGV[{min(idx-1,9)}].LoadStatus"] = True
                await asyncio.sleep(2.0)  # 模拟装货耗时
                agv.state = AgvState.IDLE
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 0
                return OpcUaCommandResult(True, command.value, agv_id, "Loaded")

            elif command == AgvCommand.UNLOAD:
                agv.load_status = False
                agv.state = AgvState.UNLOADING
                idx = int(agv_id.split("_")[-1])
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 3  # Unloading
                self._node_values[f"AGV[{min(idx-1,9)}].LoadStatus"] = False
                await asyncio.sleep(2.0)
                agv.state = AgvState.IDLE
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 0
                return OpcUaCommandResult(True, command.value, agv_id, "Unloaded")

            elif command == AgvCommand.CANCEL_TASK:
                agv.state = AgvState.IDLE
                agv.target_node_id = ""
                agv.speed = 0.0
                idx = int(agv_id.split("_")[-1])
                self._node_values[f"AGV[{min(idx-1,9)}].Status"] = 0
                self._node_values[f"AGV[{min(idx-1,9)}].TargetStation"] = ""
                self._node_values[f"AGV[{min(idx-1,9)}].Speed"] = 0.0
                return OpcUaCommandResult(True, command.value, agv_id, "Task cancelled")

            return OpcUaCommandResult(False, command.value, agv_id, "Unknown command")
        except Exception as e:
            return OpcUaCommandResult(False, command.value, agv_id, str(e))

    # ---- 状态查询 ----

    def get_all_statuses(self) -> Dict[str, AgvDeviceStatus]:
        """获取所有 AGV 状态快照"""
        return dict(self.agvs)

    def get_status(self, agv_id: str) -> Optional[AgvDeviceStatus]:
        """获取单个 AGV 状态"""
        return self.agvs.get(agv_id)

    # ---- 节点操作 (模拟 OPC UA API) ----

    def read_node_value(self, node_name: str) -> Any:
        """读取节点值"""
        return self._node_values.get(node_name)

    def write_node_value(self, node_name: str, value: Any) -> bool:
        """写入节点值"""
        old_value = self._node_values.get(node_name)
        self._node_values[node_name] = value
        # 触发数据变化回调
        for pattern, cb in self._data_change_callbacks:
            if pattern == node_name or "*" in pattern:
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(cb(node_name, old_value, value))
                except Exception:
                    pass
        return True

    def browse_nodes(self, pattern: str = "*") -> List[Dict[str, Any]]:
        """浏览节点空间 (支持通配符匹配)"""
        import fnmatch
        results = []
        for name, value in sorted(self._node_values.items()):
            if fnmatch.fnmatch(name, pattern):
                results.append({
                    "name": name,
                    "value": value,
                    "type": type(value).__name__,
                    "writable": any(
                        nd.writable and nd.name
                        for nd in DEFAULT_OPCUA_NODE_MAP.values()
                        if name.endswith(nd.name.split()[-1]) or name == nd.name
                    )
                })
        return results

    def call_method(self, method_name: str, args: Optional[List[Any]] = None) -> Any:
        """调用方法节点"""
        args = args or []

        # 解析方法名称 → 执行对应操作
        if ".Cmd_MoveTo" in method_name or ".move" in method_name.lower():
            target = args[0] if len(args) > 0 else ""
            # 触发对应AGV的MOVE
            for agv_id in list(self.agvs.keys())[:1]:  # 简化：取第一台
                # 这里只记录，不实际执行（实际执行通过send_command）
                pass
            return True

        elif ".Cmd_Start" in method_name or ("conveyor" in method_name.lower() and "start" in method_name.lower()):
            # 启动输送线
            speed = args[0] if len(args) > 0 else 1.0
            for ci in self.conveyor_states:
                self.conveyor_states[ci]["running"] = True
                self.conveyor_states[ci]["speed"] = speed
                self._node_values[f"Conveyor[{ci}].Running"] = True
                self._node_values[f"Conveyor[{ci}].Speed"] = speed
            return True

        elif ".Cmd_Stop" in method_name or ("conveyor" in method_name.lower() and "stop" in method_name.lower()):
            for ci in self.conveyor_states:
                self.conveyor_states[ci]["running"] = False
                self._node_values[f"Conveyor[{ci}].Running"] = False
            return True

        elif ".Cmd_Reset" in method_name or "reset" in method_name.lower():
            self.safety_state = {k: (True if k in ("LightCurtainOK", "ZoneEnabled") else False)
                                for k in self.safety_state}
            for key, val in self.safety_state.items():
                self._node_values[f"Safety.{key}"] = val
            return True

        logger.debug("[OPC-UA-Sim] Method call: %s(%s)", method_name, args)
        return True  # 默认返回成功

    def get_conveyor_state(self, conveyor_id: int) -> Optional[Dict[str, Any]]:
        """获取输送线状态"""
        return self.conveyor_states.get(conveyor_id)

    def get_safety_state(self) -> Dict[str, bool]:
        """获取安全联锁状态"""
        return dict(self.safety_state)

    def get_system_state(self) -> Dict[str, Any]:
        """获取系统状态"""
        return dict(self.system_state)

    # ---- 主仿真循环 ----

    async def _simulation_loop(self):
        """主仿真循环 — 每个 tick 更新所有设备状态"""
        while self._running:
            now = time.time()
            dt = self.heartbeat_interval / 60.0  # 转分钟

            # 更新系统计时
            elapsed = now - self._start_time
            self.system_state["UptimeSeconds"] = int(elapsed)
            self.system_state["CycleCounter"] += 1
            self._node_values["System.UptimeSeconds"] = int(elapsed)
            self._node_values["System.CycleCounter"] = self.system_state["CycleCounter"]

            # === 更新 AGV 状态 ===
            for agv in self.agvs.values():
                idx = int(agv.agv_id.split("_")[-1])
                prefix_idx = min(idx - 1, 9)
                prefix = f"AGV[{prefix_idx}]"

                if agv.state == AgvState.MOVING:
                    # 向目标位置移动 (匀速线性插值)
                    if agv.target_node_id:
                        target_node = next(
                            (n for n in self.map_nodes if n.get("id") == agv.target_node_id), None
                        )
                        if target_node:
                            tx, ty = target_node.get("x", 0), target_node.get("y", 0)
                            dx, dy = tx - agv.x, ty - agv.y
                            dist = (dx ** 2 + dy ** 2) ** 0.5
                            step = agv.speed * self.heartbeat_interval

                            if dist <= step:
                                # 到达目标
                                agv.x, agv.y = tx, ty
                                agv.current_node_id = agv.target_node_id
                                agv.state = AgvState.IDLE
                                agv.speed = 0.0
                                agv.target_node_id = ""

                                self._node_values[f"{prefix}.Status"] = 0  # Idle
                                self._node_values[f"{prefix}.Position.X"] = round(tx, 3)
                                self._node_values[f"{prefix}.Position.Y"] = round(ty, 3)
                                self._node_values[f"{prefix}.CurrentStation"] = agv.current_node_id
                                self._node_values[f"{prefix}.TargetStation"] = ""
                                self._node_values[f"{prefix}.Speed"] = 0.0
                            else:
                                # 移动中
                                ratio = step / max(dist, 0.001)
                                agv.x += dx * ratio
                                agv.y += dy * ratio
                                agv.angle = math.degrees(math.atan2(dy, dx))

                                self._node_values[f"{prefix}.Position.X"] = round(agv.x, 3)
                                self._node_values[f"{prefix}.Position.Y"] = round(agv.y, 3)
                                self._node_values[f"{prefix}.Angle"] = round(agv.angle, 2)

                    # 电量消耗
                    agv.battery_level = max(0, agv.battery_level - self.battery_drain_rate * dt)
                    agv.odometer += agv.speed * self.heartbeat_interval
                    agv.operating_hours += self.heartbeat_interval / 3600.0

                    self._node_values[f"{prefix}.BatteryLevel"] = int(agv.battery_level * 10)
                    self._node_values[f"{prefix}.Odometer"] = round(agv.odometer, 3)
                    self._node_values[f"{prefix}.OperatingHours"] = round(agv.operating_hours, 2)

                elif agv.state == AgvState.CHARGING:
                    # 充电恢复
                    agv.battery_level = min(100, agv.battery_level + self.charge_rate * dt)
                    self._node_values[f"{prefix}.BatteryLevel"] = int(agv.battery_level * 10)
                    if agv.battery_level >= 99.5:
                        agv.state = AgvState.IDLE
                        self._node_values[f"{prefix}.Status"] = 0

                # 低电量告警 (< 20%)
                if agv.battery_level < 20.0 and agv.state not in (AgvState.CHARGING, AgvState.ERROR):
                    for cb in self._alert_callbacks:
                        try:
                            await cb(agv.agv_id, "low_battery", {"level": agv.battery_level})
                        except Exception as e:
                            logger.debug(f"[OPC-UA-Sim] Alert callback error: {e}")

                # 随机故障注入
                if self.fault_probability > 0 and random.random() < self.fault_probability:
                    agv.state = AgvState.ERROR
                    agv.error_code = random.randint(1000, 9999)
                    agv.error_message = "Simulated fault injection"
                    self._node_values[f"{prefix}.Status"] = 5  # Error
                    self._node_values[f"{prefix}.ErrorCode"] = agv.error_code
                    self._node_values[f"{prefix}.ErrorMessage"] = agv.error_message
                    for cb in self._alert_callbacks:
                        try:
                            await cb(agv.agv_id, "fault", {"code": agv.error_code, "msg": agv.error_message})
                        except Exception:
                            pass

                # 更新心跳
                agv.last_heartbeat = now
                self._node_values[f"{prefix}.Heartbeat"] = now

            # === 更新输送线状态 ===
            for ci, cs in self.conveyor_states.items():
                prefix = f"Conveyor[{ci}]"
                if cs["running"]:
                    # 运行中的输送线: 光电传感器随机触发 (模拟物品经过)
                    if random.random() < 0.1:  # 10% 概率/tick 有物经过
                        cs["photo_eye"] = True
                        cs["throughput_count"] += 1
                    else:
                        cs["photo_eye"] = False

                    # 电机电流波动
                    cs["motor_current"] = max(0.5, cs["motor_current"] + random.uniform(-0.3, 0.3))
                else:
                    cs["motor_current"] = max(0, cs["motor_current"] * 0.9)  # 衰减至0

                self._node_values[f"{prefix}.PhotoEye"] = cs["photo_eye"]
                self._node_values[f"{prefix}.MotorCurrent"] = round(cs["motor_current"], 2)
                self._node_values[f"{prefix}.ThroughputCount"] = cs["throughput_count"]

            # === 触发状态更新回调 ===
            for cb in self._callbacks:
                for agv in self.agvs.values():
                    try:
                        await cb(agv)
                    except Exception as e:
                        logger.debug(f"[OPC-UA-Sim] Status callback error: {e}")

            await asyncio.sleep(self.heartbeat_interval)


# ==================== 主适配器类 ====================

class OpcUaAdapter:
    """
    OPC UA 统一适配器 — AGV-TMS 与工业设备的桥梁.

    架构设计:

    ┌──────────────────────────────────────────────┐
    │              OpcUaAdapter (统一入口)           │
    │                                              │
    │  ┌─────────────────┐  ┌──────────────────┐   │
    │  │ Simulation Mode │  │ Live Mode         │   │
    │  │ (_SimServer)    │  │ (asyncua Client)  │   │
    │  └────────┬────────┘  └────────┬─────────┘   │
    │           │                      │             │
    │  ┌────────▼──────────────────────▼─────────┐  │
    │  │          统一 API 层                       │  │
    │  │  read_node / write_node / call_method     │  │
    │  │  browse / subscribe / get_metrics        │  │
    │  └──────────────────────────────────────────┘  │
    └──────────────────────────────────────────────┘

    支持模式:
      - 'simulation': 内存模拟, 无需外部依赖 (默认, 开发/测试)
      - 'live':       连接真实 OPC UA Server (需要 asyncua)
      - 'record':     从录制 JSON 文件回放 (演示/调试)

    使用示例:
        # 模拟模式
        adapter = OpcUaAdapter(mode='simulation', num_sim_agvs=15)
        await adapter.initialize(map_nodes=nodes)
        await adapter.start()

        val = await adapter.read_node("AGV[0].BatteryLevel")
        await adapter.write_node("Conveyor[0].Running", True)
        result = await adapter.call_method("AGV[0].Cmd.MoveTo", ["station_42", 0.8])

        # 订阅数据变更
        await adapter.subscribe("AGV[0].Position.X", lambda n,o,v: print(f"X={v}"))

        await adapter.stop()

        # 实时模式
        adapter = OpcUaAdapter(
            mode='live',
            server_url='opc.tcp://plc01.local:4840',
            security_mode='SignAndEncrypt',
            username='operator', password='secret',
            certificate_path='/path/to/client_cert.der',
            private_key_path='/path/to/client_key.pem',
        )
    """

    def __init__(
        self,
        mode: str = "simulation",
        server_url: Optional[str] = None,
        num_sim_agvs: int = 10,
        num_sim_conveyors: int = 3,
        move_speed: float = 1.5,
        heartbeat_interval: float = 1.0,
        security_mode: str = OpcSecurityMode.NONE.value,
        security_policy: str = OpcSecurityPolicy.NONE.value,
        username: Optional[str] = None,
        password: Optional[str] = None,
        certificate_path: Optional[str] = None,
        private_key_path: Optional[str] = None,
        server_certificate: Optional[str] = None,
        record_file: Optional[str] = None,
        node_map: Optional[Dict[str, OpcUaNodeDefinition]] = None,
        fault_probability: float = 0.001,
    ):
        """
        初始化 OPC UA 适配器.

        Args:
            mode: 运行模式 ('simulation' | 'live' | 'record')
            server_url: OPC UA Server 端点 URL (如 'opc.tcp://192.168.1.100:4840')
            num_sim_agvs: 模拟模式下 AGV 数量
            num_sim_conveyors: 模拟模式下输送线数量
            move_speed: 模拟模式下 AGV 速度 m/s
            heartbeat_interval: 模拟/实时模式的心跳/轮询间隔秒数
            security_mode: 安全模式 ('None' | 'Sign' | 'SignAndEncrypt')
            security_policy: 加密策略字符串
            username: 用户名 (用于 Username/Password 认证)
            password: 密码
            certificate_path: 客户端证书路径 (.der/.pem)
            private_key_path: 客户端私钥路径 (.pem)
            server_certificate: 服务端证书 (用于验证, PEM格式字符串)
            record_file: 录制/回放的 JSON 文件路径
            node_map: 自定义节点映射表 (None则使用 DEFAULT_OPCUA_NODE_MAP)
            fault_probability: 模拟模式下的故障注入概率 (0=关闭)
        """
        self.mode = mode
        self.security_mode = security_mode
        self.security_policy = security_policy
        self.username = username
        self.password = password
        self.certificate_path = certificate_path
        self.private_key_path = private_key_path
        self.server_certificate = server_certificate

        # server_url 解析
        if server_url is None:
            try:
                from app.config import settings
                self.server_url = getattr(settings, 'OPCUA_SERVER_URL', "opc.tcp://localhost:4840")
            except (ImportError, AttributeError):
                self.server_url = "opc.tcp://localhost:4840"
        else:
            self.server_url = server_url

        self.num_sim_agvs = num_sim_agvs
        self.num_sim_conveyors = num_sim_conveyors
        self.move_speed = move_speed
        self.heartbeat_interval = heartbeat_interval
        self.record_file = record_file
        self.fault_probability = fault_probability

        # 节点映射表
        self.node_map: Dict[str, OpcUaNodeDefinition] = node_map or dict(DEFAULT_OPCUA_NODE_MAP)
        # 反向索引: node_id → name
        self._node_id_to_name: Dict[str, str] = {
            nd.node_id: name for name, nd in self.node_map.items()
        }

        # 内部组件
        self._simulator: Optional[_SimulatedOpcUaServer] = None
        self._live_client: Any = None  # asyncua.Client instance
        self._subscriptions: Dict[str, Any] = {}  # node_name → Subscription handle

        # 状态缓存
        self._status_cache: Dict[str, AgvDeviceStatus] = {}
        self._node_cache: Dict[str, Any] = {}

        # 回调列表
        self._status_subscribers: List[StatusCallback] = []
        self._alert_subscribers: List[AlertCallback] = []
        self._data_change_subs: List[Tuple[str, DataChangeCallback]] = []

        # 指标
        self.metrics = OpcUaMetrics()

        # 生命周期标志
        self._initialized = False
        self._running = False
        self._map_nodes: List[Dict] = []

        # 录制回放相关
        self._recording: List[Dict] = []
        self._replay_index: int = 0

    # ==================== 生命周期 ====================

    async def initialize(self, map_nodes: Optional[List[Dict]] = None) -> None:
        """
        初始化适配器.

        Args:
            map_nodes: 地图节点列表 (模拟模式用于路径规划)

        Raises:
            ImportError: 当 live mode 但未安装 asyncua 时
            ConnectionError: 当无法连接到 OPC UA Server 时
            ValueError: 配置参数无效时
        """
        self._map_nodes = map_nodes or []

        if self.mode == "simulation":
            self._simulator = _SimulatedOpcUaServer(
                num_agvs=self.num_sim_agvs,
                num_conveyors=self.num_sim_conveyors,
                map_nodes=self._map_nodes,
                move_speed=self.move_speed,
                heartbeat_interval=self.heartbeat_interval,
                fault_probability=self.fault_probability,
            )
            # 注册内部回调
            self._simulator.on_status_update(self._on_status_update)
            self._simulator.on_alert(self._on_alert)

        elif self.mode == "live":
            if not HAS_ASYNCUA:
                raise ImportError(
                    "Live mode requires the 'asyncua' package.\n"
                    "Install with: pip install asyncua>=1.0\n"
                    "Falling back to simulation mode."
                )

            try:
                client = Client(self.server_url)
                # 配置安全设置
                if self.security_mode != OpcSecurityMode.NONE.value:
                    client.set_security_string(
                        f"{self.security_policy},{self.security_mode},"
                        f"{self.certificate_path or ''},{self.private_key_path or ''},"
                        f"{self.server_certificate or ''}"
                    )

                # 设置用户凭证
                if self.username and self.password:
                    client.set_user(self.username)
                    client.set_password(self.password)

                self._live_client = client
                await self._live_client.connect()
                logger.info(f"[OPC-UA] Connected to {self.server_url} "
                           f"(security={self.security_mode})")

                # 自动浏览并建立节点缓存
                await self._build_live_node_cache()

            except Exception as e:
                logger.error(f"[OPC-UA] Connection failed to {self.server_url}: {e}")
                raise ConnectionError(
                    f"Cannot connect to OPC UA Server at {self.server_url}: {e}"
                ) from e

        elif self.mode == "record":
            # 回放模式: 从录制文件加载
            if not self.record_file or not os.path.exists(self.record_file):
                raise FileNotFoundError(
                    f"Record file not found: {self.record_file}\n"
                    f"Use simulation mode first with recording enabled to create one."
                )
            import json
            with open(self.record_file, "r") as f:
                self._recording = json.load(f)
            self._replay_index = 0
            logger.info(f"[OPC-UA] Record loaded: {len(self._recording)} entries")

        else:
            raise ValueError(f"Unknown mode: '{self.mode}'. Use 'simulation', 'live', or 'record'.")

        self._initialized = True
        logger.info(f"[OPC-UA] Adapter initialized in {self.mode} mode "
                   f"(AGVs={self.num_sim_agvs}, Conveyors={self.num_sim_conveyors})")

    async def start(self) -> None:
        """启动适配器 (开始数据流)"""
        if not self._initialized:
            await self.initialize()

        self._running = True

        if self.mode == "simulation":
            await self._simulator.start()
        elif self.mode == "live":
            # 启动实时订阅循环
            asyncio.create_task(self._live_subscription_loop())
        elif self.mode == "record":
            # 启动回放循环
            asyncio.create_task(self._replay_loop())

        logger.info("[OPC-UA] Adapter started")

    async def stop(self) -> None:
        """停止适配器并释放资源"""
        self._running = False

        if self._simulator:
            await self._simulator.stop()

        if self._live_client:
            try:
                await self._live_client.disconnect()
            except Exception as e:
                logger.debug(f"[OPC-UA] Disconnect error: {e}")

        self._subscriptions.clear()
        self._status_cache.clear()
        self._node_cache.clear()
        logger.info("[OPC-UA] Adapter stopped")

    # ==================== 节点读写 ====================

    async def read_node(self, node_name: str) -> Any:
        """
        读取单个 OPC UA 节点的值.

        Args:
            node_name: 节点名 (如 'AGV[0].BatteryLevel') 或 NodeId

        Returns:
            节点值 (类型取决于节点数据类型), 如果节点不存在返回 None
        """
        t0 = time.monotonic()

        try:
            if self.mode == "simulation" and self._simulator:
                result = self._simulator.read_node_value(node_name)
            elif self.mode == "live" and self._live_client:
                node_def = self.node_map.get(node_name)
                node_id_str = node_def.node_id if node_def else node_name
                node = self._live_client.get_node(node_id_str)
                result = await node.read_value()
                # 应用缩放因子
                if node_def and node_def.scale != 1.0:
                    try:
                        result = float(result) * node_def.scale + node_def.offset
                    except (TypeError, ValueError):
                        pass
            elif self.mode == "record":
                result = await self._replay_read(node_name)
            else:
                result = None

            latency_ms = (time.monotonic() - t0) * 1000
            self.metrics.record_read(latency_ms)
            return result

        except Exception as e:
            self.metrics.record_error()
            logger.debug(f"[OPC-UA] Read failed '{node_name}': {e}")
            return None

    async def write_node(self, node_name: str, value: Any) -> bool:
        """
        写入单个 OPC UA 节点的值.

        Args:
            node_name: 节点名
            value: 要写入的值

        Returns:
            True 成功, False 失败
        """
        t0 = time.monotonic()

        try:
            if self.mode == "simulation" and self._simulator:
                result = self._simulator.write_node_value(node_name, value)
            elif self.mode == "live" and self._live_client:
                node_def = self.node_map.get(node_name)
                if node_def and not node_def.writable:
                    logger.warning(f"[OPC-UA] Node '{node_name}' is read-only")
                    return False
                node_id_str = node_def.node_id if node_def else node_name
                node = self._live_client.get_node(node_id_str)
                # 反向缩放
                write_value = value
                if node_def and node_def.scale != 1.0:
                    try:
                        write_value = (float(value) - node_def.offset) / node_def.scale
                    except (TypeError, ValueError):
                        pass
                await node.write_value(write_value)
                result = True
            else:
                result = False

            latency_ms = (time.monotonic() - t0) * 1000
            self.metrics.record_write(latency_ms)
            return result

        except Exception as e:
            self.metrics.record_error()
            logger.warning(f"[OPC-UA] Write failed '{node_name}' = {value}: {e}")
            return False

    async def read_multiple_nodes(self, node_names: List[str]) -> Dict[str, Any]:
        """
        批量读取多个节点值 (优化性能).

        Args:
            node_names: 节点名列表

        Returns:
            {节点名: 值} 字典
        """
        results = {}
        if self.mode == "simulation" and self._simulator:
            t0 = time.monotonic()
            for name in node_names:
                results[name] = self._simulator.read_node_value(name)
            latency_ms = (time.monotonic() - t0) * 1000
            self.metrics.record_read(latency_ms, len(node_names))
        elif self.mode == "live" and self._live_client:
            # 并发读取
            tasks = [self.read_node(name) for name in node_names]
            values = await asyncio.gather(*tasks, return_exceptions=True)
            results = {
                name: (v if not isinstance(v, Exception) else None)
                for name, v in zip(node_names, values)
            }
        else:
            for name in node_names:
                results[name] = await self.read_node(name)
        return results

    # ==================== 方法调用 ====================

    async def call_method(
        self,
        method_name: str,
        args: Optional[List[Any]] = None,
    ) -> Any:
        """
        调用 OPC UA 方法节点.

        Args:
            method_name: 方法节点名 (如 'AGV[0].Cmd.MoveTo')
            args: 方法输入参数列表

        Returns:
            方法返回值 (通常为布尔值表示成功/失败)
        """
        t0 = time.monotonic()
        args = args or []

        try:
            if self.mode == "simulation" and self._simulator:
                result = self._simulator.call_method(method_name, args)
            elif self.mode == "live" and self._live_client:
                node_def = self.node_map.get(method_name)
                if not node_def or not node_def.is_method:
                    logger.warning(f"[OPC-UA] '{method_name}' is not a registered method")
                    return None

                # 构建 OPC UA Method Call
                method_node = self._live_client.get_node(node_def.node_id)
                # 获取父对象作为调用目标
                parent_path = ".".join(method_name.split(".")[:-1])
                parent_def = self.node_map.get(parent_path)
                if parent_def:
                    parent_node = self._live_client.get_node(parent_def.node_id)
                else:
                    # 尝试从 method_node 获取 parent
                    parent_node = method_node.get_parent()

                # 转换参数为 Variant 类型
                ua_args = []
                for i, arg in enumerate(args):
                    arg_info = node_def.input_args[i] if i < len(node_def.input_args) else {}
                    ua_type_name = arg_info.get("type", "String").upper()
                    ua_type = getattr(ua.VariantType, ua_type_name, ua.VariantType.VARIANT)
                    ua_args.append(ua.Variant(arg, ua_type))

                result = await parent_node.call_method(method_node, *ua_args)
                # 解析返回值
                if hasattr(result, 'value'):
                    result = result.value
            else:
                result = None

            latency_ms = (time.monotonic() - t0) * 1000
            self.metrics.record_method_call(latency_ms, bool(result))
            logger.debug(f"[OPC-UA] Method call: {method_name}({args}) → {result}")
            return result

        except Exception as e:
            self.metrics.record_method_call(time.monotonic() - t0, False)
            self.metrics.record_error()
            logger.warning(f"[OPC-UA] Method call failed '{method_name}': {e}")
            return None

    # ==================== 节点浏览与发现 ====================

    async def browse_nodes(
        self,
        root_pattern: str = "*",
        filter_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        浏览 OPC UA 节点空间.

        Args:
            root_pattern: 通配符模式 (如 'AGV[0].*' 或 'Conveyor.*')
            filter_types: 过滤数据类型列表 (如 ['Boolean', 'Float'])

        Returns:
            匹配的节点信息列表
        """
        if self.mode == "simulation" and self._simulator:
            results = self._simulator.browse_nodes(root_pattern)
            if filter_types:
                results = [r for r in results if r.get("type") in filter_types]
            return results

        elif self.mode == "live" and self._live_client:
            # 浏览真实的 OPC UA 地址空间
            results = []
            try:
                import fnmatch
                for name, nd in self.node_map.items():
                    if fnmatch.fnmatch(name, root_pattern):
                        if filter_types and nd.data_type.value not in filter_types:
                            continue
                        # 读取实际值
                        val = await self.read_node(name)
                        results.append({
                            "name": name,
                            "node_id": nd.node_id,
                            "data_type": nd.data_type.value,
                            "writable": nd.writable,
                            "value": val,
                            "description": nd.description,
                        })
            except Exception as e:
                logger.warning(f"[OPC-UA] Browse failed: {e}")
            return results

        return []

    async def discover_tags(self, pattern: str = "*") -> List[str]:
        """
        发现匹配模式的标签/节点名称.

        Args:
            pattern: fnmatch 通配符模式

        Returns:
            匹配的节点名列表
        """
        import fnmatch
        matched = [name for name in self.node_map.keys() if fnmatch.fnmatch(name, pattern)]
        return matched

    # ==================== 订阅 (数据变更监控) ====================

    async def subscribe(
        self,
        node_name: str,
        callback: DataChangeCallback,
        interval_ms: int = 1000,
    ) -> bool:
        """
        订阅节点的数据变更通知.

        Args:
            node_name: 要监听的节点名
            callback: 变更回调函数 (node_id, old_value, new_value) → None
            interval_ms: 发布间隔毫秒数 (仅 live 模式有效)

        Returns:
            订阅是否成功
        """
        if self.mode == "simulation" and self._simulator:
            self._simulator.on_data_change(node_name, callback)
            self._data_change_subs.append((node_name, callback))
            self.metrics.subscribe_count += 1
            return True

        elif self.mode == "live" and self._live_client:
            try:
                node_def = self.node_map.get(node_name)
                if not node_def:
                    logger.warning(f"[OPC-UA] Cannot subscribe: unknown node '{node_name}'")
                    return False

                node = self._live_client.get_node(node_def.node_id)
                handler = await node.subscribe_data_change(callback)
                self._subscriptions[node_name] = handler
                self._data_change_subs.append((node_name, callback))
                self.metrics.subscribe_count += 1
                logger.debug(f"[OPC-UA] Subscribed to '{node_name}'")
                return True
            except Exception as e:
                logger.warning(f"[OPC-UA] Subscribe failed for '{node_name}': {e}")
                return False

        return False

    async def unsubscribe(self, node_name: str) -> bool:
        """取消订阅"""
        handler = self._subscriptions.pop(node_name, None)
        if handler:
            try:
                if self.mode == "live" and self._live_client:
                    # asyncua 取消订阅
                    if hasattr(handler, 'unsubscribe'):
                        await handler.unsubscribe()
                self._data_change_subs = [
                    (n, c) for n, c in self._data_change_subs if n != node_name
                ]
                return True
            except Exception as e:
                logger.debug(f"[OPC-UA] Unsubscribe error: {e}")
        return False

    # ==================== AGV 状态快捷接口 ====================

    async def get_all_agv_statuses(self) -> List[AgvDeviceStatus]:
        """获取所有 AGV 当前状态"""
        if self.mode == "simulation" and self._simulator:
            all_status = self._simulator.get_all_statuses()
            self._status_cache = dict(all_status)
            return list(all_status.values())
        elif self.mode == "live" and self._live_client:
            return list(self._status_cache.values())
        return []

    async def get_agv_status(self, agv_id: str) -> Optional[AgvDeviceStatus]:
        """获取单个 AGV 状态"""
        if self.mode == "simulation" and self._simulator:
            status = self._simulator.get_status(agv_id)
            if status:
                self._status_cache[agv_id] = status
            return status
        return self._status_cache.get(agv_id)

    async def send_command(
        self,
        agv_id: str,
        command: AgvCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> OpcUaCommandResult:
        """向指定 AGV 下发控制指令"""
        if self.mode == "simulation" and self._simulator:
            result = await self._simulator.send_command(agv_id, command, params)
            logger.debug(f"[OPC-UA-Sim] Cmd {command.value} → {agv_id}: {result.message}")
            return result
        elif self.mode == "live" and self._live_server:
            # 通过 OPC UA Method Call 下发
            try:
                method_name = f"AGV[{self._extract_agv_idx(agv_id)}].Cmd.{command.name}"
                args_list = list(params.values()) if params else []
                success = await self.call_method(method_name, args_list)
                return OpcUaCommandResult(
                    bool(success), command.value, agv_id,
                    "Command sent via OPC UA method call",
                )
            except Exception as e:
                return OpcUaCommandResult(False, command.value, agv_id, str(e))

        return OpcUaCommandResult(False, command.value, agv_id, "Adapter not running")

    async def emergency_stop(self, agv_id: Optional[str] = None) -> Dict[str, OpcUaCommandResult]:
        """紧急停止"""
        results = {}
        targets = [agv_id] if agv_id else list(self._status_cache.keys())
        for aid in targets:
            results[aid] = await self.send_command(aid, AgvCommand.STOP)
        return results

    # ==================== 输送线状态快捷接口 ====================

    async def read_conveyor_status(self, conveyor_id: int = 0) -> Optional[Dict[str, Any]]:
        """读取输送线完整状态"""
        if self.mode == "simulation" and self._simulator:
            return self._simulator.get_conveyor_state(conveyor_id)
        elif self.mode == "live":
            prefix = f"Conveyor[{conveyor_id}]"
            keys = [k for k in self.node_map if k.startswith(prefix)]
            values = await self.read_multiple_nodes(keys)
            return values if values else None
        return None

    async def conveyor_control(self, conveyor_id: int, action: str, **kwargs) -> bool:
        """
        输送线控制.

        Args:
            conveyor_id: 输送线编号
            action: 'start' | 'stop' | 'set_speed'
            **kwargs: 动作参数 (如 speed=1.5)
        """
        method_map = {
            "start": f"Conveyor[{conveyor_id}].Cmd.Start",
            "stop": f"Conveyor[{conveyor_id}].Cmd.Stop",
        }
        method_name = method_map.get(action)
        if method_name:
            args = list(kwargs.values()) if kwargs else ([kwargs.get("speed", 1.0)] if action == "start" else [])
            result = await self.call_method(method_name, args)
            return bool(result)

        # set_speed 直接写入节点
        if action == "set_speed":
            return await self.write_node(f"Conveyor[{conveyor_id}].Speed", kwargs.get("speed", 1.0))
        return False

    # ==================== 安全联锁接口 ====================

    async def read_safety_status(self) -> Dict[str, bool]:
        """读取全部安全联锁状态"""
        if self.mode == "simulation" and self._simulator:
            return self._simulator.get_safety_state()
        elif self.mode == "live":
            safety_keys = [k for k in self.node_map if k.startswith("Safety.") and not k.startswith("Safety.")]
            results = await self.read_multiple_nodes(safety_keys)
            return {k.replace("Safety.", ""): bool(v) for k, v in results.items()}
        return {}

    async def safety_reset(self) -> bool:
        """复位安全系统"""
        result = await self.call_method("Safety.Reset", [])
        return bool(result)

    # ==================== 内部方法 ====================

    async def _build_live_node_cache(self):
        """Live模式: 预加载已知节点到缓存"""
        if not self._live_client:
            return
        for name, nd in self.node_map.items():
            try:
                node = self._live_client.get_node(nd.node_id)
                val = await node.read_value()
                self._node_cache[name] = val
            except Exception:
                pass  # 节点可能不存在于远程服务器
        logger.info(f"[OPC-UA] Node cache built: {len(self._node_cache)} nodes")

    async def _live_subscription_loop(self):
        """Live模式: 定期轮询并分发状态变更"""
        while self._running:
            try:
                # 轮询 AGV 状态节点
                for name in list(self._status_cache.keys()):
                    new_val = await self.read_node(name)
                    old_val = self._node_cache.get(name)
                    if new_val != old_val:
                        self._node_cache[name] = new_val
                        for pattern, cb in self._data_change_subs:
                            if pattern == name or "*" in pattern:
                                try:
                                    await cb(name, old_val, new_val)
                                except Exception:
                                    pass
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OPC-UA] Live subscription loop error: {e}")
                await asyncio.sleep(5.0)

    async def _replay_loop(self):
        """Record模式: 按时间戳回放录制数据"""
        while self._running and self._replay_index < len(self._recording):
            entry = self._recording[self._replay_index]
            ts = entry.get("timestamp", 0)
            delay = ts - (
                self._recording[self._replay_index - 1]["timestamp"]
                if self._replay_index > 0 else 0
            )
            if delay > 0:
                await asyncio.sleep(min(delay, 1.0))  # 最大加速1秒

            # 应用录制的数据
            node_name = entry.get("node", "")
            value = entry.get("value")
            if entry.get("type") == "write":
                await self._simulate_write(node_name, value)
            else:
                self._node_cache[node_name] = value

            self._replay_index += 1

        logger.info("[OPC-UA] Replay finished")

    async def _replay_read(self, node_name: str) -> Any:
        """从录制数据中读取"""
        return self._node_cache.get(node_name)

    async def _simulate_write(self, node_name: str, value: Any):
        """模拟写入 (回放模式)"""
        old_val = self._node_cache.get(node_name)
        self._node_cache[node_name] = value
        for pattern, cb in self._data_change_subs:
            if pattern == node_name or "*" in pattern:
                try:
                    await cb(node_name, old_val, value)
                except Exception:
                    pass

    def _extract_agv_idx(self, agv_id: str) -> int:
        """从 AGV ID 提取数字索引用于节点映射"""
        import re
        match = re.search(r'(\d+)', agv_id)
        return int(match.group(1)) if match else 0

    async def _on_status_update(self, status: AgvDeviceStatus) -> None:
        """内部: 状态更新分发"""
        self._status_cache[status.agv_id] = status
        for cb in self._status_subscribers:
            try:
                await cb(status)
            except Exception as e:
                logger.debug(f"[OPC-UA] Subscriber error: {e}")

    async def _on_alert(self, agv_id: str, alert_type: str, detail: Dict) -> None:
        """内部: 告警分发"""
        for cb in self._alert_subscribers:
            try:
                await cb(agv_id, alert_type, detail)
            except Exception as e:
                logger.debug(f"[OPC-UA] Alert subscriber error: {e}")

    # ==================== 工具方法 ====================

    def to_api_format(self, status: AgvDeviceStatus) -> Dict[str, Any]:
        """将 AgvDeviceStatus 转换为 API 兼容格式"""
        return {
            "id": status.agv_id,
            "current_node": status.current_node_id,
            "battery": status.battery_level,
            "speed": status.speed,
            "state": status.state.value,
            "x": status.x,
            "y": status.y,
            "angle": status.angle,
            "load_status": status.load_status,
            "error_code": status.error_code,
            "target_node": status.target_node_id,
        }

    def health_check(self) -> Dict[str, Any]:
        """
        全面健康检查.

        Returns:
            包含连接状态、模式、指标、设备数量的健康报告
        """
        return {
            "adapter": "opcua",
            "mode": self.mode,
            "connected": self._running and self._initialized,
            "server_url": self.server_url if self.mode == "live" else "N/A (simulation)",
            "security_mode": self.security_mode,
            "agv_count": len(self._status_cache),
            "conveyor_count": self.num_sim_conveyors if self._simulator else 0,
            "subscribed_nodes": len(self._subscriptions),
            "metrics": self.metrics.to_dict(),
            "uptime_seconds": time.time() - (self._simulator._start_time if self._simulator else 0),
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def connected_agv_count(self) -> int:
        return len(self._status_cache)

    @property
    def supported_modes(self) -> List[str]:
        return ["simulation", "live"] + (["record"] if HAS_ASYNCUA else [])
