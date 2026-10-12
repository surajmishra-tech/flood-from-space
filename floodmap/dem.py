"""Height data (Copernicus DEM GLO-30) and everything we derive from it.

* slope            - floods and debris settle on gentle ground
* HAND             - Height Above Nearest Drainage: how high a pixel sits above the river
* flow direction   - which way water runs (used for the bonus flood-path tracer)
* radar blind spots - slopes the radar can't see well (layover / shadow)
"""
from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds

from .config import CACHE_DIR
from .grid import Grid
from .rio import fill_from, read_to_grid, read_tif, write_tif

# Free public copy of Copernicus DEM GLO-30 (AWS open data, no account needed).
AWS_DEM = "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
D8_DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)  # N, NE, E, SE, S, SW, W, NW

# pysheds 0.5 still calls np.in1d, which NumPy 2.4 removed.
if not hasattr(np, "in1d"):
    np.in1d = lambda ar1, ar2, assume_unique=False, invert=False: np.isin(
        np.ravel(ar1), ar2, assume_unique=assume_unique, invert=invert)
D8_STEPS = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}


def _tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def fetch_dem(dem_grid: Grid) -> np.ndarray:
    """Copernicus DEM (metres) on `dem_grid`. Cached on disk."""
    key = hashlib.sha1(repr(dem_grid.to_dict()).encode()).hexdigest()[:16]
    path = CACHE_DIR / "dem" / f"{key}.tif"
    if path.exists():
        return read_tif(path)[0]
    w, s, e, n = transform_bounds(dem_grid.crs, "EPSG:4326", *dem_grid.bounds, densify_pts=21)
    out = None
    for lat in range(math.floor(s), math.floor(n) + 1):
        for lon in range(math.floor(w), math.floor(e) + 1):
            try:
                arr = read_to_grid(AWS_DEM.format(name=_tile_name(lat, lon)), dem_grid, Resampling.bilinear)
            except rasterio.errors.RasterioIOError:
                continue  # no tile here (open sea)
            out = fill_from(out, arr)
    if out is None:
        raise RuntimeError("No Copernicus DEM data found for this area.")
    out = np.where(np.isnan(out), 0.0, out).astype("float32")  # sea level where tiles are missing
    write_tif(path, out, dem_grid)
    return out


def slope_deg(dem: np.ndarray, res: float) -> np.ndarray:
    gy, gx = np.gradient(dem.astype("float64"), res)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")


def hillshade(dem: np.ndarray, res: float, azimuth=315.0, altitude=45.0) -> np.ndarray:
    gy, gx = np.gradient(dem.astype("float64"), res)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(azimuth), np.radians(altitude)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1).astype("float32")


def satellite_heading(direction: str, lat: float) -> float:
    """Approximate ground-track heading of Sentinel-1 (degrees from north)."""
    inc = math.radians(98.18)  # orbit inclination
    a = math.degrees(math.asin(max(-1.0, min(1.0, math.cos(inc) / math.cos(math.radians(lat))))))
    return (a - 3.0) % 360 if direction == "ascending" else (180.0 - a + 3.0) % 360


def radar_blind_mask(dem: np.ndarray, res: float, direction: str, lat: float, params) -> np.ndarray:
    """True where the radar can't see the ground properly (layover or shadow).

    Sentinel-1 looks to the right of its flight path. Slopes that face the
    satellite more steeply than the incidence angle "lay over"; slopes facing
    away more steeply than (90 - incidence) are in shadow.
    """
    gy, gx = np.gradient(dem.astype("float64"), res)
    dzdx, dzdy = gx, -gy  # east, north
    look = math.radians(satellite_heading(direction, lat) + 90.0)
    ux, uy = math.sin(look), math.cos(look)  # horizontal direction from satellite to ground
    rise = np.degrees(np.arctan(dzdx * ux + dzdy * uy))  # >0: ground rises away from the satellite
    inc = params.incidence_deg
    layover = rise > (inc - params.layover_margin_deg)
    shadow = -rise > (90.0 - inc - params.shadow_margin_deg)
    return layover | shadow


def hydrology(dem: np.ndarray, dem_grid: Grid, stream_min_km2: float,
              extra_channels: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Flow direction, flow accumulation and HAND (pysheds)."""
    from pysheds.grid import Grid as SGrid
    from pysheds.sview import Raster

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "dem.tif"
        write_tif(p, dem.astype("float32"), dem_grid, nodata=-9999.0)
        sg = SGrid.from_raster(str(p))
        d = sg.read_raster(str(p))
        pit = sg.fill_pits(d)
        flooded = sg.fill_depressions(pit)
        inflated = sg.resolve_flats(flooded)
        fdir = sg.flowdir(inflated, dirmap=D8_DIRMAP)
        acc = sg.accumulation(fdir, dirmap=D8_DIRMAP)
        cells = stream_min_km2 * 1e6 / (dem_grid.res ** 2)
        channels = np.asarray(acc) > cells
        if extra_channels is not None:
            channels |= extra_channels
        mask = Raster(channels, viewfinder=acc.viewfinder)
        hand = sg.compute_hand(fdir, inflated, mask, dirmap=D8_DIRMAP)
        hand = np.asarray(hand, dtype="float32")
        hand[(hand < 0) | ~np.isfinite(hand)] = np.nan
        return {"hand": hand, "fdir": np.asarray(fdir).astype("int32"),
                "acc": np.asarray(acc).astype("float32"), "dem_filled": np.asarray(inflated, dtype="float32"),
                "channels": channels}


def trace_downstream(fdir: np.ndarray, row: int, col: int, max_steps: int = 20000) -> list[tuple[int, int]]:
    """Follow the D8 flow directions from a cell until we leave the grid or hit a pit."""
    h, w = fdir.shape
    path, seen = [], set()
    r, c = row, col
    for _ in range(max_steps):
        if not (0 <= r < h and 0 <= c < w) or (r, c) in seen:
            break
        path.append((r, c))
        seen.add((r, c))
        step = D8_STEPS.get(int(fdir[r, c]))
        if step is None:
            break
        r, c = r + step[0], c + step[1]
    return path
