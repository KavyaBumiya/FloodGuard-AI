"""
FLOODGUARD AI — Forecast Service
=================================
Loads trained ML models and produces 15, 30, 60 minute water level predictions.
Uses Random Forest as primary model with persistence baseline comparison.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import structlog
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    Forecast,
    ModelRegistry,
    SourceType,
)
from app.services.flood_risk import assess_forecast_risk, assess_current_risk
from app.integrations import cache

logger = structlog.get_logger()

# Model feature configuration
FEATURE_WINDOW_SIZE = 12  # Number of past readings to use (12 * 5min = 1 hour)
LAGS = [1, 2, 3, 6]  # Lag indices (1=5min ago, 6=30min ago)
HORIZONS = [15, 30, 60]  # Forecast horizons in minutes
SAMPLING_INTERVAL_MINUTES = 5  # Assumed 5-minute sampling

# Minimum readings needed for forecast
MIN_READINGS_FOR_FORECAST = max(LAGS) + 2


class ForecastService:
    """
    Manages ML model loading and forecast generation for flood prediction.
    
    Models are loaded at startup and cached. Forecasts are generated
    automatically when new telemetry arrives.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._models: dict[int, Any] = {}  # horizon_minutes -> model
        self._bundles: dict[int, dict] = {}  # horizon_minutes -> artefact metadata
        self._model_registry: dict[int, ModelRegistry] = {}
        self._loaded = False
        self._task: Optional[asyncio.Task] | None = None
        self._running = False

    async def load_models(self) -> None:
        """Load all trained models from disk."""
        if self._loaded:
            return

        # Repo-relative path: resolving against the process CWD silently missed
        # the artefacts whenever uvicorn was started outside backend/.
        model_dir = self.settings.resolve_data_path(self.settings.flood_model_dir)

        for horizon in HORIZONS:
            model_path = model_dir / getattr(self.settings, f"flood_model_{horizon}min")

            if model_path.exists():
                try:
                    loaded = joblib.load(model_path)
                    # Training writes a bundle (model + provenance + metrics);
                    # older artefacts may be a bare estimator.
                    if isinstance(loaded, dict) and "model" in loaded:
                        self._models[horizon] = loaded["model"]
                        self._bundles[horizon] = {
                            k: v for k, v in loaded.items() if k != "model"
                        }
                        logger.info(
                            "forecast_model_loaded",
                            horizon=horizon,
                            algorithm=loaded.get("algorithm", "unknown"),
                        )
                    else:
                        self._models[horizon] = loaded
                        self._bundles[horizon] = {"algorithm": type(loaded).__name__}
                        logger.info("forecast_model_loaded", horizon=horizon)
                except Exception as exc:
                    logger.error("forecast_model_load_failed", horizon=horizon, error=str(exc))
                    self._models[horizon] = PersistenceModel()
            else:
                logger.warning("forecast_model_not_found", horizon=horizon, path=str(model_path))
                # Create a simple persistence model as fallback
                self._models[horizon] = PersistenceModel()
                self._bundles[horizon] = {
                    "algorithm": "persistence",
                    "note": "no trained artefact found; falling back to persistence",
                }

        # Load model registry from database
        await self._load_model_registry()
        self._loaded = True

    async def _load_model_registry(self) -> None:
        """Load active model metadata from database."""
        async with AsyncSessionLocal() as db:
            for horizon in HORIZONS:
                result = await db.execute(
                    select(ModelRegistry)
                    .where(
                        ModelRegistry.horizon_minutes == horizon,
                        ModelRegistry.is_active == True,
                    )
                    .order_by(ModelRegistry.trained_at.desc())
                    .limit(1)
                )
                registry = result.scalar_one_or_none()
                if registry:
                    self._model_registry[horizon] = registry

    async def start_auto_forecast(self) -> None:
        """Start background task for automatic forecast generation."""
        if self._running:
            return
        
        await self.load_models()
        self._running = True
        self._task = asyncio.create_task(self._auto_forecast_loop())
        logger.info("auto_forecast_started")

    async def stop_auto_forecast(self) -> None:
        """Stop background forecast task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("auto_forecast_stopped")

    async def _auto_forecast_loop(self) -> None:
        """Periodically generate forecasts for all active nodes."""
        while self._running:
            try:
                await self.generate_all_forecasts()
            except Exception as exc:
                logger.error("auto_forecast_error", error=str(exc))
            
            await asyncio.sleep(60)  # Run every minute

    async def generate_all_forecasts(self, source: str = "live") -> dict[int, list[Forecast]]:
        """Generate Mithi forecasts from recent real physical inputs only."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SensorNode).where(
                    SensorNode.node_type == "physical",
                    SensorNode.is_active == True,
                )
            )
            nodes = result.scalars().all()

        forecasts_by_node = {}
        for node in nodes:
            async with AsyncSessionLocal() as db:
                latest = (
                    await db.execute(
                        select(TelemetryReading)
                        .where(
                            TelemetryReading.node_id == node.id,
                            TelemetryReading.source == SourceType.IOT.value,
                        )
                        .order_by(desc(TelemetryReading.recorded_at))
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if latest is None:
                continue
            forecasts = await self.generate_mithi_forecasts(
                node.id,
                self.map_demo_level(latest.water_level_cm),
                latest.rainfall_mm_hr,
                latest.recorded_at,
                source="iot-demo",
            )
            if forecasts:
                forecasts_by_node[node.id] = forecasts

        return forecasts_by_node

    async def generate_forecasts_for_nodes(
        self, node_ids: list[int], source: str = "iot-demo"
    ) -> dict[int, list[Forecast]]:
        """Generate forecasts for a selected set of Mithi virtual nodes."""
        forecasts_by_node: dict[int, list[Forecast]] = {}
        for node_id in node_ids:
            forecasts = await self.generate_forecasts_for_node(node_id, source)
            if forecasts:
                forecasts_by_node[node_id] = forecasts
        return forecasts_by_node

    def map_demo_level(self, physical_level_cm: float) -> float:
        """Map Vadodara demo-channel input to the Mithi simulation level."""
        minimum = self.settings.demo_input_physical_min_cm
        maximum = self.settings.demo_input_physical_max_cm
        if maximum <= minimum:
            raise ValueError("DEMO_INPUT_PHYSICAL_MAX_CM must exceed MIN_CM")
        fraction = max(0.0, min(1.0, (physical_level_cm - minimum) / (maximum - minimum)))
        return self.settings.demo_input_mithi_min_cm + fraction * (
            self.settings.demo_input_mithi_max_cm - self.settings.demo_input_mithi_min_cm
        )

    async def generate_mithi_forecasts(
        self,
        node_id: int,
        current_level_cm: float,
        rainfall_mm_hr: float,
        observed_at: datetime,
        source: str = "iot-demo",
    ) -> list[Forecast]:
        """Forecast the mapped Mithi state without creating virtual readings."""
        async with AsyncSessionLocal() as db:
            history_result = await db.execute(
                select(TelemetryReading)
                .where(
                    TelemetryReading.node_id == node_id,
                    TelemetryReading.source == SourceType.IOT.value,
                )
                .order_by(desc(TelemetryReading.recorded_at))
                .limit(FEATURE_WINDOW_SIZE)
            )
            history = list(reversed(history_result.scalars().all()))
            if len(history) < MIN_READINGS_FOR_FORECAST:
                logger.info(
                    "insufficient_mapped_iot_history",
                    node_id=node_id,
                    count=len(history),
                    required=MIN_READINGS_FOR_FORECAST,
                )
                return []
            levels = [self.map_demo_level(item.water_level_cm) for item in history]
            levels.append(current_level_cm)
            rainfall = [item.rainfall_mm_hr for item in history] + [rainfall_mm_hr]
            features = {
                "current_level": levels[-1],
                "prev_level_1": levels[-2] if len(levels) > 1 else levels[-1],
                "prev_level_2": levels[-3] if len(levels) > 2 else levels[-1],
                "prev_level_3": levels[-4] if len(levels) > 3 else levels[-1],
                "prev_level_6": levels[-7] if len(levels) > 6 else levels[-1],
                "current_rainfall": rainfall[-1],
                "avg_rainfall_1h": float(np.mean(rainfall[-12:])),
                "rainfall_trend": rainfall[-1] - rainfall[-2] if len(rainfall) > 1 else 0.0,
                "rise_rate": levels[-1] - levels[-2] if len(levels) > 1 else 0.0,
                "level_std": float(np.std(levels[-6:])) if len(levels) >= 6 else 0.0,
            }
            forecasts: list[Forecast] = []
            for horizon in HORIZONS:
                model = self._models.get(horizon)
                if model is None:
                    continue
                try:
                    predicted = float(model.predict([list(features.values())])[0])
                except Exception as exc:
                    logger.error("mithi_forecast_prediction_failed", horizon=horizon, error=str(exc))
                    predicted = current_level_cm
                made_at = datetime.now(timezone.utc)
                forecast = Forecast(
                    node_id=node_id,
                    model_id=self._model_registry[horizon].id if horizon in self._model_registry else None,
                    made_at=made_at,
                    target_time=made_at + timedelta(minutes=horizon),
                    horizon_minutes=horizon,
                    predicted_level_cm=max(0.0, round(predicted, 1)),
                    lower_bound_cm=round(max(0.0, predicted - 10.0), 1),
                    upper_bound_cm=round(predicted + 10.0, 1),
                    input_source=source,
                    status="ok",
                )
                db.add(forecast)
                forecasts.append(forecast)
            await db.commit()
            for forecast in forecasts:
                await self._publish_forecast_event(forecast, db)
            return forecasts

    async def generate_forecasts_for_node(
        self, node_id: int, source: str = "live"
    ) -> list[Forecast]:
        """Generate 15, 30, 60 minute forecasts for a specific node."""
        async with AsyncSessionLocal() as db:
            # Get recent readings
            readings = await self._get_recent_readings(db, node_id, FEATURE_WINDOW_SIZE)
            
            if len(readings) < MIN_READINGS_FOR_FORECAST:
                logger.warning(
                    "insufficient_readings_for_forecast",
                    node_id=node_id,
                    count=len(readings),
                    required=MIN_READINGS_FOR_FORECAST,
                )
                return await self._create_insufficient_data_forecasts(db, node_id, source)

            # Build features
            features = self._build_features(readings)
            
            # Get upstream node reading if available
            upstream_level = await self._get_upstream_level(db, node_id)
            if upstream_level is not None:
                features["upstream_level_cm"] = upstream_level
            else:
                features["upstream_level_cm"] = 0.0

            forecasts = []

            for horizon in HORIZONS:
                model = self._models.get(horizon)
                if model is None:
                    continue

                try:
                    predicted_level = float(model.predict([list(features.values())])[0])
                except Exception as exc:
                    logger.error("forecast_prediction_failed", horizon=horizon, error=str(exc))
                    predicted_level = readings[0].water_level_cm  # Persistence fallback

                # Calculate confidence bounds (simplified)
                lower_bound = predicted_level - 10.0
                upper_bound = predicted_level + 10.0

                made_at = datetime.now(timezone.utc)
                target_time = made_at + timedelta(minutes=horizon)

                forecast = Forecast(
                    node_id=node_id,
                    model_id=(
                        self._model_registry[horizon].id
                        if horizon in self._model_registry and self._model_registry[horizon] is not None
                        else None
                    ),
                    made_at=made_at,
                    target_time=target_time,
                    horizon_minutes=horizon,
                    predicted_level_cm=max(0, round(predicted_level, 1)),
                    lower_bound_cm=round(lower_bound, 1),
                    upper_bound_cm=round(upper_bound, 1),
                    input_source=source,
                    status="ok",
                )
                db.add(forecast)
                forecasts.append(forecast)

                # Cache for real-time access
                await self._cache_forecast(node_id, horizon, forecast)

            await db.commit()
            
            # Publish forecast events
            for forecast in forecasts:
                await self._publish_forecast_event(forecast, db)

            return forecasts

    async def _get_recent_readings(
        self, db: AsyncSession, node_id: int, limit: int
    ) -> list[TelemetryReading]:
        """Get recent telemetry readings for feature building."""
        result = await db.execute(
            select(TelemetryReading)
            .where(
                TelemetryReading.node_id == node_id,
                TelemetryReading.source.in_([SourceType.IOT.value, SourceType.SIMULATED.value]),
            )
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(limit)
        )
        readings = result.scalars().all()
        return list(reversed(readings))  # Chronological order

    def _build_features(self, readings: list[TelemetryReading]) -> dict[str, float]:
        """Build feature vector from recent readings."""
        levels = [r.water_level_cm for r in readings]
        rainfall = [r.rainfall_mm_hr for r in readings]
        
        features = {
            "current_level": levels[-1],
            "prev_level_1": levels[-2] if len(levels) > 1 else levels[-1],
            "prev_level_2": levels[-3] if len(levels) > 2 else levels[-1],
            "prev_level_3": levels[-4] if len(levels) > 3 else levels[-1],
            "prev_level_6": levels[-7] if len(levels) > 6 else levels[-1],
            "current_rainfall": rainfall[-1],
            "avg_rainfall_1h": np.mean(rainfall[-12:]) if len(rainfall) >= 12 else np.mean(rainfall),
            "rainfall_trend": rainfall[-1] - rainfall[-2] if len(rainfall) > 1 else 0.0,
            "rise_rate": levels[-1] - levels[-2] if len(levels) > 1 else 0.0,
            "level_std": np.std(levels[-6:]) if len(levels) >= 6 else 0.0,
        }
        
        return features

    async def _get_upstream_level(self, db: AsyncSession, node_id: int) -> Optional[float]:
        """Get latest water level from upstream node."""
        from app.models.floodguard import NodeRelationship
        
        result = await db.execute(
            select(NodeRelationship).where(NodeRelationship.downstream_node_id == node_id)
        )
        relationship = result.scalar_one_or_none()
        
        if not relationship:
            return None

        upstream_result = await db.execute(
            select(TelemetryReading.water_level_cm)
            .where(TelemetryReading.node_id == relationship.upstream_node_id)
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(1)
        )
        return upstream_result.scalar_one_or_none()

    async def _create_insufficient_data_forecasts(
        self, db: AsyncSession, node_id: int, source: str
    ) -> list[Forecast]:
        """Create forecast records with 'insufficient_data' status."""
        # Get latest reading for persistence baseline
        result = await db.execute(
            select(TelemetryReading)
            .where(TelemetryReading.node_id == node_id)
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        
        if not latest:
            return []

        forecasts = []
        made_at = datetime.now(timezone.utc)
        baseline_level = latest.water_level_cm

        for horizon in HORIZONS:
            forecast = Forecast(
                node_id=node_id,
                model_id=None,
                made_at=made_at,
                target_time=made_at + timedelta(minutes=horizon),
                horizon_minutes=horizon,
                predicted_level_cm=baseline_level,
                lower_bound_cm=baseline_level - 5,
                upper_bound_cm=baseline_level + 5,
                input_source=source,
                status="insufficient_data",
            )
            db.add(forecast)
            forecasts.append(forecast)

        await db.commit()
        return forecasts

    async def _cache_forecast(self, node_id: int, horizon: int, forecast: Forecast) -> None:
        """Cache forecast in Redis for fast access."""
        try:
            redis = await cache.get_redis()
            key = f"forecast:{node_id}:{horizon}"
            await redis.setex(key, 300, json.dumps({
                "predicted_level_cm": forecast.predicted_level_cm,
                "lower_bound_cm": forecast.lower_bound_cm,
                "upper_bound_cm": forecast.upper_bound_cm,
                "horizon_minutes": forecast.horizon_minutes,
                "made_at": forecast.made_at.isoformat(),
                "target_time": forecast.target_time.isoformat(),
                "status": forecast.status,
            }))
        except Exception as exc:
            logger.warning("forecast_cache_failed", error=str(exc))

    async def _publish_forecast_event(self, forecast: Forecast, db: AsyncSession) -> None:
        """Publish forecast event for real-time updates."""
        try:
            event = {
                "event": "flood.forecast.updated",
                "payload": {
                    "node_id": forecast.node_id,
                    "horizon_minutes": forecast.horizon_minutes,
                    "predicted_level_cm": forecast.predicted_level_cm,
                    "lower_bound_cm": forecast.lower_bound_cm,
                    "upper_bound_cm": forecast.upper_bound_cm,
                    "target_time": forecast.target_time.isoformat(),
                    "status": forecast.status,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "forecast_service",
            }
            from app.services.websocket import publish_realtime_event

            await publish_realtime_event(event)
        except Exception as exc:
            logger.warning("forecast_publish_failed", error=str(exc))

    async def get_cached_forecast(self, node_id: int, horizon: int) -> Optional[dict]:
        """Get cached forecast from Redis."""
        try:
            redis = await cache.get_redis()
            key = f"forecast:{node_id}:{horizon}"
            data = await redis.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    def get_model_info(self, horizon: int) -> dict:
        """Get model metadata for a horizon."""
        registry = self._model_registry.get(horizon)
        if registry:
            return {
                "model_name": registry.model_name,
                "version": registry.version,
                "algorithm": registry.algorithm,
                "mae": registry.mae,
                "rmse": registry.rmse,
                "baseline_mae": registry.baseline_mae,
                "baseline_rmse": registry.baseline_rmse,
                "trained_at": registry.trained_at.isoformat() if registry.trained_at else None,
                "training_data_description": registry.training_data_description,
            }
        # No DB registry row: fall back to whatever the artefact itself declared
        # so the UI can still state the algorithm and honest training source.
        bundle = self._bundles.get(horizon)
        if bundle:
            metrics = bundle.get("metrics") or {}
            return {
                "model_name": bundle.get("algorithm", "unknown"),
                "version": bundle.get("trained_at", "unversioned"),
                "algorithm": bundle.get("algorithm"),
                "mae": metrics.get("mae"),
                "rmse": metrics.get("rmse"),
                "baseline_mae": metrics.get("baseline_mae"),
                "baseline_rmse": metrics.get("baseline_rmse"),
                "trained_at": bundle.get("trained_at"),
                "training_data_description": bundle.get("level_source"),
                "registered_in_db": False,
            }
        return {"model_name": "persistence", "version": "baseline", "registered_in_db": False}


class PersistenceModel:
    """Simple persistence baseline: future level = current level."""
    
    def predict(self, X):
        # X contains features, current_level is first feature
        return np.array([x[0] for x in X])


# Global instance
forecast_service = ForecastService()