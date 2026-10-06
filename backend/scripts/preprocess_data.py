"""
FLOODGUARD AI — offline data preprocessing.

Turns the heavyweight raw sources into small, project-scoped artefacts that
the API and frontend can serve directly. Run once; the outputs are committed
(or bind-mounted) so neither the IMD national NetCDF nor the 8 MB OSM extract
is ever loaded by a browser.

    IMD  NetCDF (national, 0.25 deg, daily)
      -> data/rainfall/imd/processed/mumbai_rainfall.json

    OSM  overpass turbo.geojson (9k features, whole extract)
      -> data/osm/{buildings,roads,waterways}.geojson

    DEM  terrain GeoTIFF -> validated clip of the study area

Usage:
    python -m scripts.preprocess_data [--skip-dem] [--skip-imd] [--skip-osm]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
TERRAIN_DIR = DATA / "terrain"
OSM_DIR = DATA / "osm"
IMD_RAW = DATA / "IMD"
IMD_PROCESSED = DATA / "rainfall" / "imd" / "processed"
RAW_SOURCE = DATA / "overpass turbo.geojson"

# ── Study area (PRD §1) ─────────────────────────────────────────────────────
SOUTH, WEST, NORTH, EAST = 19.0500, 72.8500, 19.0900, 72.9000
REP_LAT, REP_LON = 19.0713, 72.8830

# Building-height estimation (PRD §8). Every value here is a VISUALISATION
# ASSUMPTION, never an authoritative measurement.
LEVEL_HEIGHT_M = 3.0
# Fallback storey counts keyed by OSM `building=*` value.
TYPICAL_LEVELS = {
    "house": 2, "residential": 4, "apartments": 6, "dormitory": 5,
    "commercial": 4, "retail": 2, "office": 8, "industrial": 3,
    "warehouse": 2, "factory": 3, "school": 3, "university": 4,
    "college": 4, "hospital": 6, "garage": 1, "garages": 1,
    "shed": 1, "hut": 1, "cabin": 1, "bungalow": 2, "hotel": 6,
    "cathedral": 3, "church": 2, "temple": 1, "mosque": 2, "kindergarten": 2,
    "train_station": 3, "transportation": 3,
}
DEFAULT_LEVELS = 3


def log(msg: str) -> None:
    print(f"[preprocess] {msg}", flush=True)


# ── Terrain ──────────────────────────────────────────────────────────────────

def validate_dem() -> None:
    """Report the terrain raster's real properties instead of assuming them."""
    import rasterio

    # Files beginning with "_" are intermediate downloads (whole 1-degree
    # source tiles); the study-area clip is the one the app actually serves.
    candidates = [p for p in sorted(TERRAIN_DIR.glob("*.tif")) if not p.name.startswith("_")]
    if not candidates:
        log("DEM: no study-area clip found - run terrain provisioning first")
        return
    path = min(candidates, key=lambda p: p.stat().st_size)
    with rasterio.open(path) as src:
        data = src.read(1).astype("float64")
        nodata = src.nodata
        valid = data if nodata is None else data[data != nodata]
        log(f"DEM: {path.name}")
        log(f"  CRS      {src.crs}")
        log(f"  size     {src.width} x {src.height}")
        log(f"  bounds   {src.bounds}")
        log(f"  res      {src.res[0]:.8f} deg (~{src.res[0] * 111320:.0f} m)")
        log(f"  nodata   {nodata}")
        if valid.size:
            land = valid[valid > -5]
            log(f"  elev     min {valid.min():.2f} m  max {valid.max():.2f} m  mean {valid.mean():.2f} m")
            if land.size:
                log(f"  land     min {land.min():.2f} m  max {land.max():.2f} m  mean {land.mean():.2f} m")
            log(f"  pixels   {valid.size} valid / {data.size} total")
            if int((valid <= -5).sum()):
                log(f"  note     {(valid <= -5).sum()} coastal/sea cells (<= -5 m) are nodata-flagged")


