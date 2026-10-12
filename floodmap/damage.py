"""Question 2: what was damaged?

We put OpenStreetMap buildings (as they were before the flood) on top of the
flood map. A building counts as HIT if the flood/debris area touches it.
(We estimate exposure - "hit" does not tell how badly it was damaged.)
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd
from scipy.ndimage import distance_transform_edt

from .grid import Grid


def distance_to_flood(mask: np.ndarray, res: float):
    """Distance (m) from every pixel to the nearest flooded pixel, plus that pixel's index."""
    if not mask.any():
        return np.full(mask.shape, np.inf, "float32"), None
    dist, idx = distance_transform_edt(~mask, return_indices=True)
    return (dist * res).astype("float32"), idx


def sample(arr: np.ndarray, grid: Grid, x, y, fill=np.inf):
    r, c = grid.rowcol(x, y)
    ok = grid.inside(r, c)
    out = np.full(np.shape(x), fill, dtype="float64")
    out[ok] = arr[r[ok], c[ok]]
    return out, r, c, ok


def building_hits(buildings: gpd.GeoDataFrame, dist: np.ndarray, idx, conf: np.ndarray,
                  grid: Grid) -> gpd.GeoDataFrame:
    """Adds columns: hit (bool), conf (1-3 of the nearest flooded pixel), dist_m."""
    b = buildings.copy()
    if b.empty:
        b["hit"], b["conf"], b["dist_m"] = [], [], []
        return b
    cen = b.geometry.representative_point()
    radius = np.sqrt(b.geometry.area.to_numpy() / np.pi)
    d, r, c, ok = sample(dist, grid, cen.x.to_numpy(), cen.y.to_numpy())
    hit = ok & (d <= radius + grid.res * 0.5)
    cf = np.zeros(len(b), "uint8")
    if idx is not None and hit.any():
        rr, cc = idx[0][r[hit], c[hit]], idx[1][r[hit], c[hit]]
        cf[hit] = conf[rr, cc]
    b["hit"] = hit
    b["conf"] = cf
    b["dist_m"] = np.where(np.isfinite(d), np.round(d, 1), -1)
    b["inside"] = ok
    return b
