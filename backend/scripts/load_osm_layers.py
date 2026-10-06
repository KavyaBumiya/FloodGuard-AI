"""
FLOODGUARD AI — Load preprocessed OSM layers into PostGIS.

Reads the offline-prepared extracts produced by `scripts/preprocess_data.py`:

    data/osm/buildings.geojson   -> buildings table
    data/osm/roads.geojson       -> roads table

and inserts them with validated, study-area-clipped geometry so that the
flood engine can run spatial intersections (affected roads / buildings).

Building ground elevation is sampled from the DEM where the footprint falls
inside the raster; outside the raster it stays NULL and is never invented.

Idempotent: rerunning replaces the previously loaded rows rather than
duplicating them.

Usage (from backend/):
    python -m scripts.load_osm_layers
    python -m scripts.load_osm_layers --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.floodguard import Building, Road  # noqa: E402
from geoalchemy2.elements import WKTElement  # noqa: E402

settings = get_settings()

STUDY_SOUTH = settings.study_area_min_lat
STUDY_WEST = settings.study_area_min_lon
STUDY_NORTH = settings.study_area_max_lat
STUDY_EAST = settings.study_area_max_lon


def _study_bbox_polygon():
    from shapely.geometry import box

    return box(STUDY_WEST, STUDY_SOUTH, STUDY_EAST, STUDY_NORTH)


def _valid_geometry(geom: BaseGeometry | None) -> BaseGeometry | None:
    """Repair invalid geometry where reasonably possible, else discard."""
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
        if geom.is_empty or not geom.is_valid:
            return None
    return geom


def _clipped(geom: BaseGeometry) -> BaseGeometry | None:
    """Clip to the study area; return None when nothing remains."""
    valid_geom = _valid_geometry(geom)
    if valid_geom is None:
        return None
    if not valid_geom.intersects(_study_bbox_polygon()):
        return None
    try:
        clipped = valid_geom.intersection(_study_bbox_polygon())
    except Exception:  # noqa: BLE001
        return valid_geom  # keep the original rather than lose the feature
    return _valid_geometry(clipped)


def _explode(geom: BaseGeometry, wanted: tuple[str, ...]) -> list[BaseGeometry]:
    """Flatten multi-part / collection geometry into single-part parts.

    Clipping a feature on the study-area boundary routinely turns a
    LineString into a MultiLineString (and can yield a GeometryCollection
    mixing lines with points). The database columns are single-part, so the
    parts must be split rather than rejected.
    """
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type in wanted:
        return [geom]
    if isinstance(geom, BaseMultipartGeometry):
        parts: list[BaseGeometry] = []
        for part in geom.geoms:
            parts.extend(_explode(part, wanted))
        return parts
    return []


class DemSampler:
    """Samples ground elevation from the study-area DEM."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._dataset = None
        self._array: np.ndarray | None = None
        self._transform = None
        self._nodata: float | None = None
        self.available = False
        if path.exists():
            try:
                import rasterio

                self._dataset = rasterio.open(str(path))
                self._array = self._dataset.read(1).astype("float64")
                self._transform = self._dataset.transform
                self._nodata = self._dataset.nodata
                self.available = True
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] could not open DEM {path}: {exc}")

    def sample(self, lon: float, lat: float) -> float | None:
        if (
            not self.available
            or self._dataset is None
            or self._array is None
            or self._transform is None
        ):
            return None
        try:
            row, col = self._dataset.index(lon, lat)
        except Exception:  # noqa: BLE001
            return None
        if not (0 <= row < self._array.shape[0] and 0 <= col < self._array.shape[1]):
            return None
        value = float(self._array[row, col])
        if self._nodata is not None and value == self._nodata:
            return None
        if not np.isfinite(value):
            return None
        return round(value, 3)

    def close(self) -> None:
        if self._dataset is not None:
            self._dataset.close()


def _load_features(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} — run `python -m scripts.preprocess_data` first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features") if isinstance(payload, dict) else None
    if features is None:
        raise ValueError(f"{path} has no 'features' array")
    return features


