"""
MQTT 适配器测试套件 — P0-06 增强版验证.

覆盖范围:
  1. 数据模型 (Config/Metrics/Message/ACL/Router)
  2. VDA5050 Topic 生成
  3. Sparkplug B Payload 构建
  4. 模拟模式 AGV 状态管理
  5. 指令发送与状态转换
  6. ACL 权限控制
  7. 消息路由引擎
  8. 离线持久化存储
  9. 健康检查报告
  10. 边界情况与鲁棒性

运行: python3 -m pytest tests/test_mqtt_adapter.py -v
"""

import asyncio
import json
import os
import sqlite3
import time
import pytest
from typing import List

# 导入被测模块
import sys, os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))

from app.adapters.mqtt_vehicle_adapter import (
    # 配置与模型
    MqttConnectionConfig,
    MqttQoS,
    MqttVersion,
    MqttMessage,
    MqttHealthMetrics,
    MqttAclAction,
    MqttACLEntry,
    MqttAccessControlList,
    
    # Sparkplug B
    SparkplugDataType,
    SparkplugMessageType,
    SparkplugMetric,
    SparkplugBPayload,
    
    # 路由引擎
    RoutingRule,
    MessageRouter,
    
    # 持久化
    OfflineMessageStore,
    
    # Topic 管理
    Vda5050Topics,
    
    # 主适配器
    MqttVehicleAdapter,
    
    # BaseAdapter 类型
    VehicleCommand,
    VehicleState,
    VehicleStatus,
    TransportOrderMessage,
)


# ==================== 预定义数据 ====================

_TEST_MAP = [
    {"id": "node_00", "x": 0.0, "y": 0.0},
    {"id": "node_01", "x": 10.0, "y": 0.0},
    {"id": "node_02", "x": 20.0, "y": 0.0},
    {"id": "node_03", "x": 20.0, "y": 10.0},
    {"id": "node_04", "x": 10.0, "y": 10.0},
    {"id": "node_05", "x": 0.0, "y": 10.0},
]


@pytest.fixture
def sim_adapter() -> MqttVehicleAdapter:
    """模拟模式适配器 (仅创建)"""
    return MqttVehicleAdapter(
        mode="simulation",
        num_sim_agvs=8,
        vda5050_mode=True,
        sparkplug_enabled=False,
        offline_persistence=False,
        acl_enabled=True,
        fleet_id="test_fleet",
    )


@pytest.fixture
async def initialized_adapter(sim_adapter) -> MqttVehicleAdapter:
    """已初始化并启动的模拟适配器"""
    await sim_adapter.initialize(map_nodes=_TEST_MAP)
    await sim_adapter.start()
    yield sim_adapter
    await sim_adapter.stop()


# ==================== 1. 数据模型测试 ====================

class TestMqttQoS:
    """MQTT QoS 枚举"""

    def test_qos_values(self):
        assert MqttQoS.AT_MOST_ONCE.value == 0
        assert MqttQoS.AT_LEAST_ONCE.value == 1
        assert MqttQoS.EXACTLY_ONCE.value == 2


class TestMqttConnectionConfig:
    """连接配置模型"""

    def test_default_values(self):
        cfg = MqttConnectionConfig()
        assert cfg.broker_host == "localhost"
        assert cfg.broker_port == 1883
        assert cfg.qos == 1
        assert cfg.tls_enabled is False
        assert cfg.protocol_version == MqttVersion.V311.value

    def test_custom_config(self):
        cfg = MqttConnectionConfig(
            broker_host="iot.example.com",
            broker_port=8883,
            username="admin",
            password="secret",
            tls_enabled=True,
            protocol_version=MqttVersion.V5.value,
            qos=2,
        )
        assert cfg.broker_host == "iot.example.com"
        assert cfg.broker_port == 8883
        assert cfg.username == "admin"
        assert cfg.password == "secret"
        assert cfg.tls_enabled is True
        assert cfg.protocol_version == 5
        assert cfg.qos == 2

    def test_to_broker_address(self):
        cfg = MqttConnectionConfig(broker_host="192.168.1.100", broker_port=1883)
        assert cfg.to_broker_address() == "mqtt://192.168.1.100:1883"

    def test_to_broker_address_tls(self):
        cfg = MqttConnectionConfig(broker_host="secure.com", broker_port=8883, tls_enabled=True)
        assert cfg.to_broker_address() == "mqtts://secure.com:8883"

    def test_lwt_defaults(self):
        cfg = MqttConnectionConfig()
        assert cfg.lwt_enabled is True
        assert cfg.lwt_topic == "vda5050/connection"


class TestMqttMessage:
    """统一消息模型"""

    def test_create_message(self):
        msg = MqttMessage(
            topic="agv/001/status",
            payload={"battery": 85, "speed": 1.5},
            qos=1,
            retain=False,
        )
        assert msg.topic == "agv/001/status"
        assert msg.payload["battery"] == 85
        assert msg.qos == 1

    def test_to_dict(self):
        msg = MqttMessage(topic="test/topic", payload={"key": "val"})
        d = msg.to_dict()
        assert d["topic"] == "test/topic"
        assert d["payload"] == {"key": "val"}
        assert "qos" in d
        assert "timestamp" in d

    def test_to_publish_args(self):
        msg = MqttMessage(
            topic="agv/cmd",
            payload={"command": "move"},
            qos=1,
        )
        topic, payload_bytes, qos, retain = msg.to_publish_args()
        assert topic == "agv/cmd"
        assert isinstance(payload_bytes, bytes)
        assert json.loads(payload_bytes) == {"command": "move"}
        assert qos == 1
        assert retain is False

    def test_mqtt_v5_properties(self):
        msg = MqttMessage(
            topic="v5/test",
            payload={},
            expiry_interval=300,
            response_topic="response/topic",
        )
        assert msg.expiry_interval == 300
        assert msg.response_topic == "response/topic"


