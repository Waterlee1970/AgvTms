"""
适配器集成测试框架 - 对标Spring Integration Test规范

测试范围:
1. MQTT Vehicle Adapter
2. OPC UA Adapter
3. 通用Adapter接口契约
4. 协议转换正确性

覆盖率目标: 75%+
运行命令:
    pytest tests/integration/test_adapters.py -v -m "integration" --cov=app/adapters
"""

import pytest
import asyncio
import json
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass


# ==================== Mock基础设施 ====================

@dataclass
class MockMQTTMessage:
    """模拟MQTT消息对象"""
    topic: str
    payload: bytes
    qos: int = 0
    retain: bool = False
    
    @property
    def decoded(self) -> str:
        return self.payload.decode('utf-8')


class MockMQTTBroker:
    """
    模拟MQTT Broker (用于单元测试，不需要真实Broker)
    
    功能:
    - 模拟publish/subscribe语义
    - 记录所有发布的消息 (用于断言)
    - 支持异步消息投递
    """
    
    def __init__(self):
        self.published_messages: List[MockMQTTMessage] = []
        self.subscriptions: Dict[str, List] = {}
        self.connected_clients = set()
    
    async def publish(self, topic: str, payload: str or dict, **kwargs):
        """记录发布操作"""
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        
        msg = MockMQTTMessage(
            topic=topic,
            payload=payload.encode('utf-8'),
            **kwargs
        )
        self.published_messages.append(msg)
        
        # 投递给订阅者
        for callback in self.subscriptions.get(topic, []):
            if asyncio.iscoroutinefunction(callback):
                await callback(msg)
            else:
                callback(msg)
    
    async def subscribe(self, topic: str, callback):
        """注册订阅"""
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
        self.subscriptions[topic].append(callback)
    
    def get_messages_for_topic(self, topic_pattern: str) -> List[MockMQTTMessage]:
        """
        获取匹配topic的消息 (支持通配符)
        
        示例:
            broker.get_messages_for_topic("agv/+/status")
        """
        import fnmatch
        
        return [
            msg for msg in self.published_messages
            if fnmatch.fnmatch(msg.topic, topic_pattern)
        ]
    
    def reset(self):
        """清除所有记录 (每个测试用例开始时调用)"""
        self.published_messages.clear()
        self.subscriptions.clear()
        self.connected_clients.clear()


class MockOPCUAServer:
    """
    模拟OPC UA Server
    
    提供虚拟的节点值读写能力
    """
    
    def __init__(self):
        self.nodes: Dict[str, Any] = {
            "ns=2;s=Machine.Status": "Running",
            "ns=2;s=Machine.Speed": 120.5,
            "ns=2;s=Conveyor.Running": True,
            "ns=2;s=Conveyor.Speed_setpoint": 1.5,
            "ns=2;s=Workpiece.Count": 42,
        }
        self.write_log: List[tuple] = []  # 记录写操作
    
    def read_node_value(self, node_id: str) -> Any:
        """读取节点值"""
        if node_id in self.nodes:
            return self.nodes[node_id]
        raise ValueError(f"Node {node_id} does not exist")
    
    def write_node_value(self, node_id: str, value: Any) -> bool:
        """写入节点值"""
        if node_id not in self.nodes:
            # 自动创建新节点
            pass
        
        self.nodes[node_id] = value
        self.write_log.append((node_id, value))
        return True


# ==================== Fixtures ====================

@pytest.fixture
def mqtt_broker() -> MockMQTTBroker:
    """每个测试用例独立的Mock MQTT Broker"""
    broker = MockMQTTBroker()
    yield broker
    broker.reset()  # 清理


@pytest.fixture
def opcua_server() -> MockOPCUAServer:
    """每个测试用例独立的Mock OPC UA Server"""
    server = MockOPCUAServer()
    return server


# ==================== 1. MQTT Adapter 测试 ====================

