"""Pick the right images for a flood date.

Radar (Sentinel-1): for each track, the last pass BEFORE and the first pass AFTER
the flood, on the SAME track (same viewing angle, so pixels can be compared).
We keep the best ascending and the best descending pair: they look at the
valley from opposite sides, so one fills the other's blind spots.

Optical (Sentinel-2): the clearest image before and after, judged by the
cloud-free share of the valley (not of the whole tile).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from .config import PARAMS

MIN_COVERAGE = 0.2  # ignore passes that only touch the edge of the box


def flood_start_utc(flood_date, lon: float, time_utc: str | None = None) -> datetime:
    """Start of the flood day in UTC.

    With only a date, we use local midnight estimated from the longitude
    (Nepal: ~18:00 UTC the day before). With an exact time, we use it.
    """
    d = datetime(flood_date.year, flood_date.month, flood_date.day, tzinfo=timezone.utc)
    if time_utc:
        hh, mm = (int(x) for x in time_utc.split(":"))
        return d + timedelta(hours=hh, minutes=mm)
    return d - timedelta(hours=round(lon / 15.0))


def s1_stacks(scenes, start: datetime, params=PARAMS):
    """Radar images to compare, at most one track per direction.

    Returns [(pre_list, post_list), ...]: up to `s1_pre_images` passes before
    the flood (newest first) and up to `s1_post_images` passes after it
    (oldest first, within `s1_post_span_days` of the first one), all on the
    same track.
    """
    by_track = {}
    for s in scenes:
        if s.coverage < MIN_COVERAGE:
            continue
        by_track.setdefault((s.track, s.direction), []).append(s)
    best = {}
    for (track, direction), ss in by_track.items():
        pre = sorted([s for s in ss if s.time < start and start - s.time <= timedelta(days=params.s1_window_days)],
                     key=lambda s: s.time, reverse=True)[:params.s1_pre_images]
        post = sorted([s for s in ss if s.time >= start and s.time - start <= timedelta(days=params.s1_window_days)],
                      key=lambda s: s.time)
        if not pre or not post:
            continue
        post = [s for s in post if s.time - post[0].time <= timedelta(days=params.s1_post_span_days)][:params.s1_post_images]
        # earliest "after" image first, then the best coverage
        score = (post[0].time - start, -min(pre[0].coverage, post[0].coverage))
        if direction not in best or score < best[direction][0]:
            best[direction] = (score, (pre, post))
    return [v[1] for v in sorted(best.values(), key=lambda v: v[0])]


def clear_share(scl: np.ndarray, mask: np.ndarray) -> float:
    """Share of `mask` pixels that are cloud-free land/water in a Sentinel-2 SCL band."""
    if mask.sum() == 0:
        return 0.0
    clear = np.isin(scl, (4, 5, 6, 7))
    return float(clear[mask].mean())


def s2_pick(source, scenes, start: datetime, coarse_grid, valley_coarse, params=PARAMS):
    """Choose the clearest-over-the-valley image before and after the flood."""
    def score(sc):
        if sc.coverage < 0.5:
            return 0.0
        scl = source.read_s2(sc, coarse_grid, bands=("SCL",))["SCL"]
        return clear_share(scl, valley_coarse)

    before = sorted([s for s in scenes if s.time < start], key=lambda s: s.time, reverse=True)
    after = sorted([s for s in scenes if s.time >= start], key=lambda s: s.time)
    picked = []
    for group in (before, after):
        choice = None
        for sc in group[:6]:  # look at the closest few dates only
            if sc.cloud is not None and sc.cloud > 90:
                continue
            share = score(sc)
            if share >= params.s2_min_clear:
                choice = (sc, share)
                break
        picked.append(choice)
    return picked[0], picked[1]
