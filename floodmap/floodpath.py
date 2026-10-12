"""Bonus: from any point upstream, trace the flood path down the valley.

Uses only the Copernicus DEM: water flows to the lowest neighbouring cell
(D8 flow directions). From the clicked point we follow that path downhill and
list the settlements (OpenStreetMap before the flood) that sit close to the
path and low enough above it to be reached.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

import numpy as np
import geopandas as gpd
from shapely.geometry import LineString, Point, mapping

from . import dem as D
from . import osm
from .config import CACHE_DIR, PARAMS
from .grid import from_lonlat, make_grid, to_lonlat
from .names import english_name, nepali_name

HALF_DEG = 0.2          # analysis box: +- 0.2 degrees around the click (~40 km wide)
MAX_DIST_M = 1500.0     # settlement must be this close to the path ...
MAX_HEIGHT_M = 60.0     # ... and at most this high above it
BUILDING_DIST_M = 200.0  # buildings this close to the path ...
BUILDING_HEIGHT_M = 30.0  # ... and at most this high above it count as "at risk"


def _hydro_for(lon: float, lat: float):
    key = hashlib.sha1(f"{round(lon, 1)},{round(lat, 1)},{HALF_DEG}".encode()).hexdigest()[:12]
    path = CACHE_DIR / "floodpath" / f"{key}.npz"
    lon0, lat0 = round(lon, 1), round(lat, 1)
    grid = make_grid((lon0 - HALF_DEG, lat0 - HALF_DEG, lon0 + HALF_DEG, lat0 + HALF_DEG), 30)
    if path.exists():
        z = np.load(path)
        return grid, z["dem"], z["fdir"], z["acc"]
    dem = D.fetch_dem(grid)
    h = D.hydrology(dem, grid, PARAMS.stream_min_km2)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, dem=h["dem_filled"], fdir=h["fdir"], acc=h["acc"])
    return grid, h["dem_filled"], h["fdir"], h["acc"]


def trace(lon: float, lat: float, flood_date: date | None = None) -> dict:
    grid, dem, fdir, acc = _hydro_for(lon, lat)
    x, y = from_lonlat(grid.crs, lon, lat)
    r, c = grid.rowcol(x, y)
    r, c = int(r), int(c)
    if not grid.inside(r, c):
        raise ValueError("Point is outside the height data.")
    # start on the river: highest flow accumulation within ~300 m of the click
    k = 10
    win = acc[max(0, r - k):r + k + 1, max(0, c - k):c + k + 1]
    rr, cc = np.unravel_index(int(np.argmax(win)), win.shape)
    r, c = max(0, r - k) + rr, max(0, c - k) + cc
    cells = D.trace_downstream(fdir, r, c)
    if len(cells) < 3:
        raise ValueError("Could not trace a flow path from here (flat area or edge of the data).")
    rows, cols = np.array(cells).T
    xs = grid.transform.c + (cols + 0.5) * grid.res
    ys = grid.transform.f - (rows + 0.5) * grid.res
    line = LineString(np.c_[xs, ys]).simplify(grid.res)
    elev = dem[rows, cols]

    when = osm.snapshot_date(flood_date) if flood_date else osm.latest_snapshot()
    w, s = to_lonlat(grid.crs, grid.bounds[0], grid.bounds[1])
    e, n = to_lonlat(grid.crs, grid.bounds[2], grid.bounds[3])
    places = osm.fetch("places", (w, s, e, n), when).to_crs(grid.crs)
    found = []
    path_xy = np.c_[xs, ys]
    for _, row in places.iterrows():
        p = row.geometry
        d = line.distance(p)
        if d > MAX_DIST_M:
            continue
        i = int(np.argmin(np.hypot(path_xy[:, 0] - p.x, path_xy[:, 1] - p.y)))
        pr, pc = grid.rowcol(p.x, p.y)
        if not grid.inside(pr, pc):
            continue
        height = float(dem[int(pr), int(pc)] - elev[i])
        if height > MAX_HEIGHT_M:
            continue
        plon, plat = to_lonlat(grid.crs, p.x, p.y)
        found.append({"name": english_name(row) or "(unnamed)", "name_ne": nepali_name(row), "place": row.get("place"),
                      "km_downstream": round(line.project(p) / 1000.0, 1), "distance_m": round(d),
                      "height_above_path_m": round(max(0.0, height)), "lon": round(plon, 5), "lat": round(plat, 5)})
    found.sort(key=lambda f: f["km_downstream"])

    # buildings close to the path and low enough to be reached (many villages have no named point in OSM)
    at_risk = 0
    try:
        import shapely
        from scipy.spatial import cKDTree
        bx0, by0, bx1, by1 = line.buffer(BUILDING_DIST_M).bounds
        lw, ls = to_lonlat(grid.crs, bx0, by0)
        le, ln = to_lonlat(grid.crs, bx1, by1)
        bld = osm.fetch("buildings", (lw, ls, le, ln), when).to_crs(grid.crs)
        if len(bld):
            pts = bld.geometry.representative_point()
            near = shapely.distance(line, pts.values) <= BUILDING_DIST_M
            if near.any():
                bxy = np.c_[pts.x.to_numpy()[near], pts.y.to_numpy()[near]]
                _, idx = cKDTree(path_xy).query(bxy)
                br, bc = grid.rowcol(bxy[:, 0], bxy[:, 1])
                ok = grid.inside(br, bc)
                h = np.full(len(bxy), np.inf)
                h[ok] = dem[br[ok], bc[ok]] - elev[idx[ok]]
                at_risk = int((h <= BUILDING_HEIGHT_M).sum())
    except Exception:  # noqa: BLE001 - the count is extra information only
        at_risk = None
    lonlat = [to_lonlat(grid.crs, px, py) for px, py in line.coords]
    feats = [{"type": "Feature", "properties": {"kind": "path"}, "geometry": mapping(LineString(lonlat))}]
    feats += [{"type": "Feature", "properties": f, "geometry": mapping(Point(f["lon"], f["lat"]))} for f in found]
    return {"start": {"lon": lon, "lat": lat}, "length_km": round(line.length / 1000.0, 1),
            "settlements": found, "buildings_at_risk": at_risk, "osm_snapshot": when.isoformat(),
            "geojson": {"type": "FeatureCollection", "features": feats}}


if __name__ == "__main__":
    print(json.dumps({k: v for k, v in trace(85.37, 28.27, date(2026, 8, 26)).items() if k != "geojson"}, indent=1, ensure_ascii=False))