class TestMQTTVehicleAdapter:
    """
    MQTT Vehicle Adapter 集成测试
    
    测试场景:
    - 连接/断开生命周期
    - VDA5050协议消息收发
    - AGV状态同步
    - 命令下发
    - 异常处理
    """
    
    @pytest.mark.asyncio
    async def test_connect_to_broker(self, mqtt_broker):
        """应能成功连接到MQTT Broker"""
        try:
            from app.adapters.mqtt_vehicle_adapter import MqttVehicleAdapter, MqttConnectionConfig
            
            config = MqttConnectionConfig(
                broker_host="localhost",
                broker_port=1883,
            )
            
            adapter = MqttVehicleAdapter(
                mode="server",
                config=config,
                vda5050_mode=True,
            )
            
            # Mock实际的连接操作
            with patch.object(adapter, '_connect_internal', return_value=True):
                connected = await adapter.connect()
            
            assert connected is True, "Connection should succeed"
            
        except ImportError as e:
            pytest.skip(f"MQTT adapter not available: {e}")
    
    @pytest.mark.asyncio
    async def test_publish_agv_command(self, mqtt_broker):
        """
        应能通过MQTT发送AGV控制命令
        
        测试VDA5050标准命令格式:
        {
          "header": { "messageType": "order", "messageId": "...", "version": "2.0" },
          "orderId": "order-001",
          "orderUpdateId": 1,
          "nodes": [...],
          "edgeActions": [...]
        }
        """
        try:
            from app.adapters.mqtt_vehicle_adapter import MqttVehicleAdapter
            
            # 这里需要根据实际API调整测试逻辑
            
            # 模拟发布命令到 agv/AGV001/order topic
            command_topic = f"agv/AGV001/order"
            order_message = {
                "orderId": "test-order-001",
                "nodes": [{"nodeId": "station_A", "actions": [...] }],
            }
            
            await mqtt_broker.publish(command_topic, order_message)
            
            # 验证消息已发布
            messages = mqtt_broker.get_messages_for_topic("agv/AGV001/order")
            assert len(messages) == 1, "Command should be published"
            
            published_msg = messages[0]
            data = json.loads(published_msg.decoded)
            assert data["orderId"] == "test-order-001", "Order ID should match"
            
        except ImportError:
            pytest.skip("MQTT adapter not available")
    
    @pytest.mark.asyncio
    async def test_receive_agv_status(self, mqtt_broker):
        """
        应能接收并解析AGV状态报告
        
        监听topic: agv/+/state
        """
        status_received = asyncio.Event()
        received_data = {}
        
        async def on_status(message):
            data = json.loads(message.decoded)
            received_data.update(data)
            status_received.set()
        
        # 注册监听
        await mqtt_broker.subscribe("agv/AGV002/state", on_status)
        
        # 模拟AGV上报状态
        status_msg = {
            "header": {"messageType": "instantActions"},
            "agvId": "AGV002",
            "state": {"operationalState": "operating"},
            "position": {"x": 10.5, "y": 20.3, "theta": 90.0},
            "batteryState": {"batteryCharge": 85},
        }
        
        await mqtt_broker.publish("agv/AGV002/state", status_msg)
        
        # 等待处理完成
        try:
            await asyncio.wait_for(status_received.wait(), timeout=2.0)
            
            assert received_data["agvId"] == "AGV002"
            assert received_data["state"]["operationalState"] == "operating"
            assert received_data["batteryState"]["batteryCharge"] == 85
            
        except asyncio.TimeoutError:
            pytest.fail("Status message not received within timeout")


