"""Map pictures: PNG overlays for the web map (Web Mercator) with their lat/lon bounds."""
from __future__ import annotations

import numpy as np
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds

from .grid import Grid

FLOOD_COLORS = {  # (class, confidence) -> RGBA
    "water": (30, 110, 255),
    "debris": (214, 110, 24),
    "river": (120, 170, 230),
    "nodata": (150, 150, 150),
}
POSSIBLE_RGB = (167, 139, 250)  # "possible damage" zone (violet, lighter than the confirmed map)


def save_overlay(rgba: np.ndarray, grid: Grid, path, max_px: int = 4096) -> list:
    """Warp an RGBA image (grid CRS) to EPSG:3857 and save as PNG. Returns [[S, W], [N, E]]."""
    h, w = rgba.shape[:2]
    left, bottom, right, top = grid.bounds
    tr, width, height = calculate_default_transform(grid.crs, "EPSG:3857", w, h, left, bottom, right, top)
    scale = max(1.0, max(width, height) / max_px)
    if scale > 1:
        tr, width, height = calculate_default_transform(grid.crs, "EPSG:3857", w, h, left, bottom, right, top,
                                                        dst_width=int(width / scale), dst_height=int(height / scale))
    out = np.zeros((4, height, width), "uint8")
    for b in range(4):
        reproject(rgba[..., b], out[b], src_transform=grid.transform, src_crs=grid.crs,
                  dst_transform=tr, dst_crs="EPSG:3857", resampling=Resampling.nearest)
    Image.fromarray(np.moveaxis(out, 0, -1), "RGBA").save(path, optimize=True)
    e_left, e_top = tr.c, tr.f
    e_right, e_bottom = e_left + width * tr.a, e_top + height * tr.e
    w_, s_, e_, n_ = transform_bounds("EPSG:3857", "EPSG:4326", e_left, e_bottom, e_right, e_top)
    return [[s_, w_], [n_, e_]]


def gray(db: np.ndarray, vmin=-22.0, vmax=0.0) -> np.ndarray:
    v = np.clip((db - vmin) / (vmax - vmin), 0, 1)
    g = (v * 255).astype("uint8")
    a = np.where(np.isfinite(db), 255, 0).astype("uint8")
    return np.dstack([g, g, g, a])


def diverging(z: np.ndarray, lim=4.0) -> np.ndarray:
    """Blue (less change) -> transparent -> red (strong change). Only shows positive z."""
    v = np.clip(np.nan_to_num(z, nan=0) / lim, 0, 1)
    r = (255 * np.ones_like(v)).astype("uint8")
    g = (255 * (1 - v)).astype("uint8")
    b = (255 * (1 - v)).astype("uint8")
    a = (230 * v).astype("uint8")
    return np.dstack([r, g, b, a])


def hillshade_rgba(hs: np.ndarray) -> np.ndarray:
    g = (np.clip(np.nan_to_num(hs, nan=0.5), 0, 1) * 200 + 40).astype("uint8")
    return np.dstack([g, g, g, np.full_like(g, 255)])


def flood_rgba(cls: np.ndarray, conf: np.ndarray) -> np.ndarray:
    from .flood import DEBRIS, NEW_WATER, NO_DATA, RIVER

    out = np.zeros(cls.shape + (4,), "uint8")
    alpha = {1: 110, 2: 200, 3: 245}
    for code, key in ((NEW_WATER, "water"), (DEBRIS, "debris")):
        for c, a in alpha.items():
            m = (cls == code) & (conf == c)
            out[m] = (*FLOOD_COLORS[key], a)
    out[cls == RIVER] = (*FLOOD_COLORS["river"], 150)
    out[cls == NO_DATA] = (*FLOOD_COLORS["nodata"], 90)
    return out


def mask_rgba(mask: np.ndarray, color=(255, 255, 0), alpha=90) -> np.ndarray:
    out = np.zeros(mask.shape + (4,), "uint8")
    out[mask] = (*color, alpha)
    return out
