"""
TCP Socket 二进制协议适配器 — Phase 3 协议深化 (新增协议 #8).

支持场景:
  - 国产 AGV 厂商自定义二进制协议
  - 嵌入式设备串口转网络
  - 低延迟实时控制

特性:
  - TCP 长连接管理
  - 二进制帧编解码 (TLV 格式)
  - 心跳保活
  - 消息队列缓冲
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base_adapter import (
    BaseVehicleAdapter,
    CommandResult,
    VehicleCommand,
    VehicleState,
    VehicleStatus,
)

logger = logging.getLogger(__name__)


# ==================== 协议定义 ====================

class TcpFrameType(IntEnum):
    """TCP 帧类型定义 (参考工业标准)"""
    HEARTBEAT = 0x01           # 心跳
    STATUS_REPORT = 0x02       # 状态上报
    COMMAND = 0x10             # 控制指令
    COMMAND_RESPONSE = 0x11    # 指令响应
    DATA = 0x20                # 数据传输
    ERROR = 0xFF               # 错误


class TcpErrorCode(IntEnum):
    """错误码"""
    SUCCESS = 0x00
    INVALID_FRAME = 0x01
    CHECKSUM_ERROR = 0x02
    UNKNOWN_COMMAND = 0x03
    DEVICE_BUSY = 0x04
    TIMEOUT = 0x05


@dataclass
class TcpFrame:
    """TCP 二进制帧结构"""
    frame_type: TcpFrameType
    payload: bytes = b""
    sequence_id: int = 0
    device_id: str = ""
    
    # TLV 格式: [Header(6)] + [Payload(N)] + [Tail(2)]
    # Header: [Magic(2)=0xABCD] + [Length(2)] + [Type(1)] + [Seq(1)]
    # Tail:   [CRC16(2)]
    
    MAGIC = b'\xAB\xCD'
    
    def encode(self) -> bytes:
        """序列化为二进制帧"""
        payload_len = len(self.payload)
        length = payload_len + 4  # + type + seq + crc
        
        header = struct.pack(
            '>HBBH',                    # big-endian
            0xABCD,                     # magic
            length,                      # total length after magic
            self.frame_type,             # type
            self.sequence_id & 0xFF,     # seq
        )
        
        raw = header + self.payload
        
        # CRC16 校验 (简化实现)
        crc = self._crc16(raw[2:])  # 不含 magic 的 CRC
        tail = struct.pack('>H', crc)
        
        return raw + tail
    
    @classmethod
    def decode(cls, data: bytes) -> Optional['TcpFrame']:
        """从二进制数据解析帧"""
        if len(data) < 8:
            return None
            
        try:
            magic, length, ftype, seq = struct.unpack('>HBBH', data[:6])
            
            if magic != 0xABCD:
                return None
                
            if len(data) < 6 + length:
                return None
                
            payload = data[6:6+length-2]  # 减去 CRC
            received_crc = struct.unpack('>H', data[6+length-2:6+length])[0]
            
            # 校验 CRC
            expected_crc = cls._crc16(data[2:6+length-2])
            if received_crc != expected_crc:
                logger.warning("CRC mismatch: expected=%04X got=%04X", expected_crc, received_crc)
                
            return cls(
                frame_type=TcpFrameType(ftype),
                payload=payload,
                sequence_id=seq,
            )
        except Exception as e:
            logger.debug("Frame decode error: %s", e)
            return None
    
    @staticmethod
    def _crc16(data: bytes) -> int:
        """CRC16-CCITT 计算"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return crc


# ==================== 配置模型 ====================

@dataclass
class TcpAdapterConfig:
    """TCP 适配器配置"""
    host: str = "192.168.1.100"
    port: int = 5000
    reconnect_interval: float = 5.0     # 重连间隔
    heartbeat_interval: float = 30.0    # 心跳间隔
    receive_buffer_size: int = 8192     # 接收缓冲区
    max_frame_size: int = 4096          # 最大帧长度


@dataclass
class TcpHealthMetrics:
    """TCP 健康指标"""
    frames_sent: int = 0
    frames_received: int = 0
    frames_error: int = 0
    reconnections: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    last_heartbeat_time: float = 0.0
    connection_uptime: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "frames_error": self.frames_error,
            "error_rate": round(self.frames_error / max(1, self.frames_sent + self.frames_received), 4),
            "reconnections": self.reconnections,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "connection_uptime_s": round(self.connection_uptime, 1),
        }


# ==================== 主适配器类 ====================

