"""
FLOODGUARD AI — Flood Simulation Engine
========================================
Elevation-based raster inundation model for the Mithi River corridor.
Produces flood depth, risk, and boundary layers for 2D map and 3D twin.

This is a simplified inundation estimate, not a full hydraulic model.
Does not solve momentum or mass conservation. No flow velocity calculation.
Accuracy limited by elevation data resolution (~30m SRTM).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import rasterio
from rasterio.transform import xy
import structlog
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import unary_union
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.floodguard import (
    SensorNode,
    Forecast,
    FloodLayer,
    FloodImpact,
    Road,
    Building,
    Scenario,
    SourceType,
)
from app.integrations import cache
from geoalchemy2.elements import WKTElement

logger = structlog.get_logger()

# Simulation parameters
# self.grid_resolution_m = 10.0  # Grid cell size in metres (use self.grid_resolution_m)
# CONNECTIVITY_BUFFER_M = 50.0  # Buffer for river/drainage connectivity (use self.connectivity_buffer_m)
# MAX_DEPTH_M = 5.0  # Maximum depth for visualization (use self.max_depth_m)

# # Risk classification by depth
# # DEPTH_RISK_THRESHOLDS = {  # use self.depth_risk_thresholds
#     "low": 0.1,       # > 0.1m = low risk
#     "moderate": 0.3,  # > 0.3m = moderate
#     "high": 0.6,      # > 0.6m = high
# # # # #     "severe": 1.0,    # > 1.0m = severe
# }


class FloodSimulationEngine:
    """
    Elevation-based flood simulation for the Mithi River corridor.
    
    Process:
    1. Load precomputed terrain grid (elevation + metadata)
    2. Convert sensor water levels to water surface elevation
    3. Interpolate water surface along river centerline
    4. Calculate depth = max(0, water_surface - ground_elevation)
    5. Apply connectivity mask (only hydraulically connected areas flood)
    6. Classify depth into risk levels
    7. Vectorize to GeoJSON polygons
    8. Compute impacts on roads and buildings
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        # Load simulation parameters from config (with fallbacks to defaults)
        self.grid_resolution_m = self.settings.flood_grid_resolution_m
        self.connectivity_buffer_m = self.settings.flood_connectivity_buffer_m
        self.max_depth_m = self.settings.flood_max_depth_m
        self.depth_risk_thresholds = {
            "low": self.settings.flood_depth_risk_low_m,
            "moderate": self.settings.flood_depth_risk_moderate_m,
            "high": self.settings.flood_depth_risk_high_m,
            "severe": self.settings.flood_depth_risk_severe_m,
        }
        self._terrain_grid: Optional[dict] = None
        self._terrain_loaded = False
        self._river_line: Optional[LineString] = None
        self._river_line_source: str = "unavailable"

    async def load_terrain(self, tiff_path: Optional[str] = None) -> bool:
        """Load and preprocess terrain grid from GeoTIFF or cached data."""
        if self._terrain_loaded:
            return True

        try:
            # Try to load from cached Redis first - use separate keys for real/synthetic
            # to prevent synthetic terrain from masquerading as real elevation
            redis = await cache.get_redis()
            cached = await redis.get("floodguard:terrain_grid:real")
            if cached:
                self._terrain_grid = json.loads(cached)
                self._terrain_loaded = True
                logger.info("terrain_loaded_from_cache", source="real")
                return True

            # Resolve the configured raster. Paths are repo-relative, so they
            # must not be resolved against the process CWD.
            raw_path = tiff_path or self.settings.terrain_raster_path
            raster = self.settings.resolve_data_path(raw_path)

            if raster.exists():
                await self._load_from_geotiff(str(raster))
                source = str(raster)
                synthetic = False
            else:
                if not self.settings.allow_synthetic_terrain:
                    logger.error(
                        "terrain_raster_missing",
                        path=str(raster),
                        hint="run `python -m scripts.preprocess_data` in backend/, "
                             "or set ALLOW_SYNTHETIC_TERRAIN=true to allow synthetic "
                             "elevation (results must be labelled SIMULATED)",
                    )
                    return False
                logger.warning(
                    "terrain_raster_missing_using_synthetic",
                    path=str(raster),
                    note="synthetic elevation - outputs are SIMULATED, not real inundation",
                )
                await self._create_synthetic_grid()
                source = "synthetic"
                synthetic = True

            if self._terrain_grid is None:
                logger.error("terrain_grid_not_created", source=source)
                return False

            self._terrain_grid["source"] = source
            self._terrain_grid["synthetic"] = synthetic

            # Never let a synthetic grid occupy the real-terrain cache key, or
            # it would masquerade as real elevation for the next 24 hours.
            if not synthetic:
                await redis.setex(
                    "floodguard:terrain_grid:real",
                    86400,  # 24 hours
                    json.dumps(self._terrain_grid, default=str),
                )

            self._terrain_loaded = True
            logger.info(
                "terrain_loaded",
                source=source,
                synthetic=synthetic,
                rows=self._terrain_grid["rows"],
                cols=self._terrain_grid["cols"],
                resolution_m=self._terrain_grid.get("resolution_m"),
            )
            return True

        except Exception as exc:
            logger.error("terrain_load_failed", error=str(exc))
            return False

    async def _load_from_geotiff(self, tiff_path: str) -> None:
        """Load elevation data from SRTM GeoTIFF."""
        with rasterio.open(tiff_path) as src:
            # Read elevation data
            elevation = src.read(1).astype(np.float32)
            transform = src.transform
            crs = src.crs
            nodata = src.nodata

            # Handle nodata
            if nodata is not None:
                elevation[elevation == nodata] = np.nan

            # Get bounds
            bounds = src.bounds
            height, width = elevation.shape

            # Create coordinate grids
            rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing='ij')
            xs, ys = xy(transform, rows, cols)
            # rasterio.transform.xy returns FLAT sequences even for 2D index
            # arrays; without the reshape every grid lookup downstream raised
            # "float is not subscriptable" and no flood layer was ever stored.
            lons = np.asarray(xs, dtype="float64").reshape(height, width)
            lats = np.asarray(ys, dtype="float64").reshape(height, width)

            # Calculate grid resolution
            res_x = abs(transform.a)
            res_y = abs(transform.e)
            if crs is not None and crs.is_geographic:
                mean_lat = float(np.nanmean(lats))
                res_x_m = res_x * 111320.0 * abs(np.cos(np.radians(mean_lat)))
                res_y_m = res_y * 110540.0
            else:
                res_x_m = res_x
                res_y_m = res_y

            self._terrain_grid = {
                "elevation": elevation.tolist(),
                "lats": lats.tolist(),
                "lons": lons.tolist(),
                "rows": height,
                "cols": width,
                "resolution_m": (res_x_m + res_y_m) / 2,
                "bounds": {
                    "south": bounds.bottom,
                    "west": bounds.left,
                    "north": bounds.top,
                    "east": bounds.right,
                },
                "transform": transform.to_gdal(),
                "crs": str(crs),
                "min_elevation": float(np.nanmin(elevation)),
                "max_elevation": float(np.nanmax(elevation)),
            }

    async def _create_synthetic_grid(self) -> None:
        """Create a synthetic terrain grid for demo when no TIFF available."""
        # Study area bounds
        south = self.settings.study_area_min_lat
        north = self.settings.study_area_max_lat
        west = self.settings.study_area_min_lon
        east = self.settings.study_area_max_lon

        # Calculate grid dimensions
        lat_range = north - south
        lon_range = east - west
        
        # Approximate metres per degree
        m_per_deg_lat = 111000
        m_per_deg_lon = 111000 * np.cos(np.radians((north + south) / 2))
        
        height_m = lat_range * m_per_deg_lat
        width_m = lon_range * m_per_deg_lon
        
        rows = max(50, int(height_m / self.grid_resolution_m))
        cols = max(50, int(width_m / self.grid_resolution_m))

        # Create coordinate grids
        lats = np.linspace(north, south, rows)
        lons = np.linspace(west, east, cols)
        lon_grid, lat_grid = np.meshgrid(lons, lats)

        # Synthetic elevation: river valley sloping towards coast
        # Lower elevation near river (center), higher on banks
        center_lat = (north + south) / 2
        center_lon = (west + east) / 2
        
        dist_from_center = np.sqrt(
            ((lat_grid - center_lat) * m_per_deg_lat) ** 2 +
            ((lon_grid - center_lon) * m_per_deg_lon) ** 2
        )
        
        # Base elevation 2-8m with river channel at ~1m
        river_dist = np.abs(lon_grid - center_lon) * m_per_deg_lon
        elevation = 5.0 + 3.0 * np.tanh(river_dist / 200) - 4.0 * np.exp(-river_dist / 100)
        
        # Add some noise
        elevation += np.random.normal(0, 0.3, elevation.shape)
        elevation = np.maximum(elevation, 0.5)

        self._terrain_grid = {
            "elevation": elevation.tolist(),
            "lats": lat_grid.tolist(),
            "lons": lon_grid.tolist(),
            "rows": rows,
            "cols": cols,
            "resolution_m": self.grid_resolution_m,
            "bounds": {"south": south, "west": west, "north": north, "east": east},
            "min_elevation": float(np.min(elevation)),
            "max_elevation": float(np.max(elevation)),
        }

    def _get_river_centerline(self) -> LineString:
        """Get the real Mithi River centerline as a LineString.

        Read once from the preprocessed OSM waterways extract and cached on the
        instance. Falls back to a straight line through the study-area centre
        only when the extract is missing or unusable, and that fallback is
        flagged so callers can label the output as degraded.
        """
        cached_line = getattr(self, "_river_line", None)
        if cached_line is not None:
            return cached_line

        line: LineString | None = None
        source = "unavailable"
        try:
            path = self.settings.resolve_data_path(self.settings.osm_waterways_geojson)
            payload = json.loads(path.read_text(encoding="utf-8"))
            river_features = [
                f
                for f in payload.get("features", [])
                if (f.get("properties") or {}).get("is_river")
            ]
            # Prefer the explicitly flagged river, else the longest waterway.
            if not river_features:
                river_features = sorted(
                    payload.get("features", []),
                    key=lambda f: len((f.get("geometry") or {}).get("coordinates") or []),
                    reverse=True,
                )[:1]

            if river_features:
                coords = river_features[0]["geometry"]["coordinates"]
                # Drop the Z ordinate if present and drop repeated points.
                clean: list[tuple[float, float]] = []
                for c in coords:
                    pt = (float(c[0]), float(c[1]))
                    if not clean or pt != clean[-1]:
                        clean.append(pt)
                if len(clean) >= 2:
                    line = LineString(clean)
                    source = "osm_waterways"
        except Exception as exc:  # noqa: BLE001
            logger.warning("river_centerline_load_failed", error=str(exc))

        if line is None:
            south = self.settings.study_area_min_lat
            north = self.settings.study_area_max_lat
            center_lon = (
                self.settings.study_area_min_lon + self.settings.study_area_max_lon
            ) / 2
            line = LineString(
                [(center_lon, south), (center_lon, (south + north) / 2), (center_lon, north)]
            )
            source = "straight_line_fallback"
            logger.warning(
                "river_centerline_fallback",
                note="interpolation axis is approximate - regenerate OSM waterways",
            )

        self._river_line = line
        self._river_line_source = source
        return line

    @property
    def river_centerline_source(self) -> str:
        return getattr(self, "_river_line_source", "unavailable")

    def _interpolate_water_surface(
        self, sensor_levels: dict[int, float], sensor_locations: dict[int, Point]
    ) -> np.ndarray:
        """
        Interpolate water surface elevation across grid from sensor readings.
        
        Args:
            sensor_levels: node_id -> water_surface_elevation (m)
            sensor_locations: node_id -> Point(lon, lat)
            
        Returns:
            2D array of water surface elevation for each grid cell
        """
        grid = self._terrain_grid
        if grid is None:
            raise RuntimeError("Terrain grid must be loaded before interpolation")
        rows, cols = grid["rows"], grid["cols"]
        lats = np.array(grid["lats"])
        lons = np.array(grid["lons"])
        
        water_surface = np.full((rows, cols), np.nan)
        
        if not sensor_levels:
            return water_surface

        # For each grid cell, find nearest river point and interpolate
        river_line = self._get_river_centerline()
        
        # Sample points along river
        river_points = []
        river_values = []
        
        for node_id, level in sensor_levels.items():
            if node_id in sensor_locations:
                pt = sensor_locations[node_id]
                # Project to river
                proj_dist = river_line.project(pt)
                river_pt = river_line.interpolate(proj_dist)
                river_points.append((river_pt.x, river_pt.y))
                river_values.append(level)

        if len(river_points) < 2:
            # Single sensor - use constant water surface
            const_level = list(sensor_levels.values())[0]
            water_surface.fill(const_level)
            return water_surface

        # Interpolate along river
        from scipy.interpolate import interp1d
        
        river_points = np.array(river_points)
        river_values = np.array(river_values)
        
        # Sort by distance along river
        dists = np.array([river_line.project(Point(p)) for p in river_points])
        sort_idx = np.argsort(dists)
        dists = dists[sort_idx]
        river_values = river_values[sort_idx]
        
        # Create interpolator
        if len(dists) > 1:
            interp = interp1d(dists, river_values, kind='linear',
                            bounds_error=False, fill_value=0.0)
        else:
            interp = lambda x: river_values[0]

        # For each grid cell, find nearest river point and get water surface
        d_min = float(np.min(dists))
        d_max = float(np.max(dists))
        for i in range(rows):
            for j in range(cols):
                if np.isnan(grid["elevation"][i][j]):
                    continue
                cell_pt = Point(lons[i][j], lats[i][j])
                # Clamp to the surveyed span: extrapolating past the outermost
                # sensors invents a water surface that no observation supports.
                proj_dist = float(np.clip(river_line.project(cell_pt), d_min, d_max))
                water_surface[i][j] = float(interp(proj_dist))

        return water_surface

    def _apply_connectivity_mask(
        self, depth_grid: np.ndarray, water_surface: np.ndarray
    ) -> np.ndarray:
        """
        Apply connectivity mask: only flood cells connected to river/drainage.
        Uses flood-fill from river cells.
        """
        grid = self._terrain_grid
        if grid is None:
            return depth_grid
        rows, cols = grid["rows"], grid["cols"]
        elevation = np.array(grid["elevation"], dtype=float)
        lons = np.array(grid["lons"], dtype=float)
        lats = np.array(grid["lats"], dtype=float)

        # Distance from every grid cell to the river centreline, vectorised.
        # LineString.distance() is the planar distance to the nearest *segment*,
        # not to the nearest vertex, so this computes point-to-segment to match.
        # Previously this built a Shapely Point per cell and called distance() on
        # it - ~9700 geometry calls per simulation, which dominated the runtime.
        coords = np.asarray(self._get_river_centerline().coords, dtype=float)
        threshold = self.connectivity_buffer_m / 111000.0
        px = lons.ravel()
        py = lats.ravel()
        nearest = np.full(px.shape, np.inf, dtype=float)

        for k in range(len(coords) - 1):
            ax, ay = coords[k]
            bx, by = coords[k + 1]
            dx, dy = bx - ax, by - ay
            seg_sq = dx * dx + dy * dy
            if seg_sq == 0.0:
                dist = np.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg_sq
                np.clip(t, 0.0, 1.0, out=t)
                dist = np.hypot(px - (ax + t * dx), py - (ay + t * dy))
            np.minimum(nearest, dist, out=nearest)

        land = ~np.isnan(elevation.ravel())
        river_flat = np.flatnonzero(land & (nearest < threshold))
        if river_flat.size == 0:
            return depth_grid

        river_rows = river_flat // cols
        river_cols = river_flat - river_rows * cols

        # Flood fill from river cells, 4-connected. Water crosses into a cell
        # only where its surface clears the terrain. `wet` is precomputed so the
        # fill does no per-neighbour NaN checks.
        wet = (~np.isnan(elevation)) & (water_surface > elevation)
        flooded = np.zeros((rows, cols), dtype=bool)
        flooded[river_rows, river_cols] = True

        # deque.popleft() is O(1); list.pop(0) was O(n), making the fill
        # quadratic in cell count.
        queue = deque(zip(river_rows.tolist(), river_cols.tolist()))
        while queue:
            i, j = queue.popleft()
            for ni, nj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if 0 <= ni < rows and 0 <= nj < cols and wet[ni, nj] and not flooded[ni, nj]:
                    flooded[ni, nj] = True
                    queue.append((ni, nj))

        return np.where(flooded, depth_grid, 0.0)

    def _classify_depth_risk(self, depth: float) -> str:
        """Classify depth into risk level."""
        if depth >= self.depth_risk_thresholds["severe"]:
            return "severe"
        elif depth >= self.depth_risk_thresholds["high"]:
            return "high"
        elif depth >= self.depth_risk_thresholds["moderate"]:
            return "moderate"
        elif depth >= self.depth_risk_thresholds["low"]:
            return "low"
        return "none"

    def _vectorize_flood_layers(
        self, depth_grid: np.ndarray, risk_grid: np.ndarray
    ) -> tuple[dict, dict, dict]:
        """Convert raster grids to GeoJSON polygons for depth, risk, boundary."""
        grid = self._terrain_grid
        if grid is None:
            raise RuntimeError("Terrain grid is unavailable")
        rows, cols = grid["rows"], grid["cols"]
        lats = np.array(grid["lats"])
        lons = np.array(grid["lons"])
        resolution = grid["resolution_m"]

        depth_polygons = []
        risk_polygons = {"low": [], "moderate": [], "high": [], "severe": []}
        boundary_polygons = []
        vectorized_cells = 0

        # Longitude degrees shrink with latitude; using a plain /111000 for both
        # axes inflates the drawn cell width by ~1/cos(lat) (~6% at 19 deg N).
        mean_lat = float(np.nanmean(lats)) if lats.size else 0.0
        half_y = resolution / 2 / 111000.0
        half_x = resolution / 2 / (111000.0 * max(0.1, np.cos(np.radians(mean_lat))))

        visited = np.zeros((rows, cols), dtype=bool)

        for i in range(rows):
            for j in range(cols):
                if visited[i, j] or np.isnan(grid["elevation"][i][j]) or depth_grid[i, j] <= 0:
                    continue

                # Find connected component of same risk level
                risk_level = self._classify_depth_risk(depth_grid[i, j])
                if risk_level == "none":
                    continue

                # BFS to find connected cells
                component = []
                queue = [(i, j)]
                visited[i, j] = True
                target_risk = risk_grid[i, j]

                while queue:
                    ci, cj = queue.pop(0)
                    component.append((ci, cj))

                    for di, dj in [(-1,0), (1,0), (0,-1), (0,1)]:
                        ni, nj = ci + di, cj + dj
                        if (0 <= ni < rows and 0 <= nj < cols and 
                            not visited[ni, nj] and 
                            not np.isnan(grid["elevation"][ni][nj]) and
                            risk_grid[ni, nj] == target_risk and
                            depth_grid[ni, nj] > 0):
                            visited[ni, nj] = True
                            queue.append((ni, nj))

                # Create polygon from component cells
                if len(component) >= 4:  # Minimum for a polygon
                    polys = []
                    for ci, cj in component:
                        # Cell corners
                        lat = lats[ci, cj]
                        lon = lons[ci, cj]
                        polys.append(Polygon([
                            (lon - half_x, lat - half_y),
                            (lon + half_x, lat - half_y),
                            (lon + half_x, lat + half_y),
                            (lon - half_x, lat + half_y),
                        ]))
                    
                    if polys:
                        union_poly = unary_union(polys)
                        if union_poly.is_valid and not union_poly.is_empty:
                            geojson = mapping(union_poly)
                            props = {
                                "risk_level": risk_level,
                                "max_depth_m": float(np.max([depth_grid[ci, cj] for ci, cj in component])),
                                "cell_count": len(component),
                            }
                            
                            # Every entry in a FeatureCollection's `features` array
                            # must be a GeoJSON Feature, i.e. carry an explicit
                            # `"type": "Feature"`. Leaflet treats a typeless object
                            # as a bare geometry, finds no `type`, and rejects the
                            # whole layer with "Invalid GeoJSON object.".
                            feature = {"type": "Feature", "geometry": geojson, "properties": props}

                            if risk_level in risk_polygons:
                                risk_polygons[risk_level].append(feature)
                            depth_polygons.append(
                                {**feature, "properties": {**props, "type": "depth"}}
                            )

                            # Add to boundary if severe/high
                            if risk_level in ("high", "severe"):
                                boundary_polygons.append(feature)

        # Create FeatureCollections
        depth_fc = {"type": "FeatureCollection", "features": depth_polygons}
        risk_fc = {"type": "FeatureCollection", "features": sum(risk_polygons.values(), [])}
        boundary_fc = {"type": "FeatureCollection", "features": boundary_polygons}

        return depth_fc, risk_fc, boundary_fc

    def _build_layers_sync(
        self,
        elevation: np.ndarray,
        sensor_levels: dict[int, float],
        sensor_locations: dict[int, Point],
    ) -> tuple[np.ndarray, np.ndarray, dict, dict, dict]:
        """Run the CPU-heavy raster work outside the asyncio event loop."""
        water_surface = self._interpolate_water_surface(
            sensor_levels, sensor_locations
        )
        depth_grid = np.maximum(0, water_surface - elevation)
        depth_grid = np.where(np.isnan(elevation), 0, depth_grid)
        depth_grid = np.minimum(depth_grid, self.max_depth_m)
        depth_grid = self._apply_connectivity_mask(depth_grid, water_surface)
        risk_grid = np.vectorize(self._classify_depth_risk)(depth_grid)
        depth_fc, risk_fc, boundary_fc = self._vectorize_flood_layers(
            depth_grid, risk_grid
        )
        return water_surface, depth_grid, depth_fc, risk_fc, boundary_fc

    async def run_simulation(
        self,
        node_levels: dict[int, float],  # node_id -> water_level_cm
        time_step_minutes: int = 0,
        scenario_id: Optional[int] = None,
        input_source: str = "live",
    ) -> dict:
        """
        Run flood simulation for given sensor water levels.
        
        Returns dict with flood layers and impact summary.
        """
        if not self._terrain_loaded:
            await self.load_terrain()

        grid = self._terrain_grid
        if grid is None:
            # Never run against a missing grid: a silent synthetic substitute
            # would be reported as real inundation.
            raise RuntimeError(
                "Terrain unavailable — flood simulation cannot run. "
                "Check TERRAIN_RASTER_PATH or run `python -m scripts.preprocess_data`."
            )
        elevation = np.array(grid["elevation"])
        rows, cols = grid["rows"], grid["cols"]

        # Get sensor locations and reference elevations
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SensorNode).where(SensorNode.is_active == True)
            )
            nodes = result.scalars().all()

        sensor_levels = {}
        sensor_locations = {}
        for node in nodes:
            if node.id in node_levels:
                # Convert water level (cm above sensor) to water surface elevation (m ASL)
                ref_elev = node.reference_elevation_m or grid["min_elevation"]
                calibration = node.calibration_offset_cm / 100.0
                water_surface_elev = ref_elev + (node_levels[node.id] / 100.0) + calibration
                sensor_levels[node.id] = water_surface_elev
                
                # Get location
                if node.location:
                    # Parse POINT(lon lat)
                    import re
                    match = re.match(r"POINT\(([\d.]+) ([\d.]+)\)", str(node.location))
                    if match:
                        lon, lat = float(match.group(1)), float(match.group(2))
                        sensor_locations[node.id] = Point(lon, lat)

        # Raster interpolation, flood-fill, classification and vectorisation are
        # CPU-bound. Running them inline starves FastAPI's event loop while the
        # live IoT pipeline refreshes four time steps.
        (
            _water_surface,
            depth_grid,
            depth_fc,
            risk_fc,
            boundary_fc,
        ) = await asyncio.to_thread(
            self._build_layers_sync,
            elevation,
            sensor_levels,
            sensor_locations,
        )

        # Calculate summary stats. The reported area must match the polygons
        # the map actually draws, so it is counted from the vectorised extent
        # rather than from every cell with depth > 0 (which would include
        # sub-threshold wet cells that are never rendered).
        grid_resolution_m = float((self._terrain_grid or {}).get("resolution_m") or self.grid_resolution_m)
        max_depth = float(np.max(depth_grid)) if np.any(depth_grid > 0) else 0.0
        vectorized_cells = sum(
            int(f["properties"].get("cell_count", 0)) for f in depth_fc.get("features", [])
        )
        flooded_area_m2 = float(vectorized_cells) * (grid_resolution_m ** 2)

        # Compute impacts on roads and buildings against this extent
        impacts = await self._compute_impacts(depth_grid, depth_fc, scenario_id)

        # Create flood layers
        layers = {
            "depth": depth_fc,
            "risk": risk_fc,
            "boundary": boundary_fc,
        }

        return {
            "layers": layers,
            "max_depth_m": max_depth,
            "flooded_area_m2": flooded_area_m2,
            "impacts": impacts,
            "grid_resolution_m": grid_resolution_m,
        }

    def _sample_depth_at(self, depth_grid: np.ndarray, lon: float, lat: float) -> float:
        """Depth (m) of the grid cell containing a lon/lat, or 0.0 outside."""
        grid = self._terrain_grid
        if grid is None:
            return 0.0
        lons = np.asarray(grid["lons"])
        lats = np.asarray(grid["lats"])
        # lons/lats are 2D arrays of cell centres; pick the nearest centre.
        d2 = (lons - lon) ** 2 + (lats - lat) ** 2
        idx = np.unravel_index(int(np.argmin(d2)), d2.shape)
        if np.isnan(grid["elevation"][idx[0]][idx[1]]):
            return 0.0
        return float(depth_grid[idx[0], idx[1]])

    async def _query_features(
        self,
        db: AsyncSession,
        model,
        geom_column,
        flood_wkt,
        flood_geom,
        depth_grid: np.ndarray,
        feature_type: str,
    ) -> list[dict]:
        """Spatially intersect one feature layer with the flood extent.

        Depth is sampled at the representative point of the *intersection*, not
        of the whole feature: a building that only clips the flood edge must not
        be assigned the depth of its dry far corner (nor dropped for it).
        """
        from shapely.geometry import shape as shapely_shape

        rows = await db.execute(
            select(
                model.id,
                model.osm_id,
                model.name,
                func.ST_AsGeoJSON(geom_column).label("geojson"),
            ).where(func.ST_Intersects(geom_column, flood_wkt))
        )

        features: list[dict] = []
        for row in rows.all():
            try:
                geom = shapely_shape(json.loads(row.geojson))
            except (TypeError, ValueError):
                continue

            try:
                overlap = geom.intersection(flood_geom)
            except Exception:  # noqa: BLE001
                overlap = geom
            if overlap.is_empty:
                continue
            point = overlap.representative_point()
            depth = self._sample_depth_at(depth_grid, point.x, point.y)
            if depth <= 0:
                continue

            entry: dict[str, Any] = {
                "id": int(row.id),
                "osm_id": row.osm_id,
                "name": row.name,
                "feature_type": feature_type,
                "depth_m": round(depth, 3),
                "risk_level": self._classify_depth_risk(depth),
            }
            if feature_type == "building":
                entry["building_type"] = getattr(row, "building_type", None)
            elif feature_type == "road":
                entry["road_type"] = getattr(row, "road_type", None)
            features.append(entry)
        return features

    async def _compute_impacts(
        self,
        depth_grid: np.ndarray,
        depth_fc: dict,
        scenario_id: Optional[int],
    ) -> dict:
        """Intersect the predicted flood extent with roads and buildings.

        Uses PostGIS ST_Intersects against the stored OSM layers, then samples
        the modelled depth at each feature's representative point. Results are
        MODEL ESTIMATES: they inherit the DEM's ~30 m resolution and the OSM
        layer's completeness, and are never an official flood zone.
        """
        empty = {
            "roads": [],
            "buildings": [],
            "affected_road_count": 0,
            "affected_building_count": 0,
            "depth_range_m": None,
        }

        features = (depth_fc or {}).get("features") or []
        if not features:
            return empty

        try:
            polys = [shape(f["geometry"]) for f in features if f.get("geometry")]
            flood = unary_union([p for p in polys if not p.is_empty])
        except Exception as exc:  # noqa: BLE001
            logger.warning("impact_extent_failed", error=str(exc))
            return empty
        if flood.is_empty:
            return empty
        if not flood.is_valid:
            flood = flood.buffer(0)
        if flood.is_empty:
            return empty

        flood_wkt = WKTElement(flood.wkt, srid=4326)

        try:
            async with AsyncSessionLocal() as db:
                buildings = await self._query_features(
                    db, Building, Building.footprint, flood_wkt, flood,
                    depth_grid, "building",
                )
                roads = await self._query_features(
                    db, Road, Road.geometry, flood_wkt, flood,
                    depth_grid, "road",
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("impact_query_failed", error=str(exc))
            return empty

        depths = [f["depth_m"] for f in buildings + roads]
        return {
            "buildings": buildings,
            "roads": roads,
            "affected_building_count": len(buildings),
            "affected_road_count": len(roads),
            "depth_range_m": [round(min(depths), 3), round(max(depths), 3)] if depths else None,
        }

    async def run_for_time_steps(
        self,
        forecasts_by_node: dict[int, list[Forecast]],
        scenario_id: Optional[int] = None,
        input_source: str = "live",
    ) -> dict[int, dict]:
        """
        Run simulation for all time steps (0, 15, 30, 60 minutes).
        
        Args:
            forecasts_by_node: node_id -> list of Forecast objects
            
        Returns:
            time_step_minutes -> simulation result
        """
        results = {}
        
        for time_step in [0, 15, 30, 60]:
            # Get predicted levels for this time step
            node_levels = {}
            for node_id, forecasts in forecasts_by_node.items():
                for fc in forecasts:
                    if fc.horizon_minutes == time_step:
                        node_levels[node_id] = fc.predicted_level_cm
                        break
            
            if node_levels:
                result = await self.run_simulation(
                    node_levels=node_levels,
                    time_step_minutes=time_step,
                    scenario_id=scenario_id,
                    input_source=input_source,
                )
                results[time_step] = result

                # Store in database
                await self._store_flood_layers(result, time_step, scenario_id, input_source)

        return results

    async def _store_flood_layers(
        self, result: dict, time_step: int, scenario_id: Optional[int], input_source: str
    ) -> None:
        """Store flood layers (and their per-feature impacts) in database."""
        impacts = result.get("impacts") or {}
        async with AsyncSessionLocal() as db:
            depth_layer: FloodLayer | None = None
            for layer_type, geojson in result["layers"].items():
                layer = FloodLayer(
                    scenario_id=scenario_id,
                    time_step_minutes=time_step,
                    layer_type=layer_type,
                    geojson=geojson,
                    max_depth_m=result["max_depth_m"],
                    flooded_area_m2=result["flooded_area_m2"],
                    input_source=input_source,
                    grid_resolution_m=result["grid_resolution_m"],
                )
                db.add(layer)
                await db.flush()
                if layer_type == "depth":
                    depth_layer = layer

            if depth_layer is not None:
                for entry in list(impacts.get("buildings", [])) + list(impacts.get("roads", [])):
                    db.add(
                        FloodImpact(
                            flood_layer_id=depth_layer.id,
                            feature_type=entry.get("feature_type", "building"),
                            feature_id=int(entry["id"]),
                            estimated_depth_m=float(entry.get("depth_m") or 0.0),
                            risk_level=str(entry.get("risk_level") or "none"),
                        )
                    )
            await db.commit()

# Global instance
flood_simulation_engine = FloodSimulationEngine()