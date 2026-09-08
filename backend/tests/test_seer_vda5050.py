"""
仙工智能 (SEER) SRC 控制器 × VDA5050 通道增强单元测试.

覆盖:
  1. SEER 厂商画像: AGVId <-> serialNumber 映射 / 主题族 / 位置初始化报文 / 合规校验
  2. 坐标系对齐: SRC 地图 <-> TMS 世界地图 (旋转/平移/缩放)
  3. AGV 仿真器增强: 初始位姿/装载/充电/即时动作
  4. Vda5050VehicleAdapter SEER 画像: 连接、路由、状态、位置初始化、指令修复

运行: pytest tests/test_seer_vda5050.py -v
"""

import asyncio
import math

import pytest

from app.protocols.seer_src import (
    CoordTransform,
    SeerVda5050Profile,
    agv_id_to_serial,
    build_init_position_instant_action,
    build_init_position_order,
    seer_topics,
    serial_to_agv_id,
    translate_error_states,
    validate_message_for_seer,
)
from app.protocols.vda5050 import (
    Vda5050InstantAction,
    Vda5050Order,
)
from app.protocols.agv_simulator import AgvSimulator, AgvFleetSimulator
from app.adapters.vda5050_vehicle_adapter import Vda5050VehicleAdapter
from app.adapters.base_adapter import (
    VehicleCommand,
    VehicleState,
    TransportOrderMessage,
)
from app.adapters.adapter_manager import AdapterManager


# =============================================================================
# 1. SEER 厂商画像单元
# =============================================================================

class TestSeerProfileMapping:
    def test_profile_from_vendor_aliases(self):
        assert SeerVda5050Profile.from_vendor("seer") is not None
        assert SeerVda5050Profile.from_vendor("SEER-SRC") is not None
        assert SeerVda5050Profile.from_vendor("仙工智能") is not None
        assert SeerVda5050Profile.from_vendor("hikvision") is None
        assert SeerVda5050Profile.from_vendor(None) is None

    def test_profile_defaults(self):
        p = SeerVda5050Profile.from_vendor("seer")
        assert p.manufacturer == "SEER"
        assert p.protocol_version == "2.0.0"
        assert p.serial_prefix == "SRC-"
        assert p.topic_base == "uagv/v2"

    def test_agv_id_serial_mapping(self):
        # 通用 (无画像): 原样
        assert agv_id_to_serial("AGV2", None) == "AGV2"
        assert serial_to_agv_id("AGV2", None) == "AGV2"
        # SEER 画像: 补前缀 / 去前缀
        p = SeerVda5050Profile.from_vendor("seer")
        assert agv_id_to_serial("AGV2", p) == "SRC-AGV2"
        assert agv_id_to_serial("SRC-8F00A1", p) == "SRC-8F00A1"  # 已含前缀
        assert serial_to_agv_id("SRC-AGV2", p) == "AGV2"
        # 双向往返: AGVId -> serial -> AGVId
        assert serial_to_agv_id(agv_id_to_serial("AGV2", p), p) == "AGV2"


class TestSeerCoordTransform:
    def test_identity(self):
        tf = CoordTransform()
        assert tf.is_identity()
        assert tf.src_to_tms(3, -4) == (3.0, -4.0)
        assert tf.tms_to_src(3, -4) == (3.0, -4.0)

    def test_offset_only(self):
        tf = CoordTransform(offset_x=5, offset_y=-2)
        assert tf.src_to_tms(10, 3) == (15.0, 1.0)
        assert tf.tms_to_src(15, 1) == (10.0, 3.0)

    def test_rotation_and_scale_roundtrip(self):
        tf = CoordTransform(offset_x=100, offset_y=50, rotation_deg=90, scale=2)
        # src(3,4)*scale2=(6,8); 旋转90° -> (-8,6); 平移 -> (92,56)
        x, y = tf.src_to_tms(3, 4)
        assert (x, y) == pytest.approx((92.0, 56.0))
        back = tf.tms_to_src(x, y)
        assert back == pytest.approx((3.0, 4.0))

    def test_from_dict(self):
        tf = CoordTransform.from_dict({"offset_x": 1, "offset_y": 2, "rotation_deg": 0, "scale": 1})
        assert tf is not None and tf.src_to_tms(0, 0) == (1.0, 2.0)
        assert CoordTransform.from_dict(None) is None