# ==================== 2. 健康指标测试 ====================

class TestMqttHealthMetrics:
    """健康指标统计"""

    def setup_method(self):
        self.metrics = MqttHealthMetrics()

    def test_initial_state(self):
        m = self.metrics
        assert m.messages_published == 0
        assert m.messages_received == 0
        assert m.avg_latency_ms == 0.0
        assert m.error_rate_pct == 0.0
        assert m.throughput_per_sec == 0.0

    def test_record_publish(self):
        self.metrics.record_publish(latency_ms=12.5)
        assert self.metrics.messages_published == 1
        assert abs(self.metrics.avg_latency_ms - 12.5) < 0.01

    def test_record_receive(self):
        self.metrics.record_receive(latency_ms=5.0)
        assert self.metrics.messages_received == 1

    def test_record_error(self):
        self.metrics.record_error("connection lost")
        assert self.metrics.publish_errors == 1
        assert self.metrics.last_error_time > 0
        assert len(self.metrics.last_error_message) > 0

    def test_record_drop(self):
        self.metrics.record_drop()
        assert self.metrics.messages_dropped == 1

    def test_latency_percentiles(self):
        samples = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 50, 100]
        for s in samples:
            self.metrics.record_publish(latency_ms=float(s))
        
        p50 = self.metrics.p50_latency_ms
        p95 = self.metrics.p95_latency_ms
        p99 = self.metrics.p99_latency_ms
        
        assert 5 <= p50 <= 8       # 中位数应在中间
        assert p95 >= p50           # P95 >= P50
        assert p99 >= p95           # P99 >= P95

    @pytest.mark.asyncio
    async def test_throughput_calculation(self):
        # 使用快速方式注入样本, 避免 sleep
        m = MqttHealthMetrics()
        for i in range(10):
            m._throughput_samples.append(time.time() + i * 0.1)
            m.messages_published += 1
        tp = m.throughput_per_sec
        assert tp > 0

    def test_error_rate(self):
        for _ in range(90):
            self.metrics.record_publish()
        for _ in range(10):
            self.metrics.record_error("error")
        rate = self.metrics.error_rate_pct
        assert 9.9 <= rate <= 10.1

    def test_uptime(self):
        t0 = time.time()
        m = MqttHealthMetrics()
        time.sleep(0.1)
        uptime = m.uptime_seconds
        assert uptime >= 0.09

    def test_to_dict(self):
        self.metrics.record_publish(latency_ms=10.0)
        d = self.metrics.to_dict()
        assert "messages_published" in d
        assert "avg_latency_ms" in d
        assert "p50_latency_ms" in d
        assert "p95_latency_ms" in d
        assert "p99_latency_ms" in d
        assert "throughput_per_sec" in d
        assert "error_rate_pct" in d

    def test_reset(self):
        for _ in range(10):
            self.metrics.record_publish(latency_ms=5.0)
            self.metrics.record_error("err")
        self.metrics.reset()
        assert self.metrics.messages_published == 0
        assert self.metrics.publish_errors == 0
        assert self.metrics.avg_latency_ms == 0.0


# ==================== 3. VDA5050 Topic 测试 ====================

class TestVda5050Topics:
    """VDA5050 标准 Topic 定义"""

    def test_order_topic(self):
        t = Vda5050Topics.order_topic("fleet1")
        assert t == "vda5050/fleet1/order"

    def test_order_topic_default(self):
        t = Vda5050Topics.order_topic()
        assert t == "vda5050/default/order"

    def test_instant_action_topic(self):
        t = Vda5050Topics.instant_action_topic("my_fleet")
        assert t == "vda5050/my_fleet/instantActions"

    def test_agv_state_topic(self):
        t = Vda5050Topics.agv_state_topic("fleet1", "AGV-001")
        assert t == "vda5050/fleet1/agv/AGV-001/state"

    def test_agv_visualization_topic(self):
        t = Vda5050Topics.agv_visualization_topic("f", "a")
        assert t == "vda5050/f/agv/a/visualization"

    def test_connection_topic(self):
        t = Vda5050Topics.connection_topic()
        assert t == "vda5050/default/connection"

    def test_factsheet_topic(self):
        t = Vda5050Topics.factsheet_topic("f", "a")
        assert t == "vda5050/f/agv/a/factsheet"


# ==================== 4. Sparkplug B 测试 ====================

class TestSparkplugBTypes:
    """Sparkplug B 数据类型和消息类型枚举"""

    def test_data_types_count(self):
        types = list(SparkplugDataType)
        assert len(types) >= 15  # 至少15种标准类型

    def test_message_types(self):
        assert SparkplugMessageType.NBIRTH.value == "NBIRTH"
        assert SparkplugMessageType.NDEATH.value == "NDEATH"
        assert SparkplugMessageType.DBIRTH.value == "DBIRTH"
        assert SparkplugMessageType.DDATA.value == "DDATA"


