"""Question 1: where did the flood hit?

RADAR (Sentinel-1), track by track:
  * clean the radar "speckle" (Lee filter)
  * "before" = average of up to 3 passes, "after" = average of up to 2 passes
    on the SAME track (averaging removes much of the random noise)
  * change = |after - before| in VV + VH (decibels), smoothed over ~50 m
  * score z = how many "normal noise levels" this change stands out
  * NEW WATER = first "after" image is water (very dark) where it wasn't before
OPTICAL (Sentinel-2), only where both images are cloud-free:
  * plants destroyed (NDVI drop), ground turned bright/bare, or new water (MNDWI)
Only the valley floor is judged: low above the river (HAND) and not too steep.
Radar blind spots (layover / shadow) are skipped.

Confidence:
  HIGH   - radar and optical agree
  MEDIUM - only one sensor could look and it sees the flood
  LOW    - radar sees change but a clear optical image does not
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import uniform_filter
from skimage.filters import threshold_otsu
from skimage.morphology import remove_small_holes, remove_small_objects

from .config import PARAMS

NONE, NEW_WATER, DEBRIS, RIVER, NO_DATA = 0, 1, 2, 3, 255
LOW, MEDIUM, HIGH = 1, 2, 3
CLEAR_SCL = (4, 5, 6, 7)  # Sentinel-2 scene classes: vegetation, bare, water, unclassified


@dataclass
class TrackStack:
    """All radar images of one track: several before, one or two after."""

    track: int
    direction: str
    pre: list           # Scenes, newest first
    post: list          # Scenes, oldest first
    pre_vv: list        # linear arrays
    pre_vh: list
    post_vv: list
    post_vh: list
    blind: np.ndarray   # True = layover / shadow


@dataclass
class FloodResult:
    cls: np.ndarray            # NONE / NEW_WATER / DEBRIS / RIVER / NO_DATA
    conf: np.ndarray           # 0 / LOW / MEDIUM / HIGH
    valley: np.ndarray
    score: np.ndarray          # radar change score (z)
    stats: dict = field(default_factory=dict)


def lee_filter(img: np.ndarray, size: int, enl: float) -> np.ndarray:
    """Classic Lee speckle filter (NaN-aware)."""
    valid = np.isfinite(img)
    x = np.where(valid, img, 0.0).astype("float64")
    w = uniform_filter(valid.astype("float64"), size)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = uniform_filter(x, size) / w
        sq = uniform_filter(x * x, size) / w
        var = np.maximum(sq - mean * mean, 0)
        k = np.clip(1.0 - (1.0 / enl) / (var / (mean * mean)), 0.0, 1.0)
        out = mean + k * (x - mean)
    out[~valid] = np.nan
    return out.astype("float32")


def nan_smooth(img: np.ndarray, size: int) -> np.ndarray:
    valid = np.isfinite(img)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = uniform_filter(np.where(valid, img, 0.0), size) / uniform_filter(valid.astype("float64"), size)
    out[~valid] = np.nan
    return out.astype("float32")


def to_db(lin: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return (10.0 * np.log10(lin)).astype("float32")


def mean_linear(arrays: list[np.ndarray]) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels are fine
        return np.nanmean(np.stack(arrays), axis=0).astype("float32")


def robust_stats(x: np.ndarray) -> tuple[float, float]:
    med = float(np.median(x))
    return med, float(1.4826 * np.median(np.abs(x - med))) or 1.0


def water_threshold(values: np.ndarray, params=PARAMS) -> tuple[float, str]:
    """Automatic water threshold (Otsu) on VV dB, kept inside a safe range."""
    v = values[np.isfinite(values)]
    lo, hi = params.water_db_range
    if v.size >= 500:
        t = float(threshold_otsu(np.clip(v, -35, 5)))
        dark = (v < t).mean()
        if lo <= t <= hi and 0.002 <= dark <= 0.6:
            return t, "otsu"
    return params.water_db_default, "default"


def track_change(ts: TrackStack, valley: np.ndarray, res: float, params=PARAMS) -> dict:
    size = 5 if res <= 10 else 3
    lee = lambda a: lee_filter(a, size, params.enl)  # noqa: E731
    ref_vv = to_db(mean_linear([lee(a) for a in ts.pre_vv]))
    ref_vh = to_db(mean_linear([lee(a) for a in ts.pre_vh]))
    post_vv = to_db(mean_linear([lee(a) for a in ts.post_vv]))
    post_vh = to_db(mean_linear([lee(a) for a in ts.post_vh]))
    first_vv = to_db(lee(ts.post_vv[0]))
    valid = (np.isfinite(ref_vv) & np.isfinite(ref_vh) & np.isfinite(post_vv) & np.isfinite(post_vh)
             & np.isfinite(first_vv) & ~ts.blind)
    sel = valid & valley
    info = {"track": ts.track, "direction": ts.direction,
            "before": [s.time.strftime("%Y-%m-%d") for s in ts.pre],
            "after": [s.time.strftime("%Y-%m-%d") for s in ts.post],
            "valley_seen_pct": round(100.0 * float(sel.sum() / max(1, valley.sum())), 1)}
    empty = np.zeros(valley.shape, bool)
    if sel.sum() < 200:
        info["note"] = "too few valid valley pixels"
        return {"valid": sel, "z": np.full(valley.shape, np.nan, "float32"), "new_water": empty,
                "water_pre": empty, "water_post": empty, "d_vv": post_vv - ref_vv,
                "pre_vv": ref_vv, "post_vv": first_vv, "info": info}
    # Remove the overall shift between dates (season, soil moisture, calibration).
    d_vv, d_vh = post_vv - ref_vv, post_vh - ref_vh
    shift_vv, shift_vh = float(np.median(d_vv[sel])), float(np.median(d_vh[sel]))
    d_vv, d_vh = d_vv - shift_vv, d_vh - shift_vh
    mag = nan_smooth(np.abs(d_vv) + np.abs(d_vh), size)
    med, sig = robust_stats(mag[sel])
    z = (mag - med) / sig
    z[~valid] = np.nan

    t_w, how = water_threshold(np.concatenate([ref_vv[sel], first_vv[sel]]), params)
    water_pre = valid & (ref_vv < t_w)
    water_post = valid & (first_vv < t_w)
    d1 = first_vv - ref_vv - shift_vv
    new_water = water_post & ~water_pre & (d1 < -params.change_min_db) & sel
    info.update({"shift_db": [round(shift_vv, 2), round(shift_vh, 2)],
                 "noise_db": round(sig, 2), "water_threshold_db": round(t_w, 2),
                 "water_threshold_method": how})
    return {"valid": sel, "z": z, "new_water": new_water, "water_pre": water_pre & valley,
            "water_post": water_post & valley, "d_vv": d_vv, "pre_vv": ref_vv, "post_vv": first_vv,
            "info": info}


def optical_change(pre: dict, post: dict, valley: np.ndarray) -> dict:
    """Sentinel-2 check: new water (MNDWI), plants destroyed (NDVI drop) or ground turned bare and bright."""
    def mndwi(d):
        return (d["B03"] - d["B11"]) / (d["B03"] + d["B11"] + 1e-6)

    def ndvi(d):
        return (d["B08"] - d["B04"]) / (d["B08"] + d["B04"] + 1e-6)

    def bright(d):
        return (d["B02"] + d["B03"] + d["B04"]) / 3.0

    clear = np.isin(pre["SCL"], CLEAR_SCL) & np.isin(post["SCL"], CLEAR_SCL)
    valid = clear & valley
    with np.errstate(invalid="ignore"):
        new_water = valid & (mndwi(post) > 0.0) & (mndwi(pre) < -0.1)
        veg_loss = valid & ((ndvi(pre) - ndvi(post)) > 0.25) & (ndvi(post) < 0.3)
        bare = valid & ((bright(post) - bright(pre)) > 0.04) & (ndvi(post) < 0.3)
    return {"valid": valid, "changed": new_water | veg_loss | bare, "new_water": new_water,
            "clear_pct": round(100.0 * float(valid.sum() / max(1, valley.sum())), 1)}


def map_flood(tracks: list[dict], optical: dict | None, valley: np.ndarray, res: float,
              params=PARAMS) -> FloodResult:
    shape = valley.shape
    n_valid = np.zeros(shape, "uint8")
    any_new_water = np.zeros(shape, bool)
    river = np.zeros(shape, bool)
    zs = []
    for t in tracks:
        n_valid += t["valid"]
        any_new_water |= t["new_water"]
        river |= t["water_pre"] & t["water_post"]
        zs.append(t["z"])
    if zs:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            z = np.nanmean(np.stack(zs), axis=0).astype("float32")
    else:
        z = np.full(shape, np.nan, "float32")
    radar = (((z > params.radar_z) & (n_valid > 0)) | any_new_water) & valley

    opt_changed = optical["changed"] if optical else np.zeros(shape, bool)
    opt_valid = optical["valid"] if optical else np.zeros(shape, bool)

    conf = np.zeros(shape, "uint8")
    conf[radar & opt_changed] = HIGH
    conf[(conf == 0) & ((opt_changed & ~radar) | (radar & ~opt_valid))] = MEDIUM
    conf[(conf == 0) & radar & opt_valid & ~opt_changed] = LOW

    # Clean up: drop tiny patches, fill tiny holes.
    min_px = max(3, int(params.min_patch_m2 / res ** 2))
    main = remove_small_objects(conf >= MEDIUM, max_size=min_px)
    main = remove_small_holes(main, max_size=min_px) & valley
    low = remove_small_objects(conf == LOW, max_size=min_px) & ~main
    conf = np.where(main, np.maximum(conf, MEDIUM), np.where(low, LOW, 0)).astype("uint8")

    cls = np.zeros(shape, "uint8")
    flooded = conf > 0
    new_water = any_new_water | (optical["new_water"] if optical else False)
    cls[flooded] = DEBRIS
    cls[flooded & new_water] = NEW_WATER
    cls[~flooded & river] = RIVER
    cls[valley & (n_valid == 0) & ~opt_valid & ~flooded] = NO_DATA

    px_km2 = res * res / 1e6
    seen = (n_valid > 0) | opt_valid
    stats = {
        "valley_km2": round(float(valley.sum() * px_km2), 3),
        "valley_seen_pct": round(100.0 * float(seen[valley].mean()) if valley.any() else 0.0, 1),
        "flood_km2": round(float((conf >= MEDIUM).sum() * px_km2), 3),
        "high_km2": round(float((conf == HIGH).sum() * px_km2), 3),
        "medium_km2": round(float((conf == MEDIUM).sum() * px_km2), 3),
        "low_km2": round(float((conf == LOW).sum() * px_km2), 3),
        "new_water_km2": round(float(((conf >= MEDIUM) & (cls == NEW_WATER)).sum() * px_km2), 3),
        "debris_km2": round(float(((conf >= MEDIUM) & (cls == DEBRIS)).sum() * px_km2), 3),
        "radar_tracks": [t["info"] for t in tracks],
        "optical_clear_valley_pct": optical["clear_pct"] if optical else None,
    }
    return FloodResult(cls=cls, conf=conf, valley=valley, score=z, stats=stats)