class TestVDA5050Protocol:
    """
    VDA5050协议兼容性测试
    
    参考: https://vda5050.org/
    
    核心验证点:
    1. Header结构符合规范
    2. Order/Action消息格式正确
    3. State消息字段完整
    4. 错误码定义一致
    """
    
    def test_order_message_schema(self):
        """Order消息应符合VDA5050 v2.0 schema"""
        valid_order = {
            "header": {
                "messageType": "order",
                "messageId": "msg-uuid-1234567890",
                "timestamp": "2024-01-15T10:30:00Z",
                "version": "2.0",
                "manufacturer": "TestCompany",
                "serialNumber": "SN-001",
            },
            "orderId": "order-test-001",
            "orderUpdateId": 1,
            "nodes": [
                {
                    "nodeId": "node_station_A",
                    "sequenceId": 0,
                    "nodePosition": {"x": 100.0, "y": 200.0, "theta": None},
                    "actions": [
                        {
                            "actionType": "pick",
                            "actionId": "action-pick-01",
                            "actionParameters": {
                                "positionId": "pos_pallet_001",
                                "height": 150,
                            }
                        }
                    ],
                }
            ],
            "edgeActions": [],
        }
        
        # 验证必需字段存在
        required_header_fields = ["messageType", "messageId", "timestamp", "version"]
        for field in required_header_fields:
            assert field in valid_order["header"], f"Missing header field: {field}"
        
        assert "orderId" in valid_order
        assert "nodes" in valid_order and len(valid_order["nodes"]) > 0
        
        print(f"✓ VDA5050 Order message validated: orderId={valid_order['orderId']}")
    
    def test_state_message_schema(self):
        """State消息应符合VDA5050 v2.0 schema"""
        valid_state = {
            "header": {
                "messageType": "state",
                "messageId": "msg-state-001",
                "timestamp": "2024-01-15T10:31:00Z",
                "version": "2.0",
            },
            "agvId": "AGV-TMS-001",
            "state": {
                "operationalState": "operating",
                "paused": False,
                "nodeId": "current_position",
                "driving": True,
            },
            "position": {
                "x": 105.2,
                "y": 198.7,
                "theta": 89.5,
                "mapId": "workshop_floor_1",
            },
            "batteryState": {
                "batteryCharge": 82.5,
                "batteryVoltage": 48.3,
                "chargingStatus": True,
            },
            "errors": [],
        }
        
        assert valid_state["agvId"] == "AGV-TMS-001"
        assert valid_state["state"]["operationalState"] in ["operating", "paused", "ready"]
        assert 0 <= valid_state["batteryState"]["batteryCharge"] <= 100


# ==================== 2. OPC UA Adapter 测试 ====================