class TestSeerMessages:
    def test_topics(self):
        p = SeerVda5050Profile.from_vendor("seer")
        topics = seer_topics(p, "SRC-8F00A1")
        assert topics["order"] == "uagv/v2/SEER/SRC-8F00A1/order"
        assert topics["state"] == "uagv/v2/SEER/SRC-8F00A1/state"
        assert topics["instantAction"] == "uagv/v2/SEER/SRC-8F00A1/instantAction"

    def test_build_init_position_order(self):
        p = SeerVda5050Profile.from_vendor("seer")
        order = build_init_position_order(p, "SRC-8F00A1", 12.5, 34.25, 1.5708, map_id="mapA")
        data = order.model_dump(by_alias=True, exclude_none=True)
        assert data["manufacturer"] == "SEER"
        assert data["serialNumber"] == "SRC-8F00A1"
        assert len(order.nodes) == 1
        node = order.nodes[0]
        assert node.nodeId.startswith("IP-mapA-")
        assert node.x == pytest.approx(12.5)
        assert node.y == pytest.approx(34.25)
        types = [a.actionType for a in node.actions]
        assert "initPosition" in types
        params = {}
        for a in node.actions:
            if a.actionType == "initPosition":
                params.update({p["key"]: p["value"] for p in a.actionParameters})
        assert params["mapId"] == "mapA"
        assert params["x"] == pytest.approx(12.5)

    def test_build_init_position_instant_action(self):
        p = SeerVda5050Profile.from_vendor("seer", init_position_mode="instantAction")
        action = build_init_position_instant_action(p, "SRC-8F00A1", 1, 2, 0.5)
        assert action.instant_action_type == "initPosition"
        keys = [ap["key"] for ap in action.action_parameters]
        assert keys == ["x", "y", "theta"]

    def test_validate_message_warnings(self):
        p = SeerVda5050Profile.from_vendor("seer")
        # 合规 order -> 无警告 (manufacturer/serial 前缀均符合)
        order = build_init_position_order(p, "SRC-8F00A1", 1, 1)
        assert validate_message_for_seer("order", order, p) == []

        # 违规 order -> 厂商/序列号/坐标警告
        bad = Vda5050Order(orderId="x", nodes=[])
        warnings = validate_message_for_seer("order", bad, p)
        joined = " ".join(warnings)
        assert "manufacturer" in joined and "nodes" in joined

        # 非标 instantAction -> 警告
        action = Vda5050InstantAction(instantActionType="teleport")
        warns = validate_message_for_seer("instantAction", action, p)
        assert any("teleport" in w for w in warns)

    def test_translate_error_states(self):
        p = SeerVda5050Profile.from_vendor("seer")
        code, msg = translate_error_states(
            [{"errorType": "localization",
              "errorReferences": [{"referenceKey": "code", "referenceValue": "E401"}],
              "errorDescription": "定位丢失"}],
            p,
        )
        assert code >= 1000 and "定位丢失" in msg
        assert translate_error_states([], p) == (0, "")


# =============================================================================
# 2. AGV 仿真器增强单元
# =============================================================================

class TestAgvSimulatorEnhance:
    def test_set_pose(self):
        sim = AgvSimulator(serial="SRC-8F00A1", manufacturer="SEER")
        sim.set_pose(x=10, y=20, theta=math.radians(90), node_id="N042")
        assert (sim.x, sim.y) == (10.0, 20.0)
        assert sim.theta == pytest.approx(math.radians(90))
        assert sim.last_node_id == "N042"
        state = sim.get_state()
        assert state.agv_position["x"] == 10.0

    def test_load_and_charging_flags(self):
        sim = AgvSimulator(serial="SRC-1")
        sim.set_load(True)
        assert sim.loaded
        sim.set_charging(True)
        assert sim.charging
        assert sim.get_state().battery_state["charging"] is True
        sim.set_charging(False)
        assert not sim.charging

    @pytest.mark.asyncio
    async def test_instant_action_stop_resume(self):
        sim = AgvSimulator(serial="SRC-2")
        await sim.handle_instant_action(Vda5050InstantAction(instantActionType="stop"))
        assert sim.paused and not sim.driving
        await sim.handle_instant_action(Vda5050InstantAction(instantActionType="start"))
        assert not sim.paused
        await sim.handle_instant_action(Vda5050InstantAction(instantActionType="resume"))
        assert not sim.paused
        await sim.handle_instant_action(Vda5050InstantAction(instantActionType="cancelOrder"))
        assert sim.order_id == ""

    def test_fleet_manufacturer(self):
        fleet = AgvFleetSimulator()
        sim = fleet.add_agv(serial="SRC-9F00", manufacturer="SEER", start_theta=math.radians(90))
        assert sim.manufacturer == "SEER"
        assert fleet.get_agv("SRC-9F00") is sim
        assert sim.theta == pytest.approx(math.radians(90))


