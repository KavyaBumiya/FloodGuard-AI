"""
FLOODGUARD AI — About Data API
===============================
Data source transparency: every dataset used, its provider, resolution,
limitations, and licensing. Powers the "About This Data" panel.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models.floodguard import DataSource
from app.core.config import get_settings
from app.services import flood_risk

router = APIRouter(prefix="/api/about", tags=["about"])


@router.get("/data-sources")
async def get_data_sources(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all registered data sources with metadata."""
    result = await db.execute(
        select(DataSource).order_by(DataSource.category, DataSource.name)
    )
    sources = result.scalars().all()

    return {
        "data_sources": [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "provider": s.provider,
                "resolution": s.resolution,
                "licence_note": s.licence_note,
                "limitation": s.limitation,
                "last_updated": s.last_updated.isoformat() if s.last_updated else None,
            }
            for s in sources
        ]
    }


@router.get("/model-info")
async def get_model_info(
    _: dict = Depends(get_current_user),
):
    """Get ML model information for all horizons."""
    return {
        "models": {
            "15min": forecast_service.get_model_info(15),
            "30min": forecast_service.get_model_info(30),
            "60min": forecast_service.get_model_info(60),
        },
        "persistence_baseline": {
            "description": "Future water level equals current level (no change)",
            "note": "All ML models are evaluated against this baseline",
        },
    }


@router.get("/assumptions")
async def get_assumptions(
    _: dict = Depends(get_current_user),
):
    """Get model assumptions and limitations."""
    settings = get_settings()
    
    return {
        "risk_scoring": {
            "weights": {
                "water_level": flood_risk.WEIGHT_WATER_LEVEL,
                "rise_rate": flood_risk.WEIGHT_RISE_RATE,
                "rainfall": flood_risk.WEIGHT_RAINFALL,
                "soil_saturation": flood_risk.WEIGHT_SOIL,
            },
            "thresholds_cm": {
                "low": flood_risk.WATER_LEVEL_LOW_CM,
                "moderate": flood_risk.WATER_LEVEL_MODERATE_CM,
                "high": flood_risk.WATER_LEVEL_HIGH_CM,
                "severe": flood_risk.WATER_LEVEL_SEVERE_CM,
            },
            "note": "Weights and thresholds are prototype assumptions requiring local calibration. Not scientifically validated.",
        },
        "flood_simulation": {
            "method": "elevation_based_inundation",
            "description": "Simplified raster inundation model. Not a hydraulic model. Does not solve momentum or mass conservation. No flow velocity calculation.",
            "grid_resolution_m": settings.flood_grid_resolution_m,
            "connectivity_buffer_m": 50,
            "max_depth_m": 5.0,
            "depth_risk_thresholds_m": {
                "low": 0.1,
                "moderate": 0.3,
                "high": 0.6,
                "severe": 1.0,
            },
            "limitations": [
                "Accuracy limited by elevation data resolution (~30m SRTM)",
                "Cannot represent individual streets, drains, or building thresholds",
                "Does not model drainage network hydraulics",
                "Assumes instantaneous water surface equilibration",
            ],
        },
        "forecast": {
            "horizons_minutes": [15, 30, 60],
            "sampling_interval_minutes": 5,
            "feature_window_readings": 12,
            "lags_used": [1, 2, 3, 6],
            "model": "Random Forest Regressor (primary)",
            "note": "A few minutes of prototype data cannot establish real-world accuracy. Use for preparedness and exploration, not life-safety decisions.",
        },
        "data_priority": {
            "current_rainfall": ["ESP32 (live)", "Open-Meteo (forecast)", "IMD (historical)"],
            "current_water_level": ["ESP32 (live)"],
            "terrain": ["Local SRTM GeoTIFF", "Cesium Ion (online fallback)"],
            "buildings": ["OpenStreetMap (Overpass)"],
        },
        "source_labels": {
            "LIVE HARDWARE": "ESP32 physical sensor",
            "SIMULATED": "Virtual sensor network with documented propagation rules",
            "HISTORICAL / REFERENCE": "IMD gridded rainfall, SRTM terrain",
            "FORECAST": "Open-Meteo weather forecast",
            "TERRAIN / OSM": "SRTM elevation, OpenStreetMap features",
            "PREDICTED": "FLOODGUARD model output",
        },
    }


@router.get("/study-area")
async def get_study_area(
    _: dict = Depends(get_current_user),
):
    """Get study area configuration."""
    settings = get_settings()
    
    return {
        "name": "Mithi River Corridor - BKC to Kurla",
        "bounds": {
            "south": settings.study_area_min_lat,
            "west": settings.study_area_min_lon,
            "north": settings.study_area_max_lat,
            "east": settings.study_area_max_lon,
        },
        "representative_point": {
            "lat": settings.study_area_lat,
            "lng": settings.study_area_lon,
        },
        "grid_resolution_m": settings.flood_grid_resolution_m,
        "description": "1-2 km section of Mithi River around BKC and Kurla region, Mumbai",
    }


@router.get("/honesty-note")
async def get_honesty_note(
    _: dict = Depends(get_current_user),
):
    """Get the honesty/disclaimer note for the UI."""
    return {
        "note": (
            "Prototype readings and scenario outputs are clearly labeled. "
            "A few minutes of data cannot establish real-world model accuracy. "
            "Use this interface for preparedness and exploration, not as a life-safety system."
        ),
        "disclaimers": [
            "Flood simulation is a simplified elevation-based inundation estimate, not a hydraulic model.",
            "Elevation data resolution (~30m) limits street-level accuracy.",
            "Risk weights and thresholds are starting assumptions requiring local calibration.",
            "Satellite rainfall data (IMD, GPM) is too coarse (~10km) for street-level rainfall; local rain gauge is primary live source.",
            "No public live Mithi River water-level API is assumed to exist.",
            "Building heights from OSM are often missing; estimated heights are clearly labeled.",
            "Generated flood extents are PREDICTED, not OFFICIAL FLOOD ZONES.",
        ],
    }

# Need to import forecast_service
from app.services.forecast_service import forecast_service