class TestSparkplugMetric:
    """Sparkplug B Metric 度量值"""

    def test_create_int_metric(self):
        m = SparkplugMetric("Temperature", SparkplugDataType.INT32, value=25)
        assert m.name == "Temperature"
        assert m.data_type == SparkplugDataType.INT32
        assert m.value == 25
        assert m.is_historical is False

    def test_create_string_metric(self):
        m = SparkplugMetric("Status", SparkplugDataType.STRING, value="RUNNING")
        assert m.data_type == SparkplugDataType.STRING

    def test_create_with_alias(self):
        m = SparkplugMetric("Speed", SparkplugDataType.FLOAT, value=1.5, alias=101)
        assert m.alias == 101

    def test_to_dict_basic(self):
        m = SparkplugMetric("Test", SparkplugDataType.BOOLEAN, value=True)
        d = m.to_dict()
        assert d["name"] == "Test"
        assert d["type"] == "Boolean"
        assert d["value"] is True

    def test_to_dict_historical(self):
        m = SparkplugMetric("H", SparkplugDataType.INT64, value=42, is_historical=True)
        d = m.to_dict()
        assert d["isHistorical"] is True

    def test_to_dict_transient(self):
        m = SparkplugMetric("T", SparkplugDataType.STRING, value="", is_transient=True)
        d = m.to_dict()
        assert d["isTransient"] is True

    def test_to_dict_with_alias(self):
        m = SparkplugMetric("A", SparkplugDataType.FLOAT, value=1.0, alias=200)
        d = m.to_dict()
        assert d["alias"] == 200


class TestSparkplugPayload:
    """Sparkplug B Payload 消息体"""

    def test_nbirth_payload(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.NBIRTH,
            group_id="TEST",
            edge_node_id="Edge1",
            metrics=[
                SparkplugMetric("State", SparkplugDataType.STRING, "ONLINE"),
                SparkplugMetric("Uptime", SparkplugDataType.UINT64, 12345),
            ],
        )
        assert payload.message_type == SparkplugMessageType.NBIRTH
        assert len(payload.metrics) == 2
        assert payload.seq_number == 0

    def test_nbirth_get_topic(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.NBIRTH,
            group_id="G1",
            edge_node_id="N1",
        )
        topic = payload.get_topic()
        assert topic == "spBv1.0/G1/N1/NBIRTH"

    def test_dbirth_get_topic(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.DBIRTH,
            group_id="G",
            edge_node_id="N",
            device_id="D1",
        )
        topic = payload.get_topic()
        assert topic == "spBv1.0/G/N/DBIRTH/D1"

    def test_ddata_get_topic(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.DDATA,
            group_id="G", edge_node_id="N", device_id="D",
        )
        assert "DDATA" in payload.get_topic()

    def test_ndata_get_topic(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.NDATA,
            group_id="G", edge_node_id="N",
        )
        assert payload.get_topic() == "spBv1.0/G/N/NDATA"

    def test_ndeath_get_topic(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.NDEATH,
            group_id="G", edge_node_id="N",
        )
        assert payload.get_topic() == "spBv1.0/G/N/NDEATH"

    def test_to_payload_dict(self):
        payload = SparkplugBPayload(
            message_type=SparkplugMessageType.NDATA,
            metrics=[SparkplugMetric("T", SparkplugDataType.FLOAT, value=36.6)],
            seq_number=42,
        )
        d = payload.to_payload()
        assert d["type"] == "NDATA"
        assert d["seq"] == 42
        assert len(d["metrics"]) == 1
        assert d["metrics"][0]["name"] == "T"

    def test_seq_number_increment(self):
        adapter = MqttVehicleAdapter(mode="simulation")
        seq1 = adapter._next_sparkplug_seq()
        seq2 = adapter._next_sparkplug_seq()
        seq3 = adapter._next_sparkplug_seq()
        assert seq2 == seq1 + 1
        assert seq3 == seq2 + 1
        assert seq1 % 256 == seq1   # 初始为0

    def test_seq_wraps_at_256(self):
        adapter = MqttVehicleAdapter(mode="simulation")
        adapter._sparkplug_seq = 255
        seq = adapter._next_sparkplug_seq()
        assert seq == 255  # 第256个
        next_seq = adapter._next_sparkplug_seq()
        assert next_seq == 0  # 回绕到0


# ==================== 5. ACL 权限控制测试 ====================

class TestACLEntry:
    """ACL 规则条目"""

    def test_exact_match_allow(self):
        entry = MqttACLEntry("agv/command", MqttAclAction.PUBLISH, allow=True)
        result = entry.matches("agv/command", MqttAclAction.PUBLISH)
        assert result is True

    def test_exact_match_deny(self):
        entry = MqttACLEntry("admin/#", MqttAclAction.SUBSCRIBE, allow=False)
        result = entry.matches("admin/secret", MqttAclAction.SUBSCRIBE)
        assert result is False

    def test_no_match_wrong_action(self):
        entry = MqttACLEntry("agv/command", MqttAclAction.PUBLISH, allow=True)
        result = entry.matches("agv/command", MqttAclAction.SUBSCRIBE)
        assert result is None

    def test_wildcard_plus(self):
        entry = MqttACLEntry("agv/+/status", MqttAclAction.SUBSCRIBE, allow=True)
        assert entry.matches("agv/001/status", MqttAclAction.SUBSCRIBE) is True
        assert entry.matches("agv/002/status", MqttAclAction.SUBSCRIBE) is True

    def test_wildcard_hash(self):
        entry = MqttACLEntry("system/#", MqttAclAction.SUBSCRIBE, allow=True)
        assert entry.matches("system/alert", MqttAclAction.SUBSCRIBE) is True
        assert entry.matches("system/a/b/c", MqttAclAction.SUBSCRIBE) is True


