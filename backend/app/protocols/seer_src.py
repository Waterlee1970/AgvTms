"""
仙工智能 (SEER) SRC 控制器 × VDA5050 厂商画像库.

背景
----
仙工智能 SRC 系列核心控制器 (SRC-2000 等) 原生支持国际标准 VDA5050 v2.0
上位机接入, 整车厂商/集成商通过 MQTT 主题按
`<topicBase>/<manufacturer>/<serialNumber>/<messageType>` 与 SRC 机器人通信。
本模块为 AGV-TMS 增强的 VDA5050 通道提供 "厂商画像" (Vendor Profile) 语义层:

  1. AGVId <-> serialNumber 双向映射        —— 解决 TMS 内部车号与 SRC 序列号不一致问题
  2. 坐标系对齐 (SRC 地图 <-> TMS 世界地图)   —— 地图原点/旋转/比例差异补偿
  3. 位置初始化 (二次定位) 报文构造          —— SRC 丢失定位后由上位机下发初始位姿
  4. 消息约束校验                            —— 按 SEER 场景对 order/instantAction 合规性检查
  5. SEER 故障/错误信息翻译                  —— 统一 errorState 到 TMS 错误码的桥接
  6. 标准节点动作映射 (装载/卸载/充电等)

说明: 仙工控制器侧的私有字段 (如 controller 型号级出厂参数、RoboView 故障字典)
属于厂商受控信息。凡依赖此类私有细节的部分在代码中以 `TODO(SEER-field)`
显式标注, 并提供配置项, 待拿到官方《SRC 控制器 VDA5050 接入手册》后校准。
"""

from __future__ import annotations

import math
import re
import time
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .vda5050 import (
    Vda5050InstantAction,
    Vda5050Node,
    Vda5050NodeAction,
    Vda5050Order,
    Vda5050Topics,
)

# =============================================================================
# 常量与别名
# =============================================================================

# 厂商识别名 — Adapter 启动配置 vendor 参数支持的值
SEER_VENDOR_ALIASES = {"seer", "src", "seer-src", "seer_src", "src2000", "src-2000", "仙工", "仙工智能"}

# 默认 VDA5050 协议版本
VDA5050_VERSION = "2.0.0"

# 装载/卸载/充电等标准节点动作 (与项目 base_adapter 的统一指令对齐)
SEER_NODE_ACTION_MAP = {
    "load": "pickPosition",       # 叉取/吸盘取货
    "unload": "dropPosition",     # 放货
    "charge": "charge",           # 充电桩动作
    "initPosition": "initPosition",
}

# SEER SRC 常见 VDA5050 instantActionType
SEER_INSTANT_ACTION_TYPES = {"stop", "cancelOrder", "start", "resume", "pause"}

# 位置初始化支持模式
INIT_POSITION_MODE_ORDER = "order"            # 下发单点 order (VDA5050 规范做法)
INIT_POSITION_MODE_INSTANT = "instantAction"  # 下发 instantAction (部分上位机做法)
INIT_POSITION_MODES = {INIT_POSITION_MODE_ORDER, INIT_POSITION_MODE_INSTANT}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# =============================================================================
# 坐标系对齐
# =============================================================================

