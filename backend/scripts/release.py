"""Render release command: migrate the database, then optionally load OSM.

Render runs `releaseCommand` once before a deploy promotes a new image, so
migrations never race the N instances of the previous version still serving
traffic. Run it the same way locally:

    python -m scripts.release

Steps, in order, each skippable:

1. Wait for the database to accept connections (managed Postgres is often
   still starting when the release command fires).
2. `CREATE EXTENSION IF NOT EXISTS postgis` — Render's Postgres has PostGIS
   available but does not enable it per-database by default, and every
   geometry column in the schema needs it.
3. `alembic upgrade head`.
4. Optionally `load_osm_layers` when `RELEASE_LOAD_OSM=true`. Off by default
   because it inserts ~8,900 rows and only needs to happen once per database;
   switching it on against an already-populated database is wasted deploy time.

Every step is idempotent, so re-running a failed release is safe.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Render's release container has a short default; give migrations room.
os.environ.setdefault("ALEMBIC_CONNECT_TIMEOUT", "60")

WAIT_ATTEMPTS = int(os.environ.get("RELEASE_DB_WAIT_ATTEMPTS", "30"))
WAIT_INTERVAL = float(os.environ.get("RELEASE_DB_WAIT_INTERVAL", "2.0"))
LOAD_OSM = os.environ.get("RELEASE_LOAD_OSM", "false").strip().lower() in {"1", "true", "yes"}


def log(message: str) -> None:
    print(f"[release] {message}", flush=True)


async def wait_for_database() -> None:
    """Block until the database answers, rather than crashing the deploy.

    A managed Postgres that is still booting rejects the first few connections.
    Exiting immediately would mark the deploy failed and leave the previous
    version running, which is a far worse outcome than waiting.
    """
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.core.database import engine

    settings = get_settings()
    log(f"waiting for {settings.database_host} (up to {WAIT_ATTEMPTS * WAIT_INTERVAL:.0f}s)")

    last: Exception | None = None
    for attempt in range(1, WAIT_ATTEMPTS + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            log("database is reachable")
            return
        except Exception as exc:  # noqa: BLE001 — any driver error means "not yet"
            last = exc
            log(f"  attempt {attempt}/{WAIT_ATTEMPTS}: {type(exc).__name__}: {exc}")
            await asyncio.sleep(WAIT_INTERVAL)

    raise SystemExit(f"[release] database never became reachable: {last}")


async def ensure_postgis() -> None:
    from sqlalchemy import text

    from app.core.database import engine

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    log("postgis extension present")


def run_alembic_upgrade() -> None:
    """Apply migrations.

    Must be called from a worker thread, NOT directly inside the event loop:
    alembic/env.py ends with `asyncio.run(run_migrations_online())`, and
    asyncio.run() refuses to nest inside a running loop. Calling this directly
    from main() raises "asyncio.run() cannot be called from a running event
    loop" and every deploy fails.
    """
    from alembic import command
    from alembic.config import Config

    # alembic.ini lives in backend/; the URL is read from Settings inside
    # alembic/env.py, so alembic.ini's own placeholder is never used.
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    log("alembic upgrade head complete")


async def _upgrade() -> None:
    await asyncio.to_thread(run_alembic_upgrade)


async def load_osm() -> None:
    from scripts.load_osm_layers import load_buildings, load_roads

    from app.core.database import AsyncSessionLocal
    from app.core.config import get_settings

    settings = get_settings()
    raster = settings.resolve_data_path(settings.terrain_raster_path)
    log(f"loading OSM layers (DEM sampler path: {raster.name})")

    from scripts.load_osm_layers import DemSampler  # noqa: PLC0415 — needs settings first

    dem = DemSampler(raster)
    try:
        b_new, b_skip = await load_buildings(force=False, dem=dem)
        r_new, r_skip = await load_roads(force=False)
        async with AsyncSessionLocal() as db:
            from sqlalchemy import func, select

            from app.models.floodguard import Building, Road

            b_total = (await db.execute(select(func.count()).select_from(Building))).scalar_one()
            r_total = (await db.execute(select(func.count()).select_from(Road))).scalar_one()
        log(
            f"buildings +{b_new} inserted, {b_skip} skipped -> {b_total} total; "
            f"roads +{r_new} inserted, {r_skip} skipped -> {r_total} total"
        )
        if not b_total or not r_total:
            log("WARNING: an OSM layer is empty — infrastructure impact will report zero")
    finally:
        dem.close()


async def main() -> int:
    log(f"app_env={os.environ.get('APP_ENV', 'development')}")
    await wait_for_database()
    await ensure_postgis()
    await _upgrade()
    if LOAD_OSM:
        await load_osm()
    else:
        log("skipping OSM load (set RELEASE_LOAD_OSM=true to include it)")
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))