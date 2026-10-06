"""Redis -> Socket.IO bridge.

Services publish domain events to Redis so that MQTT ingestion, replay and
HTTP ingestion all share one path to the browser. This task turns those into
Socket.IO frames.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.config import get_settings
from app.services.websocket import GLOBAL_ROOM, sio

logger = structlog.get_logger()
settings = get_settings()

REALTIME_CHANNEL = "floodguard:realtime"


class EventBridge:
    """Relays Redis pub/sub messages onto the Socket.IO global room."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        assert self._client is not None
        pubsub = self._client.pubsub()
        await pubsub.subscribe(REALTIME_CHANNEL)
        logger.info("event_bridge_subscribed", channel=REALTIME_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                await self._dispatch(message.get("data"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("event_bridge_failed", error=str(exc))

    async def _dispatch(self, raw: Any) -> None:
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("event_bridge_bad_json")
            return

        name = event.get("event")
        if not name:
            return
        # The envelope is already the Socket.IO payload shape used by
        # app.services.websocket emitters, so forward it verbatim.
        await sio.emit(name, event, room=GLOBAL_ROOM)

        node_code = (event.get("payload") or {}).get("node_code")
        if node_code:
            await sio.emit(name, event, room=f"node_{node_code}")

    async def start(self) -> None:
        # A dedicated connection: a client in subscribe mode cannot issue
        # other commands, so it must not be the shared cache client.
        self._client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await self._client.ping()
        self._task = asyncio.create_task(self._run(), name="event-bridge")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None


bridge = EventBridge()