@dataclass
class CoordTransform:
    """
    SRC 机器人地图坐标系 <-> TMS 世界地图坐标系 的仿射对齐.

    约定: TMS 世界坐标由 SRC 地图坐标经 旋转+缩放+平移 得到
        p_tms = R(rotation_deg) · (scale · p_src) + (offset_x, offset_y)

    字段:
      offset_x/offset_y: SRC 地图原点在 TMS 地图中的坐标
      rotation_deg:      两地图坐标轴夹角 (TMS 相对 SRC, 逆时针为正)
      scale:             单位缩放 (默认 1.0, 单位均为米)
    """
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0
    scale: float = 1.0

    # ------------------------------------------------------------------
    @property
    def _cos(self) -> float:
        return math.cos(math.radians(self.rotation_deg))

    @property
    def _sin(self) -> float:
        return math.sin(math.radians(self.rotation_deg))

    def is_identity(self) -> bool:
        return (
            abs(self.offset_x) < 1e-9
            and abs(self.offset_y) < 1e-9
            and abs(self.rotation_deg) < 1e-9
            and abs(self.scale - 1.0) < 1e-9
        )

    def src_to_tms(self, x: float, y: float) -> Tuple[float, float]:
        """SRC 地图坐标 -> TMS 世界坐标."""
        sx, sy = x * self.scale, y * self.scale
        return (
            round(self._cos * sx - self._sin * sy + self.offset_x, 6),
            round(self._sin * sx + self._cos * sy + self.offset_y, 6),
        )

    def tms_to_src(self, x: float, y: float) -> Tuple[float, float]:
        """TMS 世界坐标 -> SRC 地图坐标 (逆变换)."""
        dx, dy = x - self.offset_x, y - self.offset_y
        # 逆旋转 (旋转角取负)
        inv_cos, inv_sin = self._cos, -self._sin
        return (
            round((inv_cos * dx - inv_sin * dy) / self.scale, 6),
            round((inv_sin * dx + inv_cos * dy) / self.scale, 6),
        )

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["CoordTransform"]:
        """从配置字典构建 (identity 时返回 None 亦可)."""
        if not data:
            return None
        return cls(
            offset_x=float(data.get("offset_x", data.get("x", 0.0))),
            offset_y=float(data.get("offset_y", data.get("y", 0.0))),
            rotation_deg=float(data.get("rotation_deg", data.get("rotation", 0.0))),
            scale=float(data.get("scale", 1.0)),
        )


# =============================================================================
# SEER SRC × VDA5050 厂商画像
# =============================================================================

@dataclass
class SeerVda5050Profile:
    """
    SEER SRC 控制器在 VDA5050 通道下的厂商画像.

    Attributes:
        vendor_key:       归一化厂商键 (默认 "seer")
        manufacturer:     VDA5050 header/topic 中的 manufacturer 字段
                          (注意: 实际值取决于 SRC 控制器出厂配置, 可被启动配置覆盖)
        protocol_version: 本项目遵循的 VDA5050 版本
        serial_prefix:    由 TMS AGVId 推导 serialNumber 时使用的前缀
                          (SEER 控制器序列号通常以字母前缀 + 数字组成)
        topic_base:       MQTT 主题前缀, 默认 VDA5050 标准 uagv/v2
        map_transform:    SRC 地图 -> TMS 地图 对齐变换 (None = 同一坐标系)
        init_position_mode: 位置初始化采用的报文形式 (order/instantAction)
    """

    vendor_key: str = "seer"
    manufacturer: str = "SEER"
    protocol_version: str = VDA5050_VERSION
    serial_prefix: str = "SRC-"
    topic_base: str = "uagv/v2"
    map_transform: Optional[CoordTransform] = None
    init_position_mode: str = INIT_POSITION_MODE_ORDER

    def __post_init__(self) -> None:
        if self.init_position_mode not in INIT_POSITION_MODES:
            raise ValueError(
                f"Unsupported init_position_mode: {self.init_position_mode}, "
                f"expected one of {sorted(INIT_POSITION_MODES)}"
            )
        self.manufacturer = (self.manufacturer or "SEER").strip()

    @classmethod
    def from_vendor(
        cls,
        vendor: Optional[str],
        *,
        manufacturer: Optional[str] = None,
        map_transform: Optional[CoordTransform] = None,
        init_position_mode: str = INIT_POSITION_MODE_ORDER,
        serial_prefix: str = "SRC-",
    ) -> Optional["SeerVda5050Profile"]:
        """按 vendor 字符串构建 SEER 画像; 非 SEER 厂商返回 None."""
        if not vendor:
            return None
        key = str(vendor).strip().lower()
        if key not in SEER_VENDOR_ALIASES:
            return None
        return cls(
            vendor_key="seer",
            manufacturer=manufacturer or "SEER",
            map_transform=map_transform,
            init_position_mode=init_position_mode,
            serial_prefix=serial_prefix,
        )