def _to_wkt(geom: BaseGeometry) -> WKTElement:
    return WKTElement(geom.wkt, srid=4326)


async def load_buildings(force: bool, dem: DemSampler) -> tuple[int, int]:
    path = settings.resolve_data_path(settings.osm_buildings_geojson)
    features = _load_features(path)
    inserted = skipped = 0

    async with AsyncSessionLocal() as db:
        if force:
            await db.execute(delete(Building))

        existing_ids: set[str] = set()
        if not force:
            rows = await db.execute(select(Building.osm_id))
            existing_ids = {str(r[0]) for r in rows if r[0] is not None}

        for feat in features:
            props = feat.get("properties") or {}
            osm_id = props.get("osm_id")
            if osm_id is not None and str(osm_id) in existing_ids:
                skipped += 1
                continue

            clipped = _clipped(shape(feat["geometry"]))
            if clipped is None:
                skipped += 1
                continue
            parts = _explode(clipped, ("Polygon",))
            if not parts:
                skipped += 1
                continue

            height = props.get("height_m")
            height_estimated = bool(props.get("height_is_estimated", True))

            for geom in parts:
                ground = dem.sample(geom.centroid.x, geom.centroid.y)
                db.add(
                    Building(
                        osm_id=int(osm_id) if osm_id not in (None, "") else None,
                        name=(props.get("name") or None),
                        building_type=(props.get("building_type") or None),
                        height_m=float(height) if height not in (None, "") else None,
                        height_is_estimated=height_estimated,
                        ground_elevation_m=ground,
                        footprint=_to_wkt(geom),
                    )
                )
                inserted += 1
                if inserted % 500 == 0:
                    await db.flush()

        await db.commit()

    return inserted, skipped


async def load_roads(force: bool) -> tuple[int, int]:
    path = settings.resolve_data_path(settings.osm_roads_geojson)
    features = _load_features(path)
    inserted = skipped = 0

    async with AsyncSessionLocal() as db:
        if force:
            await db.execute(delete(Road))

        existing_ids: set[str] = set()
        if not force:
            rows = await db.execute(select(Road.osm_id))
            existing_ids = {str(r[0]) for r in rows if r[0] is not None}

        for feat in features:
            props = feat.get("properties") or {}
            osm_id = props.get("osm_id")
            if osm_id is not None and str(osm_id) in existing_ids:
                skipped += 1
                continue

            clipped = _clipped(shape(feat["geometry"]))
            if clipped is None:
                skipped += 1
                continue
            parts = _explode(clipped, ("LineString",))
            if not parts:
                skipped += 1
                continue

            for geom in parts:
                db.add(
                    Road(
                        osm_id=int(osm_id) if osm_id not in (None, "") else None,
                        name=(props.get("name") or None),
                        road_type=(props.get("road_type") or None),
                        geometry=_to_wkt(geom),
                    )
                )
                inserted += 1
                if inserted % 500 == 0:
                    await db.flush()

        await db.commit()

    return inserted, skipped


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete previously loaded rows first instead of skipping them.",
    )
    args = parser.parse_args()

    dem = DemSampler(settings.resolve_data_path(settings.terrain_raster_path))
    try:
        print(f"DEM sampler available: {dem.available} ({settings.terrain_raster_path})")
        b_inserted, b_skipped = await load_buildings(args.force, dem)
        r_inserted, r_skipped = await load_roads(args.force)

        async with AsyncSessionLocal() as db:
            b_total = (await db.execute(select(func.count()).select_from(Building))).scalar_one()
            r_total = (await db.execute(select(func.count()).select_from(Road))).scalar_one()

        print(f"buildings: +{b_inserted} inserted, {b_skipped} skipped -> {b_total} total")
        print(f"roads:     +{r_inserted} inserted, {r_skipped} skipped -> {r_total} total")
        if b_total == 0 or r_total == 0:
            print(
                "[warn] layer empty — infrastructure impact will report zero. "
                "Check the OSM extracts referenced by the OSM_* settings."
            )
    finally:
        dem.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