def _resolve(ds: Any, wanted_lower: str) -> str | None:
    """Case-insensitive variable lookup.

    IMD publishes coordinate variables as LATITUDE/LONGITUDE/TIME but the
    dimension keys come back lowercased, so a direct ds[name] lookup fails.
    """
    for actual in ds.variables:
        if actual.lower() == wanted_lower:
            return actual
    return None


# ── IMD historical rainfall (PRD §4) ─────────────────────────────────────────

def _pick_dim(varname: str, coords: Any, candidates: tuple[str, ...]) -> str | None:
    low = varname.lower()
    for cand in candidates:
        if cand in low:
            return cand
    return None


def preprocess_imd() -> None:
    """Extract the Mumbai cell series out of the national IMD NetCDF.

    Variable and dimension names are discovered at runtime — the IMD files have
    changed names/format between releases, so nothing is hardcoded.
    """
    from netCDF4 import Dataset, num2date

    files = sorted(IMD_RAW.glob("*.nc"))
    if not files:
        log("IMD: no .nc files found")
        return

    IMD_PROCESSED.mkdir(parents=True, exist_ok=True)

    years: list[dict[str, Any]] = []

    for path in files:
        with Dataset(path) as ds:
            # Discover coordinate variables case-insensitively: the IMD files
            # store them as LATITUDE / LONGITUDE / TIME, not lowercase.
            lat_dim = _resolve(ds, "latitude") or _resolve(ds, "lat")
            lon_dim = _resolve(ds, "longitude") or _resolve(ds, "lon")
            time_dim = _resolve(ds, "time") or _resolve(ds, "valid_time")
            if not (lat_dim and lon_dim and time_dim):
                log(f"IMD: {path.name} skipped (vars={list(ds.variables)})")
                continue

            lats = ds[lat_dim][:]
            lons = ds[lon_dim][:]
            times = ds[time_dim]

            # Discover the rainfall variable, preferring an explicit name match.
            rain_var = None
            for vname in ds.variables:
                low = vname.lower()
                if any(k in low for k in ("rain", "precip", "rf")):
                    rain_var = vname
                    break
            if rain_var is None:
                # Fall back to the largest 3-D field that is not a coordinate.
                cands = [
                    v for v in ds.variables
                    if ds.variables[v].ndim == 3
                    and v not in {lat_dim, lon_dim, time_dim}
                ]
                if not cands:
                    log(f"IMD: {path.name} skipped (no rainfall variable)")
                    continue
                rain_var = max(cands, key=lambda v: ds.variables[v].shape[0])
            log(f"IMD: {path.name} var={rain_var} dims=({time_dim},{lat_dim},{lon_dim})")

            data = ds[rain_var][:]
            daily, cell = _extract_cell_series(data, lats, lons)

            # netCDF4's type stubs may infer a scalar datetime here even though
            # slicing the time variable returns a sequence at runtime.
            dates: Any = num2date(times[:], times.units, only_use_cftime_datetimes=False)
            years.append(
                {
                    "file": path.name,
                    "variable": rain_var,
                    "grid": {
                        "lat_min": float(lats.min()), "lat_max": float(lats.max()),
                        "lon_min": float(lons.min()), "lon_max": float(lons.max()),
                        "lat_step": float(abs(lats[1] - lats[0])),
                        "lon_step": float(abs(lons[1] - lons[0])),
                    },
                    "cell_used": cell,
                    "dates": [str(d) for d in dates],
                    "daily_mm": daily,
                }
            )

    payload = {
        "source": "IMD Gridded Rainfall",
        "source_label": "HISTORICAL / REFERENCE",
        "provider": "India Meteorological Department",
        "resolution": "0.25 deg x 0.25 deg",
        "frequency": "Daily",
        "region": "Mithi River — BKC to Kurla, Mumbai",
        "study_bbox": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "representative_point": {"lat": REP_LAT, "lon": REP_LON},
        "limitation": (
            "0.25 deg (~25 km) grid. One cell covers the entire study area, so "
            "this cannot represent street-level or catchement-scale rainfall. "
            "Use for historical context and model training only, never as a live "
            "rainfall reading."
        ),
        "years": years,
    }

    out = IMD_PROCESSED / "mumbai_rainfall.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    total_days = sum(len(y["daily_mm"]) for y in years)
    log(f"IMD: wrote {out.relative_to(REPO_ROOT)} ({out.stat().st_size // 1024} KB, {total_days} days)")
    for y in years:
        series = [v for v in y["daily_mm"] if v is not None]
        if series:
            peak = max(series)
            log(f"  {y['file']}: mean {sum(series)/len(series):.2f} mm/day, peak {peak:.1f} mm/day")


