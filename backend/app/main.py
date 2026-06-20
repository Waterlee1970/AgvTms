"""
FastAPI Application Entry Point.

AGV-TMS: Flexible Logistics Scheduling System
Hybrid scheduling with ACO + SA + NLP (V1) / MIP + A*+TW + SIPP (V2)
+ Theta* Any-Angle Path Planning (ROI Phase 1)
+ Structured Logging + Prometheus Metrics (ROI Phase 2)
+ Deadlock Prevention + Traffic Control (ROI Phase 1)
"""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router as routes_router
from .api.evaluator_api import router as evaluator_router
from .api.evaluator_api import industrial_router as industrial_integration_router
from .api.v2_routes import router as v2_router
from .api.flow_routes import router as flow_router
from .api.vda5050_routes import router as vda5050_router
from .api.vehicle_routes import router as vehicle_router
from .api.analytics_routes import router as analytics_router
from .api.simulation_routes import router as simulation_router
from .api.monitoring_routes import router as monitoring_router
from .api.phase5_8_routes import router as phase5_8_router
from .api.digital_twin_ws import router as digital_twin_router  # WebSocket for DigitalTwin3D
from .config import settings

# Phase 5.5: InfluxDB 历史数据 API
try:
    from .core.influx_service import router as history_router
    _influx_router_available = True
except ImportError:
    _influx_router_available = False

# ==================== 结构化日志系统 (替换print/logging) ====================
try:
    from .core.structured_log import setup_structured_logging, create_logging_middleware, get_logger
    setup_structured_logging(
        level=os.getenv("LOG_LEVEL", "INFO"),
        json_output=os.getenv("ENV", "dev") != "dev",
        enable_sampling=True,
    )
    logger = get_logger("main")
    _structured_log_enabled = True
except ImportError:
    # 降级到标准logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    _structured_log_enabled = False
    def create_logging_middleware(): return None  # no-op fallback

# ==================== Prometheus 监控指标 ====================
try:
    from .core.prometheus_metrics import (
        metrics, metrics_endpoint, get_metrics_content_type,
        AgvTmsMetrics,
    )
    _prometheus_enabled = True
