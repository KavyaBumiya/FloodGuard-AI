"""
FLOODGUARD AI — Database Models
=================================
Models for the Mithi River flood monitoring system (BKC → Kurla corridor).
All timestamps stored in UTC. Geometry uses SRID 4326.
"""
import uuid
from datetime import datetime
from enum import Enum

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SourceType(str, Enum):
    IOT = "iot"
    SIMULATED = "simulated"
    REPLAY = "replay"
    SCENARIO = "scenario"


class NodeType(str, Enum):
    PHYSICAL = "physical"
    VIRTUAL = "virtual"


class NodeRole(str, Enum):
    UPSTREAM = "upstream"
    MIDSTREAM = "midstream"
    DOWNSTREAM = "downstream"


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SEVERE = "severe"


class AlertType(str, Enum):
    THRESHOLD = "threshold"
    PREDICTED_RISK = "predicted_risk"
    RAPID_RISE = "rapid_rise"
    COMMUNICATION_FAILURE = "communication_failure"
    SENSOR_FAULT = "sensor_fault"


class AlertSeverity(str, Enum):
    INFORMATION = "information"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class DataMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    SCENARIO = "scenario"


class StudyArea(Base):
    """
    Single corridor definition for the Mithi River BKC–Kurla study area.
    One row only — defines the bounding box, river line, and grid resolution.
    """

    __tablename__ = "study_area"

    __table_args__ = (
        Index("ix_study_area_boundary", "boundary", postgresql_using="gist"),
        Index("ix_study_area_river_line", "river_line", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    # spatial_index=False: GeoAlchemy2 would otherwise auto-create its own
    # GiST index, duplicating the explicit indexes declared in __table_args__.
    boundary = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=False
    )
    river_line = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False), nullable=True
    )
    grid_cell_size_m: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SensorNode(Base):
    """
    Registry of every physical and virtual sensor node in the corridor.
    """

    __tablename__ = "sensor_nodes"

    __table_args__ = (
        Index("ix_sensor_nodes_location", "location", postgresql_using="gist"),
        Index("ix_sensor_nodes_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    node_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NodeType.PHYSICAL.value
    )
    role: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    location = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    reference_elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_offset_cm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    baseline_level_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_threshold_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Device auth: SHA-256 of the per-node bearer token. The plaintext token is
    # shown once at registration and never stored. NULL for virtual nodes.
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    battery_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Last time a reading was accepted from this node (drives stale/offline alerts).
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    telemetry_readings: Mapped[list["TelemetryReading"]] = relationship(
        "TelemetryReading", back_populates="node", lazy="select"
    )
    forecasts: Mapped[list["Forecast"]] = relationship(
        "Forecast", back_populates="node", lazy="select"
    )
    risk_assessments: Mapped[list["RiskAssessment"]] = relationship(
        "RiskAssessment", back_populates="node", lazy="select"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        "Alert", back_populates="node", lazy="select"
    )


class NodeRelationship(Base):
    """
    Simplified upstream/downstream propagation rules between nodes.
    Not a hydraulic model — documented propagation with delay and attenuation.
    """

    __tablename__ = "node_relationships"

    __table_args__ = (
        UniqueConstraint(
            "upstream_node_id", "downstream_node_id", name="uq_node_relationship"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upstream_node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensor_nodes.id"), nullable=False
    )
    downstream_node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensor_nodes.id"), nullable=False
    )
    delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    attenuation: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TelemetryReading(Base):
    """
    Main time-series table of accepted sensor readings.
    Includes both physical (iot) and virtual (simulated) readings.
    """

    __tablename__ = "telemetry_readings"

    __table_args__ = (
        Index("ix_telemetry_node_time", "node_id", "recorded_at"),
        Index("ix_telemetry_source", "source"),
        Index("ix_telemetry_data_mode", "data_mode"),
        Index("ix_telemetry_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensor_nodes.id"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    water_level_cm: Mapped[float] = mapped_column(Float, nullable=False)
    rainfall_mm_hr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    flow_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_moisture: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SourceType.IOT.value
    )
    data_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DataMode.LIVE.value
    )
    # Device-supplied idempotency key. A device that retries after a timeout
    # replays the same event_id and gets the original row back (200, not 201).
    event_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Risk snapshot computed at ingest, so a reading is interpretable on its own.
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    node: Mapped["SensorNode"] = relationship("SensorNode", back_populates="telemetry_readings")


class RejectedReading(Base):
    """
    Audit log of invalid readings — logged, not silently dropped.
    Also feeds the sensor fault alert.
    """

    __tablename__ = "rejected_readings"

    __table_args__ = (
        Index("ix_rejected_node_time", "node_code", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reject_reason: Mapped[str] = mapped_column(Text, nullable=False)


class ModelRegistry(Base):
    """
    Versions of trained ML models with metrics and training descriptions.
    """

    __tablename__ = "model_registry"

    __table_args__ = (
        UniqueConstraint(
            "model_name", "version", "horizon_minutes", name="uq_model_registry"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    training_data_description: Mapped[str] = mapped_column(Text, nullable=False)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    rmse: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_rmse: Mapped[float | None] = mapped_column(Float, nullable=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Forecast(Base):
    """
    Every prediction with when it was made and when it applies.
    For later comparison with actuals.
    """

    __tablename__ = "forecasts"

    __table_args__ = (
        Index("ix_forecasts_node_time", "node_id", "made_at"),
        Index("ix_forecasts_scenario", "scenario_id"),
        Index("ix_forecasts_node_target", "node_id", "target_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensor_nodes.id"), nullable=False
    )
    model_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("model_registry.id"), nullable=True
    )
    scenario_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scenarios.id"), nullable=True
    )
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_level_cm: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_bound_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")

    node: Mapped["SensorNode"] = relationship("SensorNode", back_populates="forecasts")


class RiskAssessment(Base):
    """
    Risk score and level per node and horizon (horizon 0 = current).
    Includes explainable component breakdown.
    """

    __tablename__ = "risk_assessments"

    __table_args__ = (
        Index("ix_risk_node_time", "node_id", "assessed_at"),
        Index("ix_risk_scenario", "scenario_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensor_nodes.id"), nullable=False
    )
    forecast_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("forecasts.id"), nullable=True
    )
    scenario_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scenarios.id"), nullable=True
    )
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    predicted_level_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    level_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rise_rate_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rainfall_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    soil_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    primary_driver: Mapped[str | None] = mapped_column(String(64), nullable=True)

    node: Mapped["SensorNode"] = relationship("SensorNode", back_populates="risk_assessments")


class Road(Base):
    """
    Preprocessed road geometries for impact counting and map overlay.
    """

    __tablename__ = "roads"

    __table_args__ = (
        Index("ix_roads_geom", "geometry", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    osm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    road_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    geometry = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False), nullable=False
    )
    min_elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)


class Building(Base):
    """
    Preprocessed building footprints for impact counting, 2D overlay, and 3D extrusion.
    """

    __tablename__ = "buildings"

    __table_args__ = (
        Index("ix_buildings_geom", "footprint", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    osm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    building_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ground_elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    footprint = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=False
    )


class Scenario(Base):
    """
    Saved what-if scenarios with inputs and assumptions.
    """

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rainfall_mm_hr: Mapped[float] = mapped_column(Float, nullable=False)
    initial_level_cm: Mapped[float] = mapped_column(Float, nullable=False)
    drainage_capacity_pct: Mapped[float] = mapped_column(Float, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    assumptions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FloodLayer(Base):
    """
    Simulation output polygons per time step, for live baseline or a scenario.
    Consumed by both the 2D and 3D views.
    """

    __tablename__ = "flood_layers"

    __table_args__ = (
        Index("ix_flood_layers_lookup", "scenario_id", "time_step_minutes", "layer_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scenario_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scenarios.id"), nullable=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    time_step_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    layer_type: Mapped[str] = mapped_column(String(20), nullable=False)
    geojson: Mapped[dict] = mapped_column(JSONB, nullable=False)
    max_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    flooded_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_source: Mapped[str] = mapped_column(String(20), nullable=False)
    grid_resolution_m: Mapped[float] = mapped_column(Float, nullable=False)


class FloodImpact(Base):
    """
    Per-feature impact results (which roads and buildings are affected, at what depth).
    """

    __tablename__ = "flood_impacts"

    __table_args__ = (
        Index("ix_impacts_layer", "flood_layer_id", "feature_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flood_layer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("flood_layers.id", ondelete="CASCADE"), nullable=False
    )
    feature_type: Mapped[str] = mapped_column(String(20), nullable=False)
    feature_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_depth_m: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)


class Alert(Base):
    """
    All alerts (live and scenario) with acknowledgement and history.
    """

    __tablename__ = "alerts"

    __table_args__ = (
        Index("ix_alerts_active", "is_active", "severity", "created_at"),
        Index("ix_alerts_node_type", "node_id", "alert_type", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sensor_nodes.id"), nullable=True
    )
    scenario_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scenarios.id"), nullable=True
    )
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_scenario_alert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    node: Mapped["SensorNode | None"] = relationship("SensorNode", back_populates="alerts")


class ReplayRecording(Base):
    """
    Recorded sensor sessions and scripted scenarios for Replay mode.
    """

    __tablename__ = "replay_recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DataSource(Base):
    """
    Registry of every dataset, powering the About This Data panel and source labelling.
    """

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(64), nullable=True)
    licence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitation: Mapped[str] = mapped_column(Text, nullable=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)