# =============================================================================
# 3. Vda5050VehicleAdapter SEER 增强
# =============================================================================

def _seer_adapter(**cfg):
    base = {
        "vendor": "seer",
        "vehicle_ids": ["VEH-1", "VEH-2"],
        "vehicle_configs": {
            "VEH-1": {"serial": "SRC-8F00A1", "x": 1.0, "y": 2.0, "theta": 0.0, "battery": 88.0},
            "VEH-2": {"serial": "SRC-8F00A2", "x": 10.0, "y": 10.0, "theta": 90.0},
        },
        "node_positions": {"N042": {"x": 5.0, "y": 5.0}, "CHARGE01": {"x": 50.0, "y": 50.0}},
    }
    base.update(cfg)
    return Vda5050VehicleAdapter(**base)


class TestSeerAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_connect_and_route_info(self):
        adapter = _seer_adapter()
        try:
            assert await adapter.connect()
            assert adapter.is_connected
            assert adapter.vehicle_count == 2
            assert adapter._serial_map == {"VEH-1": "SRC-8F00A1", "VEH-2": "SRC-8F00A2"}

            info = adapter.get_profile_info()
            assert info["vendor"] == "seer"
            assert info["manufacturer"] == "SEER"
            assert len(info["vehicles"]) == 2
            assert info["topics"]["order"] == "uagv/v2/SEER/SRC-8F00A1/order"

            statuses = await adapter.get_all_statuses()
            assert sorted(s.vehicle_id for s in statuses) == ["VEH-1", "VEH-2"]
            v1 = await adapter.get_status("VEH-1")
            assert v1.state == VehicleState.IDLE
            assert v1.battery_level == pytest.approx(88.0)
            assert (v1.x, v1.y) == (1.0, 2.0)
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_generic_legacy_without_ids(self):
        adapter = Vda5050VehicleAdapter(manufacturer="AGV-TMS")
        try:
            assert await adapter.connect()
            assert adapter.get_profile_info()["vendor"] == "generic"
            assert await adapter.get_all_statuses() == []
        finally:
            await adapter.disconnect()


class TestSeerAdapterCommands:
    @pytest.mark.asyncio
    async def test_move_requires_coordinates(self):
        adapter = _seer_adapter()
        try:
            await adapter.connect()
            r = await adapter.send_command("VEH-1", VehicleCommand.MOVE, {"target": "N-NO-COORD"})
            assert not r.success and "坐标" in r.message
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_move_with_node_positions(self):
        adapter = _seer_adapter()
        try:
            await adapter.connect()
            sim = adapter._get_sim("VEH-1")
            r = await adapter.send_command("VEH-1", VehicleCommand.MOVE, {"target": "N042"})
            assert r.success and "N042" in r.message
            assert "seer_warnings" in r.data or "payload" in r.data
            # 等待 process_order 接管
            for _ in range(20):
                if sim.order_id:
                    break
                await asyncio.sleep(0.02)
            assert sim.order_id
            assert sim.driving
            # 停车冻结
            r2 = await adapter.send_command("VEH-1", VehicleCommand.STOP)
            assert r2.success
            assert sim.paused and not sim.driving
        finally:
            await adapter.disconnect()
            await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_move_with_explicit_xy(self):
        adapter = _seer_adapter()
        try:
            await adapter.connect()
            r = await adapter.send_command(
                "VEH-2", VehicleCommand.MOVE,
                {"target": "N100", "x": 20.0, "y": 30.0, "theta": 90.0},
            )
            assert r.success
            assert r.data["target"] == "N100"
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_init_position_updates_pose_and_payload(self):
        adapter = _seer_adapter()
        try:
            await adapter.connect()
            sim = adapter._get_sim("VEH-1")
            r = await adapter.send_command(
                "VEH-1", VehicleCommand.INIT_POSITION,
                {"x": 5.5, "y": 6.5, "theta": 90.0, "map_id": "src_map_1"},
            )
            assert r.success
            assert sim.x == pytest.approx(5.5)
            assert sim.y == pytest.approx(6.5)
            assert sim.theta == pytest.approx(math.radians(90))
            # SEER 画像默认按 order 模式生成初始化报文
            payload = r.data.get("payload")
            assert payload is not None and payload["orderId"].startswith("SRC-IP-")
            status = await adapter.get_status("VEH-1")
            assert status.x == pytest.approx(5.5)
            assert status.angle == pytest.approx(90.0 % 360)
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_init_position_instant_action_mode(self):
        adapter = _seer_adapter(init_position_mode="instantAction")
        try:
            await adapter.connect()
            r = await adapter.send_command(
                "VEH-1", VehicleCommand.INIT_POSITION, {"x": 1, "y": 1, "theta": 0},
            )
            assert r.success
            assert r.data["payload"]["instantActionType"] == "initPosition"
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_load_unload_charge(self):
        adapter = _seer_adapter()
        try:
            await adapter.connect()
            assert (await adapter.send_command("VEH-1", VehicleCommand.LOAD)).success
            assert (await adapter.get_status("VEH-1")).load_status is True
            assert (await adapter.send_command("VEH-1", VehicleCommand.UNLOAD)).success
            assert (await adapter.get_status("VEH-1")).load_status is False
            # 原地充电
            r = await adapter.send_command("VEH-1", VehicleCommand.CHARGE)
            assert r.success
            assert adapter._get_sim("VEH-1").charging
            adapter._get_sim("VEH-1").set_charging(False)
            # 充电桩无坐标 -> 失败 (给出指引)
            r2 = await adapter.send_command("VEH-1", VehicleCommand.CHARGE, {"station": "CS-NOPE"})
            assert not r2.success
            assert "坐标" in r2.message
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_transport_order(self):
        adapter = _seer_adapter()
        try:
            await adapter.connect()
            order = TransportOrderMessage(
                order_id="TASK-9001",
                nodes=[
                    {"nodeId": "N_A", "x": 1.0, "y": 1.0},
                    {"nodeId": "N_B", "x": 2.0, "y": 2.0},
                    {"nodeId": "N_C", "x": 3.0, "y": 3.0},
                ],
            )
            r = await adapter.send_transport_order("VEH-1", order)
            assert r.success
            assert r.data["nodes"] == 3
            sim = adapter._get_sim("VEH-1")
            for _ in range(20):
                if sim.order_id == "TASK-9001":
                    break
                await asyncio.sleep(0.02)
            assert sim.order_id == "TASK-9001"
        finally:
            await adapter.disconnect()
            await asyncio.sleep(0.05)


