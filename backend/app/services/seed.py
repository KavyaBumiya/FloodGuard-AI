"""Idempotent reference-data seeding for a fresh database."""
from __future__ import annotations

import structlog

from app.core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


async def seed_reference_data() -> None:
    """Seed the single study-area row and the data-source register.

    Idempotent: re-running does not duplicate rows. Every DataSource row
    carries an explicit limitation so the UI can never present a dataset as
    more authoritative than it is.
    """
    from geoalchemy2 import WKTElement
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.floodguard import DataSource, StudyArea

    sources = (
        {
            "name": "SRTM / Copernicus GLO-30 Terrain",
            "category": "terrain",
            "provider": "OpenTopography (SRTMGL3) / Copernicus GLO-30 via AWS Open Data",
            "resolution": "30 m",
            "licence_note": "SRTM is NASA/USGS public domain; Copernicus GLO-30 is free for all uses.",
            "limitation": (
                "Surface elevation at 30 m. Cannot resolve bunds, kerbs or channel "
                "roughness; inundation depths inherit this error."
            ),
        },
        {
            "name": "IMD Gridded Rainfall",
            "category": "historical_reference",
            "provider": "India Meteorological Department",
            "resolution": "0.25 deg (~25 km) daily",
            "licence_note": "IMD data policy applies.",
            "limitation": (
                "~25 km grid. Cannot represent street-level rainfall. Historical "
                "context and model training only, never a live sensor reading."
            ),
        },
        {
            "name": "OpenStreetMap Features",
            "category": "terrain_osm",
            "provider": "OpenStreetMap contributors (ODbL)",
            "resolution": "Vector",
            "licence_note": "(c) OpenStreetMap contributors, ODbL 1.0.",
            "limitation": (
                "Building heights are largely untagged and are ESTIMATED from "
                "building type and level counts; treat as indicative only."
            ),
        },
        {
            "name": "Open-Meteo Weather Forecast",
            "category": "forecast",
            "provider": "Open-Meteo",
            "resolution": "Hourly, ~9 km ensemble",
            "licence_note": "Free for non-commercial use under CC BY 4.0.",
            "limitation": (
                "FORECAST only. Never a substitute for an in-channel ESP32 reading; "
                "a forecast that disagrees with hardware does not override it."
            ),
        },
        {
            "name": "ESP32 Field Sensors",
            "category": "live_hardware",
            "provider": "Deployed ESP32 + ultrasonic sensors",
            "resolution": "Per-node, ~5-30 s cadence",
            "licence_note": "Own deployment.",
            "limitation": (
                "Ultrasonic sensors can foam-fail in fast turbid flow; readings are "
                "range-checked and rejected outliers are logged, not dropped."
            ),
        },
    )

    # These geometries are populated from the OSM extract at bootstrap time by
    # the data-provisioning step, so only the boundary is seeded here.
    ring = (
        f"POLYGON(({settings.study_area_min_lon} {settings.study_area_min_lat}, "
        f"{settings.study_area_max_lon} {settings.study_area_min_lat}, "
        f"{settings.study_area_max_lon} {settings.study_area_max_lat}, "
        f"{settings.study_area_min_lon} {settings.study_area_max_lat}, "
        f"{settings.study_area_min_lon} {settings.study_area_min_lat}))"
    )

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(StudyArea.id).limit(1))
        if existing.scalar_one_or_none() is None:
            db.add(
                StudyArea(
                    name="Mithi River — BKC to Kurla, Mumbai",
                    min_lat=settings.study_area_min_lat,
                    min_lon=settings.study_area_min_lon,
                    max_lat=settings.study_area_max_lat,
                    max_lon=settings.study_area_max_lon,
                    boundary=WKTElement(ring, srid=4326),
                    grid_cell_size_m=settings.flood_grid_resolution_m,
                )
            )

        for spec in sources:
            found = await db.execute(
                select(DataSource.id).where(DataSource.name == spec["name"])
            )
            if found.scalar_one_or_none() is None:
                db.add(DataSource(**spec))

        await db.commit()
    logger.info("seeded_reference_data", study_area=True, data_sources=len(sources))