def looks_like_serial(serial: str, profile: Optional[SeerVda5050Profile] = None) -> bool:
    """粗略判断字符串是否已是序列号 (含字母前缀-数字或纯字母数字)."""
    if profile and profile.serial_prefix:
        if serial.startswith(profile.serial_prefix):
            return True
    return bool(re.match(r"^[A-Za-z]{2,}[-_][0-9A-Za-z_-]+$", serial))


def agv_id_to_serial(agv_id: str, profile: Optional[SeerVda5050Profile] = None) -> str:
    """
    TMS 内部 AGVId -> VDA5050 serialNumber.

    对 SEER 画像, 若 agv_id 已经形如 SRC-xxx 则原样返回, 否则补 serial_prefix。
    """
    if profile is None:
        return agv_id
    if looks_like_serial(agv_id, profile):
        return agv_id
    return f"{profile.serial_prefix}{agv_id}"


def serial_to_agv_id(serial: str, profile: Optional[SeerVda5050Profile] = None) -> str:
    """VDA5050 serialNumber -> TMS 内部 AGVId (去掉厂商序列号前缀)."""
    if profile is None:
        return serial
    prefix = profile.serial_prefix
    if prefix and serial.startswith(prefix):
        return serial[len(prefix):]
    return serial


# =============================================================================
# VDA5050 主题
# =============================================================================

def seer_topics(profile: SeerVda5050Profile, serial: str) -> Dict[str, str]:
    """
    计算某台 SRC 机器人在 broker 上的全套 VDA5050 主题.

    注: 若 profile.topic_base 与 VDA5050 标准前缀一致 (uagv/v2) 直接使用
    Vda5050Topics; 否则按自定义前缀规则拼装, 兼容 SEER 方案常见的自有主题前缀。
    """
    if profile.topic_base == "uagv/v2":
        return {
            "order": Vda5050Topics.order_topic(profile.manufacturer, serial),
            "state": Vda5050Topics.state_topic(profile.manufacturer, serial),
            "connection": Vda5050Topics.connection_topic(profile.manufacturer, serial),
            "instantAction": Vda5050Topics.instant_action_topic(profile.manufacturer, serial),
            "visualization": Vda5050Topics.visualization_topic(profile.manufacturer, serial),
        }
    base = profile.topic_base.rstrip("/")
    mfr = profile.manufacturer
    return {
        "order": f"{base}/{mfr}/{serial}/order",
        "state": f"{base}/{mfr}/{serial}/state",
        "connection": f"{base}/{mfr}/{serial}/connection",
        "instantAction": f"{base}/{mfr}/{serial}/instantAction",
        "visualization": f"{base}/{mfr}/{serial}/visualization",
    }


# =============================================================================
# 位置初始化 (二次定位) 报文构造
# =============================================================================

def build_init_position_order(
    profile: SeerVda5050Profile,
    serial: str,
    x: float,
    y: float,
    theta: float = 0.0,
    *,
    map_id: Optional[str] = None,
    order_id: Optional[str] = None,
) -> Vda5050Order:
    """
    构造 "位置初始化" order.

    VDA5050 规范做法: 上位机向 AGV 下发一个仅含单个 base 节点的 order,
    携带目标 x/y/theta, 使 SRC 控制器在地图内完成二次定位/复位。
    该单点 order 不下发 base 序列, 仅用于坐标握手。
    """
    node_id = f"IP-{map_id or 'map'}-{uuid.uuid4().hex[:6]}"
    actions: List[Vda5050NodeAction] = [
        Vda5050NodeAction(
            actionType="initPosition",
            actionId=f"ip_{int(time.time() * 1000)}",
            actionParameters=[
                {"key": "x", "value": round(x, 6)},
                {"key": "y", "value": round(y, 6)},
                {"key": "theta", "value": round(theta, 6)},
            ]
            + ([{"key": "mapId", "value": map_id}] if map_id else []),
            blockingType="HARD",
        )
    ]
    return Vda5050Order(
        orderId=order_id or f"SRC-IP-{int(time.time() * 1000)}",
        orderUpdateId=0,
        headerId=int(time.time() * 1000) % 2 ** 31,
        timestamp=_now_iso(),
        version=profile.protocol_version,
        manufacturer=profile.manufacturer,
        serialNumber=serial,
        nodes=[
            Vda5050Node(
                nodeId=node_id,
                x=round(x, 6),
                y=round(y, 6),
                theta=round(theta, 6),
                actions=actions,
                released=True,
            )
        ],
        edges=[],
    )


