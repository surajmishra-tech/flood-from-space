"""Backup image source: Microsoft Planetary Computer (no account needed).

Sentinel-1: collection `sentinel-1-rtc` = Sentinel-1 GRD, already radiometrically
terrain-corrected (gamma0, 10 m). Sentinel-2: collection `sentinel-2-l2a`.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import planetary_computer
import pystac_client
from rasterio.enums import Resampling
from shapely.geometry import box, shape
from shapely.ops import unary_union

from ..grid import Grid
from ..rio import fill_from, read_to_grid
from . import Scene, group_passes

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def _coverage(items, bbox) -> float:
    aoi = box(*bbox)
    union = unary_union([shape(it.geometry) for it in items])
    return float(union.intersection(aoi).area / aoi.area)


def _parse_time(item) -> datetime:
    return datetime.fromisoformat(item.properties["datetime"].replace("Z", "+00:00"))


class PlanetarySource:
    name = "pc"
    label = "Microsoft Planetary Computer (Sentinel-1 RTC / Sentinel-2 L2A)"

    def __init__(self):
        self.client = pystac_client.Client.open(STAC_URL)

    # ---------- Sentinel-1 radar ----------
    def find_s1(self, bbox, start: datetime, end: datetime) -> list[Scene]:
        items = list(self.client.search(collections=["sentinel-1-rtc"], bbox=list(bbox),
                                        datetime=f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}").items())
        entries = []
        for it in items:
            p = it.properties
            if p.get("sar:instrument_mode", "IW") != "IW" or "VV" not in p.get("sar:polarizations", ["VV"]):
                continue
            key = (p.get("sat:relative_orbit"), p.get("sat:orbit_state"))
            entries.append((_parse_time(it), key, it))
        scenes = []
        for g in group_passes(entries):
            first = g["items"][0]
            scenes.append(Scene("S1", g["first"], first.properties.get("platform", "sentinel-1"),
                                track=g["key"][0], direction=g["key"][1],
                                coverage=_coverage(g["items"], bbox), refs=g["items"]))
        return sorted(scenes, key=lambda s: s.time)

    def read_s1(self, scene: Scene, grid: Grid) -> dict[str, np.ndarray]:
        out = {}
        for pol in ("vv", "vh"):
            arr = None
            for it in scene.refs:
                if pol not in it.assets:
                    continue
                href = planetary_computer.sign(it.assets[pol].href)
                arr = fill_from(arr, read_to_grid(href, grid, Resampling.bilinear, nodata=-32768))
            if arr is not None:
                arr[arr <= 0] = np.nan
            out[pol] = arr
        return out

    # ---------- Sentinel-2 optical ----------
    def find_s2(self, bbox, start: datetime, end: datetime, max_cloud: float = 95) -> list[Scene]:
        items = list(self.client.search(collections=["sentinel-2-l2a"], bbox=list(bbox),
                                        datetime=f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
                                        query={"eo:cloud_cover": {"lt": max_cloud}}).items())
        entries = [(_parse_time(it), it.properties.get("sat:relative_orbit"), it) for it in items]
        scenes = []
        for g in group_passes(entries, minutes=30):
            cl = [it.properties.get("eo:cloud_cover", 100) for it in g["items"]]
            scenes.append(Scene("S2", g["first"], g["items"][0].properties.get("platform", "sentinel-2"),
                                track=g["key"], coverage=_coverage(g["items"], bbox),
                                cloud=float(np.mean(cl)), refs=g["items"]))
        return sorted(scenes, key=lambda s: s.time)

    def read_s2(self, scene: Scene, grid: Grid, bands=("B03", "B04", "B08", "B11", "SCL")) -> dict[str, np.ndarray]:
        out = {}
        for band in bands:
            arr = None
            for it in scene.refs:
                href = planetary_computer.sign(it.assets[band].href)
                if band == "SCL":
                    a = read_to_grid(href, grid, Resampling.nearest, nodata=0)
                else:
                    # Since processing baseline 04.00 (2022) the stored values carry a +1000 offset.
                    baseline = float(it.properties.get("s2:processing_baseline", "0") or 0)
                    off = -1000.0 if baseline >= 4.0 else 0.0
                    a = read_to_grid(href, grid, Resampling.bilinear, nodata=0,
                                     scale=1e-4, offset=off * 1e-4)
                arr = fill_from(arr, a)
            out[band] = arr
        return out
