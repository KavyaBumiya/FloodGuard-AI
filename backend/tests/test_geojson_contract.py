"""GeoJSON contract regression tests.

The bug these guard: ``_vectorize_flood_layers`` emitted FeatureCollections
whose entries were ``{"geometry": ..., "properties": ...}`` with no
``"type": "Feature"``. GeoJSON requires the discriminator, and Leaflet
rejects the entire layer with ``Invalid GeoJSON object.`` because
``geometryToLayer`` reads ``geojson.type``, finds ``undefined``, and falls
through to its ``throw``. Every flood layer failed to render.

These tests exercise the real generator, not a copy of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.flood_simulation import FloodSimulationEngine  # noqa: E402

VALID_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}

THRESHOLDS = {"low": 0.05, "moderate": 0.3, "high": 0.6, "severe": 1.0}


def _engine(rows: int = 12, cols: int = 12, resolution: float = 30.0):
    """Build an engine with a synthetic terrain grid, bypassing __init__."""
    engine = object.__new__(FloodSimulationEngine)
    engine.depth_risk_thresholds = dict(THRESHOLDS)

    lat0, lon0 = 19.071, 72.883
    engine._terrain_grid = {
        "rows": rows,
        "cols": cols,
        "resolution_m": resolution,
        "lats": [[lat0 + i * resolution / 111000.0 for _ in range(cols)] for i in range(rows)],
        "lons": [[lon0 + j * resolution / 111000.0 for j in range(cols)] for i in range(rows)],
        "elevation": [[0.0] * cols for _ in range(rows)],
    }
    return engine


def _wet_grid(engine, rows: int = 12, cols: int = 12):
    depth = np.zeros((rows, cols))
    for i in range(3, 9):
        for j in range(3, 9):
            depth[i][j] = 0.1 + 0.15 * ((i + j) % 6)
    risk = np.array(
        [[engine._classify_depth_risk(d) for d in row] for row in depth]
    )
    return depth, risk


@pytest.fixture(scope="module")
def layers():
    engine = _engine()
    depth, risk = _wet_grid(engine)
    depth_fc, risk_fc, boundary_fc = engine._vectorize_flood_layers(depth, risk)
    return {"depth": depth_fc, "risk": risk_fc, "boundary": boundary_fc}


@pytest.mark.parametrize("name", ["depth", "risk", "boundary"])
def test_collection_has_the_featurecollection_discriminator(layers, name):
    fc = layers[name]
    assert fc["type"] == "FeatureCollection", (
        f"{name} collection must declare type=FeatureCollection"
    )
    assert isinstance(fc["features"], list)


@pytest.mark.parametrize("name", ["depth", "risk", "boundary"])
def test_every_feature_declares_type_feature(layers, name):
    """The exact regression: a Feature without `type` is rejected by Leaflet."""
    for idx, feature in enumerate(layers[name]["features"]):
        assert feature.get("type") == "Feature", (
            f"{name}.features[{idx}] is missing type='Feature'; "
            "Leaflet will throw 'Invalid GeoJSON object.'"
        )


@pytest.mark.parametrize("name", ["depth", "risk", "boundary"])
def test_every_feature_has_geometry_and_properties(layers, name):
    for idx, feature in enumerate(layers[name]["features"]):
        assert "geometry" in feature, f"{name}.features[{idx}] has no geometry"
        assert "properties" in feature, f"{name}.features[{idx}] has no properties"
        assert isinstance(feature["properties"], dict)


@pytest.mark.parametrize("name", ["depth", "risk", "boundary"])
def test_every_geometry_type_is_known_to_leaflet(layers, name):
    for idx, feature in enumerate(layers[name]["features"]):
        geom = feature.get("geometry") or {}
        assert geom.get("type") in VALID_GEOMETRY_TYPES, (
            f"{name}.features[{idx}] has unrenderable geometry type "
            f"{geom.get('type')!r}"
        )


@pytest.mark.parametrize("name", ["depth", "risk", "boundary"])
def test_features_survive_a_json_round_trip(layers, name):
    """Serialisation is how the data reaches the browser; it must not drop keys."""
    import json

    for feature in layers[name]["features"]:
        revived = json.loads(json.dumps(feature))
        assert revived["type"] == "Feature"
        assert revived["geometry"]["type"] in VALID_GEOMETRY_TYPES
        assert isinstance(revived["properties"], dict)


def test_generator_actually_produced_features(layers):
    """Guards against the assertions above passing on an empty collection."""
    assert len(layers["depth"]["features"]) > 0
    assert len(layers["risk"]["features"]) > 0


def test_boundary_layer_only_contains_high_and_severe(layers):
    allowed = {"high", "severe"}
    for feature in layers["boundary"]["features"]:
        assert feature["properties"]["risk_level"] in allowed