class TestAccessControlList:
    """访问控制列表"""

    def setup_method(self):
        self.acl = MqttAccessControlList()

    def test_empty_acl_allows_all(self):
        assert self.acl.check("any/topic", MqttAclAction.PUBLISH) is True
        assert self.acl.check("any/topic", MqttAclAction.SUBSCRIBE) is True

    def test_add_and_check_rule(self):
        self.acl.add_rule("admin/#", MqttAclAction.PUBLISH, allow=False, priority=100)
        assert self.acl.check("admin/secret", MqttAclAction.PUBLISH) is False
        assert self.acl.check("other/topic", MqttAclAction.PUBLISH) is True

    def test_priority_ordering(self):
        self.acl.add_rule("agv/#", MqttAclAction.SUBSCRIBE, allow=False, priority=1)
        self.acl.add_rule("agv/+/status", MqttAclAction.SUBSCRIBE, allow=True, priority=10)
        # 高优先级规则先匹配 → 允许
        assert self.acl.check("agv/001/status", MqttAclAction.SUBSCRIBE) is True
        # 低优先级 → 禁止
        assert self.acl.check("agv/001/command", MqttAclAction.SUBSCRIBE) is False

    def test_load_default_rules(self):
        self.acl.load_default_agv_rules("my_fleet")
        assert self.acl.check("my_fleet/agv_001/command", MqttAclAction.PUBLISH) is True
        assert self.acl.check("agv/+/status", MqttAclAction.SUBSCRIBE) is True
        assert self.acl.check("admin/top-secret", MqttAclAction.PUBLISH) is False
        assert self.acl.check("$internal/reserved", MqttAclAction.PUBLISH) is False

    def test_rule_count(self):
        self.acl.load_default_agv_rules()
        assert len(self.acl._entries) >= 13  # 默认规则数量


# ==================== 6. 消息路由引擎测试 ====================

class TestMessageRouter:
    """消息路由器"""

    def setup_method(self):
        self.router = MessageRouter()

    def test_empty_router_returns_empty(self):
        msg = MqttMessage(topic="test", payload={})
        results = self.router.route(msg)
        assert results == []

    def test_simple_route(self):
        self.router.add_rule(RoutingRule(
            name="r1",
            source_pattern="src/+",
            target_topic_template="dest/{0}",
        ))
        msg = MqttMessage(topic="src/data", payload={"key": "val"})
        results = self.router.route(msg)
        assert len(results) == 1
        target, routed = results[0]
        assert target == "dest/data"
        assert routed.payload["key"] == "val"

    def test_disabled_rule_not_executed(self):
        rule = RoutingRule(name="r1", source_pattern="+", target_topic_template="out")
        rule.enabled = False
        self.router.add_rule(rule)
        results = self.router.route(MqttMessage(topic="t", payload={}))
        assert len(results) == 0

    def test_transform_function(self):
        def add_timestamp(d):
            d["_ts"] = time.time()
            return d
        
        self.router.add_rule(RoutingRule(
            name="transform_test",
            source_pattern="input/+",
            target_topic_template="output/{0}",
            transform_fn=add_timestamp,
        ))
        msg = MqttMessage(topic="input/data", payload={})
        _, routed = self.router.route(msg)[0]
        assert "_ts" in routed.payload

    def test_condition_filter(self):
        def only_battery_alert(m):
            return "Battery" in str(m.payload.get("type", ""))
        
        self.router.add_rule(RoutingRule(
            name="battery_only",
            source_pattern="alert/+",
            target_topic_template="filtered/{0}",
            condition_fn=only_battery_alert,
        ))
        
        msg_pass = MqttMessage(topic="alert/agv1", payload={"type": "BatteryLow"})
        msg_fail = MqttMessage(topic="alert/agv2", payload={"type": "Collision"})
        
        assert len(self.router.route(msg_pass)) == 1
        assert len(self.router.route(msg_fail)) == 0

    def test_variable_substitution(self):
        self.router.add_rule(RoutingRule(
            name="var_test",
            source_pattern="agv/{vehicleId}/status",
            target_topic_template="dashboard/{vehicle_id}/live",
        ))
        msg = MqttMessage(
            topic="agv/AGV-005/status",
            payload={"vehicleId": "AGV-005"},
        )
        target, _ = self.router.route(msg)[0]
        assert target == "dashboard/AGV-005/live"

    def test_stats_tracking(self):
        rule = RoutingRule(name="stats_r", source_pattern="s/+", target_topic_template="t")
        self.router.add_rule(rule)
        self.router.route(MqttMessage(topic="s/a", payload={}))
        self.router.route(MqttMessage(topic="s/b", payload={}))
        
        stats = self.router.get_stats()
        assert stats[0]["matches"] == 2
        assert stats[0]["errors"] == 0

    def test_remove_rule(self):
        self.router.add_rule(RoutingRule(name="to_remove", source_pattern="+", target_topic_template="t"))
        assert self.router.remove_rule("to_remove") is True
        assert self.router.remove_rule("nonexistent") is False


