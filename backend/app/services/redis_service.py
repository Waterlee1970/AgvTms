"""
Redis client for real-time state caching and Pub/Sub.

Provides:
- AGV real-time position/status cache
- Task real-time state cache
- WebSocket broadcast via Pub/Sub
- Graceful fallback to no-op when Redis is unavailable
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from ..config import settings

logger = logging.getLogger(__name__)

_redis: Optional[Any] = None  # redis.asyncio.Redis
_redis_available: Optional[bool] = None


async def _check_redis_available() -> bool:
    """Check if Redis is reachable."""
    global _redis_available
    if _redis_available is not None:
        return _redis_available
    if not settings.USE_REDIS_CACHE:
        _redis_available = False
        return False
    try:
        client = get_redis()
        await client.ping()
        _redis_available = True
        logger.info("✅ Redis connected: %s", settings.REDIS_URL)
    except Exception as e:
        _redis_available = False
        logger.warning("⚠️ Redis unavailable (%s), running without cache", e)
    return _redis_available


def get_redis() -> Any:
    """Get or create the Redis client (singleton)."""
    global _redis
    if _redis is None:
        try:
            import redis.asyncio as aioredis
            _redis = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        except ImportError:
            logger.warning("redis package not installed, caching disabled")
    return _redis


async def cache_get(key: str) -> Optional[str]:
    """Get a cached value, returns None if Redis unavailable or key missing."""
    if not await _check_redis_available():
        return None
    try:
        return await _redis.get(key)
    except Exception as e:
        logger.debug("Redis GET failed for %s: %s", key, e)
        return None


async def cache_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    """Set a cached value with optional TTL."""
    if not await _check_redis_available():
        return
    try:
        if ttl:
            await _redis.setex(key, ttl, value)
        else:
            await _redis.set(key, value)
    except Exception as e:
        logger.debug("Redis SET failed for %s: %s", key, e)


async def cache_delete(key: str) -> None:
    """Delete a cached key."""
    if not await _check_redis_available():
        return
    try:
        await _redis.delete(key)
    except Exception as e:
        logger.debug("Redis DELETE failed for %s: %s", key, e)


async def cache_get_json(key: str) -> Optional[Any]:
    """Get and deserialize a JSON value from cache."""
    raw = await cache_get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_set_json(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """Serialize and set a JSON value in cache."""
    await cache_set(key, json.dumps(value, default=str), ttl)


async def publish(channel: str, message: Dict[str, Any]) -> None:
    """Publish a message to a Redis Pub/Sub channel."""
    if not await _check_redis_available():
        return
    try:
        await _redis.publish(channel, json.dumps(message, default=str))
    except Exception as e:
        logger.debug("Redis PUBLISH failed on %s: %s", channel, e)


async def close_redis() -> None:
    """Close Redis connection on shutdown."""
    global _redis, _redis_available
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
        _redis = None
        _redis_available = None


# ---- Cache key helpers ----

def map_cache_key() -> str:
    return "map:active"


def agv_state_key(agv_id: str) -> str:
    return f"agv:{agv_id}:state"


def agvs_index_key() -> str:
    return "agvs:active"


def task_state_key(task_id: str) -> str:
    return f"task:{task_id}:state"


def schedule_result_key(result_id: str) -> str:
    return f"schedule:{result_id}"


# ---- Pub/Sub channels ----

CHANNEL_AGV_UPDATES = "channel:agv_updates"
CHANNEL_TASK_UPDATES = "channel:task_updates"
CHANNEL_SCHEDULE = "channel:schedule"