class TcpVehicleAdapter(BaseVehicleAdapter):
    """
    TCP Socket 车辆适配器.
    
    支持模式:
      - live: TCP 长连接到真实设备
      - simulation: 内存模拟
    """
    
    def __init__(
        self,
        mode: str = "simulation",
        config: Optional[TcpAdapterConfig] = None,
        num_sim_agvs: int = 5,
        **kwargs,
    ):
        super().__init__(name="tcp", protocol="tcp")
        self.mode = mode
        self.config = config or TcpAdapterConfig()
        self.num_sim_agvs = num_sim_agvs
        
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._sim_agvs: Dict[str, VehicleStatus] = {}
        self.metrics = TcpHealthMetrics()
        self._sequence_id = 0
        self._pending_responses: Dict[int, asyncio.Future] = {}  # seq → Future
        
        # 后台任务
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._connect_time: float = 0.0

    async def connect(self) -> bool:
        """建立 TCP 连接或初始化模拟"""
        if self.mode == "live":
            return await self._connect_live()
        else:
            self._init_simulation()
            return True

    async def _connect_live(self) -> bool:
        """建立 TCP 连接"""
        cfg = self.config
        
        try:
            self._reader, self._writer = await asyncio.open_connection(
                cfg.host, cfg.port,
            )
            
            self._connected = True
            self._connect_time = time.time()
            self.metrics.reconnections += 1
            
            # 启动接收和心跳任务
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            logger.info("TCP connected to %s:%d", cfg.host, cfg.port)
            return True
            
        except Exception as e:
            logger.error("TCP connect failed: %s, falling back to simulation", e)
            self.mode = "simulation"
            self._init_simulation()
            return True

    def _init_simulation(self):
        """初始化模拟"""
        self._connected = True
        for i in range(self.num_sim_agvs):
            vid = f"tcp_agv_{i+1:03d}"
            self._sim_agvs[vid] = VehicleStatus(
                vehicle_id=vid,
                state=VehicleState.IDLE,
                battery_level=88.0 + (i % 12),
                x=float(i * 18),
                y=float(i * 10),
            )
        logger.info("TCP simulation mode: %d AGVs", len(self._sim_agvs))

    async def disconnect(self) -> None:
        """断开连接"""
        # 停止后台任务
        for task in [self._receive_task, self._heartbeat_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                    
        # 关闭连接
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        
        self._connected = False
        self._sim_agvs.clear()
        logger.info("TCP adapter disconnected")

    async def _receive_loop(self):
        """接收消息循环"""
        buffer = bytearray()
        
        while self._connected and self._reader:
            try:
                data = await self._reader.read(self.config.receive_buffer_size)
                if not data:
                    break
                    
                buffer.extend(data)
                self.metrics.bytes_received += len(data)
                
                # 尝试解析完整帧
                while len(buffer) >= 8:
                    # 查找 magic
                    magic_pos = buffer.find(TcpFrame.MAGIC)
                    if magic_pos < 0 or magic_pos > 0:
                        if magic_pos < 0:
                            buffer.clear()
                        else:
                            del buffer[:magic_pos]
                        continue
                        
                    # 检查是否有足够数据读取 length 字段
                    if len(buffer) < 6:
                        break
                        
                    _, length, _, _ = struct.unpack('>HBBH', buffer[:6])
                    
                    # 等待完整帧
                    if len(buffer) < 6 + length:
                        break
                        
                    # 解析帧
                    frame_data = bytes(buffer[:6+length])
                    frame = TcpFrame.decode(frame_data)
                    
                    if frame:
                        del buffer[:6+length]
                        await self._handle_frame(frame)
                        self.metrics.frames_received += 1
                    else:
                        self.metrics.frames_error += 1
                        del buffer[:1]  # 移除一个字节重试
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("TCP receive error: %s", e)
                
        # 连接断开处理
        if self._connected:
            logger.warning("TCP connection lost, attempting reconnect...")
            self._connected = False

    async def _handle_frame(self, frame: TcpFrame):
        """处理接收到的帧"""
        if frame.frame_type == TcpFrameType.STATUS_REPORT:
            try:
                status_data = json.loads(frame.payload.decode('utf-8'))
                agv_id = status_data.get("device_id", "")
                if agv_id:
                    self._update_status_from_report(agv_id, status_data)
            except Exception as e:
                logger.debug("Status report parse error: %s", e)
                
        elif frame.frame_type == TcpFrameType.COMMAND_RESPONSE:
            future = self._pending_responses.pop(frame.sequence_id, None)
            if future and not future.done():
                future.set_result(frame)

    def _update_status_from_report(self, agv_id: str, data: Dict):
        """从状态报告更新 AGV 状态"""
        status = self._sim_agvs.get(agv_id)
        if not status:
            status = VehicleStatus(vehicle_id=agv_id)
            self._sim_agvs[agv_id] = status
            
        for key, value in data.items():
            if key == "state" and isinstance(value, str):
                try:
                    status.state = VehicleState(value)
                except ValueError:
                    pass
            elif hasattr(status, key):
                setattr(status, key, value)
        status.last_heartbeat = time.time()

    async def _heartbeat_loop(self):
        """心跳循环"""
        while self._connected:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                heartbeat = TcpFrame(
                    frame_type=TcpFrameType.HEARTBEAT,
                    sequence_id=self._next_seq(),
                )
                await self._send_frame(heartbeat)
                self.metrics.last_heartbeat_time = time.time()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)

    async def _send_frame(self, frame: TcpFrame) -> bool:
        """发送 TCP 帧"""
        if not self._writer or not self._connected:
            return False
            
        try:
            data = frame.encode()
            self._writer.write(data)
            await self._writer.drain()
            
            self.metrics.frames_sent += 1
            self.metrics.bytes_sent += len(data)
            return True
            
        except Exception as e:
            logger.error("TCP send error: %s", e)
            return False

    def _next_seq(self) -> int:
        """获取下一个序列号"""
        self._sequence_id = (self._sequence_id + 1) & 0xFF
        return self._sequence_id

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """通过 TCP 发送指令"""
        params = params or {}
        ts = time.time()

        if self.mode == "live" and self._connected:
            return await self._send_tcp_command(vehicle_id, command, params, ts)
            
        return self._simulate_command(vehicle_id, command, params, ts)

    async def _send_tcp_command(
        self, vehicle_id: str, command: VehicleCommand, params: Dict, ts: float
    ) -> CommandResult:
        """构建并发送 TCP 指令帧"""
        cmd_codes = {
            VehicleCommand.MOVE: 0x01, VehicleCommand.STOP: 0x02,
            VehicleCommand.RESUME: 0x03, VehicleCommand.CHARGE: 0x04,
            VehicleCommand.LOAD: 0x05, VehicleCommand.UNLOAD: 0x06,
            VehicleCommand.CANCEL_TASK: 0x09,
        }
        
        code = cmd_codes.get(command, 0x00)
        
        # 构建命令载荷 JSON
        payload = json.dumps({
            "device_id": vehicle_id,
            "command_code": code,
            "command_name": command.value,
            "parameters": params,
        }).encode('utf-8')
        
        frame = TcpFrame(
            frame_type=TcpFrameType.COMMAND,
            payload=payload,
            device_id=vehicle_id,
            sequence_id=self._next_seq(),
        )
        
        sent = await self._send_frame(frame)
        
        return CommandResult(
            success=sent,
            vehicle_id=vehicle_id,
            command=command.value,
            message=f"TCP command sent (seq={frame.sequence_id})" if sent else "Send failed",
            timestamp=ts,
            data={"seq": frame.sequence_id},
        )

    def _simulate_command(
        self, vehicle_id: str, command: VehicleCommand, params: Dict, ts: float
    ) -> CommandResult:
        """模拟指令执行"""
        status = self._sim_agvs.get(vehicle_id)
        if not status:
            return CommandResult(success=False, vehicle_id=vehicle_id, message="AGV not found")

        transitions = {
            VehicleCommand.MOVE: (VehicleState.MOVING, f"Moving"),
            VehicleCommand.STOP: (VehicleState.IDLE, "Stopped"),
            VehicleCommand.CHARGE: (VehicleState.CHARGING, "Charging"),
        }
        new_state, msg = transitions.get(command, (None, command.value))
        if new_state:
            status.state = new_state
        if command == VehicleCommand.LOAD:
            status.load_status = True
        if command == VehicleCommand.UNLOAD:
            status.load_status = False
            
        return CommandResult(success=True, vehicle_id=vehicle_id, command=command.value, message=msg, timestamp=ts)

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        return self._sim_agvs.get(vehicle_id)

    async def get_all_statuses(self) -> List[VehicleStatus]:
        return list(self._sim_agvs.values())

    async def health_check(self) -> Dict[str, Any]:
        """详细健康检查"""
        uptime = time.time() - self._connect_time if self._connect_time else 0
        self.metrics.connection_uptime = uptime
        
        return {
            "connected": self._connected,
            "protocol": "tcp",
            "mode": self.mode,
            "config": {"host": self.config.host, "port": self.config.port},
            "metrics": self.metrics.to_dict(),
            "sim_agvs": len(self._sim_agvs),
            "connection_uptime_s": round(uptime, 1),
        }

    def get_supported_protocols(self) -> List[str]:
        return ["tcp", "socket"]