except ImportError:
    metrics = None
    _prometheus_enabled = False
    async def metrics_endpoint(): return Response(content=b"# Prometheus not installed\n")
    def get_metrics_content_type(): return "text/plain"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup and shutdown."""
    # ---- Startup ----
    logger.info("Starting AGV-TMS", app_name=settings.APP_NAME, version=settings.APP_VERSION)

    # Initialize database
    try:
        from .db.database import init_db, _check_db_available
        await init_db()
        if await _check_db_available():
            # Seed if empty
            from .db.seed import seed
            await seed(reset=False)
            # Load state from DB
            from .services.schedule_service import schedule_service
            await schedule_service._load_state_from_db()
    except Exception as e:
        logger.warning("DB init failed, running in memory mode", error=str(e))

    # Phase 5: 初始化架构集成
    try:
        from .core.integration import initialize_integration
        from .api.routes import manager as ws_manager
        initialize_integration(ws_manager=ws_manager)
        logger.info("Phase 5 integration initialized")
    except Exception as e:
        logger.warning("Phase 5 integration init failed", error=str(e))

    # Phase 5.5: 初始化 Kafka Event Bus (异步事件驱动)
    if settings.ENABLE_KAFKA:
        try:
            from .core.kafka_service import (
                EventBus, KafkaConfig, KafkaTopic,
            )
            config = KafkaConfig(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                group_id=settings.KAFKA_GROUP_ID,
            )
            bus = EventBus.get_instance(config)
            await bus.initialize()
            app.state.event_bus = bus

            # 注册内置消息处理器: AGV状态变更 → 更新Redis缓存
            @bus.subscribe(KafkaTopic.AGV_STATUS_UPDATE)  # type: ignore
            async def _on_agv_status(msg):
                """AGV状态变更 → 更新Redis缓存"""
                try:
                    from .services.redis_service import redis_service
                    agv_id = msg.value.get('agv_id')
                    if agv_id:
                        await redis_service.hset(
                            f"agv:{agv_id}:status",
                            mapping=msg.value,
                            ex=300,
                        )
                except Exception:
                    pass

            await bus.start_consuming()
            logger.info("Kafka EventBus initialized & consuming")
        except Exception as e:
            logger.warning("Kafka init failed, events will be synchronous", error=str(e))
    else:
        logger.info("Kafka disabled (ENABLE_KAFKA=false), using sync mode")

    # Phase 5.5: 初始化 InfluxDB 时序存储
    if settings.ENABLE_INFLUXDB:
        try:
            from .core.influx_service import (
                InfluxDBClientWrapper, InfluxConfig,
                set_influx_client,
            )
            influx_client = InfluxDBClientWrapper(InfluxConfig(
                url=settings.INFLUXDB_URL,
                token=settings.INFLUXDB_TOKEN,
                org=settings.INFLUXDB_ORG,
                bucket=settings.INFLUXDB_BUCKET,
            ))
            await influx_client.connect()
            set_influx_client(influx_client)
            app.state.influx_client = influx_client
            logger.info("InfluxDB connected")
        except Exception as e:
            logger.warning("InfluxDB init failed, using file fallback", error=str(e))
    else:
        logger.info("InfluxDB disabled (ENABLE_INFLUXDB=false)")

    # Phase 5.5: 初始化 MQTT Broker 适配器
    if settings.ENABLE_MQTT:
        try:
            from .adapters.mqtt_vehicle_adapter import MqttVehicleAdapter, MqttConnectionConfig
            mqtt_config = MqttConnectionConfig(
                broker_host=settings.MQTT_BROKER_HOST,
                broker_port=settings.MQTT_BROKER_PORT,
            )
            mqtt_adapter = MqttVehicleAdapter(
                mode=settings.MQTT_MODE,
                config=mqtt_config,
                vda5050_mode=True,
            )
            connected = await mqtt_adapter.connect()
            app.state.mqtt_adapter = mqtt_adapter
            logger.info(f"MQTT adapter initialized (mode={settings.MQTT_MODE}, connected={connected})")
        except Exception as e:
            logger.warning("MQTT init failed", error=str(e))
    else:
        logger.info("MQTT disabled (ENABLE_MQTT=False)")

    logger.info("Application ready")

    yield

    # ---- Shutdown ----
    logger.info("Shutting down...")
    try:
        # Phase 5: 停止调度循环
        from .core.integration import stop_scheduler_loop
        await stop_scheduler_loop()
    except Exception as e:
        logger.warning("Scheduler loop stop error", error=str(e))

    # Phase 5.5: 关闭 MQTT
    try:
        mqtt = getattr(app.state, 'mqtt_adapter', None)
        if mqtt:
            await mqtt.disconnect()
            logger.info("MQTT adapter disconnected")
    except Exception as e:
        logger.warning("MQTT shutdown error", error=str(e))

    # Phase 5.5: 关闭 InfluxDB
    try:
        influx = getattr(app.state, 'influx_client', None)
        if influx:
            await influx.close()
            logger.info("InfluxDB client closed")
    except Exception as e:
        logger.warning("InfluxDB shutdown error", error=str(e))

    # Phase 5.5: 关闭 Kafka EventBus
    try:
        bus = getattr(app.state, 'event_bus', None)
        if bus:
            await bus.shutdown()
            logger.info("Kafka EventBus shutdown")
    except Exception as e:
        logger.warning("Kafka shutdown error", error=str(e))

    try:
        # Phase 6: 停止分布式 Worker
        from .core.distributed import distributed_scheduler
        await distributed_scheduler.stop_worker()
    except Exception as e:
        logger.warning("Distributed worker stop error", error=str(e))

    try:
        from .db.database import close_db
        await close_db()
        from .services.redis_service import close_redis
        await close_redis()
    except Exception as e:
        logger.warning("Shutdown cleanup error", error=str(e))


# Create FastAPI app
app = FastAPI(
    title="AGV-TMS 柔性物流调度系统",
    description="""
## AGV + 输送线混合调度系统

### V1 算法引擎 (原型级)
- **蚁群算法 (ACO)**: 多AGV路径规划
- **模拟退火 (SA)**: 任务最优分配
- **非线性规划 (NLP)**: 输送线任务排序

### V2 算法引擎 (工业级) 🚀
- **MIP/CP-SAT (OR-Tools)**: 精确任务分配，可证最优
- **双向A* + 时间窗**: 无碰撞路径规划
- **SIPP安全区间规划**: 大规模AGV支持
- **D*Lite动态重规划**: 实时障碍响应
- **区域锁 + 死锁检测**: 交通管控

### 数据持久化
- **PostgreSQL**: 地图/任务/AGV/调度结果持久化
- **Redis**: 实时状态缓存 + WebSocket广播

### 协议标准 (Sprint 2)
- **VDA5050**: AGV标准协议适配