def _extract_cell_series(data: Any, lats: Any, lons: Any) -> tuple[list[float | None], dict[str, Any]]:
    """Pull the grid cell nearest the representative point.

    IMD arrays are (time, lat, lon) with ascending latitude, which matches the
    IMD spec; the nearest-cell search is done on absolute distance so it stays
    correct if that order ever changes. The chosen cell is returned so the API
    can disclose it — a 0.25 deg cell is ~25 km across, so which one was picked
    materially changes the number and must not be implicit.
    """
    i = int(abs(lats[:] - REP_LAT).argmin())
    j = int(abs(lons[:] - REP_LON).argmin())
    series = data[:, i, j]
    out: list[float | None] = []
    for v in series:
        fv = float(v)
        # IMD packs missing data as very large sentinels (e.g. 9.969e36).
        out.append(None if (math.isnan(fv) or fv > 1e5 or fv < 0) else round(fv, 2))
    cell = {
        "lat": float(lats[i]),
        "lon": float(lons[j]),
        "lat_index": i,
        "lon_index": j,
        "offset_from_study_point_deg": round(
            math.hypot(float(lats[i]) - REP_LAT, float(lons[j]) - REP_LON), 4
        ),
    }
    return out, cell


# ── OSM features (PRD §7, §8) ───────────────────────────────────────────────

def _clip_bbox(geom: dict, pad: float = 0.0) -> bool:
    """True if a geometry's first coordinate falls inside the study bbox."""
    t = geom.get("type")
    coords = geom.get("coordinates")
    pts: list[list[float]] = []
    if t == "Point" and coords:
        pts = [coords]
    elif t == "LineString" and coords:
        pts = [coords[0]]
    elif t == "MultiLineString" and coords:
        for line in coords[:1]:
            if line:
                pts = [line[0]]
                break
    elif t == "Polygon" and coords:
        pts = [coords[0][0]] if coords[0] else []
    elif t == "MultiPolygon" and coords:
        for poly in coords[:1]:
            if poly and poly[0]:
                pts = [poly[0][0]]
                break
    if not pts:
        return False
    lon, lat = pts[0][0], pts[0][1]
    return (SOUTH - pad) <= lat <= (NORTH + pad) and (WEST - pad) <= lon <= (EAST + pad)


def estimate_building_height(props: dict[str, Any]) -> tuple[float, bool, str]:
    """Return (height_m, is_estimated, method).

    Order per PRD §8: explicit `height`, then `building:levels`, then a
    type-based estimate. The method string is carried through to the UI so the
    dashboard can label the value honestly.
    """
    raw_height = props.get("height")
    if raw_height:
        cleaned = str(raw_height).split(";")[0].replace(",", ".")
        try:
            value = float("".join(ch for ch in cleaned if ch.isdigit() or ch == "."))
            if 1.0 <= value <= 400.0:
                return value, False, "osm_height_tag"
        except ValueError:
            pass

    raw_levels = props.get("building:levels") or props.get("levels")
    if raw_levels:
        cleaned = str(raw_levels).split(";")[0].replace(",", ".")
        try:
            levels = float("".join(ch for ch in cleaned if ch.isdigit() or ch == "."))
            if 1.0 <= levels <= 120.0:
                return round(levels * LEVEL_HEIGHT_M, 1), False, "building_levels_x3m"
        except ValueError:
            pass

    building = str(props.get("building") or "").lower()
    levels = TYPICAL_LEVELS.get(building, DEFAULT_LEVELS)
    return round(levels * LEVEL_HEIGHT_M, 1), True, f"estimated_{building or 'default'}_levels"


