"""Settings, paths and secrets.

Secrets (API keys) live in the .env file, which is never pushed to GitHub.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RUNS_DIR = ROOT / "runs"
# The main saved result: opened when a link names none, and preferred by the danger check for its flood
FEATURED_RUN = os.getenv("FEATURED_RUN", "fb7507902568").strip()
CACHE_DIR = ROOT / "cache"

DATA_SOURCE = os.getenv("DATA_SOURCE", "pc").strip().lower()
CDSE_CLIENT_ID = os.getenv("CDSE_CLIENT_ID", "").strip()
CDSE_CLIENT_SECRET = os.getenv("CDSE_CLIENT_SECRET", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

# Bump when the method changes, so old cached runs are not reused.
VERSION = "0.1"


@dataclass(frozen=True)
class Params:
    """Method settings.

    These are physically motivated defaults. They are NOT tuned to match the
    Copernicus EMS maps - those are only used afterwards, to check results.
    """

    # Valley mask: a flood can't reach ground that is high above a river or steep.
    hand_max_m: float = 45.0          # max height above the nearest river (HAND)
    slope_max_deg: float = 35.0       # 30 m DEM slopes are steep even on narrow valley floors
    stream_min_km2: float = 5.0       # upstream area that counts as a river

    # Radar change detection.
    s1_pre_images: int = 3            # "before" reference = average of up to 3 passes (less noise)
    s1_post_images: int = 2           # "after" = average of up to 2 passes ...
    s1_post_span_days: int = 13       # ... taken within 13 days of the first one
    radar_z: float = 1.5              # change must stand out 1.5 robust sigmas above normal
    change_min_db: float = 3.0        # new water must be at least this much darker
    water_db_default: float = -16.0   # VV below this = water (if the auto value fails)
    water_db_range: tuple = (-20.0, -13.0)
    enl: float = 4.4                  # "equivalent number of looks" of Sentinel-1 GRD

    # Radar viewing geometry (Sentinel-1 IW, middle of the swath).
    incidence_deg: float = 39.0
    layover_margin_deg: float = 4.0
    shadow_margin_deg: float = 3.0

    # Cleaning: drop tiny patches.
    min_patch_m2: float = 3000.0

    # Damage and cut-off analysis.
    bridge_hit_m: float = 30.0        # a bridge counts as hit if the flood is this close
    snap_settlement_m: float = 1500.0 # village must be this close to a road
    snap_target_m: float = 1000.0     # town / hospital must be this close to a road
    extend_km: float = 15.0           # road network is loaded this far around the box
    detour_ratio: float = 1.5         # "detour" = route at least 1.5x longer ...
    detour_min_km: float = 3.0        # ... and at least 3 km longer
    nearby_buildings_m: float = 750.0 # buildings within this distance belong to a village

    # Image search windows.
    s1_window_days: int = 36
    s2_before_days: int = 60
    s2_after_days: int = 45
    s2_min_clear: float = 0.5         # at least half the valley must be cloud-free


PARAMS = Params()