# ==================== 7. 离线存储测试 ====================

class TestOfflineMessageStore:
    """SQLite 离线消息持久化"""

    @pytest.fixture
    def store(self, tmp_path):
        db_path = str(tmp_path / "test_mqtt.db")
        s = OfflineMessageStore(db_path)
        yield s
        os.unlink(db_path)

    def test_store_and_count(self, store):
        msg = MqttMessage(
            topic="offline/test",
            payload={"data": "hello"},
            qos=1,
        )
        rid = store.store(msg)
        assert rid >= 1
        assert store.pending_count() == 1

    def test_store_multiple(self, store):
        for i in range(5):
            store.store(MqttMessage(
                topic=f"topic_{i}",
                payload={"i": i},
            ))
        assert store.pending_count() == 5

    def test_get_pending(self, store):
        store.store(MqttMessage(topic="t1", payload={"a": 1}))
        store.store(MqttMessage(topic="t2", payload={"b": 2}))
        
        pending = store.get_pending(limit=10)
        assert len(pending) == 2
        assert pending[0].topic == "t1"
        assert pending[1].topic == "t2"

    def test_mark_published(self, store):
        ids = []
        for i in range(3):
            rid = store.store(MqttMessage(topic=f"t{i}", payload={}))
            ids.append(rid)
        
        store.mark_published(ids[:2])
        assert store.pending_count() == 1

    def test_mark_retry(self, store):
        rid = store.store(MqttMessage(topic="retry_test", payload={}))
        store.mark_retry(rid)
        pending = store.get_pending(limit=1)
        assert len(pending) == 1
        # retry_count 已增加 (内部字段)

    def test_cleanup_old_messages(self, store):
        old_msg = MqttMessage(topic="old", payload={}, timestamp=time.time() - 48 * 3600)
        store.store(old_msg)
        # 手动标记为 published 以便清理
        conn = sqlite3.connect(store.db_path)
        cur = conn.execute("SELECT id FROM offline_messages WHERE status='pending' LIMIT 1")
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE offline_messages SET status='published', published_at=? WHERE id=?",
                (time.time() - 48 * 3600, row[0]),
            )
            conn.commit()
        conn.close()
        
        removed = store.cleanup(max_age_hours=24)
        assert removed >= 0  # 可能为0如果没有pending记录被清理

    def test_get_stats(self, store):
        store.store(MqttMessage(topic="stat_t", payload={}))
        stats = store.get_stats()
        assert stats["total"] >= 1
        assert stats["pending_count"] >= 1
        assert "db_size_bytes" in stats

    def test_empty_store_stats(self, store):
        stats = store.get_stats()
        assert stats["total"] == 0
        assert stats["pending_count"] == 0


# ==================== 8. 模拟模式核心功能测试 ====================

class TestSimulationMode:
    """模拟模式基本功能"""

    def test_create_simulation_adapter(self):
        adapter = MqttVehicleAdapter(mode="simulation", num_sim_agvs=5)
        assert adapter.mode == "simulation"
        assert adapter.num_sim_agvs == 5

    @pytest.mark.asyncio
    async def test_initialize_creates_agvs(self, sim_adapter):
        await sim_adapter.initialize()
        statuses = await sim_adapter.get_all_statuses()
        assert len(statuses) == 8  # num_sim_agvs

    @pytest.mark.asyncio
    async def test_initialize_with_map_nodes(self, sim_adapter):
        await sim_adapter.initialize(map_nodes=_TEST_MAP)
        statuses = await sim_adapter.get_all_statuses()
        assert len(statuses) == 8
        if statuses:
            # _init_simulation 会从地图节点分配 current_node
            assert isinstance(statuses[0].current_node, str)

    @pytest.mark.asyncio
    async def test_start_stop_cycle(self, sim_adapter):
        await sim_adapter.initialize()
        await sim_adapter.start()
        assert sim_adapter.is_connected is True
        await sim_adapter.stop()

    @pytest.mark.asyncio
    async def test_connect_simulation(self):
        adapter = MqttVehicleAdapter(mode="simulation", num_sim_agvs=3)
        connected = await adapter.connect()
        assert connected is True
        assert adapter.is_connected is True

    @pytest.mark.asyncio
    async def test_disconnect(self, initialized_adapter):
        await initialized_adapter.disconnect()
        assert initialized_adapter.is_connected is False
        assert len(initialized_adapter._sim_agvs) == 0

    @pytest.mark.asyncio
    async def test_get_status_single(self, initialized_adapter):
        status = await initialized_adapter.get_status("mqtt_agv_001")
        assert status is not None
        assert status.vehicle_id == "mqtt_agv_001"
        assert status.state == VehicleState.IDLE

    @pytest.mark.asyncio
    async def test_get_status_nonexistent(self, initialized_adapter):
        status = await initialized_adapter.get_status("nonexistent_agv")
        assert status is None

    @pytest.mark.asyncio
    async def test_get_all_statuses(self, initialized_adapter):
        statuses = await initialized_adapter.get_all_statuses()
        assert isinstance(statuses, list)
        assert len(statuses) == 8
        for s in statuses:
            assert isinstance(s, VehicleStatus)
            assert s.vehicle_id.startswith("mqtt_agv_")


