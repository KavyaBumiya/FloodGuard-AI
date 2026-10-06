"""
FLOODGUARD AI — Socket.IO Server and Event Emitters
====================================================
Real-time event types for the flood monitoring dashboard.

Event types:
- flood.sensor.updated      : New telemetry reading with risk assessment
- flood.forecast.updated    : New forecast for a node/horizon
- flood.layer.updated       : New flood simulation layer (depth/risk/boundary)
- flood.alert.updated       : Alert created/updated/cleared/acknowledged
- flood.mode.changed        : System mode change (live/replay/scenario)
- flood.scenario.updated    : Scenario status update (running/completed/failed)
- flood.risk.updated        : Risk assessment update
"""
from __future__ import annotations

import json

import socketio
import structlog

from app.core.config import get_settings

logger = structlog.get_logger()

# Room every emitter targets. Declared here and imported elsewhere so the
# bridge and the emitters cannot drift apart on the name.
GLOBAL_ROOM = "global_feed"

REALTIME_CHANNEL = "floodguard:realtime"


async def publish_realtime_event(event: dict) -> None:
    """Single Redis publish path for every service emitting a domain event."""
    from app.integrations import cache

    redis = await cache.get_redis()
    await redis.publish(REALTIME_CHANNEL, json.dumps(event))

# Async Socket.IO server
_settings = get_settings()
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_settings.is_origin_allowed,
    logger=False,
    engineio_logger=False,
)


# ── Connection lifecycle ──────────────────────────────────────

@sio.event
async def connect(sid, environ, auth):
    logger.info("WebSocket client connected", sid=sid)
    # Auto-join the global feed. Every emitter targets room "global_feed", so a
    # client that never sent an explicit `subscribe` would silently receive
    # nothing and look like a broken live feed. Per-node rooms stay opt-in via
    # the `subscribe` event below.
    await sio.enter_room(sid, GLOBAL_ROOM)
    await sio.emit(
        "connected",
        {
            "message": "Connected to FLOODGUARD AI live feed",
            "room": GLOBAL_ROOM,
            "hint": "emit 'subscribe' with { room: 'node_<node_code>' } for per-node events",
        },
        to=sid,
    )


@sio.event
async def disconnect(sid):
    logger.info("WebSocket client disconnected", sid=sid)


@sio.event
async def subscribe(sid, data):
    """Client subscribes to a room: { room: 'global_feed' | 'node_<node_code>' }"""
    room = data.get("room", GLOBAL_ROOM)
    await sio.enter_room(sid, room)
    logger.info("Client subscribed to room", sid=sid, room=room)
    await sio.emit("subscribed", {"room": room}, to=sid)


@sio.event
async def unsubscribe(sid, data):
    room = data.get("room", "global_feed")
    await sio.leave_room(sid, room)
