"""
FLOODGUARD AI — Application entrypoint.

Owns the FastAPI app, the Socket.IO ASGI mount, the Redis -> Socket.IO event
bridge, and the startup/shutdown lifecycle for the ingest + forecast + alert
engines.

Everything degrades rather than crashes: if Redis or the database is down the
API still serves and the browser falls back to REST polling.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
import socketio
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import about, alerts, demo, flood_monitor, iot, scenarios
from app.core.config import get_settings
from app.core.database import Base, engine
from app.services.event_bridge import bridge
from app.services.seed import seed_reference_data
from app.services.websocket import sio

logger = structlog.get_logger()
settings = get_settings()



async def _ensure_postgis() -> None:
    """Geometry columns need the PostGIS extension to exist."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))


async def _verify_schema() -> None:
    """Confirm the Alembic-managed schema is present.

    Deliberately does NOT call create_all: Alembic is the single source of
    truth for schema, and a silent create_all would make the database and the
    migration history disagree.
    """
    from sqlalchemy import inspect as sa_inspect

    async with engine.connect() as conn:
        present = set(await conn.run_sync(lambda c: sa_inspect(c).get_table_names()))
    missing = set(Base.metadata.tables) - present
    if missing:
        logger.warning(
            "schema_incomplete_missing_tables",
            missing=sorted(missing),
            hint="run `alembic upgrade head` in backend/",
        )
    else:
        logger.info("schema_verified", tables=len(Base.metadata.tables))


async def _start_engines() -> None:
    """Start background engines, isolating failures so one bad engine cannot
    stop telemetry from being ingested."""
    from app.services.alert_engine import alert_engine
    from app.services.forecast_service import forecast_service

    starters = [
        ("forecast_service", forecast_service.start_auto_forecast),
        ("alert_engine", alert_engine.start),
    ]
    for name, start in starters:
        try:
            result = start()
            if asyncio.iscoroutine(result):
                await result
            logger.info("engine_started", engine=name)
        except Exception as exc:  # noqa: BLE001
            logger.error("engine_start_failed", engine=name, error=str(exc))


async def _stop_engines() -> None:
    from app.services.alert_engine import alert_engine
    from app.services.forecast_service import forecast_service

    for name, stop in (
        ("forecast_service", forecast_service.stop_auto_forecast),
        ("alert_engine", alert_engine.stop),
    ):
        try:
            result = stop()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            logger.warning("engine_stop_failed", engine=name, error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        await _ensure_postgis()
        await _verify_schema()
        await seed_reference_data()
    except Exception as exc:  # noqa: BLE001
        # A cold database must not stop the API from booting and explaining
        # itself via /health.
        logger.error("bootstrap_failed", error=str(exc))

    try:
        await bridge.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_bridge_unavailable", error=str(exc))

    await _start_engines()
    logger.info("floodguard_started", env=settings.app_env)
    try:
        yield
    finally:
        await _stop_engines()
        await bridge.stop()
        await engine.dispose()
        logger.info("floodguard_stopped")


app = FastAPI(
    title="FLOODGUARD AI",
    description=(
        "Flood monitoring and risk assessment for the Mithi River corridor "
        "(BKC to Kurla, Mumbai). Every response carries explicit data-source "
        "provenance; predicted layers are never presented as official flood zones."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, Any]:
    """Liveness only: the process is up and the event loop is turning.

    Deliberately touches no dependency. Render polls this every few seconds to
    decide whether to route traffic here, so it must not inherit the latency of
    a third-party API — `/health` below probes Open-Meteo over a 20 s httpx
    timeout, and a slow upstream would have the platform kill a perfectly
    healthy instance.

    Never raises.
    """
    return {"status": "ok", "service": "floodguard-ai", "version": "1.0.0"}


@app.get("/health", tags=["system"])
@app.get("/api/health", tags=["system"])
async def health() -> dict[str, Any]:
    """Liveness + dependency status. Never raises, so it always answers."""
    status: dict[str, Any] = {"status": "ok", "service": "floodguard-ai", "version": "1.0.0"}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["status"] = "degraded"
        status["database"] = f"error: {type(exc).__name__}"

    try:
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=3)
        await client.ping()
        await client.aclose()
        status["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["status"] = "degraded"
        status["redis"] = f"error: {type(exc).__name__}"

    status["ingest"] = "http"
    try:
        from app.integrations.weather.open_meteo import probe

        status["open_meteo"] = "ok" if await probe() else "unreachable"
    except Exception as exc:  # noqa: BLE001
        status["open_meteo"] = f"error: {type(exc).__name__}"
    return status


@app.get("/api", tags=["system"])
async def api_root() -> dict[str, Any]:
    return {
        "service": "FLOODGUARD AI",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "iot": "/api/iot",
            "monitoring": "/api/flood",
            "scenarios": "/api/scenarios",
            "alerts": "/api/alerts",
            "demo": "/api/demo",
            "about": "/api/about",
        },
    }


app.include_router(iot.router)
app.include_router(flood_monitor.router)
app.include_router(scenarios.router)
app.include_router(alerts.router)
app.include_router(demo.router)
app.include_router(about.router)


# Socket.IO mounted at /socket.io on the same port/ASGI app as the REST API.
socket_app = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")

# Re-export the combined app under the conventional name as well.
#
# Previously only `socket_app` carried the Socket.IO mount, so the intuitive
# `uvicorn app.main:app` started a server that answered REST but silently had
# no /socket.io route — the console then sat permanently in DEGRADED with no
# error to explain why. Both names now resolve to the same ASGI app, so either
# command serves REST *and* the realtime feed.
#
# Lifespan is unaffected: engineio.ASGIApp forwards the `lifespan` scope to
# `other_asgi_app` when no on_startup/on_shutdown hooks are supplied (see
# engineio/async_drivers/asgi.py), so the FastAPI lifespan above still runs
# PostGIS setup, the event bridge, and the background engines.
app = socket_app