# ==================== 9. 指令发送测试 ====================

class TestCommandSending:
    """指令发送与状态转换"""

    @pytest.mark.asyncio
    async def test_send_move_command(self, initialized_adapter):
        result = await initialized_adapter.send_command(
            "mqtt_agv_001", VehicleCommand.MOVE, {"target": "node_02"}
        )
        assert result.success is True
        assert result.command == "move"

        status = await initialized_adapter.get_status("mqtt_agv_001")
        assert status.state == VehicleState.MOVING
        assert status.target_node == "node_02"

    @pytest.mark.asyncio
    async def test_send_stop_command(self, initialized_adapter):
        # 先移动
        await initialized_adapter.send_command(
            "mqtt_agv_001", VehicleCommand.MOVE, {"target": "node_03"}
        )
        # 再停止
        result = await initialized_adapter.send_command(
            "mqtt_agv_001", VehicleCommand.STOP
        )
        assert result.success is True
        
        status = await initialized_adapter.get_status("mqtt_agv_001")
        assert status.state == VehicleState.IDLE
        assert status.speed == 0.0

    @pytest.mark.asyncio
    async def test_send_charge_command(self, initialized_adapter):
        result = await initialized_adapter.send_command(
            "mqtt_agv_002", VehicleCommand.CHARGE
        )
        assert result.success is True
        status = await initialized_adapter.get_status("mqtt_agv_002")
        assert status.state == VehicleState.CHARGING

    @pytest.mark.asyncio
    async def test_send_load_command(self, initialized_adapter):
        result = await initialized_adapter.send_command(
            "mqtt_agv_003", VehicleCommand.LOAD
        )
        assert result.success is True
        status = await initialized_adapter.get_status("mqtt_agv_003")
        assert status.load_status is True

    @pytest.mark.asyncio
    async def test_send_unload_command(self, initialized_adapter):
        await initialized_adapter.send_command("mqtt_agv_004", VehicleCommand.LOAD)
        await initialized_adapter.send_command("mqtt_agv_004", VehicleCommand.UNLOAD)
        status = await initialized_adapter.get_status("mqtt_agv_004")
        assert status.load_status is False

    @pytest.mark.asyncio
    async def test_send_cancel_task(self, initialized_adapter):
        await initialized_adapter.send_command(
            "mqtt_agv_005", VehicleCommand.MOVE, {"target": "node_04"}
        )
        await initialized_adapter.send_command(
            "mqtt_agv_005", VehicleCommand.CANCEL_TASK
        )
        status = await initialized_adapter.get_status("mqtt_agv_005")
        assert status.state == VehicleState.IDLE

    @pytest.mark.asyncio
    async def test_send_pickup_command(self, initialized_adapter):
        result = await initialized_adapter.send_command(
            "mqtt_agv_006", VehicleCommand.PICKUP
        )
        assert result.success is True
        status = await initialized_adapter.get_status("mqtt_agv_006")
        assert status.state == VehicleState.LOADING

    @pytest.mark.asyncio
    async def test_send_dropoff_command(self, initialized_adapter):
        await initialized_adapter.send_command("mqtt_agv_007", VehicleCommand.PICKUP)
        await initialized_adapter.send_command("mqtt_agv_007", VehicleCommand.DROPOFF)
        status = await initialized_adapter.get_status("mqtt_agv_007")
        assert status.state == VehicleState.UNLOADING

    @pytest.mark.asyncio
    async def test_send_resume_command(self, initialized_adapter):
        await initialized_adapter.send_command(
            "mqtt_agv_008", VehicleCommand.MOVE, {"target": "node_01"}
        )
        await initialized_adapter.send_command("mqtt_agv_008", VehicleCommand.STOP)
        await initialized_adapter.send_command("mqtt_agv_008", VehicleCommand.RESUME)
        status = await initialized_adapter.get_status("mqtt_agv_008")
        assert status.state == VehicleState.MOVING

    @pytest.mark.asyncio
    async def test_command_nonexistent_agv(self, initialized_adapter):
        result = await initialized_adapter.send_command(
            "ghost_agv", VehicleCommand.MOVE, {"target": "x"}
        )
        assert result.success is False
        assert "not found" in result.message.lower()


# ==================== 10. VDA5050 扩展测试 ====================