def preprocess_osm() -> None:
    """Split the OSM extract into map-ready layers with honest height metadata."""
    if not RAW_SOURCE.exists():
        log(f"OSM: source not found at {RAW_SOURCE}")
        return

    doc = json.loads(RAW_SOURCE.read_text(encoding="utf-8"))
    features = doc.get("features", [])
    log(f"OSM: {len(features)} features from {RAW_SOURCE.name}")

    OSM_DIR.mkdir(parents=True, exist_ok=True)

    buildings: list[dict] = []
    roads: list[dict] = []
    waterways: list[dict] = []
    height_methods: dict[str, int] = defaultdict(int)

    for feat in features:
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        if not _clip_bbox(geom):
            continue

        if props.get("building"):
            height_m, estimated, method = estimate_building_height(props)
            height_methods[method] += 1
            buildings.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "osm_id": (props.get("@id") or "").rsplit("/", 1)[-1] or None,
                    "name": props.get("name"),
                    "building_type": props.get("building"),
                    "height_m": height_m,
                    "height_is_estimated": estimated,
                    "height_method": method,
                    "levels": props.get("building:levels"),
                },
            })
        elif props.get("highway"):
            roads.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "osm_id": (props.get("@id") or "").rsplit("/", 1)[-1] or None,
                    "name": props.get("name"),
                    "road_type": props.get("highway"),
                },
            })
        elif props.get("waterway") or props.get("natural") == "water":
            waterways.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "osm_id": (props.get("@id") or "").rsplit("/", 1)[-1] or None,
                    "name": props.get("name"),
                    "waterway": props.get("waterway"),
                    "is_river": props.get("waterway") == "river",
                },
            })

    def _write(name: str, feats: list[dict], extra: dict | None = None) -> None:
        payload = {
            "type": "FeatureCollection",
            "source": "OpenStreetMap contributors (ODbL 1.0)",
            "source_label": "TERRAIN / OSM",
            "bbox": [WEST, SOUTH, EAST, NORTH],
            "features": feats,
            **(extra or {}),
        }
        path = OSM_DIR / name
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        log(f"  {name}: {len(feats)} features, {path.stat().st_size // 1024} KB")

    log("OSM: writing layers")
    _write("buildings.geojson", buildings, {
        "height_estimate_method": (
            "Explicit OSM `height` when present, else building:levels x 3 m, "
            "else a type-based storey estimate. Values flagged "
            "height_is_estimated=true are visualisation assumptions, not "
            "authoritative measurements."
        ),
    })
    _write("roads.geojson", roads)
    _write("waterways.geojson", waterways)

    rivers = sum(1 for w in waterways if w["properties"]["is_river"])
    log(f"  rivers: {rivers}")
    log("  building height methods:")
    for method, count in sorted(height_methods.items(), key=lambda kv: -kv[1]):
        flag = "ESTIMATED" if method.startswith("estimated") else "measured"
        log(f"    {method:<40} {count:>5}  ({flag})")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="FLOODGUARD AI data preprocessing")
    parser.add_argument("--skip-dem", action="store_true")
    parser.add_argument("--skip-imd", action="store_true")
    parser.add_argument("--skip-osm", action="store_true")
    args = parser.parse_args()

    for d in (TERRAIN_DIR, OSM_DIR, IMD_PROCESSED, DATA / "rainfall" / "forecast", DATA / "demo"):
        d.mkdir(parents=True, exist_ok=True)

    if not args.skip_dem:
        validate_dem()
    if not args.skip_imd:
        preprocess_imd()
    if not args.skip_osm:
        preprocess_osm()

    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())