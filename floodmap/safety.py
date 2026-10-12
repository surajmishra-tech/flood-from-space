"""The "I need help" side: is a person's location in danger, according to our maps?

The answer comes only from a finished run: the flood / debris map, the road access
of each settlement, and the hospitals. Code decides the danger level and writes the
message (English and Nepali), so nothing is invented by a language model.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache

import geopandas as gpd
from shapely.geometry import Point

from .config import RUNS_DIR
from .grid import utm_crs

NEAR_FLOOD_M = 100     # in or right next to the mapped flood / debris
CLOSE_FLOOD_M = 500    # close to it
SETTLEMENT_KM = 3.0    # the nearest settlement within this distance is "your village"
LEVEL_RANK = {"danger": 0, "cut_off": 1, "near": 2, "unknown": 3, "clear": 4}
NE_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def done_runs() -> list[dict]:
    out = []
    if not RUNS_DIR.exists():
        return out
    for d in RUNS_DIR.iterdir():
        f = d / "run.json"
        if not (f.exists() and (d / "facts.json").exists() and (d / "layers" / "settlements.geojson").exists()):
            continue
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if info.get("status") == "done":
            out.append(info)
    return out


def runs_covering(lon: float, lat: float) -> list[dict]:
    """Finished runs whose box contains the point: newest flood date first, then newest run."""
    hits = [r for r in done_runs() if r["bbox"][0] <= lon <= r["bbox"][2] and r["bbox"][1] <= lat <= r["bbox"][3]]
    return sorted(hits, key=lambda r: (r.get("flood_date") or "", r.get("started") or ""), reverse=True)


@lru_cache(maxsize=16)
def _run_data(rid: str, stamp: float):
    d = RUNS_DIR / rid
    facts = json.loads((d / "facts.json").read_text(encoding="utf-8"))
    bbox = facts["meta"]["bbox"]
    crs = utm_crs((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    flood = None
    f = d / "layers" / "flood.geojson"
    if f.exists():
        g = gpd.read_file(f)
        g = g[g["confidence"].isin(["medium", "high"])] if len(g) else g  # reported numbers use medium + high
        if len(g):
            flood = g.to_crs(crs)
    load = lambda name: json.loads((d / "layers" / f"{name}.geojson").read_text(encoding="utf-8"))["features"]  # noqa: E731
    settlements = [x["properties"] for x in load("settlements")]
    hospitals = []
    if (d / "layers" / "hospitals.geojson").exists():
        for x in load("hospitals"):
            lon, lat = x["geometry"]["coordinates"][:2]
            hospitals.append({**x["properties"], "lon": lon, "lat": lat})
    return facts, crs, flood, settlements, hospitals


def _latest_image(facts: dict) -> str | None:
    dates = [a for r in facts["images"].get("radar", []) for a in r.get("after", [])]
    opt = facts["images"].get("optical")
    if opt and opt.get("after"):
        dates.append(opt["after"])
    return max(dates) if dates else None


def check(lon: float, lat: float, rid: str | None = None) -> dict:
    """Danger level for one place, from the maps of the best finished run that covers it."""
    runs = runs_covering(lon, lat)
    if rid and any(r["run_id"] == rid for r in runs):
        run = next(r for r in runs if r["run_id"] == rid)
    elif runs:
        run = runs[0]
    else:
        return {"level": "unknown", "rank": LEVEL_RANK["unknown"], "lat": lat, "lon": lon, "run_id": None,
                "text": _texts("unknown", {})}

    d = RUNS_DIR / run["run_id"]
    facts, crs, flood, settlements, hospitals = _run_data(run["run_id"], (d / "facts.json").stat().st_mtime)

    info: dict = {"area_name": facts["meta"].get("area_name"), "flood_date": facts["meta"]["flood_date"],
                  "image_date": _latest_image(facts)}
    if flood is not None:
        pt = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(crs).iloc[0]
        dist = flood.distance(pt)
        i = dist.idxmin()
        info["flood_m"] = round(float(dist[i]))
        info["flood_kind"] = flood.loc[i, "kind"]

    near = sorted(((_km(lat, lon, s["lat"], s["lon"]), s) for s in settlements), key=lambda t: t[0])
    if near and near[0][0] <= SETTLEMENT_KM:
        km, s = near[0]
        info["settlement"] = {k: s.get(k) for k in ("name", "name_ne", "status", "center_flooded", "dist_before_km",
                                                      "dist_after_km", "reach_before", "reach_after")}
        info["settlement"]["km"] = round(km, 1)

    safe_h = sorted(((_km(lat, lon, h["lat"], h["lon"]), h) for h in hospitals if not h.get("hit")), key=lambda t: t[0])
    if safe_h:
        km, h = safe_h[0]
        info["hospital"] = {"name": h.get("name"), "name_ne": h.get("name_ne"), "km": round(km, 1)}

    fm, s = info.get("flood_m"), info.get("settlement") or {}
    if (fm is not None and fm <= NEAR_FLOOD_M) or (s.get("center_flooded") and s.get("km", 99) <= 1):
        level = "danger"
    elif s.get("status") == "cut_off":
        level = "cut_off"
    elif (fm is not None and fm <= CLOSE_FLOOD_M) or s.get("status") == "detour":
        level = "near"
    else:
        level = "clear"
    return {"level": level, "rank": LEVEL_RANK[level], "lat": lat, "lon": lon, "run_id": run["run_id"], **info,
            "text": _texts(level, info)}


def _texts(level: str, i: dict) -> dict:
    """The message in English and Nepali. Every number is put in here by code."""
    titles = {
        "danger": ("DANGER: you are in or next to the flood area", "खतरा: तपाईं बाढी क्षेत्रभित्र वा नजिकै हुनुहुन्छ"),
        "cut_off": ("Your area is cut off by road", "तपाईंको क्षेत्रको सडक सम्पर्क टुटेको छ"),
        "near": ("Be careful: the flood area is close to you", "सावधान: बाढी क्षेत्र तपाईंको नजिकै छ"),
        "clear": ("Our map shows no flood near you", "हाम्रो नक्सामा तपाईंको नजिक बाढी देखिँदैन"),
        "unknown": ("We have no flood map for your place yet", "तपाईंको ठाउँको बाढी नक्सा अहिलेसम्म छैन"),
    }
    en, ne = [], []

    def add(en_tpl: str, ne_tpl: str, ne_names: dict | None = None, **v):
        """One line in both languages. Numbers and dates get Nepali digits; names can be given in Nepali."""
        en.append(en_tpl.format(**v))
        nv = {k: str(x).translate(NE_DIGITS) for k, x in v.items()}
        nv.update(ne_names or {})
        ne.append(ne_tpl.format(**nv))

    if level == "unknown":
        add("Your place is outside every flood area we have mapped, so help requests cannot be sent from here. "
            "If you are in danger, call 112 (India) or 100 (Nepal).",
            "तपाईंको ठाउँ हामीले नक्सा बनाएको बाढी क्षेत्रभन्दा बाहिर छ, त्यसैले यहाँबाट मद्दत अनुरोध पठाउन सकिँदैन। "
            "खतरामा हुनुहुन्छ भने ११२ (भारत) वा १०० (नेपाल) मा फोन गर्नुहोस्।")
    fm = i.get("flood_m")
    if fm is not None:
        if fm <= 20:
            add("You are inside the mapped flood / debris area.", "तपाईं नक्सामा देखिएको बाढी / लेदो क्षेत्रभित्र हुनुहुन्छ।")
        elif fm <= 3000:
            add("Mapped flood / debris is about {m} m from you.", "नक्सामा देखिएको बाढी / लेदो तपाईंबाट करिब {m} मिटर टाढा छ।", m=fm)
    s = i.get("settlement")
    if s:
        named = s.get("name") and s["name"] != "(unnamed)"
        en_v = s["name"] if named else "Your area"
        # Nepali "from X": joined to a Nepali name (बाहुनडाँडाबाट), spaced after a Latin one
        ne_v = s["name_ne"] + "बाट" if s.get("name_ne") else (s["name"] + " बाट" if named else "तपाईंको क्षेत्रबाट")
        nn = {"v": ne_v}
        if s["status"] == "cut_off":
            add("{v} has lost its road to a town or hospital.", "{v} सहर वा अस्पताल जाने सडक टुटेको छ।", nn, v=en_v)
        elif s["status"] == "detour" and s.get("dist_after_km") and s.get("dist_before_km"):
            add("From {v}, the road to help is now {x} km longer.", "{v} मद्दत पुग्ने सडक बाटो अब {x} किमी लामो भएको छ।",
                nn, v=s["name"] if named else "your area", x=round(s["dist_after_km"] - s["dist_before_km"], 1))
        elif s["status"] == "ok":
            add("{v} still has a road connection.", "{v} अझै सडक बाटो खुला छ।", nn, v=en_v)
    h = i.get("hospital")
    if h and level != "unknown":
        if h.get("name") and h["name"] != "Hospital / health post":
            add("Nearest hospital / health post not reached by the flood: {n}, about {k} km away (straight line).",
                "बाढी नपुगेको नजिकको अस्पताल / स्वास्थ्य चौकी: {n}, करिब {k} किमी (सिधा दूरी)।",
                {"n": h.get("name_ne") or h["name"]}, n=h["name"], k=h["km"])
        else:
            add("Nearest hospital / health post not reached by the flood is about {k} km away (straight line).",
                "बाढी नपुगेको नजिकको अस्पताल / स्वास्थ्य चौकी करिब {k} किमी टाढा छ (सिधा दूरी)।", k=h["km"])
    if level in ("danger", "near", "cut_off"):
        add("If it is safe to move, go to higher ground away from the river.",
            "सुरक्षित भए नदीबाट टाढा अग्लो ठाउँमा जानुहोस्।")
    elif level == "clear":
        add("Stay away from the river and riverbanks.", "नदी र नदी किनारबाट टाढा बस्नुहोस्।")
    if i.get("image_date"):
        add("This map uses satellite images up to {d}. Things may have changed since.",
            "यो नक्सा {d} सम्मका भू-उपग्रह तस्बिरमा आधारित छ। त्यसपछि अवस्था फेरिएको हुन सक्छ।", d=i["image_date"])
    add("Demo tool, not an official warning. In danger, call the emergency numbers.",
        "यो परीक्षणका लागि बनाइएको उपकरण हो, आधिकारिक चेतावनी होइन। खतरामा परे आपतकालीन नम्बरमा फोन गर्नुहोस्।")
    return {"en": {"title": titles[level][0], "lines": en}, "ne": {"title": titles[level][1], "lines": ne}}


_COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")


def search_places(q: str, limit: int = 8) -> list[dict]:
    """Find a typed place in the settlements and hospitals of our finished runs (pre-flood OpenStreetMap).

    Coordinates also work: "28.166, 85.322" (lat, lon, as Google Maps copies them)."""
    q = (q or "").strip()
    m = _COORDS.match(q)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        lat, lon = (b, a) if abs(a) > 90 or (60 <= a <= 100 and 0 <= b <= 40) else (a, b)  # "85.32, 28.16" = lon, lat
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return [{"name": f"{lat:.5f}, {lon:.5f}", "name_ne": "", "kind": "coordinates", "lat": lat, "lon": lon, "area": ""}]
        return []
    if len(q) < 2:
        return []
    ql = q.lower()
    seen, out = set(), []
    for r in sorted(done_runs(), key=lambda r: r.get("started") or "", reverse=True):
        d = RUNS_DIR / r["run_id"] / "layers"
        for layer, kind in (("settlements", None), ("hospitals", "hospital / health post")):
            f = d / f"{layer}.geojson"
            if not f.exists():
                continue
            for x in json.loads(f.read_text(encoding="utf-8"))["features"]:
                p = x["properties"]
                name, name_ne = str(p.get("name") or ""), str(p.get("name_ne") or "")
                if name in ("", "(unnamed)") or (ql not in name.lower() and q not in name_ne):
                    continue
                lon, lat = (p["lon"], p["lat"]) if "lon" in p else x["geometry"]["coordinates"][:2]
                key = (name.lower(), round(lat, 2), round(lon, 2))  # same name within ~1 km = same place
                if key in seen:
                    continue
                seen.add(key)
                out.append({"name": name, "name_ne": name_ne, "kind": kind or p.get("place") or "place",
                            "lat": lat, "lon": lon, "area": r.get("name") or "", "starts": name.lower().startswith(ql)})
    out.sort(key=lambda o: (not o.pop("starts"), o["name"]))
    return out[:limit]
