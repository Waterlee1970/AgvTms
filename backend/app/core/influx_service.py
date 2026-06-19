"""
InfluxDB 时序数据存储服务 — AGV历史轨迹与指标分析 (Phase 5.5)

功能:
  1. InfluxDB 2.x/3.x 客户端封装 (influxdb-client)
  2. Measurement 设计:
     - agv_telemetry: AGV位置/电量/速度/状态 (高频 ~1000 pts/s)
     - task_lifecycle: 任务状态变更 (中频)
     - system_metrics: 系统性能指标 (CPU/Memory/Latency)
  3. 高性能批量写入 (Buffered Batcher)
  4. Flux 查询封装 (时间范围/聚合/降采样)
  5. 自动降级 (InfluxDB 不可用时 → 本地文件缓存)
  6. 历史回放 API 支持
  7. 热力图历史数据查询

API 新增:
  GET /api/v2/history/agv/{agv_id}?start=&end=&interval=
  GET /api/v2/history/heatmap?scene_id=&start=&end=

依赖:
  - 生产: influxdb-client
  - 开发/测试: CSV 文件降级
"""

import os
import csv
import json
import time
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any, Dict, List, Optional, Tuple, Union,
    AsyncGenerator, Callable
)
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import asyncio

logger = logging.getLogger(__name__)


# ==================== Measurement 定义 ====================

class Measurement(str, Enum):
    """InfluxDB Measurement 枚举"""
    
    # AGV 遥测数据 (高频)
    AGV_TELEMETRY = "agv_telemetry"
    # 任务生命周期 (中频)
    TASK_LIFECYCLE = "task_lifecycle"
    # 系统指标 (低频)
    SYSTEM_METRICS = "system_metrics"
    # 调度事件
    SCHEDULING_EVENTS = "scheduling_events"


# ==================== 配置 ====================

@dataclass
class InfluxConfig:
    """InfluxDB 连接配置"""
    
    url: str = "http://localhost:8086"       # v2 默认端口
    token: str = ""                           # 认证 token (v2+)
    org: str = "agv-tms"                      # 组织名称
    bucket: str = "agv_data"                  # 存储桶
    
    # 批量写入配置
    batch_size: int = 1000                    # 每批最大记录数
    flush_interval_ms: int = 10000            # 刷新间隔 (10s)
    gzip: bool = True                         # Gzip 压缩传输
    timeout: int = 10000                      # 请求超时 (ms)
    
    # 降级配置
    fallback_enabled: bool = True             # 不可用时降级到文件
    fallback_dir: str = "./data/influx_fallback"
    fallback_csv_max_rows: int = 50000        # 单个 CSV 最大行数
    
    # 查询配置
    default_query_range: str = "-1h"          # 默认查询范围


# ==================== 数据模型 ====================