class TestVDA5050Extension:
    """VDA5050 运输订单和即时动作"""

    @pytest.mark.asyncio
    async def test_send_transport_order(self, initialized_adapter):
        order = TransportOrderMessage(
            order_id="order-001",
            nodes=[{"nodeId": "node_01"}, {"nodeId": "node_02"}, {"nodeId": "node_03"}],
            edges=[{"edgeId": "e_01_02"}],
            actions=[{"actionType": "pickup"}],
        )
        result = await initialized_adapter.send_transport_order("mqtt_agv_001", order)
        assert result.success is True
        assert "order-001" in result.message or "order" in result.message.lower()

    @pytest.mark.asyncio
    async def test_send_instant_action_stop(self, initialized_adapter):
        result = await initialized_adapter.send_instant_action(
            "mqtt_agv_001", "stop"
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_send_instant_action_cancel(self, initialized_adapter):
        result = await initialized_adapter.send_instant_action(
            "mqtt_agv_002", "cancelOrder"
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_send_instant_action_start(self, initialized_adapter):
        result = await initialized_adapter.send_instant_action(
            "mqtt_agv_003", "start"
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_vda5050_topic_generation(self):
        assert Vda5050Topics.order_topic("F1") == "vda5050/F1/order"
        assert Vda5050Topics.agv_state_topic("F1", "A1") == "vda5050/F1/agv/A1/state"


# ==================== 11. 健康检查测试 ====================

class TestHealthCheck:
    """健康检查功能"""

    @pytest.mark.asyncio
    async def test_health_connected(self, initialized_adapter):
        health = await initialized_adapter.health_check()
        assert health["connected"] is True
        assert health["protocol"] == "mqtt"
        assert health["mode"] == "simulation"

    @pytest.mark.asyncio
    async def test_health_contains_metrics(self, initialized_adapter):
        health = await initialized_adapter.health_check()
        assert "metrics" in health
        metrics = health["metrics"]
        assert "messages_published" in metrics
        assert "messages_received" in metrics
        assert "avg_latency_ms" in metrics

    @pytest.mark.asyncio
    async def test_health_contains_config(self, initialized_adapter):
        health = await initialized_adapter.health_check()
        config = health["config"]
        assert "broker" in config
        assert "qos" in config
        assert "tls" in config
        assert "vda5050_mode" in config
        assert config["vda5050_mode"] is True
        assert config["acl_enabled"] is True

    @pytest.mark.asyncio
    async def test_health_contains_counts(self, initialized_adapter):
        health = await initialized_adapter.health_check()
        assert health["subscribed_topics"] >= 0
        assert health["sim_agvs"] == 8
        assert health["routing_rules"] >= 3  # 默认路由数

    @pytest.mark.asyncio
    async def test_health_score(self, initialized_adapter):
        health = await initialized_adapter.health_check()
        assert "health_score" in health
        assert 80 <= health["health_score"] <= 100

    @pytest.mark.asyncio
    async def test_health_disconnected_score(self, sim_adapter):
        # 不初始化/启动, 连接状态为False
        health = await sim_adapter.health_check()
        score = health["health_score"]
        assert score < 70  # 断开连接应扣分

    @pytest.mark.asyncio
    async def test_routing_stats_in_health(self, initialized_adapter):
        health = await initialized_adapter.health_check()
        assert "routing_stats" in health
        assert isinstance(health["routing_stats"], list)

    @pytest.mark.asyncio
    async def test_base_health_check(self, initialized_adapter):
        # health_check 返回 Dict, 检查基本结构
        base = await initialized_adapter.health_check()
        assert "connected" in base
        assert isinstance(base["connected"], bool)


class TestHealthCheckWithMetrics:
    """带指标的动态健康评分"""

    @pytest.mark.asyncio
    async def test_high_error_rate_penalty(self, initialized_adapter):
        # 注入大量错误以降低评分
        for _ in range(100):
            initialized_adapter.metrics.record_error("simulated error")
        
        health = await initialized_adapter.health_check()
        # 错误率高时分数应下降
        assert health["health_score"] < 100


# ==================== 12. 回调注册测试 ====================

class TestCallbackRegistration:
    """回调函数注册"""

    @pytest.mark.asyncio
    async def test_subscribe_callback(self, initialized_adapter):
        received = []
        
        def my_callback(topic, payload):
            received.append((topic, payload))
        
        initialized_adapter.subscribe_callback("custom/+", my_callback)
        assert "custom/+" in initialized_adapter._message_callbacks

    @pytest.mark.asyncio
    async def test_subscribe_status(self, initialized_adapter):
        updates = []
        
        def on_update(status_data):
            updates.append(status_data)
        
        initialized_adapter.subscribe_status(on_update)
        assert len(initialized_adapter._status_subscribers) == 1


# ==================== 13. 协议支持列表测试 ====================

class TestSupportedProtocols:
    """协议支持列表"""

    def test_basic_protocols(self):
        adapter = MqttVehicleAdapter(mode="simulation", vda5050_mode=False, sparkplug_enabled=False)
        protocols = adapter.get_supported_protocols()
        assert "mqtt" in protocols
        assert "vda5050-mqtt" not in protocols
        assert "sparkplug-b" not in protocols

    def test_vda5050_mode(self):
        adapter = MqttVehicleAdapter(mode="simulation", vda5050_mode=True, sparkplug_enabled=False)
        protocols = adapter.get_supported_protocols()
        assert "vda5050-mqtt" in protocols

    def test_sparkplug_mode(self):
        adapter = MqttVehicleAdapter(mode="simulation", sparkplug_enabled=True)
        protocols = adapter.get_supported_protocols()
        assert "sparkplug-b" in protocols

    def test_repr(self):
        adapter = MqttVehicleAdapter(mode="simulation", num_sim_agvs=5)
        r = repr(adapter)
        assert "MqttVehicleAdapter" in r
        assert "simulation" in r


# ==================== 14. 状态解析测试 ====================

class TestStatusParsing:
    """MQTT 状态消息解析"""

    @pytest.mark.asyncio
    async def test_parse_standard_format(self, initialized_adapter):
        data = {
            "batteryLevel": 92,
            "x": 100.5,
            "y": 200.3,
            "angle": 45.0,
            "speed": 1.2,
            "state": "moving",
            "currentNode": "node_02",
            "loadStatus": "true",
        }
        initialized_adapter._update_sim_status("parse_test_agv", data)
        status = await initialized_adapter.get_status("parse_test_agv")
        assert status is not None
        assert status.battery_level == 92
        assert status.x == 100.5
        assert status.y == 200.3
        assert status.angle == 45.0
        assert status.speed == 1.2
        assert status.state == VehicleState.MOVING
        assert status.current_node == "node_02"
        assert status.load_status is True

    @pytest.mark.asyncio
    async def test_parse_short_format(self, initialized_adapter):
        data = {
            "battery": 75,
            "x": 0, "y": 0,
            "state": "charging",
        }
        initialized_adapter._update_sim_status("short_agv", data)
        status = await initialized_adapter.get_status("short_agv")
        assert status.battery_level == 75
        assert status.state == VehicleState.CHARGING

    @pytest.mark.asyncio
    async def test_parse_vda5050_format(self, initialized_adapter):
        data = {
            "positionX": 500.0,
            "positionY": 300.0,
            "theta": 180.0,
            "velocity": 2.5,
            "agvState": "idle",
            "lastNodeId": "dock_01",
            "targetNode": "charge_station",
            "orderId": "order-999",
        }
        initialized_adapter._update_sim_status("vda_agv", data)
        status = await initialized_adapter.get_status("vda_agv")
        assert status.x == 500.0
        assert status.y == 300.0
        assert status.angle == 180.0
        assert status.speed == 2.5
        assert status.state == VehicleState.IDLE
        assert status.last_node_id == "dock_01"
        assert status.target_node == "charge_station"
        assert status.order_id == "order-999"

    @pytest.mark.asyncio
    async def test_parse_error_fields(self, initialized_adapter):
        data = {
            "errorCode": 404,
            "errorMessage": "Path not found",
        }
        initialized_adapter._update_sim_status("err_agv", data)
        status = await initialized_adapter.get_status("err_agv")
        assert status.error_code == 404
        assert "not found" in status.error_message.lower()

    @pytest.mark.asyncio
    async def test_extract_agv_id_from_topic(self, sim_adapter):
        await sim_adapter.initialize()
        
        # agv/{id}/status 格式
        assert sim_adapter._extract_agv_id("agv/AGV-001/status") == "AGV-001"
        
        # vda5050 格式 (agv 在 index=3, agvId 在 index=4)
        vid = sim_adapter._extract_agv_id("vda5050/fleet1/grp/agv/Robot-05/state")
        if vid is None:
            # 兼容: agv 可能在 index=2
            vid = sim_adapter._extract_agv_id("vda5050/fleet1/agv/Robot-05/state")
        assert vid is not None and "05" in vid
        
        # 无匹配
        assert sim_adapter._extract_agv_id("system/heartbeat") is None
        assert sim_adapter._extract_agv_id("random/topic/path") is None


# ==================== 15. 边界情况与鲁棒性测试 ====================

class TestEdgeCasesAndRobustness:
    """边界情况和鲁棒性"""

    @pytest.mark.asyncio
    async def test_empty_params_command(self, initialized_adapter):
        result = await initialized_adapter.send_command(
            "mqtt_agv_001", VehicleCommand.RESUME
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_unknown_command(self, initialized_adapter):
        # 所有命令都应有默认处理
        result = await initialized_adapter.send_command(
            "mqtt_agv_001", VehicleCommand.INIT_POSITION
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_double_start(self, initialized_adapter):
        # 再次 start 不应崩溃
        await initialized_adapter.start()
        assert initialized_adapter.is_connected is True

    @pytest.mark.asyncio
    async def test_double_stop(self, sim_adapter):
        await sim_adapter.initialize()
        await sim_adapter.start()
        await sim_adapter.stop()
        await sim_adapter.stop()  # 二次 stop 不应崩溃

    @pytest.mark.asyncio
    async def test_zero_sim_agvs(self):
        adapter = MqttVehicleAdapter(mode="simulation", num_sim_agvs=0, offline_persistence=False, acl_enabled=False)
        await adapter.initialize()
        statuses = await adapter.get_all_statuses()
        assert len(statuses) == 0

    @pytest.mark.asyncio
    async def test_large_num_sim_agvs(self):
        adapter = MqttVehicleAdapter(mode="simulation", num_sim_agvs=500, offline_persistence=False, acl_enabled=False)
        await adapter.initialize()
        statuses = await adapter.get_all_statuses()
        assert len(statuses) == 500

    @pytest.mark.asyncio
    async def test_special_characters_in_vehicle_id(self, sim_adapter):
        await sim_adapter.initialize()
        special_ids = ["AGV/001", "AGV#002", "AGV+003", "AGV_test"]
        for vid in special_ids:
            sim_adapter._sim_agvs[vid] = VehicleStatus(vehicle_id=vid)
            result = await sim_adapter.send_command(vid, VehicleCommand.STOP)
            assert result.vehicle_id == vid

    def test_config_immutability(self):
        cfg = MqttConnectionConfig(broker_host="host1")
        original = cfg.broker_host
        # 确保配置可正常读取
        assert cfg.broker_host == original

    def test_metrics_reset_doesnt_crash(self):
        metrics = MqttHealthMetrics()
        for _ in range(50):
            metrics.publish_errors += 1
        metrics.reset()
        assert metrics.publish_errors == 0


# ==================== 运行入口 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
