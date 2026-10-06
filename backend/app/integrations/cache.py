"""
Redis cache helpers for external provider responses (PRD §27).

Key conventions (§27):
    weather:{lat}:{lng}       latest weather observation     TTL 300 s
    forecast:{lat}:{lng}      hourly forecast                TTL 1800 s
    imd:rainfall:{region}     processed historical rainfall  TTL 86400 s
    osm:{bbox}                cached OSM feature extract     TTL 86400 s

All operations swallow Redis failures: cache is an optimisation, never a
dependency — when Redis is down connectors still work (slower, unsaved).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.config import get_settings

logger = structlog.get_logger()

DEFAULT_TTL_SECONDS = 300

_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def make_key(prefix: str, *parts: Any) -> str:
    """Stable cache key: prefix + deterministic hash of the parts."""
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


async def cache_get(key: str) -> Any | None:
    """Return parsed JSON value or None (miss, parse error, or Redis down)."""
    try:
        r = await get_redis()
        raw = await r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — cache must never raise
        logger.warning("cache_read_failed", key=key, error=type(exc).__name__)
        return None


async def cache_set(key: str, value: Any, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Store JSON value with TTL; failures are logged and swallowed."""
    try:
        r = await get_redis()
        await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_write_failed", key=key, error=type(exc).__name__)