对标：海康威视RCS-2000 V4.0、博世输送线、罗克韦尔APS
    """,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware — 开发环境允许全量，生产环境应收紧
_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 结构化日志中间件 (自动绑定request_id + 记录请求耗时)
if _structured_log_enabled:
    log_mw_cls = create_logging_middleware()
    if log_mw_cls:
        app.add_middleware(log_mw_cls)

# Include API routes
app.include_router(routes_router)        # /api/* (核心业务API, V1兼容)
app.include_router(v2_router)            # /api/v2/* (V2工业级API)
app.include_router(flow_router)          # /api/v2/flows/* (流程编排)
app.include_router(vda5050_router)       # /api/v2/vda5050/* (VDA5050协议)
app.include_router(vehicle_router)       # /api/v2/vehicles/* + /api/v2/traffic/* (多车型+交通)
app.include_router(analytics_router)     # /api/v2/analytics/* (数据分析)
app.include_router(simulation_router)    # /api/v2/simulation/* (仿真引擎)
app.include_router(monitoring_router)    # /metrics + /api/v2/system/info (监控)
app.include_router(evaluator_router)     # /api/v2/evaluator/* (算法评测API)
app.include_router(industrial_integration_router)  # /api/v2/industrial/* (OPC UA + WMS/MES 集成)
app.include_router(phase5_8_router)              # /api/v2/advanced/* (Phase 5-8 高级功能)
app.include_router(digital_twin_router)           # /api/v2/digital-tin/* (3D Digital Twin WebSocket)

# Phase 5.5: InfluxDB 历史数据 API
if _influx_router_available:
    app.include_router(history_router)               # /api/v2/history/*

# Prometheus metrics ticker (启动后开始计时)
if _prometheus_enabled and metrics:
    @app.on_event("startup")
    async def start_metrics_ticker():
        """启动uptime计数器."""
        import asyncio
        async def tick():
            while True:
                metrics.tick()
                await asyncio.sleep(1.0)
        asyncio.create_task(tick())


@app.get("/")
async def root():
    return {
        "name": "AGV-TMS 柔性物流调度系统",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "metrics": "/metrics" if _prometheus_enabled else "disabled (pip install prometheus-client)",
        "algorithms": {
            "v1": ["ACO", "SA", "NLP", "HYBRID"],
            "v2": ["MIP/CP-SAT", "A*+TimeWindow", "SIPP", "D*Lite", "ZoneControl",
                   "Theta*",  # ROI Phase 1: Any-angle path planning
                   "MAPF-CBS"],  # ROI Phase 1: Multi-agent collision-free
        },
        "features": [
            "PostgreSQL持久化",
            "Redis缓存",
            "V1/V2算法切换",
            "算法评价体系",
            "Phase A-D: 事件总线+状态机+适配器+策略",
            "Phase 5-8: 集成+分布式+VDA5050+数字孪生",
            "ROI-P0: 结构化日志 (structlog)",
            "ROI-P0: Prometheus指标 (/metrics)",
            "ROI-P0: 死锁预防 + 交通管制 (TrafficControlSystem)",
            "ROI-P1: Theta*任意角度路径规划",
        ],
    }


@app.get("/health")
async def health_check():
    from .db.database import _check_db_available
    from .services.redis_service import _check_redis_available
    return {
        "status": "healthy",
        "algorithms_loaded": True,
        "database": await _check_db_available() if settings.USE_DB_PERSISTENCE else False,
        "redis": await _check_redis_available() if settings.USE_REDIS_CACHE else False,
        "active_algorithm": settings.ACTIVE_ALGORITHM_VERSION,
        "structured_logging": _structured_log_enabled,
        "prometheus_metrics": _prometheus_enabled,
        # Phase 5.5 infrastructure status
        "kafka": getattr(getattr(app, 'state', None), 'event_bus', None) is not None,
        "influxdb": getattr(getattr(app, 'state', None), 'influx_client', None) is not None,
        "mqtt": getattr(getattr(app, 'state', None), 'mqtt_adapter', None) is not None,
    }


# ==================== Prometheus /metrics 端点 ====================

if _prometheus_enabled:
    
    @app.get("/metrics")
    async def prometheus_metrics():
        """Prometheus metrics endpoint — for Grafana/AlertManager scraping."""
        from .core.prometheus_metrics import get_metrics_content_type
        content = await metrics_endpoint()
        return Response(
            content=content,
            media_type=get_metrics_content_type(),
        )