class TestSeerAdapterCoordinateTransform:
    @pytest.mark.asyncio
    async def test_status_translated_to_tms(self):
        adapter = _seer_adapter(
            map_transform={"offset_x": 10.0, "offset_y": 20.0, "rotation_deg": 0.0, "scale": 1.0}
        )
        try:
            await adapter.connect()
            status = await adapter.get_status("VEH-1")  # SRC(1,2) -> TMS(11,22)
            assert status.x == pytest.approx(11.0)
            assert status.y == pytest.approx(22.0)
        finally:
            await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_init_position_with_tms_coords(self):
        adapter = _seer_adapter(
            map_transform={"offset_x": 10.0, "offset_y": 20.0, "rotation_deg": 0.0, "scale": 1.0}
        )
        try:
            await adapter.connect()
            sim = adapter._get_sim("VEH-1")
            # TMS 坐标 (15,35) -> SRC 坐标 (5,15)
            r = await adapter.send_command(
                "VEH-1", VehicleCommand.INIT_POSITION,
                {"x": 15.0, "y": 35.0, "theta": 0.0, "coordinate_frame": "tms"},
            )
            assert r.success
            assert sim.x == pytest.approx(5.0)
            assert sim.y == pytest.approx(15.0)
            status = await adapter.get_status("VEH-1")
            assert status.x == pytest.approx(15.0)
            assert status.y == pytest.approx(35.0)
        finally:
            await adapter.disconnect()


class TestAdapterManagerWithSeer:
    @pytest.mark.asyncio
    async def test_start_route_and_stop(self):
        mgr = AdapterManager()
        try:
            adapter = await mgr.start_adapter("vda5050", {
                "vendor": "seer",
                "manufacturer": "SEER",
                "vehicle_ids": ["SEER-01"],
                "vehicle_configs": {"SEER-01": {"serial": "SRC-5F5F5F", "x": 0.0, "y": 0.0}},
            })
            assert adapter.vehicle_count == 1
            info = mgr.get_info()
            assert info["total_vehicles"] == 1
            assert info["vehicle_routes"]["SEER-01"] == "vda5050"
            status = await mgr.get_status("SEER-01")
            assert status.vehicle_id == "SEER-01"
            # 通过路由表统一指令
            r = await mgr.send_command("SEER-01", VehicleCommand.INIT_POSITION,
                                       {"x": 3.0, "y": 4.0, "theta": 0.0})
            assert r.success
        finally:
            await mgr.stop_all()
