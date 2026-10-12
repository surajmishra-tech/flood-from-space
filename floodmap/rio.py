"""Small raster helpers: read any cloud GeoTIFF into our grid, write GeoTIFFs."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

from .grid import Grid

# Settings that make reading cloud-optimised GeoTIFFs over HTTP fast and robust.
GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff,.TIF",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="2",
    VSI_CACHE="TRUE",
)


def read_to_grid(href: str, grid: Grid, resampling=Resampling.bilinear,
                 nodata=None, scale: float = 1.0, offset: float = 0.0,
                 retries: int = 3) -> np.ndarray:
    """Read one band of a (remote) raster, warped onto `grid`. Missing = NaN."""
    last_err = None
    for attempt in range(retries):
        try:
            return _read_to_grid(href, grid, resampling, nodata, scale, offset)
        except rasterio.errors.RasterioIOError as err:  # network hiccup
            last_err = err
            time.sleep(2 * (attempt + 1))
    raise last_err


def _read_to_grid(href, grid, resampling, nodata, scale, offset):
    out = np.full(grid.shape, np.nan, dtype="float32")
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
        left, bottom, right, top = transform_bounds(grid.crs, src.crs, *grid.bounds, densify_pts=21)
        src_res = abs(src.res[0])
        if src.crs.is_geographic:
            src_res_m = src_res * 111_320 * np.cos(np.radians((bottom + top) / 2))
        else:
            src_res_m = src_res
        m = 3 * src_res
        win = from_bounds(left - m, bottom - m, right + m, top + m, src.transform)
        try:
            win = win.intersection(Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            return out  # no overlap
        win = win.round_offsets().round_lengths()
        if win.width < 1 or win.height < 1:
            return out
        factor = max(1, int(grid.res / src_res_m))
        out_h, out_w = max(1, int(win.height // factor)), max(1, int(win.width // factor))
        arr = src.read(1, window=win, out_shape=(out_h, out_w),
                       resampling=Resampling.average if factor > 1 else Resampling.nearest).astype("float32")
        nod = src.nodata if nodata is None else nodata
        if nod is not None:
            arr[arr == nod] = np.nan
        if scale != 1.0 or offset != 0.0:
            arr = arr * scale + offset
        win_tr = src.window_transform(win) * Affine.scale(win.width / out_w, win.height / out_h)
        reproject(arr, out, src_transform=win_tr, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=np.nan,
                  resampling=resampling)
    return out


def fill_from(base: np.ndarray, new: np.ndarray) -> np.ndarray:
    """Mosaic: keep `base`, fill its holes (NaN) from `new`."""
    if base is None:
        return new
    hole = np.isnan(base)
    base[hole] = new[hole]
    return base


def resample(arr: np.ndarray, src: Grid, dst: Grid, resampling=Resampling.bilinear) -> np.ndarray:
    out = np.full(dst.shape, np.nan, dtype="float32")
    reproject(arr.astype("float32"), out, src_transform=src.transform, src_crs=src.crs,
              src_nodata=np.nan, dst_transform=dst.transform, dst_crs=dst.crs,
              dst_nodata=np.nan, resampling=resampling)
    return out


def write_tif(path: Path, arr: np.ndarray, grid: Grid, nodata=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(driver="GTiff", width=grid.width, height=grid.height, count=1,
                   dtype=arr.dtype, crs=grid.crs, transform=grid.transform,
                   compress="deflate", tiled=True, nodata=nodata)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)


def read_tif(path: Path) -> tuple[np.ndarray, Grid]:
    with rasterio.open(path) as src:
        arr = src.read(1)
        grid = Grid(src.crs.to_string(), src.transform, src.width, src.height)
    return arr, grid
