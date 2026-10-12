"""Where satellite images come from.

Two interchangeable backends, same output:
  cdse - Copernicus Data Space Ecosystem (Sentinel Hub API). Needs a free account.
  pc   - Microsoft Planetary Computer. No account needed (backup).
Both deliver the same Sentinel-1 / Sentinel-2 data, warped onto our grid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Scene:
    """One satellite pass over the box (it may be made of several image slices)."""

    sensor: str                 # "S1" (radar) or "S2" (optical)
    time: datetime              # UTC time of the pass
    platform: str               # e.g. sentinel-1d
    track: int | None = None    # relative orbit (S1) / relative orbit (S2)
    direction: str | None = None  # ascending / descending (S1)
    coverage: float = 0.0       # share of the box covered by this pass (0..1)
    cloud: float | None = None  # S2: cloud % of the whole tiles
    refs: list = field(default_factory=list)  # backend-specific handles

    def describe(self) -> dict:
        d = {"sensor": self.sensor, "time": self.time.strftime("%Y-%m-%dT%H:%MZ"),
             "platform": self.platform, "coverage": round(self.coverage, 3)}
        if self.track is not None:
            d["track"] = self.track
        if self.direction:
            d["direction"] = self.direction
        if self.cloud is not None:
            d["tile_cloud_pct"] = round(self.cloud, 1)
        return d


def get_source(name: str | None = None):
    from ..config import CDSE_CLIENT_ID, CDSE_CLIENT_SECRET, DATA_SOURCE

    name = (name or DATA_SOURCE or "pc").lower()
    if name == "cdse":
        if not (CDSE_CLIENT_ID and CDSE_CLIENT_SECRET):
            raise RuntimeError("DATA_SOURCE=cdse needs CDSE_CLIENT_ID and CDSE_CLIENT_SECRET in the .env file.")
        from .cdse import CdseSource
        return CdseSource()
    if name == "pc":
        from .pc import PlanetarySource
        return PlanetarySource()
    raise ValueError(f"Unknown data source '{name}'. Use 'cdse' or 'pc'.")


def group_passes(entries, minutes: float = 10.0):
    """Group image slices that belong to the same satellite pass.

    `entries` is a list of (time, key, payload). Slices with the same key
    (e.g. relative orbit) taken within `minutes` of each other form one pass.
    """
    entries = sorted(entries, key=lambda e: (e[1], e[0]))
    groups = []
    for t, key, payload in entries:
        if groups and groups[-1]["key"] == key and (t - groups[-1]["last"]).total_seconds() <= minutes * 60:
            groups[-1]["items"].append(payload)
            groups[-1]["last"] = t
        else:
            groups.append({"key": key, "first": t, "last": t, "items": [payload]})
    return groups
