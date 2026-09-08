"""
VDA5050 车辆适配器 — Phase 7 (P0-7.1), SEER SRC 通道增强版.

将现有 VDA5050 协议实现封装为统一 BaseVehicleAdapter 接口, 并针对仙工智能
(SEER) SRC 系列控制器的 VDA5050 原生支持做通道增强:

  1. 厂商画像 (vendor="seer") —— manufacturer/版本/主题族/序列号前缀规约
  2. AGVId <-> serialNumber 双向映射 —— TMS 内部车号与 SRC 控制器序列号解耦
  3. 位置初始化 (二次定位) —— INIT_POSITION 指令与初始化报文构造
  4. 坐标系对齐 —— SRC 地图 <-> TMS 世界地图 仿射变换 (offset/rotation/scale)
  5. 错误翻译 —— VDA5050 errorStates -> 统一错误码/中文描述
  6. 指令修复与语义增强 —— MOVE 坐标、STOP/RESUME/CHARGE/LOAD/UNLOAD 全量语义

向后兼容: 不带 vendor 配置时保持原有通用 VDA5050 行为。
依赖: app/protocols/vda5050.py + app/protocols/agv_simulator.py + seer_src.py
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from .base_adapter import (
    BaseVehicleAdapter,
    CommandResult,
    TransportOrderMessage,
    VehicleCommand,
    VehicleState,
    VehicleStatus,
)
from ..protocols.vda5050 import (
    Vda5050Edge,
    Vda5050InstantAction,
    Vda5050Node,
    Vda5050NodeAction,
    Vda5050Order,
)
from ..protocols.seer_src import (
    SEER_VENDOR_ALIASES,
    CoordTransform,
    SeerVda5050Profile,
    agv_id_to_serial,
    build_init_position_instant_action,
    build_init_position_order,
    normalize_nodes,
    seer_topics,
    serial_to_agv_id,
    translate_error_states,
    validate_message_for_seer,
)

logger = logging.getLogger(__name__)


# VDA5050 节点动作/即时动作 映射 (项目统一指令 <-> VDA5050)
_VDA5050_ACTION_MAP = {
    VehicleCommand.LOAD: "pickPosition",
    VehicleCommand.UNLOAD: "dropPosition",
    VehicleCommand.CHARGE: "charge",
    VehicleCommand.INIT_POSITION: "initPosition",
    VehicleCommand.PICKUP: "pickPosition",
    VehicleCommand.DROPOFF: "dropPosition",
}

def _order_id(prefix: str = "ord") -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


class Vda5050VehicleAdapter(BaseVehicleAdapter):
    """
    VDA5050 车辆适配器 (SEER SRC 增强).

    配置 (通过 AdapterRegistry / /api/v2/advanced/adapters/start 传入):
      vendor:            "seer"|"seer-src"|"src"|"仙工" ... 启用 SEER 画像; 缺省通用
      manufacturer:      消息头 manufacturer (SEER 画像默认 "SEER")
      vehicle_ids:       纳入本适配器的 AGV ID 列表
      vehicle_configs:   {AGVId: {serial/start_node/x/y/theta/battery/speed}}
      node_positions:    {nodeId: {x, y}} 供 MOVE 使用 (与 SRC 地图同坐标系)
      mode:              "simulation" (进程内仿真) / "live" (预留外部通道)
      init_position_mode:"order"(默认) | "instantAction"
      map_transform:     SRC 地图 -> TMS 地图 对齐 {offset_x,offset_y,rotation_deg,scale}
      topic_base:        自定义 VDA5050 MQTT 主题前缀 (默认 uagv/v2)
      serial_prefix:     SEER 序列号前缀 (默认 "SRC-")
    """

    def __init__(
        self,
        name: str = "vda5050",
        protocol: str = "vda5050",
        manufacturer: str = "AGV-TMS",
        vendor: Optional[str] = None,
        vehicle_ids: Optional[Any] = None,
        vehicle_configs: Optional[Dict[str, Any]] = None,
        node_positions: Optional[Dict[str, Dict[str, float]]] = None,
        mode: str = "simulation",
        init_position_mode: str = "order",
        map_transform: Optional[Dict[str, Any]] = None,
        topic_base: Optional[str] = None,
        serial_prefix: Optional[str] = None,
        **kwargs: Any,
    ):
        super().__init__(name=name, protocol=protocol)
        self.mode = mode or "simulation"
        self.manufacturer = manufacturer or "AGV-TMS"
        self._config_extra = kwargs  # 其它透传配置 (host/port/...)

        # ---- 厂商画像 ----
        vendor_key = str(vendor or "").strip().lower()
        self._is_seer = bool(vendor_key) and vendor_key in SEER_VENDOR_ALIASES
        tf = CoordTransform.from_dict(map_transform)
        profile_manufacturer = None
        if self._is_seer and (not manufacturer or manufacturer == "AGV-TMS"):
            profile_manufacturer = "SEER"
        self.profile: Optional[SeerVda5050Profile] = SeerVda5050Profile.from_vendor(
            vendor,
            manufacturer=profile_manufacturer,
            map_transform=tf,
            init_position_mode=init_position_mode,
            serial_prefix=serial_prefix or "SRC-",
        )
        if self.profile and topic_base:
            self.profile.topic_base = topic_base

        # ---- 车队与映射 ----
        self._vehicle_ids: List[str] = self._normalize_ids(vehicle_ids)
        self._vehicle_configs: Dict[str, Dict[str, Any]] = dict(vehicle_configs or {})
        self._node_positions: Dict[str, Dict[str, float]] = dict(node_positions or {})
        self._serial_map: Dict[str, str] = {}  # vehicle_id -> serialNumber
        self._fleet: Any = None                # AgvFleetSimulator 实例 (本地或全局)

    # ============================= 配置工具 =============================

    @staticmethod
    def _normalize_ids(value: Optional[Any]) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value]
        return [str(value)]

    @property
    def _message_manufacturer(self) -> str:
        return self.profile.manufacturer if self.profile else self.manufacturer

    def _resolve_serial(self, vehicle_id: str) -> str:
        if self._serial_map:
            return self._serial_map.get(vehicle_id, vehicle_id)
        return vehicle_id

    def _get_fleet(self) -> Any:
        return self._fleet

    def _get_sim(self, vehicle_id: str) -> Optional[Any]:
        fleet = self._get_fleet()
        if fleet is None:
            return None
        return fleet.get_agv(self._resolve_serial(vehicle_id))

    def _coord_to_vda(self, x: Optional[float], y: Optional[float],
                      coords_in: str = "vda") -> Tuple[Optional[float], Optional[float]]:
        """按坐标来源将坐标转换到 VDA/SRC 地图坐标系."""
        if x is None or y is None:
            return x, y
        if coords_in == "tms" and self.profile and self.profile.map_transform:
            return self.profile.map_transform.tms_to_src(float(x), float(y))
        return float(x), float(y)

    def _pose_to_tms(self, x: float, y: float) -> Tuple[float, float]:
        """VDA/SRC 坐标 -> TMS 世界坐标 (用于统一状态上报)."""
        if self.profile and self.profile.map_transform:
            return self.profile.map_transform.src_to_tms(x, y)
        return x, y

    def _node_xy(self, sim: Any, node_id: str) -> Optional[Tuple[float, float]]:
        """从节点坐标映射表取节点坐标 (若未登记返回 None)."""
        pos = (self._node_positions or {}).get(node_id)
        if pos:
            return float(pos["x"]), float(pos["y"])
        if sim is not None and getattr(sim, "_node_positions", None):
            pos = sim._node_positions.get(node_id)
            if pos:
                return float(pos["x"]), float(pos["y"])
        return None

    def _finalize(self, result: CommandResult, payload: Any = None) -> CommandResult:
        """统一消息出口: 对 SEER 画像附上合规性校验结果."""
        if self.profile and payload is not None:
            msg_type = "instantAction" if isinstance(payload, Vda5050InstantAction) else "order"
            warnings = validate_message_for_seer(msg_type, payload, self.profile)
            if warnings:
                result.data["seer_warnings"] = warnings
            result.data["payload"] = payload.model_dump(by_alias=True, exclude_none=True)
        return result

    def _build_move_order(self, vehicle_id: str, target: str,
                          x: Optional[float] = None, y: Optional[float] = None,
                          theta: Optional[float] = None,
                          coords_in: str = "vda") -> Vda5050Order:
        x, y = self._coord_to_vda(x, y, coords_in)
        return Vda5050Order(
            orderId=_order_id("ord"),
            orderUpdateId=0,
            headerId=int(time.time() * 1000) % (2 ** 31),
            manufacturer=self._message_manufacturer,
            serialNumber=self._resolve_serial(vehicle_id),
            nodes=[Vda5050Node(nodeId=target, x=float(x), y=float(y),
                               theta=float(theta) if theta is not None else None)],
            edges=[],
        )

    # ============================= 生命周期 =============================

    async def connect(self) -> bool:
        """连接 (仿真模式: 建立车队/车辆映射)."""
        from ..protocols.agv_simulator import AgvFleetSimulator, fleet_simulator

        if self._vehicle_ids:
            # 自管本地车队 (按 SEER 画像或通用配置注册车辆)
            self._fleet = AgvFleetSimulator()
            mfr = self.profile.manufacturer if self.profile else self.manufacturer
            for vid in self._vehicle_ids:
                cfg = self._vehicle_configs.get(vid, {})
                serial = str(cfg.get("serial") or agv_id_to_serial(vid, self.profile))
                x = float(cfg.get("x", 0.0))
                y = float(cfg.get("y", 0.0))
                theta = float(cfg.get("theta", cfg.get("angle", 0.0)))
                sim = self._fleet.add_agv(
                    serial=serial,
                    manufacturer=mfr,
                    start_node=str(cfg.get("start_node", cfg.get("current_node", "N_P00"))),
                    start_x=x,
                    start_y=y,
                    start_theta=math.radians(theta),  # 配置按角度
                    speed=float(cfg.get("speed", 1.5)),
                    battery=float(cfg.get("battery", 100.0)),
                )
                sim.set_pose(x=x, y=y, theta=math.radians(theta))
                self._serial_map[vid] = serial
            if self._node_positions:
                self._fleet.set_node_positions(self._node_positions)
            self._vehicle_count = len(self._serial_map)
            logger.info(
                "VDA5050 adapter connected (vendor=%s): %d AGVs managed",
                self.profile.vendor_key if self.profile else "generic",
                self._vehicle_count,
            )
        else:
            # 向后兼容: 绑定全局 fleet_simulator
            self._fleet = fleet_simulator
            self._vehicle_count = len(fleet_simulator.get_all_states())
            logger.info("VDA5050 adapter connected (legacy global fleet), %d AGVs", self._vehicle_count)

        self._connected = True
        return True

    async def disconnect(self) -> None:
        """断开连接"""
        self._connected = False
        if self._fleet is not None and self._vehicle_ids:
            await self._fleet.stop_all()
        self._vehicle_count = 0
        if self._vehicle_ids:
            self._serial_map.clear()

    # ============================= 指令下发 =============================

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """下发控制指令 (SEER/VDA5050 instantAction / order 语义)."""
        params = params or {}
        ts = time.time()
        sim = self._get_sim(vehicle_id)
        if sim is None:
            return CommandResult(False, vehicle_id, command.value,
                                 f"AGV {vehicle_id} not registered (serial={self._resolve_serial(vehicle_id)})", ts)

        try:
            # ---------- 位置初始化 (仙工 SRC 二次定位) ----------
            if command == VehicleCommand.INIT_POSITION:
                return await self._cmd_init_position(vehicle_id, sim, params, ts)

            # ---------- 移动 ----------
            if command == VehicleCommand.MOVE:
                return await self._cmd_move(vehicle_id, sim, params, ts)

            # ---------- 停车 / 恢复 ----------
            if command == VehicleCommand.STOP:
                action = Vda5050InstantAction(
                    instantActionType="stop",
                    manufacturer=self._message_manufacturer,
                    serialNumber=sim.serial,
                    actionParameters=params.get("action_parameters", []),
                )
                await sim.handle_instant_action(action)
                return self._finalize(
                    CommandResult(True, vehicle_id, command.value, "急停指令已下发 (stop)", ts, {}),
                    action,
                )

            if command == VehicleCommand.RESUME:
                action = Vda5050InstantAction(
                    instantActionType="start",
                    manufacturer=self._message_manufacturer,
                    serialNumber=sim.serial,
                )
                await sim.handle_instant_action(action)
                return self._finalize(
                    CommandResult(True, vehicle_id, command.value, "恢复运行 (start)", ts, {}),
                    action,
                )

            if command == VehicleCommand.CANCEL_TASK:
                action = Vda5050InstantAction(
                    instantActionType="cancelOrder",
                    manufacturer=self._message_manufacturer,
                    serialNumber=sim.serial,
                )
                await sim.handle_instant_action(action)
                return self._finalize(
                    CommandResult(True, vehicle_id, command.value, "已取消当前任务 (cancelOrder)", ts, {}),
                    action,
                )

            # ---------- 装载 / 卸载 / 充电 ----------
            if command in (VehicleCommand.CHARGE,):
                return await self._cmd_charge(vehicle_id, sim, params, ts)

            if command in (VehicleCommand.LOAD, VehicleCommand.PICKUP):
                sim.set_load(True)
                action_type = _VDA5050_ACTION_MAP.get(command, "pickPosition")
                return CommandResult(True, vehicle_id, command.value,
                                     f"装载完成 (VDA5050 action '{action_type}')", ts,
                                     {"loaded": True, "actionType": action_type})

            if command in (VehicleCommand.UNLOAD, VehicleCommand.DROPOFF):
                sim.set_load(False)
                action_type = _VDA5050_ACTION_MAP.get(command, "dropPosition")
                return CommandResult(True, vehicle_id, command.value,
                                     f"卸货完成 (VDA5050 action '{action_type}')", ts,
                                     {"loaded": False, "actionType": action_type})

            return CommandResult(False, vehicle_id, command.value,
                                 f"Unsupported command for VDA5050: {command.value}", ts)
        except Exception as e:  # noqa: BLE001
            logger.warning("send_command(%s, %s) failed: %s", vehicle_id, command.value, e)
            return CommandResult(False, vehicle_id, command.value, str(e), ts)

    async def _cmd_init_position(self, vehicle_id: str, sim: Any,
                                 params: Dict[str, Any], ts: float) -> CommandResult:
        """位置初始化 / 二次定位."""
        pos = params.get("position") or {}
        x = params.get("x", pos.get("x"))
        y = params.get("y", pos.get("y"))
        theta = params.get("theta", pos.get("theta", params.get("angle", 0.0)))
        coords_in = params.get("coordinate_frame", "vda")

        # 允许以节点 ID 初始化 (从节点坐标表取位姿)
        node_id = params.get("node_id") or params.get("nodeId") or params.get("current_node")
        if x is None or y is None:
            xy = self._node_xy(sim, node_id) if node_id else None
            if xy:
                x, y = xy
            else:
                # 未给坐标: 维持当前位姿执行一次"同位置校准"
                x, y, theta = sim.x, sim.y, math.degrees(sim.theta)

        x, y = self._coord_to_vda(float(x), float(y), coords_in)
        theta_rad = math.radians(float(theta))
        # SRC 地图角度 -> TMS 世界角度 (仅角度对齐场景)
        if coords_in == "tms" and self.profile and self.profile.map_transform:
            theta_rad = theta_rad - math.radians(self.profile.map_transform.rotation_deg)

        sim.set_pose(x=float(x), y=float(y), theta=theta_rad, node_id=node_id)

        payload: Any = None
        detail = ""
        if self.profile:
            mode = self.profile.init_position_mode
            if mode == "instantAction":
                payload = build_init_position_instant_action(
                    self.profile, sim.serial, float(x), float(y), theta_rad,
                    map_id=params.get("map_id"),
                )
            else:
                payload = build_init_position_order(
                    self.profile, sim.serial, float(x), float(y), theta_rad,
                    map_id=params.get("map_id"),
                )
            detail = f"SRC 位置初始化报文({mode})已构造"
        else:
            detail = "位置已初始化 (simulation)"

        result = CommandResult(
            True, vehicle_id, VehicleCommand.INIT_POSITION.value,
            f"{detail}: x={x:.3f}, y={y:.3f}, theta={theta:.3f}", ts,
            {"x": x, "y": y, "theta": float(theta)},
        )
        return self._finalize(result, payload)

    async def _cmd_move(self, vehicle_id: str, sim: Any,
                        params: Dict[str, Any], ts: float) -> CommandResult:
        """移动到目标 (单点 order)."""
        target = params.get("target")
        if not target and isinstance(params.get("node"), dict):
            target = params["node"].get("nodeId") or params["node"].get("id")
        if not target:
            return CommandResult(False, vehicle_id, VehicleCommand.MOVE.value,
                                 "缺少 target (目标节点 ID)", ts)

        # 从参数/节点登记表取坐标
        node_cfg = params.get("node") if isinstance(params.get("node"), dict) else {}
        x = params.get("x", node_cfg.get("x"))
        y = params.get("y", node_cfg.get("y"))
        theta = params.get("theta", node_cfg.get("theta"))
        if x is None or y is None:
            xy = self._node_xy(sim, target)
            if xy:
                x, y = xy
        coords_in = params.get("coordinate_frame", "vda")
        if x is None or y is None:
            return CommandResult(
                False, vehicle_id, VehicleCommand.MOVE.value,
                f"目标节点 {target} 缺少坐标, 无法构造 VDA5050 order "
                "(可通过 node_positions 配置或 params.x/y 提供)", ts,
            )

        order = self._build_move_order(vehicle_id, target, x, y, theta, coords_in)
        asyncio.create_task(sim.process_order(order))
        result = CommandResult(True, vehicle_id, VehicleCommand.MOVE.value,
                               f"order 已下发, 目标 {target} (x={x}, y={y})", ts,
                               {"target": target})
        return self._finalize(result, order)

    async def _cmd_charge(self, vehicle_id: str, sim: Any,
                          params: Dict[str, Any], ts: float) -> CommandResult:
        """充电: 带 station 时前往充电点并触发充电动作, 否则原地充电."""
        station = params.get("station") or params.get("charging_station")
        if station:
            x = params.get("x")
            y = params.get("y")
            if (x is None or y is None) and station in (self._node_positions or {}):
                xy = self._node_xy(sim, station)
                x, y = xy if xy else (None, None)
            if x is None or y is None:
                return CommandResult(False, vehicle_id, VehicleCommand.CHARGE.value,
                                     f"充电桩 {station} 缺少坐标", ts)
            order = self._build_move_order(vehicle_id, station, x, y, coords_in="vda")
            charge_action_node = order.nodes[0]
            charge_action_node.actions = [Vda5050NodeAction(actionType="charge", blockingType="HARD")]
            asyncio.create_task(sim.process_order(order))
            return self._finalize(
                CommandResult(True, vehicle_id, VehicleCommand.CHARGE.value,
                              f"前往充电桩 {station} 并充电", ts, {"station": station}),
                order,
            )
        sim.set_charging(True)
        return CommandResult(True, vehicle_id, VehicleCommand.CHARGE.value,
                             "原地充电开始 (charging=true)", ts, {"charging": True})

    # ============================= 运输订单 =============================

    async def send_transport_order(
        self,
        vehicle_id: str,
        order: TransportOrderMessage,
    ) -> CommandResult:
        """下发完整 VDA5050 order (edge/node/action 序列)."""
        ts = time.time()
        sim = self._get_sim(vehicle_id)
        if sim is None:
            return CommandResult(False, vehicle_id, "transport_order",
                                 f"AGV {vehicle_id} not registered", ts)

        try:
            nodes = normalize_nodes(order.nodes, tf=self.profile.map_transform
                                    if self.profile else None,
                                    coords_in=order.coords_in if hasattr(order, "coords_in") else "vda")
            edges: List[Vda5050Edge] = []
            for e in order.edges or []:
                if not isinstance(e, dict):
                    continue
                try:
                    edges.append(Vda5050Edge(**e))
                except Exception:  # noqa: BLE001
                    logger.debug("skip invalid edge: %s", e)

            vda_order = Vda5050Order(
                orderId=order.order_id or _order_id("ord"),
                orderUpdateId=0,
                headerId=int(time.time() * 1000) % (2 ** 31),
                manufacturer=self._message_manufacturer,
                serialNumber=sim.serial,
                nodes=nodes,
                edges=edges,
            )
            asyncio.create_task(sim.process_order(vda_order))
            result = CommandResult(
                True, vehicle_id, "transport_order",
                f"VDA5050 order {order.order_id} sent ({len(nodes)} nodes)", ts,
                {"nodes": len(nodes), "edges": len(edges)},
            )
            return self._finalize(result, vda_order)
        except Exception as e:  # noqa: BLE001
            logger.warning("send_transport_order failed: %s", e)
            return CommandResult(False, vehicle_id, "transport_order", str(e), ts)

    # ============================= 状态查询 =============================

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """获取 VDA5050 AGV 状态 (统一 VehicleStatus)."""
        sim = self._get_sim(vehicle_id)
        if sim is None:
            return None

        # 位姿: SRC 地图 -> TMS 世界坐标 (若配置了地图对齐)
        wx, wy = self._pose_to_tms(sim.x, sim.y)
        theta_deg = math.degrees(sim.theta)
        if self.profile and self.profile.map_transform:
            theta_deg = theta_deg + self.profile.map_transform.rotation_deg

        if sim._errors:
            state = VehicleState.ERROR
        elif sim.charging:
            state = VehicleState.CHARGING
        elif sim.connection != "ONLINE":
            state = VehicleState.OFFLINE
        elif sim.driving:
            state = VehicleState.MOVING
        elif sim.loaded and not sim.paused:
            state = VehicleState.LOADING
        else:
            state = VehicleState.IDLE

        error_code, error_message = translate_error_states(sim._errors, self.profile)

        return VehicleStatus(
            vehicle_id=vehicle_id,
            state=state,
            x=round(wx, 6),
            y=round(wy, 6),
            angle=round(theta_deg % 360.0, 6),
            speed=sim.speed if sim.driving else 0.0,
            battery_level=round(sim.battery, 2),
            current_node=sim.current_node,
            load_status=sim.loaded,
            error_code=error_code,
            error_message=error_message,
            order_id=sim.order_id,
            last_node_id=sim.last_node_id,
            sequence_id=sim.last_node_sequence_id,
        )

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有管理车辆的 VDA5050 状态."""
        fleet = self._get_fleet()
        if fleet is None:
            return []

        if self._serial_map:
            ids = [vid for vid in self._serial_map if fleet.get_agv(self._serial_map[vid])]
        else:
            ids = list(fleet.get_all_states().keys())

        results: List[VehicleStatus] = []
        for vid in ids:
            status = await self.get_status(vid)
            if status:
                results.append(status)
        return results

    # ============================= 信息输出 =============================

    def get_profile_info(self) -> Dict[str, Any]:
        """返回厂商画像信息 (供 API/前端展示)."""
        vehicles = []
        for vid, serial in (self._serial_map or {}).items():
            sim = self._get_sim(vid)
            vehicles.append({
                "vehicle_id": vid,
                "serial_number": serial,
                "agv_id": serial_to_agv_id(serial, self.profile),
                "x": round(sim.x, 3) if sim else None,
                "y": round(sim.y, 3) if sim else None,
            })
        info: Dict[str, Any] = {
            "name": self.name,
            "protocol": self.protocol,
            "mode": self.mode,
            "vendor": self.profile.vendor_key if self.profile else "generic",
            "manufacturer": self._message_manufacturer,
            "version": self.profile.protocol_version if self.profile else "2.0.0",
            "vehicles": vehicles,
        }
        if self.profile and vehicles:
            info["topics"] = seer_topics(self.profile, vehicles[0]["serial_number"])
        if self.profile and self.profile.map_transform:
            mt = self.profile.map_transform
            info["coord_transform"] = {
                "offset_x": mt.offset_x,
                "offset_y": mt.offset_y,
                "rotation_deg": mt.rotation_deg,
                "scale": mt.scale,
            }
        return info
