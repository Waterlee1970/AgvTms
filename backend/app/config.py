"""
Application configuration via environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from typing import Optional


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, str(default)).lower().strip()
    return val in ("1", "true", "yes", "on")


class Settings:
    """Application settings loaded from environment variables."""

    # ---- App ----
    APP_NAME: str = _env("APP_NAME", "AGV-TMS")
    APP_VERSION: str = _env("APP_VERSION", "1.6.0")  # P2-3: 统一版本号源，main.py/run.py 应引用此值
    DEBUG: bool = _env_bool("DEBUG", False)

    # ---- Database ----
    DATABASE_URL: str = _env(
        "DATABASE_URL",
        "postgresql+asyncpg://agvtms:agvtms_dev@127.0.0.1:5432/agvtms",
    )
    DB_POOL_SIZE: int = _env_int("DB_POOL_SIZE", 10)
    DB_MAX_OVERFLOW: int = _env_int("DB_MAX_OVERFLOW", 20)
    DB_ECHO: bool = _env_bool("DB_ECHO", False)

    # ---- Redis ----
    REDIS_URL: str = _env("REDIS_URL", "redis://127.0.0.1:6379/0")
    REDIS_CACHE_TTL: int = _env_int("REDIS_CACHE_TTL", 300)  # 5 minutes

    # ---- Algorithm ----
    ACTIVE_ALGORITHM_VERSION: str = _env("ACTIVE_ALGORITHM_VERSION", "v2")
    USE_DB_PERSISTENCE: bool = _env_bool("USE_DB_PERSISTENCE", True)
    USE_REDIS_CACHE: bool = _env_bool("USE_REDIS_CACHE", True)

    # ---- Server ----
    HOST: str = _env("HOST", "0.0.0.0")
    PORT: int = _env_int("PORT", 8000)

    # ---- Industrial Protocol ----
    OPCUA_SERVER_URL: str = _env("OPCUA_SERVER_URL", "opc.tcp://localhost:4840")

    # ---- Kafka (Phase 5.5) ----
    ENABLE_KAFKA: bool = _env_bool("ENABLE_KAFKA", False)
    KAFKA_BOOTSTRAP_SERVERS: str = _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_GROUP_ID: str = _env("KAFKA_GROUP_ID", "agv-tms-consumer-group")

    # ---- InfluxDB (Phase 5.5) ----
    ENABLE_INFLUXDB: bool = _env_bool("ENABLE_INFLUXDB", False)
    INFLUXDB_URL: str = _env("INFLUXDB_URL", "http://localhost:8086")
    INFLUXDB_TOKEN: str = _env("INFLUXDB_TOKEN", "my-super-secret-influxdb-token")
    INFLUXDB_ORG: str = _env("INFLUXDB_ORG", "agv-tms")
    INFLUXDB_BUCKET: str = _env("INFLUXDB_BUCKET", "agv_data")

    # ---- MQTT (Mosquitto) ----
    ENABLE_MQTT: bool = _env_bool("ENABLE_MQTT", False)
    MQTT_BROKER_HOST: str = _env("MQTT_BROKER_HOST", "localhost")
    MQTT_BROKER_PORT: int = _env_int("MQTT_BROKER_PORT", 1883)
    MQTT_MODE: str = _env("MQTT_MODE", "simulation")  # simulation / live


settings = Settings()