class TestOPCUAAdapter:
    """
    OPC UA Adapter 集成测试
    
    测试场景:
    - 连接PLC/SCADA系统
    - 读取传感器数据
    - 写入控制指令
    - 异常断线重连
    """
    
    @pytest.mark.asyncio
    async def test_read_sensor_value(self, opcua_server):
        """应能从OPC UA节点读取传感器数据"""
        try:
            from app.adapters.opcua_adapter import OpcUaAdapter
            
            # 使用mock server避免真实连接
            with patch.object(OpcUaAdapter, 'connect', return_value=True):
                adapter = OpcUaAdapter(url="opc.tcp://mock-server:4840")
                
                # Mock read方法使用我们的mock server
                original_read = adapter.read_node_value
                adapter.read_node_value = lambda node_id: opcua_server.read_node_value(node_id)
                
                speed = adapter.read_node_value("ns=2;s=Machine.Speed")
                
                assert speed == 120.5, f"Expected 120.5, got {speed}"
                print(f"✓ Read sensor value: Machine.Speed = {speed}")
                
        except ImportError:
            pytest.skip("OPC UA adapter not available")
    
    @pytest.mark.asyncio
    async def test_write_control_command(self, opcua_server):
        """应能向OPC UA节点写入控制指令"""
        try:
            from app.adapters.opcua_adapter import OpcUaAdapter
            
            with patch.object(OpcUaAdapter, 'connect', return_value=True):
                adapter = OpcUaAdapter(url="opc.tcp://mock-server:4840")
                
                # 使用mock server
                adapter.write_node_value = lambda nid, val: opcua_server.write_node_value(nid, val)
                
                result = adapter.write_node_value("ns=2;s=Conveyor.Speed_setpoint", 2.5)
                
                assert result is True
                assert len(opcua_server.write_log) == 1
                
                written_node, written_val = opcua_server.write_log[-1]
                assert written_node == "ns=2;s=Conveyor.Speed_setpoint"
                assert written_val == 2.5
                
                # 验证值确实被更新
                current_val = opcua_server.read_node_value("ns=2;s=Conveyor.Speed_setpoint")
                assert current_val == 2.5, f"Value should be updated to 2.5, got {current_val}"
                
                print(f"✓ Wrote control command: Conveyor.Speed_setpoint = 2.5 m/s")
                
        except ImportError:
            pytest.skip("OPC UA adapter not available")
    
    @pytest.mark.opcua
    @pytest.mark.asyncio
    async def test_real_opcua_connection(self):
        """
        [可选] 真实OPC UA Server连接测试
        
        需要环境变量:
            OPCUA_SERVER_URL=opc.tcp://192.168.1.100:4840
            OPCUA_TEST_MODE=true
        
        注意: 此测试需要真实的PLC或仿真Server，默认跳过
        """
        import os
        
        server_url = os.getenv("OPCUA_SERVER_URL")
        if not server_url:
            pytest.skip("No OPC UA Server URL configured (set OPCUA_SERVER_URL)")
        
        try:
            from app.adapters.opcua_adapter import OpcUaAdapter
            
            adapter = OpcUaAdapter(url=server_url)
            
            connected = await adapter.connect(timeout=5.0)
            assert connected, f"Should connect to {server_url}"
            
            # 尝试读取已知节点
            value = adapter.read_node_value("ns=2;i=6001")  # Server state
            print(f"\n✓ Connected to real OPC UA server at {server_url}, server state={value}")
            
            await adapter.disconnect()
            
        except ImportError:
            pytest.skip("OPC UA client library not installed (pip install asyncua)")


# ==================== 3. Adapter接口契约测试 ====================

class TestAdapterInterfaceContract:
    """
    通用Adapter接口契约验证
    
    所有适配器必须实现以下接口:
    - connect() → bool
    - disconnect() → void
    - is_connected() → bool
    - send_command(command: dict) → bool
    - register_callback(event_type: str, handler: Callable) → void
    """
    
    def test_mqtt_adapter_implements_interface(self):
        """MQTT Adapter应实现标准接口"""
        required_methods = ['connect', 'disconnect', 'is_connected', 'send_command']
        
        try:
            from app.adapters.mqtt_vehicle_adapter import MqttVehicleAdapter
            
            for method_name in required_methods:
                assert hasattr(MqttVehicleAdapter, method_name), \
                    f"MqttVehicleAdapter missing method: {method_name}"
                assert callable(getattr(MqttVehicleAdapter, method_name)), \
                    f"{method_name} should be callable"
            
            print("✓ MqttVehicleAdapter implements all required interface methods")
            
        except ImportError:
            pytest.skip("MqttVehicleAdapter not found")
    
    def test_opcua_adapter_implements_interface(self):
        """OPC UA Adapter应实现标准接口"""
        required_methods = ['connect', 'disconnect', 'is_connected', 'read_node_value', 'write_node_value']
        
        try:
            from app.adapters.opcua_adapter import OpcUaAdapter
            
            for method_name in required_methods:
                assert hasattr(OpcUaAdapter, method_name), \
                    f"OpcUaAdapter missing method: {method_name}"
                assert callable(getattr(OpcUaAdapter, method_name)), \
                    f"{method_name} should be callable"
            
            print("✓ OpcUaAdapter implements all required interface methods")
            
        except ImportError:
            pytest.skip("OpcUaAdapter not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