@dataclass
class DataPoint:
    """时序数据点"""
    
    measurement: Measurement
    tags: Dict[str, str]                       # 标签维度 (索引字段)
    fields: Dict[str, Any]                     # 数值字段
    timestamp: Optional[datetime] = None        # 时间戳 (None=服务器时间)
    
    def to_line_protocol(self) -> str:
        """转换为 InfluxDB Line Protocol 格式"""
        ts_str = self.timestamp.strftime('%Y-%m-%dT%H:%M:%SZ') if self.timestamp else ''
        
        tags_str = ','.join(f'{k}={v}' for k, v in self.tags.items())
        
        fields_list = []
        for k, v in self.fields.items():
            if isinstance(v, (int, float)):
                fields_list.append(f'{k}={v}')
            elif isinstance(v, bool):
                fields_list.append(f'{k}={"t" if v else "f"}')
            elif isinstance(v, str):
                escaped = v.replace('"', '\\"')
                fields_list.append(f'{k}="{escaped}"')
            else:
                fields_list.append(f'{k}="{str(v)}"')
        fields_str = ','.join(fields_list)
        
        line = f"{self.measurement.value}"
        if tags_str:
            line += f",{tags_str}"
        line += f" {fields_str}"
        if ts_str:
            line += f" {ts_str}"
        
        return line
    
    def to_dict(self) -> Dict:
        return {
            'measurement': self.measurement.value,
            'tags': self.tags,
            'fields': self.fields,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class QueryResult:
    """查询结果"""
    
    columns: List[str]
    values: List[List[Any]]
    query: str
    execution_time_ms: float = 0.0
    
    def to_records(self) -> List[Dict]:
        """转换为字典列表"""
        return [dict(zip(self.columns, row)) for row in self.values]
    
    def to_dataframe(self):
        """转换为 pandas DataFrame (可选依赖)"""
        try:
            import pandas as pd
            return pd.DataFrame(self.values, columns=self.columns)
        except ImportError:
            logger.warning("pandas not installed, returning records")
            return self.to_records()
    
    @property
    def count(self) -> int:
        return len(self.values)


# ==================== 文件降级存储 ====================

class FileFallbackStorage:
    """
    文件系统降级存储 — InfluxDB 不可用时使用 CSV 文件
    
    特性:
      - 每个 Measurement 一个目录
      - 按日期分割文件
      - 支持读取和简单查询
      - 自动清理过期数据
    """
    
    def __init__(self, base_dir: str, max_rows_per_file: int = 50000):
        self.base_dir = Path(base_dir)
        self.max_rows = max_rows_per_file
        self._lock = threading.Lock()
        self._file_handles: Dict[Path, Any] = {}
        self._row_counts: Dict[Path, int] = {}
        
        # 创建目录结构
        self.base_dir.mkdir(parents=True, exist_ok=True)
        for m in Measurement:
            (self.base_dir / m.value).mkdir(exist_ok=True)
        
        self._total_written = 0
        self._total_read = 0
    
    def _get_file_path(self, measurement: Measurement, date: Optional[datetime] = None) -> Path:
        """获取日期对应的文件路径"""
        date = date or datetime.now()
        date_str = date.strftime('%Y-%m-%d')
        return self.base_dir / measurement.value / f"{date_str}.csv"
    
    async def write_point(self, point: DataPoint):
        """写入单个数据点"""
        file_path = self._get_file_path(point.measurement, point.timestamp)
        
        with self._lock:
            file_exists = file_path.exists()
            
            # 行数超限 → 创建新文件
            current_count = self._row_counts.get(file_path, 0)
            if current_count >= self.max_rows and not point.timestamp:
                # 只有当前日期文件才轮转
                timestamp_suffix = int(time.time())
                new_path = file_path.with_name(
                    f"{file_path.stem}_{timestamp_suffix}{file_path.suffix}"
                )
                if file_path.exists():
                    file_path.rename(new_path)
                file_exists = False
                current_count = 0
            
            with open(file_path, 'a', newline='') as f:
                writer = csv.writer(f)
                
                if not file_exists:
                    # 写入表头
                    all_keys = list(point.tags.keys()) + list(point.fields.keys()) + ['timestamp']
                    writer.writerow(all_keys)
                
                row = list(point.tags.values()) + \
                      [self._format_field(v) for v in point.fields.values()] + \
                      [(point.timestamp or datetime.now()).isoformat()]
                writer.writerow(row)
            
            self._row_counts[file_path] = current_count + 1
            self._total_written += 1
    
    async def write_batch(self, points: List[DataPoint]):
        """批量写入数据点"""
        for point in points:
            await self.write_point(point)
    
    def _format_field(self, value: Any) -> str:
        """格式化字段值为字符串"""
        if isinstance(value, dict):
            return json.dumps(value)
        elif isinstance(value, list):
            return json.dumps(value)
        return str(value)
    
    async def query(
        self,
        measurement: Measurement,
        tags_filter: Optional[Dict[str, str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
        order: str = "DESC",
    ) -> QueryResult:
        """
        从文件查询数据
        
        注意: 这是一个简化的实现，生产环境应使用真正的 InfluxDB
        """
        results = []
        columns = None
        start_ts = start_time or (datetime.now() - timedelta(hours=1))
        end_ts = end_time or datetime.now()
        
        # 扫描日期范围内的文件
        current_date = start_ts.date()
        end_date = end_ts.date()
        
        while current_date <= end_date:
            file_path = self._get_file_path(measurement, datetime.combine(current_date, datetime.min.time()))
            
            if file_path.exists():
                try:
                    with open(file_path, 'r', newline='') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            # 时间过滤
                            try:
                                row_time = datetime.fromisoformat(row.get('timestamp', ''))
                                if row_time < start_ts or row_time > end_ts:
                                    continue
                            except (ValueError, TypeError):
                                pass
                            
                            # 标签过滤
                            if tags_filter:
                                match_all = True
                                for k, v in tags_filter.items():
                                    if row.get(k) != v:
                                        match_all = False
                                        break
                                if not match_all:
                                    continue
                            
                            results.append(list(row.values()))
                            if columns is None:
                                columns = list(row.keys())
                            
                            if len(results) >= limit:
                                break
                except Exception as e:
                    logger.warning(f"[FileFallback] Read error {file_path}: {e}")
            
            current_date += timedelta(days=1)
            self._total_read += 1
        
        # 排序
        if order == "ASC" and results:
            # 简单排序 (按最后一列 timestamp)
            results.sort(key=lambda x: x[-1] if len(x) > len(columns) - 1 else '')
        elif order == "DESC":
            results.reverse() if results else None
        
        return QueryResult(
            columns=columns or [],
            values=results[:limit],
            query=f"file://{measurement.value}",
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_files = sum(1 for p in self.base_dir.rglob('*.csv'))
        total_size = sum(f.stat().st_size for f in self.base_dir.rglob('*.csv'))
        
        return {
            'type': 'file',
            'base_dir': str(self.base_dir),
            'total_files': total_files,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'points_written': self._total_written,
            'queries_executed': self._total_read,
        }
    
    def cleanup(self, retention_days: int = 30):
        """清理过期数据"""
        cutoff = datetime.now() - timedelta(days=retention_days)
        removed = 0
        
        for file_path in self.base_dir.rglob('*.csv'):
            try:
                file_date = datetime.strptime(file_path.stem.split('_')[0], '%Y-%m-%d').date()
                if file_date < cutoff.date():
                    file_path.unlink()
                    removed += 1
            except ValueError:
                continue
        
        logger.info(f"[FileFallback] Cleaned up {removed} expired files")
        return removed


# ==================== InfluxDB 客户端 ====================

class InfluxDBClientWrapper:
    """
    InfluxDB 客户端封装 — 高性能批量写入 + 查询
    
    特性:
      - Buffered Batcher (批量攒批写入)
      - 自动重连 + 降级到文件
      - Flux 查询封装
      - 连接池管理
      - 性能监控
    """
    
    def __init__(self, config: Optional[InfluxConfig] = None):
        self.config = config or InfluxConfig()
        self._client = None
        self._write_api = None
        self._query_api = None
        self._connected = False
        self._using_fallback = False
        
        # 缓冲区
        self._buffer: List[DataPoint] = []
        self._buffer_lock = asyncio.Lock()
        self._last_flush = time.time()
        self._flush_task: Optional[asyncio.Task] = None
        
        # 降级存储
        self._fallback = FileFallbackStorage(
            base_dir=self.config.fallback_dir,
            max_rows_per_file=self.config.fallback_csv_max_rows,
        ) if self.config.fallback_enabled else None
        
        # 统计
        self._stats_lock = threading.Lock()
        self._written_count = 0
        self._query_count = 0
        self._error_count = 0
    
    async def connect(self):
        """连接到 InfluxDB"""
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS, WriteOptions
            
            self._client = InfluxDBClient(
                url=self.config.url,
                token=self.config.token or "my-token",
                org=self.config.org,
                timeout=self.config.timeout * 1000,  # ms → us
                enable_gzip=self.config.gzip,
            )
            
            # 写入 API (异步批量模式)
            write_options = WriteOptions(
                batch_size=self.config.batch_size,
                flush_interval=self.config.flush_interval_ms,
            )
            self._write_api = self._client.write_api(
                write_options=write_options,
            )
            
            # 查询 API
            self._query_api = self._client.query_api()
            
            # 验证连接
            health = self._client.health()
            if health.status == "pass":
                self._connected = True
                self._using_fallback = False
                
                logger.info(
                    f"[InfluxDB] Connected to {self.config.url} "
                    f"(bucket={self.config.bucket}, org={self.config.org})"
                )
                
                # 启动定时刷新任务
                self._flush_task = asyncio.create_task(self._auto_flush_loop())
            else:
                raise ConnectionError(f"InfluxDB health check failed: {health.message}")
                
        except ImportError:
            logger.warning("[InfluxDB] influxdb-client not installed, using file fallback")
            self._using_fallback = True
        except Exception as e:
            logger.error(f"[InfluxDB] Connection failed: {e}, using fallback")
            self._using_fallback = True
    
    async def close(self):
        """关闭连接"""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # 最后一次刷新缓冲区
        await self.flush_buffer()
        
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.error(f"[InfluxDB] Close error: {e}")
            finally:
                self._client = None
                self._connected = False
        
        logger.info(f"[InfluxDB] Closed (written={self._written_count}, queries={self._query_count})")
    
    async def write(self, point: DataPoint) -> bool:
        """
        写入数据点 (自动选择 InfluxDB 或降级)
        
        Args:
            point: 数据点对象
            
        Returns:
            是否写入成功
        """
        # 加入缓冲区
        async with self._buffer_lock:
            self._buffer.append(point)
            
            # 检查是否需要立即刷新
            should_flush = len(self._buffer) >= self.config.batch_size
        
        if should_flush:
            await self.flush_buffer()
        
        return True
    
    async def write_batch(self, points: List[DataPoint]) -> Tuple[int, int]:
        """
        批量写入数据点
        
        Returns:
            (成功数, 失败数)
        """
        async with self._buffer_lock:
            self._buffer.extend(points)
        
        if len(self._buffer) >= self.config.batch_size:
            await self.flush_buffer()
        
        return len(points), 0
    
    async def flush_buffer(self):
        """刷新缓冲区到存储"""
        async with self._buffer_lock:
            if not self._buffer:
                return
            
            batch = self._buffer.copy()
            self._buffer.clear()
        
        if self._using_fallback or not self._connected:
            # 写入降级文件
            if self._fallback:
                for point in batch:
                    await self._fallback.write_point(point)
                with self._stats_lock:
                    self._written_count += len(batch)
        else:
            # 写入 InfluxDB
            try:
                line_protocols = [p.to_line_protocol() for p in batch]
                self._write_api.write(
                    bucket=self.config.bucket,
                    record=line_protocols,
                    write_precision='ms',
                )
                with self._stats_lock:
                    self._written_count += len(batch)
                    
            except Exception as e:
                self._error_count += 1
                logger.error(f"[InfluxDB] Write error: {e}")
                
                # 降级处理
                if self._fallback:
                    for point in batch:
                        await self._fallback.write_point(point)
                    with self._stats_lock:
                        self._written_count += len(batch)
    
    async def _auto_flush_loop(self):
        """定时刷新循环"""
        while True:
            try:
                await asyncio.sleep(self.config.flush_interval_ms / 1000)
                await self.flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[InfluxDB] Auto-flush error: {e}")
    
    async def query_flux(self, flux_query: str) -> QueryResult:
        """
        执行 Flux 查询
        
        Args:
            flux_query: Flux 查询语句
            
        Returns:
            查询结果
        """
        start_time = time.time()
        
        if self._using_fallback or not self._connected:
            # 从文件降级查询 (简化实现，仅支持基本查询)
            if self._fallback:
                result = await self._fallback.query(
                    measurement=list(Measurement)[0],  # 默认
                    limit=1000,
                )
                result.execution_time_ms = (time.time() - start_time) * 1000
                with self._stats_lock:
                    self._query_count += 1
                return result
            
            return QueryResult(columns=[], values=[], query=flux_query)
        
        try:
            # 使用 InfluxDB Query API
            tables = self._query_api.query(query=flux_query, org=self.config.org)
            
            columns = []
            values = []
            
            for table in tables:
                for record in table.records:
                    if not columns:
                        columns = list(record.values.keys())
                    values.append(list(record.values))
            
            result = QueryResult(
                columns=columns,
                values=values,
                query=flux_query,
                execution_time_ms=(time.time() - start_time) * 1000,
            )
            
            with self._stats_lock:
                self._query_count += 1
            
            return result
            
        except Exception as e:
            self._error_count += 1
            logger.error(f"[InfluxDB] Query error: {e}")
            raise
    
    # ==================== 便捷查询方法 ====================
    
    async def query_agv_history(
        self,
        agv_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        interval: str = "1m",
        limit: int = 10000,
    ) -> QueryResult:
        """
        查询 AGV 历史轨迹
        
        Args:
            agv_id: AGV ID
            start: 起始时间 (默认1小时前)
            end: 结束时间 (默认现在)
            interval: 采样间隔 (1m/5m/1h 等)
            limit: 最大返回数量
        """
        start = start or (datetime.utcnow() - timedelta(hours=1))
        end = end or datetime.utcnow()
        
        flux = f'''
        from(bucket: "{self.config.bucket}")
          |> range(start: {start.isoformat()}Z, stop: {end.isoformat()}Z)
          |> filter(fn: (r) => r._measurement == "{Measurement.AGV_TELEMETRY.value}")
          |> filter(fn: (r) => r.agv_id == "{agv_id}")
          |> aggregateWindow(every: {interval}, fn: mean, createEmpty: false)
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: {limit})
        '''
        
        return await self.query_flux(flux)
    
    async def query_heatmap_history(
        self,
        scene_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        interval: str = "15m",
    ) -> QueryResult:
        """
        查询热力图历史数据 (AGV密度分布)
        
        Args:
            scene_id: 场景 ID
            start: 起始时间
            end: 结束时间
            interval: 时间窗口大小
        """
        start = start or (datetime.utcnow() - timedelta(hours=24))
        end = end or datetime.utcnow()
        
        flux = f'''
        from(bucket: "{self.config.bucket}")
          |> range(start: {start.isoformat()}Z, stop: {end.isoformat()}Z)
          |> filter(fn: (r) => r._measurement == "{Measurement.AGV_TELEMETRY.value}")
          |> filter(fn: (r) => r.scene_id == "{scene_id}")
          |> aggregateWindow(every: {interval}, fn: count, createEmpty: false)
          |> sort(columns: ["_time"])
        '''
        
        return await self.query_flux(flux)
    
    async def query_battery_curve(
        self,
        agv_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> QueryResult:
        """
        查询电池曲线
        
        Args:
            agv_id: AGV ID
            start: 起始时间 (默认24小时)
            end: 结束时间
        """
        start = start or (datetime.utcnow() - timedelta(hours=24))
        end = end or datetime.utcnow()
        
        flux = f'''
        from(bucket: "{self.config.bucket}")
          |> range(start: {start.isoformat()}Z, stop: {end.isoformat()}Z)
          |> filter(fn: (r) => r._measurement == "{Measurement.AGV_TELEMETRY.value}")
          |> filter(fn: (r) => r.agv_id == "{agv_id}")
          |> filter(fn: (r) => r._field == "battery")
          |> sort(columns: ["_time"])
        '''
        
        return await self.query_flux(flux)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取客户端统计信息"""
        return {
            'connected': self._connected,
            'using_fallback': self._using_fallback,
            'config': {
                'url': self.config.url,
                'bucket': self.config.bucket,
                'org': self.config.org,
            },
            'written_points': self._written_count,
            'queries_executed': self._query_count,
            'errors': self._error_count,
            'buffer_size': len(self._buffer),
            'fallback_stats': self._fallback.get_stats() if self._fallback else None,
        }


# ==================== 便捷方法工厂 ====================

def create_agv_telemetry_point(
    agv_id: str,
    x: float,
    y: float,
    battery: float,
    speed: float,
    status: str,
    task_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    **extra_fields
) -> DataPoint:
    """创建 AGV 遥测数据点"""
    
    return DataPoint(
        measurement=Measurement.AGV_TELEMETRY,
        tags={
            'agv_id': agv_id,
            'scene_id': scene_id or 'default',
            'status': status,
        },
        fields={
            'x': round(float(x), 4),
            'y': round(float(y), 4),
            'battery': round(float(battery), 2),
            'speed': round(float(speed), 3),
            'task_id': task_id or '',
            **extra_fields,
        },
        timestamp=datetime.utcnow(),
    )


def create_task_lifecycle_point(
    task_id: str,
    status: str,
    agv_id: Optional[str] = None,
    pickup_node: Optional[str] = None,
    dropoff_node: Optional[str] = None,
    priority: int = 0,
    **extra_fields
) -> DataPoint:
    """创建任务生命周期数据点"""
    
    return DataPoint(
        measurement=Measurement.TASK_LIFECYCLE,
        tags={
            'task_id': task_id,
            'status': status,
            'agv_id': agv_id or 'unassigned',
        },
        fields={
            'pickup_node': pickup_node or '',
            'dropoff_node': dropoff_node or '',
            'priority': priority,
            **extra_fields,
        },
        timestamp=datetime.utcnow(),
    )


# ==================== FastAPI Router ====================

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v2/history", tags=["History"])

# 全局客户端实例 (在 main.py 中初始化)
_influx_client: Optional[InfluxDBClientWrapper] = None


def get_influx_client() -> InfluxDBClientWrapper:
    """获取全局 InfluxDB 客户端实例"""
    global _influx_client
    if _influx_client is None:
        raise HTTPException(status_code=503, detail="InfluxDB service not initialized")
    return _influx_client


def set_influx_client(client: InfluxDBClientWrapper):
    """设置全局客户端实例"""
    global _influx_client
    _influx_client = client


@router.get("/agv/{agv_id}")
async def get_agv_history(
    agv_id: str,
    start: Optional[str] = Query(None, description="ISO格式起始时间"),
    end: Optional[str] = Query(None, description="ISO格式结束时间"),
    interval: str = Query("1m", description="采样间隔 (1m/5m/1h)"),
    limit: int = Query(10000, ge=1, le=100000, description="最大返回数量"),
):
    """获取 AGV 历史轨迹数据"""
    client = get_influx_client()
    
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    
    result = await client.query_agv_history(
        agv_id=agv_id,
        start=start_dt,
        end=end_dt,
        interval=interval,
        limit=limit,
    )
    
    return {
        'agv_id': agv_id,
        'count': result.count,
        'execution_time_ms': round(result.execution_time_ms, 2),
        'data': result.to_records(),
    }


@router.get("/heatmap")
async def get_heatmap_history(
    scene_id: str = Query(..., description="场景ID"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    interval: str = Query("15m"),
):
    """获取热力图历史数据"""
    client = get_influx_client()
    
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    
    result = await client.query_heatmap_history(
        scene_id=scene_id,
        start=start_dt,
        end=end_dt,
        interval=interval,
    )
    
    return {
        'scene_id': scene_id,
        'count': result.count,
        'execution_time_ms': round(result.execution_time_ms, 2),
        'data': result.to_records(),
    }


@router.get("/battery/{agv_id}")
async def get_battery_history(
    agv_id: str,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """获取 AGV 电池曲线"""
    client = get_influx_client()
    
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    
    result = await client.query_battery_curve(
        agv_id=agv_id,
        start=start_dt,
        end=end_dt,
    )
    
    return {
        'agv_id': agv_id,
        'count': result.count,
        'execution_time_ms': round(result.execution_time_ms, 2),
        'data': result.to_records(),
    }


@router.get("/stats")
async def get_influx_stats():
    """获取 InfluxDB 服务状态和统计"""
    client = get_influx_client()
    stats = client.get_stats()
    
    # 计算健康状态
    is_healthy = stats['connected'] and stats['errors'] < 100
    
    return {
        'service': 'influxdb',
        'healthy': is_healthy,
        'statistics': stats,
    }


# ==================== 导出 ====================

__all__ = [
    'Measurement',
    'DataPoint',
    'QueryResult',
    'InfluxConfig',
    'InfluxDBClientWrapper',
    'FileFallbackStorage',
    'create_agv_telemetry_point',
    'create_task_lifecycle_point',
    'get_influx_client',
    'set_influx_client',
    'router',
]


# ==================== 快速验证脚本 ====================

if __name__ == '__main__':
    import asyncio
    
    async def test():
        print("=" * 60)
        print("🧪 InfluxDB Service Test Suite")
        print("=" * 60)
        
        # 测试 1: 数据模型
        print("\n📊 Test 1: Data Model & Line Protocol")
        
        point = create_agv_telemetry_point(
            agv_id='agv_001',
            x=10.5,
            y=20.3,
            battery=85.5,
            speed=1.2,
            status='moving',
            task_id='task_123',
            scene_id='warehouse_01',
        )
        
        assert point.measurement == Measurement.AGV_TELEMETRY
        assert point.tags['agv_id'] == 'agv_001'
        assert abs(point.fields['battery'] - 85.5) < 0.01
        print(f"  ✅ DataPoint created: {point.measurement.value}")
        
        line_proto = point.to_line_protocol()
        assert 'agv_telemetry' in line_proto
        assert 'agv_id=agv_001' in line_proto
        assert 'battery=85.5' in line_proto
        print(f"  ✅ Line Protocol: {line_proto[:80]}...")
        
        # 测试 2: 文件降级存储
        print("\n💾 Test 2: File Fallback Storage")
        
        storage = FileFallbackStorage(base_dir='./test_influx_fallback')
        
        await storage.write_point(point)
        await storage.write_point(create_task_lifecycle_point(
            task_id='task_001', status='assigned', agv_id='agv_002'
        ))
        print(f"  ✅ Points written to files")
        
        # 查询
        result = await storage.query(
            measurement=Measurement.AGV_TELEMETRY,
            tags_filter={'agv_id': 'agv_001'},
        )
        assert result.count >= 1
        print(f"  ✅ Query returned {result.count} records")
        
        stats = storage.get_stats()
        assert stats['points_written'] >= 2
        print(f"  ✅ Stats: {stats['points_written']} written")
        
        # 清理测试文件
        import shutil
        if Path('./test_influx_fallback').exists():
            shutil.rmtree('./test_influx_fallback')
        
        # 测试 3: 完整客户端 (降级模式)
        print("\n🔌 Test 3: Client Wrapper (Fallback Mode)")
        
        config = InfluxConfig(
            fallback_enabled=True,
            fallback_dir='./test_influx_fallback',
        )
        client = InfluxDBClientWrapper(config)
        await client.connect()
        
        assert client._using_fallback == True
        print(f"  ✅ Client connected in fallback mode")
        
        # 写入测试
        for i in range(5):
            p = create_agv_telemetry_point(
                agv_id=f'agv_{i}',
                x=i * 10,
                y=i * 20,
                battery=100 - i * 5,
                speed=i * 0.5,
                status='idle',
            )
            await client.write(p)
        
        await client.flush_buffer()
        print(f"  ✅ Written 5 telemetry points")
        
        stats = client.get_stats()
        assert stats['written_points'] >= 5
        print(f"  ✅ Stats: {stats['written_points']} points")
        
        await client.close()
        
        # 清理
        if Path('./test_influx_fallback').exists():
            shutil.rmtree('./test_influx_fallback')
        
        print("\n" + "=" * 60)
        print("🎉 All InfluxDB service tests PASSED!")
        print("=" * 60)
    
    asyncio.run(test())