def build_init_position_instant_action(
    profile: SeerVda5050Profile,
    serial: str,
    x: float,
    y: float,
    theta: float = 0.0,
    *,
    map_id: Optional[str] = None,
) -> Vda5050InstantAction:
    """
    构造 "位置初始化" instantAction (部分上位机兼容做法).

    注意: VDA5050 2.0.0 标准未将 initPosition 列为标准 instantActionType,
    本方法为可选项, 仅在目标 SRC 控制器固件明确支持时启用。
    """
    params: List[Dict[str, Any]] = [
        {"key": "x", "value": round(x, 6)},
        {"key": "y", "value": round(y, 6)},
        {"key": "theta", "value": round(theta, 6)},
    ]
    if map_id:
        params.append({"key": "mapId", "value": map_id})
    return Vda5050InstantAction(
        headerId=int(time.time() * 1000) % 2 ** 31,
        timestamp=_now_iso(),
        version=profile.protocol_version,
        manufacturer=profile.manufacturer,
        serialNumber=serial,
        instantActionType="initPosition",
        actionId=f"ip_{int(time.time() * 1000)}",
        actionParameters=params,
    )


# =============================================================================
# SEER 消息合规性校验
# =============================================================================

_REQUIRED_ORDER_FIELDS = {"orderId", "orderUpdateId", "nodes"}
_REQUIRED_NODE_FIELDS = {"nodeId"}
_REQUIRED_INSTANT_FIELDS = {"instantActionType"}


def validate_message_for_seer(
    message_type: str,
    payload: Any,
    profile: SeerVda5050Profile,
) -> List[str]:
    """
    校验待下发消息是否符合 SEER-VDA5050 场景约束.

    返回 warning 列表; 为空表示校验通过。
    payload 支持 VDA5050 pydantic 模型或 dict。
    """
    warnings: List[str] = []
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(by_alias=True, exclude_none=False)
    elif isinstance(payload, dict):
        data = payload
    else:
        warnings.append(f"payload 类型 {type(payload).__name__} 无法校验")
        return warnings

    mfr = data.get("manufacturer")
    if mfr and mfr != profile.manufacturer:
        warnings.append(
            f"manufacturer 应为 '{profile.manufacturer}', 实际 '{mfr}'"
        )

    ver = data.get("version")
    if ver and ver != profile.protocol_version:
        warnings.append(f"version 应为 {profile.protocol_version}, 实际 {ver}")

    header = data.get("headerId")
    if header is None or not isinstance(header, int) or header < 0:
        warnings.append("headerId 缺失或非法")

    serial = data.get("serialNumber", "")
    if serial:
        prefix = profile.serial_prefix
        if prefix and not serial.startswith(prefix):
            warnings.append(
                f"serialNumber '{serial}' 缺少 SEER 序列号前缀 '{prefix}'"
            )

    if message_type == "order":
        for req in _REQUIRED_ORDER_FIELDS:
            if req not in data:
                warnings.append(f"order 缺少必填字段 {req}")
        nodes = data.get("nodes") or []
        if not nodes:
            warnings.append("order.nodes 为空, SRC 将无法执行")
        for i, node in enumerate(nodes):
            for req in _REQUIRED_NODE_FIELDS:
                if req not in node:
                    warnings.append(f"order.nodes[{i}] 缺少字段 {req}")
            # SRC 沿已知地图导航, 仅按 nodeId 即可; x/y 缺失给出提示而非阻断
            if "x" not in node or "y" not in node:
                warnings.append(f"order.nodes[{i}] 缺 x/y 坐标, 将以 nodeId 匹配 SRC 地图")
            if node.get("released") is False:
                warnings.append(f"order.nodes[{i}] released=False 将阻塞执行")

    elif message_type == "instantAction":
        for req in _REQUIRED_INSTANT_FIELDS:
            if req not in data:
                warnings.append(f"instantAction 缺少必填字段 {req}")
        itype = data.get("instantActionType")
        if itype and itype not in SEER_INSTANT_ACTION_TYPES:
            warnings.append(
                f"instantActionType '{itype}' 不在常见 SEER 集合 {sorted(SEER_INSTANT_ACTION_TYPES)}"
            )
    return warnings


