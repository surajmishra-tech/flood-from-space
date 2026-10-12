"""Rescue priority: which cut-off or detour settlement should get help first (0-100).

A simple score computed by code from the maps (never by the language model), so every
rescuer can see why a village is ranked where it is:
  40%  size       - buildings near the settlement (OpenStreetMap has no population for most villages)
  30%  damage     - share of those buildings touched by the flood (full if the centre is flooded)
  20%  isolation  - cut off = full; long detour = partly (more extra km = more)
  10%  remoteness - how far help was by road even before the flood
"""
from __future__ import annotations

import math

WEIGHTS = {"size": 0.40, "damage": 0.30, "isolation": 0.20, "remote": 0.10}
BIG_VILLAGE = 400      # buildings that count as "full size"
FAR_KM = 30.0          # road km to help that counts as "very remote"


def parts(a: dict) -> dict[str, float]:
    n = a.get("buildings_nearby") or 0
    hit = a.get("buildings_hit_nearby") or 0
    size = min(1.0, math.log10(1 + n) / math.log10(1 + BIG_VILLAGE))
    damage = 1.0 if a.get("center_flooded") else (min(1.0, 2.0 * hit / n) if n else 0.0)
    if a.get("status") == "cut_off":
        isolation = 1.0
    elif a.get("status") == "detour" and a.get("dist_after_km") is not None and a.get("dist_before_km") is not None:
        isolation = 0.6 * min(1.0, (a["dist_after_km"] - a["dist_before_km"]) / 20.0)
    else:
        isolation = 0.0
    remote = min(1.0, (a.get("dist_before_km") or 0.0) / FAR_KM)
    return {"size": size, "damage": damage, "isolation": isolation, "remote": remote}


def score(a: dict) -> int:
    return round(100 * sum(WEIGHTS[k] * v for k, v in parts(a).items()))


def add_priority(facts: dict) -> dict:
    """Give every cut-off and detour settlement a priority, and sort those lists most urgent first."""
    s = facts.get("settlements") or {}
    for key in ("cut_off_list", "detour_list"):
        lst = s.get(key) or []
        for a in lst:
            a["priority"] = score(a)
            a["priority_parts"] = {k: round(100 * v) for k, v in parts(a).items()}
        lst.sort(key=lambda a: (-a["priority"], -(a.get("buildings_nearby") or 0)))
    return facts
