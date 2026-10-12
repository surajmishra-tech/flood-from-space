"""The analysis grid: a raster grid in local UTM metres that covers the user's box."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.warp import transform_bounds


@dataclass
class Grid:
    crs: str
    transform: Affine
    width: int
    height: int

    @property
    def res(self) -> float:
        return self.transform.a

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(left, bottom, right, top) in the grid CRS."""
        left, top = self.transform.c, self.transform.f
        return left, top - self.height * self.res, left + self.width * self.res, top

    def rowcol(self, x, y):
        """Grid row/col for map coordinates (may fall outside the grid)."""
        x = np.asarray(x, dtype="float64")
        y = np.asarray(y, dtype="float64")
        col = np.floor((x - self.transform.c) / self.res).astype("int64")
        row = np.floor((self.transform.f - y) / self.res).astype("int64")
        return row, col

    def inside(self, row, col):
        return (row >= 0) & (row < self.height) & (col >= 0) & (col < self.width)

    def coarser(self, res: float, margin_m: float = 0.0) -> "Grid":
        """A grid with the same CRS, a different pixel size and an optional margin."""
        left, bottom, right, top = self.bounds
        left = math.floor((left - margin_m) / res) * res
        bottom = math.floor((bottom - margin_m) / res) * res
        right = math.ceil((right + margin_m) / res) * res
        top = math.ceil((top + margin_m) / res) * res
        return Grid(self.crs, Affine(res, 0, left, 0, -res, top),
                    int(round((right - left) / res)), int(round((top - bottom) / res)))

    def to_dict(self) -> dict:
        return {"crs": self.crs, "transform": list(self.transform)[:6],
                "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, d: dict) -> "Grid":
        return cls(d["crs"], Affine(*d["transform"]), d["width"], d["height"])


def utm_crs(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def bbox_area_km2(bbox) -> float:
    w, s, e, n = bbox
    crs = utm_crs((w + e) / 2, (s + n) / 2)
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, w, s, e, n, densify_pts=21)
    return (right - left) * (top - bottom) / 1e6


def auto_res(bbox) -> int:
    """Pick a pixel size so that one run stays fast (max ~6 million pixels)."""
    area = bbox_area_km2(bbox)
    if area <= 600:
        return 10
    if area <= 2400:
        return 20
    if area <= 5400:
        return 30
    raise ValueError(f"The box is {area:,.0f} km². Please draw a smaller box (max 5,400 km²).")


def make_grid(bbox, res: float | None = None) -> Grid:
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
        raise ValueError("Box must be west, south, east, north in degrees, with west < east and south < north.")
    res = res or auto_res(bbox)
    crs = utm_crs((w + e) / 2, (s + n) / 2)
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, w, s, e, n, densify_pts=21)
    left, bottom = math.floor(left / res) * res, math.floor(bottom / res) * res
    right, top = math.ceil(right / res) * res, math.ceil(top / res) * res
    return Grid(crs, Affine(res, 0, left, 0, -res, top),
                int(round((right - left) / res)), int(round((top - bottom) / res)))


def to_lonlat(crs: str, x, y):
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return tr.transform(x, y)


def from_lonlat(crs: str, lon, lat):
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return tr.transform(lon, lat)