# =============================================================================
# 错误翻译
# =============================================================================

# 常见 SEER 场景错误对照 (占位示例, 需按官方故障字典校准)
# TODO(SEER-field): 以《SRC 控制器错误码字典/RoboView 故障码》替换为完整映射
SEER_ERROR_REFERENCE_TABLE: Dict[str, str] = {
    "battery": "电池状态异常 / 电量过低",
    "safety": "安全相关: 急停/安全触边/激光保护",
    "localization": "定位丢失, 需位置初始化",
    "navigation": "导航异常, 路径不可达",
    "drive": "驱动/轮组故障",
    "communication": "控制器通信中断",
    "obstacle": "障碍物检测, 路径被占用",
}


def translate_error_states(
    error_states: Iterable[Dict[str, Any]],
    profile: Optional[SeerVda5050Profile] = None,
) -> Tuple[int, str]:
    """
    将 VDA5050 State.errorStates 翻译为 (error_code, error_message).

    返回 (0, "") 表示无故障; error_code 采用 ErrorType 与参考值的简单散列。
    """
    for err in error_states or []:
        err_type = err.get("errorType", "")
        refs = err.get("errorReferences") or []
        desc = err.get("errorDescription", "")
        if isinstance(refs, list) and refs:
            # errorReferences: [{referenceKey, referenceValue}]
            key = next(
                (str(r.get("referenceValue", "")) for r in refs if "referenceValue" in r),
                str(refs[0]) if refs else "",
            )
        else:
            key = ""
        message = desc or SEER_ERROR_REFERENCE_TABLE.get(str(err_type).lower(), str(err_type) or "未知错误")
        # 稳定散列 (zlib.crc32, 跨运行一致), 便于监控/测试断言
        seed = f"{err_type}:{key}" or "unknown"
        code = 1000 + (zlib.crc32(seed.encode("utf-8")) % 9000)
        return int(code), f"{message} (type={err_type}, ref={key})"
    return 0, ""


# =============================================================================
# 便捷构造: 将节点列表规整为 Vda5050Node
# =============================================================================

def normalize_nodes(
    nodes: Iterable[Any],
    *,
    tf: Optional[CoordTransform] = None,
    coords_in: str = "vda",
) -> List[Vda5050Node]:
    """
    将内部节点表示规整为 Vda5050Node 列表.

    支持输入形式:
      - dict: {"nodeId"|"id"|"node_id": ..., "x":..., "y":..., "theta":..., "actions": [...]}
      - Vda5050Node 实例
    坐标来源标记 coords_in: "vda" 表示已是 AGV 地图坐标系, "tms" 表示需要按 tf 变换。
    """
    result: List[Vda5050Node] = []
    for item in nodes:
        if isinstance(item, Vda5050Node):
            result.append(item)
            continue
        if not isinstance(item, dict):
            continue
        node_id = item.get("nodeId") or item.get("id") or item.get("node_id") or ""
        x = float(item.get("x", item.get("position_x", 0.0)) or 0.0)
        y = float(item.get("y", item.get("position_y", 0.0)) or 0.0)
        theta = item.get("theta", item.get("angle"))
        theta = float(theta) if theta is not None else None
        if tf is not None and coords_in == "tms":
            x, y = tf.tms_to_src(x, y)
        raw_actions = item.get("actions") or []
        actions: List[Vda5050NodeAction] = []
        for a in raw_actions:
            if isinstance(a, Vda5050NodeAction):
                actions.append(a)
            elif isinstance(a, dict):
                actions.append(Vda5050NodeAction(**a))
        result.append(
            Vda5050Node(
                nodeId=node_id,
                x=x,
                y=y,
                theta=theta,
                actions=actions,
                released=bool(item.get("released", True)),
            )
        )
    return result
